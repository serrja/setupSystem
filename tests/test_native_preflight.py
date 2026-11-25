"""Exercise Debian identity and ABI checks without changing the host."""

import pytest

from setup_system.infrastructure.native_target import target


@pytest.mark.parametrize(
    ("key", "value"), [("ID", "ubuntu"), ("VERSION_ID", "14"), ("VERSION_CODENAME", "bookworm")]
)
def test_debian_identity_is_checked_as_a_whole(key, value):
    release = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}
    assert target(release, "x86_64", "amd64") == ("amd64", True)
    assert target(release, "aarch64", "arm64") == ("arm64", True)
    assert not target(release, "riscv64", "riscv64")[1]
    assert not target(dict(release, **{key: value}), "x86_64", "amd64")[1]


@pytest.mark.parametrize(
    ("kernel", "dpkg"), [("x86_64", "i386"), ("aarch64", "armhf"), ("x86_64", ""), ("x86_64", None)]
)
def test_incompatible_or_unavailable_userspace_abi_blocks_root(monkeypatch, kernel, dpkg):
    import subprocess

    from setup_system.infrastructure import native_target

    monkeypatch.setattr(native_target.platform, "machine", lambda: kernel)

    def check(argv, **kwargs):
        assert argv == ["/usr/bin/dpkg", "--print-architecture"]
        if dpkg is None:
            raise FileNotFoundError("no dpkg")
        return subprocess.CompletedProcess(argv, 0, dpkg, "")

    monkeypatch.setattr(native_target, "run_command", check)
    assert not native_target.current_target(
        {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}
    )[1]
