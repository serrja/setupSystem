"""Drive approved editor actions against a real local Git remote and a synthetic parser."""

import asyncio
import subprocess

import pytest
from setup_plugin_neovim import Inputs, Plugin, Settings, extensions

from setup_system_sdk import Mutation, Outcome, ownership_context


def test_configure_install_checkouts_and_parser_readback(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv("XDG_" + key + "_HOME", str(tmp_path / key.lower()))
    remote = tmp_path / "source"
    subprocess.run(["git", "init", "--quiet", str(remote)], check=True)
    (remote / "README").write_text("Synthetic editor fixture")
    subprocess.run(["git", "-C", str(remote), "add", "README"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(remote),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Fixture",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(remote), "tag", "v1.2.3"], check=True)
    real = extensions.git

    def configured_git(*args):
        # Map only the trusted fixture URL to its local origin for clone and ls-remote.
        if args[0] in {"clone", "ls-remote"}:
            return real("-c", f"url.{remote}.insteadOf=https://github.com/test/fixture.git", *args)
        return real(*args)

    monkeypatch.setattr(extensions, "git", configured_git)
    monkeypatch.setattr(
        extensions,
        "catalog",
        lambda: {
            "nvim-treesitter": extensions.Source(
                repo="https://github.com/test/fixture.git", major=1
            )
        },
    )
    monkeypatch.setattr(extensions, "parsers", lambda _: ["lua"])
    plugin = Plugin()
    settings = Settings(clipboard=False, primary_editor=False)
    rows = []

    def apply(action):
        with ownership_context(tuple(rows)):
            plan = plugin.prepare(action, settings, Inputs())
            assert plan.outcome == Outcome.READY
            result = asyncio.run(
                plugin.apply_request(
                    action,
                    Mutation(
                        action_id=action,
                        inputs={},
                        settings=settings.model_dump(mode="json"),
                        plan=plan,
                        holdings=tuple(rows),
                    ),
                )
            )
            assert result.outcome in (Outcome.CHANGED, Outcome.COMPLETE)
            verified = asyncio.run(plugin.verify(plan, settings, Inputs()))
            assert verified.outcome == Outcome.COMPLETE
            rows.extend(verified.resources)

    apply("configure")
    apply("install-plugins")
    assert plugin.prepare("install-plugins", settings, Inputs()).outcome == Outcome.NO_CHANGES
    parser = tmp_path / "data/setup-system/editor-parsers/parser/lua.so"

    def editor(argv, **kwargs):
        assert "--headless" in argv and "-u" in argv
        assert kwargs["env"]["SETUP_EDITOR_PARSERS"] == '["lua"]'
        if "TSInstallSync lua" in argv:
            parser.parent.mkdir(parents=True)
            parser.write_bytes(b"synthetic parser")
        return subprocess.CompletedProcess(argv, 0)

    original = extensions.run_command
    monkeypatch.setattr(
        extensions,
        "run_command",
        lambda argv, **kwargs: (
            editor(argv, **kwargs) if argv[0] == "/usr/bin/nvim" else original(argv, **kwargs)
        ),
    )
    apply("install-parsers")
    with ownership_context(tuple(rows)):
        assert plugin.prepare("install-parsers", settings, Inputs()).outcome == Outcome.NO_CHANGES
        assert (
            plugin.mutate(
                "verify-editor",
                settings,
                Inputs(),
                plugin.prepare("verify-editor", settings, Inputs()),
            ).outcome
            == Outcome.COMPLETE
        )
    parser.write_bytes(b"user edits")
    with ownership_context(tuple(rows)):
        assert plugin.prepare("install-parsers", settings, Inputs()).outcome == Outcome.BLOCKED
        assert plugin.prepare("remove-parsers", settings, Inputs()).outcome == Outcome.BLOCKED
    parser.write_bytes(b"synthetic parser")
    unrelated = parser.parent / "custom.so"
    unrelated.write_bytes(b"owner parser")
    apply("remove-parsers")
    assert not parser.exists() and unrelated.read_bytes() == b"owner parser"
    init = tmp_path / "config/nvim/init.lua"
    init.write_bytes(b"unexpected")
    with ownership_context(tuple(rows)), pytest.raises(ValueError, match="managed Neovim"):
        extensions.nvim("version", [])
