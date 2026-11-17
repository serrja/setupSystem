"""Exercise the terminal workflow without APT, credentials or a graphical session."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from textual.widgets import Input, Label

from setup_system.application.installer import InstallRequest, Operation
from setup_system.application.interaction import Interaction
from setup_system.tui.installer import InstallationApp, Prompt


@dataclass
class Service:
    interaction: Interaction | None = None
    requests: list[InstallRequest] = field(default_factory=list)
    applied: bool = False
    code: int = 0

    def run(self, request: InstallRequest) -> int:
        assert self.interaction is not None
        self.requests.append(request)
        self.interaction.write("Checked complete fixture plan")
        if request.operation == Operation.PLAN:
            return 0
        if not self.interaction.confirm("Apply complete fixture plan?"):
            return 2
        secret = self.interaction.subscription()
        assert secret == "https://fixture.invalid/q-subscription"
        self.applied = True
        self.interaction.write("Stage: fixture configuration")
        return self.code


def application(tmp_path: Path, service: Service) -> InstallationApp:
    app = InstallationApp(tmp_path, InstallRequest(Operation.PLAN), service=service)
    service.interaction = app.interaction
    return app


def test_open_and_plan_are_read_only_and_remain_responsive(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Service()
        app = application(tmp_path, service)
        async with app.run_test(size=(100, 40)) as pilot:
            assert not service.requests
            await pilot.click("#plan")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert [r.operation for r in service.requests] == [Operation.PLAN]
            assert not app.busy and app.last_code == 0
            assert not service.applied and not list(tmp_path.iterdir())

    asyncio.run(exercise())


def test_cancelled_confirmation_never_applies(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Service()
        app = application(tmp_path, service)
        async with app.run_test(size=(100, 40)) as pilot:
            app.query_one("#user", Input).value = "alice"
            await pilot.click("#apply")
            await pilot.pause()
            assert isinstance(app.screen, Prompt)
            await pilot.click("#cancel")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not service.applied and app.last_code == 2
            assert service.requests[0].user == "alice"
            assert not service.requests[0].yes

    asyncio.run(exercise())


def test_secret_input_and_failed_stage_do_not_report_success(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Service(code=5)
        app = application(tmp_path, service)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.click("#apply")
            await pilot.pause()
            await pilot.click("#confirm")
            await pilot.pause()
            secret = app.screen.query_one("#secret", Input)
            assert secret.password
            secret.value = "https://fixture.invalid/"
            secret.focus()
            await pilot.press("q")
            secret.value += "-subscription"
            await pilot.click("#confirm")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert service.applied and app.last_code == 5
            assert "Stopped" in str(app.query_one("#status", Label).render())
            assert secret.value == ""
            assert not app.busy

    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(80, 25), (100, 40)])
def test_keyboard_only_plan_confirmation_and_secret_submission(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    async def exercise() -> None:
        service = Service(code=5)
        app = application(tmp_path, service)
        async with app.run_test(size=size) as pilot:
            assert app.focused is app.query_one("#user", Input)
            await pilot.press(*"alice", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert [(r.operation, r.user) for r in service.requests] == [(Operation.PLAN, "alice")]
            assert not service.applied
            assert app.focused is app.query_one("#plan")
            await pilot.press("tab", "enter")
            await pilot.pause()
            assert isinstance(app.screen, Prompt)
            assert app.focused is app.screen.query_one("#confirm")
            assert not service.applied
            assert "Checked complete fixture plan" in app.screen.plan
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, Prompt) and app.screen.secret
            secret = app.screen.query_one("#secret", Input)
            assert app.focused is secret and secret.password
            await pilot.press(*"https://fixture.invalid/q-subscription", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert service.applied and app.last_code == 5 and not app.busy
            assert secret.value == ""
            assert all("https://fixture.invalid" not in line for line in app.output_lines)
            assert app.focused is app.query_one("#plan")
            assert not list(tmp_path.iterdir())

    asyncio.run(exercise())


def test_keyboard_can_cancel_confirmation_without_applying(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Service()
        app = application(tmp_path, service)
        async with app.run_test(size=(80, 25)) as pilot:
            await pilot.press("tab", "tab", "tab", "enter")
            await pilot.pause()
            assert isinstance(app.screen, Prompt)
            await pilot.press("tab")
            assert app.focused is app.screen.query_one("#cancel")
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not service.applied and app.last_code == 2
            assert app.focused is app.query_one("#plan")
            assert not list(tmp_path.iterdir())

    asyncio.run(exercise())


def test_real_process_boundary_can_plan_from_a_worker_thread(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from setup_system.application.interaction import ConsoleInteraction
    from setup_system.tui.process import ProcessInstallation

    class ReadOnlyInteraction(ConsoleInteraction):
        def __init__(self) -> None:
            self.output: list[str] = []

        def write(
            self, text: str, *, end: str = "\n", error: bool = False, flush: bool = False
        ) -> None:
            self.output.append(text)

        def confirm(self, prompt: str) -> bool:
            raise AssertionError("Read-only plan requested consent")

        def subscription(self) -> str:
            raise AssertionError("Read-only plan requested a secret")

    interaction = ReadOnlyInteraction()
    service = ProcessInstallation(tmp_path, interaction)
    with ThreadPoolExecutor(max_workers=1) as worker:
        code = worker.submit(service.run, InstallRequest(Operation.PLAN)).result(timeout=30)
    assert code == 4
    assert any("UNSUPPORTED" in line for line in interaction.output)
    assert list(tmp_path.iterdir()) == []


def test_fresh_textual_process_can_start_real_plan_worker(tmp_path: Path) -> None:
    import subprocess
    import sys

    code = """import asyncio,sys
