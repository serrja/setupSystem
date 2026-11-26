"""Shared immutable collections and lexical constraints for reviewed release inputs."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated

from pydantic import AfterValidator, Field, PlainSerializer


def _freeze[K, V](value: Mapping[K, V]) -> Mapping[K, V]:
    return MappingProxyType(dict(value))


type FrozenMap[K, V] = Annotated[
    Mapping[K, V], AfterValidator(_freeze), PlainSerializer(dict, return_type=dict)
]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
