"""Privileged acquisition validates caller, HTTPS, provenance and archive boundaries."""

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from setup_system.bootstrap import source as bootstrap
from setup_system.domain.system_commands import COMMAND
from setup_system.infrastructure.privileged import PrivilegedExecutor


def test_sudo_caller_must_match_all_passwd_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", str(os.getuid()))
    monkeypatch.setenv("SUDO_GID", str(os.getgid() + 1))
    monkeypatch.setenv("SUDO_USER", "fixture")
    with pytest.raises(ValueError):
        bootstrap.caller()


@pytest.mark.parametrize("uid", ["", "0", "-1", "not-a-uid"])
def test_unsafe_sudo_uid_rejected_before_network(monkeypatch: pytest.MonkeyPatch, uid: str) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", uid)
    with pytest.raises(ValueError):
        bootstrap.caller()


def test_executor_uses_system_python_and_data_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or SimpleNamespace(stdout=""),
    )
    PrivilegedExecutor("v1.2.3", "a" * 40).run(
        COMMAND.validate_python(
            {
                "action": "base",
                "settings": {
                    "schema_version": 1,
                    "hyprland": True,
                    "graphical_login": False,
                    "plymouth": False,
                },
            }
        )
    )
    argv, options = calls[0]
    assert argv[:8] == ["/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-B", "-S", "-c"]
    assert json.loads(options["input"])["action"] == "base"
    assert "--source" not in argv
    assert "PYTHONPATH" not in options["env"]
    assert "pip install" not in argv[-1] and "uv sync" not in argv[-1]


def archive(member: str, kind: bytes = tarfile.REGTYPE) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        entry = tarfile.TarInfo(member)
        entry.type = kind
        entry.linkname = "/etc/passwd"
        if kind == tarfile.REGTYPE:
            entry.size = 4
            tar.addfile(entry, io.BytesIO(b"data"))
        else:
            tar.addfile(entry)
    return data.getvalue()


@pytest.mark.parametrize(
    "member,kind",
    [
        ("../outside", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("setup_system-1.2.3/link", tarfile.SYMTYPE),
        ("other/source", tarfile.REGTYPE),
    ],
)
def test_unsafe_privileged_archives_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, member: str, kind: bytes
) -> None:
    raw = archive(member, kind)
    digest = hashlib.sha256(raw).hexdigest()

    def fetch(url, limit):
        if url.endswith("python-build-receipt.json"):
            return json.dumps(
                {
                    "schemaVersion": 1,
                    "sourceSha": "a" * 40,
                    "files": [{"name": "setup_system-1.2.3.tar.gz", "sha256": digest}],
                }
            ).encode()
        if url.endswith("checksums.sha256"):
            return f"{digest}  setup_system-1.2.3.tar.gz\n".encode()
        return raw

    monkeypatch.setattr(bootstrap, "fetch", fetch)
    # Redirect only the protected state destination into this disposable fixture.
    real_path = Path
    monkeypatch.setattr(
        bootstrap,
        "Path",
        lambda value: (
            tmp_path / "state" if value == "/var/lib/setup-system/releases" else real_path(value)
        ),
    )
    monkeypatch.setattr(bootstrap, "protected", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="Unsafe"):
        bootstrap.acquire("v1.2.3", "a" * 40)
    assert not (tmp_path / "outside").exists()


def test_native_provenance_mismatch_stops_before_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bootstrap,
        "fetch",
        lambda *args: json.dumps({"schemaVersion": 1, "sourceSha": "b" * 40}).encode(),
    )
    with pytest.raises(ValueError, match="provenance"):
        bootstrap.acquire("v1.2.3", "a" * 40)


def test_operation_lock_excludes_another_executor_and_releases_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = os.fstat

    def metadata(descriptor):
        info = original(descriptor)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink)

    monkeypatch.setattr(bootstrap, "protected", lambda path: None)
    monkeypatch.setattr(os, "fstat", metadata)
    with pytest.raises(ValueError, match="synthetic"):
        with bootstrap.operation_lock(tmp_path):
            with pytest.raises(RuntimeError, match="Another system"):
                with bootstrap.operation_lock(tmp_path):
                    pytest.fail("Concurrent executor entered")
            raise ValueError("synthetic")
    with bootstrap.operation_lock(tmp_path):
        pass


