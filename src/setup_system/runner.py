"""Run a fixed local Make target with a private, immutable request."""

from __future__ import annotations

import json
import os
import pwd
import stat
import subprocess
import sys
import tempfile
from enum import StrEnum
from pathlib import Path
from threading import Event
from time import monotonic

import pexpect  # type: ignore[import-untyped]

INSTALLED = Path("/usr/lib/setup-system")


class Target(StrEnum):
    BASE_INSPECT = "base-inspect"
    BASE_PLAN = "base-plan"
    BASE_APPLY = "base-apply"
    BASE_USER_APPLY = "base-user-apply"
    WORKSPACE_INSPECT = "workspace-inspect"
    WORKSPACE_PLAN = "workspace-plan"
    WORKSPACE_APPLY = "workspace-apply"
    WORKSPACE_USER_APPLY = "workspace-user-apply"
    CLEANUP_PLAN = "cleanup-plan"
    CLEANUP_APPLY = "cleanup-apply"
    RESTORE_PLAN = "restore-plan"
    RESTORE_APPLY = "restore-apply"
    DISPLAY_PREVIEW = "display-preview"
    PROJECT_CHECK = "project-check"
    PROJECT_LIST = "project-list"
    TEMPLATE_VERSIONS = "template-versions"
    GIT_INSPECT = "git-inspect"
    PROJECT_EDITOR = "project-editor"
    PROJECT_CODEX = "project-codex"
    PROJECT_PREPARE_PLAN = "project-prepare-plan"
    PROJECT_PREPARE_APPLY = "project-prepare-apply"
    TEMPLATE_CACHE_PLAN = "template-cache-plan"
    TEMPLATE_CACHE_APPLY = "template-cache-apply"
    TEMPLATE_CREATE_PLAN = "template-create-plan"
    TEMPLATE_CREATE_APPLY = "template-create-apply"
    TEMPLATE_UPDATE_PLAN = "template-update-plan"
    TEMPLATE_UPDATE_APPLY = "template-update-apply"
    GIT_CONFIGURE_PLAN = "git-configure-plan"
    GIT_CONFIGURE_APPLY = "git-configure-apply"
    GIT_USE_PLAN = "git-use-plan"
    GIT_USE_APPLY = "git-use-apply"
    SHELL_CONFIGURE_PLAN = "shell-configure-plan"
    SHELL_CONFIGURE_APPLY = "shell-configure-apply"
    RTK_INSTALL_PLAN = "rtk-install-plan"
    RTK_INSTALL_APPLY = "rtk-install-apply"


FOREGROUND = frozenset({Target.PROJECT_EDITOR, Target.PROJECT_CODEX})
SYSTEM = frozenset({Target.BASE_APPLY, Target.WORKSPACE_APPLY, Target.CLEANUP_APPLY})


