"""Exercise administrative onboarding without authenticating or changing a host account."""

import ast
import grp
import hashlib
import importlib.util
import json
import os
import pwd
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from setup_system.domain.administration import ADMIN_REQUEST_BYTES, AdminAccount, AdminIntent
from setup_system.infrastructure import admin_account

ROOT = Path(__file__).resolve().parents[1]


def test_admin_uses_public_ansible_interfaces():
    for folder in ("src", "ansible", "libexec", "assets", "debian"):
        for source in (ROOT / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [f"{node.module}.{alias.name}" for alias in node.names]
                else:
                    continue
                assert not any(name.startswith("ansible.modules.") for name in names), source


def test_admin_cli_constrains_operations_and_writes_one_json_record(admin):
    from typer.testing import CliRunner

    runner = CliRunner()
    assert runner.invoke(admin.cli, ["--help"]).exit_code == 0
    assert runner.invoke(admin.cli, ["arbitrary-target"]).exit_code == 2
    result = runner.invoke(admin.cli, ["account", "plan"], input='{"schema":true}\n')
    assert result.exit_code == 0
    assert result.stdout.count("\n") == 1 and result.stdout.endswith("\n")
    assert json.loads(result.stdout)["status"] == "blocked"
    assert not result.stderr


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
def admin():
    spec = importlib.util.spec_from_file_location("tested_admin", ROOT / "ansible/files/admin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def envelope(expected=None):
    return json.dumps({"schema": 1, "settings": {"username": "alice"}, "expected": expected})


def test_privileged_helper_rejects_checkout_before_writing(admin, local_account, monkeypatch):
    monkeypatch.setattr(admin, "current_target", lambda: ("amd64", True))
    selection = admin.run("plan", envelope()).model_dump(mode="json", exclude_unset=True)
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    def no_privileged_command():
        pytest.fail("Privileged execution reached policy inspection from an unpackaged source")

    monkeypatch.setattr(admin, "verify_policy", no_privileged_command)
    with pytest.raises(ValueError, match="installed"):
        admin.run("apply", envelope(selection))
    assert local_account["groups"] == ["staff"]


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "missing",
        "uid",
        "gid",
        "shell",
        "home",
        "no_root",
        "policy",
        "mid_change",
        "denied_native",
        "readback",
        "lost_group",
        "policy_drift",
        "unsupported",
        "stale_kind",
        "different_boot",
    ],
)
def test_confirmed_append_only(admin, local_account, monkeypatch, scenario):
    writes = []
    identity = local_account
    monkeypatch.setattr(admin, "require_installed_resource", lambda path, name: None)
    monkeypatch.setattr(admin, "current_target", lambda: ("amd64", True))
    selected = admin.run("plan", envelope())
    assert selected.status == "ready" and not writes
    assert selected.observed.account.uid == identity["uid"]
    assert selected.observed.account.home == identity["home"]
    consent = selected.model_dump(mode="json", exclude_unset=True)
    consent["actions"][0]["label"] = "Localized label"
    monkeypatch.setattr(os, "geteuid", lambda: 1000 if scenario == "no_root" else 0)
    if scenario == "unsupported":
        monkeypatch.setattr(admin, "current_target", lambda: ("unsupported", False))
    if scenario == "different_boot":
        monkeypatch.setattr(admin, "current_boot_identity", lambda: "f" * 64)
    if scenario == "stale_kind":
        consent["actions"][0]["username"] = "changed"
    if scenario == "missing":
        identity["exists"] = False
    if scenario == "uid":
        identity["uid"] += 1
    if scenario == "gid":
        identity["gid"] += 1
    if scenario == "shell":
        identity["shell"] = "/bin/bash"
    if scenario == "home":
        location = Path(identity["home"]).parent / "different"
        location.mkdir()
        identity["home"] = str(location)

    policy_checks = []

    def policy():
        policy_checks.append(True)
        if scenario == "mid_change":
            identity["exists"] = False
        if scenario == "policy" or (scenario == "policy_drift" and len(policy_checks) > 1):
            raise ValueError("custom policy")

    def verify(_account):
        if scenario == "readback":
            raise ValueError("sudo denied")

    def execute(account):
        assert account.username == "alice"
        writes.append(account.username)
        if scenario == "denied_native":
            raise ValueError("native operation failed")
        identity["groups"].append("sudo")
        if scenario == "lost_group":
            identity["groups"].remove("staff")
        return ""

    monkeypatch.setattr(admin, "verify_policy", policy)
    monkeypatch.setattr(admin, "verify_effective_access", verify)
    monkeypatch.setattr(admin, "append_sudo", execute)
    if scenario in {
        "missing",
        "mid_change",
        "uid",
        "gid",
        "shell",
        "home",
        "no_root",
        "unsupported",
        "policy",
        "stale_kind",
        "different_boot",
    }:
        with pytest.raises(ValueError):
            admin.run("apply", envelope(consent))
        assert not writes and identity["groups"] == ["staff"]
        return
    result = admin.run("apply", envelope(consent))
    if scenario == "success":
        assert result.status == "requires_user_login" and identity["groups"] == ["staff", "sudo"]
        assert admin.run("plan", envelope()).status == "no_changes"
        with pytest.raises(ValueError):
            admin.run("apply", envelope(consent))
    elif scenario in {"readback", "lost_group", "policy_drift"}:
        assert result.status == "partial" and len(writes) == 1
    else:
        assert result.status == "blocked" and identity["groups"] == ["staff"]
        assert len(writes) == int(scenario == "denied_native")


