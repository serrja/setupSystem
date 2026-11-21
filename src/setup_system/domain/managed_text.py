"""Merge a delimited application-owned section while preserving surrounding text."""

from __future__ import annotations


def managed_block(original: str, body: str | None) -> str:
    start, end = "# >>> setupSystem >>>", "# <<< setupSystem <<<"
    if (
        original.count(start) > 1
        or original.count(end) > 1
        or original.count(start) != original.count(end)
    ):
        raise ValueError("Malformed setupSystem-owned block")
    if start in original:
        before, rest = original.split(start, 1)
        _, after = rest.split(end, 1)
        original = before.rstrip("\n") + after
    if body is None:
        return original.rstrip("\n") + "\n"
    return original.rstrip("\n") + f"\n\n{start}\n{body.rstrip()}\n{end}\n"
