"""Host-owned routing evidence retained alongside the SDK confirmation."""

from pydantic import Field
from setup_system_sdk.native_execution import ProviderBinding as ProviderBinding
from setup_system_sdk.operations import Executor, Snapshot
from setup_system_sdk.schemas.common import ActionId


class ConfirmedSnapshot(Snapshot):
    providers: dict[ActionId, ProviderBinding] = Field(default_factory=dict)

    def worker_snapshot(self) -> Snapshot:
        """Send only revision-three SDK fields to the selected worker."""
        return Snapshot.model_validate(self.model_dump(exclude={"providers"}))

    def executors(self) -> set[Executor]:
        return {
            self.executor,
            *(s.executor for s in self.plan.steps),
            *(p.executor for p in self.providers.values()),
        }
