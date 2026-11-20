"""One immutable plan, one mutation, truthful partial results and safe cancellation."""

from pathlib import Path

import pytest

from setup_system.application.installer import InstallationService, InstallRequest, Operation
from setup_system.domain.settings import Section, SystemSettings, UserSettings


class Interaction:
    cancelled = False
    interactive = True

    def __init__(self):
        self.lines = []
        self.confirmed = True

    def write(self, text, **kwargs):
        self.lines.append(text)

    def confirm(self, prompt):
        return self.confirmed


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "platform.freedesktop_os_release",
        lambda: {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"},
    )
    monkeypatch.setattr("platform.machine", lambda: "x86_64")

    class Executor:
        def run(self, action, settings):
            raise AssertionError("No real privileged operation in a fixture")

    return InstallationService(
        tmp_path, interaction=Interaction(), executor=Executor(), authorize=lambda: True
    )


def test_plan_never_calls_executor_or_creates_files(service, tmp_path: Path) -> None:
    service.run(
        InstallRequest(Operation.PLAN, section=Section.WORKSPACE, settings=UserSettings(happ=True))
    )
    assert list(tmp_path.iterdir()) == []


def test_unsupported_application_blocks_apply_but_not_navigation(service) -> None:
    request = InstallRequest(
        Operation.APPLY, section=Section.WORKSPACE, settings=UserSettings(chatgpt=True)
    )
    assert service.run(request) == 4
    assert service.run(InstallRequest(Operation.PLAN)) == 0


def test_cancel_is_observed_between_completed_stages(
    service, monkeypatch: pytest.MonkeyPatch
) -> None:
    def apply(action, plan):
        service.cancel.set()

    monkeypatch.setattr(service, "_apply", apply)
    request = InstallRequest(Operation.APPLY, system=SystemSettings(hyprland=True))
    # A real resource root is needed for the platform manifest, never a real apply.
    service.root = Path(__file__).resolve().parents[1]
    assert service.run(request) == 3
    assert service.completed == ["base"]
    assert any("CANCELLED" in line for line in service.interaction.lines)


def test_partial_failure_never_reports_success(service, monkeypatch: pytest.MonkeyPatch) -> None:
    service.root = Path(__file__).resolve().parents[1]

    def apply(action, plan):
        if action == "desktop_files":
            raise RuntimeError("Synthetic stage failure")

    monkeypatch.setattr(service, "_apply", apply)
    assert service.run(InstallRequest(Operation.APPLY, system=SystemSettings(hyprland=True))) == 5
    assert service.completed == ["base"]
    assert any("PARTIAL/FAILED" in line for line in service.interaction.lines)


def test_second_mutation_is_rejected(service) -> None:
    service._mutation.acquire()
    try:
        assert service.run(InstallRequest(Operation.APPLY)) == 2
    finally:
        service._mutation.release()


def test_preflight_failure_does_not_report_previous_partial_work(service, monkeypatch) -> None:
    service.completed = ["previous"]
    monkeypatch.setattr(
        service, "plan", lambda request: (_ for _ in ()).throw(ValueError("bad plan"))
    )
    assert service.run(InstallRequest(Operation.APPLY)) == 2
    assert "no operation started" in service.interaction.lines[-1]
    assert "previous" not in service.interaction.lines[-1]


def test_validation_diagnostics_never_echo_rejected_values() -> None:
    from pydantic import ValidationError

    from setup_system.diagnostics import error_text

    with pytest.raises(ValidationError) as caught:
        UserSettings.model_validate({"token": "synthetic-private-value"})
    assert "synthetic-private-value" not in error_text(caught.value)
