"""Manage Docker client contexts and the explicitly selected user service."""

import asyncio
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from setup_plugin_docker import configuration, mode, registry
from setup_plugin_docker.actions import USER_SERVICE_TRANSITIONS, DockerAction
from setup_plugin_docker.mode import Settings as Settings
from setup_plugin_docker.mode import endpoint as endpoint
from setup_plugin_docker.registry import Registry as Registry
from setup_system_sdk import (
    Inputs,
    LifecycleAction,
    Outcome,
    Plan,
    PluginSettings,
    Resource,
    Result,
)
from setup_system_sdk.apt import SERVICE_TRANSITIONS, DebianPackages, ServiceAction, apply_packages
from setup_system_sdk.native import FilesBackend, command


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
    file_actions = frozenset({LifecycleAction.CONFIGURE})
    executable = "/usr/bin/docker"
    settings_model = Settings
    package_folder = Path(__file__).parent
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        LifecycleAction.CONFIGURE: configuration.Daemon,
        DockerAction.REMOVE_OWNED_CONTEXTS: configuration.RemoveContexts,
        DockerAction.CREATE_CONTEXT: Context,
        DockerAction.START_SERVICE: Inputs,
        DockerAction.STOP_SERVICE: Inputs,
        DockerAction.LOGIN: registry.SignIn,
        DockerAction.LOGOUT: Registry,
        DockerAction.SETUP_MODE: mode.DaemonMode,
        DockerAction.PRUNE: Image,
        ServiceAction.START: Inputs,
        ServiceAction.STOP: Inputs,
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
        if action_id in SERVICE_TRANSITIONS:
            if settings.mode != "rootful":
                return Plan(outcome=Outcome.BLOCKED, summary="Select rootful daemon mode first")
            return super().prepare(action_id, settings, inputs)
        if action_id == DockerAction.CREATE_CONTEXT and isinstance(inputs, Context):
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
        if action_id in USER_SERVICE_TRANSITIONS:
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
            desired = USER_SERVICE_TRANSITIONS[DockerAction(action_id)][1]
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
        if isinstance(inputs, registry.SignIn):
            return registry.login_plan(inputs)
        if isinstance(inputs, Registry):
            if action_id == DockerAction.LOGOUT:
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
        if action_id == DockerAction.SETUP_MODE and isinstance(inputs, mode.DaemonMode):
            return mode.prepare(settings, inputs)
        if action_id == DockerAction.PRUNE and isinstance(inputs, Image):
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
            action_id == DockerAction.SETUP_MODE
            and isinstance(inputs, mode.DaemonMode)
            and isinstance(settings, Settings)
        ):
            return mode.mutate(settings, inputs, plan)
        if (
            action_id == DockerAction.CREATE_CONTEXT
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
            action_id in USER_SERVICE_TRANSITIONS
            and isinstance(settings, Settings)
            and settings.mode == "rootless"
        ):
            rc, _ = command(
                "/usr/bin/systemctl",
                "--user",
                USER_SERVICE_TRANSITIONS[DockerAction(action_id)][0],
                "docker.service",
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
        elif isinstance(inputs, registry.SignIn) and action_id == DockerAction.LOGIN:
            return registry.login(inputs, plan)
        elif isinstance(inputs, Registry) and action_id == DockerAction.LOGOUT:
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
        if provider == "mode_contexts" and isinstance(settings, Settings):
            return await asyncio.to_thread(mode.contexts, settings, query)
        if provider == "unused_images" and isinstance(settings, Settings):
            return [
                i for i in await asyncio.to_thread(unused_images, settings) if i.startswith(query)
            ][:50]
        return []

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if isinstance(inputs, mode.DaemonMode) and isinstance(settings, Settings):
            return await mode.verify(settings, inputs, plan)
        if isinstance(inputs, registry.SignIn):
            current = await asyncio.to_thread(registry.presence, inputs)
            return Result(
                outcome=Outcome.COMPLETE if current == "present" else Outcome.NEEDS_USER_ACTION,
                message="Checked local credential presence for the selected registry",
            )
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
