"""Create an encrypted OpenSSH identity through the native graphical passphrase prompt."""

import hashlib
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from pydantic import Field

from setup_plugin_git.keys import public_material
from setup_system.authorization import session_environment
from setup_system.infrastructure.file_publication import move_new
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.system_paths import protected
from setup_system.infrastructure.user_storage import (
    checked_directory,
    inside_home,
    private_directory,
    safe_file,
)
from setup_system_sdk import Inputs, Outcome, Plan, Resource, Result, owned_resources

ASKPASS = Path("/usr/lib/openssh/gnome-ssh-askpass")
KEYGEN = "/usr/bin/ssh-keygen"
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
    observed_rows = []
    for row in rows:
        target = path / Path(row.identity).name
        safe_file(target, os.getuid())
        info = target.lstat()
        if stat.S_IMODE(info.st_mode) != row.permissions or info.st_size > 16384:
            raise ValueError("The SSH key file changed its mode or size")
        if target.name == PUBLIC:
            data = target.read_bytes()
            key, fingerprint = public_material(data)
            if not key.startswith("ssh-ed25519 "):
                raise ValueError("The generated SSH key type differs")
            digest = fingerprint + " " + hashlib.sha256(data).hexdigest()
        else:
            # The private key stays in OpenSSH and the digest utility. No private bytes enter IPC.
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
        observed_rows.append(row.model_copy(update={"after": digest, "verified": True}))
    return observed_rows


def held_pairs() -> dict[Path, list[Resource]]:
    groups: dict[Path, list[Resource]] = {}
    for row in owned_resources():
        if row.kind not in KINDS.values() or row.ownership != "created" or not row.cleanup:
            continue
        target = Path(row.identity)
        if target.parent.parent != directory() or KINDS.get(target.name) != row.kind:
            raise ValueError("Unrecognized SSH key ownership record")
        groups.setdefault(target.parent, []).append(row)
    if any(
        len(rows) != 2 or {r.kind for r in rows} != set(KINDS.values()) for rows in groups.values()
    ):
        raise ValueError("Incomplete SSH key ownership record")
    return groups


def generate_plan(request: Generate) -> Plan:
    path = directory() / request.name
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
            "Enter a nonempty passphrase directly in the OpenSSH dialog. No key is uploaded."
        ],
    )


def generate(request: Generate, plan: Plan) -> Result:
    path = directory() / request.name
    if generate_plan(request) != plan or plan.outcome != Outcome.READY:
        return Result(outcome=Outcome.BLOCKED, message="SSH key destination or prompt changed")
    with private_directory(path.parent):
        stage = Path(tempfile.mkdtemp(prefix=".generate-", dir=path.parent))
        created_dir = stage.lstat()
        published = False
        try:
            env = session_environment()
            env.update(SSH_ASKPASS=str(ASKPASS), SSH_ASKPASS_REQUIRE="force")
            created = run_command(
                [KEYGEN, "-q", "-t", "ed25519", "-f", str(stage / KEY), "-C", request.name],
                env=env,
                stdin=subprocess.DEVNULL,
                discard_output=True,
                check=False,
                timeout=120,
            )
            if created.returncode or not (stage / PUBLIC).is_file():
                return Result(
                    outcome=Outcome.CANCELLED, message="OpenSSH did not create a key pair"
                )
            # OpenSSH must reject the empty passphrase before this pair is published.
            empty = run_command(
                [KEYGEN, "-y", "-P", "", "-f", str(stage / KEY)],
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                stdin=subprocess.DEVNULL,
                discard_output=True,
                timeout=5,
                check=False,
            )
            if empty.returncode == 0:
                return Result(
                    outcome=Outcome.BLOCKED,
                    message="OpenSSH requires a nonempty passphrase for this key",
                )
            (stage / PUBLIC).chmod(0o644)
            rows = observed(stage, plan.resources)
            move_new(stage, path)
            published = True
            if observed(path, plan.resources) != rows:
                return Result(outcome=Outcome.PARTIAL, message="Review the changed SSH identity")
            return Result(
                outcome=Outcome.CHANGED,
                message="OpenSSH created the selected encrypted key",
                resources=rows,
            )
        finally:
            if not published and stage.exists() and os.path.samestat(stage.lstat(), created_dir):
                for name in KINDS:
                    (stage / name).unlink(missing_ok=True)
                stage.rmdir()


def removal_plan() -> Plan:
    rows = []
    for path, held in sorted(held_pairs().items()):
        actual = observed(path, held)
        for previous, current in zip(held, actual, strict=True):
            if previous.after != current.after:
                return Plan(
                    outcome=Outcome.BLOCKED, summary="A local key changed. Review its fingerprint."
                )
            rows.append(
                previous.model_copy(
                    update={
                        "effect": "remove",
                        "before": previous.after,
                        "after": None,
                        "verified": False,
                    }
                )
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


def remove(plan: Plan) -> Result:
    if plan != removal_plan():
        return Result(outcome=Outcome.BLOCKED, message="Local SSH keys changed. Preview again.")
    done: list[Resource] = []
    for path, rows in sorted(held_pairs().items()):
        stage = Path(tempfile.mkdtemp(dir=path.parent, prefix=".remove-"))
        claimed = stage / "pair"
        try:
            move_new(path, claimed)
            checked = observed(claimed, rows)
            if any(
                a.after != b.after for a, b in zip(rows, checked, strict=True)
            ) or os.path.lexists(path):
                return Result(
                    outcome=Outcome.PARTIAL,
                    message="Review the changed SSH key location",
                    resources=done,
                )
            for name in KINDS:
                (claimed / name).unlink()
            claimed.rmdir()
            done.extend(
                r.model_copy(update={"verified": True})
                for r in plan.resources
                if Path(r.identity).parent == path
            )
        finally:
            if os.path.lexists(claimed) and not os.path.lexists(path):
                move_new(claimed, path)
            if not any(stage.iterdir()):
                stage.rmdir()
    return Result(
        outcome=Outcome.CHANGED, message="Removed the reviewed local key files", resources=done
    )
