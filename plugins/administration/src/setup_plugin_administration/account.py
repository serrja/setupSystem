"""Read local identity and the effective Debian files-based sudo policy."""

from __future__ import annotations

import grp
import json
import os
import pwd
import subprocess
from pathlib import Path

from setup_system_sdk.files.account_identity import TargetUser
from setup_system_sdk.files.identity import validate_home
from setup_system_sdk.processes import run_command

from .models import AdminAccount, AdminIntent

ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "HOME": "/"}
DEFAULT_POLICY = "root ALL=(ALL:ALL) ALL\n%sudo ALL=(ALL:ALL) ALL\n"


def checked_output(*argv: str, input_text: str | None = None) -> str:
    try:
        result = run_command(
            list(argv),
            input=input_text,
            discard_output=argv[0] != "/usr/bin/cvtsudoers",
            discard_stderr=True,
            env=ENVIRONMENT,
            timeout=20,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            f"{Path(argv[0]).name} could not verify the local account or policy"
        ) from error
    return result.stdout


def _records(path: Path) -> list[list[str]]:
    return [
        line.split(":")
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    ]


def read_account(intent: AdminIntent) -> AdminAccount:
    name = intent.username
    try:
        record = pwd.getpwnam(name)
        sudo = grp.getgrnam("sudo")
        primary = grp.getgrgid(record.pw_gid)
        by_uid = pwd.getpwuid(record.pw_uid)
        by_gid = grp.getgrgid(sudo.gr_gid)
    except KeyError as error:
        raise ValueError("Choose an existing local login with a local sudo group") from error
    entries = _records(Path("/etc/passwd"))
    matching = [r for r in entries if len(r) == 7 and (r[0] == name or r[2] == str(record.pw_uid))]
    identity = (name, str(record.pw_uid), str(record.pw_gid), record.pw_dir, record.pw_shell)
    if (
        len(matching) != 1
        or tuple(matching[0][i] for i in (0, 2, 3, 5, 6)) != identity
        or by_uid.pw_name != name
    ):
        raise ValueError("The selected login must resolve uniquely to the local passwd database")
    groups = _records(Path("/etc/group"))
    for group in (sudo, primary):
        rows = [
            r
            for r in groups
            if len(r) == 4 and (r[0] == group.gr_name or r[2] == str(group.gr_gid))
        ]
        if len(rows) != 1 or rows[0][0] != group.gr_name or rows[0][2] != str(group.gr_gid):
            raise ValueError("The sudo and primary groups must resolve uniquely to local groups")
        if set(filter(None, rows[0][3].split(","))) != set(group.gr_mem):
            raise ValueError("The local and active group databases disagree")
    if by_gid.gr_name != "sudo":
        raise ValueError("The sudo group has an ambiguous identity")
    limits = {}
    for line in Path("/etc/login.defs").read_text().splitlines():
        parts = line.partition("#")[0].split()
        if len(parts) == 2 and parts[0] in {"UID_MIN", "UID_MAX"}:
            limits[parts[0]] = int(parts[1])
    if not limits.get("UID_MIN", 1000) <= record.pw_uid <= limits.get("UID_MAX", 60000):
        raise ValueError("Select an ordinary interactive login in the configured local UID range")
    shell = Path(record.pw_shell)
    if (
        record.pw_shell not in Path("/etc/shells").read_text().splitlines()
        or not shell.is_file()
        or not os.access(shell, os.X_OK)
        or shell.resolve().name in {"false", "nologin"}
    ):
        raise ValueError("The account must use an enabled executable login shell")
    validate_home(
        TargetUser(name, record.pw_uid, record.pw_gid, Path(record.pw_dir), record.pw_shell)
    )
    local_member = name in sudo.gr_mem or record.pw_gid == sudo.gr_gid
    memberships = set(os.getgrouplist(name, record.pw_gid))
    if local_member != (sudo.gr_gid in memberships):
        raise ValueError("Active sudo membership differs from the local account database")
    return AdminAccount(
        username=name,
        uid=record.pw_uid,
        gid=record.pw_gid,
        home=record.pw_dir,
        shell=record.pw_shell,
        sudo_gid=sudo.gr_gid,
        member=local_member,
        supplementary_gids=tuple(sorted(memberships - {sudo.gr_gid, record.pw_gid})),
    )


def verify_policy() -> None:
    for filename in ("sudo.conf", "nsswitch.conf"):
        path = Path("/etc") / filename
        lines = (
            [line.partition("#")[0].strip() for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )
        for line in lines:
            if (filename == "sudo.conf" and line) or (
                filename == "nsswitch.conf"
                and line.partition(":")[0].strip() == "sudoers"
                and line.partition(":")[2].split() != ["files"]
            ):
                raise ValueError("This custom sudo policy provider needs an administrator's review")
    checked_output("/usr/sbin/visudo", "-c", "-q")
    converter = ("/usr/bin/cvtsudoers", "-c", "/dev/null", "-e", "-f", "json")
    policy = json.loads(checked_output(*converter, "/etc/sudoers"))
    reference = json.loads(checked_output(*converter, "-", input_text=DEFAULT_POLICY))
    if (
        not isinstance(policy, dict)
        or set(policy) - {"Defaults", "User_Specs"}
        or policy.get("User_Specs") != reference["User_Specs"]
    ):
        raise ValueError("Expected the standard password-authenticated root and sudo group grants")
    trusted_paths = {"/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"}
    for item in policy.get("Defaults", []):
        if set(item) != {"Options"}:
            raise ValueError("Scoped sudo Defaults need an administrator's review")
        for option in item["Options"]:
            if option in ({"env_reset": True}, {"mail_badpass": True}, {"use_pty": True}):
                continue
            if (
                set(option) == {"secure_path"}
                and set(option["secure_path"].split(":")) <= trusted_paths
            ):
                continue
            raise ValueError("A sudo Defaults option requires an administrator's review")


def verify_effective_access(account: AdminAccount) -> None:
    checked_output(
        "/usr/bin/sudo", "-n", "-l", "-U", account.username, "-u", "root", "--", "/usr/bin/id"
    )
