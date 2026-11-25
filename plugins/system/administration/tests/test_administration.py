"""Exercise administrative onboarding without authenticating or changing a host account."""

import grp
import os
import pwd
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from setup_plugin_administration import account as admin_account
from setup_plugin_administration.models import AdminAccount, AdminIntent

ROOT = Path(__file__).resolve().parents[1]


def test_intent_rejects_non_login_names():
    for name in ("root", "", "-a", "../alice", "alice\n", "alice;id"):
        with pytest.raises(ValidationError):
            AdminIntent(username=name)
    assert AdminIntent(username="alice").username == "alice"


def test_account_observations_are_closed():
    account = AdminAccount(
        username="alice",
        uid=1001,
        gid=1001,
        home="/home/alice",
        shell="/bin/sh",
        sudo_gid=27,
        member=False,
    )
    for bad in ({"uid": True}, {"gid": 0}, {"home": "../etc"}):
        with pytest.raises(ValidationError):
            account.validated(**bad)
    with pytest.raises(ValidationError) as error:
        account.validated(password=None)
    assert [(e["loc"], e["type"]) for e in error.value.errors()] == [
        (("password",), "extra_forbidden")
    ]


@pytest.fixture
def local_account(tmp_path, monkeypatch):
    account_home = tmp_path / "alice"
    account_home.mkdir()
    uid, gid = os.getuid(), os.getgid()
    state = {
        "name": "alice",
        "uid": uid,
        "gid": gid,
        "home": str(account_home),
        "shell": "/bin/sh",
        "groups": ["staff"],
        "exists": True,
    }

    def account_record(key):
        if not state["exists"] or key not in (state["name"], state["uid"]):
            raise KeyError(key)
        return pwd.struct_passwd(
            (state["name"], "x", state["uid"], state["gid"], "", state["home"], state["shell"])
        )

    def groups():
        return [
            grp.struct_group(("personal", "x", state["gid"], [])),
            grp.struct_group(("staff", "x", 50, ["alice"] if "staff" in state["groups"] else [])),
            grp.struct_group(("sudo", "x", 27, ["alice"] if "sudo" in state["groups"] else [])),
        ]

    def by_name(name):
        if name.isdigit():
            raise KeyError(name)
        return next(group for group in groups() if group.gr_name == name)

    read = Path.read_text

    def synthetic_files(path, *args, **kwargs):
        if path == Path("/etc/passwd"):
            return f"alice:x:{state['uid']}:{state['gid']}::{state['home']}:{state['shell']}\n"
        if path == Path("/etc/group"):
            return "\n".join(f"{g.gr_name}:x:{g.gr_gid}:{','.join(g.gr_mem)}" for g in groups())
        if path == Path("/etc/shells"):
            return state["shell"] + "\n"
        if path == Path("/etc/login.defs"):
            return "UID_MIN 1000\nUID_MAX 60000\n"
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", synthetic_files)
    monkeypatch.setattr(
        admin_account,
        "pwd",
        SimpleNamespace(getpwnam=account_record, getpwuid=account_record),
    )
    monkeypatch.setattr(
        admin_account,
        "grp",
        SimpleNamespace(
            getgrall=groups,
            getgrnam=by_name,
            getgrgid=lambda gid: next(group for group in groups() if group.gr_gid == gid),
        ),
    )
    monkeypatch.setattr(
        os,
        "getgrouplist",
        lambda name, gid: [gid, *(g.gr_gid for g in groups() if name in g.gr_mem)],
    )
    return state


@pytest.mark.parametrize("shell", ["directory", "false_alias", "nologin"])
def test_service_shell_is_not_an_interactive_login(local_account, tmp_path, shell):
    if shell == "directory":
        selected = tmp_path
    elif shell == "false_alias":
        selected = tmp_path / "shell"
        selected.symlink_to("/bin/false")
    else:
        selected = tmp_path / "nologin"
        selected.touch(mode=0o755)
    local_account["shell"] = str(selected)
    with pytest.raises(ValueError, match="login shell"):
        admin_account.read_account(AdminIntent(username="alice"))


def test_service_uid_is_not_an_ordinary_login(local_account):
    local_account["uid"] = 42
    with pytest.raises(ValueError, match="UID range"):
        admin_account.read_account(AdminIntent(username="alice"))


@pytest.mark.parametrize(
    "snippet",
    [
        "",
        "%sudo ALL=(ALL:ALL) NOPASSWD: ALL",
        "alice ALL=(ALL) !/usr/bin/id",
        "Defaults !authenticate",
        "Defaults:%sudo !authenticate",
    ],
)
def test_native_effective_policy_on_synthetic_files(tmp_path, monkeypatch, snippet):
    converter = shutil.which("cvtsudoers") or shutil.which("cvtsudoers.ws")
    validator = shutil.which("visudo.ws" if converter and converter.endswith(".ws") else "visudo")
    assert converter and validator, "Install the sudo package to validate synthetic policies"
    source = tmp_path / "sudoers"
    include = tmp_path / "policy"
    include.write_text(snippet + "\n")
    source.write_text(f'Defaults env_reset\n{admin_account.DEFAULT_POLICY}@include "{include}"\n')

    def checked(*args, input_text=None):
        if args[0] == "/usr/sbin/visudo":
            subprocess.run([validator, "-c", "-q", "-f", source], check=True)
            return ""
        args = (converter, *args[1:-1], str(source) if args[-1] == "/etc/sudoers" else "-")
        return subprocess.check_output(args, input=input_text, text=True)

    read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            "sudoers: files\n"
            if p == Path("/etc/nsswitch.conf")
            else ""
            if p == Path("/etc/sudo.conf")
            else read(p, *a, **kw)
        ),
    )
    monkeypatch.setattr(admin_account, "checked_output", checked)
    if snippet:
        with pytest.raises(ValueError):
            admin_account.verify_policy()
    else:
        admin_account.verify_policy()
