"""The Typer and source entrypoints share one installation application service."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from setup_system.cli.main import app

ROOT = Path(__file__).resolve().parents[1]


def test_retired_installation_frontends_are_absent() -> None:
    assert not (ROOT / "bootstrap.py").exists()
    assert not (ROOT / "src/setup_system/user_install.py").exists()


@pytest.mark.parametrize("source", [str(ROOT), "."])
def test_typer_install_plan_uses_application_service(monkeypatch, source: str) -> None:
    monkeypatch.chdir(ROOT)
    from setup_system.application import installer

    calls = []
    monkeypatch.setattr(
        installer.InstallationService,
        "run",
        lambda self, request: calls.append((self.root, request)) or 0,
    )
    result = CliRunner().invoke(
        app,
        [
            "install",
            "plan",
            "--source",
            source,
            "--user",
            "alice",
            "--xdg-config-home",
            "/home/alice/config with spaces",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    root, request = calls[0]
    assert root == ROOT
    assert request.operation == installer.Operation.PLAN
    assert request.user == "alice"
    assert request.overrides == {"config": Path("/home/alice/config with spaces")}
    assert not request.yes


def test_typer_install_apply_propagates_real_result(monkeypatch) -> None:
    from setup_system.application import installer

    calls = []
    monkeypatch.setattr(
        installer.InstallationService, "run", lambda self, request: calls.append(request) or 5
    )
    result = CliRunner().invoke(app, ["install", "apply", "--source", str(ROOT), "--yes"])
    assert result.exit_code == 5
    assert len(calls) == 1
    assert calls[0].operation == installer.Operation.APPLY
    assert calls[0].yes


def test_typer_install_plan_refuses_apply_consent() -> None:
    result = CliRunner().invoke(app, ["install", "plan", "--yes"])
    assert result.exit_code == 2
