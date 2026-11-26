"""Confirm and verify a single local managed-file recovery receipt."""

import os
import sys
from pathlib import Path

from pydantic import Field

from setup_system.execution import receipt_repository
from setup_system.execution.receipts import FileState, rollback_status
from setup_system.execution.recovery_types import RecoveryEntry
from setup_system.execution.restore_files import LocalRecoveryFiles
from setup_system.services.recovery import restore
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ReceiptState, RecoveryStep
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Result
from setup_system_sdk.schemas.wire import LIMIT


class RecoveryPlan(Inputs):
    receipt: str = Field(pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
    entries: tuple[RecoveryEntry, ...] = Field(max_length=4096)

    @classmethod
    def inspect(cls, receipt: str) -> "RecoveryPlan":
        return cls(receipt=receipt, entries=tuple(restore(receipt)))

    @classmethod
    def projected(cls, receipt: str, states: dict[str, FileState | None]) -> "RecoveryPlan":
        """Plan an older receipt against the verified result of newer restorations."""
        current = cls.inspect(receipt)
        folder = receipt_repository.operation_folder(receipt)
        operation = receipt_repository.load_operation(folder)
        if operation.status is ReceiptState.RESTORED:
            return current
        files = LocalRecoveryFiles()
        entries = []
        paths = []
        for row in operation.files:
            inspected = files.inspect(row)
            name = str(inspected.path)
            if name in states:
                status = rollback_status(row, states[name], published=row.verified)
                inspected = inspected.model_copy(update={"status": status})
            entries.append(inspected)
            paths.append(inspected.path)
            if inspected.status is not RecoveryStep.CONFLICT:
                states[name] = row.before
        disappearing = {Path(name) for name, state in states.items() if state is None}
        directories = files.directories(operation, paths)
        for path, entry in files.removable_directories(directories, disappearing):
            if entry.relative in operation.requested_directories:
                entries.append(RecoveryEntry(path=path, status=RecoveryStep.WOULD_REMOVE_DIRECTORY))
            states[str(path)] = None
        return cls(receipt=receipt, entries=tuple(entries))

    def apply(self) -> Result:
        if self != self.inspect(self.receipt) or any(
            e.status is RecoveryStep.CONFLICT for e in self.entries
        ):
            return Result(
                outcome=Outcome.BLOCKED, message="Recovery facts changed or a later edit conflicts"
            )
        restore(self.receipt, apply=True)
        verified = self.inspect(self.receipt)
        complete = all(row.status is RecoveryStep.ALREADY_RESTORED for row in verified.entries)
        complete = complete and all(
            not os.path.lexists(row.path)
            for row in self.entries
            if row.status is RecoveryStep.WOULD_REMOVE_DIRECTORY
        )
        message = "Recovery verified" if complete else "Inspect the remaining recovery entries"
        retained = [str(row.retained_path) for row in verified.entries if row.retained_path]
        if retained:
            message += f" · Receipt {self.receipt} records displaced files: " + ", ".join(retained)
        return Result(
            outcome=Outcome.COMPLETE if complete else Outcome.PARTIAL, message=message[:8192]
        )


def main() -> int:
    data = sys.stdin.buffer.read(LIMIT + 1)
    if len(data) > LIMIT:
        return 2
    try:
        result = RecoveryPlan.model_validate_json(data).apply()
        sys.stdout.write(result.model_dump_json())
    except (ValueError, OSError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
