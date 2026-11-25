"""Prepare subscription-scoped Happ imports through the public routing format."""

import asyncio
import base64
import os
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, IPvAnyAddress, IPvAnyNetwork, field_validator, model_validator

from setup_system.infrastructure.user_storage import inside_home
from setup_system_sdk import (
    Inputs,
    Outcome,
    Plan,
    PluginSettings,
    Resource,
    Result,
    owned_resources,
)
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.native import FilesBackend, owned_file_removal, remove_owned_file
from setup_system_sdk.secrets import SecretUnavailableError, read_secret

Slug = Annotated[str, Field(pattern="^[a-z][a-z0-9_-]{0,47}$", max_length=48)]
Domain = Annotated[
    str,
    Field(
        pattern=r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$", max_length=253
    ),
]


class SubscriptionID(Inputs):
    subscription: Slug = Field(
        title="Subscription ID", json_schema_extra={"ui": {"choice_provider": "subscriptions"}}
    )


class ForgetSubscription(SubscriptionID):
    """Forget the local files for one reviewed subscription."""


class ForgetAll(Inputs):
    """Forget only verified local subscription and route records."""


class Subscription(Inputs):
    subscription: Slug = Field(title="Local subscription ID")
    title: str = Field(min_length=1, max_length=80, title="Display name")
    source_type: Literal["standard", "provider-json"] = Field(
        default="standard", title="Source format"
    )
    url_handling: Literal["system-keyring", "enter-in-happ"] = Field(
        default="system-keyring",
        title="Subscription URL handling",
        description="Choose the system keyring or enter the URL directly in Happ at each import.",
    )
    url_reference: UUID | None = Field(
        default=None,
        title="Subscription URL",
        description="Store the HTTPS URL in the system keyring.",
        json_schema_extra={
            "ui": {
                "widget": "secret_reference",
                "secret": True,
                "visible_when": {"url_handling": "system-keyring"},
            }
        },
    )

    @model_validator(mode="after")
    def selected_storage(self) -> Self:
        if (self.url_reference is not None) != (self.url_handling == "system-keyring"):
            raise ValueError("Choose a keyring reference only for system-keyring handling")
        return self

    @field_validator("title")
    @classmethod
    def printable_title(cls, value: str) -> str:
        if not value.strip() or not value.isprintable() or "://" in value:
            raise ValueError("Enter a plain display name")
        return value


class Route(SubscriptionID):
    profile: Slug = Field(default="user-route", title="Route profile name")
    disposition: Literal["direct", "proxy", "block"] = Field(default="direct", title="Rule action")
    domain: Domain | None = Field(default=None, title="Exact DNS name")
    network: IPvAnyNetwork | None = Field(default=None, title="IPv4 or IPv6 network")
    position: Literal["last", "first"] = Field(default="last", title="Position in this rule list")
    domestic_dns: IPvAnyAddress | None = Field(
        default=None,
        title="Domestic UDP DNS address",
        description="Leave blank to retain the profile's current DNS settings.",
    )
    strategy: Literal["AsIs", "IPIfNonMatch"] = Field(
        default="IPIfNonMatch", title="Domain strategy"
    )


class PublicRoute(Inputs):
    name: str = Field(alias="Name")
    strategy: str = Field(alias="DomainStrategy")
    domestic_type: Literal["DoU"] | None = Field(default=None, alias="DomesticDNSType")
    domestic_ip: IPvAnyAddress | None = Field(default=None, alias="DomesticDNSIP")
    direct_sites: list[str] = Field(default_factory=list, alias="DirectSites", max_length=256)
    proxy_sites: list[str] = Field(default_factory=list, alias="ProxySites", max_length=256)
    block_sites: list[str] = Field(default_factory=list, alias="BlockSites", max_length=256)
    direct_ip: list[str] = Field(default_factory=list, alias="DirectIp", max_length=256)
    proxy_ip: list[str] = Field(default_factory=list, alias="ProxyIp", max_length=256)
    block_ip: list[str] = Field(default_factory=list, alias="BlockIp", max_length=256)


def root() -> Path:
    return inside_home(
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "setup-system"
        / "plugins"
        / "happ"
        / "subscriptions"
    )


def subscription_file(key: str) -> Path:
    return inside_home(root() / key / "subscription.json")


def read_subscription(key: str) -> Subscription:
    path = subscription_file(key)
    if not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("Select an existing local subscription")
    record = Subscription.model_validate_json(path.read_bytes())
    if record.subscription != key:
        raise ValueError("Subscription record identity differs")
    return record


