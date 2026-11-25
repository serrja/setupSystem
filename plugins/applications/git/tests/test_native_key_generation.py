"""Exercise native OpenSSH with a disposable askpass program and a synthetic HOME."""

import asyncio
from pathlib import Path

import pytest
from setup_plugin_git import Plugin, Settings, local_keys

from setup_system_sdk import Mutation, Outcome, ownership_context


def setup_prompt(monkeypatch, tmp_path, *, empty=False):
    monkeypatch.setenv("HOME", str(tmp_path))
    for scope in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{scope}_HOME", str(tmp_path / scope.lower()))
    helper = tmp_path / "fixture-askpass"
    # Both prompts use the same public test input. No live secret or SSH agent is involved.
    helper.write_text("#!/bin/sh\n" + ("exit 0\n" if empty else "printf '%s' fixture-only\n"))
    helper.chmod(0o700)
    monkeypatch.setattr(local_keys, "ASKPASS", helper)
    monkeypatch.setattr(local_keys, "native_dialog", lambda: True)


def test_real_encrypted_keypair_and_cleanup(monkeypatch, tmp_path):
    setup_prompt(monkeypatch, tmp_path)
    plugin = Plugin()
    request = local_keys.Generate(name="work")
    settings = Settings()
    plan = plugin.prepare("generate-key", settings, request)
    assert plan.outcome == Outcome.READY and {row.kind for row in plan.resources} == {
        "ssh-private-key",
        "ssh-public-key",
    }
    changed = asyncio.run(
        plugin.apply_request(
            "generate-key",
            Mutation(action_id="generate-key", settings={}, inputs=request.model_dump(), plan=plan),
        )
    )
    assert changed.outcome == Outcome.CHANGED
    result = asyncio.run(plugin.verify(plan, settings, request))
    assert result.outcome == Outcome.COMPLETE and result.resources == changed.resources
    assert (
        "fixture-only" not in result.model_dump_json()
        and "PRIVATE KEY" not in result.model_dump_json()
    )
    path = Path(plan.resources[0].identity).parent
    public = path / local_keys.PUBLIC
    original = public.read_bytes()
    with ownership_context(tuple(result.resources)):
        assert plugin.prepare("generate-key", settings, request).outcome == Outcome.NO_CHANGES
        cleanup = local_keys.RemoveLocal()
        reviewed = plugin.prepare("remove-local-keys", settings, cleanup)
        assert reviewed.outcome == Outcome.READY and all(
            r.effect == "remove" and r.before for r in reviewed.resources
        )
        public.write_bytes(original.replace(b" work", b" changed"))
        assert plugin.prepare("remove-local-keys", settings, cleanup).outcome == Outcome.BLOCKED
        public.write_bytes(original)
        neighbor = path.parent / "existing"
        neighbor.write_text("keep")
        assert (
            plugin.mutate("remove-local-keys", settings, cleanup, reviewed).outcome
            == Outcome.CHANGED
        )
        assert asyncio.run(plugin.verify(reviewed, settings, cleanup)).outcome == Outcome.COMPLETE
        assert neighbor.read_text() == "keep" and not path.exists()


def test_empty_passphrase_and_terminal_handoff(monkeypatch, tmp_path):
    setup_prompt(monkeypatch, tmp_path, empty=True)
    plugin = Plugin()
    request = local_keys.Generate(name="new")
    plan = plugin.prepare("generate-key", Settings(), request)
    outcome = plugin.mutate("generate-key", Settings(), request, plan)
    assert (
        outcome.outcome == Outcome.BLOCKED and not (local_keys.directory() / request.name).exists()
    )
    stage = local_keys.staging(local_keys.directory() / request.name)
    assert str(stage) in outcome.message and "unencrypted" in outcome.message
    assert stage.exists() and "Inspect" in local_keys.generate_plan(request).summary
    stage.rename(tmp_path / "preserved-by-owner")
    monkeypatch.setattr(local_keys, "native_dialog", lambda: False)
    manual = plugin.prepare("generate-key", Settings(), request)
    assert manual.outcome == Outcome.NEEDS_USER_ACTION and "ssh-keygen -t ed25519" in manual.summary
    occupied = local_keys.directory() / request.name
    occupied.mkdir()
    assert plugin.prepare("generate-key", Settings(), request).outcome == Outcome.BLOCKED


def test_change_after_atomic_publication_keeps_both_user_edits(monkeypatch, tmp_path):
    setup_prompt(monkeypatch, tmp_path)
    move = local_keys.move_new

    def race(source, destination):
        move(source, destination)
        public = destination / local_keys.PUBLIC
        public.write_bytes(public.read_bytes().replace(b" work", b" owner"))
        source.mkdir()
        (source / local_keys.PUBLIC).write_text("new owner file")
        swapped.append(source)

    swapped = []
    monkeypatch.setattr(local_keys, "move_new", race)
    request = local_keys.Generate(name="work")
    result = local_keys.generate(request, local_keys.generate_plan(request))
    assert result.outcome == Outcome.PARTIAL
    assert (swapped[0] / local_keys.PUBLIC).read_text() == "new owner file"
    assert (local_keys.directory() / "work" / local_keys.PUBLIC).read_bytes().endswith(b" owner\n")


