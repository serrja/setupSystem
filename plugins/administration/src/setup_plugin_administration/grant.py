"""Bind sudo membership to one interactive account, policy and boot identity."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import typer
from pydantic import ValidationError
from setup_system_sdk.enums.errors import ErrorCode

from setup_plugin_administration.adapters.native import (
    INSTALLED,
    NATIVE_ANSIBLE,
    NATIVE_PYTHON,
    current_boot_identity,
    current_target,
    require_installed_resource,
)

from .account import (
    read_account,
    verify_effective_access,
    verify_policy,
)
from .models import ADMIN_REQUEST_BYTES, AdminAccount, AdminIntent, GrantConfirmation

RELATIVE = "plugins/administration/setup_plugin_administration"
cli = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


def fingerprint(account: AdminAccount, boot: str | None = None) -> str:
    return hashlib.sha256(
        (boot if boot is not None else current_boot_identity()).encode("ascii")
        + account.model_dump_json().encode()
    ).hexdigest()


def observe(username: str) -> AdminAccount:
    if not current_target()[1]:
        raise ValueError("Sudo onboarding requires a supported Debian 13 target.")
    return read_account(AdminIntent(username=username))


def append(username: str, before: str, after: str) -> None:
    """Use the public user module in a transient service with account creation disabled."""
    if os.geteuid() != 0:
        raise ValueError("Authenticate through su for the reviewed grant.")
    require_installed_resource(Path(__file__), f"{RELATIVE}/grant.py")
    verify_policy()
    account = observe(username)
    if (
        account.member
        or fingerprint(account) != before
        or fingerprint(account.validated(member=True)) != after
    ):
        raise ValueError("The account or confirmed grant changed. Preview again.")
    # The public user module has no existing-only switch. Hide Debian's account
    # creators in the service mount namespace, including during concurrent deletion.
    subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--quiet",
            "--pipe",
            "--wait",
            "--collect",
            "--property=PrivateTmp=yes",
            "--property=InaccessiblePaths=/usr/sbin/useradd -/usr/sbin/adduser -/usr/sbin/luseradd",
            "--property=WorkingDirectory=/",
            "--property=Environment=ANSIBLE_CONFIG=/usr/lib/setup-system/ansible.cfg",
            str(NATIVE_PYTHON),
            "-I",
            "-B",
            str(NATIVE_ANSIBLE),
            "-i",
            "localhost,",
            "-c",
            "local",
            str(INSTALLED / RELATIVE / "resources/membership.yml"),
            "-e",
            f"selected_user={account.username}",
            "-e",
            f"before_digest={before}",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C"},
        check=True,
    )
    observed = observe(username)
    if fingerprint(observed) != after:
        raise ValueError("Account identity, other groups or membership changed during the grant.")
    verify_policy()
    verify_effective_access(observed)


@cli.command("check-existing")
def check_existing(username: str, expected_fingerprint: str) -> None:
    """Confirm the reviewed account still exists before granting access."""
    require_installed_resource(Path(__file__), f"{RELATIVE}/grant.py")
    account = observe(username)
    if account.member or fingerprint(account) != expected_fingerprint:
        raise typer.Exit(1)


def _confirmation() -> GrantConfirmation:
    data = sys.stdin.buffer.read(ADMIN_REQUEST_BYTES + 1)
    if len(data) > ADMIN_REQUEST_BYTES:
        raise ValueError("Administrative confirmation exceeds the supported size.")
    return GrantConfirmation.model_validate_json(data)


@cli.command("grant-sudo")
def grant_sudo() -> None:
    """Apply one authenticated, reviewed sudo membership change."""
    confirmed = _confirmation()
    append(confirmed.username, confirmed.before_digest, confirmed.after_digest)


@cli.command("verify-sudo")
def verify_sudo() -> None:
    """Check that the confirmed account still has effective sudo access."""
    confirmed = _confirmation()
    account = observe(confirmed.username)
    if (
        not account.member
        or fingerprint(account) != confirmed.before_digest
        or confirmed.after_digest != confirmed.before_digest
    ):
        raise ValueError("Sudo membership or identity changed since confirmation.")
    verify_policy()
    verify_effective_access(account)


def main() -> None:
    try:
        cli()
    except (OSError, ValueError, ValidationError, subprocess.SubprocessError):
        typer.echo(
            f"Error [{ErrorCode.NATIVE_EXECUTOR_UNAVAILABLE.value}]: "
            "The reviewed administrative operation could not complete.",
            err=True,
        )
        raise SystemExit(1) from None
