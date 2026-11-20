"""Immutable plan snapshots and safe cleanup policy without operating-system I/O."""

from dataclasses import dataclass
from enum import StrEnum

from setup_system.domain.settings import Section, SystemSettings, UserSettings


class Outcome(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Action:
    id: str
    description: str
    privileged: bool = False
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkstationPlan:
    section: Section
    system: SystemSettings
    user: UserSettings
    actions: tuple[Action, ...]
    observations: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    system_revision: str | None = None

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(reason for action in self.actions for reason in action.blockers)

    def describe(self) -> tuple[str, ...]:
        return (
            *self.observations,
            *(f"{action.id}: {action.description}" for action in self.actions),
            *("BLOCKED: " + reason for reason in self.blockers),
            "Cancellation stops before the next stage; completed package changes remain.",
        )
