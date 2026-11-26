from enum import StrEnum


class WorkerMethod(StrEnum):
    EXECUTE = "execute"
    RESTORE_PLAN = "restore_plan"
    READBACK = "readback"
    PROVIDER_INSPECT = "provider_inspect"
    PROVIDER_PLAN = "provider_plan"
    INSPECT = "inspect"
    PLAN = "plan"
    VERIFY = "verify"
    CHOICES = "choices"
    VALIDATE = "validate"
    MIGRATE = "migrate"


class OperationState(StrEnum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    VALIDATING = "validating"
    PLANNING = "planning"
    READY = "ready"
    BLOCKED = "blocked"
    AUTHENTICATING = "authenticating"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActionMode(StrEnum):
    AUTHENTICATED_CHECK = "authenticated_check"
    MUTATION = "mutation"
    READ_ONLY = "read_only"
    MANUAL = "manual"


class Outcome(StrEnum):
    NO_CHANGES = "no_changes"
    READY = "ready"
    CHANGED = "changed"
    COMPLETE = "complete"
    NEEDS_USER_ACTION = "needs_user_action"
    NEEDS_REBOOT = "needs_reboot"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"


class EventPhase(StrEnum):
    PLANNED = "planned"
    PREPARED = "prepared"
    APPLIED = "applied"
    VERIFIED = "verified"
    UNRESOLVED = "unresolved"
    BLOCKED = "blocked"


class EventLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
