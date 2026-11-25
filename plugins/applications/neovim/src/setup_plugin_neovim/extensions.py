"""Resolve Neovim plugins to immutable commits and inspect offline parser state."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

from pydantic import Field, TypeAdapter

from setup_system.infrastructure.file_publication import move_new
from setup_system.infrastructure.file_publication import observe as file_state
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.user_storage import (
    checked_directory,
    inside_home,
    private_directory,
)
from setup_system_sdk import Inputs, Outcome, Plan, Resource, Result, owned_resources

ROOT = Path(__file__).parent / "resources"


class Source(Inputs):
    repo: str = Field(pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$")
    ref: str | None = Field(default=None, pattern=r"^refs/(heads|tags)/[A-Za-z0-9_.-]+$")
    major: int | None = Field(default=None, ge=0)


def catalog() -> dict[str, Source]:
    return TypeAdapter(dict[str, Source]).validate_python(
        tomllib.loads((ROOT / "editor.toml").read_text())["plugins"]
    )


def directory(name: str) -> Path:
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return checked_directory(inside_home(root / "setup-system" / name))


def git(*args: str) -> str:
    result = run_command(
        [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "http.followRedirects=false",
            "-c",
            "transfer.fsckObjects=true",
            *args,
        ],
        timeout=90,
        env={
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
        max_output_bytes=4 * 1024 * 1024,
        discard_stderr=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode:
        raise ValueError("Git could not verify or update the selected plugin checkout")
    return result.stdout.strip()


def revision(source: Source) -> str:
    ref = source.ref or f"refs/tags/v{source.major}.*"
    values = {
        name: sha
        for line in git("ls-remote", source.repo, ref, ref + "^{}").splitlines()
        for sha, name in [line.split("\t")]
        if re.fullmatch(r"[0-9a-f]{40}", sha)
    }
    if source.major is not None:
        matches = [
            (tuple(int(n) for n in match.groups()), raw)
            for raw in values
            if (match := re.fullmatch(r"refs/tags/v([0-9]+)\.([0-9]+)\.([0-9]+)", raw))
            and int(match[1]) == source.major
        ]
        if not matches:
            raise ValueError("No compatible stable plugin tag")
        ref = max(matches)[1]
    if ref not in values:
        raise ValueError("The upstream reference is unavailable")
    return values.get(ref + "^{}", values[ref])


def observe(path: Path, source: Source) -> str | None:
    checked_directory(path)
    if not path.exists():
        return None
    checked_directory(path / ".git")
    if not (path / ".git").is_dir() or git("-C", str(path), "rev-parse", "--show-toplevel") != str(
        path
    ):
        raise ValueError("An existing editor directory is not the selected checkout")
    if git("-C", str(path), "remote", "get-url", "origin") != source.repo or git(
        "-C", str(path), "status", "--porcelain"
    ):
        raise ValueError("An installed editor checkout has local changes or a different origin")
    return git("-C", str(path), "rev-parse", "HEAD")


def plan_plugins() -> Plan:
    root = directory("editor")
    rows = []
    for key, source in catalog().items():
        path = root / key
        installed = observe(path, source)
        selected = revision(source)
        if (
            installed
            and installed != selected
            and not any(r.identity == str(path) and r.after == installed for r in owned_resources())
        ):
            return Plan(
                outcome=Outcome.BLOCKED,
                summary="Adopt or review the existing editor checkout before updating",
            )
        rows.append(
            Resource(
                kind="git-checkout",
                identity=str(path),
                ownership="created" if installed is None else "modified",
                before=installed,
                after=selected,
                cleanup=False,
            )
        )
    return Plan(
        outcome=Outcome.READY if any(r.before != r.after for r in rows) else Outcome.NO_CHANGES,
        summary="Install the displayed Neovim plugin commits",
        resources=rows,
    )


def update(plan: Plan) -> Result:
    sources = catalog()
    for row in plan.resources:
        path = Path(row.identity)
        if (
            path.parent != directory("editor")
            or not row.after
            or not re.fullmatch(r"[0-9a-f]{40}", row.after)
        ):
            raise ValueError("Unexpected editor checkout")
        source = sources[path.name]
        if observe(path, source) != row.before:
            raise ValueError("A plugin checkout changed after confirmation")
        if row.before == row.after:
            continue
        with private_directory(path.parent):
            if row.before is None:
                git("clone", "--no-checkout", "--", source.repo, str(path))
            else:
                git("-C", str(path), "fetch", "--no-tags", "origin", row.after)
        git("-C", str(path), "checkout", "--detach", row.after)
        if observe(path, source) != row.after:
            return Result(outcome=Outcome.PARTIAL, message="Inspect the selected editor checkouts")
    return Result(
        outcome=Outcome.CHANGED, message="Editor checkouts updated", resources=plan.resources
    )


def verify_plugins(plan: Plan) -> Result:
    sources = catalog()
    verified = [
        r.model_copy(update={"verified": True})
        for r in plan.resources
        if observe(Path(r.identity), sources[Path(r.identity).name]) == r.after
    ]
    ok = len(verified) == len(plan.resources)
    return Result(
        outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
        message="Editor commits verified" if ok else "Some editor commits differ",
        resources=verified,
    )


def parsers(languages: list[str]) -> list[str]:
    entries = tomllib.loads((ROOT / "editor.toml").read_text())["parsers"]
    result = sorted({name for group in ["base", *languages] for name in entries[group]})
    if not all(re.fullmatch("[a-z][a-z0-9_]*", name) for name in result):
        raise ValueError("Unsupported parser name")
    return result


def parser_plan(languages: list[str]) -> Plan:
    root = directory("editor-parsers") / "parser"
    tree = directory("editor") / "nvim-treesitter"
    latest = observe(tree, catalog()["nvim-treesitter"])
    if not latest or not any(
        r.kind == "git-checkout" and r.identity == str(tree) and r.after == latest
        for r in owned_resources()
    ):
        return Plan(outcome=Outcome.BLOCKED, summary="Install and verify the editor plugins first")
    hashes = {row.identity: row.after for row in owned_resources() if row.kind == "parser"}
    rows = []
    for name in parsers(languages):
        path = inside_home(root / (name + ".so"))
        before = checksum(path)
        if before and hashes.get(str(path)) != before:
            return Plan(outcome=Outcome.BLOCKED, summary=f"Review the unmanaged parser {name}")
        if before is None:
            rows.append(
                Resource(kind="parser", identity=str(path), ownership="created", cleanup=True)
            )
    return Plan(
        outcome=Outcome.READY if rows else Outcome.NO_CHANGES,
        summary=f"Install missing parsers from the pinned Tree-sitter checkout {latest}",
        resources=rows,
    )


def checksum(path: Path) -> str | None:
    if not path.exists():
        return None
    inside_home(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def nvim(command: str, languages: list[str]) -> bool:
    root = inside_home(
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "nvim/init.lua"
    )
    managed = {r.identity: r.after for r in owned_resources() if r.kind == "file"}
    scripts = [
        root,
        *(root.parent / "lua").rglob("*.lua"),
        *(root.parent / "colors").rglob("*.lua"),
    ]
    if any(not managed.get(str(path)) or checksum(path) != managed[str(path)] for path in scripts):
        raise ValueError("Verify the managed Neovim profile before running the editor")
    checkouts = {r.identity: r.after for r in owned_resources() if r.kind == "git-checkout"}
    for name, source in catalog().items():
        path = directory("editor") / name
        if not checkouts.get(str(path)) or observe(path, source) != checkouts[str(path)]:
            raise ValueError("Verify the installed plugin checkouts before invoking Neovim")
    env = {
        "HOME": str(Path.home()),
        "PATH": str(Path.home() / ".local/bin") + ":/usr/bin:/bin",
        "CI": "true",
        "SETUP_EDITOR_CHECK": str(ROOT / "editor_check.lua"),
        "SETUP_EDITOR_PROFILES": json.dumps(languages),
        "SETUP_EDITOR_PARSERS": json.dumps(parsers(languages)),
        "FLUTTER_SUPPRESS_ANALYTICS": "true",
    }
    env.update(
        {
            k: os.environ[k]
            for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR")
            if k in os.environ
        }
    )
    with tempfile.TemporaryDirectory(prefix="setup-system-editor-") as stage:
        rc = run_command(
            [
                "/usr/bin/nvim",
                "--headless",
                "-n",
                "-i",
                "NONE",
                "-u",
                str(root),
                "-c",
                command,
                "+qa!",
            ],
            cwd=stage,
            env=env,
            timeout=180,
            check=False,
            discard_output=True,
            stdin=subprocess.DEVNULL,
        )
    return rc.returncode == 0


def install_parsers(plan: Plan, languages: list[str]) -> Result:
    names = [Path(r.identity).stem for r in plan.resources]
    if any(name not in parsers(languages) for name in names):
        raise ValueError("Unreviewed parser")
    success = nvim("TSInstallSync " + " ".join(names), languages)
    return Result(
        outcome=Outcome.CHANGED if success else Outcome.PARTIAL,
        message="Read back installed parsers",
    )


def verify_parsers(plan: Plan, languages: list[str]) -> Result:
    checked = nvim(
        (
            "lua local ok=pcall(dofile,vim.env.SETUP_EDITOR_CHECK); "
            "if not ok then vim.cmd('cquit 1') end"
        ),
        languages,
    )
    rows = [
        r.model_copy(
            update={"after": observed.sha256, "permissions": observed.mode, "verified": True}
        )
        for r in plan.resources
        if (observed := file_state(Path(r.identity))) is not None
    ]
    return Result(
        outcome=Outcome.COMPLETE
        if checked and len(rows) == len(plan.resources)
        else Outcome.PARTIAL,
        message="Editor smoke completed"
        if checked
        else "Review parser, formatter and language-server state",
        resources=rows,
    )


def parser_removal() -> Plan:
    root = directory("editor-parsers") / "parser"
    rows = []
    for held in owned_resources():
        if held.kind != "parser" or not held.cleanup or held.ownership != "created":
            continue
        path = Path(held.identity)
        if path.parent != root or not re.fullmatch(r"[a-z][a-z0-9_]*\.so", path.name):
            return Plan(outcome=Outcome.BLOCKED, summary="Review the recorded parser location")
        state = file_state(path)
        if state is None:
            continue
        if state.sha256 != held.after or state.mode != held.permissions:
            return Plan(outcome=Outcome.BLOCKED, summary=f"Review the changed parser {path.name}")
        rows.append(
            Resource(
                kind="parser",
                identity=str(path),
                ownership="created",
                effect="remove",
                before=state.sha256,
                permissions=state.mode,
                cleanup=True,
            )
        )
    return Plan(
        outcome=Outcome.READY if rows else Outcome.NO_CHANGES,
        summary="Remove the reviewed compiled parsers",
        resources=sorted(rows, key=lambda row: row.identity),
    )


def remove_parsers(plan: Plan) -> Result:
    if plan != parser_removal() or plan.outcome != Outcome.READY:
        return Result(outcome=Outcome.BLOCKED, message="The set of managed parsers changed")
    removed: list[Resource] = []
    for row in plan.resources:
        path = Path(row.identity)
        with private_directory(path.parent, create=False):
            stage = Path(tempfile.mkdtemp(dir=path.parent, prefix=".setup-system-parser-"))
            claimed = stage / "parser.so"
            try:
                move_new(path, claimed)
                state = file_state(claimed)
                if (
                    not state
                    or state.sha256 != row.before
                    or state.mode != row.permissions
                    or os.path.lexists(path)
                ):
                    return Result(
                        outcome=Outcome.PARTIAL,
                        message="A parser changed during removal",
                        resources=removed,
                    )
                claimed.unlink()
                removed.append(row.model_copy(update={"verified": True}))
            finally:
                if os.path.lexists(claimed) and not os.path.lexists(path):
                    move_new(claimed, path)
                if not any(stage.iterdir()):
                    stage.rmdir()
    return Result(
        outcome=Outcome.CHANGED, message="Removed the managed compiled parsers", resources=removed
    )
