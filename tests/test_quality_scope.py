"""Keep shipped programs visible to extension-based static checks and coverage."""

import argparse
import ast
import os
import runpy
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def test_production_scripts_are_discoverable_by_native_checks() -> None:
    tools = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]
    typed = [p for pattern in tools["mypy"]["files"] for p in ROOT.glob(pattern)]
    coverage = [
        ROOT / ("hatch_build.py" if entry == "hatch_build" else entry)
        for entry in tools["coverage"]["run"]["source"]
    ]
    provider = {"docker/python_release.py", "docker/release.py"}
    for directory in ("src", "plugins", "assets", "debian", "docker", "libexec"):
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or "tests" in path.parts:
                continue
            with path.open("rb") as stream:
                first = stream.readline(256)
            if first.startswith(b"#!") and b"python" in first:
                assert path.suffix == ".py", path
            if first.rstrip() in {b"#!/bin/sh", b"#!/bin/bash", b"#!/usr/bin/env bash"}:
                assert path.suffix == ".sh" or path == ROOT / "debian/publish", path
            if path.suffix != ".py" or str(path.relative_to(ROOT)) in provider:
                continue
            assert any(path.is_relative_to(scope) for scope in typed), path
            assert any(path.is_relative_to(scope) for scope in coverage), path

            tree = ast.parse(path.read_bytes())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    annotation = node.returns
                elif isinstance(node, (ast.arg, ast.AnnAssign)):
                    annotation = node.annotation
                else:
                    continue
                if annotation is not None:
                    assert not any(
                        isinstance(item, ast.Name) and item.id == "object"
                        for item in ast.walk(annotation)
                    ), (path, node.lineno)


def test_packaging_and_execution_scripts_are_inert_on_import(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    forbidden = Mock(side_effect=AssertionError("Entry point ran during import"))
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", forbidden)
    monkeypatch.setattr(os, "execvp", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for name in (
        "debian/build.py",
        "docker/installed_smoke.py",
        "docker/verify-installed.py",
        "libexec/dpkg_lock.py",
    ):
        runpy.run_path(str(ROOT / name), run_name="imported_script")
    forbidden.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_bundled_make_workers_preserve_the_sealed_environment():
    for makefile in (ROOT / "plugins").glob("*/*/src/*/Makefile"):
        for line in makefile.read_text().splitlines():
            if "SETUP_SYSTEM_INTERPRETER" in line and " -m " in line:
                assert " -B " in line and " -I " in line, makefile
