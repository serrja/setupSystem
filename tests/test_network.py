"""Temporary download proxy parsing, provenance, secrecy and cleanup boundaries."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import signal
import stat
import subprocess
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from setup_system import downloads, network

ROOT = Path(__file__).resolve().parents[1]
NODE = "vless://00000000-0000-4000-8000-000000000001@node.example:443?encryption=none&security=tls&type=tcp&sni=example.com#PrivateName"


def write_engine_manifest(
    root: Path,
    digest: str,
    *,
    version: str = "26.11.9",
    asset: str = "Xray-linux-64.zip",
    architecture: str = "amd64",
    source: str = "https://github.com/XTLS/Xray-core/tree/676eaf66f8f05d4d558ee7a74b4736623d3feeb9",
) -> None:
    (root / "manifests").mkdir(exist_ok=True)
    (root / "manifests/platforms.toml").write_bytes(
        (ROOT / "manifests/platforms.toml").read_bytes()
    )
    (root / "manifests/network.toml").write_text(
        f"version={json.dumps(version)}\nsource={json.dumps(source)}\n"
        f"[linux.{architecture}]\nasset={json.dumps(asset)}\nsha256={json.dumps(digest)}\n"
    )


@pytest.mark.parametrize(
    ("transport", "security"),
    [("tcp", "tls"), ("tcp", "reality"), ("ws", "tls"), ("grpc", "tls"), ("grpc", "reality")],
)
def test_secure_uri_transports_build_only_owned_loopback_proxy(
    transport: str, security: str
) -> None:
    key = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
    uri = NODE.replace("security=tls", f"security={security}").replace(
        "type=tcp", f"type={transport}"
    )
    uri = uri.split("#")[0] + f"&pbk={key}&sid=0123&serviceName=rpc&path=%2Fws&host=edge.example"
    outbound = network.parse_subscription(uri.encode())
    config = network.proxy_config(outbound, 23891)
    assert config["inbounds"] == [
        {
            "tag": "installer",
            "listen": "127.0.0.1",
            "port": 23891,
            "protocol": "http",
            "settings": {"allowTransparent": False},
        }
    ]
    assert config["log"] == {"access": "none", "error": "none", "logLevel": "none", "dnsLog": False}
    assert config["outbounds"][0]["protocol"] == "vless"
    assert set(config) == {"log", "inbounds", "outbounds"}
    stream = outbound["streamSettings"]
    assert stream["security"] == security
    assert stream["network"] == ("raw" if transport == "tcp" else transport)
    if security == "tls":
        assert stream["tlsSettings"]["allowInsecure"] is False
    else:
        assert stream["realitySettings"]["publicKey"] == key


def test_plain_and_base64_subscriptions_choose_first_node_without_names() -> None:
    raw = NODE + "\n" + NODE.replace("node.example", "second.example")
    expected = network.parse_subscription(raw.encode())
    assert network.parse_subscription(base64.b64encode(raw.encode())) == expected
    assert expected["settings"]["address"] == "node.example"
    assert "PrivateName" not in json.dumps(expected)


@pytest.mark.parametrize("encoded", [False, True])
def test_mixed_subscription_selects_first_supported_secure_node(encoded: bool) -> None:
    unsafe = NODE.replace("security=tls", "security=none")
    text = "# provider names are ignored\nvmess://unsupported-fixture\n" + unsafe + "\n" + NODE
    raw = text.encode()
    if encoded:
        raw = base64.b64encode(raw)
    assert network.parse_subscription(raw) == network.parse_subscription(NODE.encode())


def test_native_subscription_skips_unsupported_node_before_valid_secure_node() -> None:
    valid = network.parse_subscription(NODE.encode())
    invalid = json.loads(json.dumps(valid))
    invalid["streamSettings"]["security"] = "none"
    assert network.parse_subscription(json.dumps([invalid, valid]).encode()) == valid


def test_native_json_rebuilds_outbound_and_drops_provider_global_program_settings() -> None:
    outbound = network.parse_subscription(NODE.encode())
    provider = {
        "inbounds": [{"listen": "0.0.0.0", "protocol": "tun"}],
        "log": {"error": "/private/file"},
        "routing": {"domainStrategy": "AsIs"},
        "outbounds": [{"protocol": "freedom"}, outbound],
    }
    assert network.parse_subscription(json.dumps(provider).encode()) == outbound
    legacy = {
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": "node.example",
                    "port": 443,
                    "users": [{"id": outbound["settings"]["id"], "encryption": "none"}],
                }
            ]
        },
        "streamSettings": outbound["streamSettings"],
    }
    assert network.parse_subscription(json.dumps(legacy).encode()) == outbound


@pytest.mark.parametrize(
    "change",
    [
        "allowInsecure=1",
        "type=xhttp",
        "security=none",
        "security=tls&fp=unknown",
        "flow=xtls-rprx-vision&type=ws",
        "plugin=execute",
        "security=reality&pbk=private-key",
        "security=reality&type=ws",
        "path=%0Asecret",
    ],
)
def test_unsupported_or_insecure_uris_fail_without_echoing_provider(change: str) -> None:
    uri = NODE.split("?")[0] + "?" + change
    with pytest.raises(network.NetworkError) as caught:
        network.parse_subscription(uri.encode())
    assert "node.example" not in str(caught.value)
    assert "private-key" not in str(caught.value)


@pytest.mark.parametrize(
    "content",
    [
        b"<html>403 secret</html>",
        b"happ://crypto-secret",
        b"vmess://private",
        b"{}",
        b"",
        b"x" * (network.MAX_SUBSCRIPTION + 1),
    ],
)
def test_unknown_encrypted_html_and_excessive_subscriptions_are_not_imported(
    content: bytes,
) -> None:
    with pytest.raises(network.NetworkError):
        network.parse_subscription(content)


def test_native_file_paths_and_insecure_tls_are_rejected() -> None:
    for field, value in (
        ("certificates", [{"certificateFile": "/private/key"}]),
        ("allowInsecure", True),
        ("masterKeyLog", "/private/log"),
    ):
        outbound = network.parse_subscription(NODE.encode())
        outbound["streamSettings"]["tlsSettings"][field] = value
        with pytest.raises(network.NetworkError):
            network.parse_subscription(json.dumps(outbound).encode())


@pytest.mark.parametrize(
    "url",
    [
        "http://provider.example/private",
        "happ://crypto-secret",
        "https://name:secret@provider.example/private",
        "https://provider.example/\nsecret",
        "https://provider.example/private secret",
    ],
)
def test_initial_url_requires_https_without_embedded_account_credentials(url: str) -> None:
    with pytest.raises(network.NetworkError) as caught:
        network._https(url)
    assert "private" not in str(caught.value) and "secret" not in str(caught.value)


@pytest.mark.parametrize("problem", [403, "network"])
def test_download_errors_do_not_expose_secret_url_or_reason(
    monkeypatch: pytest.MonkeyPatch, problem: int | str
) -> None:
    url = "https://provider.example/private-token?key=secret"

    class Opener:
        def open(self, *args: object, **kwargs: object) -> None:
            if isinstance(problem, int):
                raise HTTPError(url, problem, "password=secret", {}, None)
            raise URLError("password=secret")

    monkeypatch.setattr(downloads, "build_opener", lambda *args: Opener())
    with pytest.raises(network.NetworkError) as caught:
        network._fetch(url, 100)
    assert all(
        secret not in str(caught.value)
        for secret in ("private-token", "provider.example", "secret")
    )
    if isinstance(problem, int):
        assert str(problem) in str(caught.value)


def archive(entries: list[tuple[str, bytes]], *, symlink: bool = False) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        for name, content in entries:
            entry = zipfile.ZipInfo(name)
            entry.external_attr = (stat.S_IFLNK if symlink else stat.S_IFREG) << 16
            bundle.writestr(entry, content)
    return stream.getvalue()


@pytest.mark.parametrize("kind", ["valid", "hash", "traversal", "symlink", "duplicate", "missing"])
def test_engine_extracts_only_digest_checked_regular_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    entries = [("xray", b"binary")]
    if kind == "traversal":
        entries.append(("../escape", b"secret"))
    elif kind == "duplicate":
        entries.append(("xray", b"duplicate"))
    elif kind == "missing":
        entries = [("LICENSE", b"license")]
    if kind == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
            data = archive(entries)
    else:
        data = archive(entries, symlink=kind == "symlink")
    manifest = tmp_path / "manifests"
    manifest.mkdir()
    digest = "0" * 64 if kind == "hash" else hashlib.sha256(data).hexdigest()
    write_engine_manifest(tmp_path, digest)
    monkeypatch.setattr(network.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(network.platform, "system", lambda: "Linux")
    monkeypatch.setattr(network, "_fetch", lambda *args, **kwargs: data)
    session = tmp_path / "session"
    session.mkdir(mode=0o700)
    if kind != "valid":
        with pytest.raises(network.NetworkError):
            network._engine(tmp_path, session)
        assert not (tmp_path / "escape").exists()
    else:
        binary = network._engine(tmp_path, session)
        assert binary.read_bytes() == b"binary"
        assert binary.stat().st_mode & 0o777 == 0o700
        assert (session / "engine.zip").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", ["none", "inside", "config", "exited", "start"])
@pytest.mark.parametrize("shared", [False, True])
def test_temporary_proxy_owns_process_files_and_always_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, shared: bool
) -> None:
    monkeypatch.setattr(network.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(network.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(network, "_fetch", lambda *args, **kwargs: NODE.encode())
    sessions: list[Path] = []
    stopped: list[object] = []
    external = tmp_path / "preserved"
    external.write_text("external data")

    def engine(root: Path, directory: Path) -> Path:
        sessions.append(directory)
        assert directory.stat().st_mode & 0o777 == 0o700
        binary = directory / "xray"
        binary.write_text("must not execute")
        return binary

    def validate(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert "-test" in argv and "secret" not in repr(argv)
        assert options["stdout"] == options["stderr"] == subprocess.DEVNULL
        config = Path(argv[-1])
        assert config.stat().st_mode & 0o777 == 0o600
        assert json.loads(config.read_text())["inbounds"][0]["listen"] == "127.0.0.1"
        return subprocess.CompletedProcess(argv, 1 if failure == "config" else 0)

    class Process:
        def __init__(self, argv: list[str], **options: object) -> None:
            if failure == "start":
                raise OSError("private-token")
            assert options["start_new_session"] is (not shared)
            assert options["stdout"] == options["stderr"] == subprocess.DEVNULL

        def poll(self) -> int | None:
            return 1 if failure == "exited" else None

    class Connection:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(network, "_engine", engine)
    monkeypatch.setattr(network.subprocess, "run", validate)
    monkeypatch.setattr(network.subprocess, "Popen", Process)
    monkeypatch.setattr(network.socket, "create_connection", lambda *args, **kwargs: Connection())
    monkeypatch.setattr(
        network, "_stop", lambda process, *, own_group: stopped.append((process, own_group))
    )
    previous = signal.getsignal(signal.SIGTERM)
    if failure == "none":
        with network.temporary_proxy(
            ROOT, "https://provider.example/secret", share_process_group=shared
        ) as details:
            assert details.http_proxy.startswith("http://127.0.0.1:")
            assert details.session_dir.exists()
    else:
        with pytest.raises(network.NetworkError) as caught:
            with network.temporary_proxy(
                ROOT, "https://provider.example/secret", share_process_group=shared
            ):
                raise network.NetworkError("caller stopped")
        assert "private-token" not in str(caught.value)
    assert sessions and not sessions[0].exists()
    assert external.read_text() == "external data"
    assert len(stopped) == (0 if failure in {"config", "start"} else 1)
    if stopped:
        assert stopped[0][1] is (not shared)
    assert signal.getsignal(signal.SIGTERM) == previous


def test_root_never_fetches_or_starts_temporary_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        network, "_fetch", lambda *args, **kwargs: pytest.fail("Root network operation")
    )
    with pytest.raises(network.NetworkError, match="non-root"):
        with network.temporary_proxy(ROOT, "https://provider.example/secret"):
            pytest.fail("Root temporary proxy")


@pytest.mark.parametrize("seed_valid", [False, True])
def test_seed_is_verified_preserved_and_invalid_seed_uses_official_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed_valid: bool
) -> None:
    data = archive([("xray", b"verified engine")])
    manifest = tmp_path / "manifests"
    manifest.mkdir()
    write_engine_manifest(tmp_path, hashlib.sha256(data).hexdigest())
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    seed = downloads / "xray-v26.11.9-amd64.zip"
    original = data if seed_valid else b"unrelated stale file"
    seed.write_bytes(original)
    calls: list[str] = []

    def fetch(url: str, limit: int, *, asset: bool) -> bytes:
        calls.append(url)
        assert (
            asset
            and url
            == "https://github.com/XTLS/Xray-core/releases/download/v26.11.9/Xray-linux-64.zip"
        )
        return data

    monkeypatch.setattr(network, "_fetch", fetch)
    monkeypatch.setattr(network.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(network.platform, "system", lambda: "Linux")
    session = tmp_path / "session"
    session.mkdir()
    assert network._engine(tmp_path, session).read_bytes() == b"verified engine"
    assert seed.read_bytes() == original
    assert len(calls) == (0 if seed_valid else 1)


@pytest.mark.parametrize("timeout", [False, True])
def test_cleanup_terminates_whole_owned_process_group_even_if_leader_exits(
    monkeypatch: pytest.MonkeyPatch, timeout: bool
) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(network.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    expire = timeout

    class Process:
        pid = 123456

        def __init__(self) -> None:
            self.calls = 0

        def wait(self, *, timeout: int) -> int:
            self.calls += 1
            if self.calls == 1 and expire:
                raise subprocess.TimeoutExpired("owned engine", timeout)
            return 0

    process = Process()
    network._stop(process)  # type: ignore[arg-type]
    assert killed == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    assert process.calls == 2


@pytest.mark.parametrize("timeout", [False, True])
def test_shared_worker_group_cleanup_signals_only_owned_engine_pid(
    monkeypatch: pytest.MonkeyPatch, timeout: bool
) -> None:
    events: list[str] = []
    expire = timeout
    monkeypatch.setattr(
        network.os,
        "killpg",
        lambda *args: pytest.fail("Worker must not signal its controller group"),
    )

    class Process:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")

        def wait(self, *, timeout: int) -> int:
            events.append("wait")
            if expire and events == ["terminate", "wait"]:
                raise subprocess.TimeoutExpired("owned engine", timeout)
            return 0

    network._stop(Process(), own_group=False)  # type: ignore[arg-type]
    assert events == (["terminate", "wait", "kill", "wait"] if timeout else ["terminate", "wait"])


@pytest.mark.parametrize(
    "change",
    [
        {"version": "latest"},
        {"version": "../26.11.9"},
        {"source": "https://github.com/XTLS/Xray-core/tree/main"},
        {"source": "https://untrusted.example/tree/" + "a" * 40},
        {"asset": "../../xray.zip"},
        {"asset": "unexpected.zip"},
        {"architecture": "x86_64"},
        {"architecture": "unknown"},
        {"digest": "invalid"},
    ],
)
def test_invalid_engine_policy_is_rejected_before_fetch_or_session_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, str]
) -> None:
    values = {"digest": "0" * 64, **change}
    write_engine_manifest(tmp_path, **values)
    monkeypatch.setattr(network, "_fetch", lambda *a, **kw: pytest.fail("Invalid pin fetched"))
    session = tmp_path / "session"
    session.mkdir()
    with pytest.raises(network.NetworkError, match="manifest or pin"):
        network._engine(tmp_path, session)
    assert list(session.iterdir()) == []


@pytest.mark.parametrize("machine,architecture", [("amd64", "amd64"), ("aarch64", "arm64")])
def test_engine_version_asset_and_architecture_are_selected_from_validated_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, machine: str, architecture: str
) -> None:
    data = archive([("xray", b"verified engine fixture")])
    asset = "Xray-linux-fixture.zip"
    write_engine_manifest(
        tmp_path,
        hashlib.sha256(data).hexdigest(),
        version="26.11.10",
        asset=asset,
        architecture=architecture,
    )
    monkeypatch.setattr(network.platform, "system", lambda: "Linux")
    monkeypatch.setattr(network.platform, "machine", lambda: machine)
    calls = []

    def fetch(url, limit, *, asset):
        assert asset and limit == network.MAX_ARCHIVE
        calls.append(url)
        return data

    monkeypatch.setattr(network, "_fetch", fetch)
    session = tmp_path / "session"
    session.mkdir()
    assert network._engine(tmp_path, session).read_bytes() == b"verified engine fixture"
    assert calls == ["https://github.com/XTLS/Xray-core/releases/download/v26.11.10/" + asset]
