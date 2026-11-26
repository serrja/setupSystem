from enum import StrEnum


class PluginAction(StrEnum):
    INSPECT_PROJECTS = "inspect-projects"
    OPEN_CODEX = "open-codex"
    OPEN_EDITOR = "open-editor"
    PREPARE_DIRECTORIES = "prepare-directories"
    TEMPLATE_CACHE = "template-cache"
    TEMPLATE_CREATE = "template-create"
    TEMPLATE_UPDATE = "template-update"
    TEMPLATE_VERSIONS = "template-versions"
