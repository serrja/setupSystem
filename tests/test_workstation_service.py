"""One immutable plan, one mutation, truthful partial results and safe cancellation."""

import os
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

    from setup_system.installation import TargetUser

    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{key}_HOME", str(tmp_path / key.lower()))
    target = TargetUser("fixture", os.getuid(), os.getgid(), tmp_path, "/bin/sh")

    class Executor:
        def run(self, action, settings):
            raise AssertionError("No real privileged operation in a fixture")

    return InstallationService(
        Path(__file__).resolve().parents[1],
        interaction=Interaction(),
        executor=Executor(),
        authorize=lambda: True,
        identity=lambda _: target,
    )


def test_plan_never_calls_executor_or_creates_files(service, tmp_path: Path) -> None:
    service.run(
        InstallRequest(Operation.PLAN, section=Section.WORKSPACE, settings=UserSettings(happ=True))
    )
    assert list(tmp_path.iterdir()) == []


def test_unsupported_platform_blocks_apply_but_not_navigation(service, monkeypatch) -> None:
    monkeypatch.setattr("platform.machine", lambda: "riscv64")
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
    assert any("PARTIAL:" in line for line in service.interaction.lines)


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


def test_region_changes_have_independent_operations_without_desktop(service, monkeypatch) -> None:
    monkeypatch.setattr("setup_system.infrastructure.regional.actual", lambda kind: None)
    request = InstallRequest(
        Operation.PLAN, system=SystemSettings(timezone="Pacific/Tahiti", keyboard="us,ru")
    )
    ids = [action.id for action in service.plan(request).actions]
    assert "timezone" in ids and "keyboard" in ids
    assert "base" not in ids and "desktop_files" not in ids


@pytest.mark.parametrize("settings", [UserSettings(), UserSettings(firefox=True)])
def test_unselected_desktop_is_absent_from_workspace_plan(service, settings) -> None:
    request = InstallRequest(Operation.PLAN, section=Section.WORKSPACE, settings=settings)
    ids = {action.id for action in service.plan(request).actions}
    assert not {"theme", "desktop_files", "base"} & ids


def test_empty_workspace_report_distinguishes_no_selection(service) -> None:
    request = InstallRequest(Operation.APPLY, section=Section.WORKSPACE)
    assert service.run(request) == 0
    assert "NO_SELECTION" in service.interaction.lines[-1]


def test_verified_no_changes_does_not_authorize_privileges(service, monkeypatch):
    monkeypatch.setattr("setup_system.infrastructure.regional.actual", lambda kind: "UTC")
    service.authorize = lambda: pytest.fail("Verified matching timezone cannot authorize sudo")
    request = InstallRequest(Operation.APPLY, system=SystemSettings(timezone="UTC"))
    assert service.run(request) == 0
    assert service.status == "No changes"
    assert "NO_CHANGES" in service.interaction.lines[-1]


def test_workspace_target_mismatch_stops_before_all_actions(service, monkeypatch):
    monkeypatch.setattr("platform.machine", lambda: "sparc")
    monkeypatch.setattr(service, "_apply", lambda *args: pytest.fail("Unsupported action ran"))
    assert (
        service.run(
            InstallRequest(
                Operation.APPLY,
                section=Section.WORKSPACE,
                settings=UserSettings(desktop_theme=True),
            )
        )
        == 4
    )
    assert service.status == "Blocked"


def test_separate_writers_share_one_lock_without_creating_settings(tmp_path):
    from setup_system.infrastructure.mutation import user_operation

    with user_operation(tmp_path), pytest.raises(RuntimeError, match="Another configuration"):
        with user_operation(tmp_path):
            pytest.fail("Concurrent writer acquired the same HOME")
    with user_operation(tmp_path):
        assert not list(tmp_path.iterdir())


def test_changed_draft_during_confirmation_stops_before_authorization(service, monkeypatch):
    monkeypatch.setattr("setup_system.infrastructure.regional.actual", lambda kind: None)

    def confirm(prompt):
        store = service._draft_store()
        store.save(SystemSettings(timezone="UTC"), None)
        return True

    service.interaction.confirm = confirm
    service.authorize = lambda: pytest.fail("Stale settings reached sudo")
    assert (
        service.run(InstallRequest(Operation.APPLY, system=SystemSettings(timezone="Europe/Paris")))
        == 5
    )
    assert "changed after plan" in service.interaction.lines[-1]


def test_confirmed_happ_upgrade_passes_the_observed_package_version(service, monkeypatch):
    from setup_system import installation
    from setup_system.adapters.debian import packages

    calls = []
    monkeypatch.setattr(packages, "installed_version", lambda name: "4.0")
    monkeypatch.setattr(
        installation,
        "_check_existing_happ",
        lambda version, **kwargs: calls.append(kwargs["expected_happ_version"]),
    )
    monkeypatch.setattr(service.executor, "run", lambda action, settings: calls.append(settings))
    result = service.run(
        InstallRequest(
            Operation.APPLY, section=Section.WORKSPACE, settings=UserSettings(apps=("happ",))
        )
    )
    assert result == 0 and calls[0] == "4.0"
    assert calls[1]["expected_happ_version"] == "4.0"
    assert any("4.0 →" in line for line in service.interaction.lines)
