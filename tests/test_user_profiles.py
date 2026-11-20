"""Keep portable profile consent and failure handling independent of CLI presentation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from setup_system.application import user_profiles as profiles
from setup_system.application.interaction import ConsoleInteraction
from setup_system.planning import Plan, Platform


@dataclass
class Interaction(ConsoleInteraction):
    accepted: bool = False
    lines: list[tuple[str, bool]] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    def write(
        self, text: str, *, end: str = "\n", error: bool = False, flush: bool = False
    ) -> None:
        self.lines.append((text, error))

    def confirm(self, prompt: str) -> bool:
        self.questions.append(prompt)
        return self.accepted


def fixture_plan(*, supported: bool = True) -> Plan:
    return Plan(Platform("debian", "13", "x86_64", supported, False, "fixture"), (), ())


@pytest.mark.parametrize("supported", [True, False])
def test_refusal_stops_before_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, supported: bool
) -> None:
    interaction = Interaction()
    monkeypatch.setattr(profiles, "create_plan", lambda *_: fixture_plan(supported=supported))
    monkeypatch.setattr(
        profiles, "apply_configuration", lambda *_a, **_k: pytest.fail("applied without consent")
    )
    code = profiles.UserProfileService(tmp_path, interaction=interaction).run("base")
    assert code == (2 if supported else 4)
    assert interaction.questions == (["Apply user configuration?"] if supported else [])
    assert not list(tmp_path.iterdir())


def test_base_profile_uses_application_interaction_and_reports_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    interaction = Interaction(accepted=True)
    monkeypatch.setattr(profiles, "create_plan", lambda *_: fixture_plan())
    calls: list[tuple[Path, bool]] = []

    def configure(root: Path, *, terminal_dev: bool) -> tuple[Path, list[tuple[str, str]]]:
        calls.append((root, terminal_dev))
        return tmp_path / "backup", [("fixture.conf", "unchanged")]

    monkeypatch.setattr(profiles, "apply_configuration", configure)
    monkeypatch.setattr(
        profiles, "install_zsh", lambda *_: pytest.fail("unexpected package install")
    )
    code = profiles.UserProfileService(tmp_path, interaction=interaction).run("base")
    assert code == 0 and calls == [(tmp_path, False)]
    assert interaction.questions == ["Apply user configuration?"]
    assert interaction.lines[-2:] == [
        (f"backup: {tmp_path / 'backup'}", False),
        ("unchanged: fixture.conf", False),
    ]


def test_configuration_failure_preserves_failure_exit_and_diagnostic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    interaction = Interaction()
    monkeypatch.setattr(profiles, "create_plan", lambda *_: fixture_plan())

    def conflict(*_args: object, **_kwargs: object) -> None:
        raise ValueError("existing file conflict")

    monkeypatch.setattr(profiles, "apply_configuration", conflict)
    code = profiles.UserProfileService(tmp_path, interaction=interaction).run("base", yes=True)
    assert code == 5
    assert interaction.lines == [("failed: existing file conflict", True)]
    assert not interaction.questions


def test_unknown_profile_fails_before_plan_or_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        profiles, "create_plan", lambda *_: pytest.fail("planned an unknown profile")
    )
    with pytest.raises(ValueError, match=r"^Unknown setup profile$"):
        profiles.UserProfileService(tmp_path).run("unknown")
    assert not list(tmp_path.iterdir())
