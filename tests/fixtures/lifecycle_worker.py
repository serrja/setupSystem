"""An external wheel fixture with a real executable, user profile and removable files."""

import asyncio
import hashlib
import sys
from pathlib import Path

from setup_system_sdk import (
    Backend,
    Inputs,
    Mutation,
    Outcome,
    Plan,
    PluginSettings,
    Resource,
    Result,
    owned_resources,
    ownership_context,
)


class Preferences(PluginSettings):
    title: str = "Example"


class Example(Backend):
    settings_model = Preferences
    inputs = dict.fromkeys(("install", "configure", "record", "uninstall"), Inputs)

    @staticmethod
    def files(settings):
        return {
            "install": ("fixture-program", b"#!/bin/sh\nprintf 'fixture 1.0\\n'\n", 0o700),
            "configure": ("fixture-profile", settings.title.encode(), 0o600),
            "record": ("fixture-record", b"checked", 0o600),
        }

    async def plan(self, action_id, settings, inputs):
        files = self.files(settings)
        holdings = {r.identity: r for r in owned_resources()}
        resources = []
        for action, (name, data, mode) in files.items():
            if action_id != "uninstall" and action_id != action:
                continue
            path = Path.home() / name
            before = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            after = None if action_id == "uninstall" else hashlib.sha256(data).hexdigest()
            if before == after:
                continue
            if before and (str(path) not in holdings or holdings[str(path)].after != before):
                return Plan(summary="Review local changes", outcome=Outcome.BLOCKED)
            resources.append(
                Resource(
                    kind="file",
                    identity=str(path),
                    before=before,
                    after=after,
                    ownership="created",
                    permissions=mode,
                    cleanup=True,
                    effect="remove" if after is None else "ensure",
                )
            )
        return Plan(
            summary="Apply the fixture action",
            resources=resources,
            outcome=Outcome.READY if resources else Outcome.NO_CHANGES,
        )

    async def verify(self, plan, settings, inputs):
        for row in plan.resources:
            path = Path(row.identity)
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if digest != row.after or (digest and path.stat().st_mode & 0o777 != row.permissions):
                return Result(outcome=Outcome.PARTIAL, message="State differs")
        return Result(outcome=Outcome.COMPLETE, message="Verified", resources=plan.resources)

    async def apply(self, request):
        settings = Preferences.model_validate(request.settings)
        with ownership_context(request.holdings):
            expected = await self.plan(
                request.action_id, settings, Inputs.model_validate(request.inputs)
            )
            if request.plan != expected or expected.outcome != Outcome.READY:
                return Result(outcome=Outcome.BLOCKED, message="Stale selection")
            for row in expected.resources:
                path = Path(row.identity)
                if row.effect == "remove":
                    path.unlink()
                else:
                    _, data, mode = self.files(settings)[request.action_id]
                    path.write_bytes(data)
                    path.chmod(mode)
            return Result(outcome=Outcome.CHANGED, message="Changed", resources=expected.resources)


if __name__ == "__main__":
    mutation = Mutation.model_validate_json(sys.stdin.buffer.read())
    if sys.argv[1:] != [mutation.action_id]:
        raise SystemExit(2)
    print(asyncio.run(Example().apply(mutation)).model_dump_json())