@pytest.mark.parametrize("account_exists", [True, False])
def test_native_append_runs_only_the_existing_account_command(
    tmp_path, admin, local_account, account_exists, monkeypatch
):
    account = admin_account.read_account(AdminIntent(username="alice"))
    # Exercise the real Ansible command module with an inert account tool.
    marker = tmp_path / "argv"
    fake_tool = tmp_path / "usermod"
    fake_tool.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > "
        + shlex.quote(str(marker))
        + ("\nexit 0\n" if account_exists else "\nexit 6\n")
    )
    fake_tool.chmod(0o700)

    def synthetic_run(argv, **kwargs):
        assert argv[4] == "/usr/bin/ansible"
        assert argv[argv.index("-m") + 1] == "ansible.builtin.command"
        args = json.loads(argv[-1])
        assert args["argv"][0] == "/usr/sbin/usermod" and not args["expand_argument_vars"]
        assert kwargs["cwd"] == ROOT
        assert Path(kwargs["env"]["HOME"]).stat().st_mode & 0o777 == 0o700
        assert "PYTHONPATH" not in kwargs["env"]
        args["argv"][0] = str(fake_tool)
        assert kwargs["env"]["ANSIBLE_PYTHON_INTERPRETER"] == argv[0]
        argv[3] = str(Path(yaml.__file__).resolve().parents[1])
        argv[4] = str(Path(sys.executable).with_name("ansible"))
        argv[-1] = json.dumps(args)
        # Match native extensions to this test environment. The module itself still
        # executes through the packaged interpreter using the system Python.
        isolated = subprocess.check_output(
            [argv[0], "-c", "import sys; print(sys.flags.isolated, sys.flags.no_site)"]
        )
        assert isolated.strip() == b"1 1"
        argv[:1] = [sys.executable, "-I", "-S", "-B"]
        return subprocess.run(argv, **kwargs)

    monkeypatch.setattr(admin, "INSTALLED", ROOT)
    monkeypatch.setattr(admin, "require_installed_resource", lambda path, relative: None)
    monkeypatch.setattr(admin, "TemporaryDirectory", lambda **kw: TemporaryDirectory(dir=tmp_path))
    monkeypatch.setattr(
        admin, "subprocess", SimpleNamespace(run=synthetic_run, DEVNULL=subprocess.DEVNULL)
    )
    if account_exists:
        admin.append_sudo(account)
    else:
        with pytest.raises(ValueError, match="native append"):
            admin.append_sudo(account)
    assert marker.read_text().splitlines() == ["--append", "--groups", "sudo", "--", "alice"]


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


