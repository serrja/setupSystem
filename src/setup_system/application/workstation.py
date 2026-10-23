"""Portable user profiles, with one separate Debian desktop package backend."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path
from typing import Literal
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field

from setup_system.adapters.debian.desktop import HYPRLAND_VERSION, package_arguments
from setup_system.storage import atomic_write, new_backup, restore, state_root

Profile = Literal["fonts", "hyprland"]
GNOME_FONT_KEYS = (
    ("org.gnome.desktop.interface", "font-name"),
    ("org.gnome.desktop.interface", "document-font-name"),
    ("org.gnome.desktop.interface", "monospace-font-name"),
    ("org.gnome.desktop.wm.preferences", "titlebar-font"),
)
FONT_PROBE = (
    "Latin:     ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz",
    "Cyrillic:  АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "Digits:    0123456789012345678901234567890123456789",
    "No spaces: |iiiiiiiiii|WWWWWWWWWW|0000000000|ЖЖЖЖЖЖЖЖЖЖ|",
    "Spaces:    |i i i i i |W W W W W |0 0 0 0 0 |Ж Ж Ж Ж Ж |",
    "Boxes:     ┌──────────┬──────────┐ │ └──────────┴──────────┘",
    "Icons:     \ue620 \uf07c \ue0b0  Emoji: 😀 👩\u200d💻",
)


class FontPolicy(BaseModel):
    """Validated values used by every generated font asset and desktop setting."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    family: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]+$")
    emoji_fallback: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]+$")
    ui_size: int = Field(ge=6, le=48)
    terminal_size: int = Field(ge=6, le=48)
    panel_size: int = Field(ge=6, le=64)
    previous_fontconfig_sha256: tuple[str, ...] = ()

    def render(self, source: Path) -> bytes:
        """Render only the explicit font placeholders, keeping other user syntax intact."""
        content = source.read_text()
        values = {
            "PRIMARY_FAMILY": self.family,
            "EMOJI_FAMILY": self.emoji_fallback,
            "UI_SIZE": self.ui_size,
            "TERMINAL_SIZE": self.terminal_size,
            "PANEL_SIZE": self.panel_size,
        }
        for key, value in values.items():
            content = content.replace("@" + key + "@", str(value))
        if re.search(r"@[A-Z_]+@", content):
            raise ValueError(f"Unknown font placeholder: {source}")
        return content.encode()


class TerminalFontRefresh(BaseModel):
    """Private receipt for a real monitor notification and equivalent font re-selection."""

    model_config = ConfigDict(extra="forbid")
    profile_id: str
    settings_schema: str
    font: str
    equivalent_font: str
    font_directory: str
    timestamp_before: int
    timestamp_after: int | None = None
    status: Literal["prepared", "monitor-notified", "complete", "failed"] = "prepared"
    error: str | None = None
    gui_verified: bool = False


