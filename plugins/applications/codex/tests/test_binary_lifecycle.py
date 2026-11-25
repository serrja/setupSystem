"""Exercise the release archive and single-file uninstall in a synthetic HOME."""

import asyncio
import hashlib
import io
import tarfile

import pytest
from setup_plugin_codex import Plugin, Settings, release

from setup_system_sdk import Inputs, Mutation, Outcome, ownership_context


def tar(member, content):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        entry = tarfile.TarInfo(member)
        entry.size = len(content)
        archive.addfile(entry, io.BytesIO(content))
    return data.getvalue()


def test_install_update_uninstall_and_local_edits(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    member = "codex-x86_64-unknown-linux-musl"
    plugin = Plugin()
    settings = Settings()
    inputs = Inputs()
    contents = b"synthetic binary"

    def mock_release():
        raw = tar(member, contents)
        artifact = release.Artifact(
            version="1.0.0",
            member=member,
            url="https://example.invalid/fixture",
            sha256=hashlib.sha256(raw).hexdigest(),
            binary_sha256=hashlib.sha256(contents).hexdigest(),
        )
        monkeypatch.setattr(release, "resolve", lambda: artifact)
        monkeypatch.setattr(release, "read_https", lambda *args: raw)

    def apply(action, rows=()):
        with ownership_context(rows):
            plan = plugin.prepare(action, settings, inputs)
            assert plan.outcome == Outcome.READY
            result = asyncio.run(
                plugin.apply_request(
                    action,
                    Mutation(action_id=action, settings={}, inputs={}, plan=plan, holdings=rows),
                )
            )
            assert (
                result.outcome == Outcome.CHANGED
                and asyncio.run(plugin.verify(plan, settings, inputs)).outcome == Outcome.COMPLETE
            )
        return plan.resources[0]

    mock_release()
    created = apply("install")
    assert release.binary().read_bytes() == contents
    contents = b"synthetic update"
    mock_release()
    updated = apply("update", (created,))
    held = created.model_copy(update={"after": updated.after})
    release.binary().write_bytes(b"owner edit")
    with ownership_context((held,)):
        assert plugin.prepare("uninstall", settings, inputs).outcome == Outcome.BLOCKED
    release.binary().write_bytes(contents)
    apply("uninstall", (held,))
    assert not release.binary().exists()


def test_malicious_member_and_changed_archive_never_execute(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    raw = tar("../outside", b"bad")
    sha = hashlib.sha256(raw).hexdigest()
    artifact = release.Artifact(
        version="1.0",
        member="codex",
        url="https://example.invalid/file",
        sha256=sha,
        binary_sha256=sha,
    )
    monkeypatch.setattr(release, "resolve", lambda: artifact)
    monkeypatch.setattr(release, "read_https", lambda *a: raw)
    with pytest.raises(ValueError, match="archive member"):
        release.install("install", release.plan("install", artifact))
    assert not release.binary().exists()
