"""Validate native requests and results from local Ansible plays.

The plays use Ansible's to_json filter to evaluate nested template values before
Pydantic validates their wire types.
"""

import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ansible.errors import AnsibleFilterError  # type: ignore[import-untyped]
from pydantic import JsonValue, TypeAdapter, ValidationError

from setup_system.diagnostics import error_text
from setup_system.domain.applications import BasePackages, applications
from setup_system.infrastructure.operation_io import read_admin_request, read_wire
from setup_system.infrastructure.upstream_asset import github_deb
from setup_system.models import BaseOptions, RequestEnvelope, TaskResult, WorkspaceOptions


def validate_request(
    data: str,
    section: str,
    apps: str,
    cleanup: str,
    operation: str | None = None,
) -> dict[str, JsonValue]:
    if section not in {"base", "workspace"}:
        raise AnsibleFilterError("Unknown native operation section")
    envelope = RequestEnvelope.model_validate_json(data)
    if operation is not None:
        target = (
            operation
            if operation in {"cleanup-plan", "cleanup-apply"}
            else f"{section}-{operation}"
        )
        envelope.for_target(target)
    settings = (BaseOptions if section == "base" else WorkspaceOptions).model_validate(
        envelope.settings
    )
    result = settings.model_dump(mode="json")
    if section == "workspace":
        entries = applications(json.loads(apps))
        known_apps = {key for key, row in entries.items() if row.kind != "language"}
        if (
            set(result["apps"]) - known_apps
            or set(result["languages"]) - (set(entries) - known_apps)
            or set(result["cleanup"])
            - set(TypeAdapter(dict[str, JsonValue]).validate_json(cleanup))
        ):
            raise AnsibleFilterError("Choose only reviewed catalog entries")
    return result


def package_names(settings: str, base: str, catalog: str) -> list[str]:
    intent = TypeAdapter(BaseOptions | WorkspaceOptions).validate_json(settings)
    packages: list[str] = []
    if isinstance(intent, BaseOptions):
        try:
            sources = BasePackages.model_validate_json(base)
        except ValidationError as exc:
            raise AnsibleFilterError("Invalid Debian package catalog") from exc
        packages.extend(sources.selected(intent))
    else:
        try:
            apps = applications(json.loads(catalog))
        except ValidationError as exc:
            raise AnsibleFilterError("Invalid application package catalog") from exc
        for name in (*intent.apps, *intent.languages):
            packages.extend(apps[name].packages)
    return sorted(set(packages))


def apt_closure(output: str, cleanup: bool) -> list[str]:
    if "The following packages will be DOWNGRADED:" in output:
        raise AnsibleFilterError("The native APT simulation would downgrade an installed package")
    lines = []
    for line in output.splitlines():
        if line.startswith(("Remv ", "Purg ", "Conf ", "Inst ")):
            if (not cleanup and line.startswith(("Remv ", "Purg "))) or (
                cleanup and line.startswith(("Inst ", "Conf "))
            ):
                raise AnsibleFilterError("APT proposes an unexpected install or removal")
            lines.append(line)
    return lines


def native_result(data: str) -> dict[str, JsonValue]:
    return TaskResult.model_validate_json(data).model_dump(mode="json", exclude_unset=True)


def same_plan(expected: str, fresh: str) -> bool:
    plan = TypeAdapter(TaskResult | None).validate_json(expected)
    return plan is not None and plan.same_plan(TaskResult.model_validate_json(fresh))


def release_asset(release: str, application: str, arch: str) -> dict[str, str]:
    return github_deb(json.loads(release), json.loads(application), arch)


def apt_actions(records: list[str]) -> list[dict[str, str]]:
    return [{"kind": "apt", "record": record, "label": record} for record in records]


def file_actions(names: list[str]) -> list[dict[str, str]]:
    return [{"kind": "user_file", "path": name, "label": name} for name in names]


def read_request(path: str) -> dict[str, JsonValue]:
    return RequestEnvelope.model_validate_json(read_wire(Path(path))).model_dump(
        mode="json", by_alias=True, exclude_unset=True
    )


class FilterModule:
    def filters(self) -> dict[str, Callable[..., Any]]:
        return dict(
            native_result=native_result,
            native_same_plan=same_plan,
            native_apt_actions=apt_actions,
            native_file_actions=file_actions,
            native_admin_request=read_admin_request,
            native_read_request=read_request,
            native_toml=tomllib.loads,
            native_error=error_text,
            native_request=validate_request,
            native_packages=package_names,
            native_closure=apt_closure,
            native_github_deb=release_asset,
        )
