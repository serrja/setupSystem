"""Validate published Debian assets against an approved official stable release."""

import re
from collections.abc import Mapping
from urllib.parse import quote


def github_deb(
    release: Mapping[str, object],
    entry: Mapping[str, object],
    arch: str,
) -> dict[str, str]:
    templates = entry.get("asset")
    pattern = templates.get(arch) if isinstance(templates, dict) else None
    if (
        not isinstance(pattern, str)
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or not release.get("published_at")
    ):
        raise ValueError("No stable upstream package for this architecture")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,5}", tag):
        raise ValueError("Unexpected upstream release tag")
    version = tag.removeprefix("v")
    name = pattern.format(version=version)
    project = entry.get("github")
    if (
        not isinstance(project, str)
        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*/[a-zA-Z0-9][a-zA-Z0-9_.-]*", project)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{1,180}\.deb", name)
    ):
        raise ValueError("Invalid release asset catalog entry")
    url = f"https://github.com/{project}/releases/download/{tag}/{quote(name)}"
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Expected official release assets")
    checksums = [
        asset.get("digest")
        for asset in assets
        if isinstance(asset, dict)
        and asset.get("name") == name
        and asset.get("browser_download_url") == url
    ]
    if (
        len(checksums) != 1
        or not isinstance(checksums[0], str)
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", checksums[0])
    ):
        raise ValueError("The stable upstream asset lacks a verified SHA-256")
    return {"name": name, "version": version, "url": url, "checksum": checksums[0]}
