"""Prepare subscription-scoped Happ imports through the public routing format."""

import base64
import os
from importlib.resources import files as package_files
from pathlib import Path
from urllib.parse import urlsplit

from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.files.backend import (
    FilesBackend,
    owned_file_removal,
    remove_owned_file,
)
from setup_system_sdk.files.user_storage import inside_home
from setup_system_sdk.plugin import PluginSettings, owned_resources
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result
from setup_system_sdk.secrets import SecretUnavailableError, read_secret

from setup_plugin_happ.enums import PluginAction
from setup_plugin_happ.models import (
    ForgetAll,
    ForgetSubscription,
    PublicRoute,
    Route,
    Subscription,
    SubscriptionID,
)


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
    path = inside_home(root() / record.subscription / f"{action.route_name}.happ")
    prefix = b"happ://routing/add/"
    if path.exists():
        if path.stat().st_size > 65536:
            raise ValueError("Route exceeds size limit")
        prior = path.read_bytes().removesuffix(b"\n")
        if not prior.startswith(prefix):
            raise ValueError("Route contains an unrecognized import")
        old = PublicRoute.model_validate_json(base64.b64decode(prior[len(prefix) :], validate=True))
        if old.name != action.route_name:
            raise ValueError("Route name differs")
        data = old.model_dump(by_alias=True)
    else:
        data = PublicRoute(Name=action.route_name, DomainStrategy=action.strategy).model_dump(
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


class PluginActions(FilesBackend):
    package_folder = Path(str(package_files("setup_plugin_happ").joinpath("resources")))

    executable = "/usr/bin/happ"

    file_actions = frozenset({PluginAction.SAVE_SUBSCRIPTION, PluginAction.EXPORT_ROUTE})

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if action_id == PluginAction.SAVE_SUBSCRIPTION and isinstance(inputs, Subscription):
            return {
                subscription_file(inputs.subscription): inputs.model_dump_json(
                    indent=2, exclude_none=True
                ).encode()
                + b"\n"
            }
        if action_id == PluginAction.EXPORT_ROUTE and isinstance(inputs, Route):
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

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        if isinstance(inputs, Subscription) or (
            action_id in {PluginAction.IMPORT, PluginAction.UPDATE_SUBSCRIPTION}
            and isinstance(inputs, SubscriptionID)
        ):
            record = (
                inputs
                if isinstance(inputs, Subscription)
                else await run_sync(read_subscription, inputs.subscription)
            )
            if record.url_reference is None:
                return await super().plan(action_id, settings, inputs, context)
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
        return await super().plan(action_id, settings, inputs, context)

    async def choices(
        self,
        provider: str,
        query: str,
        settings: PluginSettings,
        context: PluginContext | None = None,
    ) -> list[str]:
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

        return await run_sync(list_ids) if provider == "subscriptions" else []

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

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if isinstance(inputs, (ForgetSubscription, ForgetAll)):
            ok = await run_sync(
                lambda: all(not os.path.lexists(r.identity) for r in plan.resources)
            )
            return Result.observed(
                outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
                message="Inspected the reviewed local records",
                resources=plan.resources,
            )
        result = await super().verify(plan, settings, inputs)
        if result.outcome == Outcome.COMPLETE and isinstance(inputs, Route):
            return Result.observed(
                outcome=Outcome.NEEDS_USER_ACTION,
                resources=[r.model_copy(update={"verified": True}) for r in plan.resources],
                message=(
                    "Route export verified. In Happ select this subscription, import the exported "
                    "routing link, and check the named profile. Reconnect separately to "
                    "activate changes."
                ),
            )
        return result
