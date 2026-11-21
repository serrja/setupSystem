"""Render the early shell platform gate from the validated release capabilities."""

from __future__ import annotations

import argparse
from pathlib import Path

from setup_system.infrastructure.catalog_repository import load_policy

ROOT = Path(__file__).resolve().parents[1]
START = "# Generated from manifests/platforms.toml."
END = "# End of generated platform gate."


def render() -> str:
    policy = load_policy(ROOT)
    profile = policy.hyprland
    rows = [
        START,
        "require_platform() {",
        "    [ \"$(uname -s)\" = Linux ] || fail 'This bootstrap requires Linux'",
    ]
    for field, value in (
        ("ID", profile.distribution),
        ("VERSION_ID", profile.version),
        ("VERSION_CODENAME", profile.codename),
    ):
        query = f'''$1 == "{field}" {{gsub(/"/, "", $2); print $2}}'''
        rows.append(
            f"""    [ "$(awk -F= '{query}' /etc/os-release)" = {value} ]"""
            + f" || fail 'This workstation requires {profile.distribution} {profile.version}'"
        )
    rows.append('    case "$(uname -m)" in')
    for machine in profile.architectures:
        names = sorted({machine, *(k for k, v in policy.architectures.items() if v == machine)})
        rows.append(
            f"        {'|'.join(names)}) architecture={policy.debian_architecture(machine)} ;;"
        )
    rows.extend(["        *) fail 'Unsupported architecture' ;;", "    esac", "}", END])
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    script = ROOT / "install.sh"
    original = script.read_text()
    begin, end = original.index(START), original.index(END) + len(END)
    updated = original[:begin] + render() + original[end:]
    if args.write:
        script.write_text(updated)
    elif original != updated:
        raise SystemExit("Refresh install.sh with docker/bootstrap_platform.py --write")


if __name__ == "__main__":
    main()
