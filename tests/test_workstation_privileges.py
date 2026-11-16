"""Hyprland user configuration must fail closed before root executes a stage."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from setup_system.application import workstation
from setup_system.cli.workstation import workstation_app

ROOT = Path(__file__).resolve().parents[1]


def test_service_rejects_root_before_planning_or_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(workstation.os, "geteuid", lambda: 0)
    monkeypatch.setenv("HOME", str(tmp_path))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Root Hyprland apply entered a planning or execution boundary")

    for name in ("plan", "_run", "_font_files", "new_backup", "atomic_write"):
        monkeypatch.setattr(workstation, name, forbidden)
    with pytest.raises(ValueError, match=r"(?i)root"):
        workstation.apply(ROOT, "hyprland")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("consent", [[], ["--yes"]], ids=["no-consent", "consented"])
def test_cli_rejects_root_before_user_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, consent: list[str]
) -> None:
    monkeypatch.setattr(workstation.os, "geteuid", lambda: 0)
    monkeypatch.setenv("HOME", str(tmp_path))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Root CLI dispatched user configuration")

    monkeypatch.setattr(workstation, "apply", forbidden)
    monkeypatch.setattr(workstation, "plan", forbidden)
    result = CliRunner().invoke(workstation_app, ["apply", "--profile", "hyprland", *consent])
    assert result.exit_code == 2, result.output
    assert "user configuration cannot run as root" in result.output.lower(), result.output
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(("profile", "uid"), [("hyprland", 1000), ("fonts", 1000), ("fonts", 0)])
def test_cli_dispatches_normal_user_and_preserves_fonts_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, profile: str, uid: int
) -> None:
    monkeypatch.setattr(workstation.os, "geteuid", lambda: uid)
    calls: list[tuple[Path, str]] = []

    def apply(root: Path, selected: str, **options: object) -> Path:
        calls.append((root, selected))
        return tmp_path / "backup-id"

    monkeypatch.setattr(workstation, "apply", apply)
    result = CliRunner().invoke(workstation_app, ["apply", "--profile", profile, "--yes"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0][1] == profile
    assert "backup-id" in result.output
    assert not list(tmp_path.iterdir())


def test_service_keeps_fonts_planning_available_to_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workstation.os, "geteuid", lambda: 0)

    def plan(*args: object, **kwargs: object) -> None:
        raise ValueError("Fixture read-only planning boundary")

    monkeypatch.setattr(workstation, "plan", plan)
    with pytest.raises(ValueError, match="Fixture read-only planning boundary"):
        workstation.apply(ROOT, "fonts")


@pytest.mark.parametrize("command", ["plan", "doctor"])
def test_root_cli_can_still_inspect_read_only_hyprland_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    monkeypatch.setattr(workstation.os, "geteuid", lambda: 0)
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list[str] = []

    def plan(
        root: Path, profile: workstation.Profile, **options: object
    ) -> workstation.WorkstationPlan:
        calls.append(profile)
        return workstation.WorkstationPlan(
            profile=profile,
            os="debian",
            architecture="x86_64",
            installed_hyprland=None,
            files=(),
        )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Read-only root command dispatched apply")

    monkeypatch.setattr(workstation, "plan", plan)
    monkeypatch.setattr(workstation, "apply", forbidden)
    result = CliRunner().invoke(workstation_app, [command, "--profile", "hyprland"])
    assert result.exit_code == 0, result.output
    assert calls == ["hyprland"]
    assert '"profile": "hyprland"' in result.output
    assert not list(tmp_path.iterdir())
