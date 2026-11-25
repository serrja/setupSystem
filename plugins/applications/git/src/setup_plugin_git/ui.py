"""Git author and hosting shortcuts for the trusted Textual host."""

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Label

from setup_system_sdk.ui import PluginPanel


class GitPanel(PluginPanel):
    def compose(self) -> ComposeResult:
        yield Label("Commit author and hosting account")
        with Horizontal():
            yield Button("Author profile", id="configure")
            yield Button("Hosting status", id="status")
            yield Button("SSH public key", id="upload-key")

    @on(Button.Pressed)
    def open_action(self, event: Button.Pressed) -> None:
        if event.button.id in {"configure", "status", "upload-key"}:
            event.stop()
            self.context.show_action(event.button.id)
