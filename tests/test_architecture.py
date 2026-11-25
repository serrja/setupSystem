"""Keep the shipped layer boundaries and read-only imports executable in the native gate."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src/setup_system"


def module_imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield from (node.module + "." + alias.name for alias in node.names)


def test_layer_dependencies_and_domain_purity() -> None:
    for path in SOURCE.rglob("*.py"):
        parts = path.relative_to(SOURCE).parts
        layer = parts[0]
        tree = ast.parse(path.read_text())
        imports = tuple(module_imports(tree))
        assert not any(name.startswith("setup_plugin_") for name in imports), path
        if layer == "domain":
            for name in imports:
                assert not name.startswith("setup_system.") or name.startswith(
                    "setup_system.domain."
                ), (path, name)
                assert name.split(".")[0] not in {
                    "os",
                    "subprocess",
                    "platform",
                    "socket",
                    "urllib",
                    "tempfile",
                    "shutil",
                    "importlib",
                }, (path, name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        assert node.func.id not in {"open", "__import__"}, (path, node.lineno)
                    elif isinstance(node.func, ast.Attribute):
                        assert node.func.attr not in {
                            "open",
                            "read_text",
                            "read_bytes",
                            "write_text",
                            "write_bytes",
                            "home",
                            "expanduser",
                            "resolve",
                            "exists",
                            "glob",
                            "stat",
                            "lstat",
                            "iterdir",
                            "is_file",
                            "cwd",
                        }, (path, node.lineno)
        if layer in {"infrastructure", "adapters", "bootstrap", "application"}:
            forbidden = [
                "setup_system.entrypoint.",
                "setup_system.composition.",
                "setup_system.tui.",
                "setup_system.ui.",
                "setup_system.app.",
            ]
            if layer != "application":
                forbidden.append("setup_system.application.")
            assert not any(name.startswith(tuple(forbidden)) for name in imports), path
        if layer in {"tui", "ui"}:
            assert not any(
                name.startswith(
                    (
                        "setup_system.infrastructure.",
                        "setup_system.adapters.",
                        "setup_system.bootstrap.",
                        "setup_system.composition.",
                    )
                )
                for name in imports
            ), path
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "model_construct", (path, node.lineno)


def test_imports_never_read_the_release_catalog(tmp_path: Path) -> None:
    modules = [
        ".".join(("setup_system", *path.relative_to(SOURCE).with_suffix("").parts))
        for path in sorted(SOURCE.rglob("*.py"))
        if path.name != "__init__.py" and "resources" not in path.parts
    ]
    program = """
import importlib, platform, sys
def observed_host_query(query):
    def call(*args, **kwargs):
        caller = sys._getframe(1).f_globals.get("__name__", "")
        if caller.startswith("setup_system."):
            raise AssertionError(f"{caller} queried the host during import")
        return query(*args, **kwargs)
    return call
for name in ("machine", "system", "freedesktop_os_release"):
    setattr(platform, name, observed_host_query(getattr(platform, name)))

def observe(event, args):
    if event == 'open' and isinstance(args[0], str):
        path = args[0]
        caller = sys._getframe(1).f_globals.get('__name__', '')
        catalog = path.endswith('.toml') and any(
            '/'+s+'/' in path for s in ('config', 'manifests', 'resources')
        )
        private = path.startswith(('/etc/', sys.argv[2] + '/'))
        if catalog or (private and caller.startswith('setup_system.')):
            raise AssertionError('Import opened ' + path)
sys.addaudithook(observe)
sys.path.insert(0, sys.argv[1])
for module in sys.argv[3:]:
    importlib.import_module(module)
"""
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(SOURCE.parent), str(tmp_path), *modules],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        check=True,
        timeout=45,
    )
