"""Exercise staged native desktop resources with the installed role's Jinja settings."""

import json
import os
import runpy
import shlex
import tomllib
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

from setup_system.infrastructure.managed_files import apply_files, changed_files

ROOT = Path(__file__).resolve().parents[1]
FILTERS = runpy.run_path(str(ROOT / "ansible/filter_plugins/workspace.py"))


def facts():
    return {
        "home": str(Path.home()),
        "xdg": {
            key: os.environ.get("XDG_" + key.upper() + "_HOME", str(Path.home() / key))
            for key in ("config", "data", "state", "cache")
        },
    }


def render(source, settings=None, info=None, **overrides):
    role = yaml.safe_load((ROOT / "ansible/tasks/user_plan.yml").read_text())
    step = next(
        task["ansible.builtin.template"] for task in role if "ansible.builtin.template" in task
    )
    env = Environment(
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        **{k: v for k, v in step.items() if k.endswith("_string")},
    )
    env.filters["quote"] = shlex.quote
    env.filters["editor_globs_lua"] = FILTERS["editor_globs_lua"]
    env.filters["to_json"] = json.dumps
    ctx = {"render_ctx": FILTERS["context"](str(ROOT), settings or {}, info or facts())}
    editor = tomllib.loads((ROOT / "config/workspace/editor.toml").read_text())
    ctx["editor_catalog"] = editor
    ctx["plugin_lua"] = "{" + ",".join(map(json.dumps, editor["plugins"])) + "}"
    ctx.update(overrides)
    return env.from_string(source).render(**ctx)


def files(root, settings=None):
    settings = {"hyprland": True} if settings is None else settings
    info = facts()
    selected = settings.get("apps", []) + settings.get("languages", [])
    rows = FILTERS["editor_specs"](root, info, selected, settings, {})
    modes = {Path.home() / row["path"]: int(row["mode"], 8) for row in rows}
    desired = {
        Path.home() / row["path"]: render(Path(row["source"]).read_text(), settings, info).encode()
        for row in rows
    }
    return desired, modes


def drift(root):
    desired, modes = files(root)
    return changed_files(desired, modes=modes)


def apply(root):
    desired, modes = files(root)
    return apply_files(desired, modes=modes)
