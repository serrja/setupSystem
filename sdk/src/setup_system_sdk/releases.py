"""Verify GitHub release identities and published asset digests."""

import re

from pydantic import BaseModel, StrictBool


class ReleaseAsset(BaseModel):
    name: str
    browser_download_url: str
    digest: str | None = None


class GithubRelease(BaseModel):
    draft: StrictBool
    prerelease: StrictBool
    tag_name: str
    published_at: str
    assets: tuple[ReleaseAsset, ...]

    def asset(self, repository: str, name: str) -> tuple[str, str]:
        url = f"https://github.com/{repository}/releases/download/{self.tag_name}/{name}"
        matches = [
            row for row in self.assets if row.name == name and row.browser_download_url == url
        ]
        if (
            len(matches) != 1
            or not matches[0].digest
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", matches[0].digest)
        ):
            raise ValueError("Official release lacks a unique verified asset")
        return url, matches[0].digest.removeprefix("sha256:")
