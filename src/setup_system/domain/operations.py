"""Transitions and outcomes justified by execution evidence."""

from enum import StrEnum

from pydantic import ValidationError
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.operations import Receipt, Result
from setup_system_sdk.schemas.common import Inputs


class OperationPhase(StrEnum):
    PLANNED = "planned"
    PREPARED = "prepared"
    APPLIED = "applied"
    VERIFIED = "verified"
    UNRESOLVED = "unresolved"


class StepPhase(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    APPLIED = "applied"
    VERIFIED = "verified"
    UNRESOLVED = "unresolved"


class ChangePhase(StrEnum):
    PREPARED = "prepared"
    APPLIED = "applied"
    VERIFIED = "verified"


class RollbackPhase(StrEnum):
    PREPARED = "prepared"
    VERIFIED = "verified"
    UNRESOLVED = "unresolved"


UNFINISHED = frozenset(OperationPhase) - {OperationPhase.VERIFIED}
SUCCESS = frozenset({Outcome.CHANGED, Outcome.NO_CHANGES, Outcome.COMPLETE, Outcome.NEEDS_REBOOT})

STEP_PREDECESSORS = {
    StepPhase.RUNNING: (StepPhase.PREPARED,),
    StepPhase.APPLIED: (StepPhase.RUNNING,),
    StepPhase.VERIFIED: (StepPhase.RUNNING, StepPhase.APPLIED, StepPhase.UNRESOLVED),
    StepPhase.UNRESOLVED: (StepPhase.RUNNING, StepPhase.APPLIED, StepPhase.UNRESOLVED),
}


def interrupted(*, changed: bool, uncertain: bool) -> Outcome:
    if uncertain:
        return Outcome.UNKNOWN_OUTCOME
    return Outcome.PARTIAL if changed else Outcome.CANCELLED


class StepEvidence(Inputs):
    applied: Receipt | None = None
    readback: Result | None = None

    @classmethod
    def decode(cls, raw: str | None) -> "StepEvidence":
        if not raw:
            return cls()
        try:
            return cls.model_validate_json(raw)
        except ValidationError:
            return cls(applied=Receipt.model_validate_json(raw))

    def effect(self, phase: StepPhase) -> bool | None:
        if phase is StepPhase.PREPARED:
            return False
        observed = self.readback
        if phase is not StepPhase.VERIFIED or observed is None or self.applied is None:
            return None
        if self.applied.result.outcome is Outcome.CHANGED:
            return True
        if self.applied.result.outcome is Outcome.NO_CHANGES and all(
            r.before == r.after for r in observed.resources
        ):
            return False
        if observed.outcome is Outcome.CHANGED or any(
            r.before != r.after for r in observed.resources
        ):
            return True
        return None
