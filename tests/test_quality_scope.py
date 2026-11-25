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


def test_current_offline_references_match_model_generators():
    import sys

    # The generator loads each trusted package model. Keep this test's module changes local.
    modules = dict(sys.modules)
    try:
        generate = runpy.run_path(str(ROOT / "docker/plugin_reference.py"))["update"]
        for project in (ROOT / "plugins").glob("*/*/pyproject.toml"):
            assert generate(project, False) == []
    finally:
        for name in tuple(sys.modules):
            if name.startswith("setup_plugin_"):
                if name in modules:
                    sys.modules[name] = modules[name]
                else:
                    sys.modules.pop(name)
    reference = runpy.run_path(str(ROOT / "docker/settings_reference.py"))["reference"]
    assert reference(ROOT) == (ROOT / "docs/settings_map.md").read_text()


def test_coverage_evidence_validates_denominators_and_run_timestamp(tmp_path, monkeypatch, capsys):
    import json

    import pytest

    tools = runpy.run_path(str(ROOT / "docker/coverage_report.py"))
    publish = tools["report"]
    output = tmp_path / "summary.md"
    source = tmp_path / "coverage.json"
    payload = {
        "meta": {"branch_coverage": True},
        "files": {"example.py": {}},
        "totals": {
            "covered_lines": 1,
            "num_statements": 2,
            "covered_branches": 0,
            "num_branches": 0,
        },
    }
    source.write_text(json.dumps(payload))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert "1 / 2" in publish(source, "coveragepy", output, source)
    assert "notApplicable" in output.read_text()
    assert "::warning::" in capsys.readouterr().out
    payload["meta"]["branch_coverage"] = False
    source.write_text(json.dumps(payload))
    assert "notMeasured" in publish(source, "coveragepy", output, None)
    assert tools["annotation"]("a%\r\nb") == "a%25%0D%0Ab"
    for key, invalid in (("num_statements", 0), ("covered_lines", True)):
        bad = payload | {"totals": payload["totals"] | {key: invalid}}
        source.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="counters"):
            publish(source, "coveragepy", output, None)
    source.write_text(json.dumps(payload | {"files": {}}))
    with pytest.raises(ValueError, match="inventory"):
        publish(source, "coveragepy", output, None)
    source.write_text(
        '<coverage lines-covered="1" lines-valid="1" branches-covered="1" '
        'branches-valid="2"><class/></coverage>'
    )
    assert "1 / 2" in publish(source, "cobertura", output, None)
    source.write_text("<notcoverage/>")
    with pytest.raises(ValueError, match="Cobertura"):
        publish(source, "cobertura", output, None)
    os.utime(source, ns=(0, 0))
    with pytest.raises(ValueError, match="predates"):
        publish(source, "cobertura", output, output)
