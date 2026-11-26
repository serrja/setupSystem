"""Restore the exact home files named by a confirmed worker receipt."""

from functools import partial

from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.enums.resources import ReceiptState, RecoveryStep, ResourceEffect
from setup_system_sdk.files import receipt_repository
from setup_system_sdk.files.receipts import Operation, RunId
from setup_system_sdk.files.recovery import recover
from setup_system_sdk.files.restore_files import LocalRecoveryFiles
from setup_system_sdk.files.user_storage import target_path
from setup_system_sdk.operations import Receipt, Result, StepPlan
from setup_system_sdk.resources import Resource
from setup_system_sdk.schemas.common import Inputs


class FileRollback(Inputs):
    backup: RunId
    resources: tuple[Resource, ...]


def check_receipt(params: FileRollback, operation: Operation) -> None:
    if operation.run_id != params.backup or operation.requested_directories:
        raise ValueError("Unexpected recovery identity or directory publication")
    if operation.status not in {ReceiptState.COMPLETE, ReceiptState.RESTORED}:
        raise ValueError("Incomplete file receipt")
    approved = {r.identity: r for r in params.resources if r.kind == "file"}
    if len(approved) != len(operation.files) or len(approved) != len(params.resources):
        raise ValueError("File receipt exceeds the approved resources")
    for row in operation.files:
        item = approved[str(target_path(row.relative))]
        if (
            not row.verified
            or row.after.sha256 != item.after
            or (row.before.sha256 if row.before else None) != item.before
        ):
            raise ValueError("File receipt differs from the approved fingerprints")


def plan(original: StepPlan, receipt: Receipt) -> StepPlan:
    backup = receipt.result.recovery_id
    if not backup or original.capability or not original.resources:
        raise ValueError("This step has no local file recovery receipt")
    params = FileRollback(backup=backup, resources=original.resources)
    folder = receipt_repository.operation_folder(backup)
    operation = receipt_repository.load_operation(folder)
    check_receipt(params, operation)
    entries = recover(
        folder,
        apply=False,
        journal=receipt_repository,
        files=LocalRecoveryFiles(),
        check=partial(check_receipt, params),
    )
    if any(e.status is RecoveryStep.CONFLICT for e in entries):
        raise ValueError("A managed file changed since the operation")
    by_path = {str(target_path(row.relative)): row for row in operation.files}
    rows = tuple(
        r.model_copy(
            update={
                "before": r.after,
                "after": r.before,
                "before_permissions": by_path[r.identity].after.mode,
                "permissions": before.mode if (before := by_path[r.identity].before) else None,
                "effect": ResourceEffect.ENSURE if r.before else ResourceEffect.REMOVE,
            }
        )
        for r in original.resources
    )
    return StepPlan(
        step_key=original.step_key,
        executor=original.executor,
        handler="restore-files",
        contract_version="1",
        payload=params.model_dump(mode="json"),
        resources=rows,
        permissions=frozenset({Permission.USER_FILES}),
        lock_keys=original.lock_keys,
    )


def restore_step(step: StepPlan, *, apply: bool) -> Result:
    params = FileRollback.model_validate(step.payload)
    folder = receipt_repository.operation_folder(params.backup)
    entries = recover(
        folder,
        apply=apply,
        journal=receipt_repository,
        files=LocalRecoveryFiles(),
        check=partial(check_receipt, params),
    )
    if apply:
        return Result(
            outcome=Outcome.CHANGED,
            message="Processed file rollback",
            resources=list(step.resources),
        )
    verified = entries and all(row.status is RecoveryStep.ALREADY_RESTORED for row in entries)
    return Result.observed(
        outcome=Outcome.COMPLETE if verified else Outcome.UNKNOWN_OUTCOME,
        message="Checked original file fingerprints",
        resources=list(step.resources) if verified else [],
    )
