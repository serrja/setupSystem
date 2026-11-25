"""Exercise package and native writer exclusion without touching the system locks."""

import asyncio
import fcntl
import hashlib
import os
import shlex
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from test_plugin_lifecycle import activation

from setup_system import make_transport, plugin_manager
from setup_system.application.rollback import RecoveryPlan
from setup_system.authorization import BecomePassword
from setup_system.plugin_manager import PluginManager, Snapshot
from setup_system_sdk import Mutation, Outcome, Plan, Result

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Package:
    root: Path
    lock: Path
    db: Path
    maintainer: Path


@pytest.fixture
def packaged_guard(tmp_path, monkeypatch):
    package = tmp_path / "package"
    helpers = package / "libexec"
    helpers.mkdir(parents=True)
    lock = tmp_path / "run/lock"
    lock.mkdir(parents=True)
    lock.chmod(0o755)
    db = tmp_path / "database"
    db.mkdir()
    for name in ("lock", "lock-frontend"):
        (db / name).touch()
    guard = helpers / "locked.sh"
    guard.write_text(
        (ROOT / "libexec/locked.sh")
        .read_text()
        .replace("lock=/run/setup-system", "lock=" + shlex.quote(str(lock)))
        .replace("'0:0:755'", f"'{os.getuid()}:{os.getgid()}:755'")
        .replace(
            "/usr/lib/setup-system/libexec/dpkg_lock.py", shlex.quote(str(helpers / "dpkg_lock.py"))
        )
    )
    guard.chmod(0o700)
    (helpers / "dpkg_lock.py").write_text(
        (ROOT / "libexec/dpkg_lock.py")
        .read_text()
        .replace("/var/lib/dpkg/lock", str(db / "lock"))
        .replace("/usr/lib/setup-system/plugin_catalog", str(package / "plugin_catalog"))
    )
    maintainer = tmp_path / "preinst"
    maintainer.write_text(
        (ROOT / "debian/guard.sh")
        .read_text()
        .replace("lock=/run/setup-system", "lock=" + shlex.quote(str(lock)))
        .replace("0:0:755", f"{os.getuid()}:{os.getgid()}:755")
        .replace("owner_uid=0", f"owner_uid={os.getuid()}")
    )
    maintainer.chmod(0o700)
    monkeypatch.setattr(make_transport, "INSTALLED", package)
    return Package(package, lock, db, maintainer)


def installed_host(monkeypatch):
    monkeypatch.setattr(make_transport, "PYTHON_PACKAGE", Path(make_transport.__file__).parent)


def operation(home):
    state = activation(home)
    snap = Snapshot(
        plugin_id=state.manifest.id,
        digest=state.digest,
        mutation=Mutation(
            action_id="configure", settings={}, inputs={}, plan=Plan(summary="Review")
        ),
    )
    return PluginManager(state_root=home / "state"), state, snap


@pytest.mark.parametrize(
    "writer", ["dpkg", "frontend", "setup-system", "missing", "symlink", "mode"]
)
@pytest.mark.parametrize(
    "operation_type", ["installed-user", "installed-restore", "privileged-checkout"]
)
def test_busy_or_missing_lock_blocks_actual_launcher_before_mutation(
    packaged_guard, home, monkeypatch, writer, operation_type
):
    package = packaged_guard
    manager, state, snap = operation(home)
    (home / "Makefile").write_text("write:\n\t@touch changed\n")
    token = None
    if operation_type == "privileged-checkout":
        state.manifest.actions[0].permissions.append("root")
        state.manifest.permissions.append("root")
        token = BecomePassword("fixture")
        monkeypatch.setattr(plugin_manager, "protected_resources", lambda state: home)
    else:
        installed_host(monkeypatch)
        monkeypatch.setattr(plugin_manager, "validate_home_access", lambda path: None)
    with ExitStack() as stack:
        if writer == "missing":
            package.lock.rmdir()
        elif writer == "symlink":
            other = package.lock.with_name("other")
            package.lock.rename(other)
            package.lock.symlink_to(other)
        elif writer == "mode":
            package.lock.chmod(0o777)
        elif writer == "setup-system":
            handle = os.open(package.lock, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, handle)
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:
            file = stack.enter_context(
                (package.db / ("lock" if writer == "dpkg" else "lock-frontend")).open("r+b")
            )
            fcntl.lockf(file, fcntl.LOCK_EX)
        if operation_type == "installed-restore":
            confirmed = RecoveryPlan(receipt="20260101T000000Z-aaaaaaaa", entries=())
            result = asyncio.run(manager.run_recovery(confirmed))
        else:
            result = asyncio.run(manager.runner.execute(state, snap, token))
        assert result.outcome is Outcome.BLOCKED and "writer lock" in result.message
        assert not (home / "changed").exists()
        if writer == "missing":
            assert not os.path.lexists(package.lock)
        if token:
            assert token.take() == b"fixture"