def test_initial_loader_resolves_only_a_published_tag_commit():
    from setup_system.bootstrap.loader import PROGRAM

    loader = {"__name__": "loader_fixture"}
    exec(compile(PROGRAM, "<release loader>", "exec"), loader)
    sha = "f" * 40
    tag = "v1.2.3"

    def fetch(url, limit):
        if "/releases/tags/" in url:
            return json.dumps(
                {"tag_name": tag, "draft": False, "published_at": "2026-11-01"}
            ).encode()
        if "/git/ref/" in url:
            return json.dumps(
                {"ref": "refs/tags/" + tag, "object": {"type": "tag", "sha": "a" * 40}}
            ).encode()
        assert url.endswith("a" * 40)
        return json.dumps({"object": {"type": "commit", "sha": sha}}).encode()

    assert loader["resolve"]({"release": tag, "source_sha": sha}, fetch).endswith("/" + sha + "/")
    with pytest.raises(ValueError, match="disagree"):
        loader["resolve"]({"release": tag, "source_sha": "b" * 40}, fetch)


def test_verified_extraction_reuses_only_the_identical_root_owned_tree(tmp_path, monkeypatch):
    name = "setup_system-1.2.3"
    raw = archive(name + "/README.md")
    digest = hashlib.sha256(raw).hexdigest()
    sha = "f" * 40

    def fetch(url, limit):
        if url.endswith("python-build-receipt.json"):
            return json.dumps(
                {
                    "schemaVersion": 1,
                    "sourceSha": sha,
                    "files": [{"name": name + ".tar.gz", "sha256": digest}],
                }
            ).encode()
        if url.endswith("checksums.sha256"):
            return (digest + "  " + name + ".tar.gz\n").encode()
        return raw

    monkeypatch.setattr(bootstrap, "fetch", fetch)
    monkeypatch.setattr(bootstrap, "protected", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bootstrap,
        "Path",
        lambda value: (
            tmp_path / "releases" if value == "/var/lib/setup-system/releases" else Path(value)
        ),
    )
    original = bootstrap.acquire("v1.2.3", sha)
    assert bootstrap.acquire("v1.2.3", sha) == original
    (original / "unknown").touch()
    with pytest.raises(ValueError, match="conflicts"):
        bootstrap.acquire("v1.2.3", sha)
    assert list(original.parent.iterdir()) == [original]


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_root_requires_an_integer_native_receipt_version(monkeypatch, version):
    sha = "a" * 40
    urls = []

    def fetch(url, limit):
        urls.append(url)
        return json.dumps({"schemaVersion": version, "sourceSha": sha}).encode()

    monkeypatch.setattr(bootstrap, "fetch", fetch)
    with pytest.raises(ValueError, match="provenance"):
        bootstrap.acquire("v1.2.3", sha)
    assert len(urls) == 1 and urls[0].endswith("/python-build-receipt.json")


def test_login_confirmation_crosses_stdlib_envelope_without_host_elevation(tmp_path, monkeypatch):
    import sys
    from contextlib import nullcontext

    from test_desktop_system import system_fixture

    from setup_system.bootstrap import validation_runtime
    from setup_system.domain.system_commands import LoginCommand, LoginOptions
    from setup_system.infrastructure import privileged_tasks

    system, account, environment, _, snapshot = system_fixture(tmp_path)
    confirmed = system.plan(account, environment, snapshot=snapshot).consent
    options = LoginOptions(
        schema_version=1, hyprland=True, graphical_login=True, plymouth=False, confirmed=confirmed
    )
    command = LoginCommand(settings=options)
    received = []
    monkeypatch.setattr(bootstrap, "caller", lambda: SimpleNamespace(pw_name=account.name))
    monkeypatch.setattr(bootstrap, "operation_lock", nullcontext)
    monkeypatch.setattr(bootstrap, "acquire", lambda *args: tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(validation_runtime, "validation_runtime", lambda source: nullcontext())
    monkeypatch.setattr(privileged_tasks, "execute", lambda r, u, cmd: received.append(cmd) or 0)

    def handoff(argv, **options):
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(options["input"].encode())))
        assert bootstrap.main() == 0
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("subprocess.run", handoff)
    PrivilegedExecutor("v1.2.3", "a" * 40).run(command)
    assert received == [command]
