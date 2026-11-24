"""Exercise fixed native targets and supervise real, isolated child processes."""

import asyncio
import hashlib
import os
import secrets
import shlex
import shutil
import sys
from pathlib import Path

import pytest

from setup_system import make_transport as transport
from setup_system.runner import AsyncMakeRunner, BecomePassword, Command, Target


def fixture_make(tmp_path: Path, body: str) -> None:
    (tmp_path / "Makefile").write_text(
        "workspace-plan workspace-apply workspace-user-apply admin-apply:\n"
        "\t@umask 077; exec /usr/bin/python3 -I task.py\n"
    )
    (tmp_path / "task.py").write_text(body)


def command(target, settings=None, expected=None):
    return Command.capture(target, {"schema": 1, "settings": settings or {}, "expected": expected})


def run_transport(tmp_path, target, secret=None, *, cancel=None, phases=None):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "ANSIBLE_HOME": str(tmp_path / "ansible-home"),
        "SETUP_SYSTEM_RESULT": str(tmp_path / "result"),
        "SETUP_SYSTEM_CANCEL": str(tmp_path / "cancel"),
    }
    return transport.execute_make(
        tmp_path,
        target,
        env,
        cancel if cancel is not None else asyncio.Event(),
        phases.append if phases is not None else lambda phase: None,
        take_password=secret.take if secret is not None else None,
    )


def test_request_is_detached_data_and_host_make_overrides_are_absent(tmp_path, monkeypatch):
    value = '/tmp/a $() " with spaces 資料'
    fields = {"project": value}
    request = command(Target.WORKSPACE_PLAN, fields)
    fields["project"] = "/tmp/other"
    fixture_make(
        tmp_path,
        """import json, os, stat
from pathlib import Path
p=Path(os.environ['SETUP_SYSTEM_REQUEST'])
assert stat.S_IMODE(p.stat().st_mode)==0o600
assert 'ANSIBLE_LIBRARY' not in os.environ and 'eval' not in os.getenv('MAKEFLAGS','')
assert not os.isatty(0) and not os.isatty(1)
Path('observed').write_text(json.loads(p.read_bytes())['settings']['project'])
Path(os.environ['SETUP_SYSTEM_RESULT']).write_text('{"status":"ready"}')
""",
    )
    monkeypatch.setenv("MAKEFLAGS", "--eval=bad")
    monkeypatch.setenv("ANSIBLE_LIBRARY", str(tmp_path))
    phases = []
    result = asyncio.run(AsyncMakeRunner(tmp_path).run(request, on_event=phases.append))
    assert result.status == "ready" and (tmp_path / "observed").read_text() == value
    assert phases and all(p.target == Target.WORKSPACE_PLAN for p in phases)
    assert value not in str(phases)
    for invalid in ("arbitrary", "run"):
        with pytest.raises(ValueError):
            command(invalid)
    with pytest.raises(ValueError):
        command(Target.WORKSPACE_PLAN, {"project": "/tmp/name\nbad"})


def test_cancel_before_spawn_discards_credentials(tmp_path):
    event = asyncio.Event()
    event.set()
    token = BecomePassword("sample")
    # Cancellation precedes authorizing or opening the system operation.
    cmd = command(Target.BASE_INSPECT)
    reply = asyncio.run(AsyncMakeRunner(tmp_path).run(cmd, password=token, cancel=event))
    assert reply.status == "cancelled"
    with pytest.raises(RuntimeError, match="expired"):
        token.take()


