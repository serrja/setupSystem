"""The normal-user Textual shell for independently activated workstation plugins."""

from __future__ import annotations

import asyncio
import os
import pwd
import sys
import tomllib
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit, urlunsplit

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import (
    Button,
    ContentSwitcher,
    Footer,
    Header,
    Input,
    OptionList,
    Select,
    Static,
)
from textual.widgets.option_list import Option

from setup_system.authorization import (
    BecomePassword,
    interactive_logins,
    prepare_sudo,
    restore_preferences,
)
from setup_system.configuration import Settings
from setup_system.configuration.legacy import translate
from setup_system.configuration.store import load_values, save_values
from setup_system.configuration.store import profiles as profile_names
from setup_system.extensions import (
    Activation,
    CatalogEntry,
    TrustPolicy,
    builtins,
    install_release,
    installed,
    preview_link,
)
from setup_system.infrastructure.downloads import GITHUB_RELEASE_HOSTS
from setup_system.infrastructure.palette import palette
from setup_system.infrastructure.system_paths import protected
from setup_system.layout import GUIDES, INSTALLED, resource_root
from setup_system.plugin_manager import PluginManager, descriptor, protected_resources, query
from setup_system.profiles import preview_profile, read_profiles
from setup_system.ui.help import Help
from setup_system.ui.plugin_dialogs import Choose, Confirm, Prompt, SecretPrompt
from setup_system.ui.plugin_page import PluginPage
from setup_system_sdk import Outcome
from setup_system_sdk.protocol import Request


def source_policy() -> TrustPolicy:
    path = Path("/etc/setup-system/plugin-sources.toml")
    if not path.exists():
        return TrustPolicy(hosts=GITHUB_RELEASE_HOSTS)
    protected(path, directory=False)
    return TrustPolicy.model_validate(tomllib.loads(path.read_text()))


