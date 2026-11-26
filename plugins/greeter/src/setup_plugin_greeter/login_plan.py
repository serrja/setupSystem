"""Observe the login environment and plan the selected greeter activation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from setup_system.execution.account_identity import TargetUser
from setup_system.execution.inspection import inspect_system
from setup_system.execution.native_templates import render_asset
from setup_system.execution.palette import desktop_style, palette
from setup_system.execution.system_facts import EncryptionState, SystemSnapshot

from .login_boot import manager_enabled
from .login_files import LoginFiles
from .models import DesktopFile, DesktopPlan, LoginConsent

XDG_KEYS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")


def greeter_style(root: Path) -> bytes:
    return render_asset(
        Path(__file__).parent / "assets/login/greeter.css",
        {"auth_screen": desktop_style(root).auth_screen.tokens(palette(root))},
    )


def default_target(command: Callable[[list[str]], subprocess.CompletedProcess[str]]) -> str:
    previous = command(
        ["/usr/bin/systemctl", "--system", "--no-pager", "--no-ask-password", "get-default"]
    ).stdout.strip()
    if re.fullmatch(r"[A-Za-z0-9_.@:-]+\.target", previous) is None:
        raise ValueError("Cannot identify the current system boot target")
    return previous


def plan_login(
    root: Path,
    store: LoginFiles,
    command: Callable[[list[str]], subprocess.CompletedProcess[str]],
    target: TargetUser,
    environment: Mapping[str, str],
    snapshot: SystemSnapshot | None,
) -> DesktopPlan:
    if (
        target.uid <= 0
        or target.gid < 0
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", target.name)
    ):
        raise ValueError("Graphical sessions require a verified ordinary account")
    home = target.home
    selected: dict[str, str] = {}
    for key in XDG_KEYS:
        value = Path(environment[key])
        if (
            not value.is_absolute()
            or ".." in value.parts
            or value == home
            or not value.is_relative_to(home)
        ):
            raise ValueError("Login XDG roots must stay within the verified HOME")
        selected[key] = str(value)
    snapshot = inspect_system(home) if snapshot is None else snapshot
    home_encryption = snapshot.encryption.home
    if home_encryption.state == EncryptionState.LOCKED or (
        home_encryption.state == EncryptionState.UNKNOWN
        and any(layer.casefold() in {"fscrypt", "ecryptfs"} for layer in home_encryption.layers)
    ):
        raise ValueError(
            "The encrypted HOME is locked or its unlock status is unknown; "
            "unlock and verify it before user runtime setup"
        )
    paths = store.paths
    deferred: list[str] = []
    observed_inputs: dict[str, str | None] = {}
    try:
        enabled: bool | None = manager_enabled(store)
        store.check_path(paths.vendor_unit)
        if not paths.vendor_unit.is_file():
            enabled = None
            deferred.append(str(paths.vendor_unit))
    except PermissionError:
        if os.geteuid() == 0:
            raise
        enabled = None
        deferred.append(str(paths.display_manager))
    previous = default_target(command)
    registry = (
        json.dumps(
            {
                "schema": 1,
                "name": target.name,
                "uid": target.uid,
                "gid": target.gid,
                "home": str(home),
                "environment": selected,
            },
            sort_keys=True,
            indent=2,
        ).encode()
        + b"\n"
    )
    bridge_source = (Path(__file__).parent / "assets/login/login_session.py").read_bytes()
    session_command = shlex.join((str(paths.bridge), str(paths.registry)))
    greeter_command = shlex.join(
        (
            "/usr/bin/cage",
            "-s",
            "--",
            "/usr/bin/gtkgreet",
            "-s",
            str(paths.style),
            "-c",
            session_command,
        )
    )
    config = (
        "[general]\nsource_profile = false\n\n[terminal]\nvt = 7\nswitch = true\n\n"
        f'[default_session]\ncommand = {json.dumps(greeter_command)}\nuser = "_greetd"\n'
    ).encode()
    systemd_path = json.dumps(str(paths.config).replace("%", "%%"))
    dropin = f"[Service]\nExecStart=\nExecStart=/usr/sbin/greetd --config {systemd_path}\n".encode()
    desired = (
        (paths.config, config, 0o644),
        (paths.unit_dropin, dropin, 0o644),
        (paths.registry / f"{target.uid}.json", registry, 0o644),
        (paths.bridge, bridge_source, 0o755),
        (paths.style, greeter_style(root), 0o644),
    )
    try:
        receipt = store.receipt()
        receipt_observed = store.fingerprint(paths.receipt)
        observed_inputs[str(paths.receipt)] = receipt_observed
    except PermissionError:
        if os.geteuid() == 0:
            raise
        receipt, receipt_observed = None, None
        deferred.append(str(paths.receipt))
    files: list[DesktopFile] = []
    for path, content, mode in desired:
        try:
            observed = store.fingerprint(path)
            mode_before = None if observed is None else stat.S_IMODE(path.lstat().st_mode)
        except PermissionError:
            if os.geteuid() == 0:
                raise
            observed, mode_before = None, None
            deferred.append(str(path))
        else:
            observed_inputs[str(path)] = (
                hashlib.sha256(f"{mode_before}:{observed}".encode()).hexdigest()
                if observed is not None
                else None
            )
        if observed is not None and str(paths.receipt) not in deferred:
            recorded = receipt.files.get(str(path)) if receipt is not None else None
            desired_hash = hashlib.sha256(content).hexdigest()
            if observed != desired_hash and (recorded is None or recorded.sha256 != observed):
                raise ValueError(f"Unknown or edited desktop file preserved for review: {path}")
        files.append(
            DesktopFile(
                path=path, content=content, mode=mode, observed=observed, observed_mode=mode_before
            )
        )
    return DesktopPlan(
        target=target,
        environment=selected,
        files=tuple(files),
        snapshot=snapshot,
        consent=LoginConsent(
            previous_target=previous,
            manager_enabled=enabled,
            intended=hashlib.sha256(
                json.dumps(
                    [
                        (str(path), hashlib.sha256(content).hexdigest(), mode)
                        for path, content, mode in desired
                    ]
                ).encode()
            ).hexdigest(),
            observed=observed_inputs,
            deferred=tuple(deferred),
        ),
        receipt_observed=receipt_observed,
    )
