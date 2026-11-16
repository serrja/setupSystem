"""Exercise private worker IPC with owned unprivileged fixture processes only."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from setup_system import installation, network_session, network_worker
from setup_system.application import happ
from setup_system.network import NetworkError, ProxyDetails

ROOT = Path(__file__).resolve().parents[1]
SUBSCRIPTION = "https://subscription.invalid/private-fixture-token"


def controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, *, startup_timeout: float = 2
) -> tuple[network_session.TemporaryNetwork, dict[str, object]]:
    uid, gid = os.getuid(), os.getgid()
    home = tmp_path / "desktop home"
    home.mkdir()
    target = installation.TargetUser("network_fixture", uid, gid, home, "/bin/bash")
    environment = installation.user_environment(target, {})
    observed: dict[str, object] = {}
    monkeypatch.setattr(network_session.os, "geteuid", lambda: 0)
    groups = sorted({gid, *os.getgroups()})

    def group_lookup(name: str, primary: int) -> list[int]:
        assert name == target.name and primary == gid
        return groups

    monkeypatch.setattr(network_session.os, "getgrouplist", group_lookup)
    real_popen = subprocess.Popen

    def factory(argv: list[str], **options: object) -> subprocess.Popen[bytes]:
        assert argv[:3] == ["/usr/bin/python3", "-I", "-B"]
        assert SUBSCRIPTION not in repr(argv) + repr(options)
        assert options["env"] == environment
        assert options["user"] == uid and options["group"] == gid
        assert options["extra_groups"] == groups
        assert options["cwd"] == home and options["umask"] == 0o077
        assert options["stdin"] == options["stdout"] == subprocess.PIPE
        assert options["stderr"] == subprocess.DEVNULL
        assert options["start_new_session"] is True
        observed["argv"], observed["environment"] = argv, options["env"]
        for key in ("user", "group", "extra_groups"):
            options.pop(key)
        # Assertions above verify the intended root boundary. The actual fixture
        # stays at the existing normal UID and never invokes installer code.
        process = real_popen([sys.executable, "-I", "-B", "-c", body], **options)
        observed["process"] = process
        return process

    return network_session.TemporaryNetwork(
        ROOT,
        target,
        environment,
        factory=factory,
        startup_timeout=startup_timeout,
        shutdown_timeout=0.1,
    ), observed


def worker_script(
    action: str = "valid", *, proxy: str = "http://127.0.0.1:23456", artifact: str = "valid"
) -> str:
    return f"""
import hashlib,json,os,pathlib,sys,time
request=json.loads(sys.stdin.buffer.readline())
home=pathlib.Path(os.environ["HOME"])
(home/"request-metadata.json").write_text(json.dumps({{"keys":sorted(request),"operation":request["operation"],"digest":hashlib.sha256(request["subscription"].encode()).hexdigest()}}))
cache=pathlib.Path(os.environ["XDG_CACHE_HOME"])/"setup-system"
path=cache/"network-fixture"/"happ.deb"
path.parent.mkdir(parents=True)
path.write_bytes(b"synthetic package, never installed")
artifact={artifact!r}
if artifact=="outside":path=home/"network-fixture"/"happ.deb"
if artifact=="relative":path=pathlib.Path("network-fixture/happ.deb")
if artifact=="traversal":path=cache/"network-fixture"/".."/"network-fixture"/"happ.deb"
if artifact=="filename":path=cache/"network-fixture"/"other.deb"
action={action!r}
if action=="timeout":time.sleep(30)
if action=="failed":
 print(json.dumps({{"state":"failed","stage":"temporary-client","http_status":403}}),flush=True)
 print(json.dumps({{"state":"closed"}}),flush=True)
 raise SystemExit(5)
if action=="oversize":print("x"*9000,flush=True)
elif action=="invalid-json":print("not JSON",flush=True)
else:
 print(json.dumps({{"state":"progress","stage":"temporary-client"}}),flush=True)
 print(json.dumps({{"state":"ready","proxy":{proxy!r},"happ":str(path)}}),flush=True)
stop=json.loads(sys.stdin.buffer.readline())
if action=="no-close":raise SystemExit(0)
if action=="oversized-close":
 print(json.dumps({{"state":"progress","padding":"x"*9000}}),flush=True)
 print(json.dumps({{"state":"closed"}}),flush=True)
 raise SystemExit(0)
if action=="trailing-close":
 print(json.dumps({{"state":"closed"}}),flush=True)
 print("trailing unsolicited output",flush=True)
 raise SystemExit(0)
