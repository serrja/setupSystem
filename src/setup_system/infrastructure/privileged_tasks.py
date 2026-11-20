"""Stdlib-only allowlisted system tasks, imported exclusively from verified root source."""

from __future__ import annotations

import hashlib
import json
import platform
import stat
import subprocess
import tomllib
from pathlib import Path

from setup_system import installation as system
from setup_system.adapters.debian.package_session import HappPin
from setup_system.desktop_system import DesktopSystem
from setup_system.diagnostics import PartialApplyError, error_text
from setup_system.infrastructure.trusted_bootstrap import REGIONAL, caller, fetch, protected


def _system_revision(expected: object) -> None:
    path = Path("/etc/setup-system/system.toml")
    protected(path, directory=False)
    observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    if expected != observed:
        raise ValueError("System settings changed after plan confirmation")


def _validate(settings: object, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(settings, dict) or not set(settings) <= fields:
        raise ValueError("Unexpected system settings")
    if any(not isinstance(key, str) for key in settings):
        raise ValueError("Invalid system setting keys")
    return dict(settings)


def execute(root: Path, username: str, action: str, settings: object) -> int:
    account = caller()
    if account.pw_name != username:
        raise ValueError("Caller changed after acquisition")
    protected(root)
    target = system.resolve_target(username)
    environment = system.user_environment(target, {})
    if action in REGIONAL:
        from setup_system.infrastructure import regional

        options = _validate(
            settings, frozenset({"value", "before", "packages", "expected_system_sha256"})
        )
        _system_revision(options.get("expected_system_sha256"))
        desired = regional.validate(action, options.get("value"))
        before = regional.actual(action)
        if regional.equivalent(action, before, desired):
            return 0
        if before != options.get("before"):
            raise ValueError("Regional settings changed after plan confirmation")
        needed = regional.prerequisites(action)
        if options.get("packages", []) != list(needed):
            raise ValueError("Regional package prerequisites changed; inspect a new plan")
        if needed:
            _packages(root, needed)
            generated = regional.actual(action)
            if before is None and action == "keyboard" and generated == "us":
                before = generated
        regional.apply(action, desired, before)
        return 0
    if action == "plymouth":
        from setup_system.infrastructure.graphical_unlock import apply_unlock

        options = _validate(settings, frozenset({"identity", "packages", "expected_system_sha256"}))
        _system_revision(options.get("expected_system_sha256"))
        apply_unlock(
            options.get("identity"),
            packages=options.get("packages", []),
            install=lambda needed: _packages(root, needed),
        )
        return 0
    if action in {"base", "login", "system_settings"}:
        options = _validate(
            settings,
            frozenset(
                {
                    "schema_version",
                    "hyprland",
                    "graphical_login",
                    "timezone",
                    "locale",
                    "keyboard",
                    "plymouth",
                    "xdg",
                    "expected_system_sha256",
                }
            ),
        )
        expected = options.pop("expected_system_sha256", None)
        _system_revision(expected)
        from setup_system.infrastructure.trusted_bootstrap import validate_settings

        validate_settings(action, options, target.home)
        xdg = options.pop("xdg", {})
        if not isinstance(xdg, dict):
            raise ValueError("Invalid XDG overrides")
        environment = system.user_environment(
            target, {key: Path(value) for key, value in xdg.items()}
        )
        if action == "system_settings":
            _save_system(options, expected=expected)
            return 0
        if options["hyprland"] is not True:
            raise ValueError("An explicit Hyprland selection is required")
        if action == "login":
            if options.get("graphical_login") is not True:
                raise ValueError("Graphical login must be selected")
            desktop = DesktopSystem(root, run=system.run_command)
            plan = desktop.plan(target, environment, manage_clock=False)
            desktop.apply(desktop.revalidate(plan))
            return 0
        with system.prepare_packages(
            root, target=target, graphical_login=options["graphical_login"] is True
        ) as prepared:
            prepared.install()
    elif action == "workspace_packages":
        from setup_system.domain.catalog import components

        options = _validate(
            settings, frozenset({"packages", "happ", "external_debs", "expected_happ_version"})
        )
        catalog = components(root=root)
        allowed = {package for component in catalog for package in component.packages}
        external = options.get("external_debs", [])
        external_ids = {c.id for c in catalog if c.handler == "catalog_deb" and not c.blocker}
        if not isinstance(external, list) or not all(
            isinstance(i, str) and i in external_ids for i in external
        ):
            raise ValueError("External Debian package is not allowlisted")
        packages = options.get("packages")
        if not isinstance(packages, list) or not all(
            isinstance(p, str) and p in allowed for p in packages
        ):
            raise ValueError("Workspace package is not allowlisted")
        version = options.get("expected_happ_version")
        if version is not None and not isinstance(version, str):
            raise ValueError("Invalid Happ package version")
        if "firefox-esr" in packages:
            _firefox_policy(inspect_only=True)
        _packages(
            root,
            tuple(packages),
            happ=options.get("happ") is True,
            external=tuple(external),
            expected_happ_version=version,
        )
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
    system._write_system(path, content, 0o644, parent_mode=0o755)
    if path.read_bytes() != content:
        raise PartialApplyError("System settings readback failed")


def _packages(
    root: Path,
    packages: tuple[str, ...],
    *,
    happ: bool = False,
    external: tuple[str, ...] = (),
    expected_happ_version: str | None = None,
) -> None:
    import tempfile

    from setup_system.adapters.debian.package_session import AptCommands, AptSources
    from setup_system.capabilities import load_policy

    policy = load_policy(root)
    debian_packages = packages
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
        local_debs: tuple[Path, ...] = ()
        if external:
            from setup_system.infrastructure.catalog_deb import stage_deb

            arch = policy.debian_architecture(platform.machine())
            local_debs = tuple(stage_deb(root, name, arch, stage) for name in external)
            packages = (*packages, *(str(path) for path in local_debs))
        session = system._PackageSession(
            AptCommands(stage, None, packages),
            sources,
            pin,
            paths,
            system.run_command,
            print,
            persist_backports=False,
            local_debs=local_debs,
            expected_happ_version=expected_happ_version,
        )
        try:
            session.download()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            session.failed(exc)
            raise
        try:
            session.install()
            from setup_system.adapters.debian.packages import installed_version

            if any(installed_version(name) is None for name in debian_packages):
                raise RuntimeError("Debian package database readback failed")
            if happ:
                if pin is None or installed_version("happ") != pin.version:
                    raise RuntimeError("Happ package version readback failed")
                session.activate_happ()
            if external:
                from setup_system.infrastructure.catalog_deb import verify_installed

                for name in external:
                    verify_installed(root, name, arch)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            session.failed(exc)
            raise PartialApplyError(error_text(exc)) from exc


def _firefox_policy(
    path: Path = Path("/etc/firefox/policies/policies.json"),
    *,
    inspect_only: bool = False,
    backups: Path = Path("/var/lib/setup-system/settings-backups"),
) -> None:
    extension_id = "uBlock0@raymondhill.net"
    install_url = f"https://addons.mozilla.org/firefox/downloads/latest/{extension_id}/latest.xpi"
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
                extension_id: {
                    "installation_mode": "normal_installed",
                    "install_url": install_url,
                },
            },
        }
    }
    protected(path, directory=False)
    raw = path.read_bytes() if path.exists() else None
    content = (json.dumps(desired, indent=2) + "\n").encode()
    legacy = content.replace(
        b"/" + extension_id.encode() + b"/latest.xpi", b"/ublock-origin/latest.xpi"
    )
    if raw is not None and raw not in {legacy, content}:
        raise ValueError("Existing enterprise Firefox policies require manual review")
    migrate = []
    for directory, child in ((path.parent.parent, path.parent), (path.parent, path)):
        if directory.exists() and not directory.stat().st_mode & stat.S_IXOTH:
            if (
                raw not in {legacy, content}
                or stat.S_IMODE(directory.stat().st_mode) != 0o700
                or set(directory.iterdir()) != {child}
            ):
                raise ValueError(
                    "Private Firefox policy directory has unknown contents; review permissions"
                )
            migrate.append(directory)
    if inspect_only:
        return
    if raw == legacy or migrate:
        assert raw is not None
        backup = backups / (hashlib.sha256(raw).hexdigest() + ".json")
        protected(backup, directory=False)
        system._write_system(backup, raw)
    system._write_system(path, content, 0o644, parent_mode=0o755)
    for directory in reversed(migrate):
        # Only the verified policy and its exclusive, root-controlled ancestors qualify.
        directory.chmod(0o711)
    if json.loads(path.read_text()) != desired:
        raise RuntimeError("Firefox policy readback failed")
