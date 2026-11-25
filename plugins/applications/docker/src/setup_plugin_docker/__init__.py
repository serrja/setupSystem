"""Manage Docker client contexts and the explicitly selected user service."""

import asyncio
import os
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from setup_plugin_docker import configuration, registry
from setup_plugin_docker.registry import Registry as Registry
from setup_system_sdk import Inputs, Outcome, Plan, PluginSettings, Resource, Result
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.native import FilesBackend, command


class Settings(PluginSettings):
    mode: Literal["rootless", "rootful"] = Field(
        default="rootless",
        title="Daemon mode",
        description=(
            "Rootful socket access grants root-equivalent control. Group membership is never "
            "changed."
        ),
    )


class Context(Inputs):
    name: str = Field(
        min_length=1, max_length=64, pattern="^[a-z][a-z0-9_-]*$", title="New context name"
    )


class Image(Inputs):
    digest: str = Field(
        pattern=r"^sha256:[a-f0-9]{64}$",
        title="Unused image digest",
        json_schema_extra={"ui": {"choice_provider": "unused_images"}},
    )


def unused_images(settings: Settings) -> list[str]:
    rc, images = command(
        "/usr/bin/docker",
        "--host",
        endpoint(settings),
        "image",
        "ls",
        "--filter",
        "dangling=true",
        "--no-trunc",
        "--quiet",
        capture=True,
    )
    if rc:
        raise ValueError("Cannot inspect unused images on the selected Docker daemon")
    return sorted(set(images.splitlines()))


def endpoint(settings: Settings) -> str:
    return (
        f"unix:///run/user/{os.getuid()}/docker.sock"
        if settings.mode == "rootless"
        else "unix:///var/run/docker.sock"
    )


def context_host(name: str) -> str | None:
    rc, names = command("/usr/bin/docker", "context", "ls", "--format", "{{.Name}}", capture=True)
    if rc:
        raise ValueError("Docker could not enumerate client contexts")
    if name not in names.splitlines():
        return None
    rc, host = command(
        "/usr/bin/docker",
        "context",
        "inspect",
        name,
        "--format",
        "{{.Endpoints.docker.Host}}",
        capture=True,
    )
    if rc:
        raise ValueError("Docker context changed during inspection")
    return host.strip()


