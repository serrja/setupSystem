"""Exercise package, platform and managed-prefix preconditions without changing the host."""

import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import pytest

from setup_system.infrastructure import firefox_policy
from setup_system.infrastructure.native_target import target

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("key", "value"), [("ID", "ubuntu"), ("VERSION_ID", "14"), ("VERSION_CODENAME", "bookworm")]
)
def test_debian_identity_is_checked_as_a_whole(key, value):
    release = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}
    assert target(release, "x86_64", "amd64") == ("amd64", True)
    assert target(release, "aarch64", "arm64") == ("arm64", True)
    assert not target(release, "riscv64", "riscv64")[1]
    assert not target(dict(release, **{key: value}), "x86_64", "amd64")[1]


def test_firefox_matching_policy_has_no_second_action(tmp_path, monkeypatch, capsys):
    scope = runpy.run_path(str(ROOT / "ansible/files/system_contract.py"))
    policy = tmp_path / "policies.json"
    run = scope["run"]
    run.__globals__["policy_stamp"] = lambda: scope["policy_stamp"](policy)

    def plan():
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps({"section": "workspace", "settings": {"apps": ["firefox"]}})),
        )
        run("plan")
        return json.loads(capsys.readouterr().out)

    assert plan()["actions"]
    policy.write_bytes(firefox_policy.desired_bytes())
    policy.chmod(0o644)
    assert plan()["actions"] == []
    policy.chmod(0o600)
    assert plan()["actions"]


def test_user_manager_rejects_repointed_prefix_before_read(tmp_path, monkeypatch):
    home = tmp_path / "user"
    data = home / ".local/share"
    root = data / "setup-system/tools"
    root.mkdir(parents=True)
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (root / "npm").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    workspace = runpy.run_path(str(ROOT / "ansible/filter_plugins/workspace.py"))
    with pytest.raises(ValueError, match="unsafe"):
        workspace["manager_paths"](str(data), ["codex"])
    with pytest.raises(ValueError, match="unsafe"):
        workspace["metadata_version"](str(data), "npm", "@openai/codex")
    assert not list(outside.iterdir())


@pytest.fixture
def signing_home():
    # Agent socket names must fit sockaddr_un even when pytest's test path is long.
    with tempfile.TemporaryDirectory(prefix="sign-") as directory:
        try:
            yield Path(directory)
        finally:
            subprocess.run(["gpgconf", "--homedir", directory, "--kill", "gpg-agent"], check=True)


