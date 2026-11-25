"""Exercise fixed native targets and supervise real, isolated child processes."""

import asyncio
import hashlib
import os
import secrets
import shlex
import sys
from pathlib import Path

import pytest

from setup_system import make_transport as transport
from setup_system.authorization import BecomePassword
from setup_system.configuration import Settings


def fixture_make(tmp_path: Path, body: str) -> None:
    (tmp_path / "Makefile").write_text(
        "workspace-plan workspace-apply workspace-user-apply admin-apply:\n"
        "\t@umask 077; exec /usr/bin/python3 -I task.py\n"
    )
    (tmp_path / "task.py").write_text(body)


def run_transport(tmp_path, target, secret=None, *, cancel=None, phases=None):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "ANSIBLE_HOME": str(tmp_path / "ansible-home"),
        "SETUP_SYSTEM_RESULT": str(tmp_path / "result"),
        "SETUP_SYSTEM_CANCEL": str(tmp_path / "cancel"),
    }
    return transport.authenticated_make(
        tmp_path,
        ["-rR", "-j1", "-f", str(tmp_path / "Makefile"), target],
        env,
        cancel if cancel is not None else asyncio.Event(),
        phases.append if phases is not None else lambda phase: None,
        options=Settings.application(),
        take_password=secret.take,
        root_account=target == "admin-apply",
    )


