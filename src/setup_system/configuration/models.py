"""Validated normal-user application preferences."""

from typing import Annotated

from pydantic import Field, StrictFloat, StrictInt

from setup_system.domain.contracts import ContractModel


class ApplicationOptions(ContractModel):
    """Timing and directory scan bounds consumed by Textual and its Make runner."""

    suggestion_debounce_seconds: Annotated[StrictFloat, Field(ge=0, le=1)]
    suggestion_cache_directories: Annotated[StrictInt, Field(ge=1, le=128)]
    suggestion_scan_entries: Annotated[StrictInt, Field(ge=1, le=8192)]
    suggestion_results: Annotated[StrictInt, Field(ge=1, le=512)]
    prompt_timeout_seconds: Annotated[StrictFloat, Field(ge=0.01, le=300)]
    idle_timeout_seconds: Annotated[StrictFloat, Field(ge=0.01, le=3600)]
    terminal_timeout_seconds: Annotated[StrictFloat, Field(ge=1, le=3600)]
    termination_grace_seconds: Annotated[StrictFloat, Field(ge=0.01, le=30)]
    read_only_timeout_seconds: Annotated[StrictFloat, Field(ge=0.01, le=86400)]
