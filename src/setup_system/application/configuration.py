"""Apply the user configuration from portable assets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from setup_system.application.projectLayout import activeProjects, loadLayout
from setup_system.planning import createPlan
from setup_system.storage import atomicWrite, managedBlock, newBackup


def _assetTargets(
    root: Path, xdgConfig: Path, codexHome: Path, agentsHome: Path, terminalDev: bool
) -> dict[Path, Path]:
    result: dict[Path, Path] = {}
    for source in (root / "assets/zsh").glob("*.zsh"):
        result[xdgConfig / "zsh" / source.name] = source
    result[xdgConfig / "git/setup-system.inc"] = root / "assets/git/style.inc"
    if terminalDev:
        for source in (root / "assets/nvim").rglob("*"):
            if source.is_file():
                result[xdgConfig / "nvim" / source.relative_to(root / "assets/nvim")] = source
        for source in (root / "assets/tmux").rglob("*"):
            if source.is_file():
                result[xdgConfig / "tmux" / source.relative_to(root / "assets/tmux")] = source
    for source in (root / "assets/agents").rglob("*"):
        if not source.is_file():
            continue
        rel = source.relative_to(root / "assets/agents")
        if rel.parts[0] == "codex":
            result[codexHome / rel.name] = source
        else:
            result[agentsHome / rel] = source
    return result


def _mergeCodexConfig(original: str, home: Path) -> str:
    data = tomllib.loads(original) if original.strip() else {}
    if "sandbox_mode" in data or "sandbox_workspace_write" in data:
        raise ValueError("Remove legacy sandbox settings before enabling permission profiles")
    broad = str(home)
    if broad in data.get("projects", {}):
        if set(data["projects"][broad]) != {"trust_level"}:
            raise ValueError("Home trust entry has unknown fields; review it manually")
        lines = original.splitlines(keepends=True)
        header = f'[projects."{broad}"]'
        start = next(i for i, line in enumerate(lines) if line.strip() == header)
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines)
        )
        original = "".join(lines[:start] + lines[end:])
    replacement = 'default_permissions = ":read-only"'
    if re.search(r"(?m)^default_permissions\s*=", original):
        original = re.sub(r"(?m)^default_permissions\s*=.*$", replacement, original, count=1)
    else:
        original = replacement + "\n" + original
    tomllib.loads(original)
    return original


def _scopeConfig(root: Path, home: Path) -> str:
    projects = activeProjects(loadLayout(root, home=home))
    lines = ["# Local paths only. This file stays outside public Git.", "version = 1"]
    for name, path in projects.items():
        lines += ["", f"[scope.{json.dumps(name)}]", f"path = {json.dumps(str(path))}"]
    return "\n".join(lines) + "\n"


def applyConfiguration(
    root: Path, *, terminalDev: bool = False
) -> tuple[Path, list[tuple[str, str]]]:
    plan = createPlan(root, "terminalDev" if terminalDev else "base")
    if not plan.platform.implemented or plan.blocked:
        raise ValueError(plan.platform.reason)
    home = Path.home()
    xdgConfig = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    xdgCache = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache"))
    xdgState = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    codexHome = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    agentsHome = home / ".agents"
    if any(
        not path.resolve().is_relative_to(home.resolve())
        for path in (xdgConfig, xdgCache, xdgState)
    ):
        raise ValueError("XDG paths outside home require a separate scoped install")
    historyDirectory = xdgState / "zsh"
    for parent in (historyDirectory, *historyDirectory.parents):
        if parent == home:
            break
        if parent.is_symlink():
            raise ValueError("Zsh history path traverses a symlink")
    assets = _assetTargets(root, xdgConfig, codexHome, agentsHome, terminalDev)
    manifestPath = agentsHome / "manifest.json"
    previous = json.loads(manifestPath.read_text()) if manifestPath.exists() else {"assets": {}}
    previousHashes = previous.get("assets", {})
    for target, source in assets.items():
        if target.is_symlink():
            raise ValueError(f"Asset target is a symlink: {target}")
        if not target.exists():
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        expected = previousHashes.get(str(target))
        sourceHash = hashlib.sha256(source.read_bytes()).hexdigest()
        if expected is None and actual != sourceHash:
            raise ValueError(f"Existing unmanaged asset requires review: {target}")
        if expected is not None and actual not in {expected, sourceHash}:
            raise ValueError(f"Managed asset was locally edited: {target}")

    zshrc = home / ".zshrc"
    bashrc = home / ".bashrc"
    gitconfig = home / ".gitconfig"
    codexConfig = codexHome / "config.toml"
    codexRules = codexHome / "AGENTS.md"
    scopePath = xdgConfig / "agents/scopes.toml"
    cacheCompletion = xdgCache / "setup-system/codex.zsh"
    cacheVersion = xdgCache / "setup-system/codex.version"
    zshBody = (
        'if [ -r "${XDG_CONFIG_HOME:-$HOME/.config}/zsh/init.zsh" ]; then '
        'source "${XDG_CONFIG_HOME:-$HOME/.config}/zsh/init.zsh"; fi'
    )
    zshText = managedBlock(zshrc.read_text() if zshrc.exists() else "", zshBody)
    bashText = bashrc.read_text() if bashrc.exists() else ""
    if "# >>> setupSystem >>>" in bashText:
        bashText = managedBlock(bashText, None)
    gitText = gitconfig.read_text() if gitconfig.exists() else ""
    gitText = re.sub(
        r"(?m)^\[include\]\n[ \t]*path[ \t]*=[ \t]*~/setupSystem/git/config[ \t]*\n?",
        "",
        gitText,
    )
    gitBody = "[include]\n    path = " + json.dumps(str(xdgConfig / "git/setup-system.inc"))
    gitText = managedBlock(gitText, gitBody)
    codexText = _mergeCodexConfig(codexConfig.read_text() if codexConfig.exists() else "", home)
    policyText = (root / "assets/agents/policy/AGENTS.md").read_text()
    if codexRules.is_symlink():
        raise ValueError("Existing Codex AGENTS.md symlink requires review")
    desired: dict[Path, bytes] = {target: source.read_bytes() for target, source in assets.items()}
    desired.update(
        {
            zshrc: zshText.encode(),
            gitconfig: gitText.encode(),
            codexConfig: codexText.encode(),
            codexRules: policyText.encode(),
        }
    )
    if bashrc.exists() and bashrc.read_text() != bashText:
        desired[bashrc] = bashText.encode()
    if not scopePath.exists():
        desired[scopePath] = _scopeConfig(root, home).encode()

    command = shutil.which("codex")
    completion: bytes | None = None
    version: bytes | None = None
    if command:
        versionRun = subprocess.run(
            [command, "--version"], capture_output=True, timeout=15, check=False
        )
        if versionRun.returncode == 0:
            version = versionRun.stdout.strip() + b"\n"
            if (
                not cacheVersion.exists()
                or cacheVersion.read_bytes() != version
                or not cacheCompletion.exists()
            ):
                output = subprocess.run(
                    [command, "completion", "zsh"], capture_output=True, timeout=30, check=False
                )
                if output.returncode != 0 or not output.stdout:
                    raise RuntimeError("Codex Zsh completion generation failed")
                completion = output.stdout
    if completion is not None and version is not None:
        desired[cacheCompletion] = completion
        desired[cacheVersion] = version

    assetHashes = {
        str(target): hashlib.sha256(source.read_bytes()).hexdigest()
        for target, source in assets.items()
    }
    manifest = {"version": 1, "assets": {**previousHashes, **assetHashes}}
    desired[manifestPath] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    backup = newBackup([path for path in desired if path.exists()])
    historyDirectory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if agentsHome.exists() and agentsHome.stat().st_mode & 0o200 == 0:
        agentsHome.chmod(0o700)
    results: list[tuple[str, str]] = []
    for target, content in desired.items():
        status = atomicWrite(target, content, 0o644 if target in assets else 0o600)
        results.append((str(target), status))
    return backup, results
