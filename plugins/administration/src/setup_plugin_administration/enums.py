from enum import StrEnum


class PluginAction(StrEnum):
    GRANT_SUDO = "grant-sudo"
    VERIFY_SUDO = "verify-sudo"
