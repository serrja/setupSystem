"""Freeze an APT simulation to an exact independently checked dependency closure."""

from __future__ import annotations

import re


def validate_simulation(output: str) -> None:
    if re.search(r"(?mi)^Remv\s|\bDOWNGRADED\b|\bwill be REMOVED\b", output):
        raise RuntimeError("APT proposed removal/downgrade; no package changes authorized")


def freeze_selection(
    arguments: list[str],
    output: str,
    happ_version: str,
    local_versions: dict[str, str] | None = None,
) -> list[str]:
    """Retain explicit requests and constrain the simulated dependency closure exactly."""
    selected: dict[str, str] = {}
    for line in output.splitlines():
        if not line.startswith("Inst "):
            continue
        match = re.match(r"Inst (\S+)(?: \[[^]]+\])? \((\S+) ", line)
        if (
            match is None
            or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?", match[1])
            or not re.fullmatch(r"[A-Za-z0-9.+:~_-]+", match[2])
        ):
            raise RuntimeError("Cannot freeze an unrecognized APT simulation entry")
        name, version = match[1], match[2]
        if name in selected and selected[name] != version:
            raise RuntimeError("APT simulation selected conflicting package versions")
        if name.split(":", 1)[0] == "happ":
            if version != happ_version:
                raise RuntimeError("APT simulation selected an unexpected Happ version")
            continue
        local = (local_versions or {}).get(name.split(":", 1)[0])
        if local is not None:
            if version != local:
                raise RuntimeError("APT selected a different local artifact version")
            continue
        selected[name] = version
    frozen: list[str] = []
    for argument in arguments:
        if argument.startswith("/"):
            frozen.append(argument)
            continue
        name, separator, pinned = argument.partition("=")
        choices = [key for key in selected if key == name or key.split(":", 1)[0] == name]
        if len(choices) == 1:
            version = selected.pop(choices[0])
            if separator and pinned != version:
                raise RuntimeError("APT simulation conflicts with an exact package pin")
            frozen.append(f"{name}={version}")
        else:
            frozen.append(argument)
    return [*frozen, *(f"{name}={version}" for name, version in selected.items())]
