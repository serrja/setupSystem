"""Run a signed local APT preview and verify the exact pre-dpkg action stream."""

import hashlib
import io
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import pytest

from setup_system.layout import resource_root
from setup_system_sdk import Plan, Resource, apt

ROOT = resource_root()


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


def test_actual_dpkg_actions_and_archives_must_match(tmp_path, monkeypatch):
    folder = tmp_path / "archives"
    folder.mkdir()
    deb = folder / "example.deb"
    deb.write_bytes(b"fixture")
    digest = hashlib.sha256(deb.read_bytes()).hexdigest()
    row = Resource(
        kind="package",
        identity="example",
        ownership="modified",
        before="1",
        after="2",
        source_sha256=digest,
    )
    approval = apt.Approval(
        plugin_id="git", action_id="install", plan=Plan(summary="fixture", resources=[row])
    )
    monkeypatch.setattr(apt, "protected", lambda *a, **kw: None)
    monkeypatch.setattr(apt, "current_target", lambda: ("amd64", True))
    head = "VERSION 3\nAPT::Architecture=amd64\n\n"
    valid = (
        f"example 1 amd64 none < 2 amd64 none {deb}\n"
        "example 2 amd64 none = 2 amd64 none **CONFIGURE**\n"
    )

    def check(contents):
        monkeypatch.setattr(sys, "stdin", io.StringIO(head + contents))
        apt.verify_hook(tmp_path, approval)

    check(valid)
    for changed in (
        valid.replace(" < 2 ", " < 3 "),
        valid.replace(" < 2 ", " > 2 "),
        valid.replace("example 1 ", "example 1.8 "),
        valid.replace(str(deb), "**REMOVE**"),
        valid.replace("example ", "other "),
        valid.partition("\n")[0],
        valid.replace("2 amd64 none =", "2 arm64 none ="),
    ):
        with pytest.raises(ValueError):
            check(changed)
    deb.write_bytes(b"changed")
    with pytest.raises(ValueError, match="signed index"):
        check(valid)
    with pytest.raises(ValueError, match="staging path"):
        apt.check(tmp_path)


def test_installed_version_is_stable_across_repeated_plans_updates_and_uninstall(
    tmp_path, monkeypatch
):
    import json

    from setup_system_sdk import Outcome, ownership_context

    folder = tmp_path / "plugin"
    folder.mkdir()
    (folder / "apt.json").write_text(json.dumps({"names": ["example"]}))
    data = {"installed": None, "latest": "1"}
    monkeypatch.setattr(apt, "keyring", lambda _: None)
    monkeypatch.setattr(apt, "current_target", lambda: ("amd64", True))
    monkeypatch.setattr(apt, "installed", lambda n: data["installed"])

    def query(args, *a, **kwargs):
        if args[0].endswith("dpkg-query"):
            return "install|||amd64"
        if args[0].endswith("apt-cache"):
            return "SHA256: " + data["latest"] * 64
        if "remove" in args:
            return f"Remv example [{data['installed']}]"
        if "--simulate" in args and data["installed"] != data["latest"]:
            before = f" [{data['installed']}]" if data["installed"] else ""
            version = data["latest"]
            return (
                f"Inst example{before} ({version} stable [amd64])\n"
                f"Conf example ({version} stable [amd64])"
            )
        return ""

    monkeypatch.setattr(apt, "query", query)

    def resolve(step):
        stage = tmp_path / step
        stage.mkdir()
        return apt.resolve(tmp_path, stage, folder, apt.Packages(names=("example",)))

    first = resolve("install")
    created = first.resources[0]
    assert created.before is None and created.after == "1" and created.source_sha256 == "1" * 64
    data["installed"] = "1"
    assert apt.verify_packages(first).outcome == Outcome.COMPLETE
    repeat = resolve("repeat")
    assert (
        repeat.outcome == Outcome.NO_CHANGES
        and repeat.resources[0].before == created.after == repeat.resources[0].after
    )
    assert repeat.resources[0].ownership == "preexisting"
    with ownership_context((repeat.resources[0],)):
        assert apt.uninstall_plan(folder).outcome == Outcome.NO_CHANGES
    data["latest"] = "2"
    update = resolve("update")
    updated = update.resources[0]
    assert (
        updated.before == created.after
        and updated.after == "2"
        and updated.source_sha256 == "2" * 64
    )
    data["installed"] = "2"
    with ownership_context(
        (
            created.model_copy(
                update={"after": updated.after, "source_sha256": updated.source_sha256}
            ),
        )
    ):
        removal = apt.uninstall_plan(folder)
        assert removal.resources[0].before == "2" and removal.resources[0].effect == "remove"
        data["installed"] = None
        assert apt.verify_packages(removal).outcome == Outcome.COMPLETE
