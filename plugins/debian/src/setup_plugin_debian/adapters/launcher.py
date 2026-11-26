"""Revalidate one installed handler and authenticate on a private terminal."""

import fcntl
import hashlib
import importlib.util
import os
import stat
import sys
from pathlib import Path

from pydantic import Field
from setup_system_sdk.backend import Backend, ownership_context
from setup_system_sdk.enums.operation import Outcome, WorkerMethod
from setup_system_sdk.files.manifest_types import Digest
from setup_system_sdk.files.system_paths import protected
from setup_system_sdk.operations import Plan, Receipt, Result, Snapshot, StepPlan
from setup_system_sdk.schemas.common import ActionId, Identifier, Inputs
from setup_system_sdk.schemas.wire import LIMIT, Request

from setup_plugin_debian.adapters import apt
from setup_plugin_debian.adapters.authorization import authorize
from setup_plugin_debian.adapters.layout import (
    INSTALLED,
    require_installed_resource,
    verify_installation,
)
from setup_plugin_debian.models import PackageRequest
from setup_plugin_debian.providers import proposal


class ExecutionRecord(Inputs):
    plugin_id: Identifier
    version: str
    release_digest: Digest
    launcher: Path
    launcher_sha256: Digest
    allowed_handlers: frozenset[ActionId]
    resources: dict[Path, Digest] = Field(max_length=4096)


def confirmed(raw: bytes) -> tuple[Snapshot, StepPlan]:
    req = Request.model_validate_json(raw)
    snap = req.snapshot
    if (
        req.method is not WorkerMethod.EXECUTE
        or snap is None
        or snap.operation_id != req.operation_id
    ):
        raise ValueError("Expected an approved operation")
    (step,) = [
        s for s in snap.plan.steps if s.step_key == req.step_key and s.executor == req.executor
    ]
    path = Path("/etc/setup-system/executors") / (step.executor.plugin_id + ".json")
    protected(path, directory=False)
    record = ExecutionRecord.model_validate_json(path.read_bytes())
    if (record.plugin_id, record.version, record.release_digest) != (
        step.executor.plugin_id,
        step.executor.version,
        step.executor.release_digest,
    ) or step.handler not in record.allowed_handlers:
        raise ValueError("Executor differs from its installed record")
    verify_installation(INSTALLED)
    for resource, expected in [
        (record.launcher, record.launcher_sha256),
        *record.resources.items(),
    ]:
        protected(resource, directory=False)
        with resource.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("Installed executor resource changed")
    if not step.capability and (snap.executor != step.executor or snap.action_id != step.handler):
        raise ValueError("Unexpected local native executor")
    return snap, step


def perform(raw: bytes) -> None:
    snap, step = confirmed(raw)
    plan = Plan.model_validate(step.preconditions["proposal"])
    if step.resources != tuple(plan.resources) or plan.outcome not in {
        Outcome.READY,
        Outcome.NO_CHANGES,
    }:
        raise ValueError("Native step exceeds its confirmation")
    with ownership_context(snap.holdings):
        if step.capability == "system.package-cleanup":
            from setup_plugin_debian.cleanup_provider import cleanup_request, run_cleanup

            if (
                step.executor.plugin_id != "debian"
                or step.handler != "package-cleanup"
                or snap.executor.plugin_id != "cleanup"
            ):
                raise ValueError("Unapproved cleanup executor")
            run_cleanup(cleanup_request(step.payload), plan)
        elif step.capability:
            if (
                step.executor.plugin_id != "debian"
                or step.handler != "packages"
                or step.capability != "system.packages"
            ):
                raise ValueError("Unrecognized installed provider handler")
            params = PackageRequest.model_validate(step.payload)
            if (
                params.consumer != snap.executor.plugin_id
                or params.evidence is not None
                or params.inventory
                or proposal(params) != plan
            ):
                raise ValueError("Package preconditions changed")
            if plan.outcome == Outcome.READY:
                apt.apply_packages(params.consumer, params.action, plan)
        else:
            plugin = step.executor.plugin_id
            package = "setup_plugin_" + plugin.replace("-", "_")
            relative = f"plugins/{plugin}/{package}/__init__.py"
            source = INSTALLED / relative
            require_installed_resource(source, relative)
            spec = importlib.util.spec_from_file_location(
                package, source, submodule_search_locations=[str(source.parent)]
            )
            if not spec or not spec.loader:
                raise ValueError("Installed implementation unavailable")
            module = importlib.util.module_from_spec(spec)
            sys.modules[package] = module
            spec.loader.exec_module(module)
            backend = module.Plugin()
            if not isinstance(backend, Backend):
                raise ValueError("Invalid installed backend")
            implementation = importlib.import_module(package + ".plugin")
            if hasattr(implementation, "apply_native_step"):
                implementation.apply_native_step(snap, step)
                return
            command = backend.command(step.handler)
            settings = backend.settings_model.model_validate(snap.settings)
            inputs = command.validate(snap.inputs)
            import anyio

            async def validate() -> None:
                if await command.plan(settings, inputs, None) != plan:
                    raise ValueError("Confirmed native facts changed")

            anyio.run(validate, backend="asyncio")
            if plan.outcome == Outcome.READY:
                implementation = importlib.import_module(package + ".plugin")
                implementation.apply_native(step.handler, settings, inputs, plan)


def launch(raw: bytes, response: bytes) -> Receipt:
    snap, step = confirmed(raw)
    authorize(raw, response)
    return Receipt(
        operation_id=snap.operation_id,
        step_key=step.step_key,
        executor=step.executor,
        result=Result(
            outcome=Outcome.CHANGED,
            message="Native handler returned",
            resources=list(step.resources),
        ),
    )


def main() -> None:
    if os.geteuid() == 0:
        raise ValueError("Start the installed launcher as the application user")
    lock = Path("/run/setup-system")
    protected(lock, directory=True)
    descriptor = os.open(lock, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        dispatch()
    finally:
        os.close(descriptor)


def dispatch() -> None:
    match sys.argv[1:]:
        case ["--authorization-fd", descriptor]:
            with os.fdopen(int(descriptor), "rb", buffering=0) as secret:
                response = secret.read(1025)
            raw = sys.stdin.buffer.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError("Oversized native request")
            print(launch(raw, response).model_dump_json())
        case ["--perform", path, digest]:
            with os.fdopen(
                os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
            ) as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Invalid request file")
                raw = stream.read(LIMIT + 1)
            if len(raw) > LIMIT or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("Native request changed")
            perform(raw)
        case _:
            raise ValueError("Unknown launcher invocation")


if __name__ == "__main__":
    main()
