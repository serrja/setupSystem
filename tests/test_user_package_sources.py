"""Isolate native package-manager settings and carry approved artifact checksums to Apply."""

import base64
import hashlib
import json
import os
import runpy
from pathlib import Path

import pytest
import yaml
from test_native_ansible import ROOT, play
from test_native_ansible import run_env as run_env

artifact = runpy.run_path(str(ROOT / "ansible/filter_plugins/package_artifacts.py"))[
    "package_artifact"
]


def npm_metadata(content=b"package archive"):
    return {
        "dist": {
            "tarball": "https://registry.npmjs.org/@vue/language-server/-/language-server-3.0.0.tgz",
            "integrity": "sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode(),
        }
    }


def pypi_metadata(content=b"package archive"):
    file = "basedpyright-1.2.3-py3-none-any.whl"
    return {
        "urls": [
            {
                "filename": file,
                "packagetype": "bdist_wheel",
                "yanked": False,
                "url": "https://files.pythonhosted.org/packages/ab/cd/a1b2c3/" + file,
                "digests": {"sha256": hashlib.sha256(content).hexdigest()},
            }
        ]
    }


@pytest.mark.parametrize("fault", ["host", "query", "short", "weak", "filename"])
def test_npm_rejects_unverifiable_primary_artifact(fault):
    dist = npm_metadata()
    d = dist["dist"]
    if fault == "host":
        d["tarball"] = d["tarball"].replace("registry.npmjs.org", "example.invalid")
    if fault == "query":
        d["tarball"] += "?elsewhere"
    if fault == "short":
        d["integrity"] = "sha512-AQ=="
    if fault == "weak":
        d["integrity"] = d["integrity"].replace("sha512", "sha1")
    if fault == "filename":
        d["tarball"] = d["tarball"].replace("3.0.0", "3.0.1")
    with pytest.raises(ValueError):
        artifact(dist, "@vue/language-server", "npm", "3.0.0")


@pytest.mark.parametrize("fault", ["host", "hash", "arch", "yanked"])
def test_pypi_requires_a_compatible_verified_wheel(fault):
    meta = pypi_metadata()
    f = meta["urls"][0]
    if fault == "host":
        f["url"] = f["url"].replace("files.pythonhosted.org", "example.invalid")
    if fault == "hash":
        f["digests"]["sha256"] = "unknown"
    if fault == "arch":
        f["filename"] = f["filename"].replace("py3-none-any", "cp37-cp37-win32")
    if fault == "yanked":
        f["yanked"] = True
    with pytest.raises(ValueError):
        artifact(meta, "basedpyright", "pypi", "1.2.3")


@pytest.mark.parametrize("manager", ["npm", "pypi"])
@pytest.mark.parametrize("correct", [True, False])
def test_verified_native_manager_boundary_ignores_foreign_configuration(run_env, manager, correct):
    stage = Path(run_env["SETUP_SYSTEM_RESULT"]).parent
    home = Path(run_env["HOME"])
    work = stage / "managers/work"
    work.mkdir(parents=True)
    manager_home = stage / "managers/home"
    manager_home.mkdir()
    (stage / "managers/tmp").mkdir()
    (stage / "managers/artifacts").mkdir()
    (home / ".npmrc").write_text("@vue:registry=https://untrusted.example.invalid/\n")
    pip = home / "pip.conf"
    pip.write_text("[global]\nextra-index-url = https://untrusted.example.invalid/\n")
    run_env.update(
        NPM_CONFIG_USERCONFIG=str(home / ".npmrc"),
        PIP_CONFIG_FILE=str(pip),
        PIP_EXTRA_INDEX_URL="https://untrusted.example.invalid",
        NPM_CONFIG_REGISTRY="https://untrusted.example.invalid",
    )
    payload = b"package archive"
    name, version = (
        ("@vue/language-server", "3.0.0") if manager == "npm" else ("basedpyright", "1.2.3")
    )
    spec = artifact(npm_metadata() if manager == "npm" else pypi_metadata(), name, manager, version)
    source = stage / "downloaded artifact"
    source.write_bytes(payload if correct else b"unexpected data")
    spec["url"] = source.as_uri()
    marker = stage / "invoked"
    binary = stage / "native probe"
    binary.write_text(
        "#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n"
        + "Path("
        + repr(str(marker))
        + ").write_text(json.dumps([dict(os.environ),os.getcwd(),"
        "sys.argv[1:],Path(sys.argv[-1]).read_text()]))\n"
    )
    binary.chmod(0o700)
    role = yaml.safe_load((ROOT / "ansible/tasks/tool_install.yml").read_text())
    tasks = []
    for task in role:
        if "ansible.builtin.get_url" in task:
            tasks.append(task)
        args = task.get("ansible.builtin.command", {}).get("argv", [])
        for program in ("/usr/bin/npm", "/usr/bin/pipx"):
            if program in args:
                args[args.index(program)] = str(binary)
                tasks.append(task)
    book = stage / "manager.yml"
    book.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "become": False,
                    "vars": {
                        "run_dir": str(stage),
                        "tool_prefix": str(home / "tools"),
                        "managed": {
                            "key": name,
                            "value": {
                                "manager": manager,
                                "current": None,
                                "version": version,
                                "artifact": spec,
                            },
                        },
                    },
                    "tasks": tasks,
                }
            ]
        )
    )
    result = play([str(book)], run_env)
    assert not (home / "tools").exists()
    if not correct:
        assert result.returncode and "checksum" in result.stdout.lower()
        assert not marker.exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    env, cwd, argv, contents = json.loads(marker.read_text())
    assert contents == payload.decode()
    assert Path(cwd) == work and env["HOME"] == str(manager_home)
    assert not any("untrusted" in value for value in env.values())
    if manager == "npm":
        assert env["NPM_CONFIG_USERCONFIG"] != str(home / ".npmrc")
        assert env["NPM_CONFIG_USERCONFIG"] != env["NPM_CONFIG_GLOBALCONFIG"]
        assert "--registry=https://registry.npmjs.org" in argv and "--ignore-scripts" in argv
    else:
        assert env["PIP_CONFIG_FILE"] == os.devnull and "PIP_EXTRA_INDEX_URL" not in env
        assert env["PIP_INDEX_URL"] == "https://pypi.org/simple"


