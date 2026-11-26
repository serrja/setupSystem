"""Read the current account's picker-visible models over the official stdio API."""

import json
import subprocess
from pathlib import Path

import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
from pydantic import BaseModel, Field
from setup_system_sdk.environment import session_environment
from setup_system_sdk.processes import ProcessSpec, current_processes


class Effort(BaseModel):
    reasoning_effort: str = Field(max_length=64, alias="reasoningEffort")


class Model(BaseModel):
    model: str = Field(max_length=160)
    efforts: list[Effort] = Field(max_length=32, alias="supportedReasoningEfforts")


class Page(BaseModel):
    data: list[Model] = Field(max_length=100)


async def models(executable: Path) -> dict[str, list[str]]:
    service = current_processes()
    spec = ProcessSpec(
        (str(executable), "app-server", "--listen", "stdio://"),
        cwd=Path("/"),
        env=session_environment(),
        stdin=subprocess.PIPE,
        discard_stderr=True,
        timeout=service.timeout(10),
        grace=service.grace,
    )

    async def reply(reader: BufferedByteReceiveStream, number: int) -> dict[str, list[str]]:
        for _ in range(64):
            line = await reader.receive_until(b"\n", 262144)
            data = json.loads(line)
            if data.get("id") == number:
                if "error" in data:
                    raise ValueError("Codex capability query unavailable")
                if number == 1:
                    return {}
                page = Page.model_validate(data["result"])
                return {m.model: [e.reasoning_effort for e in m.efforts] for m in page.data}
        raise ValueError("Incomplete Codex capability reply")

    async with service.runner.open(spec) as process:
        assert process.stdin is not None and process.stdout is not None
        reader = BufferedByteReceiveStream(process.stdout)
        await process.stdin.send(
            b'{"id":1,"method":"initialize","params":{"clientInfo":{"name":"setup-system","version":"1"}}}\n'
        )
        await reply(reader, 1)
        await process.stdin.send(
            b'{"method":"initialized","params":{}}\n{"id":2,"method":"model/list","params":{"limit":100,"includeHidden":false}}\n'
        )
        result = await reply(reader, 2)
        await process.stdin.aclose()
        with anyio.move_on_after(service.grace):
            await process.process.wait()
        return result