def test_package_update_sees_real_running_make_and_unlocked_target_follows(
    packaged_guard, home, monkeypatch
):
    installed_host(monkeypatch)
    monkeypatch.setattr(plugin_manager, "validate_home_access", lambda path: None)
    manager, state, snap = operation(home)
    (home / "Makefile").write_text("write:\n\t@/usr/bin/python3 -I -B task.py\n")
    (home / "task.py").write_text(
        "from pathlib import Path\nimport time\nPath('ready').touch()\n"
        "end=time.monotonic()+5\n"
        "while not Path('finish').exists() and time.monotonic()<end: time.sleep(.01)\n"
        "print("
        + repr(Result(outcome=Outcome.COMPLETE, message="Synthetic readback").model_dump_json())
        + ")\n"
    )

    async def exercise():
        task = asyncio.create_task(manager.runner.execute(state, snap, None))
        try:
            async with asyncio.timeout(4):
                while not (home / "ready").exists():
                    if task.done():
                        pytest.fail(str(await task))
                    await asyncio.sleep(0.01)
            denied = await asyncio.to_thread(
                subprocess.run, [packaged_guard.maintainer], capture_output=True, check=False
            )
            assert denied.returncode and b"busy" in denied.stderr
        finally:
            (home / "finish").touch()
            outcome = await task
        assert outcome.outcome is Outcome.COMPLETE

    asyncio.run(exercise())
    subprocess.run([packaged_guard.maintainer], check=True)


def test_privileged_guard_rechecks_confirmed_release_before_prompt(
    packaged_guard, home, monkeypatch
):
    manager, state, snap = operation(home)
    state.manifest.actions[0].permissions.append("root")
    state.manifest.permissions.append("root")
    package = packaged_guard.root
    (package / "sdk").mkdir()
    (package / "sdk/environment.mk").touch()
    catalog = package / "plugin_catalog"
    catalog.mkdir()
    (catalog / "demo.json").write_bytes(b"replacement")
    monkeypatch.setattr(plugin_manager, "protected_resources", lambda state: home)
    monkeypatch.setattr(plugin_manager, "INSTALLED", package)
    (home / "Makefile").write_text(
        "write:\n\t@umask 077; printf '%s' '"
        + Result(outcome=Outcome.COMPLETE, message="Synthetic native readback").model_dump_json()
        + '\' > "$$SETUP_SYSTEM_RESULT"\n'
    )
    token = BecomePassword("fixture")
    result = asyncio.run(manager.runner.execute(state, snap, token))
    assert result.outcome is Outcome.BLOCKED and token.take() == b"fixture"
    digest = hashlib.sha256(b"replacement").hexdigest()
    state = replace(state, digest=digest)
    snap = snap.model_copy(update={"digest": digest})
    result = asyncio.run(manager.runner.execute(state, snap, BecomePassword("fixture")))
    assert result.outcome is Outcome.COMPLETE
