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


@pytest.mark.parametrize("operation", ["plan", "apply"])
def test_typer_private_subscription_file_is_mapped_without_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from setup_system.application import installer

    private = tmp_path / "private input with spaces"
    private.write_text("https://provider.invalid/subscription?token=SYNTHETIC_PRIVATE_MARKER")
    private.chmod(0o600)
    observed = []
    monkeypatch.setattr(
        installer.InstallationService, "run", lambda self, request: observed.append(request) or 0
    )
    args = ["install", operation, "--vpn-config-file", str(private)]
    if operation == "apply":
        args.append("--yes")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert len(observed) == 1 and observed[0].subscription_file == private
    assert private.read_text().endswith("SYNTHETIC_PRIVATE_MARKER")
    assert "SYNTHETIC_PRIVATE_MARKER" not in result.output


def test_private_handoff_uses_exact_eight_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    from setup_system import entrypoint
    from setup_system.application import installer

    environment = {
        f"XDG_{kind.upper()}_HOME": f"/home/alice/{kind}" for kind in entrypoint.XDG_KINDS
    }
    private = Path("/home/alice/private input")
    args = entrypoint.source_arguments(
        installer.Operation.APPLY, "alice", environment, yes=True, subscription_file=private
    )
    assert args == [
        "apply",
        "alice",
        "/home/alice/config",
        "/home/alice/data",
        "/home/alice/state",
        "/home/alice/cache",
        "1",
        str(private),
    ]
    observed = []
    monkeypatch.setattr(
        installer.InstallationService, "run", lambda self, request: observed.append(request) or 0
    )
    assert entrypoint.main(ROOT, args) == 0
    assert observed[0].subscription_file == private


@pytest.mark.parametrize("args", [[], ["apply"] * 7, ["apply"] * 9])
def test_malformed_private_handoff_never_calls_service(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    from setup_system import entrypoint
    from setup_system.application import installer

    monkeypatch.setattr(
        installer.InstallationService,
        "run",
        lambda *a: pytest.fail("Malformed private handoff entered the service"),
    )
    assert entrypoint.main(ROOT, args) == 2
