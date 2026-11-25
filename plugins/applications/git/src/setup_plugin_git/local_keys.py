"""Create an encrypted OpenSSH identity through the native graphical passphrase prompt."""

import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path

from pydantic import Field

from setup_plugin_git.keys import public_material
from setup_system.authorization import session_environment
from setup_system.infrastructure import retirement_checkpoint as checkpoint
from setup_system.infrastructure.file_publication import move_new
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.recorded_tree import CLAIM_DIRECTORY, Entry, remove_recorded
from setup_system.infrastructure.system_paths import protected
from setup_system.infrastructure.user_storage import (
    FileSnapshot,
    checked_directory,
    inside_home,
    private_directory,
    safe_file,
)
from setup_system_sdk import Inputs, Outcome, Plan, Resource, Result, owned_resources

ASKPASS = Path("/usr/lib/openssh/gnome-ssh-askpass")
KEYGEN = "/usr/bin/ssh-keygen"
MARKER = ".setup-system-key-removal.json"
KEY = "id_ed25519"
PUBLIC = KEY + ".pub"
KINDS = {KEY: "ssh-private-key", PUBLIC: "ssh-public-key"}


class Generate(Inputs):
    name: str = Field(
        pattern=r"^[a-z][a-z0-9_-]{0,47}$",
        title="Key name",
        description="A new private directory under ~/.ssh/setup-system contains the pair.",
    )


class RemoveLocal(Inputs):
    """Remove unchanged key pairs recorded as created by this plugin."""


def directory() -> Path:
    return checked_directory(inside_home(Path.home() / ".ssh/setup-system"))


def native_dialog() -> bool:
    env = session_environment()
    if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")) or not os.access(ASKPASS, os.X_OK):
        return False
    protected(ASKPASS, directory=False)
    protected(Path(KEYGEN), directory=False)
    return True


def template(path: Path) -> list[Resource]:
    return [
        Resource(
            kind=kind,
            identity=str(path / name),
            ownership="created",
            cleanup=True,
            permissions=0o600 if name == KEY else 0o644,
        )
        for name, kind in KINDS.items()
    ]


def observed(path: Path, rows: list[Resource]) -> list[Resource]:
    checked_directory(path)
    if stat.S_IMODE(path.stat().st_mode) != 0o700 or set(path.iterdir()) != {
        path / KEY,
        path / PUBLIC,
    }:
        raise ValueError("The SSH key directory contains unexpected permissions or files")
    return [
        row.model_copy(
            update={
                "after": key_entry(path / Path(row.identity).name, row.permissions)[0],
                "verified": True,
            }
        )
        for row in rows
    ]


def key_entry(target: Path, permissions: int | None) -> tuple[str, Entry]:
    safe_file(target, os.getuid())
    info = target.lstat()
    if stat.S_IMODE(info.st_mode) != permissions or info.st_size > 16384:
        raise ValueError("The SSH key file changed its mode or size")
    if target.name == PUBLIC:
        data = target.read_bytes()
        key, fingerprint = public_material(data)
        if not key.startswith("ssh-ed25519 "):
            raise ValueError("The generated SSH key type differs")
        digest = fingerprint + " " + hashlib.sha256(data).hexdigest()
    else:
        # Only the digest of the private key crosses the native process boundary.
        result = run_command(
            ["/usr/bin/sha256sum", "--", str(target)],
            timeout=5,
            max_output_bytes=4096,
            discard_stderr=True,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
        )
        digest = result.stdout.partition(" ")[0]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Cannot fingerprint the private key file")
    current = target.lstat()
    if not os.path.samestat(info, current) or current.st_ctime_ns != info.st_ctime_ns:
        raise ValueError("The SSH key changed during readback")
    return digest, (info.st_dev, info.st_ino, info.st_mode, digest.rsplit(" ", 1)[-1])


def held_pairs() -> dict[Path, list[Resource]]:
    groups: dict[Path, list[Resource]] = {}
    for row in owned_resources():
        if row.kind not in KINDS.values() or row.ownership != "created" or not row.cleanup:
            continue
        target = Path(row.identity)
        if (
            target.parent.parent != directory()
            or KINDS.get(target.name) != row.kind
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", target.parent.name)
        ):
            raise ValueError("Unrecognized SSH key ownership record")
        groups.setdefault(target.parent, []).append(row)
    if any(
        len(rows) != 2 or {r.kind for r in rows} != set(KINDS.values()) for rows in groups.values()
    ):
        raise ValueError("Incomplete SSH key ownership record")
    return groups


def staging(path: Path) -> Path:
    return path.with_name(".generate-" + path.name)


def interrupted(path: Path) -> str:
    return (
        f"Inspect the unpublished key directory {staging(path)}. Keep it private. "
        "Verify the public fingerprint in OpenSSH, then move the directory to a private "
        "location you control before retrying this name."
    )


