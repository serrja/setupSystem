"""Exclude active dpkg database writers before executing a locked native target."""

import os
import sys

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
                    f"The package database is busy (PID {fields[4]}). Retry after dpkg finishes.",
                    file=sys.stderr,
                )
                sys.exit(75)
except (ValueError, OSError) as problem:
    print(f"Cannot check package database locks: {problem}", file=sys.stderr)
    sys.exit(75)
os.execvp(sys.argv[1], sys.argv[1:])
