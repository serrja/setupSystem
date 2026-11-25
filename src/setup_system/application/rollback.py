"""Confirm and verify a single local managed-file recovery receipt."""

import sys

from pydantic import Field

from setup_system.application.recovery import restore
from setup_system_sdk import Inputs, Outcome, Result
from setup_system_sdk.protocol import LIMIT


class RecoveryPlan(Inputs):
    receipt: str = Field(pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
    entries: tuple[tuple[str, str], ...] = Field(max_length=4096)

    @classmethod
    def inspect(cls, receipt: str) -> "RecoveryPlan":
        return cls(receipt=receipt, entries=tuple(restore(receipt)))

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
