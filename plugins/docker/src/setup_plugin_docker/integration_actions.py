"""Identifiers and exact native commands for Docker-specific actions."""

from setup_plugin_docker.enums import DockerAction

USER_SERVICE_TRANSITIONS = {
    DockerAction.START_SERVICE: ("start", "active"),
    DockerAction.STOP_SERVICE: ("stop", "inactive"),
}
