"""Git author and SSH key shortcuts for the trusted Textual host."""

from setup_system_sdk.ui import PluginPanel
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Label


class GitPanel(PluginPanel):
    def compose(self) -> ComposeResult:
        yield Label("Commit identity and SSH keys")
        with Horizontal():
            yield Button("Author identity", id="configure")
            yield Button("SSH public key", id="upload-key")

    @on(Button.Pressed)
    def open_action(self, event: Button.Pressed) -> None:
        if event.button.id in {"configure", "upload-key"}:
            event.stop()
            self.context.show_action(event.button.id)