class Plugin(DebianPackages, FilesBackend):
    file_actions = frozenset({"configure"})
    executable = "/usr/bin/docker"
    settings_model = Settings
    package_folder = Path(__file__).parent
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        "configure": configuration.Daemon,
        "remove-owned-contexts": configuration.RemoveContexts,
        "create-context": Context,
        "start-service": Inputs,
        "stop-service": Inputs,
        "login": Registry,
        "logout": Registry,
        "setup-mode": Inputs,
        "prune": Image,
        "start-system-service": Inputs,
        "stop-system-service": Inputs,
    }

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(inputs, configuration.Daemon):
            raise ValueError("Unexpected daemon configuration")
        return dict([configuration.daemon_file(inputs)])

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if not isinstance(settings, Settings):
            raise ValueError("Unexpected Docker settings")
        if isinstance(inputs, configuration.Daemon):
            if settings.mode != "rootless":
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Choose rootless mode for the user daemon settings",
                )
            plan = super().prepare(action_id, settings, inputs)
            return plan.model_copy(
                update={
                    "warnings": ["Restart separately to apply logging options to new containers."]
                }
            )
        if isinstance(inputs, configuration.RemoveContexts):
            return configuration.context_cleanup(context_host)
        if action_id in {"start-system-service", "stop-system-service"}:
            if settings.mode != "rootful":
                return Plan(outcome=Outcome.BLOCKED, summary="Select rootful daemon mode first")
            return super().prepare(action_id, settings, inputs)
        if action_id == "create-context" and isinstance(inputs, Context):
            host = context_host(inputs.name)
            desired = endpoint(settings)
            if host and host != desired:
                return Plan(
                    outcome=Outcome.BLOCKED, summary="An existing context uses a different endpoint"
                )
            return Plan(
                summary=f"Register context {inputs.name} for {desired}",
                outcome=Outcome.NO_CHANGES if host else Outcome.READY,
                resources=[
                    Resource(
                        kind="docker-context",
                        identity=inputs.name,
                        ownership="preexisting" if host else "created",
                        before=host,
                        after=desired,
                        cleanup=host is None,
                    )
                ],
            )
        if action_id in {"start-service", "stop-service"}:
            if settings.mode == "rootful":
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Choose the system service action for rootful Docker",
                )
            rc, state = command(
                "/usr/bin/systemctl",
                "--user",
                "show",
                "docker.service",
                "--property=LoadState,ActiveState",
                capture=True,
            )
            if rc or "LoadState=loaded" not in state.splitlines():
                return Plan(
                    outcome=Outcome.BLOCKED, summary="A loaded rootless user service is required"
                )
            active = next(
                (v.partition("=")[2] for v in state.splitlines() if v.startswith("ActiveState=")),
                "",
            )
            if active not in {"active", "inactive"}:
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Wait for the current Docker service transition",
                )
            desired = "active" if action_id == "start-service" else "inactive"
            return Plan(
                outcome=Outcome.NO_CHANGES if active == desired else Outcome.READY,
                summary=f"Set the rootless Docker user service to {desired}",
                resources=[
                    Resource(
                        kind="service",
                        identity="user/docker.service",
                        ownership="modified",
                        before=active,
                        after=desired,
                    )
                ],
                warnings=["Starting or stopping this daemon affects its workloads."],
            )
        if isinstance(inputs, Registry):
            if action_id == "logout":
                state = registry.presence(inputs)
                if state != "manual":
                    return Plan(
                        outcome=Outcome.READY if state == "present" else Outcome.NO_CHANGES,
                        summary=f"Sign out of {inputs.host} in the local Docker credential store",
                        warnings=["Remote tokens can remain valid on other devices."],
                    )
            return Plan(
                outcome=Outcome.NEEDS_USER_ACTION,
                summary=(
                    f"In a terminal run docker {action_id} {inputs.host}. "
                    "Use a configured credential helper before login. Check the registry "
                    "account for remote tokens."
                ),
            )
        if action_id == "setup-mode":
            return Plan(
                outcome=Outcome.NEEDS_USER_ACTION,
                summary=(
                    "Verify subuid, subgid and newuidmap prerequisites, then run "
                    "dockerd-rootless-setuptool.sh install as your user."
                    if settings.mode == "rootless"
                    else (
                        "Review the rootful docker.service with your administrator before "
                        "starting it. Group membership stays unchanged."
                    )
                ),
            )
        if action_id == "prune" and isinstance(inputs, Image):
            if inputs.digest not in unused_images(settings):
                return Plan(outcome=Outcome.BLOCKED, summary="The image is no longer dangling")
            rc, containers = command(
                "/usr/bin/docker",
                "--host",
                endpoint(settings),
                "ps",
                "--all",
                "--filter",
                "ancestor=" + inputs.digest,
                "--format",
                "{{.ID}}",
                capture=True,
            )
            if rc or containers.strip():
                return Plan(outcome=Outcome.BLOCKED, summary="A container may require this image")
            return Plan(
                summary=f"Remove only image {inputs.digest} from {endpoint(settings)}",
                warnings=["This deletion is irreversible. Volumes are preserved."],
                resources=[
                    Resource(
                        kind="docker-image",
                        identity=endpoint(settings) + "/" + inputs.digest,
                        ownership="preexisting",
                        effect="remove",
                        before=inputs.digest,
                    )
                ],
            )
        return super().prepare(action_id, settings, inputs)

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if (
            action_id == "create-context"
            and isinstance(inputs, Context)
            and isinstance(settings, Settings)
        ):
            rc, _ = command(
                "/usr/bin/docker",
                "context",
                "create",
                inputs.name,
                "--docker",
                "host=" + endpoint(settings),
            )
        elif (
            action_id in {"start-service", "stop-service"}
            and isinstance(settings, Settings)
            and settings.mode == "rootless"
        ):
            rc, _ = command(
                "/usr/bin/systemctl", "--user", action_id.split("-")[0], "docker.service"
            )
        elif isinstance(inputs, Image) and isinstance(settings, Settings):
            rc, _ = command(
                "/usr/bin/docker",
                "--host",
                endpoint(settings),
                "image",
                "rm",
                "--no-prune",
                inputs.digest,
            )
        elif isinstance(inputs, Registry) and action_id == "logout":
            rc, _ = command("/usr/bin/docker", "logout", inputs.host)
        elif isinstance(inputs, configuration.RemoveContexts):
            return configuration.remove_contexts(plan, context_host)
        elif isinstance(inputs, configuration.Daemon):
            return super().mutate(action_id, settings, inputs, plan)
        else:
            raise ValueError("Unexpected Docker action")
        return Result(
            outcome=Outcome.UNKNOWN_OUTCOME if rc else Outcome.CHANGED,
            message="Check Docker state" if rc else "Docker action submitted",
            resources=plan.resources,
        )

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        if provider == "unused_images" and isinstance(settings, Settings):
            return [
                i for i in await asyncio.to_thread(unused_images, settings) if i.startswith(query)
            ][:50]
        return []

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if isinstance(inputs, Registry):
            current = await asyncio.to_thread(registry.presence, inputs)
            return Result(
                outcome=Outcome.COMPLETE if current == "absent" else Outcome.PARTIAL,
                message="Checked the selected registry's local credential helper",
            )
        if isinstance(inputs, configuration.RemoveContexts):
            states = await asyncio.to_thread(
                lambda: [context_host(r.identity) for r in plan.resources]
            )
            return Result(
                outcome=Outcome.COMPLETE if not any(states) else Outcome.PARTIAL,
                message="Inspected the selected client contexts",
                resources=plan.resources,
            )
        row = plan.resources[0]
        if row.kind == "docker-context" and isinstance(inputs, Context):
            ok = await asyncio.to_thread(context_host, inputs.name) == row.after
        elif isinstance(inputs, Image) and isinstance(settings, Settings):
            rc, images = await asyncio.to_thread(
                command,
                "/usr/bin/docker",
                "--host",
                endpoint(settings),
                "image",
                "ls",
                "--all",
                "--no-trunc",
                "--quiet",
                capture=True,
            )
            ok = rc == 0 and inputs.digest not in images.splitlines()
        elif row.identity == "system/docker.service":
            return await super().verify(plan, settings, inputs)
        elif row.kind == "service":
            rc, actual = await asyncio.to_thread(
                command,
                "/usr/bin/systemctl",
                "--user",
                "show",
                "docker.service",
                "--property=ActiveState",
                "--value",
                capture=True,
            )
            ok = rc == 0 and actual.strip() == row.after
        else:
            return await super().verify(plan, settings, inputs)
        return Result(
            outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
            message="Docker state verified" if ok else "Docker state differs",
            resources=plan.resources,
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("docker", action_id, plan)
