"""Probe an isolated stdio fixture that implements the official read-only client exchange."""

import asyncio
import sys

from setup_plugin_codex import Plugin, Settings, client

from setup_system_sdk import Inputs, Outcome
from setup_system_sdk.worker import check_choices


def test_uninstalled_defaults_and_native_capability_readback(tmp_path, monkeypatch):
    import setup_plugin_codex as app

    executable = tmp_path / "fixture-client"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(app, "program", lambda: executable)
    plugin = Plugin()
    settings = Settings()
    asyncio.run(check_choices(plugin, settings, settings))
    assert plugin.prepare("check", settings, Inputs()).outcome == Outcome.BLOCKED
    assert plugin.prepare("status", settings, Inputs()).outcome == Outcome.BLOCKED
    assert "unavailable" in asyncio.run(plugin.inspect(settings)).message
    executable.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import sys,json
if sys.argv[1]=='--version':
    print('fixture 1.0')
    sys.exit()
if sys.argv[1] in ('login','logout'):
    sys.exit()
assert sys.argv[1:]==['app-server','--listen','stdio://']
for raw in sys.stdin:
    item=json.loads(raw)
    if item.get('method')=='initialized': continue
    result={}
    if item['method']=='model/list':
        result={'data':[{'model':'fixture','supportedReasoningEfforts':[{'reasoningEffort':'low'}]}]}
    print(json.dumps({'id':item['id'],'result':result}),flush=True)
"""
    )
    executable.chmod(0o700)
    assert plugin.prepare("check", settings, Inputs()).outcome == Outcome.NO_CHANGES
    assert plugin.prepare("status", settings, Inputs()).outcome == Outcome.NO_CHANGES
    assert asyncio.run(plugin.choices("models", "fix", settings)) == ["fixture"]
    assert asyncio.run(plugin.choices("efforts", "l", Settings(model="fixture"))) == ["low"]
    assert asyncio.run(client.models(executable)) == {"fixture": ["low"]}
    logout = plugin.prepare("logout", settings, Inputs())
    assert (
        logout.outcome == Outcome.READY
        and plugin.mutate("logout", settings, Inputs(), logout).outcome == Outcome.CHANGED
    )
    assert (
        asyncio.run(plugin.verify(logout, settings, Inputs())).outcome == Outcome.NEEDS_USER_ACTION
    )
    # The fixture's read-only query fails before any installation when its response is invalid.
    executable.write_text("#!" + sys.executable + "\nimport sys; sys.exit(2)\n")
    import pytest

    with pytest.raises((ValueError, BrokenPipeError, ConnectionResetError)):
        asyncio.run(client.models(executable))
