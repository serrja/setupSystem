from enum import StrEnum


class StorageScope(StrEnum):
    SETTINGS = "settings"
    ACTION = "action"
    KEYRING = "keyring"
