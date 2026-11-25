"""The last directory and its durable receipt survive interruptions in either order."""

import asyncio
import os
from pathlib import Path

import pytest
from test_extensions import activate, fixture_release
from test_plugin_lifecycle import activation

from setup_system import extension_lifecycle, extensions
from setup_system.infrastructure import retirement_checkpoint as checkpoint
from setup_system_sdk import Inputs, Outcome, Resource, ownership_context


class PowerLoss(BaseException):
    """Stop execution without entering recoverable I/O error handlers."""


def pending_extension(tmp_path, monkeypatch, *, after_directory):
    state = activation(tmp_path)
    env = tmp_path / "envs"
    root = env / "demo"
    gen = root / ("a" * 32)
    gen.mkdir(parents=True, mode=0o700)
    (gen / "example").write_bytes(b"program")
    extension_lifecycle.seal_generation(gen)
    pointer = extensions._Pointer(generation=gen.name, digest=state.digest, plugin_id="demo")
    (root / "active.json").write_text(pointer.model_dump_json())
    monkeypatch.setattr(extension_lifecycle, "_read_generation", lambda *_: state)
    entries = extension_lifecycle.removable(env, "demo")
    native = os.unlink if after_directory else os.rmdir

    def crash(path, *args, **kwargs):
        if path == ("demo.final" if after_directory else "demo.directory"):
            raise PowerLoss
        return native(path, *args, **kwargs)

    with monkeypatch.context() as faults:
        faults.setattr(os, "unlink" if after_directory else "rmdir", crash)
        with pytest.raises(PowerLoss):
            extension_lifecycle.retire(env, "demo", entries)
    return root, entries


@pytest.mark.parametrize("after_directory", [False, True])
def test_external_card_remains_resumable_until_final_receipt_removal(
    tmp_path, monkeypatch, after_directory
):
    root, entries = pending_extension(tmp_path, monkeypatch, after_directory=after_directory)
    assert not os.path.lexists(root) and checkpoint.pending(root)
    assert extensions.active(root.parent, "demo") is None
    [card] = extensions.installed(root.parent)
    assert card.manifest.id == "demo" and card.disabled and not card.reason
    assert extension_lifecycle.removable(root.parent, "demo") == entries
    release, _ = fixture_release(tmp_path, "demo", "1")
    with pytest.raises(ValueError, match="interrupted retirement"):
        activate(release, tmp_path)
    assert not os.path.lexists(root)
    extension_lifecycle.retire(root.parent, "demo", entries)
    assert not checkpoint.pending(root) and not extensions.installed(root.parent)
    assert activate(release, tmp_path).manifest.id == "demo"


@pytest.mark.parametrize("intruder", ["file", "symlink", "directory"])
def test_distinct_name_recreated_during_retirement_is_preserved(tmp_path, monkeypatch, intruder):
    root, entries = pending_extension(tmp_path, monkeypatch, after_directory=True)
    personal = tmp_path / "independent"
    personal.mkdir()
    (personal / "notes").write_bytes(b"preserve")
    if intruder == "symlink":
        root.symlink_to(personal, target_is_directory=True)
    elif intruder == "directory":
        root.mkdir()
        (root / "new").write_text("preserve too")
    else:
        root.write_text("user-created file")
    with pytest.raises((ValueError, OSError)):
        extension_lifecycle.removable(root.parent, root.name)
    with pytest.raises((ValueError, OSError)):
        extension_lifecycle.retire(root.parent, "demo", entries)
    assert checkpoint.pending(root) and os.path.lexists(root)
    assert (personal / "notes").read_bytes() == b"preserve"
    if intruder == "file":
        assert root.read_text() == "user-created file"
    if intruder == "directory":
        assert (root / "new").read_text() == "preserve too"
    root.rename(tmp_path / "preserved")
    extension_lifecycle.retire(root.parent, "demo", entries)


def test_toolchain_rechecks_parent_receipt_after_final_directory_disappears(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "plugins/development/python/src")
    )
    from setup_plugin_python import Plugin, Settings

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    plugin = Plugin()
    folder = plugin.storage() / ("1" * 64)
    folder.mkdir(parents=True, mode=0o700)
    (folder / "tool").write_bytes(b"fixture")
    extension_lifecycle.seal_generation(folder)
    row = Resource(
        kind="toolchain_generation",
        identity=str(folder),
        after=folder.name,
        ownership="created",
        cleanup=True,
    )
    native = os.unlink

    def crash(name, *args, **kwargs):
        if name == folder.name + ".final":
            raise PowerLoss
        return native(name, *args, **kwargs)

    with ownership_context((row,)):
        plan = plugin.remove_plan()
        with monkeypatch.context() as faults:
            faults.setattr(os, "unlink", crash)
            with pytest.raises(PowerLoss):
                plugin.remove(plan)
        assert not folder.exists() and checkpoint.pending(folder)
        assert asyncio.run(plugin.verify(plan, Settings(), Inputs())).outcome is Outcome.PARTIAL
        assert plugin.remove(plugin.remove_plan()).outcome is Outcome.CHANGED
        assert asyncio.run(plugin.verify(plan, Settings(), Inputs())).outcome is Outcome.COMPLETE


def test_parent_receipt_symlink_is_preserved(tmp_path, monkeypatch):
    root, entries = pending_extension(tmp_path, monkeypatch, after_directory=True)
    directory = root.parent / checkpoint.NAMESPACE
    marker = directory / "demo.final"
    original = tmp_path / "saved-receipt"
    marker.rename(original)
    private = tmp_path / "user"
    private.write_text("untouched")
    marker.symlink_to(private)
    with pytest.raises(OSError):
        extension_lifecycle.retire(root.parent, root.name, entries)
    assert marker.is_symlink() and private.read_text() == "untouched"
    marker.unlink()
    original.rename(marker)
    extension_lifecycle.retire(root.parent, root.name, entries)
