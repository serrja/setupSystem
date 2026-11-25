"""Exclude active dpkg database writers before executing a locked native target."""

import hashlib
import os
import re
import sys
from pathlib import Path


def main() -> None:
    identities = set()
    try:
        for name in ("/var/lib/dpkg/lock", "/var/lib/dpkg/lock-frontend"):
            try:
                info = os.stat(name)
            except FileNotFoundError:
                continue
            identities.add((os.major(info.st_dev), os.minor(info.st_dev), info.st_ino))
        with open("/proc/locks", encoding="ascii") as locks:
            for line in locks:
                fields = line.split()
                if fields[1:4] != ["POSIX", "ADVISORY", "WRITE"]:
                    continue
                major, minor, inode = fields[5].split(":")
                if (int(major, 16), int(minor, 16), int(inode)) in identities:
                    print(
                        f"The package database is busy (PID {fields[4]}). Retry after dpkg exits.",
                        file=sys.stderr,
                    )
                    sys.exit(75)
        if "SETUP_SYSTEM_RELEASE_DIGEST" in os.environ:
            plugin = os.environ.get("SETUP_SYSTEM_RELEASE_ID", "")
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin):
                raise ValueError("Invalid confirmed extension")
            release = Path("/usr/lib/setup-system/plugin_catalog") / (plugin + ".json")
            with release.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != os.environ["SETUP_SYSTEM_RELEASE_DIGEST"]:
                raise ValueError("The confirmed Debian extension release changed")
    except (ValueError, OSError) as problem:
        print(f"Cannot check package database locks: {problem}", file=sys.stderr)
        sys.exit(75)
    os.execv("/usr/bin/make", ["/usr/bin/make", *sys.argv[1:]])


if __name__ == "__main__":
    main()