def generate_plan(request: Generate) -> Plan:
    path = directory() / request.name
    if os.path.lexists(staging(path)):
        return Plan(outcome=Outcome.NEEDS_USER_ACTION, summary=interrupted(path))
    if checkpoint.pending(path) or os.path.lexists(path / MARKER):
        return Plan(outcome=Outcome.BLOCKED, summary="Finish the interrupted key removal first")
    if os.path.lexists(path):
        held = held_pairs().get(path)
        if held and observed(path, held) == [r.model_copy(update={"verified": True}) for r in held]:
            return Plan(outcome=Outcome.NO_CHANGES, summary="The managed key pair is present")
        return Plan(
            outcome=Outcome.BLOCKED, summary="The key name is already in use. Choose a new name."
        )
    if not native_dialog():
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION,
            summary=(
                "Run ssh-keygen -t ed25519 in your terminal. Choose a new filename and enter its "
                "passphrase there. Return to Upload public SSH key and select the .pub file. "
                "Managed creation needs a graphical session and Debian ssh-askpass-gnome."
            ),
        )
    return Plan(
        summary=f"Create an encrypted Ed25519 key in {path}",
        resources=template(path),
        warnings=[
            f"OpenSSH prepares the unpublished key in {staging(path)}.",
            "Enter a nonempty passphrase directly in the OpenSSH dialog. No key is uploaded.",
        ],
    )


def generate(request: Generate, plan: Plan) -> Result:
    path = directory() / request.name
    if generate_plan(request) != plan or plan.outcome != Outcome.READY:
        return Result(outcome=Outcome.BLOCKED, message="SSH key destination or prompt changed")
    stage = staging(path)
    with private_directory(path.parent) as parent:
        try:
            os.mkdir(stage.name, 0o700, dir_fd=parent)
        except FileExistsError:
            return Result(outcome=Outcome.NEEDS_USER_ACTION, message=interrupted(path))
        os.fsync(parent)
    with private_directory(stage, create=False) as opened:
        created_dir = os.fstat(opened)
        env = session_environment()
        env.update(SSH_ASKPASS=str(ASKPASS), SSH_ASKPASS_REQUIRE="force")
        created = run_command(
            [KEYGEN, "-q", "-t", "ed25519", "-f", KEY, "-C", request.name],
            cwd=str(stage),
            env=env,
            stdin=subprocess.DEVNULL,
            discard_output=True,
            check=False,
            timeout=120,
        )
        if created.returncode or not (stage / PUBLIC).is_file():
            return Result(outcome=Outcome.NEEDS_USER_ACTION, message=interrupted(path))
        empty = run_command(
            [KEYGEN, "-y", "-P", "", "-f", KEY],
            cwd=str(stage),
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
            discard_output=True,
            timeout=5,
            check=False,
        )
        if empty.returncode == 0:
            return Result(
                outcome=Outcome.BLOCKED,
                message=(
                    "OpenSSH rejected the empty passphrase for managed use. "
                    "The unpublished private key may be unencrypted. " + interrupted(path)
                ),
            )
        if not os.path.samestat(created_dir, stage.lstat()):
            return Result(outcome=Outcome.UNKNOWN_OUTCOME, message=interrupted(path))
        safe_file(stage / PUBLIC, os.getuid())
        with os.fdopen(
            os.open(PUBLIC, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=opened), "rb"
        ) as public:
            if not public_material(public.read(16385))[0].startswith("ssh-ed25519 "):
                raise ValueError("Unexpected generated public key")
            os.fchmod(public.fileno(), 0o644)
        rows = observed(stage, plan.resources)
        with checkpoint.allow_creation(path):
            if not os.path.samestat(created_dir, stage.lstat()):
                return Result(outcome=Outcome.UNKNOWN_OUTCOME, message=interrupted(path))
            move_new(stage, path)
        if (
            not os.path.samestat(created_dir, path.lstat())
            or observed(path, plan.resources) != rows
        ):
            return Result(outcome=Outcome.PARTIAL, message="Review the changed SSH identity")
        return Result(
            outcome=Outcome.CHANGED,
            message="OpenSSH created the selected encrypted key",
            resources=rows,
        )


def verify_generation(request: Generate, plan: Plan) -> Result:
    path = directory() / request.name
    if os.path.lexists(staging(path)):
        return Result(outcome=Outcome.NEEDS_USER_ACTION, message=interrupted(path))
    if (
        not os.path.lexists(path)
        and not checkpoint.pending(path)
        and plan.resources == template(path)
    ):
        return Result(
            outcome=Outcome.CANCELLED,
            message="Both confirmed destinations and the key staging directory are empty.",
            resources=[
                r.model_copy(update={"verified": True, "permissions": None}) for r in plan.resources
            ],
        )
    try:
        rows = observed(path, plan.resources)
    except (OSError, ValueError):
        return Result(
            outcome=Outcome.PARTIAL, message="Inspect the incomplete or changed SSH key directory."
        )
    if any(
        expected.after is not None and expected.after != actual.after
        for expected, actual in zip(plan.resources, rows, strict=True)
    ):
        return Result(
            outcome=Outcome.PARTIAL,
            message="The generated SSH fingerprints differ from native evidence.",
        )
    return Result(
        outcome=Outcome.COMPLETE, message="Verified the generated OpenSSH key pair", resources=rows
    )


