"""Schema-driven forms bound to a single plugin's application services."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import SecretBytes
from setup_system_sdk.enums.operation import OperationState, Outcome, WorkerMethod
from setup_system_sdk.schemas.wire import Request
from setup_system_sdk.secrets import store_secret
from setup_system_sdk.ui import UIContext
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Select, Static

from setup_system.application.authorization import AuthorizationRequest
from setup_system.application.plugins import CatalogEntry
from setup_system.bootstrap import Runtime
from setup_system.errors import OperationError
from setup_system.ui.help import Help
from setup_system.ui.operation_state import availability
from setup_system.ui.panels import panel_type
from setup_system.ui.plugin_dialogs import Confirm, Prompt, SecretPrompt
from setup_system.ui.schema_fields import SchemaForm


class PluginPage(VerticalScroll):
    def __init__(
        self, runtime: Runtime, entry: CatalogEntry, reload: Callable[[], Awaitable[None]]
    ) -> None:
        super().__init__()
        if entry.manifest is None:
            raise ValueError("Unavailable plugin")
        self.services = runtime
        self.entry = entry
        self.manifest = entry.manifest
        self.reload_catalog = reload
        self.revision = ""
        self.operation: UUID | None = None
        self.state = OperationState.IDLE
        self.activated = False

    def compose(self) -> ComposeResult:
        yield Static(f"{self.manifest.name} {self.manifest.version}", markup=False)
        if self.entry.release:
            yield Button("Review activation", id="activate")
            yield Static("", id="result", markup=False)
            return
        yield Button("Help", id="plugin-help", disabled=True)
        yield Vertical(id="custom-panel")
        yield Button("Inspect", id="inspect", disabled=True)
        yield Button("Enable" if self.entry.disabled else "Disable", id="toggle")
        yield Button("Remove extension", id="remove-extension")
        yield Static("Settings")
        yield Vertical(id="settings-form", classes="forms")
        yield Button("Save settings", id="save", disabled=True)
        yield Select(
            [(a.label, a.id) for a in self.manifest.actions], id="action", prompt="Choose an action"
        )
        yield Vertical(id="input-form", classes="forms")
        yield Button("Preview", id="preview", disabled=True)
        yield Button("Cancel operation", id="cancel-operation", disabled=True)
        yield Static("", id="result", markup=False)

    @on(Button.Pressed, "#plugin-help")
    async def help_page(self) -> None:
        release = await self.services.plugins.active_release(self.manifest.id)
        await self.app.push_screen(Help(release.resources))

    async def choices(self, provider: str, query: str) -> list[str]:
        reply = await self.services.plugins.query(
            Request(
                plugin_id=self.manifest.id,
                method=WorkerMethod.CHOICES,
                provider=provider,
                query=query,
                overrides=self.query_one("#settings-form SchemaForm", SchemaForm).values(
                    explicit=True
                ),
            )
        )
        return list(reply.choices)

    def update_controls(self) -> None:
        if self.entry.release or not self.is_mounted:
            return
        controls = availability(
            self.state,
            activated=self.activated,
            selected=isinstance(self.query_one("#action", Select).value, str),
        )
        for button in ("#inspect", "#save", "#plugin-help"):
            self.query_one(button, Button).disabled = not self.activated
        self.query_one("#preview", Button).disabled = not controls.preview
        self.query_one("#cancel-operation", Button).disabled = not controls.stop

    @work(exit_on_error=False)
    async def watch_operation(self, operation: UUID) -> None:
        done = self.services.operations.finished.get(operation)
        if done:
            await done.wait()
        if self.operation == operation:
            self.operation = None
            self.state = OperationState.IDLE
            self.update_controls()

    async def on_mount(self) -> None:
        self.operation = self.services.operations.active.get(self.manifest.id)
        if self.operation:
            self.state = OperationState.RUNNING
            self.watch_operation(self.operation)
        self.update_controls()
        if self.entry.disabled or self.entry.release:
            return
        try:
            release = await self.services.plugins.active_release(self.manifest.id)
            kind = panel_type(
                release.resources,
                release.manifest,
                release.digest,
                self.services.plugins.startup_ui.get(self.manifest.id),
            )
            if kind:

                def select(key: str) -> None:
                    self.query_one("#action", Select).value = key

                await self.query_one("#custom-panel", Vertical).mount(
                    kind(UIContext(self.manifest.id, select))
                )
        except (ValueError, OSError):
            pass
        try:
            saved = await self.services.plugins.profile(self.manifest.id)
        except OperationError as error:
            self.query_one("#result", Static).update(error.detail.message)
            return
        except ValueError:
            self.query_one("#result", Static).update(
                "Activate a reviewed release to open this plugin."
            )
            return
        self.revision = saved.revision
        form = SchemaForm(self.manifest.settings_schema, saved.values, self.choices)
        form.save_secret = lambda secret: store_secret(self.manifest.id, secret)
        await self.query_one("#settings-form", Vertical).mount(form)
        self.activated = True
        self.update_controls()

    @on(Select.Changed, "#action")
    async def select_action(self, event: Select.Changed) -> None:
        self.update_controls()
        area = self.query_one("#input-form", Vertical)
        await area.remove_children()
        if isinstance(event.value, str):
            form = SchemaForm(self.manifest.input_schemas[event.value], {}, self.choices)
            form.save_secret = lambda secret: store_secret(self.manifest.id, secret)
            await area.mount(form)

    async def authorize(self, request: AuthorizationRequest) -> SecretBytes | None:
        prompt = SecretPrompt(
            f"Authorize {request.executor.plugin_id} · {request.step_key}\n"
            f"Operation {request.operation_id}"
        )
        secret = await prompt.ask(self.app)
        return SecretBytes(secret.get_secret_value().encode()) if secret else None

    @on(Button.Pressed)
    @work(exit_on_error=False)
    async def command(self, event: Button.Pressed) -> None:
        button = event.button.id
        plugin = self.manifest.id
        display = self.query_one("#result", Static)
        try:
            if button == "activate" and self.entry.source and self.entry.release:
                release = self.entry.release
                summary = [
                    self.manifest.name,
                    self.manifest.version,
                    self.manifest.source,
                    *self.manifest.permissions,
                    release.digest,
                    *(f"{w.name} · {w.sha256}" for w in release.selected().wheels),
                ]
                if await self.app.push_screen_wait(
                    Confirm("\n".join(summary), "Review activation")
                ):
                    digest = await self.app.push_screen_wait(
                        Prompt("Independently verified release SHA-256")
                    )
                    if digest:
                        await self.services.plugins.activate(
                            release, digest, self.entry.source.parent
                        )
                        await self.reload_catalog()
            elif button == "inspect":
                reply = await self.services.plugins.query(
                    Request(plugin_id=plugin, method=WorkerMethod.INSPECT)
                )
                if reply.result:
                    display.update(reply.result.message)
            elif button == "save":
                form = self.query_one("#settings-form SchemaForm", SchemaForm)
                saved = await self.services.plugins.save(
                    plugin, form.values(explicit=True), self.revision
                )
                self.revision = saved.revision
                display.update("Settings saved.")
            elif button == "toggle":
                await self.services.plugins.enable(plugin, self.entry.disabled)
                await self.reload_catalog()
                await self.remove()
            elif button == "remove-extension":
                files = await self.services.plugins.removal_files(plugin)
                if await self.app.push_screen_wait(Confirm("\n".join(files), "Remove extension")):
                    await self.services.plugins.remove(plugin, files)
                    await self.reload_catalog()
                    await self.remove()
            elif button == "cancel-operation" and self.operation:
                self.services.operations.cancel(self.operation)
            elif button == "preview":
                if (
                    not availability(
                        self.state,
                        activated=self.activated,
                        selected=isinstance(self.query_one("#action", Select).value, str),
                    ).preview
                    or self.operation is not None
                ):
                    return
                self.state = OperationState.PLANNING
                self.update_controls()
                for editor in self.query(SchemaForm):
                    editor.clear_errors()
                try:
                    await self.review_action(plugin, display)
                finally:
                    if self.operation is None:
                        self.state = OperationState.IDLE
                    self.update_controls()
        except OperationError as error:
            display.update(error.detail.message)
            for editor in self.query(SchemaForm):
                editor.show_error(error.detail.field, error.detail.message)
        except (RuntimeError, OSError, ValueError):
            display.update("Operation incomplete. Review the release, settings and history.")

    async def review_action(self, plugin: str, display: Static) -> None:
        action = self.query_one("#action", Select).value
        if not isinstance(action, str):
            return
        snapshot = await self.services.operations.preview(
            plugin,
            action,
            self.query_one("#settings-form SchemaForm", SchemaForm).values(explicit=True),
            self.query_one("#input-form SchemaForm", SchemaForm).values(visible_only=True),
        )
        display.update(snapshot.plan.summary)
        if (
            snapshot.plan.outcome not in (Outcome.READY, Outcome.NO_CHANGES)
            or not snapshot.plan.steps
        ):
            return
        review = [snapshot.plan.summary, *snapshot.plan.warnings]
        for step in snapshot.plan.steps:
            review.extend(
                [
                    f"{step.step_key} · {step.executor.plugin_id} "
                    f"{step.executor.version} · "
                    f"{step.executor.release_digest}",
                    ", ".join(sorted(step.permissions)),
                    *(r.description() for r in step.resources),
                ]
            )
        self.state = OperationState.AUTHENTICATING
        self.update_controls()
        if await self.app.push_screen_wait(Confirm("\n".join(review), "Apply confirmed plan")):
            if self.operation is not None or plugin in self.services.operations.active:
                return
            self.operation = snapshot.operation_id
            self.state = OperationState.RUNNING
            self.update_controls()
            try:
                result = await self.services.operations.apply(snapshot, self.authorize)
                display.update(result.message)
            finally:
                if self.operation == snapshot.operation_id:
                    if self.services.operations.active.get(plugin) != snapshot.operation_id:
                        self.operation = None
                    elif self.is_mounted:
                        self.watch_operation(snapshot.operation_id)
