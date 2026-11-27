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
from setup_system.application.recovery import OperationHistoryItem, RecoveryAction
from setup_system.bootstrap import Runtime
from setup_system.errors import OperationError
from setup_system.ui.plugin_dialogs import Confirm, Prompt, SecretPrompt
from setup_system.ui.plugin_page import PluginPage


def history_line(row: OperationHistoryItem) -> str:
    return " · ".join(
        value
        for value in (
            row.operation_id,
            row.plugin_id,
            row.action_id,
            row.phase.value,
            row.outcome.value if row.outcome else "",
            row.started.isoformat(sep=" ", timespec="seconds") if row.started else "",
        )
        if value
    )


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

    async def reload_catalog(self, plugin: str | None = None) -> CatalogEntry | None:
        rows = await self.services.plugins.catalog()
        self.entries = {row.manifest.id: row for row in rows if row.manifest}
        self.filter_catalog()
        self.query_one("#feedback", Static).update(
            "\n".join(row.reason for row in rows if row.reason)
        )
        return self.entries.get(plugin) if plugin is not None else None

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
                "\n".join(history_line(row) for row in rows) or "No recorded operations.", "Close"
            )
        )

    @on(Button.Pressed, "#restore-files")
    @work(exit_on_error=False)
    async def restore_files(self) -> None:
        rows = await self.services.recovery.available(RecoveryAction.ROLLBACK)
        if not rows:
            self.query_one("#feedback", Static).update(
                "No file operations are available for rollback."
            )
            return
        value = await self.push_screen_wait(
            Prompt("Select a completed file operation\n" + "\n".join(history_line(r) for r in rows))
        )
        if not value or value not in {r.operation_id for r in rows}:
            return
        try:
            plan = await self.services.recovery.rollback(UUID(value))
            detail = "\n".join([plan.plan.summary, *(r.description() for r in plan.plan.resources)])
            if await self.push_screen_wait(Confirm(detail, "Restore approved files")):
                result = await self.services.operations.apply(plan)
                self.query_one("#feedback", Static).update(result.message)
        except OperationError as error:
            self.query_one("#feedback", Static).update(error.detail.message)
        except (ValueError, RuntimeError, OSError):
            self.query_one("#feedback", Static).update(
                "The original executor could not confirm this rollback."
            )

    @on(Button.Pressed, "#recover")
    @work(exit_on_error=False)
    async def recover(self) -> None:
        try:
            await self.review_recovery()
        except OperationError as error:
            self.query_one("#feedback", Static).update(error.detail.message)
        except (ValueError, RuntimeError, OSError):
            self.query_one("#feedback", Static).update(
                "Recovery is unavailable. Review the retained evidence."
            )

    async def review_recovery(self) -> None:
        resumable = await self.services.recovery.available(RecoveryAction.RESUME)
        historical = await self.services.recovery.available(RecoveryAction.RECONCILE)
        rows = [*resumable, *historical]
        if not rows:
            self.query_one("#feedback", Static).update("No unfinished operations.")
            return
        value = await self.push_screen_wait(
            Prompt("Operation to inspect\n" + "\n".join(history_line(r) for r in rows))
        )
        if value and value in {r.operation_id for r in historical}:
            plan = await self.services.recovery.preview_reconciliation(UUID(value))
            self.query_one("#feedback", Static).update(plan.result.message)
            if plan.result.outcome is Outcome.COMPLETE and await self.push_screen_wait(
                Confirm(
                    "\n".join(
                        [plan.result.message, *(r.description() for r in plan.result.resources)]
                    ),
                    "Confirm historical reconciliation",
                )
            ):
                result = await self.services.recovery.reconcile(plan)
                self.query_one("#feedback", Static).update(result.message)
            return
        if value and value in {r.operation_id for r in resumable}:
            result = await self.services.recovery.inspect(UUID(value))
            self.query_one("#feedback", Static).update(result.message)
            if result.outcome != Outcome.NEEDS_USER_ACTION:
                return
            resume = await self.services.recovery.plan(UUID(value))
            lines = ["Verify and finish operation " + value]
            for step in resume.snapshot.plan.steps:
                if step.step_key in resume.remaining:
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
                    prompt = SecretPrompt(
                        f"Authorize {request.executor.plugin_id} for {request.operation_id}"
                    )
                    response = await prompt.ask(self)
                    return SecretBytes(response.get_secret_value().encode()) if response else None

                resumed = await self.services.recovery.apply(resume, authorize)
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
                Prompt("Independently verified SHA-256 of this release (blank: latest available)")
            )
            if digest is not None:
                feedback = self.query_one("#feedback", Static)

                async def progress(message: str) -> None:
                    feedback.update(message)

                state = await self.services.plugins.activate(
                    release,
                    digest or release.digest,
                    Path(link).parent if link.startswith("/") else None,
                    progress,
                )
                await self.reload_catalog()
                feedback.update(f"{state.manifest.name} {state.manifest.version} is active.")
        except OperationError as error:
            self.query_one("#feedback", Static).update(error.detail.message)
        except ValueError as error:
            self.query_one("#feedback", Static).update(str(error))
        except (OSError, RuntimeError):
            self.query_one("#feedback", Static).update(
                "Activation failed. Check the origin, digest and dependencies."
            )