def route_export(action: Route) -> tuple[Path, bytes]:
    record = read_subscription(action.subscription)
    if record.source_type == "provider-json":
        raise ValueError("The provider owns JSON subscription routing")
    if action.domain is None and action.network is None and action.domestic_dns is None:
        raise ValueError("Choose a domain, an IP network or a domestic DNS address")
    path = inside_home(root() / record.subscription / f"{action.profile}.happ")
    prefix = b"happ://routing/add/"
    if path.exists():
        if path.stat().st_size > 65536:
            raise ValueError("Route exceeds size limit")
        prior = path.read_bytes().removesuffix(b"\n")
        if not prior.startswith(prefix):
            raise ValueError("Route contains an unrecognized import")
        old = PublicRoute.model_validate_json(base64.b64decode(prior[len(prefix) :], validate=True))
        if old.name != action.profile:
            raise ValueError("Route name differs")
        data = old.model_dump(by_alias=True)
    else:
        data = PublicRoute(Name=action.profile, DomainStrategy=action.strategy).model_dump(
            by_alias=True
        )
    data["DomainStrategy"] = action.strategy
    if action.domestic_dns is not None:
        data.update(DomesticDNSType="DoU", DomesticDNSIP=str(action.domestic_dns))
    for suffix, value in (
        ("Sites", f"full:{action.domain.lower()}" if action.domain else None),
        ("Ip", str(action.network) if action.network else None),
    ):
        if value is None:
            continue
        for disposition in ("Direct", "Proxy", "Block"):
            group = data[disposition + suffix]
            if disposition.lower() == action.disposition:
                group[:] = [item for item in group if item != value]
                group.insert(0 if action.position == "first" else len(group), value)
            else:
                group[:] = [rule for rule in group if rule != value]
    valid = PublicRoute.model_validate(data)
    encoded = base64.b64encode(valid.model_dump_json(by_alias=True, exclude_none=True).encode())
    return path, prefix + encoded + b"\n"


