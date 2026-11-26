"""Resolve immutable active executors without importing plugin code."""

from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from setup_system_sdk.manifests import Requirement
from setup_system_sdk.operations import Executor

from setup_system.infrastructure.plugins.environments import Activation, active, installed, retained
from setup_system.infrastructure.plugins.lifecycle import generation, generation_lock


class PluginRegistry:
    def __init__(self, root: Path, selected: Mapping[str, str]) -> None:
        self.root = root
        self.selected = dict(selected)

    def resolve(self, plugin_id: str) -> Activation:
        state = active(self.root, plugin_id)
        if not state:
            raise ValueError(f"Plugin {plugin_id} is unavailable")
        return state

    def resolve_executor(self, identity: Executor) -> Activation:
        try:
            return retained(self.root, identity)
        except (OSError, ValueError) as error:
            raise ValueError(
                f"Restore the reviewed environment for {identity.plugin_id} {identity.version}, "
                f"generation {identity.generation}, release {identity.release_digest}."
            ) from error

    def provider(self, requirement: Requirement) -> Activation:
        chosen = self.selected.get(requirement.id)
        candidates = []
        for entry in installed(self.root):
            manifest = entry.manifest
            if not manifest or entry.disabled or entry.reason or (chosen and manifest.id != chosen):
                continue
            if any(
                c.id == requirement.id and Version(c.version) in SpecifierSet(requirement.version)
                for c in manifest.provides
            ):
                candidates.append(manifest.id)
        if len(candidates) != 1:
            raise ValueError(f"Select one active compatible provider for {requirement.id}")
        return self.resolve(candidates[0])

    @contextmanager
    def leases(self, identities: set[Executor]) -> Iterator[None]:
        with ExitStack() as stack:
            for identity in sorted(identities, key=lambda row: (row.plugin_id, row.generation)):
                release = self.resolve_executor(identity)
                stack.enter_context(generation_lock(generation(release), exclusive=False))
                self.resolve_executor(identity)
            yield
