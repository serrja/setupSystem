"""A persistent plugin workspace built from its reviewed package metadata."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Awaitable, Callable
from uuid import UUID
from zipfile import ZipFile

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, ContentSwitcher, Select, Static, TabbedContent, TabPane

from setup_system.errors import OperationError
from setup_system.execution.authorization import BecomePassword
from setup_system.execution.native_runner import Snapshot, descriptor, protected_resources
from setup_system.execution.worker import query
from setup_system.services.cleanup import apply_cleanup, preview_cleanup, save_keyring_entry
from setup_system.services.extension_lifecycle import removable, retire, toggle
from setup_system.services.extensions import CatalogEntry, active
from setup_system.services.operations import (
    PRIVILEGED,
    VERIFIED,
    Operations,
    can_review,
    track_existing,
)
from setup_system.services.plugin_ui import panel_type
from setup_system.services.rollback import RecoveryPlan
from setup_system.services.settings import SavedValues, SettingsStore
from setup_system.ui.operation_state import availability
from setup_system.ui.plugin_dialogs import Choose, Confirm, SecretPrompt
from setup_system.ui.schema_fields import SchemaForm
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.enums.operation import ActionMode, OperationState, Outcome, WorkerMethod
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.errors import Error
from setup_system_sdk.schemas.manifest import Action
from setup_system_sdk.schemas.wire import Request
from setup_system_sdk.ui import UIContext


def help_text(entry: CatalogEntry, article: str = "help.md") -> str:
    source = entry.source
    manifest = entry.manifest
    if not source or not manifest:
        return entry.reason
    module = manifest.entry_point.partition(":")[0].partition(".")[0]
    if source.suffix == ".whl":
        with ZipFile(source) as archive:
            with archive.open(module + "/" + article) as stream:
                return stream.read(65536).decode()
    if source.name == "pyproject.toml":
        for prefix in ("src", ""):
            path = source.parent / prefix / module / article
            if path.is_file():
                return path.read_text()[:65536]
    return (source / article).read_text()[:65536]


class PluginPage(Vertical):
    def __init__(
        self,
        entry: CatalogEntry,
        manager: Operations,
        store: SettingsStore,
        activate: Callable[[CatalogEntry], Awaitable[None]],
        startup_digest: str | None = None,
        candidate: CatalogEntry | None = None,
    ) -> None:
        assert entry.manifest
        super().__init__(id="plugin-" + entry.manifest.id)
        self.startup_digest = startup_digest
        self.entry = entry
        self.candidate = candidate
        self.manifest = entry.manifest
        self.manager = manager
        self.store = store
        self.activate = activate
        self.state = OperationState.IDLE
        self.revision = 0
        self.activated = False
        self.saved: SavedValues | None = None
        self.settings_form: SchemaForm | None = None
        self.snapshot: Snapshot | None = None
        self.forms: dict[str, SchemaForm] = {}
        self.choice_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}
        self.active_action: Action | None = None
        self.status_line = Static("", markup=False, classes="feedback")
        self.observations = Static("", markup=False)
        self.review = Static("", markup=False)
        self.reference = Static("", markup=False)

    def compose(self) -> ComposeResult:
        manifest = self.manifest
        yield Static(manifest.name, markup=False, classes="title")
        with TabbedContent():
            with TabPane("Overview", id="overview"):
                with VerticalScroll():
                    yield Static(
                        f"{manifest.category} · {manifest.version}\n"
                        f"{manifest.author}\n{manifest.source}\n"
                        f"Requested permissions: {', '.join(manifest.permissions) or 'none'}",
                        markup=False,
                    )
                    yield Static(id="extension-state", markup=False)
                    yield Vertical(id="custom-panel")
                    yield Button(
                        "Activate / update extension",
                        id="activate",
                        disabled=self.candidate is None or self.candidate.release is None,
                    )
                    yield Button("Inspect program", id="inspect")
                    yield self.observations
                    yield Button("Disable extension", id="disable")
                    yield Button("Enable extension", id="enable")
                    yield Button("Remove extension", id="remove-extension")
                    yield Button("Full cleanup", id="full-cleanup")
            with TabPane("Settings", id="settings"):
                with VerticalScroll():
                    yield Vertical(id="settings-form")
                    yield Button("Save settings", id="save", variant="primary")
            if any(a.page == "accounts" for a in manifest.actions):
                with TabPane("Accounts", id="accounts"):
                    with VerticalScroll():
                        for i, action in enumerate(manifest.actions):
                            if action.page == "accounts":
                                yield Button(
                                    action.label, id=f"account-{i}", classes="select-action"
                                )
            with TabPane("Actions", id="actions"):
                with VerticalScroll():
                    yield Select[str](
                        [(a.label, a.id) for a in manifest.actions],
                        allow_blank=True,
                        prompt="Choose action",
                        id="action-choice",
                    )
                    yield Static(id="action-description", markup=False)
                    yield ContentSwitcher(id="action-forms")
                    yield Button("Preview action", id="preview", variant="primary")
                    yield Button("Stop at a safe boundary", id="cancel-action")
                    yield self.review
                    yield Static("", id="availability", markup=False)
                    yield Button("Review and apply", id="apply", disabled=True, variant="warning")
            with TabPane("Run parameters", id="parameters"):
                with VerticalScroll():
                    yield Static(
                        "Allowed flags and environment values for the selected action.",
                        markup=False,
                    )
                    yield ContentSwitcher(id="run-forms")
            with TabPane("Managed resources", id="resources"):
                with VerticalScroll():
                    yield Static(id="holdings", markup=False)
                    yield Button("Refresh resources", id="refresh-resources")
                    yield Button("Check interrupted operations", id="reconcile")
                    yield Button("Roll back a file operation", id="rollback", disabled=True)
            with TabPane("History", id="history"):
                with VerticalScroll():
                    yield Static(id="events", markup=False)
                    yield Button("Refresh history", id="refresh-history")
            with TabPane("Help", id="help"):
                with VerticalScroll():
                    yield Static(id="article", markup=False)
                    yield Button("Field reference", id="field-reference")
                    yield self.reference
        yield self.status_line

    async def on_mount(self) -> None:
        try:
            await self.load_settings()
            self.query_one("#article", Static).update(
                await asyncio.to_thread(help_text, self.entry)
            )
            await self.refresh_state()
        except (ValueError, OSError, OperationError):
            self.status_line.update(
                "Plugin resources are unavailable. Check the installed package."
            )
        self.render_controls()

    async def refresh_state(self) -> None:
        try:
            state = await self.manager.activation(self.manifest.id)
            self.activated = state.manifest == self.manifest
            if not self.activated:
                raise ValueError("The extension metadata changed. Reopen the application.")
            container = self.query_one("#custom-panel", Vertical)
            if not container.children:
                panel = await asyncio.to_thread(panel_type, state, self.startup_digest)
                if panel:
                    await container.mount(panel(UIContext(self.manifest.id, self.show_action)))
            text = f"Active extension {state.manifest.version} · {state.digest[:12]}"
        except OperationError as failure:
            self.activated = False
            text = failure.detail.message
        except ValueError:
            text = self.entry.reason or (
                "Extension disabled"
                if self.entry.disabled
                else "The release changed. Reopen the application to load the verified UI."
            )
        self.query_one("#extension-state", Static).update(text)
        self.query_one("#rollback", Button).disabled = not await self.manager.records.recoveries(
            self.manifest.id
        )

        self.render_controls()

    def render_controls(self) -> None:
        controls = availability(
            self.state, activated=self.activated, selected=self.active_action is not None
        )
        self.query_one("#preview", Button).disabled = not controls.preview
        review = self.query_one("#apply", Button)
        review.disabled = not controls.review
        review.label = (
            "Review and apply"
            if self.active_action is None or self.active_action.mode is ActionMode.MUTATION
            else "Review action"
        )
        self.query_one("#cancel-action", Button).disabled = not controls.stop
        self.query_one("#availability", Static).update(controls.reason)

    def invalidate(self) -> None:
        self.revision += 1
        self.snapshot = None
        self.review.update("")
        if self.state not in (OperationState.RUNNING, OperationState.AUTHENTICATING):
            self.state = OperationState.IDLE
        self.render_controls()

    @on(SchemaForm.Edited)
    def edited(self, event: SchemaForm.Edited) -> None:
        event.stop()
        for form in self.query(SchemaForm):
            form.clear_errors()
        self.invalidate()

    def show_action(self, action: str) -> None:
        if any(item.id == action for item in self.manifest.actions):
            self.query_one("#action-choice", Select).value = action
            self.query_one(TabbedContent).active = "actions"

    async def dynamic_choices(self, provider: str, query_text: str) -> list[str]:
        state = await self.manager.activation(self.manifest.id)
        request = Request(
            method=WorkerMethod.CHOICES,
            plugin_id=self.manifest.id,
            provider=provider,
            query=query_text,
            saved=self.saved.values if self.saved else {},
            overrides=self.unsaved_values(),
        )
        key = (
            state.digest,
            hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
        )
        cached = self.choice_cache.get(key)
        if cached and time.monotonic() - cached[0] < 2:
            return cached[1]
        result = await query(state, request, self.manager.options)
        values = list(result.choices[: self.manager.options.suggestion_results])
        if len(self.choice_cache) >= self.manager.options.suggestion_cache_directories:
            self.choice_cache.pop(next(iter(self.choice_cache)))
        self.choice_cache[key] = (time.monotonic(), values)
        return values

    async def load_settings(self) -> None:
        self.saved = await self.store.read(self.manifest.id)
        if self.saved.values and self.saved.version != self.manifest.settings_version:
            raise ValueError("Saved settings require a schema migration.")
        container = self.query_one("#settings-form", Vertical)
        await container.remove_children()
        form = SchemaForm(self.manifest.settings_schema, self.saved.values, self.dynamic_choices)
        form.plugin_id = self.manifest.id
        form.save_secret = lambda secret: save_keyring_entry(self.manager, self.manifest.id, secret)
        self.settings_form = form
        await container.mount(form)

    @on(Select.Changed, "#action-choice")
    async def selected_action(self, event: Select.Changed) -> None:
        self.active_action = None
        self.invalidate()
        if not isinstance(event.value, str):
            self.query_one("#action-description", Static).update("")
            self.query_one("#action-forms", ContentSwitcher).current = None
            self.query_one("#run-forms", ContentSwitcher).current = None
            return
        i, action = next((i, a) for i, a in enumerate(self.manifest.actions) if a.id == event.value)
        self.active_action = action
        self.render_controls()
        self.query_one("#action-description", Static).update(action.description or action.label)
        schema = self.manifest.input_schemas[action.id]
        props = schema.get("properties")
        if not isinstance(props, dict):
            props = {}
        for container, fields, group in (
            (
                "#action-forms",
                {k: v for k, v in props.items() if k not in ("flags", "environment")},
                "input",
            ),
            (
                "#run-forms",
                {k: v for k, v in props.items() if k in ("flags", "environment")},
                "run",
            ),
        ):
            key = f"{group}-{i}"
            switcher = self.query_one(container, ContentSwitcher)
            if key not in self.forms:
                form = SchemaForm({**schema, "properties": fields}, {}, self.dynamic_choices)
                form.plugin_id = self.manifest.id
                form.save_secret = lambda secret: save_keyring_entry(
                    self.manager, self.manifest.id, secret
                )
                form.id = key
                self.forms[key] = form
                await switcher.mount(form)
            switcher.current = key

    def unsaved_values(self) -> Values:
        values = self.settings_form.values(explicit=True) if self.settings_form else {}
        saved = self.saved.values if self.saved else {}
        return {
            key: value for key, value in values.items() if key not in saved or saved[key] != value
        }

    def _inputs(self) -> Values:
        if self.active_action is None:
            raise ValueError("Choose an action")
        index = self.manifest.actions.index(self.active_action)
        return {
            **self.forms[f"input-{index}"].values(visible_only=True),
            **self.forms[f"run-{index}"].values(visible_only=True),
        }

    @on(Button.Pressed)
    def button(self, event: Button.Pressed) -> None:
        event.stop()
        name = event.button.id or ""
        if name.startswith("account-"):
            item = self.manifest.actions[int(name.partition("-")[2])]
            self.query_one("#action-choice", Select).value = item.id
            self.query_one(TabbedContent).active = "actions"
            return
        self.execute_command(name)

    @work(exit_on_error=False)
    async def execute_command(self, name: str) -> None:
        revision = self.revision
        try:
            await self._command(name)
        except OperationError as exc:
            if name not in ("preview", "apply") and self.state in (
                OperationState.RUNNING,
                OperationState.AUTHENTICATING,
            ):
                self.status_line.update(exc.detail.message)
            elif revision == self.revision:
                self.show_error(exc.detail)
        except (ValueError, OSError, RuntimeError, ExceptionGroup):
            if name not in ("preview", "apply") and self.state in (
                OperationState.RUNNING,
                OperationState.AUTHENTICATING,
            ):
                self.status_line.update(
                    "Review the operation status before changing this extension."
                )
            elif revision == self.revision:
                self.show_error(
                    Error(
                        code=ErrorCode.CONFLICT,
                        message="Review the extension and saved settings, then preview again.",
                        retryable=True,
                    )
                )

    def show_error(self, error: Error) -> None:
        self.snapshot = None
        self.state = OperationState.FAILED
        if error.field:
            for form in self.query(SchemaForm):
                form.show_error(error.field, error.message)
        location = ".".join(str(key) for key in error.field)
        self.status_line.update(
            " · ".join(
                part
                for part in (
                    error.code.value,
                    location,
                    error.message,
                    str(error.operation_id) if error.operation_id else "",
                )
                if part
            )
        )
        self.render_controls()

    async def authorize(self, action: Action) -> BecomePassword | None:
        if not PRIVILEGED.intersection(action.permissions):
            return None
        state = await self.manager.activation(self.manifest.id)
        await asyncio.to_thread(protected_resources, state)
        secret = await self.app.push_screen_wait(
            SecretPrompt(
                "Root password for su"
                if Permission.SU in action.permissions
                else "Sudo password for this action"
            )
        )
        return BecomePassword(secret.get_secret_value()) if secret else None

    async def _command(self, name: str) -> None:
        plugin = self.manifest.id
        if name == "field-reference":
            self.reference.update(
                await asyncio.to_thread(help_text, self.entry, "settings_reference.md")
            )
        elif name == "cancel-action":
            count = self.manager.cancel(plugin)
            self.status_line.update(
                "Cancellation requested at a safe boundary." if count else "No running operation."
            )
        elif name == "activate":
            self.invalidate()
            if self.candidate is None:
                return
            await self.activate(self.candidate)
            await self.refresh_state()
        elif name in ("enable", "disable"):
            self.invalidate()
            async with self.manager.records.claim_extension(plugin):
                if await self.manager.records.pending(plugin):
                    raise OperationError(
                        Error(
                            code=ErrorCode.CONFLICT,
                            message="Resolve the unfinished operation before changing activation.",
                        )
                    )
                release = await asyncio.to_thread(active, self.manager.env_root, plugin)
                enable = name == "enable"
                if enable != (release is not None):
                    await asyncio.to_thread(toggle, self.manager.env_root, plugin, enable=enable)
                if enable:
                    release = await asyncio.to_thread(active, self.manager.env_root, plugin)
                    if release is None:
                        raise OperationError(
                            Error(
                                code=ErrorCode.INCOMPATIBLE_PLUGIN,
                                message="Activate the reviewed extension before enabling it.",
                            )
                        )
                    await self.manager.extensions.activate(release.manifest, release.digest)
                else:
                    await self.manager.extensions.disable(plugin)
            await self.refresh_state()
        elif name == "remove-extension":
            try:
                entries = await asyncio.to_thread(removable, self.manager.env_root, plugin)
            except FileNotFoundError:
                if await asyncio.to_thread(os.path.lexists, self.manager.env_root / plugin):
                    raise
                entries = ()
            if await self.app.push_screen_wait(
                Confirm(
                    "Remove extension registration and reviewed code?\n" + "\n".join(entries),
                    "Remove extension",
                )
            ):
                self.invalidate()
                async with self.manager.records.claim_extension(plugin):
                    if await self.manager.records.pending(plugin):
                        raise OperationError(
                            Error(
                                code=ErrorCode.CONFLICT,
                                message="Resolve the unfinished operation before removal.",
                            )
                        )
                    if entries:
                        await asyncio.to_thread(retire, self.manager.env_root, plugin, entries)
                    elif await asyncio.to_thread(os.path.lexists, self.manager.env_root / plugin):
                        raise OperationError(
                            Error(
                                code=ErrorCode.CONFLICT,
                                message="The extension changed after review. Inspect it again.",
                            )
                        )
                    await self.manager.extensions.remove(plugin)
                await self.refresh_state()
        elif name == "full-cleanup":
            cleanup = await preview_cleanup(
                self.manager, self.store, plugin, self.saved.values if self.saved else {}
            )
            if cleanup.blockers:
                self.review.update(cleanup.details())
                self.status_line.update("Cleanup blocked · " + " · ".join(cleanup.blockers))
                return
            if await self.app.push_screen_wait(Confirm(cleanup.details(), "Confirm full cleanup")):
                result = await apply_cleanup(self.manager, self.store, cleanup, self.authorize)
                self.status_line.update(result.outcome.value + " · " + result.message)
                await self.refresh_state()
        elif name == "rollback":
            available = await self.manager.records.recoveries(plugin)
            chosen = await self.app.push_screen_wait(
                Choose("Select a managed-file operation", [(r, str(o)) for o, r in available])
            )
            if chosen:
                recovery = await asyncio.to_thread(
                    RecoveryPlan.inspect, dict(available)[UUID(chosen)]
                )
                if await self.app.push_screen_wait(
                    Confirm(
                        "\n".join(entry.description() for entry in recovery.entries),
                        "Roll back operation",
                    )
                ):
                    result = await self.manager.rollback(plugin, UUID(chosen), recovery)
                    self.status_line.update(result.outcome.value + " · " + result.message)
                    await self.refresh_state()
        elif name == "reconcile":
            results = await self.manager.reconcile(plugin)
            self.status_line.update(
                " · ".join(r.message for r in results) or "No interrupted operations."
            )
        elif name == "inspect":
            self.observations.update("")
            state = await self.manager.activation(plugin)
            reply = await query(
                state,
                Request(
                    method=WorkerMethod.INSPECT,
                    plugin_id=plugin,
                    overrides=self.settings_form.values(explicit=True)
                    if self.settings_form
                    else {},
                ),
                self.manager.options,
            )
            if reply.result:
                self.observations.update("\n".join(r.description() for r in reply.result.resources))
                self.status_line.update(reply.result.outcome.value + " · " + reply.result.message)
        elif name == "save" and self.settings_form and self.saved:
            state = await self.manager.activation(plugin)
            values = self.settings_form.values(explicit=True)
            await query(
                state,
                Request(method=WorkerMethod.VALIDATE, plugin_id=plugin, overrides=values),
                self.manager.options,
            )
            self.saved = await self.store.write(
                plugin, values, self.manifest.settings_version, self.saved.revision
            )
            self.settings_form.stored = dict(self.saved.values)
            self.invalidate()
            self.status_line.update("Settings saved.")
        elif name == "preview":
            await self.preview()
        elif name == "apply":
            await self.apply()
        elif name == "refresh-resources":
            resources = await self.manager.records.holdings(plugin)
            self.query_one("#holdings", Static).update(
                "\n".join(f"{r.kind} · {r.identity} · {r.ownership}" for r in resources)
                or "No verified resources."
            )
        elif name == "refresh-history":
            events = await self.manager.records.events(plugin, limit=200)
            self.query_one("#events", Static).update(
                "\n".join(
                    f"{e.timestamp:%Y-%m-%d %H:%M} UTC · {e.action_id} · {e.message_code}"
                    for e in events
                )
                or "No recorded operations."
            )

    async def preview(self) -> None:
        action = self.active_action
        controls = availability(self.state, activated=self.activated, selected=action is not None)
        if not controls.preview or action is None or self.saved is None:
            return
        self.revision += 1
        revision = self.revision
        self.snapshot = None
        self.state = OperationState.VALIDATING
        self.render_controls()
        try:
            inputs = self._inputs()
            overrides = self.unsaved_values()
            self.state = OperationState.PLANNING
            self.render_controls()
            snapshot = await self.manager.preview(
                self.manifest.id,
                action.id,
                overrides,
                inputs,
                saved=self.saved.values,
                revision=self.saved.revision,
            )
        except OperationError as exc:
            if revision == self.revision:
                self.show_error(exc.detail)
            return
        except Exception:
            if revision == self.revision:
                self.show_error(
                    Error(
                        code=ErrorCode.WORKER_FAILED,
                        message="Could not prepare the plan. Check the release and local storage.",
                        retryable=True,
                    )
                )
            return
        if revision != self.revision:
            return
        plan = snapshot.mutation.plan
        self.review.update(
            "\n".join(
                [plan.summary, *plan.warnings, *(row.description() for row in plan.resources)]
            )
        )
        if can_review(action, plan):
            self.snapshot = snapshot
            self.state = OperationState.READY
        else:
            self.state = (
                OperationState.SUCCEEDED if plan.outcome in VERIFIED else OperationState.BLOCKED
            )
        self.status_line.update(plan.outcome.value)
        self.render_controls()

    async def apply(self) -> None:
        snapshot = self.snapshot
        if self.state is not OperationState.READY or snapshot is None:
            return
        revision = self.revision
        self.snapshot = None
        self.state = OperationState.AUTHENTICATING
        self.render_controls()
        password = None
        try:
            release = await self.manager.activation(self.manifest.id)
            action = descriptor(release, snapshot.mutation.action_id)
            plan = snapshot.mutation.plan
            details = [
                action.label,
                plan.summary,
                *plan.warnings,
                *(r.description() for r in plan.resources),
            ]
            details.append("Permissions: " + (", ".join(action.permissions) or "none"))
            if action.mode is ActionMode.MUTATION and not action.supports_rollback:
                details.append("Operation rollback is unavailable.")
            label = "Confirm apply" if action.mode is ActionMode.MUTATION else "Continue"
            if not await self.app.push_screen_wait(Confirm("\n".join(details), label)):
                self.state = OperationState.CANCELLED
                return
            if revision != self.revision:
                self.state = OperationState.IDLE
                return
            if (
                action.mode in (ActionMode.MUTATION, ActionMode.AUTHENTICATED_CHECK)
                and not track_existing(action, plan)
                and PRIVILEGED.intersection(action.permissions)
            ):
                password = await self.authorize(action)
                if password is None:
                    self.state = OperationState.CANCELLED
                    self.status_line.update("Authorization cancelled.")
                    return
            if revision != self.revision:
                self.state = OperationState.IDLE
                return
            self.state = OperationState.RUNNING
            self.render_controls()
            execution_password, password = password, None
            result = await self.manager.apply(snapshot, execution_password)
            self.status_line.update(result.outcome.value + " · " + result.message)
            if revision != self.revision:
                self.state = OperationState.IDLE
            elif result.outcome is Outcome.BLOCKED:
                self.state = OperationState.BLOCKED
            elif result.outcome is Outcome.CANCELLED:
                self.state = OperationState.CANCELLED
            elif result.outcome in VERIFIED or result.outcome is Outcome.NEEDS_USER_ACTION:
                self.state = OperationState.SUCCEEDED
            else:
                self.state = OperationState.FAILED
        except OperationError as exc:
            self.show_error(exc.detail)
        except Exception:
            self.show_error(
                Error(
                    code=ErrorCode.UNKNOWN_OUTCOME,
                    message="Inspect operation history before retrying.",
                    operation_id=snapshot.operation_id,
                )
            )
        finally:
            if password:
                password.discard()
            if self.state in (OperationState.AUTHENTICATING, OperationState.RUNNING):
                self.state = OperationState.FAILED
            self.render_controls()
