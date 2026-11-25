"""Exercise official key API requests against synthetic account responses."""

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from setup_plugin_git import Plugin, Settings, keys

from setup_system_sdk import Mutation, Outcome, ownership_context


@pytest.mark.parametrize("cleanup", [False, True])
def test_create_verify_revoke_only_owned_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: bool
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    public = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
    )
    path = tmp_path / "example.pub"
    path.write_bytes(public)
    path.chmod(0o600)
    remote: list[dict[str, str | int]] = []
    monkeypatch.setattr(keys, "command", lambda *args, **kwargs: (0, "fixture-access"))

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "sample"})
        if request.method == "GET" and request.url.path == "/user/keys":
            return httpx.Response(200, json=remote)
        if request.method == "POST":
            remote.append({"id": 28, "key": public.decode()})
            return httpx.Response(201, json=remote[0])
        assert request.url.path == "/user/keys/28"
        if request.method == "GET":
            return httpx.Response(200, json=remote[0])
        assert request.method == "DELETE"
        remote.clear()
        return httpx.Response(204)

    native_client = httpx.Client

    def fake(**kwargs: Any) -> httpx.Client:
        return native_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(keys.httpx, "Client", fake)
    plugin = Plugin()
    add = keys.Upload(account="sample", public_key=path, title="Work")
    plan = asyncio.run(plugin.plan("upload-key", Settings(), add))
    assert plan.outcome == Outcome.READY and not remote
    result = asyncio.run(
        plugin.apply_request(
            "upload-key",
            Mutation(
                action_id="upload-key", plan=plan, settings={}, inputs=add.model_dump(mode="json")
            ),
        )
    )
    assert result.resources[0].remote_id == "28" and result.resources[0].verified
    checked = asyncio.run(plugin.verify(plan, Settings(), add))
    assert checked.outcome == Outcome.COMPLETE and checked.resources[0].remote_id == "28"
    delete = keys.Revoke(account="sample", key_id=28)
    assert asyncio.run(plugin.plan("revoke-key", Settings(), delete)).outcome == Outcome.BLOCKED
    from setup_plugin_git import CleanupKeys

    action = "remove-owned-keys" if cleanup else "revoke-key"
    selection = CleanupKeys() if cleanup else delete
    with ownership_context(tuple(checked.resources)):
        plan = asyncio.run(plugin.plan(action, Settings(), selection))
    assert plan.outcome == Outcome.READY
    result = asyncio.run(
        plugin.apply_request(
            action,
            Mutation(
                action_id=action,
                plan=plan,
                settings={},
                inputs=selection.model_dump(mode="json"),
                holdings=tuple(checked.resources),
            ),
        )
    )
    assert result.outcome == Outcome.CHANGED
    with ownership_context(tuple(checked.resources)):
        assert asyncio.run(plugin.verify(plan, Settings(), selection)).outcome == Outcome.COMPLETE
    assert path.read_bytes() == public


def test_redirect_never_receives_account_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    visited = []

    def handle(request: httpx.Request) -> httpx.Response:
        visited.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://unexpected.example/user"})

    previous = httpx.Client
    monkeypatch.setattr(keys, "command", lambda *args, **kwargs: (0, "synthetic-token"))
    monkeypatch.setattr(
        keys.httpx,
        "Client",
        lambda **kwargs: previous(**kwargs, transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(ValueError, match="rejected"):
        with keys.account_api(keys.KeyAccount(account="sample")):
            pytest.fail("A redirect cannot establish the account")
    assert visited == ["https://api.github.com/user"]


def test_ssh_readback_requires_named_account_and_host_verification(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "fixture.pub"
    public = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
    )
    path.write_bytes(public)
    path.chmod(0o600)
    selection = keys.Probe(account="fixture", public_key=path)
    account = {"name": "someone-else"}

    def native(args, **kw):
        assert "StrictHostKeyChecking=yes" in args and args[-1] == "github.com"
        assert args[args.index("-i") + 1] == str(path)
        return subprocess.CompletedProcess(
            args,
            1,
            "",
            (
                f"Hi {account['name']}! You've successfully authenticated, "
                "but GitHub does not provide shell access."
            ),
        )

    monkeypatch.setattr(keys, "run_command", native)
    assert keys.access(selection).outcome == Outcome.NEEDS_USER_ACTION
    account["name"] = "fixture"
    assert keys.access(selection).outcome == Outcome.NO_CHANGES


def test_remote_id_replaced_between_post_and_get_remains_unverified(tmp_path, monkeypatch):
    from contextlib import contextmanager

    monkeypatch.setenv("HOME", str(tmp_path))
    p = tmp_path / "fixture.pub"
    public = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
        .decode()
    )
    p.write_text(public)
    p.chmod(0o600)
    calls = []

    def handle(req):
        calls.append((req.method, req.url.path))
        if req.url.path == "/user/keys" and req.method == "GET":
            return httpx.Response(200, json=[])
        if req.url.path == "/user/keys" and req.method == "POST":
            return httpx.Response(201, json={"id": 6, "key": public})
        assert req.method == "GET" and req.url.path == "/user/keys/6"
        return httpx.Response(200, json={"id": 7, "key": public})

    @contextmanager
    def account(selection):
        with httpx.Client(
            base_url="https://example.invalid", transport=httpx.MockTransport(handle)
        ) as api:
            yield api

    monkeypatch.setattr(keys, "account_api", account)
    add = keys.Upload(account="sample", public_key=p, title="Work")
    plan = keys.prepare(add)
    native = keys.mutate(add, plan)
    assert native.outcome == Outcome.UNKNOWN_OUTCOME and not native.resources
    assert calls == [("GET", "/user/keys"), ("POST", "/user/keys"), ("GET", "/user/keys/6")]
