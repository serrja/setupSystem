"""Private root-to-user handoff for worker phases; no public installation parser."""

from __future__ import annotations

from pathlib import Path

from setup_system.application.installer import InstallationService, InstallRequest, Operation

XDG_KINDS = ("config", "data", "state", "cache")


def worker_arguments(
    operation: Operation,
    user: str,
    environment: dict[str, str],
    *,
    subscription_file: Path | None = None,
) -> list[str]:
    return [
        operation.value,
        user,
        *(environment[f"XDG_{kind.upper()}_HOME"] for kind in XDG_KINDS),
        str(subscription_file) if subscription_file is not None else "-",
    ]


def main(root: Path, arguments: list[str]) -> int:
    if len(arguments) != 7:
        print("Invalid internal installation handoff. Use setup-system install apply.")
        return 2
    try:
        operation = Operation(arguments[0])
    except ValueError:
        print("Invalid internal installation operation. Use setup-system install apply.")
        return 2
    if operation not in {Operation.NETWORK_STAGE, Operation.PREPARE_USER, Operation.USER_STAGE}:
        print("Only private user phases are accepted by this worker.")
        return 2
    request = InstallRequest(
        operation,
        user=arguments[1],
        yes=True,
        subscription_file=None if arguments[6] == "-" else Path(arguments[6]),
        overrides={
            kind: Path(value) for kind, value in zip(XDG_KINDS, arguments[2:6], strict=True)
        },
    )
    return InstallationService(root).run(request)
