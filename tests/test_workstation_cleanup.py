"""Removal must stop on any unexpected dependency or protected package."""

import pytest

from setup_system.domain.cleanup import CleanupCandidate, validate_removals
from setup_system.domain.system_commands import CleanupOptions
from setup_system.infrastructure import cleanup
from setup_system.infrastructure.catalog_repository import load_cleanup


def test_optional_closure_cannot_expand_even_to_another_candidate() -> None:
    with pytest.raises(ValueError, match="closure"):
        validate_removals(
            frozenset({"reportbug"}),
            frozenset({"reportbug", "nano"}),
            frozenset(),
            catalog=load_cleanup(),
            editor_verified=True,
        )


@pytest.mark.parametrize(
    "package", ["apt", "sudo", "linux-image-amd64", "cryptsetup", "hyprland", "openssh-server"]
)
def test_critical_packages_are_never_cleanup_candidates(package: str) -> None:
    with pytest.raises(ValueError):
        validate_removals(
            frozenset({package}),
            frozenset({package}),
            frozenset(),
            catalog=load_cleanup(),
            editor_verified=True,
        )


def test_fallback_editor_replacement_is_required() -> None:
    with pytest.raises(ValueError, match="Neovim"):
        validate_removals(
            frozenset({"nano"}),
            frozenset({"nano"}),
            frozenset(),
            catalog=load_cleanup(),
            editor_verified=False,
        )
    validate_removals(
        frozenset({"nano"}),
        frozenset({"nano"}),
        frozenset(),
        catalog=load_cleanup(),
        editor_verified=True,
    )


def test_real_essential_flag_overrides_catalog_candidate() -> None:
    with pytest.raises(ValueError, match="Essential"):
        validate_removals(
            frozenset({"reportbug"}),
            frozenset({"reportbug"}),
            frozenset({"reportbug"}),
            catalog=load_cleanup(),
            editor_verified=True,
        )


def test_preview_uses_dpkg_and_simulation_without_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def run(argv):
        calls.append(argv)
        if "dpkg-query" in argv[0]:
            return "reportbug\tinstalled\tinstall\tno\tno\napt\tinstalled\tinstall\tyes\tno\n"
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
    assert load_cleanup()["nano"].requires_verified_editor


def test_apply_removes_exact_confirmed_set_without_another_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from types import SimpleNamespace

    from setup_system.bootstrap import source as trusted_bootstrap
    from setup_system.infrastructure import admin_editor, processes, system_files

    monkeypatch.setattr(
        trusted_bootstrap,
        "caller",
        lambda: SimpleNamespace(pw_name="fixture", pw_dir=str(tmp_path)),
    )
    monkeypatch.setattr(cleanup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        admin_editor,
        "editor_ready",
        lambda *args: pytest.fail("Document removal does not execute an editor"),
    )
    selected = frozenset({"reportbug"})
    results = iter((selected, frozenset(), selected))
    monkeypatch.setattr(
        cleanup,
        "preview",
        lambda *args, **kwargs: cleanup.CleanupPlan(
            requested=next(results), removed=selected, essential=frozenset(), simulation=""
        ),
    )
    records = []
    calls = []
    monkeypatch.setattr(system_files, "write_system", lambda path, data: records.append(data))
    monkeypatch.setattr(processes, "run_command", lambda argv, **kwargs: calls.append(argv))
    cleanup.apply_cleanup(
        CleanupOptions.model_validate(
            {"mode": "remove", "packages": ["reportbug"], "closure": ["reportbug"]}
        )
    )
    assert calls == [["/usr/bin/dpkg", "--remove", "--", "reportbug"]]
    assert b'"status": "complete"' in records[-1]
    with pytest.raises(ValueError, match="closure changed"):
        cleanup.apply_cleanup(
            CleanupOptions.model_validate(
                {"mode": "remove", "packages": ["reportbug"], "closure": []}
            )
        )
    assert len(calls) == 1


def test_apt_hold_blocks_catalog_candidate(monkeypatch):
    def run(args):
        if "dpkg-query" in args[0]:
            return "reportbug\tinstalled\thold\tno\tno\n"
        return "Remv reportbug [1.0]\n"

    monkeypatch.setattr(cleanup, "_run", run)
    with pytest.raises(ValueError, match="Protected"):
        cleanup.preview(("reportbug",))


def test_catalog_controls_editor_requirement():
    selected = frozenset({"reportbug"})
    with pytest.raises(ValueError, match="Neovim"):
        validate_removals(
            selected,
            selected,
            frozenset(),
            catalog={"reportbug": CleanupCandidate(requires_verified_editor=True)},
            editor_verified=False,
        )


@pytest.mark.parametrize(
    "mode,packages",
    [("remove", ["--", "reportbug"]), ("purge", ["--", "reportbug"]), ("autoremove", [])],
)
def test_each_cleanup_mode_has_its_own_simulation(monkeypatch, mode, packages):
    def run(args):
        if "dpkg-query" in args[0]:
            return "reportbug\tinstalled\tinstall\tno\tno\n"
        assert args == ["/usr/bin/apt-get", "--simulate", mode, *packages]
        return ("Purg" if mode == "purge" else "Remv") + " reportbug [1.0]\n"

    monkeypatch.setattr(cleanup, "_run", run)
    assert cleanup.preview(("reportbug",), mode=mode).removed == {"reportbug"}
