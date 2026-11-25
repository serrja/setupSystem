"""Read the current account's picker-visible models over the official stdio API."""

import asyncio
import json
import os
from pathlib import Path

from pydantic import BaseModel, Field


class Effort(BaseModel):
    reasoning_effort: str = Field(max_length=64, alias="reasoningEffort")


class Model(BaseModel):
    model: str = Field(max_length=160)
    efforts: list[Effort] = Field(max_length=32, alias="supportedReasoningEfforts")


class Page(BaseModel):
    data: list[Model] = Field(max_length=100)


async def models(executable: Path) -> dict[str, list[str]]:
    process = await asyncio.create_subprocess_exec(
        str(executable),
        "app-server",
        "--listen",
        "stdio://",
        cwd="/",
        env={
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin",
            **(
                {"XDG_DATA_HOME": os.environ["XDG_DATA_HOME"]}
                if "XDG_DATA_HOME" in os.environ
                else {}
            ),
        },
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=262144,
    )
    assert process.stdin is not None and process.stdout is not None
    reader = process.stdout

    async def reply(number: int) -> dict[str, list[str]]:
        for _ in range(64):
            line = await reader.readline()
            if not line:
                break
            data = json.loads(line)
            if data.get("id") == number:
                if "error" in data:
                    raise ValueError("Codex capability query unavailable")
                if number == 1:
                    return {}
                page = Page.model_validate(data["result"])
                return {m.model: [e.reasoning_effort for e in m.efforts] for m in page.data}
        raise ValueError("Incomplete Codex capability reply")

    try:
        async with asyncio.timeout(10):
            process.stdin.write(
                b'{"id":1,"method":"initialize","params":{"clientInfo":{"name":"setup-system","version":"1"}}}\n'
            )
            await process.stdin.drain()
            await reply(1)
            process.stdin.write(
                b'{"method":"initialized","params":{}}\n{"id":2,"method":"model/list","params":{"limit":100,"includeHidden":false}}\n'
            )
            await process.stdin.drain()
            return await reply(2)
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 1)
        except TimeoutError:
            process.kill()
            await process.wait()
