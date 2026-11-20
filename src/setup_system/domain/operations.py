"""Immutable plan snapshots and safe cleanup policy without operating-system I/O."""

from dataclasses import dataclass

from setup_system.domain.settings import Section, SystemSettings, UserSettings


@dataclass(frozen=True)
class Action:
    id: str
    description: str
    privileged: bool = False
    blockers: tuple[str, ...] = ()
    handler: str | None = None
    before: str | None = None
    packages: tuple[str, ...] = ()
    external: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    value: str | None = None


@dataclass(frozen=True)
class WorkstationPlan:
    section: Section
    system: SystemSettings
    user: UserSettings
    actions: tuple[Action, ...]
    observations: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    system_revision: str | None = None
    user_revision: str | None = None
    draft_revision: str | None = None
    matched: bool = False

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