class BecomePassword:
    """Transfer a single response to Ansible's hidden prompt."""

    def __init__(self, value: str) -> None:
        if (
            not value
            or len(value.encode()) > 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("Enter a non-empty single-line password")
        self._response = value.encode()

    def take(self) -> bytes:
        response, self._response = self._response, b""
        if not response:
            raise RuntimeError("The authorization response has expired")
        return response

    def discard(self) -> None:
        self._response = b""


def resource_root() -> Path:
    """Prefer resources beside the executing module in source and wheel builds."""
    module = Path(__file__).absolute()
    if module.parent.parent.name == "src":
        root = module.parents[2]
    elif module.parent.parent == Path("/usr/lib/python3/dist-packages"):
        root = INSTALLED
    else:
        root = module.parent / "bundle"
    makefile = root / "Makefile"
    if any(parent.is_symlink() for parent in (makefile, *makefile.parents)):
        raise ValueError("Program resources cannot traverse symlinks")
    if makefile.is_file():
        return root
    raise RuntimeError("The configurator resources are missing")


def _environment() -> dict[str, str]:
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8"}
    for key in (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "CODEX_HOME",
        "WAYLAND_DISPLAY",
        "HYPRLAND_INSTANCE_SIGNATURE",
        "DISPLAY",
    ):
        if key in os.environ:
            environment[key] = os.environ[key]
    return environment


def _run_make(
    root: Path,
    target: Target,
    env: dict[str, str],
    password: BecomePassword | None,
    cancel: Event | None,
) -> int:
    args = ["-rR", "-j1", "--no-print-directory", "-f", str(root / "Makefile"), target.value]
    if target in FOREGROUND:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("Opening the project requires an interactive terminal")
        return subprocess.run(["/usr/bin/make", *args], cwd=root, env=env, check=False).returncode
    child = pexpect.spawn(
        "/usr/bin/make",
        args,
        cwd=str(root),
        env=env,
        echo=True,
        timeout=None,
    )
    # Until the native getpass prompt no playbook task can have started.
    awaiting = password is not None
    deadline = monotonic() + 30
    tail = b""
    try:
        while True:
            if cancel is not None and cancel.is_set():
                if awaiting:
                    child.close(force=True)
                    return 130
                if "SETUP_SYSTEM_CANCEL" in env:
                    Path(env["SETUP_SYSTEM_CANCEL"]).touch(mode=0o600, exist_ok=True)
            if awaiting and monotonic() > deadline:
                child.close(force=True)
                raise RuntimeError("The native Ansible password prompt was unavailable")
            try:
                chunk = child.read_nonblocking(4096, timeout=0.2)
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                break
            if awaiting:
                tail = (tail + chunk)[-512:]
                if tail.split(b"\n")[-1] in (b"BECOME password: ", b"SUDO password: "):
                    if child.getecho():
                        child.close(force=True)
                        raise RuntimeError("Password prompt has terminal echo enabled")
                    assert password is not None
                    child.sendline(password.take())
                    password = None
                    awaiting = False
                    tail = b""
        child.close()
        return int(child.exitstatus if child.exitstatus is not None else 1)
    finally:
        if password is not None:
            password.discard()


class MakeRunner:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else resource_root()

    def run(
        self,
        target: Target,
        payload: dict[str, object],
        *,
        password: BecomePassword | None = None,
        cancel: Event | None = None,
    ) -> dict[str, object]:
        """Discard terminal output and read only the operation's structured result."""
        target = Target(target)
        if os.geteuid() == 0:
            raise ValueError("Open setup-system as your normal user")
        if cancel is not None and cancel.is_set():
            return {"status": "cancelled"}
        if target in SYSTEM and self.root != INSTALLED:
            raise ValueError("System changes require the installed Debian package")
        if (target in SYSTEM) != (password is not None):
            raise ValueError("The selected operation has a different authorization requirement")
        env = _environment()
        if target in FOREGROUND:
            for key in ("TERM", "COLORTERM"):
                value = os.environ.get(key) or ("xterm-256color" if key == "TERM" else "")
                if (
                    value
                    and len(value) <= 80
                    and all(c.isascii() and (c.isalnum() or c in "-_.+") for c in value)
                ):
                    env[key] = value
        if self.root != INSTALLED:
            env["SETUP_SYSTEM_INTERPRETER"] = sys.executable
        with tempfile.TemporaryDirectory(prefix="setup-system-") as temporary:
            stage = Path(temporary)
            request = stage / "request.json"
            result = stage / "result.json"
            with open(request, "x", opener=lambda p, f: os.open(p, f, 0o600)) as out:
                json.dump(payload, out, allow_nan=False)
            env.update(
                SETUP_SYSTEM_REQUEST=str(request),
                SETUP_SYSTEM_RESULT=str(result),
                SETUP_SYSTEM_CANCEL=str(stage / "cancel"),
            )
            code = _run_make(self.root, target, env, password, cancel)
            password = None
            if code == 130:
                return {"status": "cancelled"}
            if not result.is_file():
                raise RuntimeError(
                    "Operation failed. Check authorization, prerequisites and the operation lock."
                )
            with os.fdopen(os.open(result, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as f:
                info = os.fstat(f.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_size > 2**20
                ):
                    raise ValueError("Invalid operation result")
                data = json.loads(f.read(2**20 + 1))
            if not isinstance(data, dict) or not isinstance(data.get("status"), str):
                raise ValueError("Invalid operation result")
            if code and data.get("status") != "blocked":
                raise RuntimeError(f"Native operation failed with exit status {code}")
            return data
