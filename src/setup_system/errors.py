"""Safe failures shared by host services and their presentation."""

from setup_system_sdk.schemas.errors import Error


class OperationError(Exception):
    def __init__(self, detail: Error) -> None:
        super().__init__(detail.message)
        self.detail = detail
