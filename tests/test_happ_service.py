"""Keep package-triggered service starts deferred and preserve administrator-owned policy."""

import os
import stat

import pytest

from setup_system.infrastructure import service_policy


@pytest.mark.parametrize("kind", ["unknown", "nonexecutable", "symlink"])
def test_administrator_policy_is_preserved(tmp_path, kind):
    path = tmp_path / "policy"
    if kind == "symlink":
        path.symlink_to("/dev/null")
    else:
        path.write_bytes(b"#!/bin/sh\nexit 101\n" if kind == "nonexecutable" else b"custom policy")
        path.chmod(0o644)
    with pytest.raises(ValueError, match=r"review|not executable|symlink"):
        with service_policy.defer_services(path):
            pytest.fail("Misconfigured policy must block the transaction")
    assert path.exists()


@pytest.mark.parametrize(("existed", "mask"), [(True, 0o022), (False, 0o077), (False, 0o777)])
def test_verified_temporary_policy_is_executable_and_is_removed(tmp_path, existed, mask):
    path = tmp_path / "policy"
    if existed:
        path.write_bytes(service_policy.DEFER_POLICY)
        path.chmod(0o644)
    previous = os.umask(mask)
    try:
        with service_policy.defer_services(path):
            assert path.read_bytes() == service_policy.DEFER_POLICY
            assert stat.S_IMODE(path.stat().st_mode) == 0o755
    finally:
        os.umask(previous)
    assert not path.exists()


def test_known_administrator_policy_remains_in_place(tmp_path):
    path = tmp_path / "policy"
    content = b"#!/bin/sh\nexit 101\n"
    path.write_bytes(content)
    path.chmod(0o755)
    before = path.stat()
    with service_policy.defer_services(path):
        assert path.read_bytes() == content
    assert os.path.samestat(before, path.stat())


@pytest.mark.parametrize("same_content", [False, True])
def test_late_policy_replacement_survives_actual_claim(tmp_path, monkeypatch, same_content):
    path = tmp_path / "policy"
    admin = tmp_path / "admin"
    replacement = service_policy.DEFER_POLICY if same_content else b"administrator update"
    admin.write_bytes(replacement)
    admin.chmod(0o755)
    identity = admin.stat()
    move = service_policy.move_new

    def replace_before_claim(source, destination, **kwargs):
        if source == path:
            os.replace(admin, path)
        return move(source, destination, **kwargs)

    monkeypatch.setattr(service_policy, "move_new", replace_before_claim)
    with pytest.raises(RuntimeError, match="policy changed"):
        with service_policy.defer_services(path):
            assert path.read_bytes() == service_policy.DEFER_POLICY
    assert os.path.samestat(identity, path.stat()) and path.read_bytes() == replacement
