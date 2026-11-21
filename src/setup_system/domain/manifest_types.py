"""Shared immutable collections and lexical constraints for reviewed release inputs."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated

from pydantic import AfterValidator, Field, PlainSerializer


def _freeze[K, V](value: Mapping[K, V]) -> Mapping[K, V]:
    return MappingProxyType(dict(value))


def _unique[T](values: tuple[T, ...]) -> tuple[T, ...]:
    if len(values) != len(set(values)):
        raise ValueError("Repeated release manifest entry")
    return values


type FrozenMap[K, V] = Annotated[
    Mapping[K, V], AfterValidator(_freeze), PlainSerializer(dict, return_type=dict)
]
type UniqueEntries[T] = Annotated[tuple[T, ...], Field(min_length=1), AfterValidator(_unique)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Commit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Version = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
