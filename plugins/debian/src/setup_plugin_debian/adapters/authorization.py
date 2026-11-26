"""Deliver a single response to the installed executor's hidden terminal prompt."""

import hashlib
import re
import signal
import tempfile
from pathlib import Path

import pexpect


def authorize(raw: bytes, response: bytes) -> None:
    if not response or len(response) > 1024 or any(b < 32 or b == 127 for b in response):
        raise ValueError("Invalid terminal response")
    stopped = False

    def boundary(*_: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGUSR1, boundary)
    with tempfile.TemporaryDirectory() as folder:
        request = Path(folder) / "request"
        with request.open("xb") as output:
            output.write(raw)
        child = pexpect.spawn(
            "/usr/bin/python3",
            [
                "-I",
                "-B",
                "-m",
                "setup_plugin_debian.adapters.launcher",
                "--perform",
                str(request),
                hashlib.sha256(raw).hexdigest(),
            ],
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(Path.home())},
        )
        sent = False
        try:
            while True:
                hit = child.expect(
                    [
                        re.compile(rb"(?m)^(?:BECOME|SUDO|SU) password: \Z"),
                        pexpect.EOF,
                        re.compile(rb"(?s).{8192}"),
                        pexpect.TIMEOUT,
                    ],
                    timeout=2,
                )
                if hit == 1:
                    break
                if stopped and not sent:
                    raise ValueError("Authorization cancelled")
                if hit == 0:
                    if sent or child.getecho():
                        raise ValueError("Untrusted or repeated native password prompt")
                    child.sendline(response)
                    sent = True
        finally:
            child.close()
        if child.exitstatus != 0:
            raise ValueError("Native transition requires readback")
