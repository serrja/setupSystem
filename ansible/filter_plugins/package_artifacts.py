"""Select the exact official npm tarball or compatible PyPI wheel shown at confirmation."""

import base64
import binascii
import re
from collections.abc import Callable
from typing import TypedDict
from urllib.parse import urlsplit

from packaging.tags import sys_tags
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename


class WheelFile(TypedDict):
    filename: str
    yanked: bool
    packagetype: str
    url: str
    digests: dict[str, str]


class RegistryMetadata(TypedDict, total=False):
    dist: dict[str, str]
    urls: list[WheelFile]
    versions: dict[str, "RegistryMetadata"]
    deprecated: str


def package_artifact(
    metadata: RegistryMetadata, name: str, manager: str, version: str
) -> dict[str, str]:
    if manager == "npm":
        dist = metadata["dist"]
        filename = name.rsplit("/", 1)[-1] + "-" + version + ".tgz"
        expected = f"https://registry.npmjs.org/{name}/-/{filename}"
        if dist["tarball"] != expected:
            raise ValueError("npm did not publish this tarball at the official package URL")
        method, _, encoded = dist["integrity"].partition("-")
        try:
            digest = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid npm tarball integrity") from exc
        if method != "sha512" or len(digest) != 64:
            raise ValueError("A published SHA-512 is required for the npm tarball")
        return {"filename": filename, "url": expected, "checksum": "sha512:" + digest.hex()}
    if manager != "pypi":
        raise ValueError("Unknown native package manager")
    order = {tag: i for i, tag in enumerate(sys_tags())}
    matches = []
    for file in metadata["urls"]:
        filename = file["filename"]
        if file.get("yanked") or file["packagetype"] != "bdist_wheel":
            continue
        try:
            wheel, release, _, tags = parse_wheel_filename(filename)
        except InvalidWheelFilename:
            continue
        compatible = [order[tag] for tag in tags if tag in order]
        if canonicalize_name(name) != wheel or str(release) != version or not compatible:
            continue
        url = urlsplit(file["url"])
        if not (
            url.scheme == "https"
            and url.netloc == "files.pythonhosted.org"
            and not url.query
            and not url.fragment
            and re.fullmatch(
                r"/packages/[a-f0-9]{2}/[a-f0-9]{2}/[a-f0-9]+/" + re.escape(filename), url.path
            )
            and re.fullmatch(r"[a-zA-Z0-9_.+-]+\.whl", filename)
        ):
            raise ValueError("Unexpected PyPI wheel URL or filename")
        sha = file["digests"]["sha256"]
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise ValueError("Missing published PyPI wheel SHA-256")
        matches.append(
            (
                min(compatible),
                filename,
                {"filename": filename, "url": file["url"], "checksum": "sha256:" + sha},
            )
        )
    if not matches:
        raise ValueError(f"No compatible verified {name} wheel for this Python and architecture")
    return min(matches, key=lambda item: item[:2])[2]


def registry_release(info: RegistryMetadata, name: str, series: dict[str, int]) -> RegistryMetadata:
    if name not in series:
        return info
    releases = []
    for version, manifest in info["versions"].items():
        match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version)
        if match and int(match[1]) == series[name] and not manifest.get("deprecated"):
            releases.append((tuple(map(int, match.groups())), manifest))
    if not releases:
        raise ValueError(f"No compatible stable release found for {name}")
    return max(releases, key=lambda item: item[0])[1]


class FilterModule:
    def filters(self) -> dict[str, Callable[..., RegistryMetadata | dict[str, str]]]:
        return {"package_artifact": package_artifact, "registry_release": registry_release}
