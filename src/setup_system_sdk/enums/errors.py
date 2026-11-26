from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    INVALID_INPUT = "invalid_input"
    UNAVAILABLE_CHOICE = "unavailable_choice"
    INCOMPATIBLE_PLUGIN = "incompatible_plugin"
    UNKNOWN_ACTION = "unknown_action"
    WORKER_FAILED = "worker_failed"
    WORKER_TIMEOUT = "worker_timeout"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    STALE_PLAN = "stale_plan"
    AUTHORIZATION = "authorization"
    CONFLICT = "conflict"
    UNKNOWN_OUTCOME = "unknown_outcome"
