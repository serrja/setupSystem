"""Contain Textual's private fatal hook in one adapter for versions without a public hook."""

from rich.text import Text
from textual.app import App

from setup_system.errors import normalize_failure
from setup_system.ui.error_presenter import error_line


class SafeApp[Result](App[Result]):
    def _handle_exception(self, error: Exception) -> None:
        """Restore Textual's terminal and print one safe fatal result on exit."""
        detail = normalize_failure(error, stage="ui")
        journal = getattr(getattr(self, "services", None), "journal", None)
        if journal is not None:
            try:
                journal.record_failure(detail, error, stage="ui")
            except Exception:
                pass
        self._return_code = 1
        message = error_line(detail)
        if detail.diagnostic_id:
            message += f" · Diagnostic ID: {detail.diagnostic_id}"
        self.panic(Text(message, style="red"))
