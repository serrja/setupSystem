"""Report the distribution name and stable version target for release allocation."""

import tomllib
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import Version


def main() -> None:
    with Path("pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    print("package-name=" + canonicalize_name(project["name"], validate=True))
    print("version-target=" + Version(project["version"]).base_version)


if __name__ == "__main__":
    main()