@pytest.mark.parametrize(
    ("target", "prompt"), [("workspace-apply", "BECOME"), ("admin-apply", "SU")]
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
        asyncio.run(run_transport(tmp_path, "workspace-apply", BecomePassword("fixture")))


def test_response_is_one_use_and_has_no_plaintext_representation():
    response = BecomePassword("single-use-fixture")
    assert "single-use-fixture" not in repr(response)
    assert response.take() == b"single-use-fixture"
    with pytest.raises(RuntimeError, match="expired"):
        response.take()
    with pytest.raises(ValueError):
        BecomePassword("two\nlines")


@pytest.mark.parametrize("after_auth", [False, True])
def test_pty_read_failure_reaps_child_without_interrupting_mutation(
    tmp_path, monkeypatch, after_auth
):
    fixture_make(
        tmp_path,
        """import getpass, os, time
from pathlib import Path
getpass.getpass("BECOME password: ")
Path("consumed").touch()
# The runner must continue draining output until the native transaction finishes.
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
            if after_auth:
                async with asyncio.timeout(2):
                    while not (tmp_path / "consumed").exists():
                        await asyncio.sleep(0.005)
            raise OSError("private-test-transcript")
        return await expect(reader, *args, **kwargs)

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", spawned)
    monkeypatch.setattr(transport.os, "openpty", opened)
    monkeypatch.setattr(transport.fdspawn, "expect", broken)
    with pytest.raises(transport.TransportError) as error:
        asyncio.run(run_transport(tmp_path, "workspace-apply", BecomePassword("fixture")))
    assert "private-test-transcript" not in str(error.value)
    assert error.value.__context__ is None
    assert len(children) == len(masters) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(children[0], 0)
    with pytest.raises(OSError):
        os.fstat(masters[0])
    assert (tmp_path / "boundary").exists() == after_auth


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
                tmp_path, "workspace-apply", BecomePassword("fixture-only"), phases=phases
            )
        )
        == 0
    )
    assert transport.Phase.AUTHENTICATING in phases and transport.Phase.RUNNING in phases


def test_readiness_deadline_does_not_transmit_password(tmp_path, monkeypatch):
    fixture_make(tmp_path, "import time\ntime.sleep(10)\n")
    monkeypatch.setenv("SETUP_SYSTEM_PROMPT_TIMEOUT_SECONDS", "0.15")
    token = BecomePassword("test-only")
    with pytest.raises(transport.ReadinessTimeoutError, match="prompt"):
        asyncio.run(run_transport(tmp_path, "admin-apply", token))
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
        asyncio.run(run_transport(tmp_path, "admin-apply", BecomePassword("wrong"), phases=phases))
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
    monkeypatch.setenv("SETUP_SYSTEM_IDLE_TIMEOUT_SECONDS", "0.04")
    phases = []
    assert (
        asyncio.run(
            run_transport(tmp_path, "workspace-apply", BecomePassword("fixture"), phases=phases)
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
        asyncio.run(run_transport(tmp_path, "workspace-apply", BecomePassword("fixture")))
    assert "fixture" not in str(message.value)
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


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
            result = await transport.authenticated_make(
                tmp_path,
                ["-rR", "-f", str(tmp_path / "Makefile"), "admin-apply"],
                env,
                stop,
                notice,
                options=Settings.application(),
                take_password=token.take,
                root_account=True,
            )
        assert result == 130
        assert token.take() == b"fixture"

    asyncio.run(exercise())
    assert not (tmp_path / "changed").exists()


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
                await run_transport(tmp_path, "admin-apply", BecomePassword("fixture"))

    asyncio.run(exercise())
    assert not (tmp_path / "changed").exists()


def test_premature_terminal_eof_has_a_deadline_before_authorization(tmp_path, monkeypatch):
    fixture_make(tmp_path, "import time; time.sleep(0.6)")
    monkeypatch.setenv("SETUP_SYSTEM_PROMPT_TIMEOUT_SECONDS", "0.08")

    async def early_eof(self, *args, **kwargs):
        return 1

    monkeypatch.setattr(transport.fdspawn, "expect", early_eof)
    secret = BecomePassword("unused fixture response")
    with pytest.raises(transport.ReadinessTimeoutError):
        asyncio.run(run_transport(tmp_path, "admin-apply", secret))
    assert secret.take() == b"unused fixture response"


def test_installed_resources_require_protected_parents_and_recipes(tmp_path, monkeypatch):
    from setup_system import layout

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
    python_package = root / "python"
    python_package.mkdir(mode=0o755)
    for name, file in (
        ("setup_system_sdk", "worker.py"),
        ("setup_system-1.0.dist-info", "METADATA"),
    ):
        (python_package.parent / name).mkdir(mode=0o755)
        (python_package.parent / name / file).touch(mode=0o644)
    (python_package / "layout.py").touch(mode=0o644)
    monkeypatch.setattr(layout, "INSTALLED", root)
    monkeypatch.setattr(layout, "PYTHON_PACKAGE", python_package)
    registered = {root, *root.rglob("*")}
    monkeypatch.setattr(layout, "_package_paths", lambda: registered)
    layout.verify_installation(root)
    recipe.chmod(0o664)
    with pytest.raises(ValueError):
        layout.verify_installation(root)
    recipe.chmod(0o644)
    (recipes / "alias.mk").symlink_to(recipe)
    with pytest.raises(ValueError):
        layout.verify_installation(root)


def test_second_prompt_with_echo_enabled_is_dismissed_without_input(tmp_path):
    fixture_make(
        tmp_path,
        """import getpass, signal
from pathlib import Path
getpass.getpass("SU password: ")
signal.alarm(1)
input("Verification code: ")
Path("changed").touch()
""",
    )
    with pytest.raises(transport.AuthorizationError, match="Additional native"):
        asyncio.run(run_transport(tmp_path, "admin-apply", BecomePassword("fixture")))
    assert not (tmp_path / "changed").exists()


def test_second_raw_prompt_receives_hangup_without_another_secret(tmp_path):
    fixture_make(
        tmp_path,
        """import errno, getpass, os, signal, tty
from pathlib import Path
getpass.getpass("SU password: ")
tty.setraw(0)
signal.alarm(2)
os.write(1, b"Verification code: ")
try:
    received = os.read(0, 1024)
except OSError as exc:
    assert exc.errno == errno.EIO
    received = b""
Path("unexpected-input" if received else "dismissed").touch()
""",
    )
    with pytest.raises(transport.AuthorizationError, match="Additional native"):
        asyncio.run(run_transport(tmp_path, "admin-apply", BecomePassword("fixture")))
    assert (tmp_path / "dismissed").exists() and not (tmp_path / "unexpected-input").exists()


@pytest.mark.parametrize("condition", ["reader_error", "cancel", "eof", "idle"])
def test_interrupted_input_cannot_leave_a_second_read_waiting(tmp_path, monkeypatch, condition):
    fixture_make(
        tmp_path,
        """import errno, getpass, os, select, sys
from pathlib import Path
assert getpass.getpass("SU password: ") == "fixture"
Path("consumed").touch()
if Path("eof").exists():
    os.close(1)
    os.close(2)
else:
    print("Another challenge", flush=True)
if not select.select([0], [], [], 0.7)[0]:
    sys.exit(2)
try:
    assert os.read(0, 32) == b""
except OSError as exc:
    assert exc.errno == errno.EIO
Path("finished").touch()
""",
    )
    # The child closes its output. Make retains a writer, so the fixture also
    # delivers the EOF callback that a disconnected reader would receive.
    if condition == "eof":
        (tmp_path / "eof").touch()

    async def exercise():
        cancel = asyncio.Event()
        expect = transport.fdspawn.expect
        calls = 0

        async def interrupt(reader, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                async with asyncio.timeout(2):
                    while not (tmp_path / "consumed").exists():
                        await asyncio.sleep(0.005)
                if condition == "reader_error":
                    raise OSError("simulated broken reader")
                if condition == "cancel":
                    cancel.set()
                if condition == "eof":
                    return 0
            return await expect(reader, *args, **kwargs)

        monkeypatch.setattr(transport.fdspawn, "expect", interrupt)
        if condition == "idle":
            monkeypatch.setenv("SETUP_SYSTEM_IDLE_TIMEOUT_SECONDS", "0.06")
        operation = run_transport(tmp_path, "admin-apply", BecomePassword("fixture"), cancel=cancel)
        if condition == "reader_error":
            with pytest.raises(transport.TransportError):
                await operation
        else:
            await operation
        assert (tmp_path / "finished").exists()

    asyncio.run(exercise())
