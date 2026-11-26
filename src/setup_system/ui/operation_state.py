"""Derive controls and explanations from the currently displayed command and plan."""

from dataclasses import dataclass

from setup_system_sdk.enums.operation import OperationState


@dataclass(frozen=True)
class Availability:
    preview: bool
    review: bool
    stop: bool
    reason: str


def availability(state: OperationState, *, activated: bool, selected: bool) -> Availability:
    if state is OperationState.RUNNING:
        return Availability(
            False, False, True, "Operation running. Stop requests take effect at a safe boundary."
        )
    if state is OperationState.AUTHENTICATING:
        return Availability(False, False, False, "Review and authorization are in progress.")
    if not activated:
        return Availability(False, False, False, "Activate the extension to preview an action.")
    if not selected:
        return Availability(False, False, False, "Choose an action to preview.")
    if state in (OperationState.PLANNING, OperationState.VALIDATING):
        return Availability(False, False, False, "Preparing a plan for the current values.")
    if state is OperationState.READY:
        return Availability(True, True, False, "The plan is ready for review.")
    messages = {
        OperationState.BLOCKED: "Review the reported requirement and preview again.",
        OperationState.FAILED: "Check the reported error and preview again.",
        OperationState.CANCELLED: "Preview again to review a new operation.",
        OperationState.SUCCEEDED: "Preview again to review a new operation.",
    }
    return Availability(
        True, False, False, messages.get(state, "Preview the current values to enable review.")
    )