def test_privileged_python_uses_only_fixed_distro_imports(tmp_path):
    play = yaml.safe_load((ROOT / "ansible/admin.yml").read_text())[0]
    raw = next(
        task["ansible.builtin.raw"]
        for outer in play["tasks"]
        for task in outer.get("block", ())
        if "ansible.builtin.raw" in task
    )
    fixture = tmp_path / "ansible/files/admin.py"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        "import json,sys\nprint(json.dumps([sys.flags.isolated, sys.flags.no_site, "
        "sys.path, sys.argv[1:]]))\n"
    )
    private = tmp_path / "fake-site"
    private.mkdir()
    canary = tmp_path / "unexpected-site"
    (private / "sitecustomize.py").write_text(f"open({str(canary)!r}, 'w').close()\n")
    sentinel = json.dumps({"label": "$HOME; words in quotes"})
    distro = tmp_path / "distro"
    library = distro / "setup_system"
    library.mkdir(parents=True)
    guard = library / "layout.py"
    guard.write_text(
        f"from pathlib import Path\nINSTALLED = Path({str(tmp_path)!r})\n"
        "def require_installed_resource(source, relative):\n"
        "    raise ValueError('expected installed resource')\n"
    )
    program = raw
    program = program.replace('"/usr/lib/python3/dist-packages"', f'"{distro}"')
    program = program.replace("{{ payload.wire | quote }}", shlex.quote(sentinel))
    argv = shlex.split(program)
    start = argv.index("/usr/bin/python3")
    assert argv[start + 1 : start + 5] == ["-I", "-S", "-B", "-c"]
    denied = subprocess.run(argv[start:], text=True, capture_output=True, check=False)
    assert (
        denied.returncode and not denied.stdout and "expected installed resource" in denied.stderr
    )
    # The second fixture models the successful trust check, running at this CI user's UID.
    guard.write_text(
        f"from pathlib import Path\nINSTALLED = Path({str(tmp_path)!r})\n"
        "def require_installed_resource(source, relative):\n"
        "    assert source == INSTALLED / relative and source.is_file()\n"
    )
    result = subprocess.check_output(
        argv[start:],
        text=True,
        cwd=tmp_path,
        env={"PYTHONPATH": str(private), "PYTHONUSERBASE": str(private)},
    )
    isolated, no_site, search, arguments = json.loads(result)
    assert isolated and no_site and arguments == ["account", "apply", sentinel]
    assert search[-1] == str(distro)
    assert all(p.startswith("/usr/lib/python3") for p in search[:-1])
    assert not canary.exists()


