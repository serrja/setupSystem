from enum import StrEnum


class PluginAction(StrEnum):
    INSTALL_PARSERS = "install-parsers"
    INSTALL_PLUGINS = "install-plugins"
    REMOVE_PARSERS = "remove-parsers"
    VERIFY_EDITOR = "verify-editor"
