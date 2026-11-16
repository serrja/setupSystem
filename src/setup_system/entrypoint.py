"""Private positional handoff from validated shell arguments, without a second CLI."""

from __future__ import annotations

from pathlib import Path

from setup_system.application.installer import InstallationService, InstallRequest, Operation

XDG_KINDS = ("config", "data", "state", "cache")


def source_arguments(
    operation: Operation,
    user: str,
    environment: dict[str, str],
    *,
    yes: bool,
    subscription_file: Path | None = None,
) -> list[str]:
    return [
        operation.value,
        user,
        *(environment[f"XDG_{kind.upper()}_HOME"] for kind in XDG_KINDS),
        "1" if yes else "0",
        str(subscription_file) if subscription_file is not None else "-",
    ]


def main(root: Path, arguments: list[str]) -> int:
    if len(arguments) != 8 or arguments[6] not in {"0", "1"}:
        print("Invalid internal installation handoff. Use sudo bash ./setup.sh.")
        return 2
    try:
        operation = Operation(arguments[0])
    except ValueError:
        print("Invalid internal installation operation. Use sudo bash ./setup.sh.")
        return 2
    request = InstallRequest(
        operation,
        user=arguments[1],
        yes=arguments[6] == "1",
        subscription_file=None if arguments[7] == "-" else Path(arguments[7]),
        overrides={
            kind: Path(value) for kind, value in zip(XDG_KINDS, arguments[2:6], strict=True)
        },
    )
    return InstallationService(root).run(request)
