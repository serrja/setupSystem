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

from setup_system.application.project_layout import active_projects, load_layout
from setup_system.planning import create_plan
from setup_system.storage import atomic_write, managed_block, new_backup


def _asset_targets(
    root: Path, xdg_config: Path, codex_home: Path, agents_home: Path, terminal_dev: bool
) -> dict[Path, Path]:
    result: dict[Path, Path] = {}
    for source in (root / "assets/zsh").glob("*.zsh"):
        result[xdg_config / "zsh" / source.name] = source
    result[xdg_config / "git/setup-system.inc"] = root / "assets/git/style.inc"
    if terminal_dev:
        for source in (root / "assets/nvim").rglob("*"):
            if source.is_file():
                result[xdg_config / "nvim" / source.relative_to(root / "assets/nvim")] = source
        for source in (root / "assets/tmux").rglob("*"):
            if source.is_file():
                result[xdg_config / "tmux" / source.relative_to(root / "assets/tmux")] = source
    for source in (root / "assets/agents").rglob("*"):
        if not source.is_file():
            continue
        rel = source.relative_to(root / "assets/agents")
        if rel.parts[0] == "codex":
            result[codex_home / rel.name] = source
        else:
            result[agents_home / rel] = source
    return result


def _merge_codex_config(original: str, home: Path) -> str:
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


def _scope_config(root: Path, home: Path) -> str:
    projects = active_projects(load_layout(root, home=home))
    lines = ["# Local paths only. This file stays outside public Git.", "version = 1"]
    for name, path in projects.items():
        lines += ["", f"[scope.{json.dumps(name)}]", f"path = {json.dumps(str(path))}"]
    return "\n".join(lines) + "\n"


def apply_configuration(
    root: Path, *, terminal_dev: bool = False
) -> tuple[Path, list[tuple[str, str]]]:
    plan = create_plan(root, "terminalDev" if terminal_dev else "base")
    if not plan.platform.implemented or plan.blocked:
        raise ValueError(plan.platform.reason)
    home = Path.home()
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    xdg_cache = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache"))
    xdg_state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    agents_home = home / ".agents"
    if any(
        not path.resolve().is_relative_to(home.resolve())
        for path in (xdg_config, xdg_cache, xdg_state)
    ):
        raise ValueError("XDG paths outside home require a separate scoped install")
    history_directory = xdg_state / "zsh"
    for parent in (history_directory, *history_directory.parents):
        if parent == home:
            break
        if parent.is_symlink():
            raise ValueError("Zsh history path traverses a symlink")
    assets = _asset_targets(root, xdg_config, codex_home, agents_home, terminal_dev)
    manifest_path = agents_home / "manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"assets": {}}
    previous_hashes = previous.get("assets", {})
    for target, source in assets.items():
        if target.is_symlink():
            raise ValueError(f"Asset target is a symlink: {target}")
        if not target.exists():
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        expected = previous_hashes.get(str(target))
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if expected is None and actual != source_hash:
            raise ValueError(f"Existing unmanaged asset requires review: {target}")
        if expected is not None and actual not in {expected, source_hash}:
            raise ValueError(f"Managed asset was locally edited: {target}")

    zshrc = home / ".zshrc"
    bashrc = home / ".bashrc"
    gitconfig = home / ".gitconfig"
    codex_config = codex_home / "config.toml"
    codex_rules = codex_home / "AGENTS.md"
    scope_path = xdg_config / "agents/scopes.toml"
    cache_completion = xdg_cache / "setup-system/codex.zsh"
    cache_version = xdg_cache / "setup-system/codex.version"
    zsh_body = (
        'if [ -r "${XDG_CONFIG_HOME:-$HOME/.config}/zsh/init.zsh" ]; then '
        'source "${XDG_CONFIG_HOME:-$HOME/.config}/zsh/init.zsh"; fi'
    )
    zsh_text = managed_block(zshrc.read_text() if zshrc.exists() else "", zsh_body)
    bash_text = bashrc.read_text() if bashrc.exists() else ""
    if "# >>> setupSystem >>>" in bash_text:
        bash_text = managed_block(bash_text, None)
    git_text = gitconfig.read_text() if gitconfig.exists() else ""
    git_text = re.sub(
        r"(?m)^\[include\]\n[ \t]*path[ \t]*=[ \t]*~/setupSystem/git/config[ \t]*\n?",
        "",
        git_text,
    )
    git_body = "[include]\n    path = " + json.dumps(str(xdg_config / "git/setup-system.inc"))
    git_text = managed_block(git_text, git_body)
    codex_text = _merge_codex_config(
        codex_config.read_text() if codex_config.exists() else "", home
    )
    policy_text = (root / "assets/agents/policy/AGENTS.md").read_text()
    if codex_rules.is_symlink():
        raise ValueError("Existing Codex AGENTS.md symlink requires review")
    desired: dict[Path, bytes] = {target: source.read_bytes() for target, source in assets.items()}
    desired.update(
        {
            zshrc: zsh_text.encode(),
            gitconfig: git_text.encode(),
            codex_config: codex_text.encode(),
            codex_rules: policy_text.encode(),
        }
    )
    if bashrc.exists() and bashrc.read_text() != bash_text:
        desired[bashrc] = bash_text.encode()
    if not scope_path.exists():
        desired[scope_path] = _scope_config(root, home).encode()

    command = shutil.which("codex")
    completion: bytes | None = None
    version: bytes | None = None
    if command:
        version_run = subprocess.run(
            [command, "--version"], capture_output=True, timeout=15, check=False
        )
        if version_run.returncode == 0:
            version = version_run.stdout.strip() + b"\n"
            if (
                not cache_version.exists()
                or cache_version.read_bytes() != version
                or not cache_completion.exists()
            ):
                output = subprocess.run(
                    [command, "completion", "zsh"], capture_output=True, timeout=30, check=False
                )
                if output.returncode != 0 or not output.stdout:
                    raise RuntimeError("Codex Zsh completion generation failed")
                completion = output.stdout
    if completion is not None and version is not None:
        desired[cache_completion] = completion
        desired[cache_version] = version

    asset_hashes = {
        str(target): hashlib.sha256(source.read_bytes()).hexdigest()
        for target, source in assets.items()
    }
    manifest = {"version": 1, "assets": {**previous_hashes, **asset_hashes}}
    desired[manifest_path] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    backup = new_backup([path for path in desired if path.exists()])
    history_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if agents_home.exists() and agents_home.stat().st_mode & 0o200 == 0:
        agents_home.chmod(0o700)
    results: list[tuple[str, str]] = []
    for target, content in desired.items():
        status = atomic_write(target, content, 0o644 if target in assets else 0o600)
        results.append((str(target), status))
    return backup, results
