"""Safe failures shared by host services and their presentation."""

import asyncio
import re
from uuid import UUID, uuid4

from pydantic import ValidationError
from setup_system_sdk.downloads import DownloadError, DownloadFailure
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.schemas.errors import Error
from sqlalchemy.exc import SQLAlchemyError


class OperationError(RuntimeError):
    def __init__(self, detail: Error) -> None:
        super().__init__(detail.message)
        self.detail = detail


def _leaves(failure: BaseException) -> list[BaseException]:
    if isinstance(failure, BaseExceptionGroup):
        return [leaf for child in failure.exceptions for leaf in _leaves(child)]
    return [failure]


def normalize_failure(
    failure: BaseException,
    *,
    stage: str,
    operation_id: UUID | None = None,
    component: str | None = None,
) -> Error:
    """Expose one safe failure; retain its original exception for private diagnostics."""
    leaves = _leaves(failure)
    active = [leaf for leaf in leaves if not isinstance(leaf, asyncio.CancelledError)]
    if not active:
        raise asyncio.CancelledError from failure
    selected: BaseException | None = next(
        (leaf for leaf in active if isinstance(leaf, OperationError)), None
    )
    selected = selected or next((leaf for leaf in active if isinstance(leaf, DownloadError)), None)
    selected = selected or next(
        (leaf for leaf in active if isinstance(leaf, ValidationError)), None
    )
    selected = selected or active[0]
    if isinstance(selected, OperationError):
        detail = selected.detail
        return detail.model_copy(
            update={
                "operation_id": detail.operation_id or operation_id,
                "diagnostic_id": detail.diagnostic_id or uuid4(),
            }
        )
    context = {"stage": stage}
    if component and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", component):
        context["component"] = component
    if isinstance(selected, DownloadError):
        if selected.status is not None:
            context["http_status"] = str(selected.status)
        if selected.host and re.fullmatch(r"[a-z0-9.-]{1,253}", selected.host):
            context["hostname"] = selected.host
        integrity = selected.kind in {DownloadFailure.DIGEST, DownloadFailure.SIZE}
        code = ErrorCode.INTEGRITY_MISMATCH if integrity else ErrorCode.DOWNLOAD_FAILED
        subject = component or "the reviewed artifact"
        message = (
            f"Integrity verification failed for {subject}."
            if integrity
            else f"Could not download {subject}"
            + (f" (HTTP {selected.status})." if selected.status else ".")
        )
        hint = (
            "Retry when the verified release is available."
            if not integrity
            else ("Review the release source and digest before retrying.")
        )
        retryable = selected.kind is DownloadFailure.NETWORK or selected.status in {
            408,
            429,
            502,
            503,
            504,
        }
    elif isinstance(selected, ValidationError):
        code = (
            ErrorCode.CONFIGURATION_INVALID
            if stage in {"configuration", "startup"}
            else ErrorCode.INVALID_INPUT
        )
        message = (
            "Configuration is invalid."
            if stage in {"configuration", "startup"}
            else "Check the field values."
        )
        hint = "Correct the indicated setting and retry."
        retryable = False
        first = selected.errors(include_input=False, include_context=False)[0]
        field = tuple(part for part in first.get("loc", ()) if isinstance(part, (str, int)))
    elif isinstance(selected, PermissionError):
        code = ErrorCode.STORAGE_UNAVAILABLE
        message = "A private application directory has unsafe ownership or permissions."
        hint = "Check the owner and permissions of the configured state directory."
        retryable = False
    elif isinstance(selected, ModuleNotFoundError):
        name = selected.name
        module = name if name and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", name) else None
        code = ErrorCode.DEPENDENCY_MISSING
        message = (
            f"Required Python module {module} is unavailable."
            if module
            else "An application Python dependency is unavailable."
        )
        hint = "Repair the verified runtime or activate a complete plugin release."
        retryable = False
    elif stage == "integrity" and isinstance(selected, (OSError, ValueError)):
        code = ErrorCode.INTEGRITY_MISMATCH
        message = "The reviewed wheel differs from its release descriptor."
        hint = "Review the selected release and its approved digest."
        retryable = False
    elif stage == "database" and isinstance(selected, ValueError):
        code = ErrorCode.DATABASE_NEEDS_MIGRATION
        message = "The database schema is unavailable or needs migration."
        hint = "Run setup-system migrate after checking the database state."
        retryable = False
    elif stage == "database" and isinstance(selected, (OSError, SQLAlchemyError)):
        code = ErrorCode.STORAGE_UNAVAILABLE
        message = "The private database could not be opened."
        hint = "Check its owner, permissions and available storage."
        retryable = False
    elif stage == "uv":
        code = (
            ErrorCode.DEPENDENCY_MISSING
            if isinstance(selected, FileNotFoundError)
            else ErrorCode.ACTIVATION_FAILED
        )
        message = (
            "The bundled uv executable is unavailable."
            if code is ErrorCode.DEPENDENCY_MISSING
            else "The verified uv runtime is unavailable."
        )
        hint = "Check the bundled executable or the pinned uv download."
        retryable = False
    elif stage == "activation":
        code = ErrorCode.ACTIVATION_FAILED
        message = "Plugin activation did not complete."
        hint = "Review the selected release and its requirements."
        retryable = False
    else:
        code = ErrorCode.INTERNAL_ERROR
        message = "The requested action could not be completed."
        hint = "Review the diagnostic ID and retry after correcting the cause."
        retryable = False
    return Error(
        code=code,
        message=message,
        hint=hint,
        field=field if isinstance(selected, ValidationError) else (),
        operation_id=operation_id,
        diagnostic_id=uuid4(),
        retryable=retryable,
        context=context,
    )
