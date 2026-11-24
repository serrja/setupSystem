"""Pure input and result conversions for local Ansible plays."""

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ansible.errors import AnsibleFilterError  # type: ignore[import-untyped]

from setup_system.diagnostics import error_text
from setup_system.domain.applications import applications
from setup_system.infrastructure.operation_io import read_admin_request, read_wire
from setup_system.infrastructure.upstream_asset import github_deb
from setup_system.models import BaseOptions, RequestEnvelope, TaskResult, WorkspaceOptions


def validate_request(
    data: object,
    section: str,
    apps: dict[str, object],
    cleanup: dict[str, object],
    operation: str | None = None,
) -> dict[str, Any]:
    if section not in {"base", "workspace"}:
        raise AnsibleFilterError("Unknown native operation section")
    envelope = RequestEnvelope.model_validate(data)
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
        entries = applications(apps)
        known_apps = {key for key, row in entries.items() if row.kind != "language"}
        if (
            set(result["apps"]) - known_apps
            or set(result["languages"]) - (set(apps) - known_apps)
            or set(result["cleanup"]) - set(cleanup)
        ):
            raise AnsibleFilterError("Choose only reviewed catalog entries")
    return result


def _package_list(section: object, field: str) -> list[str]:
    names = section.get(field) if isinstance(section, dict) else None
    if (
        not isinstance(names, list)
        or not names
        or any(
            not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.+-]+", name)
            for name in names
        )
        or len(names) != len(set(names))
    ):
        raise AnsibleFilterError(f"Invalid Debian package list for {field}")
    return names


def package_names(
    settings: dict[str, Any], base: dict[str, object], catalog: dict[str, dict[str, object]]
) -> list[str]:
    packages = []
    if settings.get("hyprland"):
        section = base.get("hyprland")
        stable = _package_list(section, "stable")
        backports = _package_list(section, "backports")
        if set(stable).intersection(backports):
            raise AnsibleFilterError("Conflicting stable and backports package selections")
        packages += stable
        packages += [p + "/trixie-backports" for p in backports]
    if settings.get("graphical_login"):
        packages += _package_list(base.get("login"), "stable")
    if settings.get("locale"):
        packages += ["locales"]
    if settings.get("keyboard"):
        packages += ["console-setup", "xkb-data"]
    for selected in settings.get("apps", []) + settings.get("languages", []):
        entry = catalog[selected]
        if "packages" in entry:
            packages += _package_list(entry, "packages")
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


class FilterModule:
    def filters(self) -> dict[str, Callable[..., object]]:
        return dict(
            native_result=lambda data: TaskResult.model_validate(data).model_dump(
                mode="json", exclude_unset=True
            ),
            native_same_plan=lambda expected, fresh: (
                expected is not None
                and TaskResult.model_validate(expected).same_plan(TaskResult.model_validate(fresh))
            ),
            native_apt_actions=lambda records: [
                {"kind": "apt", "record": record, "label": record} for record in records
            ],
            native_file_actions=lambda names: [
                {"kind": "user_file", "path": name, "label": name} for name in names
            ],
            native_admin_request=read_admin_request,
            native_read_request=lambda path: json.loads(read_wire(Path(path))),
            native_toml=tomllib.loads,
            native_error=error_text,
            native_request=validate_request,
            native_packages=package_names,
            native_closure=apt_closure,
            native_github_deb=github_deb,
        )