class PairRetirement(Inputs):
    directory: tuple[int, int]
    files: dict[str, Entry]


def retirement(path: Path, held: list[Resource]) -> PairRetirement | None:
    """Bind durable file claims to the exact digests and modes in the resource ledger."""
    if checkpoint.pending(path):
        saved = checkpoint.load(path, MARKER)
        checkpoint.check_target(path, saved)
        record = PairRetirement.model_validate_json(saved.payload)
        identity = saved.directory
    elif not os.path.lexists(path):
        return None
    else:
        marker = path / MARKER
        with private_directory(path, create=False) as parent:
            info = os.fstat(parent)
            if stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("SSH key directory permissions changed")
            identity = info.st_dev, info.st_ino
            if os.path.lexists(marker):
                if set(os.listdir(parent)) - {*KINDS, MARKER, CLAIM_DIRECTORY}:
                    raise ValueError("An unrecorded file appeared in the SSH key directory")
                receipt = FileSnapshot.read(parent, MARKER, limit=8192)
                record = PairRetirement.model_validate_json(receipt.data)
                for row in held:
                    name = Path(row.identity).name
                    if os.path.lexists(path / name) and key_entry(path / name, row.permissions)[
                        1
                    ] != record.files.get(name):
                        raise ValueError("SSH key changed during retirement")
            else:
                if set(os.listdir(parent)) != KINDS.keys():
                    raise ValueError("Unexpected entries in the SSH key directory")
                files = {}
                for row in held:
                    name = Path(row.identity).name
                    digest, entry = key_entry(path / name, row.permissions)
                    if digest != row.after:
                        raise ValueError("A local key changed")
                    files[name] = entry
                record = PairRetirement(directory=identity, files=files)
    if record.directory != identity or record.files.keys() != KINDS.keys():
        raise ValueError("SSH key removal has different file or directory identities")
    for row in held:
        entry = record.files[Path(row.identity).name]
        if (
            not row.after
            or not stat.S_ISREG(entry[2])
            or stat.S_IMODE(entry[2]) != row.permissions
            or entry[3] != row.after.rsplit(" ", 1)[-1]
        ):
            raise ValueError("SSH key removal differs from the verified ledger entry")
    return record


def removal_plan() -> Plan:
    rows: list[Resource] = []
    for path, held in sorted(held_pairs().items()):
        try:
            record = retirement(path, held)
        except (OSError, ValueError):
            return Plan(
                outcome=Outcome.BLOCKED,
                summary="Review the changed SSH key or its retirement receipt.",
            )
        if record is None:
            continue
        rows.extend(
            previous.model_copy(
                update={
                    "effect": "remove",
                    "before": previous.after,
                    "after": None,
                    "verified": False,
                }
            )
            for previous in held
        )
    return Plan(
        outcome=Outcome.READY if rows else Outcome.NO_CHANGES,
        summary="Delete the reviewed local SSH key pairs",
        resources=sorted(rows, key=lambda row: row.identity),
        warnings=[
            "This permanently removes these private keys and may interrupt access on other hosts."
        ]
        if rows
        else [],
    )


def removed(plan: Plan) -> bool:
    directories = {Path(row.identity).parent for row in plan.resources}
    return all(not os.path.lexists(path) and not checkpoint.pending(path) for path in directories)


def remove(plan: Plan) -> Result:
    if plan != removal_plan() or plan.outcome != Outcome.READY:
        return Result(outcome=Outcome.BLOCKED, message="Local SSH keys changed. Preview again.")
    done: list[Resource] = []
    for path, held in sorted(held_pairs().items()):
        selected = [row for row in plan.resources if Path(row.identity).parent == path]
        if not selected:
            continue
        record = retirement(path, held)
        if not record:
            return Result(
                outcome=Outcome.PARTIAL,
                message="Review the missing SSH key receipt",
                resources=done,
            )
        payload = record.model_dump_json().encode()
        if not checkpoint.pending(path):
            marker = path / MARKER
            if not os.path.lexists(marker):
                checkpoint.publish(path, MARKER, payload, path.parent)
            if retirement(path, held) != record:
                return Result(
                    outcome=Outcome.PARTIAL,
                    message="SSH key removal receipt changed",
                    resources=done,
                )
            remove_recorded(path, record.files, marker)
        checkpoint.finish(path, MARKER, payload)
        done.extend(row.model_copy(update={"verified": True}) for row in selected)
    return Result(
        outcome=Outcome.CHANGED, message="Removed the reviewed local key files", resources=done
    )
