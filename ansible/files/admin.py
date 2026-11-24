"""Grant only a confirmed local sudo membership through the Debian Ansible user backend."""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Never, Self

from ansible.modules.user import User
from pydantic import ValidationError

from ansible import __version__ as ansible_version
from setup_system.domain.actions import AdminAction
from setup_system.domain.administration import ADMIN_REQUEST_BYTES, AdminAccount, AdminIntent
from setup_system.infrastructure.admin_account import (
    checked_output,
    current_boot_identity,
    read_account,
    verify_effective_access,
    verify_policy,
)
from setup_system.infrastructure.native_target import current_target
from setup_system.infrastructure.operation_io import read_admin_request
from setup_system.infrastructure.system_paths import protected
from setup_system.models import AdminObservations, RequestEnvelope, Status, TaskResult


class ExistingLogin(User):
    """Use the existing-user Debian backend without its account-creation dispatcher."""

    def __new__(cls, module: "SudoAppend") -> Self:
        return object.__new__(cls)

    # Group append needs ordinary identity fields. Avoid the default shadow lookup.
    user_info = User.get_pwd_info


class SudoAppend:
    check_mode = False

    def __init__(self, account: AdminAccount) -> None:
        self.account = account
        self.attempted = False
        self.params: defaultdict[str, object] = defaultdict(
            lambda: None,
            {
                "name": account.username,
                "state": "present",
                "groups": ["sudo"],
                "append": True,
                "create_home": False,
                "ssh_key_type": "rsa",
            },
        )

    def get_bin_path(self, name: str, required: bool) -> str:
        if name != "usermod" or not required:
            raise ValueError("Unsupported account backend")
        return "/usr/sbin/usermod"

    def fail_json(self, **_details: object) -> Never:
        raise ValueError("The native user module could not verify the local group")

    def run_command(
        self, argv: list[str], *, use_unsafe_shell: bool = False, data: bytes | None = None
    ) -> tuple[int, str, str]:
        if use_unsafe_shell or data is not None:
            raise ValueError("Unexpected user-module invocation")
        if argv == ["/usr/sbin/usermod", "--help"]:
            return 0, checked_output(*argv), ""
        if argv != ["/usr/sbin/usermod", "-a", "-G", "sudo", self.account.username]:
            raise ValueError("This Ansible backend did not request an append-only sudo membership")
        if read_account(AdminIntent(username=self.account.username)) != self.account:
            raise ValueError("The account changed before the native append. Preview again")
        self.attempted = True
        return 0, checked_output(*argv), ""


def require_packaged_source() -> None:
    source = Path(__file__)
    if source != Path("/usr/lib/setup-system/ansible/files/admin.py") or not source.is_file():
        raise ValueError("Root Apply requires the installed Debian account helper")
    protected(source, directory=False)


def plan(account: AdminAccount, boot_identity: str) -> TaskResult:
    actions = (
        ()
        if account.member
        else (
            AdminAction(
                kind="admin",
                username=account.username,
                label=f"Grant {account.username} full administrative access via sudo."
                " After a new login, the selected user's password will authorize root commands.",
            ),
        )
    )
    return TaskResult(
        status=Status.NO_CHANGES if account.member else Status.READY,
        actions=actions,
        observed=AdminObservations(
            account=account,
            boot_identity=boot_identity,
            needs_system=bool(actions),
            needs_user=False,
        ),
    )


def run(action: str, raw: bytes) -> TaskResult:
    if action not in {"inspect", "plan", "apply"}:
        raise ValueError("Unknown administrative operation")
    request = RequestEnvelope.model_validate_json(raw).for_target(f"admin-{action}")
    intent = AdminIntent.model_validate(request.settings)
    if not current_target()[1]:
        raise ValueError("Administrative onboarding requires supported Debian 13 (trixie)")
    if tuple(ansible_version.split(".")[:2]) not in {("2", "19"), ("2", "20")}:
        raise ValueError("This version of the Ansible user backend has not been reviewed")
    if action == "apply":
        if os.geteuid() != 0:
            raise ValueError(
                "The packaged administrative operation requires root authentication via su"
            )
        require_packaged_source()
    account = read_account(intent)
    boot_identity = current_boot_identity()
    observed = plan(account, boot_identity)
    if action != "apply":
        return observed
    if request.expected is None or not request.expected.same_plan(observed) or account.member:
        raise ValueError("The selected account or confirmed action changed. Preview again")
    verify_policy()
    if read_account(intent) != account:
        raise ValueError("The account changed during the policy check. Preview again")
    backend = SudoAppend(account)
    try:
        ExistingLogin(backend).modify_user_usermod()
        after = read_account(intent)
        if after != account.validated(member=True):
            raise ValueError("The account no longer matches the expected identity and membership")
        verify_policy()
        verify_effective_access(after)
    except (ValueError, OSError, KeyError, TypeError) as error:
        try:
            unchanged = read_account(intent) == account
        except (ValueError, OSError):
            unchanged = False
        return TaskResult(
            status=Status.BLOCKED if unchanged or not backend.attempted else Status.PARTIAL,
            blockers=(
                f"{error}. Membership or its effective policy could not be confirmed."
                " Inspect the account again.",
            ),
        )
    return TaskResult(
        status=Status.REQUIRES_USER_LOGIN,
        actions=(
            AdminAction(
                kind="admin",
                username=account.username,
                label=f"Verified sudo membership for {account.username}. "
                "Start a new login session to use it.",
            ),
        ),
        observed=AdminObservations(
            account=after, boot_identity=boot_identity, needs_system=False, needs_user=False
        ),
    )


def preflight(request: str) -> int:
    if os.geteuid() == 0:
        raise ValueError("Validate administrative consent as the unprivileged caller")
    try:
        print(read_admin_request(request, "apply")["digest"])
    except (OSError, ValueError):
        result = TaskResult(
            status=Status.BLOCKED,
            blockers=("Invalid or changed administrative request. Preview a new account plan.",),
        )
        path = Path(request).with_name("result.json")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(result.model_dump_json(exclude_unset=True))
        return 2
    return 0


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "preflight":
        raise SystemExit(preflight(sys.argv[2]))
    try:
        if len(sys.argv) not in {2, 3}:
            raise ValueError("Expected one packaged administration operation")
        raw = (
            sys.argv[2].encode()
            if len(sys.argv) == 3
            else sys.stdin.buffer.read(ADMIN_REQUEST_BYTES + 1)
        )
        if len(raw) > ADMIN_REQUEST_BYTES:
            raise ValueError("Administrative request is too large")
        outcome = run(sys.argv[1], raw)
    except ValidationError:
        outcome = TaskResult(
            status=Status.BLOCKED,
            blockers=("Invalid administrative request. Preview the account again.",),
        )
    except (ValueError, OSError) as error:
        outcome = TaskResult(status=Status.BLOCKED, blockers=(str(error),))
    print(json.dumps(outcome.model_dump(mode="json", exclude_unset=True)))


if __name__ == "__main__":
    main()
