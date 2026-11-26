"""Typed inputs and settings for this plugin."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, IPvAnyAddress, IPvAnyNetwork, field_validator, model_validator
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.settings import PluginSettings

Domain = Annotated[
    str,
    Field(
        pattern=r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$", max_length=253
    ),
]


Slug = Annotated[str, Field(pattern="^[a-z][a-z0-9_-]{0,47}$", max_length=48)]


class SubscriptionID(Inputs):
    subscription: Slug = Field(
        description="Identify the local subscription whose settings or routes this action uses.",
        title="Subscription ID",
        json_schema_extra={"ui": {"choice_provider": "subscriptions"}},
    )


class ForgetSubscription(SubscriptionID):
    """Forget the local files for one reviewed subscription."""


class ForgetAll(Inputs):
    """Forget only verified local subscription and route records."""


class Subscription(Inputs):
    subscription: Slug = Field(
        description="Identify the local subscription whose settings or routes this action uses.",
        title="Local subscription ID",
    )
    title: str = Field(
        description="Enter a recognizable display label for the selected record.",
        min_length=1,
        max_length=80,
        title="Display name",
    )
    source_type: Literal["standard", "provider-json"] = Field(
        description="Select the subscription format provided by the VPN service.",
        default="standard",
        title="Source format",
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
    route_name: Slug = Field(
        description="Name the routing configuration to export for the selected subscription.",
        default="user-route",
        title="User Route name",
    )
    disposition: Literal["direct", "proxy", "block"] = Field(
        description="Route matching traffic directly, through the proxy or to the block list.",
        default="direct",
        title="Rule action",
    )
    domain: Domain | None = Field(
        description="Match this exact DNS name when applying the routing rule.",
        default=None,
        title="Exact DNS name",
    )
    network: IPvAnyNetwork | None = Field(
        description="Match addresses within this IP network when applying the routing rule.",
        default=None,
        title="IPv4 or IPv6 network",
    )
    position: Literal["last", "first"] = Field(
        description="Place this rule at the beginning or end of its routing list.",
        default="last",
        title="Position in this rule list",
    )
    domestic_dns: IPvAnyAddress | None = Field(
        default=None,
        title="Domestic UDP DNS address",
        description="Leave blank to retain the route's current DNS settings.",
    )
    strategy: Literal["AsIs", "IPIfNonMatch"] = Field(
        description="Choose how Happ resolves domain names while matching this route.",
        default="IPIfNonMatch",
        title="Domain strategy",
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


Settings = PluginSettings