@pytest.mark.parametrize(
    ("target", "prompt"), [(Target.WORKSPACE_APPLY, "BECOME"), (Target.ADMIN_APPLY, "SU")]
)
def test_getpass_receives_only_the_hidden_prompt(tmp_path, target, prompt):
    secret = secrets.token_hex(16)
    fixture_make(
        tmp_path,
        f"""import getpass, hashlib, os, sys
from pathlib import Path
value=getpass.getpass("{prompt} password: ")
assert value not in str(os.environ) and value not in repr(sys.argv)
Path(os.environ['SETUP_SYSTEM_RESULT']).write_text(hashlib.sha256(value.encode()).hexdigest())
""",
    )
    phases = []
    assert asyncio.run(run_transport(tmp_path, target, BecomePassword(secret), phases=phases)) == 0
    assert (tmp_path / "result").read_text() == hashlib.sha256(secret.encode()).hexdigest()
    assert all(secret.encode() not in p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    assert secret not in str(phases)


def test_visible_prompt_cannot_receive_a_secret(tmp_path):
    fixture_make(tmp_path, 'print("BECOME password: ", end="", flush=True)\ninput()\n')
    with pytest.raises(transport.AuthorizationError, match="echo"):
        asyncio.run(run_transport(tmp_path, Target.WORKSPACE_APPLY, BecomePassword("fixture")))


def test_response_is_one_use_and_has_no_plaintext_representation():
    response = BecomePassword("single-use-fixture")
    assert "single-use-fixture" not in repr(response)
    assert response.take() == b"single-use-fixture"
    with pytest.raises(RuntimeError, match="expired"):
        response.take()
    with pytest.raises(ValueError):
        BecomePassword("two\nlines")


def test_two_writers_surface_the_native_lock_reason(tmp_path):
    import fcntl

    root = Path(__file__).resolve().parents[1]
    lock_task = (root / "libexec/locked.sh").read_text().replace("lock=/run/setup-system", "lock=.")
    (tmp_path / "lock-task").write_text(lock_task)
    shutil.copy2(root / "libexec/dpkg_lock.py", tmp_path / "dpkg_lock.py")
    (tmp_path / "Makefile").write_text("workspace-plan:\n\t@/bin/sh ./lock-task /usr/bin/false\n")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = asyncio.run(AsyncMakeRunner(tmp_path).run(command(Target.WORKSPACE_PLAN)))
        assert result.status == "blocked" and "retry" in str(result.blockers).lower()
    finally:
        os.close(fd)


@pytest.mark.parametrize("after_auth", [False, True])
def test_pty_read_failure_reaps_child_without_interrupting_mutation(
    tmp_path, monkeypatch, after_auth
):
    fixture_make(
        tmp_path,
        """import getpass, os, time
from pathlib import Path
getpass.getpass("BECOME password: ")
# The runner must leave the private channel and transaction alive until this boundary.
cancel=Path(os.environ['SETUP_SYSTEM_CANCEL'])
until=time.monotonic()+2
while not cancel.exists() and time.monotonic()<until: time.sleep(.01)
print("x"*100000, flush=True)
Path("boundary").write_text("finished")
""",
    )
    children = []
    masters = []
    spawn = asyncio.create_subprocess_exec
    openpty = os.openpty
    expect = transport.fdspawn.expect

    async def spawned(*args, **kwargs):
        proc = await spawn(*args, **kwargs)
        children.append(proc.pid)
        return proc

    def opened():
        master, slave = openpty()
        masters.append(master)
        return master, slave

    calls = 0

    async def broken(reader, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == (2 if after_auth else 1):
            raise OSError("private-test-transcript")
        return await expect(reader, *args, **kwargs)

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", spawned)
    monkeypatch.setattr(transport.os, "openpty", opened)
    monkeypatch.setattr(transport.fdspawn, "expect", broken)
    with pytest.raises(transport.TransportError) as error:
        asyncio.run(run_transport(tmp_path, Target.WORKSPACE_APPLY, BecomePassword("fixture")))
    assert "private-test-transcript" not in str(error.value)
    assert error.value.__context__ is None
    assert len(children) == len(masters) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(children[0], 0)
    with pytest.raises(OSError):
        os.fstat(masters[0])
    assert (tmp_path / "boundary").exists() == after_auth


def test_noninteractive_idle_timeout_and_read_only_cancel_reap_processes(tmp_path, monkeypatch):
    fixture_make(
        tmp_path,
        """import os, time
from pathlib import Path
Path("child").write_text(str(os.getpid()))
time.sleep(8)
Path("finished").touch()
""",
    )
    monkeypatch.setattr(transport, "IDLE_TIMEOUT", 0.2)
    with pytest.raises(transport.TransportError, match="read-only"):
        asyncio.run(run_transport(tmp_path, Target.WORKSPACE_PLAN))
    assert not (tmp_path / "finished").exists()


def test_cancelling_worker_keeps_writer_and_snapshot_until_safe_boundary(tmp_path, monkeypatch):
    import threading

    owner = threading.get_ident()
    create_marker = transport._cancel_at_boundary

    def marker(env):
        assert threading.get_ident() != owner
        create_marker(env)

    monkeypatch.setattr(transport, "_cancel_at_boundary", marker)
    fixture_make(
        tmp_path,
        """import json, os, time
from pathlib import Path
Path("writing").touch()
marker=Path(os.environ["SETUP_SYSTEM_CANCEL"])
until=time.monotonic()+4
while not marker.exists() and time.monotonic()<until: time.sleep(.01)
assert marker.exists()
time.sleep(.08)
assert Path(os.environ["SETUP_SYSTEM_REQUEST"]).is_file()
Path(os.environ["SETUP_SYSTEM_RESULT"]).write_text('{"status":"cancelled"}')
Path("boundary").touch()
""",
    )

    async def exercise():
        # Use a confirmed user mutation without invoking any system package tools.
        expected = {
            "status": "ready",
            "actions": [{"kind": "user_file", "path": ".config/test", "label": "theme"}],
            "observed": {"needs_user": True, "needs_system": False},
        }
        cmd = command(Target.WORKSPACE_USER_APPLY, {"desktop_theme": True}, expected)
        task = asyncio.create_task(AsyncMakeRunner(tmp_path).run(cmd))
        async with asyncio.timeout(3):
            while not (tmp_path / "writing").exists():
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert (tmp_path / "boundary").is_file()

    asyncio.run(exercise())


def test_real_ansible_asks_on_the_private_terminal_without_running_become(tmp_path):
    ansible = Path(sys.executable).parent / "ansible-playbook"
    assert ansible.is_file()
    (tmp_path / "play.yml").write_text(
        "- hosts: localhost\n  gather_facts: false\n  tasks:\n"
        "    - name: Observe the private terminal\n"
        "      ansible.builtin.debug:\n        msg: ready\n"
    )
    (tmp_path / "Makefile").write_text(
        "workspace-apply:\n\t@"
        + shlex.quote(str(ansible))
        + " -i localhost, -c local --ask-become-pass play.yml\n"
    )
    phases = []
    assert (
        asyncio.run(
            run_transport(
                tmp_path, Target.WORKSPACE_APPLY, BecomePassword("fixture-only"), phases=phases
            )
        )
        == 0
    )
    assert transport.Phase.AUTHENTICATING in phases and transport.Phase.RUNNING in phases


def test_readiness_deadline_does_not_transmit_password(tmp_path, monkeypatch):
    fixture_make(tmp_path, "import time\ntime.sleep(10)\n")
    monkeypatch.setattr(transport, "PROMPT_TIMEOUT", 0.15)
    token = BecomePassword("test-only")
    with pytest.raises(transport.ReadinessTimeoutError, match="prompt"):
        asyncio.run(run_transport(tmp_path, Target.ADMIN_APPLY, token))
    assert token.take() == b"test-only"


def test_wrong_native_password_does_not_start_configuration(tmp_path):
    fixture_make(
        tmp_path,
        """import getpass, sys
from pathlib import Path
if getpass.getpass("BECOME password: ") != "required": sys.exit(1)
Path("changed").touch()
""",
    )
    phases = []
    assert (
        asyncio.run(
            run_transport(tmp_path, Target.ADMIN_APPLY, BecomePassword("wrong"), phases=phases)
        )
        != 0
    )
    assert not (tmp_path / "changed").exists() and "wrong" not in str(phases)


def test_quiet_authenticated_writer_reports_status_until_it_finishes(tmp_path, monkeypatch):
    fixture_make(
        tmp_path,
        """import getpass, time
from pathlib import Path
getpass.getpass("BECOME password: ")
time.sleep(.18)
Path("boundary").touch()
""",
    )
    monkeypatch.setattr(transport, "IDLE_TIMEOUT", 0.04)
    phases = []
    assert (
        asyncio.run(
            run_transport(
                tmp_path, Target.WORKSPACE_APPLY, BecomePassword("fixture"), phases=phases
            )
        )
        == 0
    )
    assert (tmp_path / "boundary").is_file()
    assert phases.count(transport.Phase.WAITING) == 1


def test_spawn_failure_closes_both_pty_fds(tmp_path, monkeypatch):
    descriptors = []
    openpty = os.openpty

    def opened():
        pair = openpty()
        descriptors.extend(pair)
        return pair

    async def failed(*args, **kwargs):
        raise OSError("fixture transcript")

    monkeypatch.setattr(transport.os, "openpty", opened)
    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", failed)
    with pytest.raises(transport.TransportError, match="authorization terminal") as message:
        asyncio.run(run_transport(tmp_path, Target.WORKSPACE_APPLY, BecomePassword("fixture")))
    assert "fixture" not in str(message.value)
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_read_only_cancel_stops_even_a_recipe_that_ignores_term(tmp_path, monkeypatch):
    fixture_make(
        tmp_path,
        """import signal,time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path('ready').touch()
time.sleep(4)
Path('changed').touch()
""",
    )
    monkeypatch.setattr(transport, "TERMINATE_TIMEOUT", 0.1)

    async def exercise():
        stop = asyncio.Event()
        task = asyncio.create_task(run_transport(tmp_path, Target.WORKSPACE_PLAN, cancel=stop))
        async with asyncio.timeout(2):
            while not (tmp_path / "ready").exists():
                await asyncio.sleep(0.01)
            stop.set()
            assert await task == 130

    asyncio.run(exercise())
    assert not (tmp_path / "changed").exists()


def test_private_authorization_cancel_preserves_unused_token(tmp_path):
    fixture_make(
        tmp_path, 'import getpass\ngetpass.getpass("SU password: ")\nopen("changed","w").close()'
    )

    async def exercise():
        stop = asyncio.Event()
        token = BecomePassword("fixture")

        def notice(phase):
            if phase == transport.Phase.AUTHENTICATING:
                stop.set()

        env = {"SETUP_SYSTEM_CANCEL": str(tmp_path / "cancel")}
        async with asyncio.timeout(2):
            result = await transport.execute_make(
                tmp_path, Target.ADMIN_APPLY, env, stop, notice, take_password=token.take
            )
        assert result == 130
        assert token.take() == b"fixture"

    asyncio.run(exercise())
    assert not (tmp_path / "changed").exists()


def test_oversized_or_unrelated_command_is_rejected_before_spawn():
    for envelope in (
        {"schema": 1, "settings": {"project": "/" + "a" * (2**20)}, "expected": None},
        {"schema": 1, "settings": {}, "expected": None, "makefile": "/tmp/other"},
        {"schema": True, "settings": {}, "expected": None},
    ):
        with pytest.raises(ValueError):
            Command.capture(Target.WORKSPACE_PLAN, envelope)


def test_terminal_error_keeps_the_recorded_native_outcome(tmp_path, monkeypatch):
    from setup_system import runner

    async def broken(_root, _target, env, *_args, **_kwargs):
        Path(env["SETUP_SYSTEM_RESULT"]).write_text(
            '{"status":"partial","blockers":["Review recovery"]}'
        )
        Path(env["SETUP_SYSTEM_RESULT"]).chmod(0o600)
        raise transport.TransportError("Native channel interrupted")

    monkeypatch.setattr(runner, "execute_make", broken)
    with pytest.raises(transport.TransportError, match="Recorded native outcome: Partial") as fail:
        asyncio.run(AsyncMakeRunner(tmp_path).run(command(Target.WORKSPACE_PLAN)))
    assert "Review recovery" in str(fail.value)


def test_repeated_hidden_prompt_is_dismissed_without_a_second_response(tmp_path):
    fixture_make(
        tmp_path,
        "import getpass\nfrom pathlib import Path\n"
        "getpass.getpass('BECOME password: ')\n"
        "getpass.getpass('Verification code: ')\nPath('changed').touch()\n",
    )

    async def exercise():
        async with asyncio.timeout(2):
            with pytest.raises(transport.AuthorizationError, match="Additional native"):
                await run_transport(tmp_path, Target.ADMIN_APPLY, BecomePassword("fixture"))

    asyncio.run(exercise())
    assert not (tmp_path / "changed").exists()


def test_all_private_snapshot_io_runs_off_the_ui_thread(tmp_path, monkeypatch):
    import threading

    import aiofiles.tempfile
    import aiofiles.threadpool

    from setup_system import runner

    fixture_make(
        tmp_path,
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['SETUP_SYSTEM_RESULT']).write_text('{\"status\":\"ready\"}')\n",
    )
    owner = threading.get_ident()
    seen = set()

    def guard(module, name):
        original = getattr(module, name)

        def checked(*args, **kwargs):
            assert threading.get_ident() != owner, f"{name} blocked the UI loop"
            seen.add(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, checked)

    guard(runner, "_environment")
    guard(runner, "read_wire")
    guard(aiofiles.threadpool, "sync_open")
    guard(aiofiles.tempfile, "syncTemporaryDirectory")
    reply = asyncio.run(AsyncMakeRunner(tmp_path).run(command(Target.WORKSPACE_PLAN)))
    assert reply.status == "ready"
    assert seen == {"_environment", "read_wire", "sync_open", "syncTemporaryDirectory"}


def test_partial_native_outcome_survives_cancellation_exit_code(tmp_path, monkeypatch):
    from setup_system import runner

    async def interrupted(_root, _target, env, *_args, **_kwargs):
        path = Path(env["SETUP_SYSTEM_RESULT"])
        path.write_text('{"status":"partial","blockers":["Inspect recovery"]}')
        path.chmod(0o600)
        return 130

    monkeypatch.setattr(runner, "execute_make", interrupted)
    reply = asyncio.run(AsyncMakeRunner(tmp_path).run(command(Target.WORKSPACE_PLAN)))
    assert reply.status == "partial" and reply.blockers == ("Inspect recovery",)


def test_premature_terminal_eof_has_a_deadline_before_authorization(tmp_path, monkeypatch):
    fixture_make(tmp_path, "import time; time.sleep(0.6)")
    monkeypatch.setattr(transport, "PROMPT_TIMEOUT", 0.08)

    async def early_eof(self, *args, **kwargs):
        return 1

    monkeypatch.setattr(transport.fdspawn, "expect", early_eof)
    secret = BecomePassword("unused fixture response")
    with pytest.raises(transport.ReadinessTimeoutError):
        asyncio.run(run_transport(tmp_path, Target.ADMIN_APPLY, secret))
    assert secret.take() == b"unused fixture response"


def test_installed_resources_require_protected_parents_and_recipes(tmp_path, monkeypatch):
    from setup_system.infrastructure import system_paths

    # Give the temporary ancestors the package authority, then validate real entry modes.
    root = tmp_path / "installed"
    recipes = root / "mk"
    recipes.mkdir(parents=True)
    (root / "Makefile").write_text("include mk/base.mk\n")
    recipe = recipes / "base.mk"
    recipe.write_text("run:\n")
    original = Path.lstat

    def as_packaged(path):
        info = original(path)
        fields = list(info)
        fields[4] = 0
        if not path.is_relative_to(root):
            fields[0] &= ~0o022
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", as_packaged)
    for path in (root, recipes, recipe, root / "Makefile"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    system_paths.verify_program_tree(root)
    recipe.chmod(0o664)
    with pytest.raises(ValueError):
        system_paths.verify_program_tree(root)
    recipe.chmod(0o644)
    (recipes / "alias.mk").symlink_to(recipe)
    with pytest.raises(ValueError):
        system_paths.verify_program_tree(root)
