from pydantic import Field

from setup_system_sdk.enums.resources import ResourceEffect, ResourceOwnership
from setup_system_sdk.schemas.common import Identifier, Inputs


class Resource(Inputs):
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    kind: Identifier
    identity: str = Field(min_length=1, max_length=4096)
    verified: bool = False
    remote_id: str | None = Field(default=None, max_length=512)
    ownership: ResourceOwnership
    before: str | None = None
    after: str | None = None
    cleanup: bool = False
    effect: ResourceEffect = ResourceEffect.ENSURE
    before_permissions: int | None = Field(default=None, ge=0, le=0o7777)
    permissions: int | None = Field(default=None, ge=0, le=0o7777)
    consumers: tuple[Identifier, ...] = ()

    def description(self) -> str:
        """Show exact approved versions, remote objects and permissions in consent."""
        values = [f"{self.effect} {self.kind} · {self.identity} · {self.ownership}"]
        for label, value in (
            ("Before", self.before),
            ("After", self.after),
            ("Source SHA-256", self.source_sha256),
            ("Remote ID", self.remote_id),
        ):
            if value is not None:
                values.append(f"{label}: {value}")
        for label, mode in (
            ("Previous permissions", self.before_permissions),
            ("Permissions", self.permissions),
        ):
            if mode is not None:
                values.append(f"{label}: {mode:04o}")
        if self.consumers:
            values.append("Consumers: " + ", ".join(self.consumers))
        return " · ".join(
            "".join(c if c.isprintable() else repr(c)[1:-1] for c in v) for v in values
        )
