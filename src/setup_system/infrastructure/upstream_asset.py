"""Validate published Debian assets against an approved official stable release."""

import re
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, StrictBool

from setup_system.domain.applications import Application


class ReleaseAsset(BaseModel):
    name: str
    browser_download_url: str
    digest: str | None = None


class GithubRelease(BaseModel):
    """Read the fields used for selection from the extensible GitHub response."""

    draft: StrictBool
    prerelease: StrictBool
    tag_name: str
    published_at: str
    assets: tuple[ReleaseAsset, ...]


def github_deb(release: Any, entry: Any, arch: str) -> dict[str, str]:
    latest = GithubRelease.model_validate(release)
    application = Application.model_validate(entry)
    if (
        arch not in ("amd64", "arm64")
        or latest.draft
        or latest.prerelease
        or not latest.published_at
    ):
        raise ValueError("No stable upstream package for this architecture")
    pattern = application.asset.get("amd64" if arch == "amd64" else "arm64")
    project = application.github
    if (
        not pattern
        or not project
        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*/[a-zA-Z0-9][a-zA-Z0-9_.-]*", project)
    ):
        raise ValueError("Invalid release asset catalog entry")
    tag = latest.tag_name
    if not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,5}", tag):
        raise ValueError("Unexpected upstream release tag")
    version = tag.removeprefix("v")
    name = pattern.format(version=version)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{1,180}\.deb", name):
        raise ValueError("Invalid release asset name")
    url = f"https://github.com/{project}/releases/download/{tag}/{quote(name)}"
    checksums = [
        asset.digest
        for asset in latest.assets
        if asset.name == name and asset.browser_download_url == url
    ]
    if (
        len(checksums) != 1
        or not checksums[0]
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", checksums[0])
    ):
        raise ValueError("The stable upstream asset lacks a verified SHA-256")
    return {"name": name, "version": version, "url": url, "checksum": checksums[0]}
