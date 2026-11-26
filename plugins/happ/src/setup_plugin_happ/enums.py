from enum import StrEnum


class PluginAction(StrEnum):
    EXPORT_ROUTE = "export-route"
    FORGET_LOCAL = "forget-local"
    FORGET_OWNED_FILES = "forget-owned-files"
    IMPORT = "import"
    RECONNECT = "reconnect"
    REMOVE_SUBSCRIPTION = "remove-subscription"
    SAVE_SUBSCRIPTION = "save-subscription"
    START_SYSTEM_SERVICE = "start-system-service"
    STOP_SYSTEM_SERVICE = "stop-system-service"
    UPDATE_SUBSCRIPTION = "update-subscription"
