"""Read the installed workstation guides without opening a browser."""

from pathlib import Path
from typing import ClassVar

import aiofiles
from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Select

TOPICS = {
    "configuration": "Configuration",
    "administration": "Administrative access",
    "recovery": "Recovery",
    "desktop_login": "Login and screen lock",
    "graphical_unlock": "Disk unlock",
    "applications": "Application sources",
}


class Help(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "close", "Close")]

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory

    def compose(self) -> ComposeResult:
        yield Select(
            [(label, key) for key, label in TOPICS.items()],
            value="configuration",
            allow_blank=False,
            id="help-topic",
        )
        with VerticalScroll():
            yield Markdown(open_links=False)
        yield Button("Close help", id="help-close")

    @on(Select.Changed)
    def show_topic(self, event: Select.Changed) -> None:
        key = event.value
        if isinstance(key, str) and key in TOPICS and self.is_active:
            self.run_worker(self.read_topic(key), group="help-topic", exclusive=True)

    async def read_topic(self, key: str) -> None:
        if not self.app.is_running or not self.is_active:
            return
        markdown = self.query_one(Markdown)
        try:
            async with aiofiles.open(self.directory / (key + ".md"), encoding="utf-8") as src:
                content = await src.read()
        except (OSError, UnicodeError):
            content = "The local guide is unavailable. Check the installed package."
        if self.app.is_running and self.is_active and markdown.is_attached:
            await markdown.update(content)

    @on(Button.Pressed, "#help-close")
    def action_close(self) -> None:
        self.dismiss()
