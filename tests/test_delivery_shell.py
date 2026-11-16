"""Bootstrap argument/preflight checks must be side-effect free."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("arguments", [["--help"], ["--unknown"], [], ["--release", "latest"]])
def test_bootstrap_preflight_does_not_download(tmp_path: Path, arguments: list[str]) -> None:
    result = subprocess.run(
        ["sh", str(ROOT / "install.sh"), *arguments],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == (0 if arguments == ["--help"] else 2)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("ready,corrupt", [(False, False), (True, False), (False, True)])
def test_complete_bootstrap_reaches_only_the_textual_entrypoint(
    tmp_path: Path, ready: bool, corrupt: bool
) -> None:
    import fcntl
    import hashlib
    import io
    import os
    import pty
    import shlex
    import sys
    import tarfile
    import termios

    home = tmp_path / "home with spaces"
    home.mkdir()
    uv = tmp_path / "uv.tar.gz"
    binary = b"""#!/bin/sh
if [ "$1" = --version ]; then printf "uv 0.12.15\\n"; exit 0; fi
if [ "$1" = python ]; then printf 'python prepared\\n' >> "$HOME/bootstrap.log"; exit 0; fi
while [ "$1" != -- ]; do shift; done
shift
[ "$1" = python ] || exit 90
shift
exec PYTHON_EXECUTABLE "$@"
"""
    binary = binary.replace(b"PYTHON_EXECUTABLE", shlex.quote(sys.executable).encode())
    if ready:
        managed = home / ".local/share/setup-system/bin/uv"
        managed.parent.mkdir(parents=True)
        managed.write_bytes(binary)
        managed.chmod(0o755)
    with tarfile.open(uv, "w:gz") as archive:
        member = tarfile.TarInfo("x86_64-unknown-linux-gnu/uv")
        member.size = len(binary)
        member.mode = 0o755
        archive.addfile(member, io.BytesIO(binary))
    policy = tmp_path / "runtime.toml"
    text = (ROOT / "manifests/runtime.toml").read_text()
    import re

    text = re.sub(
        r'amd64_sha256 = "[0-9a-f]+"',
        'amd64_sha256 = "' + hashlib.sha256(uv.read_bytes()).hexdigest() + '"',
        text,
    )
    policy.write_text(text)
    released = tmp_path / "setup_system-1.2.3.tar.gz"
    helper = b"""import os,pathlib
home=pathlib.Path(os.environ['HOME'])
source=home/'.local/share/setup-system/releases/v1.2.3'
source.mkdir(parents=True)
command=home/'.local/share/setup-system/runtime/bin/python'
command.parent.mkdir(parents=True)
command.write_text('#!/bin/sh\\nprintf "textual launched\\\\n" >> "$HOME/bootstrap.log"\\n')
command.chmod(0o755)
"""
    with tarfile.open(released, "w:gz") as archive:
        for name, content in {
            "uv.lock": b"fixture",
            "src/setup_system/_delivery_entry.py": helper,
        }.items():
            member = tarfile.TarInfo("setup_system-1.2.3/" + name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    checksum = tmp_path / "checksums.sha256"
    checksum.write_text(
        hashlib.sha256(released.read_bytes()).hexdigest() + "  " + released.name + "\n"
    )
    if corrupt:
        checksum.write_text("0" * 64 + "  " + released.name + "\n")
    script = (ROOT / "install.sh").read_text().rsplit('bootstrap_main "$@"', 1)[0]
    script += f"""
id() {{ case "$1" in -u) [ "$#" -eq 1 ] && printf 0 || printf 12345 ;; -g) printf 12345 ;; esac; }}
getent() {{ printf '%s\\n' 'fixture:x:12345:12345::{home}:/bin/sh'; }}
stat() {{ printf 12345; }}
uname() {{ [ "$1" = -s ] && printf Linux || printf x86_64; }}
awk() {{ if [ "${{3-}}" = /etc/os-release ]; then printf debian; else command awk "$@"; fi; }}
as_user() {{ env HOME={shlex.quote(str(home))} "$@"; }}
fetch() {{
 case "$1" in
  */runtime.toml) cp {shlex.quote(str(policy))} "$2" ;;
  */checksums.sha256) cp {shlex.quote(str(checksum))} "$2" ;;
  */setup_system-1.2.3.tar.gz) cp {shlex.quote(str(released))} "$2" ;;
  */uv-*.tar.gz)
    printf 'uv downloaded\\n' >> "$HOME/bootstrap.log"
    cp {shlex.quote(str(uv))} "$2" ;;
  *) exit 91 ;;
 esac
}}
export HOME={shlex.quote(str(home))}
bootstrap_main --release v1.2.3 --user fixture
"""
    master, slave = pty.openpty()

    def terminal() -> None:
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    try:
        process = subprocess.Popen(
            ["sh", "-c", script],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={"PATH": "/usr/bin:/bin"},
            preexec_fn=terminal,
        )
        code = process.wait(timeout=20)
        os.set_blocking(master, False)
        terminal_output = bytearray()
        try:
            while chunk := os.read(master, 4096):
                terminal_output.extend(chunk)
        except (BlockingIOError, OSError):
            pass
        assert code == (2 if corrupt else 0), terminal_output.decode(errors="replace")
        expected = [] if ready else ["uv downloaded"]
        if not corrupt:
            expected += ["python prepared", "textual launched"]
        assert (home / "bootstrap.log").read_text().splitlines() == expected
    finally:
        os.close(master)
        os.close(slave)


def test_no_curl_uses_bounded_verified_native_apt_download(tmp_path: Path) -> None:
    import shlex

    destination = tmp_path / "download"
    log = tmp_path / "argv"
    definitions = (ROOT / "install.sh").read_text().rsplit('bootstrap_main "$@"', 1)[0]
    script = (
        definitions
        + f"""
PATH=''
as_user() {{
    printf '%s\\n' "$@" > {shlex.quote(str(log))}
    for last do :; done
    printf 'fixture' > "$last"
}}
stat() {{ /usr/bin/stat "$@"; }}
fetch https://fixture.invalid/archive {shlex.quote(str(destination))} 8192
"""
    )
    result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    arguments = log.read_text().splitlines()
    assert "/usr/lib/apt/apt-helper" in arguments
    assert "Acquire::https::Verify-Peer=true" in arguments
    assert "Acquire::https::Verify-Host=true" in arguments
    assert "Acquire::https::User-Agent=setupSystem" in arguments
    assert "ulimit -f" in arguments[2]
    assert "16" in arguments
    assert "--kill-after=5s" in arguments
    assert "download-file" in arguments
    assert not {"update", "install"} & set(arguments)
    assert destination.read_bytes() == b"fixture"
