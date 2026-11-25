"""Optional host UI contract. Import this module only in trusted thin UI components."""

from collections.abc import Callable
from dataclasses import dataclass

from textual.widget import Widget


@dataclass(frozen=True)
class UIContext:
    plugin_id: str
    show_action: Callable[[str], None]


class PluginPanel(Widget):
    def __init__(self, context: UIContext) -> None:
        super().__init__()
        self.context = context
