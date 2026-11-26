from enum import StrEnum


class PluginAction(StrEnum):
    ACCOUNT_SYNC = "account-sync"
    COMMUNITY_PLUGINS = "community-plugins"
    CORE_FEATURE = "core-feature"
    CREATE_VAULT = "create-vault"
    EDITOR = "editor"
    EDITOR_SNIPPET = "editor-snippet"
    HOTKEYS = "hotkeys"
    OPEN = "open"
    READ_HOTKEY = "read-hotkey"
    SET_THEME = "set-theme"
