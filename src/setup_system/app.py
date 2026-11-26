"""Textual shell for the plugin catalog and confirmed operations."""

from pathlib import Path
from typing import ClassVar
from uuid import UUID

from pydantic import SecretBytes
from setup_system_sdk.enums.operation import Outcome
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from setup_system.application.authorization import AuthorizationRequest
from setup_system.application.plugins import CatalogEntry
from setup_system.bootstrap import Runtime
from setup_system.ui.plugin_dialogs import Confirm, Prompt, SecretPrompt
from setup_system.ui.plugin_page import PluginPage


class SetupApp(App[None]):
    TITLE = "setupSystem"
    BINDINGS: ClassVar[list[BindingType]] = [
        ("ctrl+f", "search", "Find plugin"),
        ("ctrl+h", "history", "History"),
        ("ctrl+q", "quit", "Quit"),
    ]
    CSS = """
    #sidebar { width: 32; border-right: solid $accent; padding: 0 1; }
    #workspace, #catalog { height: 1fr; }
    PluginPage, .forms { height: auto; }
    .dialog { width: 75%; height: auto; max-height: 85%; padding: 1 2;
              border: solid $accent; background: $panel; }
    .review-text { height: auto; max-height: 25; }
    Confirm, Prompt, SecretPrompt { align: center middle; }
    #feedback { height: auto; max-height: 5; }
    """

    def __init__(self, runtime: Runtime) -> None:
        super().__init__()
        self.services = runtime
        self.entries: dict[str, CatalogEntry] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Input(placeholder="Search plugins", id="search")
                yield OptionList(id="catalog")
                yield Button("Add from link", id="add")
                yield Button("History", id="history")
                yield Button("Restore files", id="restore-files")
                yield Button("Recover operation", id="recover")
            with Vertical(id="workspace"):
                yield Static("Select a plugin or activate a reviewed release.")
        yield Static("", id="feedback", markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        await self.reload_catalog()

    async def reload_catalog(self) -> None:
        rows = await self.services.plugins.catalog()
        self.entries = {row.manifest.id: row for row in rows if row.manifest}
        self.filter_catalog()
        self.query_one("#feedback", Static).update(
            "\n".join(row.reason for row in rows if row.reason)
        )

    @on(Input.Changed, "#search")
    def filter_catalog(self) -> None:
        needle = self.query_one("#search", Input).value.casefold()
        listing = self.query_one("#catalog", OptionList)
        listing.clear_options()
        for key, entry in sorted(self.entries.items()):
            manifest = entry.manifest
            if manifest and needle in (manifest.name + key + manifest.category).casefold():
                listing.add_option(
                    Option(
                        f"{manifest.name} · {manifest.category}"
                        + (" · disabled" if entry.disabled else ""),
                        id=key,
                        disabled=bool(entry.reason),
                    )
                )

    @on(OptionList.OptionSelected, "#catalog")
    async def open_plugin(self, event: OptionList.OptionSelected) -> None:
        key = event.option.id
        if not key:
            return
        workspace = self.query_one("#workspace", Vertical)
        await workspace.remove_children()
        await workspace.mount(PluginPage(self.services, self.entries[key], self.reload_catalog))

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    @on(Button.Pressed, "#history")
    @work(exit_on_error=False)
    async def action_history(self) -> None:
        rows = await self.services.recovery.history()
        await self.push_screen_wait(
            Confirm(
                "\n".join(" · ".join(row) for row in rows) or "No recorded operations.", "Close"
            )
        )

    @on(Button.Pressed, "#restore-files")
    @work(exit_on_error=False)
    async def restore_files(self) -> None:
        rows = [r for r in await self.services.recovery.history() if r[3] == "verified"]
        if not rows:
            self.query_one("#feedback", Static).update("No completed operations.")
            return
        value = await self.push_screen_wait(
            Prompt("Select a completed file operation\n" + "\n".join(" · ".join(r) for r in rows))
        )
        if not value or value not in {r[0] for r in rows}:
            return
        try:
            plan = await self.services.recovery.rollback(UUID(value))
            detail = "\n".join([plan.plan.summary, *(r.description() for r in plan.plan.resources)])
            if await self.push_screen_wait(Confirm(detail, "Restore approved files")):
                result = await self.services.operations.apply(plan)
                self.query_one("#feedback", Static).update(result.message)
        except (ValueError, RuntimeError, OSError):
            self.query_one("#feedback", Static).update(
                "The original executor could not confirm this rollback."
            )

    @on(Button.Pressed, "#recover")
    @work(exit_on_error=False)
    async def recover(self) -> None:
        rows = await self.services.recovery.history(unfinished=True)
        if not rows:
            self.query_one("#feedback", Static).update("No unfinished operations.")
            return
        value = await self.push_screen_wait(
            Prompt("Operation to inspect\n" + "\n".join(" · ".join(r) for r in rows))
        )
        if value in {r[0] for r in rows} and value:
            result = await self.services.recovery.inspect(UUID(value))
            self.query_one("#feedback", Static).update(result.message)
            if result.outcome != Outcome.NEEDS_USER_ACTION:
                return
            plan = await self.services.recovery.plan(UUID(value))
            lines = ["Verify and finish operation " + value]
            for step in plan.snapshot.plan.steps:
                if step.step_key in plan.remaining:
                    lines.extend(
                        [
                            step.step_key
                            + " · "
                            + step.executor.plugin_id
                            + " · "
                            + step.executor.release_digest,
                            *(row.description() for row in step.resources),
                        ]
                    )
            if await self.push_screen_wait(Confirm("\n".join(lines), "Confirm recovery")):

                async def authorize(request: AuthorizationRequest) -> SecretBytes | None:
                    response = await self.push_screen_wait(
                        SecretPrompt(
                            f"Authorize {request.executor.plugin_id} for {request.operation_id}"
                        )
                    )
                    return SecretBytes(response.get_secret_value().encode()) if response else None

                resumed = await self.services.recovery.apply(plan, authorize)
                self.query_one("#feedback", Static).update(resumed.message)

    @on(Button.Pressed, "#add")
    @work(exit_on_error=False)
    async def add(self) -> None:
        link = await self.push_screen_wait(
            Prompt("HTTPS release URL or absolute path to a release manifest")
        )
        if not link:
            return
        try:
            release = await self.services.plugins.preview_release(link)
            record = release.selected()
            info = [
                f"{record.manifest.name} {record.manifest.version}",
                record.manifest.author,
                record.manifest.source,
                ", ".join(record.manifest.permissions),
                release.digest,
                *(f"{w.name} · {w.sha256}" for w in record.wheels),
            ]
            if not await self.push_screen_wait(Confirm("\n".join(info), "Review activation")):
                return
            digest = await self.push_screen_wait(
                Prompt("Independently verified SHA-256 of this release")
            )
            if digest:
                await self.services.plugins.activate(
                    release, digest, Path(link).parent if link.startswith("/") else None
                )
                await self.reload_catalog()
        except (OSError, RuntimeError, ValueError):
            self.query_one("#feedback", Static).update(
                "Activation failed. Check the origin, digest and dependencies."
            )
