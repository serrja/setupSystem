"""Schema-driven forms bound to a single plugin's application services."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import SecretBytes
from setup_system_sdk.enums.operation import ActionMode, OperationState, Outcome, WorkerMethod
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
from setup_system.errors import OperationError, normalize_failure
from setup_system.ui.error_presenter import ErrorPanel
from setup_system.ui.features import Feature, declared_features
from setup_system.ui.help import Help
from setup_system.ui.operation_state import availability
from setup_system.ui.panels import panel_type
from setup_system.ui.plugin_dialogs import Confirm, Prompt, SecretPrompt
from setup_system.ui.schema_fields import SchemaForm


class PluginPage(VerticalScroll):
    def __init__(
        self,
        runtime: Runtime,
        entry: CatalogEntry,
        reload: Callable[[str | None], Awaitable[CatalogEntry | None]],
    ) -> None:
        super().__init__()
        if entry.manifest is None:
            raise ValueError("Unavailable plugin")
        self.services = runtime
        self.entry = entry
        self.manifest = entry.manifest
        self.view = declared_features(self.manifest, entry.source)
        self.blocked_actions: dict[str, str] = {}
        self.reload_catalog = reload
        self.revision = ""
        self.operation: UUID | None = None
        self.state = OperationState.IDLE
        self.activated = False

    def compose(self) -> ComposeResult:
        yield Static(f"{self.manifest.name} {self.manifest.version}", markup=False)
        if self.entry.release:
            yield Button("Review activation", id="activate")
            yield Static("Available · activation required", markup=False)
            yield ErrorPanel("", id="result", markup=False)
            return
        if self.view.features[Feature.HELP].supported:
            yield Button("Help", id="plugin-help", disabled=True)
        yield Vertical(id="custom-panel")
        if self.view.features[Feature.PROVIDER_STATUS].supported:
            yield Static(
                "Capabilities: " + ", ".join(cap.id for cap in self.manifest.provides),
                markup=False,
            )
            yield Static("Checking provider status…", id="provider-status", markup=False)
        if (
            self.view.features[Feature.ACTIONS].supported
            or self.view.features[Feature.ACCOUNTS].supported
        ):
            yield Button("Inspect", id="inspect", disabled=True)
        yield Button("Enable" if self.entry.disabled else "Disable", id="toggle")
        yield Button("Remove extension", id="remove-extension")
        if self.view.features[Feature.SETTINGS].supported:
            yield Static("Settings")
            yield Vertical(id="settings-form", classes="forms")
        if self.view.features[Feature.SAVE_SETTINGS].supported:
            yield Button("Save settings", id="save", disabled=True)
        if self.view.actions:
            yield Select(
                [(a.label, a.id) for a in self.manifest.actions],
                id="action",
                prompt="Choose an action",
            )
            yield Static("", id="requirements", markup=False)
            yield Vertical(id="input-form", classes="forms")
            yield Button("Preview", id="preview", disabled=True)
            yield Button("Cancel operation", id="cancel-operation", disabled=True)
        yield ErrorPanel("", id="result", markup=False)

    @on(Button.Pressed, "#plugin-help")
    async def help_page(self) -> None:
        try:
            release = await self.services.plugins.active_release(self.manifest.id)
            await self.app.push_screen(Help(release.resources))
        except Exception as failure:
            await self._show_failure(failure, stage="help")

    async def choices(self, provider: str, query: str) -> list[str]:
        settings = self.query("#settings-form SchemaForm")
        reply = await self.services.plugins.query(
            Request(
                plugin_id=self.manifest.id,
                method=WorkerMethod.CHOICES,
                provider=provider,
                query=query,
                overrides=(
                    self.query_one("#settings-form SchemaForm", SchemaForm).values(explicit=True)
                    if settings
                    else {}
                ),
            )
        )
        return list(reply.choices)

    def update_controls(self) -> None:
        if self.entry.release or not self.is_mounted:
            return
        for feature in self.view.features.values():
            feature.ready = feature.supported and self.activated
        for action_id, action in self.view.actions.items():
            action.ready = self.activated and action_id not in self.blocked_actions
            action.reason = self.blocked_actions.get(action_id, "")
        action_control = self.query("#action")
        selected = self.query_one("#action", Select).value if action_control else None
        current_action = self.view.actions.get(selected) if isinstance(selected, str) else None
        controls = availability(
            self.state,
            activated=self.activated,
            selected=bool(current_action and current_action.ready),
        )
        for button in ("#inspect", "#save", "#plugin-help"):
            for control in self.query(button):
                if isinstance(control, Button):
                    control.disabled = not self.activated
        for control in self.query("#preview"):
            if isinstance(control, Button):
                control.disabled = not controls.preview
        for control in self.query("#cancel-operation"):
            if isinstance(control, Button):
                control.disabled = not controls.stop
        for status in self.query("#requirements"):
            if isinstance(status, Static):
                status.update(current_action.reason if current_action else "")

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
        await self.load_active()
        if self.view.actions:
            try:
                self.blocked_actions = await self.services.plugins.action_requirements(
                    self.manifest
                )
                self.update_controls()
            except Exception as failure:
                await self._show_failure(failure, stage="availability")

    async def load_active(self) -> None:
        """Populate controls for the currently activated catalog entry."""
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
                    if self.query("#action"):
                        self.query_one("#action", Select).value = key

                await self.query_one("#custom-panel", Vertical).mount(
                    kind(UIContext(self.manifest.id, select))
                )
        except (ValueError, OSError) as failure:
            await self._show_failure(failure, stage="plugin_panel")
        try:
            saved = await self.services.plugins.profile(self.manifest.id)
        except OperationError as error:
            self.query_one("#result", ErrorPanel).set_error(error.detail)
            return
        except ValueError as failure:
            await self._show_failure(failure, stage="configuration")
            return
        self.revision = saved.revision
        if self.view.features[Feature.SETTINGS].supported:
            form = SchemaForm(self.manifest.settings_schema, saved.values, self.choices)
            form.save_secret = lambda secret: store_secret(self.manifest.id, secret)
            await self.query_one("#settings-form", Vertical).mount(form)
        self.activated = True
        for status in self.query("#provider-status"):
            if isinstance(status, Static):
                status.update("Active provider")
        self.update_controls()

    @on(Select.Changed, "#action")
    async def select_action(self, event: Select.Changed) -> None:
        self.update_controls()
        area = self.query_one("#input-form", Vertical)
        await area.remove_children()
        if isinstance(event.value, str):
            action = next(item for item in self.manifest.actions if item.id == event.value)
            label = {
                ActionMode.READ_ONLY: "Run read-only check",
                ActionMode.MANUAL: "Open instructions",
            }.get(action.mode, "Preview")
            self.query_one("#preview", Button).label = label
            schema = self.manifest.input_schemas[event.value]
            if not schema.get("properties"):
                return
            form = SchemaForm(schema, {}, self.choices)
            form.save_secret = lambda secret: store_secret(self.manifest.id, secret)
            await area.mount(form)

    async def authorize(self, request: AuthorizationRequest) -> SecretBytes | None:
        prompt = SecretPrompt(
            f"Authorize {request.executor.plugin_id} · {request.step_key}\n"
            f"Operation {request.operation_id}"
        )
        secret = await prompt.ask(self.app)
        return SecretBytes(secret.get_secret_value().encode()) if secret else None

    async def _show_failure(self, failure: Exception, *, stage: str) -> None:
        detail = normalize_failure(failure, stage=stage, component=self.manifest.id)
        for result in self.query("#result"):
            if isinstance(result, ErrorPanel):
                result.set_error(detail)
        for editor in self.query(SchemaForm):
            editor.show_error(detail.field, detail.message)
        try:
            executor = (await self.services.plugins.active_release(self.manifest.id)).executor
        except Exception:
            executor = None
        try:
            await self.services.journal.emit_failure(
                detail,
                failure,
                stage=stage,
                plugin_id=self.manifest.id,
                executor=executor,
            )
        except Exception:
            pass

    @on(Button.Pressed)
    @work(exit_on_error=False)
    async def command(self, event: Button.Pressed) -> None:
        try:
            button = event.button.id
            plugin = self.manifest.id
            display = self.query_one("#result", ErrorPanel)
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
                        Prompt("Independently verified release SHA-256 (blank: latest available)")
                    )
                    if digest is not None:

                        async def progress(message: str) -> None:
                            display.update(message)

                        await self.services.plugins.activate(
                            release,
                            digest or release.digest,
                            self.entry.source.parent,
                            progress,
                        )
                        entry = await self.reload_catalog(plugin)
                        if entry is not None and entry.manifest is not None:
                            self.entry = entry
                            self.manifest = entry.manifest
                            self.view = declared_features(self.manifest, entry.source)
                            await self.recompose()
                            await self.load_active()
                            self.query_one("#result", ErrorPanel).update(
                                f"{self.manifest.name} {self.manifest.version} is active."
                            )
                            if self.view.actions:
                                self.blocked_actions = (
                                    await self.services.plugins.action_requirements(self.manifest)
                                )
                                self.update_controls()
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
                await self.reload_catalog(None)
                await self.remove()
            elif button == "remove-extension":
                files = await self.services.plugins.removal_files(plugin)
                if await self.app.push_screen_wait(Confirm("\n".join(files), "Remove extension")):
                    await self.services.plugins.remove(plugin, files)
                    await self.reload_catalog(None)
                    await self.remove()
            elif button == "cancel-operation" and self.operation:
                self.services.operations.cancel(self.operation)
            elif button == "preview":
                selection = self.query_one("#action", Select).value
                selected = self.view.actions.get(selection) if isinstance(selection, str) else None
                if (
                    not availability(
                        self.state,
                        activated=self.activated,
                        selected=bool(selected and selected.ready),
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
        except Exception as failure:
            await self._show_failure(failure, stage="plugin_action")

    async def review_action(self, plugin: str, display: Static) -> None:
        action = self.query_one("#action", Select).value
        if not isinstance(action, str):
            return
        snapshot = await self.services.operations.preview(
            plugin,
            action,
            (
                self.query_one("#settings-form SchemaForm", SchemaForm).values(explicit=True)
                if self.query("#settings-form SchemaForm")
                else {}
            ),
            (
                self.query_one("#input-form SchemaForm", SchemaForm).values(visible_only=True)
                if self.query("#input-form SchemaForm")
                else {}
            ),
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