class SetupApp(App[None]):
    TITLE = "setupSystem"
    BINDINGS: ClassVar[list[BindingType]] = [
        ("f1", "help", "Help"),
        ("ctrl+f", "search", "Search"),
        ("ctrl+h", "history", "History"),
        ("ctrl+q", "quit", "Quit"),
    ]
    CSS = """
    Screen { layout: vertical; }
    #shell { height: 1fr; }
    #sidebar { width: 32; min-width: 20; padding: 0 1; border-right: solid $accent; }
    #catalog { height: 1fr; }
    #pages { width: 1fr; padding: 0 1; }
    .title { text-style: bold; color: $accent; margin: 0 0 1 0; }
    .feedback, #feedback { height: auto; max-height: 5; overflow-y: auto; }
    #action-forms, #run-forms, #settings-form { height: auto; }
    PluginPage { height: 1fr; }
    PluginPage TabbedContent { height: 1fr; }
    TabPane { padding: 1 0; }
    Button { margin: 0 1 0 0; }
    Confirm, Prompt, SecretPrompt { align: center middle; }
    .dialog { width: 75%; max-width: 90; height: auto; max-height: 85%; padding: 1 2;
              border: solid $accent; background: $panel; }
    .review-text { height: auto; max-height: 20; }
    """

    def __init__(
        self,
        root: Path | None = None,
        manager: PluginManager | None = None,
        store: Settings | None = None,
    ) -> None:
        super().__init__()
        self.root = root or resource_root()
        self.manager = manager or PluginManager()
        self.store = store or Settings()
        self.entries: dict[str, CatalogEntry] = {}
        self.external: dict[str, CatalogEntry] = {}
        self.active_ids: set[str] = set()
        self.unavailable: list[tuple[str, str]] = []
        self.opened: set[str] = set()
        self.startup_digests: dict[str, str] = {}
        colors = palette(
            self.root if (self.root / "config/shared/palette.toml").exists() else resource_root()
        )
        theme = Theme(
            name="mocha-green",
            primary=colors["green"],
            secondary=colors["teal"],
            warning=colors["yellow"],
            error=colors["red"],
            accent=colors["green"],
            foreground=colors["text"],
            background=colors["base"],
            surface=colors["surface0"],
            panel=colors["mantle"],
        )
        self.register_theme(theme)
        self.theme = theme.name

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="shell"):
            with Vertical(id="sidebar"):
                yield Input(placeholder="Search plugins", id="search")
                yield Select[str](
                    [("All categories", "all")], value="all", allow_blank=False, id="category"
                )
                yield OptionList(id="catalog")
                yield Button("Add from link", id="add-link")
                yield Button("Global history", id="global-history")
                yield Button("Export history", id="export-history")
                yield Button("Profiles", id="workstation-profiles")
                yield Button("Help and recovery", id="help")
            with ContentSwitcher(initial="welcome", id="pages"):
                with Vertical(id="welcome"):
                    yield Static("Workstation plugins", classes="title")
                    yield Static("Choose a plugin to inspect, configure or preview an action.")
                    yield Static("Administrative access", classes="title")
                    yield Select[str]([], id="account", prompt="Select an existing local login")
                    yield Button("Prepare administrative access", id="authorize")
                    yield Static("Read-only access is available.", id="auth-state")
                    yield Button("Review previous settings", id="legacy")
                    yield Button("Restore a previous backup", id="old-restore")
        yield Static("", id="feedback", markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        names = await asyncio.to_thread(interactive_logins)
        picker = self.query_one("#account", Select)
        picker.set_options([(name, name) for name in names])
        current = pwd.getpwuid(os.getuid()).pw_name
        if current in names:
            picker.value = current
        await self.reload_catalog()
        for entry in await asyncio.to_thread(installed, self.manager.env_root):
            if entry.manifest and not entry.reason and not entry.disabled:
                state = await self.manager.activation(entry.manifest.id)
                self.startup_digests[entry.manifest.id] = state.digest

    async def reload_catalog(self) -> None:
        available, enabled = await asyncio.gather(
            asyncio.to_thread(builtins, self.root),
            asyncio.to_thread(installed, self.manager.env_root),
        )
        self.active_ids = {
            e.manifest.id for e in enabled if e.manifest and not e.disabled and not e.reason
        }
        self.unavailable = [
            (e.source.name if e.source else "Extension", e.reason)
            for e in enabled + available
            if e.manifest is None
        ]
        self.entries = dict(self.external)
        disabled = []
        for entry in enabled + available:
            if entry.manifest is None or entry.reason:
                disabled.append(entry.reason)
            if entry.manifest:
                self.entries[entry.manifest.id] = entry
        for entry in enabled:
            if entry.manifest and entry.disabled and entry.manifest.id in self.entries:
                from dataclasses import replace

                self.entries[entry.manifest.id] = replace(
                    self.entries[entry.manifest.id], disabled=True
                )
        categories = sorted(
            {entry.manifest.category for entry in self.entries.values() if entry.manifest}
        )
        self.query_one("#category", Select).set_options(
            [("All categories", "all"), *[(name, name) for name in categories]]
        )
        self.query_one("#category", Select).value = "all"
        self.render_catalog()
        self.query_one("#feedback", Static).update(" · ".join(disabled))

    def render_catalog(self) -> None:
        needle = self.query_one("#search", Input).value.casefold()
        category = self.query_one("#category", Select).value
        listing = self.query_one("#catalog", OptionList)
        listing.clear_options()
        for key, entry in sorted(self.entries.items()):
            item = entry.manifest
            if (
                item
                and category in (Select.BLANK, "all", item.category)
                and needle in (item.name + " " + key).casefold()
            ):
                listing.add_option(
                    Option(
                        item.name
                        + " · "
                        + (
                            entry.reason
                            or (
                                "disabled"
                                if entry.disabled
                                else "active"
                                if key in self.active_ids
                                else "available"
                            )
                        ),
                        id=key,
                        disabled=bool(entry.reason),
                    )
                )

        if category in (Select.BLANK, "all"):
            for name, reason in self.unavailable:
                if needle in name.casefold():
                    listing.add_option(Option(f"{name} · {reason}", disabled=True))

    @on(Input.Changed, "#search")
    @on(Select.Changed, "#category")
    def filter_catalog(self) -> None:
        self.render_catalog()

    @on(OptionList.OptionSelected, "#catalog")
    async def open_plugin(self, event: OptionList.OptionSelected) -> None:
        key = event.option.id
        if key is None or (manifest := self.entries[key].manifest) is None:
            return
        switcher = self.query_one("#pages", ContentSwitcher)
        if key not in self.opened:
            self.opened.add(key)
            await switcher.mount(
                PluginPage(
                    self.entries[key],
                    self.manager,
                    self.store.for_version(manifest.settings_version),
                    self.activate,
                    self.startup_digests.get(key),
                )
            )
        switcher.current = "plugin-" + key

    async def activate(self, entry: CatalogEntry) -> None:
        release = entry.release
        if not release or not entry.manifest or entry.reason:
            raise ValueError("Build or obtain the verified plugin wheel before activation")
        manifest = entry.manifest
        policy = await asyncio.to_thread(source_policy)
        permissions = ", ".join(manifest.permissions) or "none"
        origin = "Local package"
        if release.origin:
            parsed = urlsplit(release.origin)
            origin = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        detail = (
            f"{manifest.name} {manifest.version}\nAuthor: {manifest.author}\n"
            f"Publisher source: {manifest.source}\nDownloaded from: {origin}\nSDK: {manifest.sdk}\n"
            f"Permissions: {permissions}\nSHA-256: {release.digest}"
        )
        closure = await asyncio.to_thread(release.selected)
        detail += "\nBinary distributions\n" + "\n".join(
            f"{w.name} · SHA-256 {w.sha256}" for w in closure.wheels
        )
        if not await self.push_screen_wait(Confirm(detail, "Trust and activate")):
            return
        if release.origin and release.digest not in policy.digests:
            match = await self.push_screen_wait(
                Prompt("Enter the independently verified SHA-256 digest for this release.")
            )
            if match != release.digest:
                raise ValueError("The approved digest differs from this release")
        policy = policy.model_copy(update={"digests": policy.digests | {release.digest}})
        previous = next(
            (
                e.manifest
                for e in await asyncio.to_thread(installed, self.manager.env_root)
                if e.manifest and e.manifest.id == manifest.id
            ),
            None,
        )
        before = self.store.for_version(previous.settings_version if previous else 1)
        after = self.store.for_version(manifest.settings_version)

        def verify_profiles(state: Activation) -> None:
            proposals = []
            for name in profile_names(before, manifest.id):
                old = load_values(before, manifest.id, name)
                if old.revision.endswith(":absent"):
                    continue
                reply = asyncio.run(
                    query(
                        state,
                        Request(
                            method="migrate",
                            plugin_id=manifest.id,
                            saved=old.values,
                            settings_version=old.version,
                        ),
                        self.manager.options,
                    )
                )
                if before.directory != after.directory:
                    candidate = load_values(after, manifest.id, name)
                    if (
                        not candidate.revision.endswith(":absent")
                        and candidate.values != reply.settings
                    ):
                        raise ValueError("A previous migration has conflicting profile values")
                    proposals.append((name, candidate.revision, reply.settings, old.revision))
            for name, revision, values, source_revision in proposals:
                if source_revision != load_values(before, manifest.id, name).revision:
                    raise ValueError("Profile changed during migration")
                save_values(after, manifest.id, name, values, manifest.settings_version, revision)

        async with self.manager.ledger.writer(manifest.id):
            if await self.manager.ledger.pending(manifest.id):
                raise ValueError("Reconcile interrupted operations before updating the extension")
            await install_release(
                release,
                policy=policy,
                env_root=self.manager.env_root,
                wheelhouse=entry.source.parent if entry.source else None,
                validate_settings=verify_profiles,
            )
        self.query_one("#feedback", Static).update(manifest.name + " extension activated.")

    @on(Button.Pressed, "#add-link")
    @work(exit_on_error=False)
    async def add_link(self) -> None:
        url = await self.push_screen_wait(Prompt("HTTPS release manifest or binary plugin wheel"))
        if not url:
            return
        try:
            release = await preview_link(url, await asyncio.to_thread(source_policy))
            manifest = release.manifest
            existing = self.entries.get(manifest.id)
            reserved = next(
                (
                    e
                    for e in await asyncio.to_thread(builtins, self.root)
                    if e.manifest and e.manifest.id == manifest.id
                ),
                None,
            )
            if reserved and (not reserved.release or reserved.release.digest != release.digest):
                raise ValueError("An installed package reserves this plugin identity")
            if (
                existing
                and existing.manifest
                and (
                    existing.manifest.source != manifest.source
                    or existing.manifest.author != manifest.author
                )
            ):
                raise ValueError("Plugin identity collision")
            entry = CatalogEntry(manifest, release=release)
            await self.activate(entry)
            self.external[manifest.id] = entry
            await self.reload_catalog()
        except Exception:
            self.query_one("#feedback", Static).update(
                "Check the allowed origin, release identity, binary dependencies and digest."
            )

    @on(Button.Pressed, "#authorize")
    @work(exclusive=True, group="authorize", exit_on_error=False)
    async def prepare_auth(self) -> None:
        account = self.query_one("#account", Select).value
        if isinstance(account, str):
            try:

                async def prompt() -> BecomePassword | None:
                    secret = await self.push_screen_wait(
                        SecretPrompt("Sudo password for " + account)
                    )
                    return BecomePassword(secret.get_secret_value()) if secret else None

                text = await prepare_sudo(
                    account, prompt, self.manager.options.prompt_timeout_seconds
                )
            except (OSError, ValueError):
                text = "Native sudo is unavailable. Review Administration to prepare an account."
            self.query_one("#auth-state", Static).update(text)

    @on(Button.Pressed, "#legacy")
    @work(exit_on_error=False)
    async def review_legacy(self) -> None:
        names = await asyncio.to_thread(
            lambda: [
                name
                for name in (
                    "base.toml",
                    "workspace.toml",
                    "system-intentions.toml",
                    "settings.toml",
                )
                if (self.store.directory / name).is_file()
            ]
        )
        if not names:
            self.query_one("#feedback", Static).update("No previous stage settings found.")
            return
        choice = await self.push_screen_wait(
            Choose("Previous settings", [(name, name) for name in names])
        )
        if choice not in names or not choice:
            return
        converted = await asyncio.to_thread(translate, self.store.directory / choice)
        lines = [f"{r.plugin} · {r.action} · {r.inputs}" for r in converted.actions]
        lines.extend(f"{p} · {values}" for p, values in converted.settings.items())
        if not await self.push_screen_wait(
            Confirm("\n".join(lines) or "No selected actions", "Import profile values")
        ):
            return
        from setup_system.cleanup import save_profile

        for plugin, values in converted.settings.items():
            state = await self.manager.activation(plugin)
            await query(
                state,
                Request(method="validate", plugin_id=plugin, overrides=values),
                self.manager.options,
            )
            old = await self.store.read(plugin, "imported")
            if old.values and old.values != values:
                raise ValueError("Existing imported profile has local changes")
            await save_profile(
                self.manager,
                self.store,
                plugin,
                "imported",
                values,
                state.manifest.settings_version,
                old.revision,
            )
        self.query_one("#feedback", Static).update(
            "Imported profile values saved. Review each selected action on its plugin page."
        )

    @on(Button.Pressed, "#old-restore")
    @work(exit_on_error=False)
    async def restore_previous(self) -> None:
        from setup_system.application.rollback import RecoveryPlan
        from setup_system.infrastructure.receipt_repository import list_operations

        registered = await self.manager.ledger.registered_receipts()
        receipts = [
            (r, s) for r, s in await asyncio.to_thread(list_operations) if r not in registered
        ]
        if not receipts:
            self.query_one("#feedback", Static).update("No file recovery receipts found.")
            return
        receipt = await self.push_screen_wait(
            Choose("Previous backup", [(f"{name} · {status}", name) for name, status in receipts])
        )
        if receipt:
            plan = await asyncio.to_thread(RecoveryPlan.inspect, receipt)
            if await self.push_screen_wait(
                Confirm("\n".join(f"{s} · {n}" for n, s in plan.entries), "Restore reviewed files")
            ):
                result = await self.manager.run_recovery(plan)
                self.query_one("#feedback", Static).update(
                    result.outcome.value + " · " + result.message
                )

    @on(Button.Pressed, "#help")
    def action_help(self) -> None:
        self.push_screen(Help(GUIDES if self.root == INSTALLED else self.root / "docs"))

    @on(Button.Pressed, "#workstation-profiles")
    @work(exclusive=True, group="profiles", exit_on_error=False)
    async def run_profile(self) -> None:
        profiles = await asyncio.to_thread(read_profiles, self.root)
        name = await self.push_screen_wait(
            Choose("Workstation profile", [(p.name, key) for key, p in profiles.items()])
        )
        if not name:
            return
        try:
            snapshots = await preview_profile(self.manager, self.store, profiles[name])
        except (ValueError, OSError, ExceptionGroup):
            self.query_one("#feedback", Static).update(
                "Activate the selected profile extensions and check their settings."
            )
            return
        detail = []
        for snapshot in snapshots:
            item = snapshot.mutation
            detail.extend(
                [
                    f"{snapshot.plugin_id} · {item.action_id} · {item.plan.outcome}",
                    item.plan.summary,
                    *item.plan.warnings,
                ]
            )
            detail.extend(r.description() for r in item.plan.resources)
        if not await self.push_screen_wait(Confirm("\n".join(detail), "Apply profile")):
            return
        if any(
            s.mutation.plan.outcome not in (Outcome.READY, Outcome.NO_CHANGES) for s in snapshots
        ):
            self.query_one("#feedback", Static).update("Profile blocked by the reviewed actions.")
            return
        for snapshot in snapshots:
            if (
                snapshot.mutation.plan.outcome == Outcome.NO_CHANGES
                and snapshot.mutation.action_id not in ("install", "update")
            ):
                continue
            state = await self.manager.activation(snapshot.plugin_id)
            action = descriptor(state, snapshot.mutation.action_id)
            password = None
            if snapshot.mutation.plan.outcome is not Outcome.NO_CHANGES and {
                "root",
                "sudo",
                "su",
            } & set(action.permissions):
                await asyncio.to_thread(protected_resources, state)
                secret = await self.push_screen_wait(
                    SecretPrompt(f"Native authorization for {state.manifest.name}")
                )
                if secret is None:
                    self.query_one("#feedback", Static).update(
                        "Profile stopped at an action boundary."
                    )
                    return
                password = BecomePassword(secret.get_secret_value())
                del secret
            result = await self.manager.apply(snapshot, password)
            self.query_one("#feedback", Static).update(
                f"{snapshot.plugin_id} · {result.outcome} · {result.message}"
            )
            if result.outcome not in (Outcome.COMPLETE, Outcome.CHANGED, Outcome.NO_CHANGES):
                return

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    @on(Button.Pressed, "#global-history")
    @work(exit_on_error=False)
    async def action_history(self) -> None:
        events = await self.manager.journal.history()
        await self.push_screen_wait(
            Confirm(
                "\n".join(
                    f"{e.timestamp:%Y-%m-%d %H:%M} UTC · {e.plugin_id} · {e.action_id} · {e.phase}"
                    for e in events
                )
                or "No recorded operations.",
                "Close",
            )
        )

    @on(Button.Pressed, "#export-history")
    @work(exit_on_error=False)
    async def export_history(self) -> None:
        path = await self.manager.journal.export()
        self.query_one("#feedback", Static).update(f"Saved history report to {path}")

    async def action_quit(self) -> None:
        await self.manager.drain()
        self.exit()

    async def on_unmount(self) -> None:
        await self.manager.drain()


def main() -> None:
    if os.geteuid() == 0 or os.geteuid() != os.getuid():
        sys.exit("Run setup-system as a normal user")
    try:
        restore_preferences()
        app = SetupApp()
    except (ValueError, OSError):
        sys.stderr.write("Invalid application settings or package resources.\n")
        raise SystemExit(1) from None
    app.run()


if __name__ == "__main__":
    main()
