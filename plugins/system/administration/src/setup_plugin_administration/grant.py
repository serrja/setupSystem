"""Bind sudo membership to one interactive account, policy and boot identity."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from setup_system.infrastructure.native_target import current_boot_identity, current_target
from setup_system.layout import INSTALLED, require_installed_resource

from .account import (
    read_account,
    verify_effective_access,
    verify_policy,
)
from .models import ADMIN_REQUEST_BYTES, AdminAccount, AdminIntent, GrantConfirmation

RELATIVE = "plugins/administration/setup_plugin_administration"


def fingerprint(account: AdminAccount) -> str:
    return hashlib.sha256(
        current_boot_identity().encode("ascii") + account.model_dump_json().encode()
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
            "/usr/bin/python3",
            "-I",
            "-B",
            "/usr/bin/ansible-playbook",
            "-i",
            "localhost,",
            "-c",
            "local",
            str(INSTALLED / RELATIVE / "membership.yml"),
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


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "check-existing":
        require_installed_resource(Path(__file__), f"{RELATIVE}/grant.py")
        account = observe(sys.argv[2])
        if account.member or fingerprint(account) != sys.argv[3]:
            sys.exit(1)
    elif len(sys.argv) == 2 and sys.argv[1] in ("grant-sudo", "verify-sudo"):
        data = sys.stdin.buffer.read(ADMIN_REQUEST_BYTES + 1)
        if len(data) > ADMIN_REQUEST_BYTES:
            raise ValueError("Administrative confirmation exceeds the supported size.")
        confirmed = GrantConfirmation.model_validate_json(data)
        if sys.argv[1] == "grant-sudo":
            append(confirmed.username, confirmed.before_digest, confirmed.after_digest)
        else:
            account = observe(confirmed.username)
            if (
                not account.member
                or fingerprint(account) != confirmed.before_digest
                or confirmed.after_digest != confirmed.before_digest
            ):
                raise ValueError("Sudo membership or identity changed since confirmation.")
            verify_policy()
            verify_effective_access(account)
    else:
        sys.exit(2)
