"""Operate on the bounded regional, login, boot and enterprise-policy recovery contracts."""

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from setup_system.domain.login import LoginConsent
from setup_system.infrastructure import firefox_policy, graphical_unlock, happ_facts, regional
from setup_system.infrastructure.identity import resolve_target, user_environment
from setup_system.infrastructure.login import DesktopSystem
from setup_system.infrastructure.native_target import current_target
from setup_system.infrastructure.system_paths import protected

ROOT = Path(__file__).resolve().parents[2]


def desktop(xdg):
    target = resolve_target(None)
    env = user_environment(target, {k: Path(v) for k, v in xdg.items()})
    return DesktopSystem(ROOT, run=subprocess.run), target, env


def policy_stamp(path=Path("/etc/firefox/policies/policies.json")):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            raise ValueError("Unsafe Firefox enterprise policy")
        raw = path.read_bytes()
        return [
            info.st_dev,
            info.st_ino,
            stat.S_IMODE(info.st_mode),
            hashlib.sha256(raw).hexdigest(),
        ]
    except FileNotFoundError:
        return None
    except PermissionError:
        return "root-only"


def policy_matches(stamp):
    return bool(
        isinstance(stamp, list)
        and stamp[2] == 0o644
        and stamp[3] == hashlib.sha256(firefox_policy.desired_bytes()).hexdigest()
    )


def main():
    request = json.load(sys.stdin)
    op = sys.argv[1]
    settings = request["settings"]
    actions, reasons, observations = [], [], {}
    if op == "happ-inspect" and "happ" in settings.get("apps", []):
        unit = happ_facts.unit_info()
        installed = happ_facts.installed_version()
        if (
            request.get("exact")
            or installed is None
            or installed.split("-")[0] != request["version"]
        ):
            happ_facts.require_idle(installed, request["version"], unit)
        print(json.dumps({"unit": unit, "installed": installed}))
        return
    if op == "plan":
        if request["section"] == "base":
            for kind in ("timezone", "locale", "keyboard"):
                if settings.get(kind) is not None:
                    desired = regional.validate(kind, settings[kind])
                    actual = regional.actual(kind)
                    observations[kind] = actual
                    if not regional.equivalent(kind, actual, desired):
                        actions.append(f"Set {kind} to {desired}")
            if settings.get("graphical_login"):
                manager, account, env = desktop(request["xdg"])
                plan = manager.plan(account, env)
                observations["login"] = plan.consent.model_dump(mode="json")
                if (
                    any(
                        item.observed != hashlib.sha256(item.content).hexdigest()
                        or item.observed_mode != item.mode
                        for item in plan.files
                    )
                    or not plan.grub_managed
                    or plan.previous_target != "graphical.target"
                    or not plan.consent.manager_enabled
                ):
                    actions.append("Enable greetd with a recoverable GRUB menu")
            if settings.get("plymouth"):
                unlock = graphical_unlock.inspect_unlock()
                observations["unlock"] = unlock.model_dump(mode="json")
                reasons.extend(unlock.blockers)
                if not unlock.blockers:
                    observations["unlock_current"] = graphical_unlock.is_current(ROOT)
                    if not observations["unlock_current"]:
                        actions.append(unlock.description)
        elif "firefox" in settings.get("apps", []):
            observations["firefox"] = policy_stamp()
            if not policy_matches(observations["firefox"]):
                actions.append("Apply Firefox enterprise privacy policy")
        print(json.dumps({"actions": actions, "blockers": reasons, "observed": observations}))
        return
    if os.geteuid() != 0 or ROOT != Path("/usr/lib/setup-system") or not current_target()[1]:
        raise ValueError("System changes require authenticated Debian 13 system execution")
    protected(ROOT)
    before = request["observed"]
    if op == "happ-unit" and "happ" in settings.get("apps", []):
        info = happ_facts.unit_info()
        if (
            not any(
                path.is_file()
                for path in (
                    Path("/etc/systemd/system/happd.service"),
                    Path("/usr/lib/systemd/system/happd.service"),
                )
            )
            or happ_facts.installed_version() is None
        ):
            raise ValueError("The verified Debian package did not provide its service")
        print(json.dumps(info))
    elif op == "region":
        for kind in ("timezone", "locale", "keyboard"):
            if settings.get(kind) is not None:
                original = before[kind]
                if original is None and kind == "keyboard" and regional.actual(kind) == "us":
                    original = "us"
                regional.apply(kind, settings[kind], original)
    elif op == "login" and settings.get("graphical_login"):
        manager, account, env = desktop(request["xdg"])
        changed = manager.apply(
            manager.revalidate(LoginConsent.model_validate(before["login"]), account, env)
        )
        print(json.dumps({"changed": changed}))
    elif op == "firefox" and "firefox" in settings.get("apps", []):
        if before["firefox"] != "root-only" and before["firefox"] != policy_stamp():
            raise ValueError("Firefox policy changed after confirmation")
        firefox_policy.apply(firefox_policy.inspect())
        if not policy_matches(policy_stamp()):
            raise RuntimeError("Firefox enterprise policy readback failed")
    elif op == "boot" and settings.get("plymouth"):
        unlock = before["unlock"]

        def install(packages):
            stage = Path(request["apt_work"])
            if stage.parent != Path("/run/setup-system") or not stage.name.startswith(
                "setup-system-apt-"
            ):
                raise ValueError("Expected a private root APT stage")
            protected(stage)
            subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    str(ROOT / "ansible/files/apt_apply.py"),
                    str(stage),
                ],
                check=True,
            )

        changed = graphical_unlock.apply_unlock(
            unlock["identity"], root=ROOT, packages=unlock["packages"], install=install
        )
        print(json.dumps({"changed": changed}))
    else:
        raise ValueError("Unknown or unselected system operation")


if __name__ == "__main__":
    main()
