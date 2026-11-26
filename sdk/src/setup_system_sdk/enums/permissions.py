from enum import StrEnum


class Permission(StrEnum):
    ACCOUNT_KEYS = "account-keys"
    ACCOUNT_STATUS = "account-status"
    CLIENT_CONTEXT = "client-context"
    NATIVE_TERMINAL = "native-terminal"
    REMOVE_PREEXISTING = "remove-preexisting"
    REMOVE_SELECTED = "remove-selected"
    ROOT = "root"
    SESSION_OUTPUT = "session-output"
    SU = "su"
    SUDO = "sudo"
    UI_CODE = "ui-code"
    USER_FILES = "user-files"
    USER_SERVICE = "user-service"
    USER_SESSION = "user-session"
    VAULT_SETTINGS = "vault-settings"
