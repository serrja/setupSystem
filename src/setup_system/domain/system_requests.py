"""Finite native preflight and elevated stage inputs, independent of the current host."""

from typing import Self

from pydantic import Field, StrictBool, model_validator

from setup_system.domain.actions import RegionAction, SystemAction, Token
from setup_system.domain.contracts import ContractModel
from setup_system.domain.intentions import BaseOptions, Section, WorkspaceOptions
from setup_system.domain.observations import SystemBefore, XdgPaths


class SystemIntent(ContractModel):
    section: Section
    settings: BaseOptions | WorkspaceOptions
    xdg: XdgPaths | None = None

    @model_validator(mode="after")
    def check_stage(self) -> Self:
        if self.section == "base" and not isinstance(self.settings, BaseOptions):
            raise ValueError("System base settings must match their stage")
        if self.section == "workspace" and isinstance(self.settings, BaseOptions):
            # An empty selection is unambiguous in the enclosing stage.
            WorkspaceOptions.model_validate(self.settings.model_dump(exclude_unset=True))
        if (
            isinstance(self.settings, BaseOptions)
            and self.settings.graphical_login
            and not self.xdg
        ):
            raise ValueError("Graphical login requires inspected user directories")
        return self


class HappInspection(ContractModel):
    settings: WorkspaceOptions
    version: Token
    exact: StrictBool = False


class StageApply(SystemIntent):
    actions: tuple[RegionAction | SystemAction, ...] = Field(max_length=7)
    observed: SystemBefore
    apt_work: str = Field(
        default="",
        max_length=128,
        pattern=r"^$|^/run/setup-system/setup-system-apt-[A-Za-z0-9._-]+$",
    )

    def require_operation(self, operation: str) -> None:
        kinds = {action.kind for action in self.actions}
        kind = {
            "happ-unit": "happ_start",
            "region": "region",
            "login": "graphical_login",
            "boot": "plymouth",
            "firefox": "firefox_policy",
        }.get(operation)
        if not kind or kind not in kinds:
            raise ValueError("System operation is missing from the confirmed actions")
        if kind == "graphical_login" and (
            not isinstance(self.settings, BaseOptions)
            or not self.settings.graphical_login
            or self.observed.login is None
        ):
            raise ValueError(
                "Graphical login requires selected settings and inspected target information"
            )
        if kind == "plymouth" and (
            not isinstance(self.settings, BaseOptions)
            or not self.settings.plymouth
            or self.observed.unlock is None
            or not self.observed.unlock.identity
            or self.observed.unlock.blockers
            or self.observed.unlock_current is not False
        ):
            raise ValueError("Plymouth requires an applicable changed boot observation")
        if kind in {"firefox_policy", "happ_start"}:
            app = "firefox" if kind == "firefox_policy" else "happ"
            if not isinstance(self.settings, WorkspaceOptions) or app not in self.settings.apps:
                raise ValueError("Select the application before its system action")
            if app == "firefox" and "firefox" not in self.observed.model_fields_set:
                raise ValueError("Firefox requires its observed policy state")
        if kind == "region":
            if not isinstance(self.settings, BaseOptions):
                raise ValueError("Regional actions require base settings")
            picked = [action for action in self.actions if isinstance(action, RegionAction)]
            if len({a.field for a in picked}) != len(picked) or any(
                a.value != getattr(self.settings, a.field)
                or a.field not in self.observed.model_fields_set
                for a in picked
            ):
                raise ValueError("Confirmed regional fields differ from their selected values")
