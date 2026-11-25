"""Identifiers and exact native commands for Docker-specific actions."""

from enum import StrEnum


class DockerAction(StrEnum):
    CREATE_CONTEXT = "create-context"
    REMOVE_OWNED_CONTEXTS = "remove-owned-contexts"
    START_SERVICE = "start-service"
    STOP_SERVICE = "stop-service"
    LOGIN = "login"
    LOGOUT = "logout"
    SETUP_MODE = "setup-mode"
    PRUNE = "prune"


USER_SERVICE_TRANSITIONS = {
    DockerAction.START_SERVICE: ("start", "active"),
    DockerAction.STOP_SERVICE: ("stop", "inactive"),
}
