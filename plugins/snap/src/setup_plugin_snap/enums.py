from enum import StrEnum


class SnapAction(StrEnum):
    LIST_INSTALLED = "list-installed"
    INSTALL_APPS = "install-apps"
    UPDATE_APPS = "update-apps"
    REMOVE_APPS = "remove-apps"
    CHECK_APPS = "check-apps"