def font_policy(root: Path) -> FontPolicy:
    """Read the one font-policy manifest; do not download or apply anything."""
    manifest = tomllib.loads((root / "manifests/fonts.toml").read_text())
    font = manifest["jetbrains_mono_nerd_font"]
    return FontPolicy(
        family=font["family"], emoji_fallback=font["emoji_fallback"], **manifest["profile"]
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


def _terminal_schema(profile_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f-]{36}", profile_id):
        raise ValueError("Expected an existing GNOME Terminal profile UUID")
    return (
        "org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:"
        + profile_id
        + "/"
    )


def _terminal_profiles() -> list[str]:
    value = _run(["gsettings", "get", "org.gnome.Terminal.ProfilesList", "list"])
    profiles = ast.literal_eval(value.removeprefix("@as "))
    if not isinstance(profiles, list) or not all(isinstance(item, str) for item in profiles):
        raise ValueError("Cannot read GNOME Terminal profile list")
    return profiles


def _fontconfig_timestamp() -> int:
    """Read GNOME's monitor-owned property; never synthesize its D-Bus notification."""
    if not shutil.which("gdbus"):
        raise ValueError("Terminal refresh requires gdbus and GNOME's running font monitor")
    value = _run(
        [
            "gdbus",
            "call",
            "--session",
            "--timeout",
            "3",
            "--dest",
            "org.gtk.Settings",
            "--object-path",
            "/org/gtk/Settings",
            "--method",
            "org.freedesktop.DBus.Properties.Get",
            "org.gtk.Settings",
            "FontconfigTimestamp",
        ]
    )
    match = re.fullmatch(r"\(<int64 ([0-9]+)>,\)", value)
    if match is None:
        raise ValueError("Cannot read GNOME's real FontconfigTimestamp")
    return int(match.group(1))


def _prepare_terminal_refresh(policy: FontPolicy, profile_id: str | None) -> TerminalFontRefresh:
    if not profile_id:
        raise ValueError("Terminal font refresh requires an explicitly selected terminal profile")
    schema = _terminal_schema(profile_id)
    current = _run(["gsettings", "get", schema, "font"])
    pattern = "'" + re.escape(policy.family) + r"(?: Regular)? ([0-9]+(?:\.[0-9]+)?)'"
    match = re.fullmatch(pattern, current)
    if match is None:
        raise ValueError("Refresh requires this profile to already use the primary Regular font")
    size = match.group(1)
    data = _safe(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")))
    directory = _safe(data / "fonts")
    if not directory.is_dir() or not shutil.which("fc-cache"):
        raise ValueError("Refresh requires an installed user font directory and fc-cache")
    return TerminalFontRefresh(
        profile_id=profile_id,
        settings_schema=schema,
        font=f"'{policy.family} {size}'",
        equivalent_font=f"'{policy.family} Regular {size}'",
        font_directory=str(directory),
        timestamp_before=_fontconfig_timestamp(),
    )


def _refresh_terminal_font(refresh: TerminalFontRefresh, folder: Path) -> None:
    """Notify the existing font monitor, then re-select only one backed-up profile font."""
    receipt = folder / "terminal-refresh.json"

    def save() -> None:
        atomic_write(receipt, (refresh.model_dump_json(indent=2) + "\n").encode())

    try:
        if _run(["gsettings", "get", refresh.settings_schema, "font"]) != refresh.font:
            raise ValueError("Terminal font changed after backup; refusing refresh")
        refresh.timestamp_before = _fontconfig_timestamp()
        save()
        # A real directory event wakes gsd's existing file monitor. Exclusive creation
        # never overwrites a user file; the non-font marker is removed immediately.
        with tempfile.NamedTemporaryFile(
            mode="w", prefix=".setup-system-font-refresh-", dir=refresh.font_directory
        ) as marker:
            marker.write("setupSystem font monitor notification; not a font\n")
        _run(["fc-cache", "-f", refresh.font_directory])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            timestamp = _fontconfig_timestamp()
            if timestamp != refresh.timestamp_before:
                refresh.timestamp_after = timestamp
                break
            time.sleep(0.25)
        if refresh.timestamp_after is None:
            raise ValueError("GNOME's font monitor did not acknowledge the directory change")
        refresh.status = "monitor-notified"
        save()
        if _run(["gsettings", "get", refresh.settings_schema, "font"]) != refresh.font:
            raise ValueError("Terminal font changed while waiting; refusing re-selection")
        try:
            _run(["gsettings", "set", refresh.settings_schema, "font", refresh.equivalent_font])
            if (
                _run(["gsettings", "get", refresh.settings_schema, "font"])
                != refresh.equivalent_font
            ):
                raise ValueError("Equivalent terminal font readback failed")
        finally:
            _run(["gsettings", "set", refresh.settings_schema, "font", refresh.font])
        if _run(["gsettings", "get", refresh.settings_schema, "font"]) != refresh.font:
            raise ValueError("Terminal font restoration readback failed")
        refresh.status = "complete"
        save()
    except BaseException as exc:
        refresh.status = "failed"
        refresh.error = str(exc)
        save()
        raise


def _font_observations(root: Path) -> dict[str, str]:
    policy = font_policy(root)
    observations = {
        "font-policy:primary": policy.family,
        "font-policy:fallback": policy.emoji_fallback + " (missing glyphs only)",
        "terminal:active-profile": "not inferred from the default profile or sandbox environment",
        "terminal:zoom": "window-local; not observable through GNOME Terminal profile GSettings",
        "terminal:geometry": "pixel cell dimensions require the actual terminal window",
    }
    if shutil.which("fc-match"):
        for name, pattern in {
            "sans-serif": "sans-serif",
            "monospace": "monospace",
            "primary": policy.family + ":style=Regular",
            "latin": policy.family + ":charset=0041",
            "cyrillic": policy.family + ":charset=0416",
            "box": policy.family + ":charset=2502",
            "icon": policy.family + ":charset=e620",
            "emoji": policy.family + ":charset=1f600",
        }.items():
            observations["fontconfig:" + name] = _run(
                [
                    "fc-match",
                    "-f",
                    "family=%{family}; style=%{style}; spacing=%{spacing}; file=%{file}",
                    pattern,
                ]
            )
    # Process names only: arguments/environment can contain project data or credentials.
    pid = os.getpid()
    ancestry: list[str] = []
    for _ in range(32):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            end = stat.rindex(")")
            ancestry.append(stat[stat.index("(") + 1 : end])
            pid = int(stat[end + 2 :].split()[1])
        except (OSError, ValueError, IndexError):
            break
        if pid <= 0:
            break
    observations["process-ancestry:namespace-local"] = " <- ".join(ancestry)
    observations["process-ancestry:limitation"] = (
        "A sandbox can hide the host terminal/tmux; absence here is not evidence of absence."
    )
    return observations


def _targets(root: Path, profile: Profile) -> dict[Path, bytes]:
    config = _config()
    policy = font_policy(root)
    if profile == "fonts":
        return {
            _safe(config / "fontconfig/conf.d/60-setup-system-fonts.conf"): policy.render(
                root / "assets/fonts/60-setup-system-fonts.conf"
            )
        }
    folder = root / "assets/hyprland"
    targets = {
        config / "hypr/setup-system" / path.relative_to(folder): policy.render(path)
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


def plan(root: Path, profile: Profile, *, terminal_profile: str | None = None) -> WorkstationPlan:
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
        for key in (
            "XDG_SESSION_TYPE",
            "XDG_CURRENT_DESKTOP",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TERM",
            "TERM_PROGRAM",
            "TERM_PROGRAM_VERSION",
            "VTE_VERSION",
            "GNOME_TERMINAL_SCREEN",
        )
    }
    observations.update(_font_observations(root))
    observations["TMUX"] = "present" if os.environ.get("TMUX") else "unset"
    if shutil.which("gsettings"):
        schemas = _run(["gsettings", "list-schemas"]).splitlines()
        for schema, key in GNOME_FONT_KEYS:
            if schema in schemas:
                observations[f"{schema}:{key}"] = _run(["gsettings", "get", schema, key])
        if "org.gnome.desktop.interface" in schemas:
            for key in ("text-scaling-factor", "scaling-factor"):
                observations[key] = _run(["gsettings", "get", "org.gnome.desktop.interface", key])
        if "org.gnome.Terminal.ProfilesList" in schemas:
            observations["terminal:default-profile"] = _run(
                ["gsettings", "get", "org.gnome.Terminal.ProfilesList", "default"]
            )
            profiles = _terminal_profiles()
            if terminal_profile and terminal_profile not in profiles:
                blockers.append("Requested GNOME Terminal profile does not exist")
            for profile_id in profiles:
                schema = _terminal_schema(profile_id)
                for key in (
                    "visible-name",
                    "font",
                    "use-system-font",
                    "cell-width-scale",
                    "cell-height-scale",
                ):
                    observations[f"terminal:profiles:{profile_id}:{key}"] = _run(
                        ["gsettings", "get", schema, key]
                    )
            if terminal_profile:
                observations["terminal:requested-profile"] = terminal_profile
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


def _settings(
    policy: FontPolicy,
    gnome: bool,
    terminal_profile: str | None,
    reset_terminal_cell_spacing: bool,
) -> list[tuple[str, str, str]]:
    settings = []
    if gnome:
        for schema, key in GNOME_FONT_KEYS:
            style = " Bold" if key == "titlebar-font" else ""
            settings.append((schema, key, f"'{policy.family}{style} {policy.ui_size}'"))
    if reset_terminal_cell_spacing and not terminal_profile:
        raise ValueError("Reset cell spacing requires an explicitly selected terminal profile")
    if terminal_profile:
        schema = _terminal_schema(terminal_profile)
        if terminal_profile not in _terminal_profiles():
            raise ValueError("Terminal profile does not exist")
        if _run(["gsettings", "get", schema, "use-system-font"]) != "false":
            raise ValueError("Terminal uses system font; review that choice before overriding it")
        old = _run(["gsettings", "get", schema, "font"]).strip("'")
        size = old.rsplit(" ", 1)[-1]
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", size):
            raise ValueError("Cannot preserve terminal font size")
        settings.append((schema, "font", f"'{policy.family} {size}'"))
        if reset_terminal_cell_spacing:
            # Verified GNOME Terminal profile keys; not screen scaling or a zoom reset.
            settings.extend(
                (schema, key, "1.0") for key in ("cell-width-scale", "cell-height-scale")
            )
    return settings


def backup(root: Path, profile: Profile) -> Path:
    """Back up profile-owned existing files; leave unrelated user files alone."""
    return new_backup([path for path in _targets(root, profile) if path.exists()])


def apply(
    root: Path,
    profile: Profile,
    *,
    gnome: bool = False,
    terminal_profile: str | None = None,
    reset_terminal_cell_spacing: bool = False,
    refresh_terminal_font: bool = False,
) -> Path:
    """Apply portable files with conflict checks and a recoverable per-write receipt."""
    report = plan(root, profile, terminal_profile=terminal_profile)
    if report.blockers:
        raise ValueError("; ".join(report.blockers))
    if profile != "fonts" and (
        gnome or terminal_profile or reset_terminal_cell_spacing or refresh_terminal_font
    ):
        raise ValueError("GNOME options belong to the fonts profile")
    policy = font_policy(root)
    desired = _targets(root, profile)
    if profile == "fonts":
        for family in (policy.emoji_fallback,):
            if family not in _run(["fc-match", "-f", "%{family}", family]):
                raise ValueError("Install the distro's emoji fallback before applying this profile")
        desired.update(_font_files(root))
    receipt_path = _receipt()
    previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    for path, content in desired.items():
        if path.exists() and path.read_bytes() != content:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            known_alias = (
                profile == "fonts"
                and path == _config() / "fontconfig/conf.d/60-setup-system-fonts.conf"
                and digest in policy.previous_fontconfig_sha256
            )
            if previous.get(str(path)) != digest and not known_alias:
                raise ValueError(f"Locally edited/unmanaged file requires review: {path}")
    settings = _settings(policy, gnome, terminal_profile, reset_terminal_cell_spacing)
    refresh = _prepare_terminal_refresh(policy, terminal_profile) if refresh_terminal_font else None
    setting_rows = []
    for schema, key, value in settings:
        old = _run(["gsettings", "get", schema, key])
        if old != value or (
            refresh is not None and schema == refresh.settings_schema and key == "font"
        ):
            setting_rows.append({"schema": schema, "key": key, "before": old, "after": value})
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
    if refresh is not None:
        atomic_write(
            folder / "terminal-refresh.json", (refresh.model_dump_json(indent=2) + "\n").encode()
        )
    for path, content in changed.items():
        atomic_write(path, content, 0o600 if path == receipt_path else 0o644)
    for row in setting_rows:
        _run(["gsettings", "set", row["schema"], row["key"], row["after"]])
        if _run(["gsettings", "get", row["schema"], row["key"]]) != row["after"]:
            raise RuntimeError("GSettings readback failed; backup contains rollback values")
    if profile == "fonts" and changed and refresh is None:
        _run(["fc-cache", "-f"])
    if refresh is not None:
        try:
            _refresh_terminal_font(refresh, folder)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ValueError(f"Terminal refresh failed: {exc}; backup: {folder}") from exc
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
