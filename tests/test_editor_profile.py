"""Profile selection, native Git resolution and headless failure propagation."""

import json
import os
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from native_workspace import FILTERS, ROOT, files
from pydantic import ValidationError
from test_native_ansible import play
from test_native_ansible import run_env as run_env


def test_neovim_action_fails_if_parser_verification_fails(run_env):
    """Inject only the failing editor process into the actual Ansible verification task."""
    stage = Path(run_env["SETUP_SYSTEM_RESULT"]).parent
    proof = stage / "after-smoke"
    fixture = stage / "failing-editor"
    fixture.write_text(
        "#!" + sys.executable + "\n"
        "import json,os,sys\n"
        "assert json.loads(os.environ['SETUP_EDITOR_PARSERS']) == ['python']\n"
        "assert '--headless' in sys.argv and '-u' in sys.argv\n"
        "print('Parser verification failed',file=sys.stderr)\nsys.exit(23)\n"
    )
    fixture.chmod(0o700)
    role = yaml.safe_load((ROOT / "ansible/tasks/user_apply.yml").read_text())
    verify = next(task for task in role if task["name"].startswith("Verify the editor starts"))
    args = verify["ansible.builtin.command"]["argv"]
    assert args[0] == "/usr/bin/nvim"
    args[0] = str(fixture)
    (stage / "editor-check").mkdir()
    book = stage / "smoke.yml"
    book.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "become": False,
                    "vars": {
                        "selected": ["neovim"],
                        "run_dir": str(stage),
                        "intent": {"languages": ["python"]},
                        "selected_parsers": ["python"],
                        "facts": {
                            "home": run_env["HOME"],
                            "xdg": {"config": run_env["XDG_CONFIG_HOME"]},
                        },
                    },
                    "tasks": [
                        verify,
                        {
                            "ansible.builtin.copy": {
                                "content": "done",
                                "dest": str(proof),
                                "mode": "0600",
                            }
                        },
                    ],
                }
            ]
        )
    )
    result = play([str(book)], run_env)
    assert result.returncode != 0
    assert "Parser verification failed" in result.stdout + result.stderr
    assert not proof.exists()


def test_editor_catalog_covers_selectable_languages_and_resolves_compatible_git_tags(
    tmp_path, monkeypatch
):
    catalog = tomllib.loads((ROOT / "config/workspace/applications.toml").read_text())
    editor = tomllib.loads((ROOT / "config/workspace/editor.toml").read_text())
    assert set(editor["parsers"]) - {"base"} == {
        name for name, entry in catalog.items() if entry["kind"] == "language"
    }
    assert all(row["repo"].startswith("https://github.com/") for row in editor["plugins"].values())
    for key in ("config", "data", "state", "cache"):
        monkeypatch.setenv("XDG_" + key.upper() + "_HOME", str(tmp_path / key))
    monkeypatch.setenv("HOME", str(tmp_path))
    desired, _ = files(ROOT, {"apps": ["neovim"], "languages": ["python"]})
    init = desired[tmp_path / "config/nvim/init.lua"].decode()
    assert all(json.dumps(name) in init for name in editor["plugins"])
    assert 'local selected = {"python"}' in init and "missing = false" in init
    assert {"ruff", "basedpyright"} == set(catalog["python"]["pypi"])
    rows = [{"ref": "refs/tags/" + v} for v in ("v1.3.4", "v1.12.0", "v2.0.0", "v1.13.0-beta")]
    choose = FILTERS["stable_tag"]
    assert choose(rows, 1) == "refs/tags/v1.12.0"
    with pytest.raises(ValueError, match="No stable release"):
        choose(rows, 4)


def test_search_exclusions_follow_the_catalog_in_both_editor_pickers(run_env):
    stage = Path(run_env["SETUP_SYSTEM_RESULT"]).parent
    globs = ["**/private file", '**/quo"te', "**/подборка/$(echo test)"]
    catalog = stage / "editor.toml"
    catalog.write_text("[search]\nexclude_globs = " + json.dumps(globs, ensure_ascii=False))
    output = stage / "init.lua"
    step = {
        "src": str(ROOT / "ansible/templates/nvim-init.lua"),
        "dest": str(output),
        "mode": "0600",
        "variable_start_string": "@{",
        "variable_end_string": "}@",
    }
    book = stage / "editor.yml"
    lookup = "{{ lookup('ansible.builtin.file', catalog_path) | native_toml }}"
    book.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "become": False,
                    "vars": {
                        "catalog_path": str(catalog),
                        "editor_catalog": lookup,
                        "plugin_lua": "{}",
                        "selected_lua": "{}",
                    },
                    "tasks": [{"ansible.builtin.template": step}],
                }
            ]
        )
    )
    result = play([str(book)], run_env)
    assert result.returncode == 0, result.stdout + result.stderr
    init = output.read_text()
    rows = next(line for line in init.splitlines() if line.startswith("local excluded = {"))
    assert (
        json.loads("[" + rows.removeprefix("local excluded = {").removesuffix("}") + "]") == globs
    )
    assert 'vim.fn.shellescape("!" .. glob)' in init
    assert any("rg --files" in line and "rgFlags" in line for line in init.splitlines())
    assert any("rg_opts =" in line and "rgFlags" in line for line in init.splitlines())


@pytest.mark.parametrize(
    "search",
    [
        {"exclude_globs": []},
        {"exclude_globs": [""]},
        {"exclude_globs": ["!allow-credentials"]},
        {"exclude_globs": ["line\nbreak"]},
        {"exclude_globs": [False]},
        {"exclude_globs": ["valid"], "unknown": True},
    ],
)
def test_invalid_editor_search_policy_stops_rendering(search):
    with pytest.raises(ValidationError):
        FILTERS["editor_globs_lua"](json.dumps(search))


def test_existing_flutter_checkout_with_local_changes_blocks_before_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sdk = tmp_path / "SDK directory"
    url = "https://github.com/flutter/flutter.git"
    observe = runpy.run_path(str(ROOT / "ansible/files/user_checkout.py"))["observe"]
    assert observe(sdk, url) == ""
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(["git", "init", "--quiet", str(sdk)], check=True, env=env)
    subprocess.run(["git", "-C", str(sdk), "remote", "add", "origin", url], check=True, env=env)
    personal = sdk / "personal.txt"
    personal.write_text("local changes")
    with pytest.raises(ValueError, match="local edits"):
        observe(sdk, url)
    assert personal.read_text() == "local changes"


def test_failed_editor_smoke_leaves_retryable_state(tmp_path, monkeypatch):
    module = runpy.run_path(str(ROOT / "ansible/files/editor_proof.py"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    proof = module["run"]
    current = {"signature": "1" * 64}
    monkeypatch.setitem(proof.__globals__, "proof", lambda request: current["signature"])
    assert not proof("check", {}) and not (tmp_path / "state").exists()
    assert not proof("check", {})  # A failed native smoke never calls record.
    proof("record", {})
    assert proof("check", {})
    current["signature"] = "2" * 64
    assert not proof("check", {})
    file = tmp_path / "state/setup-system/editor-verification.v1"
    file.write_text("external data")
    with pytest.raises(ValueError, match="Unrecognized"):
        proof("record", {})
    assert file.read_text() == "external data"
