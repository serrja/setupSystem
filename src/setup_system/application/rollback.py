"""Confirm and verify a single local managed-file recovery receipt."""

import sys
from pathlib import Path

from pydantic import Field

from setup_system.application.recovery import restore
from setup_system.domain.receipts import FileState, rollback_status
from setup_system.infrastructure import receipt_repository
from setup_system.infrastructure.restore_files import LocalRecoveryFiles
from setup_system_sdk import Inputs, Outcome, Result
from setup_system_sdk.protocol import LIMIT


class RecoveryPlan(Inputs):
    receipt: str = Field(pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
    entries: tuple[tuple[str, str], ...] = Field(max_length=4096)

    @classmethod
    def inspect(cls, receipt: str) -> "RecoveryPlan":
        return cls(receipt=receipt, entries=tuple(restore(receipt)))

    @classmethod
    def projected(cls, receipt: str, states: dict[str, FileState | None]) -> "RecoveryPlan":
        """Plan an older receipt against the verified result of newer restorations."""
        current = cls.inspect(receipt)
        folder = receipt_repository.operation_folder(receipt)
        if receipt_repository.receipt_source(folder) is None:
            return current
        operation = receipt_repository.load_operation(folder)
        if operation.status == "restored":
            return current
        files = LocalRecoveryFiles()
        entries = []
        paths = []
        for row in operation.files:
            path, status = files.inspect(row)
            name = str(path)
            if name in states:
                status = rollback_status(row, states[name], published=row.verified)
            entries.append((name, status))
            paths.append(path)
            if status != "conflict":
                states[name] = row.before
        disappearing = {Path(name) for name, state in states.items() if state is None}
        directories = files.directories(operation, paths)
        for path, entry in files.removable_directories(directories, disappearing):
            if entry.relative in operation.requested_directories:
                entries.append((str(path), "would-remove-directory"))
            states[str(path)] = None
        return cls(receipt=receipt, entries=tuple(entries))

    def apply(self) -> Result:
        if self != self.inspect(self.receipt) or any(s == "conflict" for _, s in self.entries):
            return Result(
                outcome=Outcome.BLOCKED, message="Recovery facts changed or a later edit conflicts"
            )
        restore(self.receipt, apply=True)
        verified = self.inspect(self.receipt)
        complete = all(
            s.startswith("already restored") or s in ("unchanged", "present")
            for _, s in verified.entries
        )
        return Result(
            outcome=Outcome.COMPLETE if complete else Outcome.PARTIAL,
            message="Recovery verified" if complete else "Inspect the remaining recovery entries",
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