def test_read_only_apt_uses_private_signed_indexes(tmp_path, signing_home):
    # Use a signed flat archive to exercise real APT without a network or host hooks.
    stage = tmp_path / "apt"
    repo = tmp_path / "repository"
    keyhome = signing_home
    for d in (stage / "lists/partial", stage / "archives/partial", repo):
        d.mkdir(parents=True, mode=0o700)
    now = datetime.now(UTC)
    raw = (
        b"Package: sample-tool\nVersion: 1.0\nArchitecture: all\n"
        b"Maintainer: Test <a@invalid.tld>\nDescription: Fixture\n"
        b"Filename: ./sample.deb\nSize: 1\nSHA256: "
        + hashlib.sha256(b"x").hexdigest().encode()
        + b"\n\n"
    )
    (repo / "Packages").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (repo / "Release").write_text(
        f"Origin: Test\nLabel: Test\nDate: {format_datetime(now)}\n"
        f"Valid-Until: {format_datetime(now + timedelta(days=1))}\n"
        f"SHA256:\n {digest} {len(raw)} Packages\n"
    )
    gpg = [
        "/usr/bin/gpg",
        "--homedir",
        str(keyhome),
        "--batch",
        "--no-tty",
        "--pinentry-mode",
        "loopback",
        "--passphrase",
        "",
    ]
    subprocess.run(
        [
            *gpg,
            "--quick-generate-key",
            "Native Fixture <fixture@invalid.tld>",
            "ed25519",
            "sign",
            "1d",
        ],
        check=True,
        capture_output=True,
    )
    key = tmp_path / "trusted.gpg"
    key.write_bytes(subprocess.check_output([*gpg, "--export"]))
    subprocess.run(
        [*gpg, "--output", str(repo / "InRelease"), "--clearsign", str(repo / "Release")],
        check=True,
    )
    sources = tmp_path / "fixture.sources"
    sources.write_text(f"Types: deb\nURIs: {repo.as_uri()}\nSuites: ./\nSigned-By: {key}\n")
    (stage / "status").touch()
    args = [
        "/usr/bin/apt-get",
        "-o",
        "Dir::Etc::sourcelist=" + str(sources),
        "-o",
        "Dir::State::lists=" + str(stage / "lists"),
        "-o",
        "Dir::Cache::archives=" + str(stage / "archives"),
        "-o",
        "Dir::State::status=" + str(stage / "status"),
        "-o",
        "Dir::State::extended_states=" + str(stage / "extended"),
    ]
    env = dict(
        os.environ, APT_CONFIG=str(ROOT / "config/base/apt.conf"), LC_ALL="C", HOME=str(tmp_path)
    )
    result = subprocess.run(
        [*args, "update", "--error-on=any"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(p.name.endswith("InRelease") for p in (stage / "lists").iterdir())
    plan = subprocess.check_output(
        [*args, "--simulate", "--no-remove", "install", "--", "sample-tool"], env=env, text=True
    )
    assert "Inst sample-tool (1.0" in plan
    (repo / "InRelease").write_text(
        (repo / "InRelease").read_text().replace("Origin: Test", "Origin: Changed")
    )
    result = subprocess.run([*args, "update", "--error-on=any"], env=env, capture_output=True)
    assert result.returncode != 0
    assert (stage / "status").read_bytes() == b""


def test_apt_hook_rejects_changed_actual_dpkg_transaction(tmp_path, monkeypatch):
    scope = runpy.run_path(str(ROOT / "ansible/files/apt_apply.py"))
    (tmp_path / "approved.json").write_text(
        json.dumps(["Inst example [1] (2 stable [amd64])", "Conf example (2 stable [amd64])"])
    )
    monkeypatch.setitem(
        scope["verify"].__globals__,
        "run_command",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "amd64", ""),
    )
    prefix = "VERSION 3\nAPT::Architecture=amd64\n\n"
    accepted = (
        "example 1 amd64 none < 2 amd64 none /var/cache/example.deb\n"
        "example 2 amd64 none = 2 amd64 none **CONFIGURE**\n"
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(prefix + accepted))
    scope["verify"](tmp_path)
    for changed in (
        accepted.replace(" < 2 ", " < 3 "),
        accepted.replace(" < 2 ", " > 2 "),
        accepted.replace("example 1 ", "example 1.8 "),
        accepted.replace("/var/cache/example.deb", "**REMOVE**"),
        accepted.replace("example ", "unrelated "),
        accepted.splitlines(keepends=True)[0],
    ):
        monkeypatch.setattr(sys, "stdin", io.StringIO(prefix + changed))
        with pytest.raises(ValueError, match=r"closure|transaction"):
            scope["verify"](tmp_path)


@pytest.mark.parametrize(
    ("kernel", "dpkg"), [("x86_64", "i386"), ("aarch64", "armhf"), ("x86_64", ""), ("x86_64", None)]
)
def test_incompatible_or_unavailable_userspace_abi_blocks_root(monkeypatch, kernel, dpkg):
    import subprocess

    from setup_system.infrastructure import native_target

    monkeypatch.setattr(native_target.platform, "machine", lambda: kernel)

    def check(argv, **kwargs):
        assert argv == ["/usr/bin/dpkg", "--print-architecture"]
        if dpkg is None:
            raise FileNotFoundError("no dpkg")
        return subprocess.CompletedProcess(argv, 0, dpkg, "")

    monkeypatch.setattr(native_target, "run_command", check)
    assert not native_target.current_target(
        {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}
    )[1]
