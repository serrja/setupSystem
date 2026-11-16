"""Portable user profiles, with one separate Debian desktop package backend."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path
from typing import Literal, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field

from setup_system.adapters.debian.hyprland import (
    HYPRLAND_VERSION,
    package_arguments,
    profile_capabilities,
)
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
        if re.search(
            r"@[A-Z_]+@",
            content.replace("@DEFAULT_AUDIO_SINK@", "").replace("@DEFAULT_AUDIO_SOURCE@", ""),
        ):
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


def _required_commands(root: Path) -> None:
    """Fail before user writes if the system stage did not deliver the configured tools."""
    commands = cast(list[str], profile_capabilities(root)["commands"])
    missing = [name for name in commands if shutil.which(name) is None]
    agent = Path("/usr/libexec/hyprpolkitagent")
    if not agent.is_file() or not os.access(agent, os.X_OK):
        missing.append(str(agent))
    if missing:
        raise ValueError("Hyprland system stage is incomplete; missing: " + ", ".join(missing))


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


def _xdg(name: str, default: str) -> Path:
    return _safe(Path(os.environ.get(name, Path.home() / default)))


def _targets(root: Path, profile: Profile) -> dict[Path, bytes]:
    config = _config()
    policy = font_policy(root)
    fontconfig = _safe(config / "fontconfig/conf.d/60-setup-system-fonts.conf")
    font_content = policy.render(root / "assets/fonts/60-setup-system-fonts.conf")
    if profile == "fonts":
        return {fontconfig: font_content}
    folder = root / "assets/hyprland"
    targets = {
        config / "hypr/setup-system" / path.relative_to(folder): policy.render(path)
        for path in folder.rglob("*")
        if path.is_file() and path.name != "start-hyprland"
    }
    entry = _safe(config / "hypr/hyprland.lua")
    original = entry.read_text() if entry.exists() else ""
    line = 'require("setup-system.profile")'
    if original.strip() and line not in original.splitlines():
        raise ValueError("Existing Hyprland entrypoint requires a reviewed manual include")
    targets[entry] = (
        "-- Managed by setupSystem; put personal overrides in local.lua.\n"
        + line
        + "\n"
        + 'local cfg = os.getenv("XDG_CONFIG_HOME") or (os.getenv("HOME") .. "/.config")\n'
        + 'local path = cfg .. "/hypr/local.lua"\n'
        + 'local source = io.open(path, "r")\n'
        + "if source then source:close(); dofile(path) end\n"
    ).encode()
    targets[fontconfig] = font_content
    targets[config / "xdg-desktop-portal/hyprland-portals.conf"] = (
        b"[preferred]\ndefault=hyprland;gtk\norg.freedesktop.impl.portal.FileChooser=gtk\n"
    )
    gtk = (
        "# Managed by setupSystem\n[Settings]\ngtk-theme-name=Adwaita-dark\n"
        "gtk-icon-theme-name=Adwaita\ngtk-cursor-theme-name=Adwaita\n"
        f"gtk-font-name={policy.family} {policy.ui_size}\n"
        "gtk-application-prefer-dark-theme=1\n"
    ).encode()
    targets[config / "gtk-3.0/settings.ini"] = gtk
    targets[config / "gtk-4.0/settings.ini"] = gtk
    data = _xdg("XDG_DATA_HOME", ".local/share")
    source = (folder / "start-hyprland").read_text()
    for placeholder, path in (
        ("CONFIG_HOME", config),
        ("DATA_HOME", data),
        ("STATE_HOME", _xdg("XDG_STATE_HOME", ".local/state")),
        ("CACHE_HOME", _xdg("XDG_CACHE_HOME", ".cache")),
    ):
        source = source.replace("@" + placeholder + "@", shlex.quote(str(path)))
    targets[data / "setup-system/bin/start-hyprland"] = source.encode()
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
            packages = package_arguments(root=root)
        except ValueError as exc:
            blockers.append(str(exc))
        return WorkstationPlan(
            profile=profile,
            os=release.get("PRETTY_NAME", "unknown"),
            architecture=platform.machine(),
            installed_hyprland=version,
            files=files,
            packages=tuple(packages),
            blockers=tuple(blockers),
            observations={
                "session-start": str(
                    _xdg("XDG_DATA_HOME", ".local/share") / "setup-system/bin/start-hyprland"
                ),
                "package-validation": (
                    "Preliminary; signed APT refresh and simulation occur only in apply"
                ),
                "locale-timezone-network": "Existing settings and connections are preserved",
            },
        )
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


def _font_manifest(root: Path) -> dict[str, object]:
    value = tomllib.loads((root / "manifests/fonts.toml").read_text())["jetbrains_mono_nerd_font"]
    if not isinstance(value, dict):
        raise ValueError("Invalid pinned font manifest")
    version, digest = value.get("version"), value.get("sha256")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", version):
        raise ValueError("Invalid pinned font version")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid pinned font digest")
    expected_url = (
        "https://github.com/ryanoasis/nerd-fonts/releases/download/v"
        + version
        + "/JetBrainsMono.zip"
    )
    if value.get("url") != expected_url:
        raise ValueError("Font archive must use its pinned official release URL")
    files = value.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 16:
        raise ValueError("Invalid pinned font file list")
    names: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Invalid pinned font file identity")
        name, checksum = item.get("name"), item.get("sha256")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name in {"", ".", ".."}
            or "\\" in name
            or any(ord(character) < 32 for character in name)
            or name in names
            or not isinstance(checksum, str)
            or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
        ):
            raise ValueError("Invalid pinned font file identity")
        names.add(name)
    return cast(dict[str, object], value)


def _font_cache_path(path: Path) -> Path:
    result = _safe(path)
    home = Path.home().resolve()
    for current in (result, *result.parents):
        if current.exists():
            info = current.lstat()
            if info.st_uid != os.geteuid():
                raise ValueError("Font cache path has a different owner")
            if current != result and not current.is_dir():
                raise ValueError("Font cache parent is not a directory")
        if current == home:
            break
    return result


def _font_cache(
    manifest: dict[str, object],
) -> tuple[Path, Path, bytes]:
    folder = _xdg("XDG_CACHE_HOME", ".cache") / "setup-system/downloads"
    name = "JetBrainsMono-" + str(manifest["version"]) + "-" + str(manifest["sha256"]) + ".zip"
    archive = _font_cache_path(folder / name)
    receipt = _font_cache_path(archive.with_suffix(".zip.json"))
    provenance = {
        "schema": 1,
        "owner": "setupSystem",
        "kind": "font-archive",
        "version": manifest["version"],
        "url": manifest["url"],
        "sha256": manifest["sha256"],
    }
    return archive, receipt, (json.dumps(provenance, sort_keys=True) + "\n").encode()


def _font_archive(path: Path, checksum: str) -> bytes:
    _font_cache_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_size > 80 * 1024 * 1024
        ):
            raise ValueError("Font cache must be a bounded user-owned regular file")
        content = stream.read(80 * 1024 * 1024 + 1)
    if len(content) > 80 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != checksum:
        raise ValueError("Font cache archive digest mismatch; preserve it and review the cache")
    return content


def _font_contents(content: bytes, manifest: dict[str, object]) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            if len(bundle.infolist()) > 4096:
                raise ValueError("Font archive contains too many entries")
            total = 0
            for item in cast(list[dict[str, str]], manifest["files"]):
                matches = [entry for entry in bundle.infolist() if entry.filename == item["name"]]
                if len(matches) != 1:
                    raise ValueError("Font archive has missing or duplicate managed files")
                entry = matches[0]
                mode = stat.S_IFMT(entry.external_attr >> 16)
                total += entry.file_size
                if (
                    entry.is_dir()
                    or mode not in {0, stat.S_IFREG}
                    or entry.file_size > 16 * 1024 * 1024
                    or total > 64 * 1024 * 1024
                ):
                    raise ValueError("Font archive member is unsafe or exceeds its size limit")
                data = bundle.read(entry)
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ValueError("Font file digest mismatch")
                result[item["name"]] = data
    except zipfile.BadZipFile:
        raise ValueError("Invalid cached font archive") from None
    return result


def _font_publish(path: Path, content: bytes) -> None:
    _font_cache_path(path)
    missing: list[Path] = []
    current = path.parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    for current in reversed(missing):
        current.mkdir(mode=0o700)
        current.chmod(0o700)
    _font_cache_path(path)
    descriptor, name = tempfile.mkstemp(prefix=".font-download-", dir=path.parent)
    candidate = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(candidate, path, follow_symlinks=False)
    finally:
        candidate.unlink(missing_ok=True)


def prefetch_workstation(root: Path) -> None:
    """Cache a pinned font archive without requiring installed graphical commands."""
    if os.geteuid() == 0:
        raise ValueError("Workstation prefetch must run as the target user")
    manifest = _font_manifest(root)
    archive, receipt, provenance = _font_cache(manifest)
    if receipt.exists() and (
        not receipt.is_file()
        or receipt.stat().st_size > 32 * 1024
        or receipt.read_bytes() != provenance
    ):
        raise ValueError("Font cache provenance differs; preserve it for review")
    if archive.exists():
        content = _font_archive(archive, str(manifest["sha256"]))
    else:
        host = "github.com"
        try:
            with urlopen(str(manifest["url"]), timeout=60) as response:
                final = urlsplit(response.geturl())
                if final.scheme != "https" or final.hostname not in {
                    "github.com",
                    "release-assets.githubusercontent.com",
                    "objects.githubusercontent.com",
                }:
                    raise ValueError("Font download redirected outside the pinned release hosts")
                content = response.read(80 * 1024 * 1024 + 1)
        except HTTPError as exc:
            host = urlsplit(exc.url).hostname or host
            raise ValueError(f"Font download failed: HTTP {exc.code} from {host}") from None
        except (URLError, TimeoutError):
            raise ValueError(f"Font download failed for host {host}") from None
        if len(content) > 80 * 1024 * 1024:
            raise ValueError("Font archive exceeds its size limit")
        if hashlib.sha256(content).hexdigest() != manifest["sha256"]:
            raise ValueError("Font archive digest mismatch")
    _font_contents(content, manifest)
    if not archive.exists():
        _font_publish(archive, content)
    if not receipt.exists():
        _font_publish(receipt, provenance)


def _font_files(root: Path, *, offline: bool = False) -> dict[Path, bytes]:
    manifest = _font_manifest(root)
    data = _safe(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")))
    destination = _safe(data / ("fonts/JetBrainsMonoNerdFont-" + str(manifest["version"])))
    expected = cast(list[dict[str, str]], manifest["files"])
    for item in expected:
        _safe(destination / item["name"])
    if all(
        (destination / item["name"]).is_file()
        and hashlib.sha256((destination / item["name"]).read_bytes()).hexdigest() == item["sha256"]
        for item in expected
    ):
        return {}
    if not offline:
        prefetch_workstation(root)
    archive, receipt, provenance = _font_cache(manifest)
    if not archive.is_file() or not receipt.is_file():
        raise ValueError("Verified font cache is missing; prefetch before offline apply")
    if receipt.stat().st_size > 32 * 1024 or receipt.read_bytes() != provenance:
        raise ValueError("Font cache provenance differs; preserve it for review")
    contents = _font_contents(_font_archive(archive, str(manifest["sha256"])), manifest)
    return {_safe(destination / name): content for name, content in contents.items()}


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
    offline: bool = False,
) -> Path:
    """Apply portable files with conflict checks and a recoverable per-write receipt."""
    if profile == "hyprland" and os.geteuid() == 0:
        raise ValueError("Hyprland user configuration cannot run as root")
    report = plan(root, profile, terminal_profile=terminal_profile)
    if report.blockers:
        raise ValueError("; ".join(report.blockers))
    if profile != "fonts" and (
        gnome or terminal_profile or reset_terminal_cell_spacing or refresh_terminal_font
    ):
        raise ValueError("GNOME options belong to the fonts profile")
    if profile == "hyprland":
        expected = str(profile_capabilities(root)["upstream_version"])
        if (
            report.installed_hyprland is None
            or re.search(
                r"(?<![0-9.])" + re.escape(expected) + r"(?![0-9.])", report.installed_hyprland
            )
            is None
        ):
            raise ValueError(f"Hyprland {expected} must be installed by setup.sh before user apply")
        _required_commands(root)
    policy = font_policy(root)
    desired = _targets(root, profile)
    if profile == "fonts":
        for family in (policy.emoji_fallback,):
            if family not in _run(["fc-match", "-f", "%{family}", family]):
                raise ValueError("Install the distro's emoji fallback before applying this profile")
    desired.update(_font_files(root, offline=True) if offline else _font_files(root))
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
        if not path.exists()
        or path.read_bytes() != content
        or (path.name == "start-hyprland" and not path.stat().st_mode & 0o100)
    }
    if not changed and not setting_rows and refresh is None:
        last = previous.get("__backup__")
        if isinstance(last, str):
            folder = _safe(Path(last))
            if folder.is_dir():
                return folder
        # Compatibility for the old receipt: no new backup or write is needed.
        return receipt_path.parent
    folder = new_backup([path for path in (*changed, receipt_path) if path.exists()])
    previous.update(
        {str(path): hashlib.sha256(content).hexdigest() for path, content in desired.items()}
    )
    previous["__backup__"] = str(folder)
    changed[receipt_path] = (json.dumps(previous, indent=2) + "\n").encode()
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
        executable = path.name == "start-hyprland"
        atomic_write(
            path,
            content,
            0o700 if executable else (0o600 if path == receipt_path else 0o644),
            preserve_existing_mode=not executable,
        )
        if executable:
            path.chmod(0o700)
    for row in setting_rows:
        _run(["gsettings", "set", row["schema"], row["key"], row["after"]])
        if _run(["gsettings", "get", row["schema"], row["key"]]) != row["after"]:
            raise RuntimeError("GSettings readback failed; backup contains rollback values")
    if changed and refresh is None:
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
    checked: set[Path] = set()
    for item in operation["files"]:
        path = _safe(Path(item["path"]))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["after"]:
            raise ValueError(f"Rollback conflicts with a later edit: {path}")
        checked.add(path)
        if item["created"]:
            paths.append(path)
    # Older operations also backed up an unchanged ownership receipt. It has no
    # operation-after hash, so only its unchanged backup-before hash is safe.
    manifest = json.loads((folder / "manifest.json").read_text())
    for item in manifest["items"]:
        relative = Path(item.get("home_relative") or Path(item["source"]).relative_to(Path.home()))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe backup path")
        path = _safe(Path.home() / relative)
        if path not in checked and (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != item.get("sha256")
        ):
            raise ValueError(f"Rollback lacks unchanged-target evidence: {path}")
    for row in operation["settings"]:
        if _run(["gsettings", "get", row["schema"], row["key"]]) != row["after"]:
            raise ValueError("Rollback conflicts with a later GSettings edit")
    results = [
        (path, "would-restore" if status == "conflict" else status)
        for path, status in restore(backup_id, apply=False)
    ]
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