@pytest.mark.parametrize("variant", ["unconfirmed", "changed", "missing", "denied", "locked_root"])
def test_administrative_play_validates_before_native_su(tmp_path, admin, local_account, variant):
    # Substitute a failing, unprivileged executable in the play under test. This
    # exercises the real become plugin without entering PAM or changing any user.
    play = tmp_path / "admin.yml"
    called = tmp_path / "su-invoked"
    su = tmp_path / "denying-su"
    refusal = "Account validation failure" if variant == "locked_root" else "Authentication failure"
    su.write_text(
        f"#!/bin/sh\necho called >> {shlex.quote(str(called))}\necho 'su: {refusal}' >&2\nexit 1\n"
    )
    su.chmod(0o700)
    play.write_text((ROOT / "ansible/admin.yml").read_text().replace("/usr/bin/su", str(su)))
    shutil.copytree(ROOT / "ansible/filter_plugins", tmp_path / "filter_plugins")
    request = tmp_path / "request.json"
    account = admin_account.read_account(AdminIntent(username="alice"))
    expected = admin.plan(account, admin_account.current_boot_identity()).model_dump(
        mode="json", exclude_unset=True
    )
    request.write_text(envelope(expected if variant != "unconfirmed" else None))
    request.chmod(0o600)
    result = tmp_path / "result.json"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "ANSIBLE_HOME": str(tmp_path / ".ansible"),
        "ANSIBLE_REMOTE_TEMP": str(tmp_path / "remote"),
        "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"),
        "ANSIBLE_NOCOLOR": "true",
        "SETUP_SYSTEM_REQUEST": str(request),
        "SETUP_SYSTEM_RESULT": str(result),
        "SETUP_SYSTEM_CANCEL": str(tmp_path / "cancel"),
    }
    identity = (
        "f" * 64 if variant == "changed" else hashlib.sha256(request.read_bytes()).hexdigest()
    )
    proc = subprocess.run(
        [
            str(Path(sys.executable).with_name("ansible-playbook")),
            "-i",
            "localhost,",
            "-c",
            "local",
            str(play),
            "-e",
            "setup_action=apply",
            "-e",
            f"ansible_su_exe={su}",
            *([] if variant == "missing" else ["-e", f"admin_request_sha={identity}"]),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=35,
        check=False,
    )
    assert called.exists() == (variant in {"denied", "locked_root"}), (
        "Invalid consent reached the su plugin"
    )
    if called.exists():
        assert called.read_text().splitlines() == ["called"], "The refused credential was retried"
    assert proc.returncode == 0, proc.stdout
    assert json.loads(result.read_bytes())["status"] == "blocked"


@pytest.mark.parametrize(
    "issue",
    [
        "symlink",
        "fifo",
        "size",
        "mode",
        "owner_exec",
        "different_identity",
        "duplicate_action",
        "wrong_scope",
    ],
)
def test_bounded_administrative_wire_on_private_files(tmp_path, admin, local_account, issue):
    from setup_system.infrastructure.operation_io import read_admin_request

    account = admin_account.read_account(AdminIntent(username="alice"))
    expected = admin.plan(account, admin_account.current_boot_identity()).model_dump(
        mode="json", exclude_unset=True
    )
    path = tmp_path / "request.json"
    if issue == "duplicate_action":
        expected["actions"] *= 2
    if issue == "wrong_scope":
        expected["observed"]["needs_user"] = True
    path.write_text(envelope(expected))
    path.chmod(0o600)
    if issue == "symlink":
        moved = path.rename(tmp_path / "valid")
        path.symlink_to(moved)
    elif issue == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif issue == "size":
        path.write_text(" " * ADMIN_REQUEST_BYTES + path.read_text())
    elif issue == "mode":
        path.chmod(0o644)
    elif issue == "owner_exec":
        path.chmod(0o700)
    elif issue == "different_identity":
        digest = read_admin_request(str(path), "apply")["digest"]
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises((OSError, ValueError)):
        read_admin_request(str(path), "apply", digest if issue == "different_identity" else None)


@pytest.mark.parametrize("issue", ["unconfirmed", "symlink", "fifo", "mode", "size", "malformed"])
def test_make_wrapper_rejects_before_ansible_can_prompt(tmp_path, issue):
    root = tmp_path / "installed"
    (root / "ansible/files").mkdir(parents=True)
    shutil.copyfile(ROOT / "ansible/files/admin.py", root / "ansible/files/admin.py")
    fake_play = tmp_path / "ansible-playbook"
    called = tmp_path / "asked-for-password"
    fake_play.write_text(f"from pathlib import Path\nPath({str(called)!r}).touch()\n")
    wrapper = (
        (ROOT / "libexec/play.sh")
        .read_text()
        .replace(
            '. "$(dirname -- "$0")/runtime.sh"',
            "root=$PWD; installed_root=$PWD; interpreter=" + shlex.quote(sys.executable),
        )
        .replace("verify_installation(Path.cwd())", "None")
        .replace("/usr/bin/python3 -I -B -c", shlex.quote(sys.executable) + " -I -B -c")
        .replace("playbook=/usr/bin/ansible-playbook", "playbook=" + shlex.quote(str(fake_play)))
    )
    (root / "libexec").mkdir()
    (root / "libexec/locked.sh").write_text('exec "$@"\n')
    stage = tmp_path / "private"
    stage.mkdir(mode=0o700)
    path = stage / "request.json"
    path.write_text(envelope())
    path.chmod(0o600)
    if issue == "symlink":
        path.symlink_to(path.rename(stage / "valid"))
    elif issue == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif issue == "mode":
        path.chmod(0o644)
    elif issue == "size":
        path.write_bytes(b" " * ADMIN_REQUEST_BYTES + path.read_bytes())
    elif issue == "malformed":
        path.write_text("{")
    result = stage / "result.json"
    proc = subprocess.run(
        ["/bin/sh", "-c", wrapper, "play", "admin", "apply"],
        cwd=root,
        env={**os.environ, "SETUP_SYSTEM_REQUEST": str(path), "SETUP_SYSTEM_RESULT": str(result)},
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert proc.returncode != 0 and not called.exists()
    assert json.loads(result.read_bytes())["status"] == "blocked"
    assert result.stat().st_mode & 0o777 == 0o600


def test_admin_wire_preserves_consent_digest_and_unicode(
    tmp_path, admin, local_account, monkeypatch
):
    from setup_system.infrastructure.operation_io import read_admin_request

    monkeypatch.setattr(admin, "current_target", lambda: ("amd64", True))
    selected = admin.run("plan", envelope()).model_dump(mode="json", exclude_unset=True)
    selected["actions"][0]["label"] = "Подтвердить доступ"
    original = json.dumps(json.loads(envelope(selected)), ensure_ascii=False, indent=2).encode()
    path = tmp_path / "request.json"
    path.write_bytes(original)
    path.chmod(0o600)
    result = read_admin_request(str(path), "apply")
    assert result["digest"] == hashlib.sha256(original).hexdigest()
    assert json.loads(result["wire"]) == json.loads(original)