from pathlib import Path
from setup_system.application.installer import InstallRequest,Operation
from setup_system.tui.installer import InstallationApp
async def exercise():
 app=InstallationApp(Path(sys.argv[1]),InstallRequest(Operation.PLAN))
 async with app.run_test(size=(100,40)) as pilot:
  await pilot.click('#plan')
  await app.workers.wait_for_complete()
  await pilot.pause()
  assert app.last_code==4 and not app.busy,app.output_lines
asyncio.run(exercise())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


def test_process_protocol_roundtrips_and_rejects_unknown_messages() -> None:
    import pytest

    from setup_system.application.installation_host import (
        Answer,
        Progress,
        Question,
        Result,
        decode_message,
        encode_message,
    )

    for message in (Progress("stage"), Question("confirm"), Answer(None), Result(5)):
        assert decode_message(encode_message(message)) == message
    for raw in ('{"kind":"result","code":true}', '{"kind":"exec","command":"false"}', "{}"):
        with pytest.raises(ValueError):
            decode_message(raw)


def test_process_protocol_refuses_worker_and_unknown_operations_before_apply() -> None:
    import io
    import json

    import pytest

    from setup_system.application.installation_host import read_request

    for operation in ("network-stage", "unknown", [], {}):
        payload = {
            "root": "/fixture",
            "operation": operation,
            "user": None,
            "yes": False,
            "overrides": {},
            "subscription_file": None,
        }
        with pytest.raises(ValueError):
            read_request(io.StringIO(json.dumps(payload)))


def test_timezone_is_editable_and_enter_only_requests_a_plan(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Service()
        app = application(tmp_path, service)
        async with app.run_test(size=(80, 25)) as pilot:
            timezone = app.query_one("#timezone", Input)
            timezone.value = "Europe/Paris"
            timezone.focus()
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(service.requests) == 1
            assert service.requests[0].timezone == "Europe/Paris"
            assert service.requests[0].operation == Operation.PLAN and not service.applied

    asyncio.run(exercise())


def test_timezone_protocol_accepts_older_request_and_rejects_wrong_type(tmp_path: Path) -> None:
    import io
    import json

    from setup_system.application.installation_host import encode_request, read_request

    request = InstallRequest(Operation.PLAN, timezone="Europe/Paris")
    payload = json.loads(encode_request(tmp_path, request))
    _, decoded = read_request(io.StringIO(json.dumps(payload) + "\n"))
    assert decoded.timezone == "Europe/Paris"
    del payload["timezone"]
    _, older = read_request(io.StringIO(json.dumps(payload) + "\n"))
    assert older.timezone is None
    payload["timezone"] = True
    with pytest.raises(ValueError, match="request values"):
        read_request(io.StringIO(json.dumps(payload) + "\n"))
