import subprocess
import sys
from pathlib import Path

import anyio
import httpx
import pytest
from pytest_subprocess import FakeProcess
from setup_system_sdk.downloads import DownloadClient, DownloadError, DownloadPolicy
from setup_system_sdk.processes import ProcessRunner, ProcessSpec


@pytest.mark.asyncio
async def test_anyio_fake_process(fp: FakeProcess, home: Path) -> None:
    command = ["contract-probe", "--inspect"]
    recorder = fp.register(command, stdout=b"ok", stderr=b"diagnostic", returncode=11)
    result = await ProcessRunner().run(
        ProcessSpec(tuple(command), cwd=home, env={"LANG": "C"}, stdin=subprocess.DEVNULL)
    )
    assert (result.returncode, result.stdout, result.stderr) == (11, b"ok", b"diagnostic")
    assert recorder.calls[0].kwargs is not None
    assert recorder.calls[0].kwargs["env"] == {"LANG": "C"}
    assert recorder.calls[0].kwargs["stdin"] == subprocess.DEVNULL
    assert tuple(fp.calls[0]) == tuple(command)


@pytest.mark.asyncio
async def test_both_real_pipes_are_drained() -> None:
    result = await ProcessRunner().run(
        ProcessSpec(
            (sys.executable, "-c", "import os; os.write(2,b'e'*200000); os.write(1,b'o'*200000)")
        )
    )
    assert result.stderr == b"e" * 200000
    assert result.stdout == b"o" * 200000


@pytest.mark.asyncio
async def test_real_timeout_collects_group(home: Path) -> None:
    path = home / "child"
    code = (
        "import os,signal,time,sys; pid=os.fork(); "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "open(sys.argv[1],'w').write(str(os.getpid())) if not pid else None; time.sleep(60)"
    )
    with pytest.raises(TimeoutError):
        await ProcessRunner().run(
            ProcessSpec((sys.executable, "-c", code, str(path)), timeout=0.8, grace=0.05)
        )
    pid = int(path.read_text())
    stat = Path(f"/proc/{pid}/stat")
    with anyio.fail_after(2):
        while True:
            try:
                if stat.read_text().split()[2] == "Z":
                    break
            except FileNotFoundError:
                break
            await anyio.sleep(0.01)


@pytest.mark.asyncio
async def test_retries_only_transient_get() -> None:
    attempts = []

    async def handle(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(503 if len(attempts) == 1 else 200, stream=httpx.ByteStream(b"ok"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = DownloadClient(http)
        policy = DownloadPolicy(frozenset({"unit.example"}), 2, 1, total_timeout=2)
        assert await client.read("https://unit.example/a", policy) == b"ok"
        with pytest.raises(DownloadError):
            await client.read("https://unit.example/a", policy, "0" * 64)
        assert len(attempts) == 3


@pytest.mark.asyncio
async def test_private_terminal_and_pipe_have_separate_contents(home: Path) -> None:
    import os
    import termios

    master, slave = os.openpty()
    try:
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        command = (
            sys.executable,
            "-c",
            "import os,sys; assert os.isatty(0); "
            "assert input()=='fixture-password'; print('accepted',flush=True)",
        )
        async with ProcessRunner().open(ProcessSpec(command, stdin=slave, timeout=3)) as managed:
            os.write(master, b"fixture-password\n")
            assert managed.stdout
            assert await managed.stdout.receive() == b"accepted\n"
            assert await managed.process.wait() == 0
        os.set_blocking(master, False)
        with pytest.raises(BlockingIOError):
            os.read(master, 128)
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_cancel_at_native_boundary(home: Path) -> None:
    import signal

    from anyio.abc import Process
    from setup_system_sdk.processes import CancellationPolicy

    marker = home / "safe"
    code = (
        "import signal,sys,time,pathlib; "
        "signal.signal(signal.SIGUSR1,lambda *_:"
        "(pathlib.Path(sys.argv[1]).write_text('safe'),sys.exit())); "
        "print('ready',flush=True); time.sleep(45)"
    )

    async def boundary(child: Process) -> None:
        child.send_signal(signal.SIGUSR1)
        await child.wait()

    spec = ProcessSpec(
        (sys.executable, "-c", code, str(marker)),
        cancellation=CancellationPolicy.SAFE_BOUNDARY,
        safe_boundary=boundary,
    )
    async with ProcessRunner().open(spec) as child:
        assert child.stdout and await child.stdout.receive() == b"ready\n"
        await child.cancel()
    assert marker.read_text() == "safe"