if stop!={{"operation":"stop"}}:raise SystemExit(7)
owned=cache/"network-fixture"/"happ.deb"
owned.unlink()
owned.parent.rmdir()
print(json.dumps({{"state":"closed"}}),flush=True)
"""


def test_controller_sends_subscription_only_on_stdin_and_requires_clean_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session, observed = controller(tmp_path, monkeypatch, worker_script())
    ready = session.start(SUBSCRIPTION)
    assert ready.http_proxy == "http://127.0.0.1:23456"
    assert ready.happ.exists()
    metadata = json.loads((session.target.home / "request-metadata.json").read_text())
    assert metadata == {
        "keys": ["operation", "schema", "subscription"],
        "operation": "start",
        "digest": hashlib.sha256(SUBSCRIPTION.encode()).hexdigest(),
    }
    assert SUBSCRIPTION not in repr(observed["argv"]) + repr(observed["environment"])
    session.close()
    session.close()
    assert not ready.happ.exists() and session.ready is None
    assert session.process is None
    process = observed["process"]
    assert isinstance(process, subprocess.Popen) and process.returncode == 0
    assert SUBSCRIPTION not in capsys.readouterr().out


@pytest.mark.parametrize(
    "proxy",
    [
        "https://127.0.0.1:23456",
        "http://remote.invalid:23456",
        "http://test:dummy@127.0.0.1:23456",
        "http://127.0.0.1:80",
        "http://127.0.0.1:23456/path",
        "http://127.0.0.1:23456/?key=dummy",
        "http://127.0.0.1:23456/#fragment",
    ],
)
def test_controller_rejects_remote_low_port_or_credential_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, proxy: str
) -> None:
    session, observed = controller(tmp_path, monkeypatch, worker_script(proxy=proxy))
    with pytest.raises(RuntimeError):
        session.start(SUBSCRIPTION)
    assert session.process is None and session.ready is None
    assert observed["process"].poll() is not None


@pytest.mark.parametrize("artifact", ["outside", "relative", "traversal", "filename"])
def test_controller_rejects_artifacts_outside_owned_cache_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    session, _ = controller(tmp_path, monkeypatch, worker_script(artifact=artifact))
    with pytest.raises(RuntimeError, match=r"artifact|owned session"):
        session.start(SUBSCRIPTION)
    assert session.process is None and session.ready is None


@pytest.mark.parametrize("action", ["oversize", "invalid-json", "failed", "timeout"])
def test_startup_failure_timeout_and_stdout_limit_never_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    session, observed = controller(
        tmp_path,
        monkeypatch,
        worker_script(action),
        startup_timeout=0.1 if action == "timeout" else 2,
    )
    with pytest.raises((RuntimeError, TimeoutError)) as caught:
        session.start(SUBSCRIPTION)
    assert SUBSCRIPTION not in str(caught.value)
    assert session.process is None and session.ready is None
    assert observed["process"].poll() is not None


def test_close_without_cleanup_ack_is_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, observed = controller(tmp_path, monkeypatch, worker_script("no-close"))
    session.start(SUBSCRIPTION)
    with pytest.raises(RuntimeError, match="cleanup"):
        session.close()
    assert session.process is None and session.ready is None
    assert observed["process"].poll() is not None


def test_partial_raw_stdin_writes_cannot_truncate_secret_handoff() -> None:
    reader, descriptor = os.pipe()

    class Writer:
        def __init__(self) -> None:
            self.written = bytearray()

        def fileno(self) -> int:
            return descriptor

        def write(self, data: bytes) -> int:
            count = os.write(descriptor, data[:7])
            self.written.extend(data[:count])
            return count

        def flush(self) -> None:
            pass

    writer = Writer()
    session = network_session.TemporaryNetwork(ROOT, None, {})
    session.process = SimpleNamespace(stdin=writer)
    message = {"schema": 1, "operation": "start", "subscription": SUBSCRIPTION}
    try:
        session._send(message)
        assert bytes(writer.written) == (json.dumps(message, ensure_ascii=True) + "\n").encode()
    finally:
        os.close(reader)
        os.close(descriptor)


def test_controller_kills_owned_descendant_when_worker_leader_has_exited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descendant = """import os,pathlib,signal,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
pathlib.Path(os.environ["HOME"],"descendant.pid").write_text(str(os.getpid()))
while True:time.sleep(1)
"""
    script = worker_script("no-close").replace(
        'if action=="no-close":raise SystemExit(0)',
        f"""if action=="no-close":
 import subprocess
 subprocess.Popen([sys.executable,"-I","-B","-c",{descendant!r}],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 deadline=time.monotonic()+3
 while not (home/"descendant.pid").exists():
  if time.monotonic()>deadline:raise SystemExit(9)
  time.sleep(0.01)
 raise SystemExit(0)""",
    )
    session, observed = controller(tmp_path, monkeypatch, script)
    session.start(SUBSCRIPTION)
    pid: int | None = None
    try:
        with pytest.raises(RuntimeError, match="cleanup"):
            session.close()
        pid = int((session.target.home / "descendant.pid").read_text())
        deadline = time.monotonic() + 3
        while True:
            proc = Path(f"/proc/{pid}/stat")
            if not proc.exists() or proc.read_text().split()[2] == "Z":
                break
            assert time.monotonic() < deadline, "Owned descendant survived failed-worker cleanup"
            time.sleep(0.01)
        assert observed["process"].poll() is not None
    finally:
        marker = session.target.home / "descendant.pid"
        if pid is None and marker.exists():
            pid = int(marker.read_text())
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("download_fails", [False, True])
def test_worker_downloads_happ_through_proxy_then_cleans_before_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, download_fails: bool
) -> None:
    home = tmp_path / "desktop home"
    home.mkdir()
    target = installation.TargetUser("network_fixture", os.getuid(), os.getgid(), home, "/bin/bash")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "fixture-prior-value")
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    events: list[str] = []
    messages: list[dict[str, object]] = []
    ready = threading.Event()
    engine = tmp_path / "engine-session"
    threads: list[threading.Thread] = []
    real_thread = threading.Thread

    class Input:
        def __init__(self) -> None:
            self.first = True

        def readline(self, limit: int) -> bytes:
            if self.first:
                self.first = False
                return (
                    json.dumps({"schema": 1, "operation": "start", "subscription": SUBSCRIPTION})
                    + "\n"
                ).encode()
            assert ready.wait(3)
            return b'{"operation":"stop"}\n'

    monkeypatch.setattr(network_worker.sys, "stdin", SimpleNamespace(buffer=Input()))
    monkeypatch.setattr(network_worker.signal, "signal", lambda *args: None)
    monkeypatch.setattr(network_worker.os, "kill", lambda *args: None)

    def make_thread(*args: object, **kwargs: object) -> threading.Thread:
        result = real_thread(*args, **kwargs)
        threads.append(result)
        return result

    monkeypatch.setattr(network_worker.threading, "Thread", make_thread)

    @contextmanager
    def proxy(root: Path, secret: str, *, share_process_group: bool):
        assert secret == SUBSCRIPTION and share_process_group is True
        engine.mkdir(mode=0o700)
        (engine / "private-config").write_text("synthetic")
        events.append("proxy")
        try:
            yield ProxyDetails("http://127.0.0.1:23456", engine)
        finally:
            (engine / "private-config").unlink()
            engine.rmdir()
            events.append("engine-clean")

    def download(root: Path, destination: Path) -> Path:
        events.append("happ-download")
        assert os.environ["HTTP_PROXY"] == os.environ["HTTPS_PROXY"] == "http://127.0.0.1:23456"
        assert not {"NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"} & os.environ.keys()
        assert destination.is_relative_to(home / ".cache/setup-system")
        assert destination.parent.name.startswith("network-")
        assert destination.parent.stat().st_mode & 0o777 == 0o700
        if download_fails:
            raise NetworkError("HTTP 403 from private-provider-token")
        destination.write_bytes(b"synthetic package")
        return destination

    def emit(message: dict[str, object]) -> None:
        messages.append(message)
        state = message["state"]
        if state in {"ready", "failed"}:
            ready.set()
        if state == "ready":
            events.append("ready")
            assert Path(str(message["happ"])).exists() and engine.exists()
        if state == "closed":
            assert not engine.exists()
            assert not list((home / ".cache/setup-system").glob("network-*"))
            events.append("closed")

    monkeypatch.setattr("setup_system.network.temporary_proxy", proxy)
    monkeypatch.setattr(happ, "download_happ", download)
    monkeypatch.setattr(network_worker, "_emit", emit)
    result = network_worker.serve_network(ROOT, target, None)
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert result == (5 if download_fails else 0)
    assert events == (
        ["proxy", "happ-download", "engine-clean", "closed"]
        if download_fails
        else ["proxy", "happ-download", "ready", "engine-clean", "closed"]
    )
    assert SUBSCRIPTION not in json.dumps(messages) and "private-provider-token" not in json.dumps(
        messages
    )
    if download_fails:
        assert {"state": "failed", "stage": "happ-download", "http_status": 403} in messages
        assert not any(message["state"] == "ready" for message in messages)


@pytest.mark.parametrize("kind", ["valid", "public", "symlink", "outside", "owner", "hardlink"])
def test_private_subscription_file_checks_never_echo_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = installation.TargetUser("fixture", os.getuid(), os.getgid(), home, "/bin/bash")
    path = home / "subscription"
    path.write_text(SUBSCRIPTION + "\n")
    path.chmod(0o600)
    if kind == "public":
        path.chmod(0o644)
    elif kind == "symlink":
        other = home / "alias"
        other.symlink_to(path)
        path = other
    elif kind == "outside":
        path = tmp_path / "outside"
        path.write_text(SUBSCRIPTION)
        path.chmod(0o600)
    elif kind == "owner":
        target = installation.TargetUser("fixture", os.getuid() + 1, os.getgid(), home, "/bin/bash")
    elif kind == "hardlink":
        os.link(path, home / "alias")
    if kind == "valid":
        assert network_worker._subscription(path, None, target) == SUBSCRIPTION
    else:
        with pytest.raises(ValueError) as caught:
            network_worker._subscription(path, None, target)
        assert SUBSCRIPTION not in str(caught.value)
    assert path.read_text().strip() == SUBSCRIPTION


@pytest.mark.parametrize("action", ["oversized-close", "trailing-close"])
def test_closure_stdout_is_bounded_and_has_no_unsolicited_trailing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    session, observed = controller(tmp_path, monkeypatch, worker_script(action))
    session.start(SUBSCRIPTION)
    with pytest.raises(RuntimeError):
        session.close()
    assert session.process is None and session.ready is None
    assert observed["process"].poll() is not None


@pytest.mark.parametrize(
    "value",
    [
        None,
        "http://subscription.invalid/private-fixture-token",
        "happ://crypto-private-fixture-token",
    ],
)
def test_missing_or_unsupported_noninteractive_subscription_is_not_echoed(
    tmp_path: Path, value: object
) -> None:
    target = installation.TargetUser("fixture", os.getuid(), os.getgid(), tmp_path, "/bin/bash")
    with pytest.raises(ValueError) as caught:
        network_worker._subscription(None, value, target)
    assert "private-fixture-token" not in str(caught.value)


def test_malformed_proxy_port_cannot_escape_as_a_raw_worker_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _ = controller(
        tmp_path,
        monkeypatch,
        worker_script(proxy="http://127.0.0.1:private-fixture-token"),
    )
    with pytest.raises((RuntimeError, ValueError)) as caught:
        session.start(SUBSCRIPTION)
    assert "private-fixture-token" not in str(caught.value)
    assert session.ready is None and session.process is None


def test_failed_worker_exposes_only_allowed_stage_and_http_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _ = controller(tmp_path, monkeypatch, worker_script("failed"))
    with pytest.raises(RuntimeError) as caught:
        session.start(SUBSCRIPTION)
    assert "temporary-client" in str(caught.value) and "HTTP 403" in str(caught.value)
    assert SUBSCRIPTION not in str(caught.value)
    assert session.ready is None and session.process is None


@pytest.mark.parametrize("closed_receipt", [False, True])
def test_stop_broken_pipe_preserves_failure_only_after_confirmed_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, closed_receipt: bool
) -> None:
    script = worker_script("failed").replace(
        'if action=="failed":\n print(', 'if action=="failed":\n os.close(0)\n print('
    )
    receipt = ' print(json.dumps({"state":"closed"}),flush=True)\n raise SystemExit(5)'
    script = script.replace(
        receipt,
        (' print(json.dumps({"state":"closed"}),flush=True)\n' if closed_receipt else "")
        + " time.sleep(0.3)\n raise SystemExit(5)",
    )
    session, observed = controller(tmp_path, monkeypatch, script)
    session.shutdown_timeout = 1
    with pytest.raises(RuntimeError) as caught:
        session.start(SUBSCRIPTION)
    if closed_receipt:
        assert "HTTP 403" in str(caught.value) and "temporary-client" in str(caught.value)
    else:
        assert "cleanup" in str(caught.value)
    assert session.process is None and session.ready is None
    assert observed["process"].poll() is not None
