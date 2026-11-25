"""Grant only a confirmed local sudo membership with an existing-account-only group append."""

import json
import os
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

import typer
from pydantic import ValidationError

from setup_system.domain.actions import AdminAction
from setup_system.domain.administration import ADMIN_REQUEST_BYTES, AdminAccount, AdminIntent
from setup_system.infrastructure.admin_account import (
    current_boot_identity,
    read_account,
    verify_effective_access,
    verify_policy,
)
from setup_system.infrastructure.native_target import current_target
from setup_system.infrastructure.operation_io import read_admin_request
from setup_system.layout import INSTALLED, PYTHON_PACKAGE, require_installed_resource
from setup_system.machine_io import write_model
from setup_system.models import AdminObservations, RequestEnvelope, Status, TaskResult


class Operation(StrEnum):
    INSPECT = "inspect"
    PLAN = "plan"
    APPLY = "apply"


cli = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


def append_sudo(account: AdminAccount) -> None:
    """Run the single existing-account operation from a root-owned Ansible context."""
    arguments = {
        "argv": ["/usr/sbin/usermod", "--append", "--groups", "sudo", "--", account.username],
        "expand_argument_vars": False,
    }
    # su cannot pipeline modules. Stage this command under root's private /run directory.
    python = str(INSTALLED / "libexec/isolated-python.sh")
    with TemporaryDirectory(prefix="setup-system-admin-", dir="/run") as stage:
        result = subprocess.run(
            [
                python,
                "-c",
                "import runpy, sys; sys.path.append(sys.argv.pop(1));"
                " runpy.run_path(sys.argv.pop(1), run_name='__main__')",
                str(PYTHON_PACKAGE.parent),
                "/usr/bin/ansible",
                "localhost",
                "-i",
                "localhost,",
                "-c",
                "local",
                "-m",
                "ansible.builtin.command",
                "-a",
                json.dumps(arguments),
            ],
            cwd=INSTALLED,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME": stage,
                "LANG": "C.UTF-8",
                "ANSIBLE_CONFIG": str(INSTALLED / "ansible.cfg"),
                "ANSIBLE_PYTHON_INTERPRETER": python,
                "ANSIBLE_HOME": "/usr/share/ansible",
                "ANSIBLE_LOCAL_TEMP": stage + "/local",
                "ANSIBLE_REMOTE_TEMP": stage + "/remote",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    if result.returncode:
        raise ValueError("The native append to the local sudo group failed")


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
    if action == "apply":
        if os.geteuid() != 0:
            raise ValueError(
                "The packaged administrative operation requires root authentication via su"
            )
        require_installed_resource(Path(__file__), "ansible/files/admin.py")
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
    try:
        append_sudo(account)
        after = read_account(intent)
        if after != account.validated(member=True):
            raise ValueError("The account no longer matches the expected identity and membership")
        verify_policy()
        verify_effective_access(after)
    except (ValueError, OSError) as error:
        try:
            unchanged = read_account(intent) == account
        except (ValueError, OSError):
            unchanged = False
        return TaskResult(
            status=Status.BLOCKED if unchanged else Status.PARTIAL,
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


@cli.command()
def preflight(request: str) -> None:
    """Check the private Apply request and emit its pre-prompt digest."""
    if os.geteuid() == 0:
        typer.echo("Validate administrative consent as the unprivileged caller", err=True)
        raise typer.Exit(2)
    try:
        typer.echo(read_admin_request(request, "apply")["digest"])
    except (OSError, ValueError):
        result = TaskResult(
            status=Status.BLOCKED,
            blockers=("Invalid or changed administrative request. Preview a new account plan.",),
        )
        path = Path(request).with_name("result.json")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                write_model(result, stream)
        except OSError:
            typer.echo("Could not publish the blocked administrative result", err=True)
        raise typer.Exit(2) from None


@cli.command()
def account(operation: Operation, wire: Annotated[str | None, typer.Argument()] = None) -> None:
    """Inspect, plan or apply one confirmed existing-account change."""
    try:
        raw = wire.encode() if wire is not None else sys.stdin.buffer.read(ADMIN_REQUEST_BYTES + 1)
        if len(raw) > ADMIN_REQUEST_BYTES:
            raise ValueError("Administrative request is too large")
        outcome = run(operation.value, raw)
    except ValidationError:
        outcome = TaskResult(
            status=Status.BLOCKED,
            blockers=("Invalid administrative request. Preview the account again.",),
        )
    except (ValueError, OSError) as error:
        outcome = TaskResult(status=Status.BLOCKED, blockers=(str(error),))
    write_model(outcome)


if __name__ == "__main__":
    cli()
