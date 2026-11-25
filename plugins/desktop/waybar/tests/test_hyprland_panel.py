"""Exercise the Debian Waybar bridge with local fixtures, never a real desktop."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from setup_plugin_waybar import FOLDER, Plugin, Settings

from setup_system_sdk import Inputs

ASSETS = FOLDER / "assets"
panel = types.ModuleType("managed_hyprland_panel")
panel.__file__ = str(ASSETS / "panel.py")
sys.modules[panel.__name__] = panel
exec(compile((ASSETS / "panel.py").read_text(), panel.__file__, "exec"), panel.__dict__)


@pytest.fixture
def waybar_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    home = tmp_path.resolve()
    monkeypatch.setenv("HOME", str(home))
    for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{kind}_HOME", str(home / kind.lower()))
    files = Plugin().files("configure", Settings(), Inputs())
    return next(data.decode() for path, data in files.items() if path.name == "waybar.json")


def device(root: Path, group: str, name: str, **attributes: str) -> None:
    path = root / "class" / group / name
    path.mkdir(parents=True)
    for key, value in attributes.items():
        (path / key).write_text(value + "\n")


def test_hardware_selects_real_system_batteries_and_native_backlight(tmp_path: Path) -> None:
    device(tmp_path, "power_supply", "BAT7", type="Battery", scope="System", present="1")
    device(tmp_path, "power_supply", "mouse", type="Battery", scope="Device", present="1")
    device(tmp_path, "power_supply", "absent", type="Battery", scope="System", present="0")
    device(tmp_path, "power_supply", "AC", type="Mains", online="1")
    device(
        tmp_path, "backlight", "firmware", type="firmware", max_brightness="100", brightness="50"
    )
    device(tmp_path, "backlight", "native", type="raw", max_brightness="400", brightness="80")
    assert panel.hardware(tmp_path) == (("BAT7",), "native")


def test_generated_panel_has_no_fake_desktop_battery_or_legacy_dispatch(
    waybar_template: str, tmp_path: Path
) -> None:
    template = json.loads(waybar_template)
    config = panel.configuration(template, tmp_path / "cache with 'quotes", tmp_path)
    assert "battery" not in config["modules-right"]
    assert "backlight" not in config["modules-right"]
    assert "hyprland/workspaces" not in config["modules-left"]
    assert "timezone" not in config["clock"]
    assert "%Z" in config["clock"]["tooltip-format"]
    assert len([key for key in config if key.startswith("custom/workspace-")]) == 10
    for number in range(1, 11):
        module = config[f"custom/workspace-{number}"]
        assert f"hl.dsp.focus({{ workspace = {number} }})" in module["on-click"]
        assert module["signal"] == panel.UPDATE_SIGNAL
        assert "python" not in module["exec"]


def test_battery_instances_show_each_real_device_without_fabricated_aggregate(
    waybar_template: str,
    tmp_path: Path,
) -> None:
    for name in ("BAT0", "BAT1"):
        device(tmp_path, "power_supply", name, type="Battery", scope="System", present="1")
    config = panel.configuration(json.loads(waybar_template), tmp_path, tmp_path)
    batteries = [config[name] for name in config["modules-right"] if name.startswith("battery#")]
    assert [item["bat"] for item in batteries] == ["BAT0", "BAT1"]
    assert all("format-charging" in item and "format-full" in item for item in batteries)


def test_workspace_state_has_actual_active_and_occupied_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        panel,
        "_query",
        lambda topic: {"id": 2} if topic == "activeworkspace" else [{"id": 1, "windows": 3}],
    )
    state = panel.workspace_state()
    assert "active" in state[2]["class"] and "occupied" in state[1]["class"]
    assert "active" not in state[1]["class"]
    monkeypatch.setattr(panel, "_query", lambda *_: [])
    unavailable = panel.workspace_state()
    assert all("active" not in row["class"] for row in unavailable.values())
    assert all("unavailable" in row["tooltip"] for row in unavailable.values())


def executable(path: Path, source: str) -> None:
    path.write_text(f"#!{sys.executable}\n" + source)
    path.chmod(0o700)


def test_oversized_workspace_reply_is_rejected_while_hyprctl_is_running(tmp_path, monkeypatch):
    executable(
        tmp_path / "hyprctl",
        "import os,time\nos.write(1, b'x' * 4096)\ntime.sleep(30)\n",
    )
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(panel, "MAX_REPLY", 1024)
    with pytest.raises(ValueError, match="exceeds its bound"):
        panel._query("workspaces")


def test_actual_custom_module_commands_quote_cache_paths_and_use_lua_ipc(
    waybar_template: str, tmp_path: Path
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    record = tmp_path / "calls"
    executable(
        tools / "hyprctl",
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['CALLS']).write_text(json.dumps(sys.argv[1:]))\n",
    )
    state = tmp_path / "cache with 'quotes"
    state.mkdir()
    panel.publish(state, {1: {"text": "1", "class": ["active"]}})
    config = panel.configuration(json.loads(waybar_template), state, tmp_path)
    module = config["custom/workspace-1"]
    env = {"PATH": str(tools) + os.pathsep + os.defpath, "CALLS": str(record)}
    result = subprocess.run(
        ["sh", "-c", module["exec"]], env=env, capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == {"text": "1", "class": ["active"]}
    assert (state / "ready").is_file()
    subprocess.run(["sh", "-c", module["on-click"]], env=env, check=True)
    assert json.loads(record.read_text()) == ["dispatch", "hl.dsp.focus({ workspace = 1 })"]


def test_broker_observes_real_unix_events_and_refreshes_one_owned_waybar(
    waybar_template: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A proc cwd alias keeps AF_UNIX addresses below108 bytes inside deep G03 fixtures.
    monkeypatch.chdir(tmp_path)
    tools, runtime = tmp_path / "tools", tmp_path / "runtime"
    tools.mkdir()
    runtime.mkdir(mode=0o700)
    endpoint = runtime / "hypr/fixture/.socket2.sock"
    endpoint.parent.mkdir(parents=True)
    status, ready, seen = tmp_path / "ipc.json", tmp_path / "ready", tmp_path / "observed.json"
    status.write_text('{"active":1}')
    executable(
        tools / "hyprctl",
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "a=json.loads(Path(os.environ['IPC_STATE']).read_text())['active']\n"
        "value={'id':a} if sys.argv[-1]=='activeworkspace' else [{'id':a,'windows':1}]\n"
        "print(json.dumps(value))\n",
    )
    executable(
        tools / "waybar",
        """import json,os,signal,sys,time
