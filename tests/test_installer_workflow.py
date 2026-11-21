"""Direct delivery and optional application selection replace the temporary proxy flow."""

from pathlib import Path

import pytest

from setup_system.composition import build_service
from setup_system.domain.operations import InstallRequest, Operation
from setup_system.domain.settings import Section, SystemSettings, UserSettings

ROOT = Path(__file__).resolve().parents[1]


def test_happ_is_not_required_for_base_or_theme_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "platform.freedesktop_os_release",
        lambda: {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"},
    )
    service = build_service(ROOT)
    assert not any(
        action.id == "happ"
        for action in service.plan(
            InstallRequest(operation=Operation.PLAN, system=SystemSettings(hyprland=True))
        ).actions
    )
    plan = service.plan(InstallRequest(operation=Operation.PLAN, section=Section.WORKSPACE))
    assert not plan.actions
    assert not plan.blockers


def test_happ_is_selected_only_by_explicit_workspace_intention() -> None:
    service = build_service(ROOT)
    plan = service.plan(
        InstallRequest(
            operation=Operation.PLAN, section=Section.WORKSPACE, settings=UserSettings(happ=True)
        )
    )
    assert "workspace_packages" in [action.id for action in plan.actions]
    assert not any("subscription" in action.description.lower() for action in plan.actions)


def test_cancelled_confirmation_never_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_workstation_service import Interaction

    monkeypatch.setattr("platform.machine", lambda: "amd64")
    monkeypatch.setattr(
        "platform.freedesktop_os_release",
        lambda: {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"},
    )
    interaction = Interaction()
    interaction.confirmed = False
    service = build_service(ROOT, interaction=interaction)
    monkeypatch.setattr(service, "_apply", lambda *args: pytest.fail("Cancelled plan was applied"))
    assert (
        service.run(
            InstallRequest(
                operation=Operation.APPLY,
                section=Section.WORKSPACE,
                settings=UserSettings(apps=("codex",)),
            )
        )
        == 2
    )
