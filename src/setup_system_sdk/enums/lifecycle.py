from enum import StrEnum


class LifecycleAction(StrEnum):
    CHECK = "check"
    INSTALL = "install"
    UPDATE = "update"
    CONFIGURE = "configure"
    UNINSTALL = "uninstall"


class ServiceAction(StrEnum):
    START = "start-system-service"
    STOP = "stop-system-service"


class PackageAction(StrEnum):
    REMOVE_RETIRED = "remove-retired-package"


class HostAction(StrEnum):
    SAVE_SECRET = "save-secret"
    REMOVE_SECRET = "remove-secret"