@pytest.mark.parametrize("cut", ["private-claim", "parent-receipt"])
def test_lost_channel_retains_exact_key_claims_until_resume(tmp_path, monkeypatch, cut):
    import hashlib
    import os

    from setup_system.infrastructure import recorded_tree, retirement_checkpoint

    class PowerLoss(BaseException):
        pass

    setup_prompt(monkeypatch, tmp_path)
    plugin = Plugin()
    settings = Settings()
    request = local_keys.Generate(name="selected")
    creation = local_keys.generate_plan(request)
    made = local_keys.generate(request, creation)
    assert made.outcome == Outcome.CHANGED
    target = local_keys.directory() / request.name
    cleanup = local_keys.RemoveLocal()
    with ownership_context(tuple(made.resources)):
        plan = plugin.prepare("remove-local-keys", settings, cleanup)
        with monkeypatch.context() as faults:
            if cut == "private-claim":
                original = recorded_tree._discard

                def interrupted(claims, name, record):
                    if name == hashlib.sha256(os.fsencode(local_keys.KEY)).hexdigest():
                        raise PowerLoss
                    original(claims, name, record)

                faults.setattr(recorded_tree, "_discard", interrupted)
            else:
                unlink = os.unlink

                def interrupted_unlink(name, *a, **kw):
                    if name == "selected.final":
                        raise PowerLoss
                    unlink(name, *a, **kw)

                faults.setattr(os, "unlink", interrupted_unlink)
            with pytest.raises(PowerLoss):
                plugin.mutate("remove-local-keys", settings, cleanup, plan)
        assert not (target / local_keys.KEY).exists()
        assert asyncio.run(plugin.verify(plan, settings, cleanup)).outcome == Outcome.PARTIAL
        assert local_keys.generate_plan(request).outcome == Outcome.BLOCKED
        if cut == "private-claim":
            # A later file with the original basename cannot become the selected private key.
            unknown = target / "personal"
            unknown.mkdir()
            (unknown / "notes").write_text("keep")
            assert plugin.prepare("remove-local-keys", settings, cleanup).outcome == Outcome.BLOCKED
            unknown.rename(tmp_path / "personal")
            replacement = target / local_keys.KEY
            replacement.write_bytes(b"owner-created file")
            replacement.chmod(0o600)
            assert plugin.prepare("remove-local-keys", settings, cleanup).outcome == Outcome.BLOCKED
            assert replacement.read_bytes() == b"owner-created file"
            replacement.rename(tmp_path / "owner-file")
        else:
            assert not target.exists() and retirement_checkpoint.pending(target)
        assert plugin.prepare("remove-local-keys", settings, cleanup) == plan
        assert (
            plugin.mutate("remove-local-keys", settings, cleanup, plan).outcome == Outcome.CHANGED
        )
        assert asyncio.run(plugin.verify(plan, settings, cleanup)).outcome == Outcome.COMPLETE
        assert not target.exists() and not retirement_checkpoint.pending(target)


def test_stopped_before_key_publication_exposes_reserved_stage_without_adopting_its_files(
    monkeypatch, tmp_path
):
    class PowerLoss(BaseException):
        pass

    setup_prompt(monkeypatch, tmp_path)
    request = local_keys.Generate(name="stopped")
    target = local_keys.directory() / request.name
    plan = local_keys.generate_plan(request)
    stage = local_keys.staging(target)
    assert stage.name == ".generate-stopped" and any(
        str(stage) in warning for warning in plan.warnings
    )

    def stop_before_move(source, destination):
        assert source == stage and destination == target
        raise PowerLoss

    with monkeypatch.context() as faults:
        faults.setattr(local_keys, "move_new", stop_before_move)
        with pytest.raises(PowerLoss):
            local_keys.generate(request, plan)
    assert not target.exists() and (stage / local_keys.KEY).stat().st_mode & 0o777 == 0o600
    (stage / local_keys.PUBLIC).write_bytes(b"owner-reviewed substitute")
    assert local_keys.generate_plan(request).outcome == Outcome.NEEDS_USER_ACTION
    result = local_keys.verify_generation(request, plan)
    assert result.outcome == Outcome.NEEDS_USER_ACTION and str(stage) in result.message
    assert not result.resources and not local_keys.removal_plan().resources
    assert local_keys.generate(request, plan).outcome == Outcome.BLOCKED
    assert (stage / local_keys.PUBLIC).read_bytes() == b"owner-reviewed substitute"
