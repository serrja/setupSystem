"""Transitions and outcomes justified by execution evidence."""

from enum import StrEnum

from pydantic import ValidationError
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.operations import Receipt, Result
from setup_system_sdk.schemas.common import Inputs


class StepPhase(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    APPLIED = "applied"
    VERIFIED = "verified"
    UNRESOLVED = "unresolved"


SUCCESS = frozenset({Outcome.CHANGED, Outcome.NO_CHANGES, Outcome.COMPLETE, Outcome.NEEDS_REBOOT})


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

    def effect(self, phase: str) -> bool | None:
        if phase == StepPhase.PREPARED:
            return False
        observed = self.readback
        if phase != StepPhase.VERIFIED or observed is None:
            return None
        if observed.outcome is Outcome.NO_CHANGES:
            return False if all(r.before == r.after for r in observed.resources) else None
        if self.applied and self.applied.result.outcome is Outcome.NO_CHANGES:
            if all(r.before == r.after for r in observed.resources):
                return False
        if observed.outcome is Outcome.CHANGED or (
            self.applied and self.applied.result.outcome is Outcome.CHANGED
        ):
            return True
        if any(r.before != r.after for r in observed.resources):
            return True
        return None
