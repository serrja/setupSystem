"""Keep every shipped Python program visible to static checks and coverage."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_production_python_is_discoverable_by_native_checks() -> None:
    tools = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]
    typed = [ROOT / entry for entry in tools["mypy"]["files"]]
    coverage = [
        ROOT / ("src/setup_system" if entry == "setup_system" else entry)
        for entry in tools["coverage"]["run"]["source"]
    ]
    provider = {"docker/python_release.py", "docker/release.py"}
    for directory in ("src", "ansible", "assets", "debian", "docker", "libexec"):
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            with path.open("rb") as stream:
                first = stream.readline(256)
            if first.startswith(b"#!") and b"python" in first:
                assert path.suffix == ".py", path
            if path.suffix != ".py" or str(path.relative_to(ROOT)) in provider:
                continue
            assert any(path.is_relative_to(scope) for scope in typed), path
            assert any(path.is_relative_to(scope) for scope in coverage), path
