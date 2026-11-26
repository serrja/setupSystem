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

from platformdirs import user_data_path
from sqlalchemy.ext.asyncio import AsyncEngine
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

from setup_system.errors import OperationError
from setup_system.execution.authorization import (
    BecomePassword,
    interactive_logins,
    prepare_sudo,
    restore_preferences,
)
from setup_system.execution.downloads import GITHUB_RELEASE_HOSTS
from setup_system.execution.palette import palette
from setup_system.execution.system_paths import protected
from setup_system.execution.worker import query
from setup_system.layout import PackageLayout
from setup_system.logging import export_history
from setup_system.services.extensions import (
    Activation,
    CatalogEntry,
    TrustPolicy,
    builtins,
    install_release,
    installed,
    preview_link,
)
from setup_system.services.operations import Operations
from setup_system.services.settings import SettingsStore, load_values
from setup_system.settings import Settings
from setup_system.storage.engine import create_engine
from setup_system.storage.migrations import upgrade
from setup_system.storage.repositories.extensions import ExtensionRepository
from setup_system.storage.repositories.operations import OperationRepository
from setup_system.storage.session import session_factory
from setup_system.ui.help import Help
from setup_system.ui.plugin_dialogs import Confirm, Prompt, SecretPrompt
from setup_system.ui.plugin_page import PluginPage
from setup_system_sdk.enums.operation import WorkerMethod
from setup_system_sdk.schemas.wire import Request


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
        layout: PackageLayout | None = None,
        settings: Settings | None = None,
        store: SettingsStore | None = None,
    ) -> None:
        super().__init__()
        self.layout_paths = layout or PackageLayout.locate()
        self.options = settings or Settings()
        self.store = store or SettingsStore()
        self.engine: AsyncEngine = create_engine(self.options)
        factory = session_factory(self.engine)
        self.manager = Operations(
            self.options,
            user_data_path("setup-system") / "v10" / "extensions",
            self.store,
            OperationRepository(factory),
            ExtensionRepository(factory),
        )
        self.selected_plugin: str | None = None
        self.entries: dict[str, CatalogEntry] = {}
        self.external: dict[str, CatalogEntry] = {}
        self.candidates: dict[str, CatalogEntry] = {}
        self.active_ids: set[str] = set()
        self.unavailable: list[tuple[str, str]] = []
        self.opened: set[str] = set()
        self.startup_digests: dict[str, str] = {}
        colors = palette(self.layout_paths.root)
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
                yield Button("Help and recovery", id="help")
            with ContentSwitcher(initial="welcome", id="pages"):
                with Vertical(id="welcome"):
                    yield Static("Workstation plugins", classes="title")
                    yield Static("Choose a plugin to inspect, configure or preview an action.")
                    yield Static("Administrative access", classes="title")
                    yield Select[str]([], id="account", prompt="Select an existing local login")
                    yield Button("Prepare administrative access", id="authorize")
                    yield Static("Read-only access is available.", id="auth-state")
        yield Static("", id="feedback", markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        try:
            await upgrade(self.engine)
        except Exception:
            self.exit(
                return_code=1,
                message="Check the database identity, permissions and migration status.",
            )
            return
        names = await asyncio.to_thread(interactive_logins)
        picker = self.query_one("#account", Select)
        picker.set_options([(name, name) for name in names])
        current = pwd.getpwuid(os.getuid()).pw_name
        if current in names:
            picker.value = current
        await self.reload_catalog(initial=True)

    async def reload_catalog(self, *, initial: bool = False) -> None:
        available, enabled = await asyncio.gather(
            asyncio.to_thread(builtins, self.layout_paths.root),
            asyncio.to_thread(installed, self.manager.env_root),
        )
        self.active_ids.clear()
        self.candidates = dict(self.external)
        self.unavailable = []
        for entry in available:
            if entry.manifest is not None and not entry.reason:
                self.candidates[entry.manifest.id] = entry
            else:
                self.unavailable.append(
                    (entry.source.name if entry.source else "Extension", entry.reason)
                )
        self.entries = dict(self.candidates)
        for entry in enabled:
            if entry.manifest is None or entry.reason:
                self.unavailable.append(
                    (entry.source.name if entry.source else "Extension", entry.reason)
                )
                continue
            plugin = entry.manifest.id
            # Use the installed schema for active operations and interrupted readbacks.
            self.entries[plugin] = entry
            if not entry.disabled:
                try:
                    release = await self.manager.activation(plugin)
                except (ValueError, OSError, OperationError):
                    continue
                if initial:
                    self.startup_digests[plugin] = release.digest
                self.active_ids.add(plugin)
        categories = sorted(
            {entry.manifest.category for entry in self.entries.values() if entry.manifest}
        )
        self.query_one("#category", Select).set_options(
            [("All categories", "all"), *[(name, name) for name in categories]]
        )
        self.query_one("#category", Select).value = "all"
        self.render_catalog()
        self.query_one("#feedback", Static).update(
            " · ".join(reason for _, reason in self.unavailable)
        )

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
        if key is not None:
            await self.open_workspace(key)

    async def open_workspace(self, key: str) -> None:
        if key not in self.entries or self.entries[key].manifest is None:
            return
        switcher = self.query_one("#pages", ContentSwitcher)
        if self.selected_plugin != key and self.selected_plugin is not None:
            self.query_one("#plugin-" + self.selected_plugin, PluginPage).invalidate()
        self.selected_plugin = key
        if key in self.opened:
            page = self.query_one("#plugin-" + key, PluginPage)
            if page.manifest != self.entries[key].manifest:
                await page.remove()
                self.opened.remove(key)
        if key not in self.opened:
            self.opened.add(key)
            await switcher.mount(
                PluginPage(
                    self.entries[key],
                    self.manager,
                    self.store,
                    self.activate,
                    self.startup_digests.get(key),
                    self.candidates.get(key),
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
        detail += "\nIf uv is unavailable, activation fetches the verified Astral runtime once."
        if not await self.push_screen_wait(Confirm(detail, "Trust and activate")):
            return
        if release.origin and release.digest not in policy.digests:
            match = await self.push_screen_wait(
                Prompt("Enter the independently verified SHA-256 digest for this release.")
            )
            if match != release.digest:
                raise ValueError("The approved digest differs from this release")
        policy = policy.model_copy(update={"digests": policy.digests | {release.digest}})

        def validate_settings(candidate: Activation) -> None:
            document = load_values(self.store, manifest.id)
            if document.values and document.version != manifest.settings_version:
                raise ValueError("The saved settings require a separate schema migration.")
            asyncio.run(
                query(
                    candidate,
                    Request(
                        method=WorkerMethod.VALIDATE, plugin_id=manifest.id, saved=document.values
                    ),
                    self.options,
                )
            )

        async with self.manager.records.claim_extension(manifest.id):
            if await self.manager.records.pending(manifest.id):
                raise ValueError("Resolve interrupted operations before updating this extension.")
            release_state = await install_release(
                release,
                policy=policy,
                env_root=self.manager.env_root,
                wheelhouse=entry.source.parent if entry.source else None,
                validate_settings=validate_settings,
            )
            await self.manager.extensions.activate(release_state.manifest, release_state.digest)
        await self.reload_catalog()
        self.query_one("#feedback", Static).update(manifest.name + " extension activated.")
        if self.selected_plugin == manifest.id and manifest.id in self.opened:
            page = self.query_one("#plugin-" + manifest.id, PluginPage)
            if page.manifest != manifest:
                self.call_later(self.open_workspace, manifest.id)

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
                    for e in await asyncio.to_thread(builtins, self.layout_paths.root)
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

    @on(Button.Pressed, "#help")
    def action_help(self) -> None:
        self.push_screen(Help(self.layout_paths.guides))

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    @on(Button.Pressed, "#global-history")
    @work(exit_on_error=False)
    async def action_history(self) -> None:
        events = await self.manager.records.events(limit=200)
        await self.push_screen_wait(
            Confirm(
                "\n".join(
                    f"{e.timestamp:%Y-%m-%d %H:%M} UTC · {e.plugin_id} · "
                    f"{e.action_id} · {e.phase.value} · {e.message_code}"
                    for e in events
                )
                or "No recorded operations.",
                "Close",
            )
        )

    @on(Button.Pressed, "#export-history")
    @work(exit_on_error=False)
    async def export_history(self) -> None:
        try:
            destination = await export_history(self.manager.records)
        except (OSError, ValueError):
            self.query_one("#feedback", Static).update(
                "Could not export the local operation journal."
            )
            return
        self.query_one("#feedback", Static).update(f"Saved the operation journal to {destination}")

    async def action_quit(self) -> None:
        await self.manager.drain()
        self.exit()

    async def on_unmount(self) -> None:
        try:
            await self.manager.drain()
        finally:
            await self.engine.dispose()


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
    if app.return_code:
        raise SystemExit(app.return_code)


if __name__ == "__main__":
    main()
