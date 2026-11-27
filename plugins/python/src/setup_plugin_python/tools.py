"""Python tool installer with an isolated uv environment."""

from pathlib import Path

from setup_system_toolchain.tools import Removal as Removal
from setup_system_toolchain.tools import ToolAction as ToolAction
from setup_system_toolchain.tools import Toolchain as Toolchain
from setup_system_toolchain.tools import execute as execute_tool


def execute(argv: list[str], stage: Path, *, capture: bool = False) -> str:
    return execute_tool(
        argv,
        stage,
        capture=capture,
        environment={
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