class Plugin(DebianPackages, FilesBackend):
    package_folder = Path(__file__).parent
    executable = "/usr/bin/happ"
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        "start-system-service": Inputs,
        "stop-system-service": Inputs,
        "save-subscription": Subscription,
        "import": SubscriptionID,
        "update-subscription": SubscriptionID,
        "remove-subscription": SubscriptionID,
        "forget-local": ForgetSubscription,
        "forget-owned-files": ForgetAll,
        "export-route": Route,
        "reconnect": SubscriptionID,
    }
    file_actions = frozenset({"save-subscription", "export-route"})

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if action_id == "save-subscription" and isinstance(inputs, Subscription):
            return {
                subscription_file(inputs.subscription): inputs.model_dump_json(
                    indent=2, exclude_none=True
                ).encode()
                + b"\n"
            }
        if action_id == "export-route" and isinstance(inputs, Route):
            path, content = route_export(inputs)
            return {path: content}
        raise ValueError("Unexpected subscription action")

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if isinstance(inputs, (ForgetSubscription, ForgetAll)):
            directory = (
                root() / inputs.subscription if isinstance(inputs, ForgetSubscription) else root()
            )
            rows = []
            warnings = []
            for r in sorted(owned_resources(), key=lambda r: r.identity):
                path = Path(r.identity)
                if (
                    r.kind != "file"
                    or not path.is_relative_to(directory)
                    or not r.cleanup
                    or r.ownership != "created"
                ):
                    continue
                if len(path.relative_to(root()).parts) != 2 or (
                    path.name != "subscription.json" and path.suffix != ".happ"
                ):
                    return Plan(
                        outcome=Outcome.BLOCKED, summary="Review the unexpected local record"
                    )
                reviewed = owned_file_removal(path)
                if reviewed.outcome != Outcome.READY:
                    return Plan(
                        outcome=Outcome.BLOCKED,
                        summary="Reconcile the modified or missing local record",
                    )
                rows.extend(reviewed.resources)
                warnings.extend(reviewed.warnings)
            return Plan(
                outcome=Outcome.READY if rows else Outcome.NO_CHANGES,
                summary="Remove only the displayed managed subscription and route files",
                resources=rows,
                warnings=warnings[:64],
            )
        if isinstance(inputs, SubscriptionID):
            record = read_subscription(inputs.subscription)
            if isinstance(inputs, Route):
                if record.source_type == "provider-json":
                    return Plan(
                        outcome=Outcome.BLOCKED,
                        summary="This JSON subscription supplies its routing profile",
                    )
            else:
                handoffs = {
                    "import": (
                        "Open Happ, choose +, and enter the URL from your secret store in the "
                        "subscription dialog."
                    ),
                    "update-subscription": (
                        "In Happ, open this subscription and choose its refresh action."
                    ),
                    "remove-subscription": (
                        "In Happ, select this subscription and confirm its removal. This also "
                        "removes its routes and cached geofiles."
                    ),
                    "reconnect": (
                        "In Happ, verify the chosen subscription and route, then confirm "
                        "Reconnect when ready. Remote access may be interrupted."
                    ),
                }
                if record.url_handling == "enter-in-happ":
                    handoffs.update(
                        {
                            "import": "Choose + in Happ and enter the URL directly in Happ.",
                            "update-subscription": (
                                "Open the subscription in Happ, enter its current URL directly in "
                                "the native dialog when prompted, and refresh it."
                            ),
                        }
                    )
                return Plan(
                    outcome=Outcome.NEEDS_USER_ACTION,
                    summary=f"{record.title}. {handoffs[action_id]}",
                )
        return super().prepare(action_id, settings, inputs)

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if isinstance(inputs, Subscription) or (
            action_id in {"import", "update-subscription"} and isinstance(inputs, SubscriptionID)
        ):
            record = (
                inputs
                if isinstance(inputs, Subscription)
                else await asyncio.to_thread(read_subscription, inputs.subscription)
            )
            if record.url_reference is None:
                return await super().plan(action_id, settings, inputs)
            try:
                secret = await read_secret("happ", record.url_reference)
                raw = secret.get_secret_value()
                url = urlsplit(raw)
                valid = (
                    len(raw) <= 8192
                    and url.scheme == "https"
                    and bool(url.hostname)
                    and not url.username
                    and not url.password
                    and not url.fragment
                    and url.port in {None, 443}
                    and all(ch.isprintable() and not ch.isspace() for ch in raw)
                )
            except (ValueError, SecretUnavailableError):
                valid = False
            if not valid:
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary=(
                        "Enter a valid HTTPS subscription URL in the system keyring, or choose "
                        "enter-in-happ to supply the URL in Happ on each import."
                    ),
                )
        return await super().plan(action_id, settings, inputs)

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        def list_ids() -> list[str]:
            directory = root()
            if not directory.is_dir():
                return []
            result: list[str] = []
            with os.scandir(directory) as rows:
                for index, entry in enumerate(rows):
                    if index >= 512 or len(result) >= 50:
                        break
                    if entry.name.startswith(query) and entry.is_dir(follow_symlinks=False):
                        try:
                            read_subscription(entry.name)
                        except (OSError, ValueError):
                            continue
                        result.append(entry.name)
            return sorted(result)

        return await asyncio.to_thread(list_ids) if provider == "subscriptions" else []

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if not isinstance(inputs, (ForgetSubscription, ForgetAll)):
            return super().mutate(action_id, settings, inputs, plan)
        done: list[Resource] = []
        for row in plan.resources:
            path = Path(row.identity)
            candidate = owned_file_removal(path)
            if (
                candidate.resources != [row]
                or remove_owned_file(path, candidate).outcome != Outcome.CHANGED
            ):
                return Result(
                    outcome=Outcome.PARTIAL,
                    message="Review remaining subscription records",
                    resources=done,
                )
            done.append(row.model_copy(update={"verified": True}))
        return Result(outcome=Outcome.CHANGED, message="Local records removed", resources=done)

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if isinstance(inputs, (ForgetSubscription, ForgetAll)):
            ok = await asyncio.to_thread(
                lambda: all(not os.path.lexists(r.identity) for r in plan.resources)
            )
            return Result(
                outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
                message="Inspected the reviewed local records",
                resources=plan.resources,
            )
        result = await super().verify(plan, settings, inputs)
        if result.outcome == Outcome.COMPLETE and isinstance(inputs, Route):
            return Result(
                outcome=Outcome.NEEDS_USER_ACTION,
                resources=[r.model_copy(update={"verified": True}) for r in plan.resources],
                message=(
                    "Route export verified. In Happ select this subscription, import the exported "
                    "routing link, and check the named profile. Reconnect separately to "
                    "activate changes."
                ),
            )
        return result


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("happ", action_id, plan)
