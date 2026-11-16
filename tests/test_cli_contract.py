"""Stable CLI registration and retired installation options."""

from __future__ import annotations

import pytest
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from setup_system.cli import main


def test_command_groups_and_public_option_names_remain_available() -> None:
    command = get_command(main.app)
    assert isinstance(command, TyperGroup)
    assert set(command.commands) == {
        "agents",
        "apps",
        "apply",
        "backup",
        "doctor",
        "git",
        "happ",
        "install",
        "plan",
        "projects",
        "restore",
        "rtk",
        "shell",
        "templates",
        "workstation",
        "tui",
    }
    groups = {
        "install": {"plan", "apply"},
        "apps": {"install", "list"},
        "projects": {"list", "prepare", "path", "open"},
        "templates": {"list", "path", "versions", "create", "update", "cache"},
        "workstation": {"plan", "doctor", "font-probe", "prefetch", "apply", "backup", "restore"},
    }
    for name, expected in groups.items():
        group = command.commands[name]
        assert isinstance(group, TyperGroup)
        assert set(group.commands) == expected
    workstation_group = command.commands["workstation"]
    assert isinstance(workstation_group, TyperGroup)
    assert "--offline" in {
        option for param in workstation_group.commands["apply"].params for option in param.opts
    }
    install_group = command.commands["install"]
    assert isinstance(install_group, TyperGroup)
    for operation in ("plan", "apply"):
        assert "--vpn-config-file" in {
            option for param in install_group.commands[operation].params for option in param.opts
        }
    restore = command.commands["restore"]
    assert {option for param in restore.params for option in param.opts} >= {
        "--backup",
        "--dry-run",
        "--apply",
        "--yes",
        "--replaceCurrent",
    }


@pytest.mark.parametrize(
    "path", [[], ["apps"], ["projects"], ["templates"], ["workstation"], ["install"]]
)
def test_help_does_not_enter_an_executor(monkeypatch: pytest.MonkeyPatch, path: list[str]) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("help entered an executor")

    monkeypatch.setattr("setup_system.application.workstation.apply", fail)
    result = CliRunner().invoke(main.app, [*path, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output and "Commands" in result.output


def test_retired_package_install_flag_cannot_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("a retired option entered apply")

    monkeypatch.setattr("setup_system.application.workstation.apply", fail)
    result = CliRunner().invoke(
        main.app,
        ["workstation", "apply", "--profile", "hyprland", "--yes", "--install-debian-packages"],
    )
    assert result.exit_code == 2, result.output
    assert "No such option" in result.output