from pathlib import Path
config=Path(sys.argv[sys.argv.index('-c')+1])
folder=config.parent
signal_number=signal.SIGRTMIN+8
def update(*args):
 rows={n:json.loads((folder/f'workspace-{n}.json').read_text()) for n in range(1,11)}
 Path(os.environ['OBSERVED']).write_text(json.dumps(rows))
 if 'active' in rows[2]['class']:
  raise SystemExit(0)
signal.signal(signal_number,update)
(folder/'ready').touch()
Path(os.environ['WAYBAR_READY']).touch()
while True:
 signal.pause()
""",
    )
    environment = {
        "PATH": str(tools) + os.pathsep + os.defpath,
        "HOME": str(tmp_path),
        "XDG_RUNTIME_DIR": "/proc/self/cwd/runtime",
        "HYPRLAND_INSTANCE_SIGNATURE": "fixture",
        "IPC_STATE": str(status),
        "WAYBAR_READY": str(ready),
        "OBSERVED": str(seen),
    }
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "waybar.json").write_text(waybar_template)
    (profile / "style.css").touch()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind("runtime/hypr/fixture/.socket2.sock")
        server.listen(1)
        server.settimeout(10)
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(ASSETS / "panel.py"), str(profile)],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            connection, _ = server.accept()
            with connection:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert ready.exists()
                status.write_text('{"active":2}')
                connection.sendall(b"workspacev2>>2,2\n")
                out, error = process.communicate(timeout=10)
            assert process.returncode == 0, out + error
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    rows = json.loads(seen.read_text())
    assert "active" in rows["2"]["class"] and "active" not in rows["1"]["class"]
    assert not list(runtime.glob("setup-system-panel-*"))


def test_optional_audio_bus_failure_does_not_prevent_desktop_helpers(tmp_path: Path) -> None:
    tools, runtime, config, data = (
        tmp_path / name for name in ("tools", "runtime", "config", "data")
    )
    for path in (tools, runtime, config, data):
        path.mkdir(mode=0o700)
    calls = tmp_path / "calls"
    profile = config / "hypr/setup-system"
    profile.mkdir(parents=True)
    (profile / "panel.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "with Path(os.environ['CALLS']).open('a') as f:f.write('panel\\n')\n"
    )
    for name in ("mako", "hypridle", "swaybg", "wl-paste", "fixture-agent"):
        executable(
            tools / name,
            "import os,sys\n"
            "from pathlib import Path\n"
            "with Path(os.environ['CALLS']).open('a') as f:f.write(Path(sys.argv[0]).name+'\\n')\n",
        )
    for name in ("dbus-update-activation-environment", "systemctl"):
        executable(tools / name, "raise SystemExit(1)\n")
    executable(tools / "hyprctl", "import time\ntime.sleep(0.4)\nraise SystemExit(1)\n")
    script = tmp_path / "session.sh"
    script.write_text(
        (
            Path(__file__).resolve().parents[4]
            / "plugins/desktop/hyprland/src/setup_plugin_hyprland/assets/session.sh"
        )
        .read_text()
        .replace("/usr/libexec/hyprpolkitagent", "fixture-agent")
    )
    environment = {
        "PATH": str(tools) + os.pathsep + os.defpath,
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(config),
        "XDG_DATA_HOME": str(data),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_RUNTIME_DIR": str(runtime),
        "HYPRLAND_INSTANCE_SIGNATURE": "fixture",
        "CALLS": str(calls),
    }
    result = subprocess.run(
        ["sh", str(script)], env=environment, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
    started = set(calls.read_text().splitlines())
    assert {"panel", "mako", "hypridle", "swaybg", "fixture-agent", "wl-paste"} <= started
    assert (
        "Audio user units did not start"
        in (tmp_path / "state/setup-system/hyprland-session.log").read_text()
    )


def test_session_term_exits_monitor_loop_and_reaps_owned_helpers(tmp_path: Path) -> None:
    tools, runtime, config, data, started, stopped = (
        tmp_path / name for name in ("tools", "runtime", "config", "data", "started", "stopped")
    )
    for path in (tools, runtime, config, data, started, stopped):
        path.mkdir(mode=0o700)
    profile = config / "hypr/setup-system"
    profile.mkdir(parents=True)
    helper = """import os,signal
