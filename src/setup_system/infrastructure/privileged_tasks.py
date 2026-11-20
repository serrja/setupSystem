"""Stdlib-only allowlisted system tasks, imported exclusively from verified root source."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

from setup_system import installation as system
from setup_system.adapters.debian.package_session import HappPin
from setup_system.desktop_system import DesktopSystem
from setup_system.infrastructure.trusted_bootstrap import caller, fetch, protected

WORKSPACE_PACKAGES = frozenset(
    {
        "firefox-esr",
        "webext-ublock-origin-firefox",
        "git",
        "gh",
        "zsh",
        "neovim",
        "ripgrep",
        "fzf",
    }
)


def _validate(settings: object, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(settings, dict) or not set(settings) <= fields:
        raise ValueError("Unexpected system settings")
    if any(not isinstance(key, str) for key in settings):
        raise ValueError("Invalid system setting keys")
    return settings


def execute(root: Path, username: str, action: str, settings: object) -> int:
    account = caller()
    if account.pw_name != username:
        raise ValueError("Caller changed after acquisition")
    protected(root)
    target = system.resolve_target(username)
    environment = system.user_environment(target, {})
    if action == "base":
        options = _validate(
            settings,
            frozenset(
                {
                    "schema_version",
                    "hyprland",
                    "graphical_login",
                    "timezone",
                    "keyboard",
                    "plymouth",
                    "xdg",
                    "expected_system_sha256",
                }
            ),
        )
        expected = options.pop("expected_system_sha256", None)
        destination = Path("/etc/setup-system/system.toml")
        protected(destination, directory=False)
        observed = (
            hashlib.sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
        )
        if expected != observed:
            raise ValueError("System settings changed after plan confirmation")
        xdg = options.pop("xdg", {})
        if not isinstance(xdg, dict):
            raise ValueError("Invalid XDG overrides")
        environment = system.user_environment(
            target, {key: Path(value) for key, value in xdg.items()}
        )
        if options.get("schema_version") != 1 or options.get("hyprland") is not True:
            raise ValueError("An explicit supported Hyprland plan is required")
        if (
            type(options.get("graphical_login")) is not bool
            or type(options.get("plymouth")) is not bool
        ):
            raise ValueError("Invalid feature settings")
        if options.get("plymouth"):
            raise ValueError(
                "Plymouth requires validated initramfs recovery on a disposable target"
            )
        if options.get("keyboard") not in {"us", "us,ru"}:
            raise ValueError("Unsupported keyboard layout")
        zone = options.get("timezone")
        if zone is not None:
            if not isinstance(zone, str) or not re.fullmatch(r"[A-Za-z0-9_+./-]{1,100}", zone):
                raise ValueError("Invalid timezone")
            ZoneInfo(zone)
        desktop = DesktopSystem(root, run=system.run_command)
        plan = (
            desktop.plan(target, environment, timezone=zone) if options["graphical_login"] else None
        )
        with system.prepare_packages(
            root,
            target=target,
            extra_packages=plan.extra_packages if plan else (),
            graphical_login=options["graphical_login"] is True,
        ) as prepared_packages:
            prepared_packages.install()
        if plan is not None:
            desktop.apply(desktop.revalidate(plan))
        if plan is None and zone is not None:
            system.run_command(
                ["/usr/bin/timedatectl", "set-timezone", zone], check=True, timeout=30
            )
            observed_zone = system.run_command(
                ["/usr/bin/timedatectl", "show", "--property=Timezone", "--value"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
            if observed_zone != zone:
                raise RuntimeError("Timezone readback failed")
        _save_system(options, expected=expected)
    elif action == "workspace_packages":
        options = _validate(settings, frozenset({"packages", "happ"}))
        packages = options.get("packages")
        if not isinstance(packages, list) or not all(
            isinstance(p, str) and p in WORKSPACE_PACKAGES for p in packages
        ):
            raise ValueError("Workspace package is not allowlisted")
        _packages(root, tuple(packages), happ=options.get("happ") is True)
        if "firefox-esr" in packages:
            _firefox_policy()
    elif action == "cleanup":
        from setup_system.infrastructure.cleanup import apply_cleanup

        options = _validate(settings, frozenset({"packages", "mode", "closure", "xdg"}))
        apply_cleanup(options)
    else:
        raise ValueError("Unknown privileged action")
    return 0


def _save_system(settings: dict[str, object], *, expected: object = None) -> None:
    path = Path("/etc/setup-system/system.toml")
    protected(path, directory=False)
    observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    if observed != expected:
        raise ValueError("System settings changed during apply; preserve the newer intent")
    if not path.parent.exists():
        path.parent.mkdir(mode=0o755, parents=True)
    # Values are from the finite validated schema above; no TOML template is accepted.
    lines = []
    for key, value in settings.items():
        if value is not None:
            lines.append(
                key + " = " + (str(value).lower() if isinstance(value, bool) else json.dumps(value))
            )
    content = ("\n".join(lines) + "\n").encode()
    if path.exists():
        original = path.read_bytes()
        if original == content:
            return
        backup = Path("/var/lib/setup-system/settings-backups") / (
            hashlib.sha256(original).hexdigest() + ".toml"
        )
        protected(backup, directory=False)
        system._write_system(backup, original, 0o600)
    system._write_system(path, content, 0o644)


def _packages(root: Path, packages: tuple[str, ...], *, happ: bool = False) -> None:
    import tempfile

    from setup_system.adapters.debian.package_session import AptCommands, AptSources
    from setup_system.capabilities import load_policy

    policy = load_policy(root)
    paths = system.SystemPaths()
    sources = AptSources(policy.hyprland.codename, policy.hyprland.backports, paths.keyring)
    system._validate_package_paths(paths, sources.managed_backports())
    system._check_offline_namespace(system.run_command)
    with tempfile.TemporaryDirectory(prefix="setup-system-apt-", dir=paths.runtime) as temporary:
        stage = Path(temporary)
        stage.chmod(0o755)
        pin = None
        if happ:
            arch = policy.debian_architecture(platform.machine())
            pin = HappPin.load(root / "manifests/happ.toml", arch)
            manifest = tomllib.loads((root / "manifests/happ.toml").read_text())
            raw = fetch(manifest["linux"][arch]["url"], 256 * 1024 * 1024)
            if hashlib.sha256(raw).hexdigest() != pin.digest:
                raise ValueError("Happ checksum mismatch")
            local = stage / "happ.deb"
            local.write_bytes(raw)
            local.chmod(0o644)
            metadata = system.run_command(
                ["/usr/bin/dpkg-deb", "--field", str(local), "Package", "Version", "Architecture"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            pin.validate_metadata(metadata.stdout)
            packages = (*packages, str(local))
        session = system._PackageSession(
            AptCommands(stage, None, packages),
            sources,
            pin,
            paths,
            system.run_command,
            print,
        )
        session.download()
        session.install()
        if happ:
            session.activate_happ()


def _firefox_policy() -> None:
    path = Path("/etc/firefox/policies/policies.json")
    desired = {
        "policies": {
            "EnableTrackingProtection": {
                "Value": True,
                "Locked": False,
                "Cryptomining": True,
                "Fingerprinting": True,
            },
            "Preferences": {
                "browser.contentblocking.category": {"Value": "strict", "Status": "default"},
                "dom.security.https_only_mode": {"Value": True, "Status": "default"},
            },
            "ExtensionSettings": {
                "uBlock0@raymondhill.net": {
                    "installation_mode": "normal_installed",
                    "install_url": "https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi",
                },
            },
        }
    }
    protected(path, directory=False)
    raw = path.read_bytes() if path.exists() else None
    content = (json.dumps(desired, indent=2) + "\n").encode()
    if raw is not None and raw != content:
        raise ValueError("Existing enterprise Firefox policies require manual review")
    system._write_system(path, content, 0o644)
    if json.loads(path.read_text()) != desired:
        raise RuntimeError("Firefox policy readback failed")
