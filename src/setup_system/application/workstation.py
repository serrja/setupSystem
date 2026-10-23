"""Portable user profiles, with one separate Debian desktop package backend."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Literal
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field

from setup_system.adapters.debian.desktop import HYPRLAND_VERSION, package_arguments
from setup_system.storage import atomic_write, new_backup, restore, state_root

Profile = Literal["fonts", "hyprland"]
GNOME_FONTS = (
    ("org.gnome.desktop.interface", "font-name", "Noto Sans 11"),
    ("org.gnome.desktop.interface", "document-font-name", "Noto Sans 11"),
    ("org.gnome.desktop.interface", "monospace-font-name", "JetBrainsMono Nerd Font Mono 11"),
    ("org.gnome.desktop.wm.preferences", "titlebar-font", "Noto Sans Bold 11"),
)


class WorkstationPlan(BaseModel):
    """Read-only report; package commands are separate from portable user files."""

    model_config = ConfigDict(frozen=True)
    profile: Profile
    os: str
    architecture: str
    installed_hyprland: str | None
    target_hyprland: str = HYPRLAND_VERSION
    files: tuple[str, ...]
    packages: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    gui_verified: bool = False
    observations: dict[str, str] = Field(default_factory=dict)


def _run(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=True)
    return result.stdout.strip()


def _safe(path: Path) -> Path:
    home = Path.home().resolve()
    path = path.expanduser().absolute()
    if not path.is_relative_to(home) or ".." in path.parts:
        raise ValueError("Profile path must stay inside the user's home")
    for parent in (path, *path.parents):
        if parent == home:
            break
        if parent.is_symlink():
            raise ValueError(f"Profile path traverses symlink: {parent}")
    return path


def _config() -> Path:
    return _safe(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")))


def _receipt() -> Path:
    return _safe(state_root() / "workstation.json")


def _hyprland_version() -> str | None:
    if not shutil.which("Hyprland"):
        return None
    return _run(["Hyprland", "--version"]).splitlines()[0]


def _targets(root: Path, profile: Profile) -> dict[Path, bytes]:
    config = _config()
    if profile == "fonts":
        return {
            _safe(config / "fontconfig/conf.d/60-setup-system-fonts.conf"): (
                root / "assets/fonts/60-setup-system-fonts.conf"
            ).read_bytes()
        }
    folder = root / "assets/hyprland"
    targets = {
        config / "hypr/setup-system" / path.relative_to(folder): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }
    entry = config / "hypr/hyprland.lua"
    original = entry.read_text() if entry.exists() else ""
    line = 'require("setup-system.profile")'
    if line not in original.splitlines():
        # Existing active config may define conflicting binds/services. Preserve it, refuse merge.
        if original.strip():
            raise ValueError("Existing Hyprland entrypoint requires a reviewed manual include")
        targets[entry] = (line + "\n").encode()
    portal = config / "xdg-desktop-portal/hyprland-portals.conf"
    targets[portal] = (
        b"[preferred]\ndefault=hyprland;gtk\norg.freedesktop.impl.portal.FileChooser=gtk\n"
    )
    return {_safe(path): content for path, content in targets.items()}


def plan(root: Path, profile: Profile) -> WorkstationPlan:
    """Inspect OS and files without creating cache, backup or package state."""
    release = platform.freedesktop_os_release()
    blockers: list[str] = []
    packages: list[str] = []
    try:
        files = tuple(str(path) for path in _targets(root, profile))
    except ValueError as exc:
        blockers.append(str(exc))
        files = ()
    version = _hyprland_version()
    if profile == "hyprland":
        try:
            packages = package_arguments()
        except ValueError as exc:
            blockers.append(str(exc))
        if version is None or not re.search(r"\b0\.55\.2\b", version):
            blockers.append("Configuration apply requires installed Hyprland 0.55.2")
    observations = {
        key: os.environ.get(key, "unset")
        for key in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "LANG", "LC_ALL", "TERM")
    }
    for name, argv in {
        "monospace": ["fc-match", "-f", "%{family}", "monospace"],
        "emoji": ["fc-match", "-f", "%{family}", "emoji"],
    }.items():
        if shutil.which(argv[0]):
            observations[name] = _run(argv)
    if shutil.which("gsettings"):
        schemas = _run(["gsettings", "list-schemas"]).splitlines()
        for schema, key, _ in GNOME_FONTS:
            if schema in schemas:
                observations[f"{schema}:{key}"] = _run(["gsettings", "get", schema, key])
        if "org.gnome.desktop.interface" in schemas:
            for key in ("text-scaling-factor", "scaling-factor"):
                observations[key] = _run(["gsettings", "get", "org.gnome.desktop.interface", key])
        if "org.gnome.Terminal.ProfilesList" in schemas:
            profile_id = _run(
                ["gsettings", "get", "org.gnome.Terminal.ProfilesList", "default"]
            ).strip("'")
            if re.fullmatch(r"[0-9a-f-]{36}", profile_id):
                schema = (
                    "org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:"
                    + profile_id
                    + "/"
                )
                for key in ("font", "use-system-font", "cell-width-scale", "cell-height-scale"):
                    observations["terminal:" + key] = _run(["gsettings", "get", schema, key])
    for connector in sorted(Path("/sys/class/drm").glob("card*-*/status")):
        observations[connector.parent.name] = connector.read_text().strip()
    for key in ("GTK_THEME", "QT_QPA_PLATFORMTHEME", "QT_SCALE_FACTOR", "GDK_SCALE"):
        observations[key] = os.environ.get(key, "unset")
    if shutil.which("lspci"):
        devices = _run(["lspci", "-nnk"]).splitlines()
        graphics: list[str] = []
        selected = False
        for line in devices:
            if line and not line[0].isspace():
                selected = any(
                    word in line for word in ("VGA", "3D controller", "Display controller")
                )
            if selected:
                graphics.append(line)
        observations["gpu"] = "\n".join(graphics)
    return WorkstationPlan(
        profile=profile,
        os=release.get("PRETTY_NAME", "unknown"),
        architecture=platform.machine(),
        installed_hyprland=version,
        files=files,
        packages=tuple(packages),
        blockers=tuple(blockers),
        observations=observations,
    )


def _font_files(root: Path) -> dict[Path, bytes]:
    manifest = tomllib.loads((root / "manifests/fonts.toml").read_text())[
        "jetbrains_mono_nerd_font"
    ]
    data = _safe(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")))
    destination = data / ("fonts/JetBrainsMonoNerdFont-" + manifest["version"])
    expected = manifest["files"]
    if all(
        (destination / item["name"]).is_file()
        and hashlib.sha256((destination / item["name"]).read_bytes()).hexdigest() == item["sha256"]
        for item in expected
    ):
        return {}
    # Explicit apply only; the archive is never executed or extracted by path.
    with tempfile.TemporaryFile() as archive:
        with urlopen(manifest["url"], timeout=60) as response:
            if not response.url.startswith("https://"):
                raise ValueError("Font download redirected outside HTTPS")
            shutil.copyfileobj(response, archive)
        archive.seek(0)
        if hashlib.file_digest(archive, "sha256").hexdigest() != manifest["sha256"]:
            raise ValueError("Font archive digest mismatch")
        archive.seek(0)
        with zipfile.ZipFile(archive) as bundle:
            result: dict[Path, bytes] = {}
            for item in expected:
                content = bundle.read(item["name"])
                if hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise ValueError("Font file digest mismatch")
                result[_safe(destination / item["name"])] = content
            return result


def _settings(gnome: bool, terminal_profile: str | None) -> list[tuple[str, str, str]]:
    settings = list(GNOME_FONTS) if gnome else []
    if terminal_profile:
        if not re.fullmatch(r"[0-9a-f-]{36}", terminal_profile):
            raise ValueError("Expected an existing GNOME Terminal profile UUID")
        available = _run(["gsettings", "get", "org.gnome.Terminal.ProfilesList", "list"])
        if f"'{terminal_profile}'" not in available:
            raise ValueError("Terminal profile does not exist")
        schema = (
            "org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:"
            + terminal_profile
            + "/"
        )
        if _run(["gsettings", "get", schema, "use-system-font"]) != "false":
            raise ValueError("Terminal uses system font; review that choice before overriding it")
        old = _run(["gsettings", "get", schema, "font"]).strip("'")
        size = old.rsplit(" ", 1)[-1]
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", size):
            raise ValueError("Cannot preserve terminal font size")
        settings.append((schema, "font", "JetBrainsMono Nerd Font Mono " + size))
    return settings


def backup(root: Path, profile: Profile) -> Path:
    """Back up profile-owned existing files; leave unrelated user files alone."""
    return new_backup([path for path in _targets(root, profile) if path.exists()])


def apply(
    root: Path, profile: Profile, *, gnome: bool = False, terminal_profile: str | None = None
) -> Path:
    """Apply portable files with conflict checks and a recoverable per-write receipt."""
    report = plan(root, profile)
    if report.blockers:
        raise ValueError("; ".join(report.blockers))
    if profile != "fonts" and (gnome or terminal_profile):
        raise ValueError("GNOME options belong to the fonts profile")
    desired = _targets(root, profile)
    if profile == "fonts":
        for family in ("Noto Sans", "Noto Color Emoji"):
            if family not in _run(["fc-match", "-f", "%{family}", family]):
                raise ValueError("Install the distro's Noto fonts before applying this profile")
        desired.update(_font_files(root))
    receipt_path = _receipt()
    previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    for path, content in desired.items():
        if path.exists() and path.read_bytes() != content:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if previous.get(str(path)) != digest:
                raise ValueError(f"Locally edited/unmanaged file requires review: {path}")
    settings = _settings(gnome, terminal_profile)
    setting_rows = []
    for schema, key, value in settings:
        old = _run(["gsettings", "get", schema, key])
        if old != "'" + value + "'":
            setting_rows.append(
                {"schema": schema, "key": key, "before": old, "after": "'" + value + "'"}
            )
    changed = {
        path: content
        for path, content in desired.items()
        if not path.exists() or path.read_bytes() != content
    }
    previous.update(
        {str(path): hashlib.sha256(content).hexdigest() for path, content in desired.items()}
    )
    receipt_content = (json.dumps(previous, indent=2) + "\n").encode()
    if not receipt_path.exists() or receipt_path.read_bytes() != receipt_content:
        changed[receipt_path] = receipt_content
    folder = new_backup(
        [path for path in changed if path.exists()]
        + ([receipt_path] if receipt_path.exists() else [])
    )
    operation = {
        "profile": profile,
        "files": [
            {
                "path": str(path),
                "created": not path.exists(),
                "after": hashlib.sha256(content).hexdigest(),
            }
            for path, content in changed.items()
        ],
        "settings": setting_rows,
    }
    atomic_write(folder / "workstation.json", (json.dumps(operation, indent=2) + "\n").encode())
    for path, content in changed.items():
        atomic_write(path, content, 0o600 if path == receipt_path else 0o644)
    for row in setting_rows:
        _run(["gsettings", "set", row["schema"], row["key"], row["after"]])
        if _run(["gsettings", "get", row["schema"], row["key"]]) != row["after"]:
            raise RuntimeError("GSettings readback failed; backup contains rollback values")
    if profile == "fonts" and changed:
        _run(["fc-cache", "-f"])
    return folder


def restore_profile(backup_id: str, *, apply_changes: bool = False) -> list[tuple[str, str]]:
    """Refuse rollback over later user edits; preview is the default."""
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", backup_id):
        raise ValueError("Invalid backup ID")
    folder = _safe(state_root() / "backups" / backup_id)
    operation = json.loads((folder / "workstation.json").read_text())
    paths: list[Path] = []
    for item in operation["files"]:
        path = _safe(Path(item["path"]))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["after"]:
            raise ValueError(f"Rollback conflicts with a later edit: {path}")
        if item["created"]:
            paths.append(path)
    for row in operation["settings"]:
        if _run(["gsettings", "get", row["schema"], row["key"]]) != row["after"]:
            raise ValueError("Rollback conflicts with a later GSettings edit")
    results = restore(backup_id, apply=False)
    if apply_changes:
        new_backup(paths)
        results = restore(backup_id, apply=True, replace_current=True)
        for row in operation["settings"]:
            _run(["gsettings", "set", row["schema"], row["key"], row["before"]])
        for path in paths:
            path.unlink()
        if operation["profile"] == "fonts":
            _run(["fc-cache", "-f"])
    return results + [(str(path), "removed" if apply_changes else "would-remove") for path in paths]
