"""Launch browser sign-in only for an official binary verified in the local ledger."""

import asyncio
import hashlib

import pytest
import setup_plugin_codex as app

from setup_system_sdk import Mutation, Outcome, Resource, ownership_context


@pytest.mark.parametrize(
    ("terminal_rc", "active", "expected"),
    [
        (0, True, Outcome.CHANGED),
        (0, False, Outcome.NEEDS_USER_ACTION),
        (1, True, Outcome.NEEDS_USER_ACTION),
    ],
)
def test_sign_in_rechecks_binary_and_native_account(
    tmp_path, monkeypatch, terminal_rc, active, expected
):
    monkeypatch.setenv("HOME", str(tmp_path))
    binary = app.release.binary()
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"synthetic official client")
    binary.chmod(0o700)
    holdings = (
        Resource(
            kind="file",
            identity=str(binary),
            ownership="created",
            after=hashlib.sha256(binary.read_bytes()).hexdigest(),
            cleanup=True,
            permissions=0o700,
        ),
    )
    client, inputs, settings = app.Plugin(), app.SignIn(), app.Settings()
    assert client.prepare("login", settings, inputs).outcome == Outcome.NEEDS_USER_ACTION
    launched = []
    checked = []

    def status(*argv):
        assert argv == (
            str(binary),
            "-c",
            'cli_auth_credentials_store="keyring"',
            "login",
            "status",
        )
        checked.append(True)
        return (0 if launched and active else 1, "")

    def terminal(argv):
        assert argv == [str(binary), "-c", 'cli_auth_credentials_store="keyring"', "login"]
        launched.append(True)
        return terminal_rc

    monkeypatch.setattr(app, "command", status)
    monkeypatch.setattr(app.user_terminal, "run_native", terminal)
    with ownership_context(holdings):
        monkeypatch.setattr(app.user_terminal, "available", lambda: False)
        assert client.prepare("login", settings, inputs).outcome == Outcome.NEEDS_USER_ACTION
        monkeypatch.setattr(app.user_terminal, "available", lambda: True)
        plan = client.prepare("login", settings, inputs)
        assert plan.outcome == Outcome.READY
        result = asyncio.run(
            client.apply_request(
                "login",
                Mutation(
                    action_id="login",
                    inputs={},
                    settings={},
                    plan=plan,
                    holdings=holdings,
                ),
            )
        )
        assert result.outcome == expected and len(checked) == 4
        assert asyncio.run(client.verify(plan, settings, inputs)).outcome == (
            Outcome.COMPLETE if active else Outcome.NEEDS_USER_ACTION
        )
        if active:
            assert client.prepare("login", settings, inputs).outcome == Outcome.NO_CHANGES
        binary.write_bytes(b"changed")
        assert client.prepare("login", settings, inputs).outcome == Outcome.NEEDS_USER_ACTION
        assert len(launched) == 1
