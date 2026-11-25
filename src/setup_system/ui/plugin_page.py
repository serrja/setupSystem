"""A persistent plugin workspace built from its reviewed package metadata."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable, Coroutine
from typing import Any
from zipfile import ZipFile

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, ContentSwitcher, Select, Static, TabbedContent, TabPane

from setup_system.application.rollback import RecoveryPlan
from setup_system.authorization import BecomePassword
from setup_system.cleanup import apply_cleanup, preview_cleanup, save_keyring_entry, save_profile
from setup_system.configuration import Settings
from setup_system.configuration.store import SavedValues, profiles
from setup_system.extension_lifecycle import removable, retire, toggle
from setup_system.extensions import CatalogEntry
from setup_system.plugin_manager import (
    PluginManager,
    Snapshot,
    descriptor,
    protected_resources,
    query,
)
from setup_system.plugin_ui import panel_type
from setup_system.ui.plugin_dialogs import Choose, Confirm, Prompt, SecretPrompt
from setup_system.ui.schema_fields import SchemaForm
from setup_system_sdk import Action, Outcome, Values
from setup_system_sdk.protocol import Request
from setup_system_sdk.ui import UIContext


def help_text(entry: CatalogEntry, article: str = "help.md") -> str:
    source = entry.source
    manifest = entry.manifest
    if not source or not manifest:
        return entry.reason
    module = manifest.entry_point.partition(":")[0]
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
        manager: PluginManager,
        store: Settings,
        activate: Callable[[CatalogEntry], Coroutine[Any, Any, None]],
        startup_digest: str | None = None,
    ) -> None:
        assert entry.manifest
        super().__init__(id="plugin-" + entry.manifest.id)
        self.startup_digest = startup_digest
        self.entry = entry
        self.manifest = entry.manifest
        self.manager = manager
        self.store = store
        self.activate = activate
        self.profile = "default"
        self.saved: SavedValues | None = None
        self.settings_form: SchemaForm | None = None
        self.snapshot: Snapshot | None = None
        self.forms: dict[str, SchemaForm] = {}
        self.choice_cache: dict[tuple[str, str, str], tuple[float, list[str]]] = {}
        self.active_action: Action | None = None
        self.drafts: dict[str, Values] = {}
        self.status_line = Static("", markup=False, classes="feedback")
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
                    yield Button("Activate / update extension", id="activate")
                    yield Button("Inspect program", id="inspect")
                    yield Button("Disable extension", id="disable")
                    yield Button("Enable extension", id="enable")
                    yield Button("Remove extension", id="remove-extension")
                    yield Button("Full cleanup", id="full-cleanup")
            with TabPane("Settings / Profiles", id="settings"):
                with VerticalScroll():
                    yield Select[str](
                        [("default", "default")], value="default", allow_blank=False, id="profile"
                    )
                    yield Button("New profile", id="new-profile")
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
                    yield Button("Review remaining cleanup", id="resume-cleanup")
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
            await self.change_profile("default")
            self.query_one("#article", Static).update(
                await asyncio.to_thread(help_text, self.entry)
            )
            await self.refresh_state()
        except Exception:
            self.status_line.update("Plugin resources are unavailable. Check the installation.")

    async def refresh_state(self) -> None:
        try:
            state = await self.manager.activation(self.manifest.id)
            container = self.query_one("#custom-panel", Vertical)
            if not container.children:
                panel = await asyncio.to_thread(panel_type, state, self.startup_digest)
                if panel:
                    await container.mount(panel(UIContext(self.manifest.id, self.show_action)))
            text = f"Active extension {state.manifest.version} · {state.digest[:12]}"
        except ValueError as error:
            text = self.entry.reason or (
                "Extension disabled" if self.entry.disabled else str(error)
            )
        self.query_one("#extension-state", Static).update(text)
        self.query_one("#rollback", Button).disabled = not await self.manager.ledger.recoveries(
            self.manifest.id
        )

    def show_action(self, action: str) -> None:
        if any(item.id == action for item in self.manifest.actions):
            self.query_one("#action-choice", Select).value = action
            self.query_one(TabbedContent).active = "actions"

    async def dynamic_choices(self, provider: str, query_text: str) -> list[str]:
        state = await self.manager.activation(self.manifest.id)
        request = Request(
            method="choices",
            plugin_id=self.manifest.id,
            provider=provider,
            query=query_text,
            saved=self.saved.values if self.saved else {},
            overrides=self.unsaved_values(),
        )
        key = (
            state.digest,
            self.profile,
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

    async def change_profile(self, name: str) -> None:
        if self.settings_form:
            self.drafts[self.profile] = self.settings_form.values(explicit=True)
        saved = await self.store.read(self.manifest.id, name)
        if saved.values and saved.version != self.manifest.settings_version:
            raise ValueError("This profile needs migration")
        self.profile, self.saved = name, saved
        container = self.query_one("#settings-form", Vertical)
        await container.remove_children()
        form = SchemaForm(
            self.manifest.settings_schema, self.drafts.get(name, saved.values), self.dynamic_choices
        )
        form.plugin_id = self.manifest.id
        form.save_secret = lambda secret: save_keyring_entry(self.manager, self.manifest.id, secret)
        self.settings_form = form
        await container.mount(form)
        choices = await asyncio.to_thread(profiles, self.store, self.manifest.id)
        select = self.query_one("#profile", Select)
        select.set_options([(p, p) for p in sorted(set(choices) | self.drafts.keys() | {name})])
        select.value = name

    @on(Select.Changed, "#profile")
    async def selected_profile(self, event: Select.Changed) -> None:
        if isinstance(event.value, str) and event.value != self.profile:
            await self.change_profile(event.value)

    @on(Select.Changed, "#action-choice")
    async def selected_action(self, event: Select.Changed) -> None:
        if not isinstance(event.value, str):
            return
        i, action = next((i, a) for i, a in enumerate(self.manifest.actions) if a.id == event.value)
        self.active_action = action
        self.snapshot = None
        self.review.update("")
        self.query_one("#apply", Button).disabled = True
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
        try:
            await self._command(name)
        except (ValueError, OSError, RuntimeError, ExceptionGroup) as exc:
            # Validation errors can contain raw input. Keep raw errors out of the UI.
            message = (
                str(exc)
                if type(exc) is ValueError and "\n" not in str(exc) and len(str(exc)) < 160
                else "The request could not be validated or completed."
            )
            self.status_line.update(message)

    async def authorize(self, action: Action) -> BecomePassword | None:
        if not {"root", "su", "sudo"}.intersection(action.permissions):
            return None
        state = await self.manager.activation(self.manifest.id)
        await asyncio.to_thread(protected_resources, state)
        secret = await self.app.push_screen_wait(
            SecretPrompt(
                "Root password for su"
                if "su" in action.permissions
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
            await self.activate(self.entry)
            await self.refresh_state()
        elif name == "new-profile":
            profile = await self.app.push_screen_wait(
                Prompt("New profile name (letters, digits, underscore or hyphen)")
            )
            if profile:
                await self.change_profile(profile)
        elif name in ("disable", "enable"):
            async with self.manager.ledger.writer(plugin):
                await asyncio.to_thread(
                    toggle, self.manager.env_root, plugin, enable=name == "enable"
                )
            await self.refresh_state()
        elif name == "remove-extension":
            if await self.manager.ledger.pending(plugin):
                raise ValueError("Resolve interrupted operations before removing the worker")
            entries = await asyncio.to_thread(removable, self.manager.env_root, plugin)
            if await self.app.push_screen_wait(
                Confirm(
                    "Remove the extension code and environments?\n" + "\n".join(entries),
                    "Remove extension",
                )
            ):
                async with self.manager.ledger.writer(plugin):
                    if await self.manager.ledger.pending(plugin):
                        raise ValueError(
                            "Resolve interrupted operations before removing the worker"
                        )
                    await asyncio.to_thread(retire, self.manager.env_root, plugin, entries)
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
            available = await self.manager.ledger.recoveries(plugin)
            chosen = await self.app.push_screen_wait(
                Choose("Select a managed-file operation", [(r, o) for o, r in available])
            )
            if chosen:
                recovery = await asyncio.to_thread(RecoveryPlan.inspect, dict(available)[chosen])
                if await self.app.push_screen_wait(
                    Confirm(
                        "\n".join(f"{s} · {n}" for n, s in recovery.entries), "Roll back operation"
                    )
                ):
                    result = await self.manager.rollback(plugin, chosen, recovery)
                    self.status_line.update(result.outcome.value + " · " + result.message)
                    await self.refresh_state()
        elif name == "resume-cleanup":
            remaining = await self.manager.remaining_cleanup(plugin)
            if not remaining:
                self.status_line.update("No interrupted cleanup can safely resume.")
                return
            chosen = await self.app.push_screen_wait(
                Choose(
                    "Interrupted cleanup",
                    [(s.mutation.action_id, str(s.operation_id)) for s in remaining],
                )
            )
            snap = next((s for s in remaining if str(s.operation_id) == chosen), None)
            if snap:
                plan = snap.mutation.plan
                detail = "\n".join(
                    [plan.summary, *plan.warnings, *(r.description() for r in plan.resources)]
                )
                if await self.app.push_screen_wait(Confirm(detail, "Resume remaining removals")):
                    state = await self.manager.activation(plugin)
                    password = await self.authorize(descriptor(state, snap.mutation.action_id))
                    result = await self.manager.resume_cleanup(snap, password)
                    self.status_line.update(result.outcome.value + " · " + result.message)
        elif name == "reconcile":

            async def approve_readback(action: Action, snapshot: Snapshot) -> BecomePassword | None:
                plan = snapshot.mutation.plan
                detail = "\n".join(
                    [
                        action.label,
                        plan.summary,
                        *plan.warnings,
                        *(r.description() for r in plan.resources),
                    ]
                )
                if await self.app.push_screen_wait(
                    Confirm(detail, "Inspect with administrative access")
                ):
                    return await self.authorize(action)
                return None

            results = await self.manager.reconcile(plugin, approve_readback)
            self.status_line.update(
                " · ".join(r.message for r in results) or "No interrupted operations."
            )
        elif name == "inspect":
            state = await self.manager.activation(plugin)
            reply = await query(
                state,
                Request(
                    method="inspect",
                    plugin_id=plugin,
                    overrides=self.settings_form.values(explicit=True)
                    if self.settings_form
                    else {},
                ),
                self.manager.options,
            )
            if reply.result:
                self.status_line.update(reply.result.outcome.value + " · " + reply.result.message)
        elif name == "save" and self.settings_form and self.saved:
            state = await self.manager.activation(plugin)
            values = self.settings_form.values(explicit=True)
            await query(
                state,
                Request(method="validate", plugin_id=plugin, overrides=values),
                self.manager.options,
            )
            await save_profile(
                self.manager,
                self.store,
                plugin,
                self.profile,
                values,
                self.manifest.settings_version,
                self.saved.revision,
            )
            self.saved = await self.store.read(plugin, self.profile)
            self.status_line.update("Profile saved.")
        elif name == "preview" and self.active_action and self.settings_form:
            self.query_one("#apply", Button).disabled = True
            snapshot = await self.manager.preview(
                plugin,
                self.active_action.id,
                self.unsaved_values(),
                self._inputs(),
                saved=self.saved.values if self.saved else {},
            )
            self.snapshot = snapshot
            plan = snapshot.mutation.plan
            self.review.update(
                plan.summary
                + "\n"
                + "\n".join(
                    [
                        *plan.warnings,
                        *(r.description() for r in plan.resources),
                    ]
                )
            )
            self.status_line.update(plan.outcome.value)
            self.query_one("#apply", Button).disabled = (
                plan.outcome != Outcome.READY
                and not (
                    plan.outcome is Outcome.NO_CHANGES
                    and self.active_action.id in ("install", "update")
                )
            ) or not self.active_action.target
        elif name == "apply" and self.snapshot:
            snapshot, self.snapshot = self.snapshot, None
            self.query_one("#apply", Button).disabled = True
            state = await self.manager.activation(plugin)
            action = descriptor(state, snapshot.mutation.action_id)
            plan = snapshot.mutation.plan
            review = [
                action.label,
                plan.summary,
                *plan.warnings,
                *[r.description() for r in plan.resources],
            ]
            if not action.supports_rollback:
                review.append("Operation rollback is unavailable.")
            if not await self.app.push_screen_wait(Confirm("\n".join(review), "Confirm apply")):
                return
            password = None if plan.outcome is Outcome.NO_CHANGES else await self.authorize(action)
            if (
                plan.outcome is not Outcome.NO_CHANGES
                and {"root", "su", "sudo"}.intersection(action.permissions)
                and password is None
            ):
                self.status_line.update("Authorization cancelled.")
                return
            result = await self.manager.apply(snapshot, password)
            self.status_line.update(result.outcome.value + " · " + result.message)
        elif name == "refresh-resources":
            resources = await self.manager.ledger.holdings(plugin)
            self.query_one("#holdings", Static).update(
                "\n".join(f"{r.kind} · {r.identity} · {r.ownership}" for r in resources)
                or "No verified resources."
            )
        elif name == "refresh-history":
            events = await self.manager.journal.history(plugin)
            self.query_one("#events", Static).update(
                "\n".join(
                    f"{e.timestamp:%Y-%m-%d %H:%M} UTC · {e.action_id} · {e.phase}" for e in events
                )
                or "No recorded operations."
            )
