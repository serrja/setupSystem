"""Merge selected rootless daemon values without reading Docker client credentials."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, TypeAdapter

from setup_system.infrastructure.user_storage import capture_receipt, inside_home
from setup_system_sdk import Inputs, Outcome, Plan, Resource, Result, owned_resources
from setup_system_sdk.native import command


class Daemon(Inputs):
    driver: Literal["local", "json-file"] = Field(default="local", title="Container log driver")
    log_size_mib: int = Field(
        default=10,
        ge=1,
        le=1024,
        title="Maximum log file size",
        json_schema_extra={"ui": {"units": "MiB"}},
    )
    log_files: int = Field(default=3, ge=1, le=64, title="Number of rotated log files")


def daemon_file(inputs: Daemon) -> tuple[Path, bytes]:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = inside_home(root / "docker/daemon.json")
    typ = TypeAdapter(dict[str, JsonValue])
    data = typ.validate_json(capture_receipt(path).data) if path.exists() else {}
    options = data.setdefault("log-opts", {})
    if not isinstance(options, dict):
        raise ValueError("Docker daemon log options require manual review")
    data["log-driver"] = inputs.driver
    options.update({"max-size": f"{inputs.log_size_mib}m", "max-file": str(inputs.log_files)})
    return path, typ.dump_json(data, indent=2) + b"\n"


class RemoveContexts(Inputs):
    """Remove only unchanged, previously created local client contexts."""


def context_cleanup(inspect_host: Callable[[str], str | None]) -> Plan:
    rows = []
    for r in sorted(owned_resources(), key=lambda r: r.identity):
        if r.kind != "docker-context" or not r.cleanup or r.ownership != "created":
            continue
        # The ledger records the same closed input format used by the creation action.
        if not r.after or inspect_host(r.identity) != r.after:
            return Plan(outcome=Outcome.BLOCKED, summary="Reconcile the edited or missing context")
        rc, current = command("/usr/bin/docker", "context", "show", capture=True)
        if rc or current.strip() == r.identity or r.identity == "default":
            return Plan(
                outcome=Outcome.BLOCKED, summary="Select a different active Docker context first"
            )
        rows.append(
            Resource(
                kind=r.kind,
                identity=r.identity,
                ownership="created",
                before=r.after,
                cleanup=True,
                effect="remove",
            )
        )
    return Plan(
        outcome=Outcome.READY if rows else Outcome.NO_CHANGES,
        summary="Remove the listed inactive client contexts",
        resources=rows,
    )


def remove_contexts(plan: Plan, inspect_host: Callable[[str], str | None]) -> Result:
    done: list[Resource] = []
    for r in plan.resources:
        current = inspect_host(r.identity)
        if current != r.before:
            return Result(outcome=Outcome.PARTIAL, message="Docker context changed", resources=done)
        rc, _ = command("/usr/bin/docker", "context", "rm", r.identity)
        if rc or inspect_host(r.identity) is not None:
            return Result(
                outcome=Outcome.PARTIAL, message="Check the remaining contexts", resources=done
            )
        done.append(r.model_copy(update={"verified": True}))
    return Result(
        outcome=Outcome.CHANGED, message="Owned context removals verified", resources=done
    )
