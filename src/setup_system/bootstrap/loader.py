"""Fixed system-Python entry for fetching a release-authorized privileged adapter."""

# sudo's interpreter is isolated. Keep this initial program independent of the user's
# app environment, HOME, proxy configuration and downloaded/local helper files.
PROGRAM = r"""
import io
import json
import os
import platform
import re
import sys
import tomllib
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected privileged source redirect")


def fetch(url, limit):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "setupSystem"})
    with opener.open(request, timeout=60) as response:
        result = response.read(limit + 1)
    if len(result) > limit:
        raise ValueError("Privileged release metadata exceeds its limit")
    return result


def resolve(request, get):
    tag, sha = request["release"], request["source_sha"]
    if (
        not isinstance(tag, str)
        or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-dev\.[0-9]+)?", tag)
        or not isinstance(sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
    ):
        raise ValueError("Invalid privileged release identity")
    api = "https://api.github.com/repos/serrja/setupSystem"
    release = json.loads(get(api + "/releases/tags/" + tag, 1048576))
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or not release.get("published_at")
    ):
        raise ValueError("A published setupSystem release is required")
    ref = json.loads(get(api + "/git/ref/tags/" + tag, 16384))
    if ref.get("ref") != "refs/tags/" + tag:
        raise ValueError("Unexpected release tag")
    obj = ref["object"]
    if obj["type"] == "tag" and re.fullmatch(r"[0-9a-f]{40}", obj["sha"]):
        obj = json.loads(get(api + "/git/tags/" + obj["sha"], 65536))["object"]
    if obj.get("type") != "commit" or obj.get("sha") != sha:
        raise ValueError("Published release tag and requested source commit disagree")
    return "https://raw.githubusercontent.com/serrja/setupSystem/" + sha + "/"


def launch():
    if (
        os.geteuid() != 0 or not sys.flags.isolated
        or not sys.flags.no_site or not sys.flags.dont_write_bytecode
    ):
        raise ValueError("Requires isolated system Python via sudo")
    release = platform.freedesktop_os_release()
    if release.get("ID") != "debian":
        raise ValueError("System operations require Debian")
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        raise ValueError("Oversized privileged request")
    request = json.loads(raw)
    source = resolve(request, fetch)
    matrix = tomllib.loads(fetch(source + "manifests/platforms.toml", 131072).decode())
    policy = matrix["hyprland"]
    machine = platform.machine()
    if (
        release.get("ID") != policy["distribution"]
        or release.get("VERSION_ID") != policy["version"]
        or release.get("VERSION_CODENAME") != policy["codename"]
        or matrix["architectures"].get(machine, machine) not in policy["architectures"]
        or sys.implementation.name != "cpython"
        or ".".join(map(str, sys.version_info[:2])) != policy["system_python"]
    ):
        raise ValueError(
            "System operations are unsupported on this Debian version/architecture"
        )
    program = fetch(
        source + "src/setup_system/bootstrap/source.py", 131072
    )
    module = {"__name__": "setup_system_release_bootstrap"}
    exec(compile(program, "<verified release adapter>", "exec"), module)
    sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    return module["main"]()


if __name__ == "__main__":
    try:
        raise SystemExit(launch())
    except (ValueError, KeyError, OSError, TypeError, RuntimeError) as exc:
        print(
            "Privileged release preflight failed: "
            + type(exc).__name__
            + ": "
            + str(exc),
            file=sys.stderr,
        )
        raise SystemExit(5) from None
"""
