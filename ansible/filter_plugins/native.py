"""Pure input and result conversions for local Ansible plays."""

import hashlib
import json
import re
import tomllib

from ansible.errors import AnsibleFilterError

from setup_system.diagnostics import error_text
from setup_system.domain.applications import applications
from setup_system.infrastructure.upstream_asset import github_deb


def validate_request(data, section, apps, cleanup):
    from setup_system.models import BaseOptions, WorkspaceOptions

    if section not in {"base", "workspace"}:
        raise AnsibleFilterError("Unknown native operation section")
    if not isinstance(data, dict) or set(data) != {"schema", "settings", "expected"}:
        raise AnsibleFilterError("Invalid request envelope")
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise AnsibleFilterError("Unsupported request schema")
    if data["expected"] is not None and not isinstance(data["expected"], dict):
        raise AnsibleFilterError("Invalid confirmed plan")
    settings = (BaseOptions if section == "base" else WorkspaceOptions).model_validate(
        data["settings"]
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


def _package_list(section, field):
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


def package_names(settings, base, catalog):
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


def apt_closure(output, cleanup):
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


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class FilterModule:
    def filters(self):
        return dict(
            native_toml=tomllib.loads,
            native_error=error_text,
            native_request=validate_request,
            native_packages=package_names,
            native_closure=apt_closure,
            native_github_deb=github_deb,
            native_hash=canonical_hash,
        )
