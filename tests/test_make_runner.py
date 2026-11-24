"""Exercise the fixed Make invocation and native PTY password exchange."""

import json
import os
from pathlib import Path
from threading import Event

import pytest

from setup_system.runner import BecomePassword, MakeRunner, Target


def fixture_make(tmp_path: Path, body: str) -> MakeRunner:
    (tmp_path / "Makefile").write_text(
        "workspace-plan workspace-apply:\n\t@/usr/bin/python3 -I task.py\n"
    )
    (tmp_path / "task.py").write_text(body)
    return MakeRunner(tmp_path)


def test_request_is_data_and_host_make_overrides_are_absent(tmp_path, monkeypatch):
    runner = fixture_make(
        tmp_path,
        """import json, os, stat
from pathlib import Path
p=Path(os.environ['SETUP_SYSTEM_REQUEST'])
assert stat.S_IMODE(p.stat().st_mode)==0o600
assert 'ANSIBLE_LIBRARY' not in os.environ
assert 'eval' not in os.getenv('MAKEFLAGS','')
value=json.loads(p.read_bytes())['settings']['project']
Path(os.environ['SETUP_SYSTEM_RESULT']).write_text(json.dumps({'status':'ready','observed':{'project':value}}))
""",
    )
    monkeypatch.setenv("MAKEFLAGS", "--eval=bad")
    monkeypatch.setenv("ANSIBLE_LIBRARY", str(tmp_path))
    value = 'a $() " with spaces\n資料'
    answer = runner.run(Target.WORKSPACE_PLAN, {"settings": {"project": value}})
    assert answer["observed"] == {"project": value}
    with pytest.raises(ValueError):
        runner.run("arbitrary", {})


def test_cancel_before_spawn(tmp_path):
    event = Event()
    event.set()
    assert MakeRunner(tmp_path).run(Target.BASE_APPLY, {}, cancel=event)["status"] == "cancelled"


def test_native_getpass_reads_only_the_hidden_prompt(tmp_path):
    import secrets

    from setup_system.runner import _run_make

    secret = secrets.token_hex(16)
    fixture_make(
        tmp_path,
        """import getpass, hashlib, json, os
from pathlib import Path
value=getpass.getpass("BECOME password: ")
assert value not in str(os.environ) and value not in repr(__import__('sys').argv)
Path(os.environ['SETUP_SYSTEM_RESULT']).write_text(json.dumps({'digest':hashlib.sha256(value.encode()).hexdigest()}))
""",
    )
    import hashlib

    dest = tmp_path / "result"
    env = {"PATH": "/usr/bin:/bin", "SETUP_SYSTEM_RESULT": str(dest)}
    assert _run_make(tmp_path, Target.WORKSPACE_APPLY, env, BecomePassword(secret), None) == 0
    assert json.loads(dest.read_bytes())["digest"] == hashlib.sha256(secret.encode()).hexdigest()
    assert all(secret.encode() not in p.read_bytes() for p in tmp_path.iterdir() if p.is_file())


def test_visible_prompt_cannot_receive_a_secret(tmp_path):
    from setup_system.runner import _run_make

    fixture_make(
        tmp_path,
        """import sys
print("BECOME password: ", end="", flush=True)
sys.stdin.readline()
""",
    )
    with pytest.raises(RuntimeError, match="echo"):
        _run_make(
            tmp_path, Target.WORKSPACE_APPLY, {"PATH": os.defpath}, BecomePassword("fixture"), None
        )


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
    lock_task = (root / "libexec/locked").read_text().replace("lock=/run/setup-system", "lock=.")
    (tmp_path / "lock-task").write_text(lock_task)
    (tmp_path / "Makefile").write_text("restore-apply:\n\t@/bin/sh ./lock-task /usr/bin/false\n")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = MakeRunner(tmp_path).run(Target.RESTORE_APPLY, {})
        assert result["status"] == "blocked"
        assert "retry" in str(result["blockers"]).lower()
    finally:
        os.close(fd)
