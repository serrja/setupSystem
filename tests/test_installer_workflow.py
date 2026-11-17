"""One installation coordinates download, cleanup and offline apply through real privilege argv."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from setup_system import installation
from setup_system.application import installer, interaction
from setup_system.application.installer import InstallationService, InstallRequest, Operation
from setup_system.network_session import NetworkReady

ROOT = Path(__file__).resolve().parents[1]
PROXY = "http://127.0.0.1:18080"


@dataclass
class Workflow:
    service: InstallationService
    target: installation.TargetUser
    events: list[str]
    marks: list[tuple[str, str | None, tuple[str, ...]]]
    state: dict[str, Any]
    network: Any
    subscription: str


def workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None = None, *, tty: bool = True
) -> Workflow:
    home = tmp_path / "desktop home"
    home.mkdir()
    target = installation.TargetUser("alice", os.getuid(), os.getgid(), home, "/bin/bash")
    paths = installation.SystemPaths(journal=tmp_path / "install.json")
    if failure != "start":
        paths.journal.write_text('{"schema":1,"completed":[]}\n')
    events: list[str] = []
    marks: list[tuple[str, str | None, tuple[str, ...]]] = []
    state: dict[str, Any] = {"stage": "metadata", "status": "running", "completed": []}
    subscription = (
        "https://subscription.invalid/" + hashlib.sha256(str(tmp_path).encode()).hexdigest()
    )
    monkeypatch.setattr(installation, "resolve_target", lambda user: target)
    monkeypatch.setattr(installation, "current_packages", lambda root: ["hyprland=fixture"])
    monkeypatch.setattr(installation, "validate_source_access", lambda *a, **kw: None)
    monkeypatch.setattr(installation, "validate_service_manager", lambda **kw: None)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    monkeypatch.setattr(installer.os, "getgrouplist", lambda name, gid: [gid, 44])
    monkeypatch.setattr(interaction.sys.stdin, "isatty", lambda: tty)
    monkeypatch.setattr(interaction.getpass, "getpass", lambda prompt: subscription)

    class Runtime:
        def lock(self) -> None:
            return None

        def inputs(self) -> list[tuple[Path, bool]]:
            return []

        def prepare(self) -> Path:
            pytest.fail("Root service attempted a user runtime build")

        def existing(self) -> Path:
            pytest.fail("Root service attempted user runtime execution")

    class Network:
        closed = False
        close_calls = 0
        cleanup_failed = False

        def start(self, secret: str | None) -> NetworkReady:
            assert secret == subscription
            events.append("network-start")
            if failure == "start":
                raise RuntimeError("Fixture temporary network startup failure")
            return NetworkReady(PROXY, home / "temporary/happ.deb")

        def close(self) -> None:
            self.close_calls += 1
            if self.closed:
                return
            if failure == "cleanup" and not self.cleanup_failed:
                self.cleanup_failed = True
                events.append("network-close-failed")
                raise RuntimeError("Fixture temporary client cleanup failure")
            events.append("network-close")
            self.closed = True

    network = Network()

    def network_factory(*args: object, **options: object) -> Network:
        assert args[0] == ROOT and args[1] == target
        environment = args[2]
        assert isinstance(environment, dict) and environment["HOME"] == str(home)
        assert "GH_TOKEN" not in environment
        assert options["subscription_file"] is None
        events.append("network-create")
        return network

    class Packages:
        def __enter__(self) -> Packages:
            events.append("download-only")
            if failure == "download":
                state.update(stage="resolution", status="failed")
                raise subprocess.CalledProcessError(
                    100, ["fixture-apt"], stderr="Fixture resolution failure"
                )
            state.update(stage="user-prefetch", status="prepared")
            state["completed"] = ["metadata", "resolution", "downloads"]
            return self

        def __exit__(self, *exception: object) -> None:
            if exception[0] is not None:
                state["status"] = "failed"

        def install(self) -> None:
            assert network.closed
            events.append("install")
            state.update(stage="packages", status="running")
            if failure == "install":
                raise subprocess.CalledProcessError(
                    100, ["fixture-offline-apt"], stderr="Fixture package failure"
                )
            state["completed"] += ["packages", "source"]
            state.update(stage="happ-service", status="packages-complete")

        def activate_happ(self) -> None:
            assert network.closed and "install" in events
            events.append("activate")
            state["stage"] = "happ-service"
            if failure == "activate":
                raise RuntimeError("Fixture vendor Happ daemon failure")
            state["completed"].append("happ-service")
            state.update(stage="user-runtime", status="system-complete")

    def packages_factory(root: Path, **options: object) -> Packages:
        assert root == ROOT and options["target"] == target
        assert options["proxy"] == PROXY
        assert options["happ"] == home / "temporary/happ.deb"
        assert options["paths"] == paths
        return Packages()

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        environment = options["env"]
        assert isinstance(environment, dict)
        assert environment["HOME"] == str(home)
        assert (
            not {"GH_TOKEN", "PYTHONPATH", "SUDO_USER", "DBUS_SESSION_BUS_ADDRESS"}
            & environment.keys()
        )
        assert subscription not in argv and subscription not in environment.values()
        assert options["cwd"] == home and options["umask"] == 0o077
        if Operation.PREPARE_USER.value in argv:
            assert not network.closed and "download-only" in events
            assert argv[:3] == [sys.executable, "-I", "-B"]
            assert options["user"] == target.uid and options["group"] == target.gid
            assert options["extra_groups"] == [target.gid, 44]
            assert environment["HTTP_PROXY"] == environment["HTTPS_PROXY"] == PROXY
            assert "UV_OFFLINE" not in environment
            events.append("user-prefetch")
            code = 5 if failure == "prefetch" else 0
        else:
            assert network.closed and "activate" in events
            assert argv[:12] == [
                "/usr/bin/unshare",
                "--net",
                "--",
                "/usr/bin/setpriv",
                "--reuid",
                str(target.uid),
                "--regid",
                str(target.gid),
                "--init-groups",
                sys.executable,
                "-I",
                "-B",
            ]
            assert Operation.USER_STAGE.value in argv
            assert not {"user", "group", "extra_groups"} & options.keys()
            assert environment["UV_OFFLINE"] == "true"
            assert not {"HTTP_PROXY", "HTTPS_PROXY"} & environment.keys()
            events.append("user-config")
            code = 5 if failure == "user" else 0
        return subprocess.CompletedProcess(argv, code, "fixture user output\n", "")

    def mark(
        path_options: installation.SystemPaths,
        status: str,
        *,
        stage: str | None = "user",
        completed: tuple[str, ...] = (),
    ) -> None:
        assert path_options == paths and status in {"complete", "failed", "running"}
        assert set(completed) <= {"user-downloads", "user-configuration"}
        marks.append((status, stage, completed))
        state["status"] = status
        if stage is not None:
            state["stage"] = stage
        state["completed"] = list(dict.fromkeys([*state["completed"], *completed]))
        events.append("journal-" + status)

    monkeypatch.setattr(installation, "run_command", execute)
    monkeypatch.setattr(installation, "mark_user_stage", mark)
    service = InstallationService(
        ROOT, runtime=Runtime(), network=network_factory, packages=packages_factory, paths=paths
    )
    return Workflow(service, target, events, marks, state, network, subscription)


def test_one_apply_orders_all_downloads_cleanup_and_offline_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = workflow(tmp_path, monkeypatch)
    assert fixture.service.run(InstallRequest(Operation.APPLY, user="alice", yes=True)) == 0
    assert fixture.events == [
        "network-create",
        "network-start",
        "download-only",
        "user-prefetch",
        "network-close",
        "install",
        "activate",
        "user-config",
        "journal-complete",
    ]
    assert fixture.marks == [("complete", "user", ("user-downloads", "user-configuration"))]
    assert fixture.network.closed and fixture.network.close_calls == 2
    assert fixture.state["completed"] == [
        "metadata",
        "resolution",
        "downloads",
        "packages",
        "source",
        "happ-service",
        "user-downloads",
        "user-configuration",
    ]
    output = capsys.readouterr()
    assert (
        "Installation completed" in output.out
        and fixture.subscription not in output.out + output.err
    )
    assert not list(fixture.target.home.iterdir())


@pytest.mark.parametrize(
    "failure", ["start", "download", "prefetch", "cleanup", "install", "activate", "user"]
)
def test_every_failed_phase_closes_the_temporary_client_without_false_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    fixture = workflow(tmp_path, monkeypatch, failure)
    assert fixture.service.run(InstallRequest(Operation.APPLY, user="alice", yes=True)) == 5
    assert fixture.network.closed and fixture.network.close_calls >= 1
    output = capsys.readouterr()
    assert "Installation completed" not in output.out
    assert fixture.subscription not in output.out + output.err
    if failure in {"start", "download", "prefetch", "cleanup"}:
        assert "install" not in fixture.events and "activate" not in fixture.events
    if failure in {"install", "activate"}:
        assert "user-config" not in fixture.events
    if failure == "start":
        assert not fixture.marks
    else:
        status, stage, completed = fixture.marks[-1]
        assert status == "failed"
        assert (
            stage
            == {
                "download": None,
                "prefetch": "user-downloads",
                "cleanup": "temporary-network-cleanup",
                "install": None,
                "activate": None,
                "user": "user",
            }[failure]
        )
        expected = (
            ("user-downloads",) if failure in {"cleanup", "install", "activate", "user"} else ()
        )
        assert completed == expected
        assert "user-configuration" not in fixture.state["completed"]
    assert not list(fixture.target.home.iterdir())


@pytest.mark.parametrize("input_file", [False, True], ids=["missing-option", "missing-file"])
def test_unattended_missing_private_input_stops_before_network_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_file: bool,
) -> None:
    fixture = workflow(tmp_path, monkeypatch, tty=False)
    request = InstallRequest(
        Operation.APPLY,
        user="alice",
        yes=True,
        subscription_file=fixture.target.home / "absent-private-input" if input_file else None,
    )
    assert fixture.service.run(request) == 2
    assert fixture.events == [] and not fixture.marks
    assert fixture.network.close_calls == 0
    assert not list(fixture.target.home.iterdir())


def test_default_interactive_apply_asks_once_and_keeps_subscription_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = workflow(tmp_path, monkeypatch)
    prompts: list[str] = []

    def consent(prompt: str) -> str:
        prompts.append(prompt)
        return "yes"

    monkeypatch.setattr("builtins.input", consent)
    assert fixture.service.run(InstallRequest(Operation.APPLY, user="alice")) == 0
    assert len(prompts) == 1 and fixture.subscription not in prompts[0]


def test_network_factory_failure_reports_the_stage_without_false_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = workflow(tmp_path, monkeypatch)

    def unavailable(*_args: object, **_kwargs: object) -> installer.NetworkPort:
        raise RuntimeError("Fixture temporary network adapter unavailable")

    fixture.service.network = unavailable
    assert fixture.service.run(InstallRequest(Operation.APPLY, user="alice", yes=True)) == 5
    output = capsys.readouterr()
    assert "FAILED installation stage temporary-network" in output.err
    assert "Installation completed" not in output.out
    assert fixture.subscription not in output.out + output.err
    assert fixture.marks == [("failed", "temporary-network", ())]
    assert fixture.network.close_calls == 0
    assert fixture.events == ["journal-failed"]
