from enum import StrEnum


class ResourceOwnership(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    PREEXISTING = "preexisting"
    SHARED = "shared"


class ResourceEffect(StrEnum):
    ENSURE = "ensure"
    REMOVE = "remove"


class ReceiptState(StrEnum):
    RUNNING = "running"
    PARTIAL = "partial"
    FAILED = "failed"
    COMPLETE = "complete"
    RESTORED = "restored"


class RecoveryStep(StrEnum):
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    WOULD_REMOVE = "would-remove"
    WOULD_RESTORE = "would-restore"
    WOULD_REMOVE_DIRECTORY = "would-remove-directory"
    WOULD_RETURN_DISPLACED = "would-return-displaced"
    RETURNED_DISPLACED = "returned-displaced"
    REMOVED = "removed"
    RESTORED = "restored"
    ALREADY_RESTORED = "already-restored"
