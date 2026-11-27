"""Release-bound provider evidence for a protected native dispatch."""

from pathlib import Path

from pydantic import Field

from setup_system_sdk.files.manifest_types import Digest
from setup_system_sdk.operations import Executor, StepPlan
from setup_system_sdk.schemas.common import ActionId, Identifier, Inputs
from setup_system_sdk.schemas.wire import Request


class ProviderBinding(Inputs):
    executor: Executor
    contract_version: str


class NativeFactProvider(Inputs):
    plugin_id: Identifier
    version: str
    release_digest: Digest
    contract_version: str

    def matches(self, binding: ProviderBinding) -> bool:
        release = binding.executor
        return (
            release.plugin_id,
            release.version,
            release.release_digest,
            binding.contract_version,
        ) == (self.plugin_id, self.version, self.release_digest, self.contract_version)


class TrustedExecution(Inputs):
    plugin_id: Identifier
    version: str
    release_digest: Digest
    launcher: Path
    launcher_sha256: Digest
    allowed_handlers: frozenset[ActionId]
    resources: dict[Path, Digest] = Field(max_length=4096)
    fact_providers: dict[ActionId, dict[ActionId, NativeFactProvider]] | None = None

    def validate_providers(
        self,
        step: StepPlan,
        selected: dict[str, ProviderBinding],
        planned: tuple[StepPlan, ...] = (),
    ) -> tuple[StepPlan, ...]:
        """Return delegated steps that supply independent release evidence for an older record."""
        if step.capability:
            return ()
        if self.fact_providers is None:
            evidence = tuple(s for s in planned if s.capability in selected)
            observed = {s.capability for s in evidence}
            if observed != set(selected) or any(
                s.capability is None
                or selected[s.capability].executor != s.executor
                or selected[s.capability].contract_version != s.contract_version
                for s in evidence
            ):
                raise ValueError(
                    "Install a native record identifying the unresolved fact providers."
                )
            return evidence
        expected = self.fact_providers.get(step.handler, {})
        if set(expected) != set(selected) or any(
            not release.matches(selected[capability]) for capability, release in expected.items()
        ):
            raise ValueError(
                "Selected providers differ from those approved for this native handler."
            )
        return ()


class NativeRequest(Request):
    providers: dict[ActionId, ProviderBinding] = Field(default_factory=dict)