def test_native_vue_series_preserve_javascript_tsserver_and_debian_node_engine():
    import tomllib

    selector = runpy.run_path(str(ROOT / "ansible/filter_plugins/package_artifacts.py"))[
        "registry_release"
    ]
    ranges = tomllib.loads((ROOT / "config/workspace/editor.toml").read_text())[
        "npm_compatible_series"
    ]
    for name, major in ranges.items():
        versions = {f"{major}.0.{patch}": {"version": f"{major}.0.{patch}"} for patch in (2, 12, 3)}
        versions[f"{major}.1.0-beta"] = {}
        versions[f"{major + 1}.0.0"] = {}
        versions[f"{major}.11.0"] = {"deprecated": "this release was withdrawn"}
        assert selector({"versions": versions}, name, ranges)["version"] == f"{major}.0.12"


def test_public_metadata_requests_ignore_netrc_and_strip_authorization_on_redirect(run_env):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    home = Path(run_env["HOME"])
    netrc = home / ".netrc"
    netrc.write_text("machine 127.0.0.1 login synthetic password fixture-only\n")
    netrc.chmod(0o600)
    defaults = yaml.safe_load((ROOT / "ansible/base.yml").read_text())[0]["module_defaults"]
    assert (
        defaults
        == yaml.safe_load((ROOT / "ansible/workspace.yml").read_text())[0]["module_defaults"]
    )
    seen = []

    class Endpoint(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", self.path.replace("/redirect", "/target"))
            else:
                self.send_response(200)
            self.end_headers()
            self.wfile.write(b"public fixture")

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Endpoint) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        root = "http://127.0.0.1:" + str(server.server_port)
        stage = Path(run_env["SETUP_SYSTEM_RESULT"]).parent
        tasks = []
        for name in ("uri", "get_url"):
            for auth in (False, True):
                route = "/redirect-" + name + str(auth)
                args = {"url": root + route, "use_proxy": False}
                if name == "get_url":
                    args.update(dest=str(stage / route[1:]), mode="0600")
                if auth:
                    args["headers"] = {"Authorization": "Bearer explicit-fixture"}
                tasks.append({"ansible.builtin." + name: args})
        book = stage / "public.yml"
        book.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "gather_facts": False,
                        "become": False,
                        "module_defaults": defaults,
                        "tasks": tasks,
                    }
                ]
            )
        )
        try:
            result = play([str(book)], run_env)
        finally:
            server.shutdown()
            thread.join()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(seen) >= 8
    for dest, auth_value in seen:
        assert auth_value == (
            "Bearer explicit-fixture"
            if dest.startswith("/redirect") and dest.endswith("True")
            else None
        )