from pathlib import Path
pid=str(os.getpid())
def stop(*args):
 Path(os.environ['STOPPED'],pid).touch()
 raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
Path(os.environ['STARTED'],pid).touch()
while True:
 signal.pause()
"""
    (profile / "panel.py").write_text(helper)
    for name in ("mako", "hypridle", "swaybg", "wl-paste", "fixture-agent"):
        executable(tools / name, helper)
    executable(tools / "dbus-update-activation-environment", "raise SystemExit(0)\n")
    executable(tools / "systemctl", "raise SystemExit(1)\n")
    executable(tools / "hyprctl", "raise SystemExit(0)\n")
    script = tmp_path / "session.sh"
    script.write_text(
        (
            Path(__file__).resolve().parents[4]
            / "plugins/desktop/hyprland/src/setup_plugin_hyprland/assets/session.sh"
        )
        .read_text()
        .replace("/usr/libexec/hyprpolkitagent", "fixture-agent")
    )
    environment = {
        "PATH": str(tools) + os.pathsep + os.defpath,
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(config),
        "XDG_DATA_HOME": str(data),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_RUNTIME_DIR": str(runtime),
        "HYPRLAND_INSTANCE_SIGNATURE": "fixture",
        "STARTED": str(started),
        "STOPPED": str(stopped),
    }
    process = subprocess.Popen(
        ["sh", str(script)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while len(list(started.iterdir())) != 7 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(list(started.iterdir())) == 7
        process.send_signal(signal.SIGTERM)
        out, error = process.communicate(timeout=8)
        assert process.returncode == 0, out + error
        assert {path.name for path in started.iterdir()} == {
            path.name for path in stopped.iterdir()
        }
        for path in started.iterdir():
            with pytest.raises(ProcessLookupError):
                os.kill(int(path.name), 0)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)


def test_boolean_workspace_id_cannot_mark_first_workspace_active(monkeypatch):
    monkeypatch.setattr(
        panel, "_query", lambda topic: {"id": True} if topic == "activeworkspace" else []
    )
    state = panel.workspace_state()
    assert all("active" not in row["class"] for row in state.values())


@pytest.mark.parametrize("signature", [".", "..", "nested/name", ""])
def test_runtime_requires_one_real_session_signature(tmp_path, monkeypatch, signature):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", signature)
    with pytest.raises(ValueError, match="private Hyprland runtime"):
        panel._runtime()


@pytest.mark.parametrize("oversized", [True, False])
def test_broker_consumes_fragmented_events_and_reaps_the_process_after_eof(
    waybar_template, tmp_path, monkeypatch, oversized
):
    import threading

    runtime = tmp_path / "runtime"
    endpoint = runtime / "hypr/test/.socket2.sock"
    endpoint.parent.mkdir(parents=True)
    runtime.chmod(0o700)
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "waybar.json").write_text(waybar_template)
    (profile / "style.css").touch()
    tools = tmp_path / "tools"
    tools.mkdir()
    pid = tmp_path / "pid"
    started = tmp_path / "started"
    executable(
        tools / "waybar",
        "import os,signal,sys\nfrom pathlib import Path\n"
        "path=Path(sys.argv[sys.argv.index('-c')+1])\n"
        "signal.signal(signal.SIGRTMIN+8, lambda *args: None)\n"
        "Path(os.environ['WPID']).write_text(str(os.getpid()))\n"
        "Path(os.environ['WFOLDER']).write_text(str(path.parent))\n"
        "(path.parent/'ready').touch()\n"
        "while True: signal.pause()\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setenv("WPID", str(pid))
    monkeypatch.setenv("WFOLDER", str(started))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/proc/self/cwd/runtime")
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "test")
    state = {"active": 1}
    monkeypatch.setattr(panel, "MAX_REPLY", 48)
    monkeypatch.setattr(
        panel, "_query", lambda topic: {"id": state["active"]} if topic == "activeworkspace" else []
    )
    observed = []
    errors = []

    def serve(server):
        try:
            channel, _ = server.accept()
            with channel:
                deadline = time.monotonic() + 5
                while not started.is_file():
                    if time.monotonic() > deadline:
                        raise TimeoutError("Fixture Waybar did not start")
                    time.sleep(0.01)
                if oversized:
                    channel.sendall(b"x" * 100)
                    return
                folder = Path(started.read_text())
                state["active"] = 3
                channel.sendall(b"ignored>>x\nwork")
                channel.sendall(b"spacev2>>3,3\n")
                while True:
                    flags = json.loads((folder / "workspace-3.json").read_text())["class"]
                    if "active" in flags:
                        observed.append(True)
                        return
                    if time.monotonic() > deadline:
                        raise TimeoutError("No update for complete workspace event")
                    time.sleep(0.01)
        except BaseException as exc:
            errors.append(exc)

    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind("runtime/hypr/test/.socket2.sock")
            server.listen(1)
            server.settimeout(5)
            peer = threading.Thread(target=serve, args=(server,))
            peer.start()
            try:
                if oversized:
                    with pytest.raises(ValueError, match="event exceeds"):
                        panel.run(profile)
                else:
                    assert panel.run(profile) == 0
            finally:
                peer.join(6)
            assert not peer.is_alive() and not errors
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
    assert observed == ([] if oversized else [True])
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)
    assert not list(runtime.glob("setup-system-panel-*"))
