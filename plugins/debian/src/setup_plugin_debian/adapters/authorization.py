"""Deliver a single response to the installed executor's hidden terminal prompt."""

import hashlib
import os
import re
import signal
import tempfile
import time
from pathlib import Path

import pexpect

from setup_plugin_debian.adapters.layout import NATIVE_PYTHON


def authorize(raw: bytes, response: bytes) -> None:
    if not response or len(response) > 1024 or any(b < 32 or b == 127 for b in response):
        raise ValueError("Invalid terminal response")
    prompt_timeout = float(os.environ.get("SETUP_SYSTEM_NATIVE_PROMPT_TIMEOUT", "30"))
    if not 0.01 <= prompt_timeout <= 300:
        raise ValueError("Invalid authorization prompt timeout")
    stopped = False

    def boundary(*_: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGUSR1, boundary)
    with tempfile.TemporaryDirectory() as folder:
        request = Path(folder) / "request"
        with request.open("xb") as output:
            output.write(raw)
        if stopped:
            raise ValueError("Authorization cancelled before the executor started")
        child = pexpect.spawn(
            str(NATIVE_PYTHON),
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
        deadline = time.monotonic() + prompt_timeout
        sent = False
        refused = False
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
                if hit == 0:
                    # A passwordless transaction can already be running. Only an
                    # outstanding authentication prompt is a safe point to stop.
                    if stopped or time.monotonic() >= deadline or sent or child.getecho():
                        refused = True
                        child.sendeof()
                    else:
                        child.sendline(response)
                        sent = True
        finally:
            child.close()
        if refused or child.exitstatus != 0:
            raise ValueError("Native transition requires readback")
