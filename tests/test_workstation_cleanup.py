"""Removal must stop on any unexpected dependency or protected package."""

import pytest

from setup_system.domain.cleanup import validate_removals
from setup_system.infrastructure import cleanup


def test_optional_closure_cannot_expand_even_to_another_candidate() -> None:
    with pytest.raises(ValueError, match="closure"):
        validate_removals(
            frozenset({"reportbug"}),
            frozenset({"reportbug", "nano"}),
            frozenset(),
            editor_verified=True,
        )


@pytest.mark.parametrize(
    "package", ["apt", "sudo", "linux-image-amd64", "cryptsetup", "hyprland", "openssh-server"]
)
def test_critical_packages_are_never_cleanup_candidates(package: str) -> None:
    with pytest.raises(ValueError):
        validate_removals(
            frozenset({package}), frozenset({package}), frozenset(), editor_verified=True
        )


def test_fallback_editor_replacement_is_required() -> None:
    with pytest.raises(ValueError, match="Neovim"):
        validate_removals(
            frozenset({"nano"}), frozenset({"nano"}), frozenset(), editor_verified=False
        )
    validate_removals(frozenset({"nano"}), frozenset({"nano"}), frozenset(), editor_verified=True)


def test_real_essential_flag_overrides_catalog_candidate() -> None:
    with pytest.raises(ValueError, match="Essential"):
        validate_removals(
            frozenset({"reportbug"}),
            frozenset({"reportbug"}),
            frozenset({"reportbug"}),
            editor_verified=True,
        )


def test_preview_uses_dpkg_and_simulation_without_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def run(argv):
        calls.append(argv)
        if "dpkg-query" in argv[0]:
            return "reportbug\tinstalled\tno\tno\napt\tinstalled\tyes\tno\n"
        assert "--simulate" in argv
        return "Remv reportbug [13.0]\n"

    monkeypatch.setattr(cleanup, "_run", run)
    plan = cleanup.preview(("reportbug",))
    assert plan.removed == frozenset({"reportbug"})
    assert len(calls) == 2


def test_absent_candidates_do_not_resolve_another_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cleanup, "_run", lambda argv: calls.append(argv) or "")
    assert not cleanup.preview(("reportbug",)).requested
    assert len(calls) == 1
    assert cleanup.cleanup_catalog()["nano"]["requires_verified_editor"]


def test_apply_removes_exact_confirmed_set_without_another_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from types import SimpleNamespace

    from setup_system import installation as system
    from setup_system.infrastructure import privileged_bootstrap_helpers, trusted_bootstrap

    monkeypatch.setattr(
        trusted_bootstrap,
        "caller",
        lambda: SimpleNamespace(pw_name="fixture", pw_dir=str(tmp_path)),
    )
    monkeypatch.setattr(cleanup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        privileged_bootstrap_helpers,
        "editor_ready",
        lambda *args: pytest.fail("Document removal does not execute an editor"),
    )
    selected = frozenset({"reportbug"})
    monkeypatch.setattr(
        cleanup,
        "preview",
        lambda *args, **kwargs: cleanup.CleanupPlan(selected, selected, frozenset(), ""),
    )
    records = []
    calls = []
    monkeypatch.setattr(system, "_write_system", lambda path, data: records.append(data))
    monkeypatch.setattr(system, "run_command", lambda argv, **kwargs: calls.append(argv))
    cleanup.apply_cleanup({"mode": "remove", "packages": ["reportbug"], "closure": ["reportbug"]})
    assert calls == [["/usr/bin/dpkg", "--remove", "--", "reportbug"]]
    assert b'"status": "complete"' in records[-1]
    with pytest.raises(ValueError, match="closure changed"):
        cleanup.apply_cleanup({"mode": "remove", "packages": ["reportbug"], "closure": []})
    assert len(calls) == 1
