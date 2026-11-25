"""Exercise the native archive and the delivery boundaries without host installation."""

import hashlib
import io
import json
import os
import runpy
import shlex
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.3"
FILENAME = f"setup-system_{VERSION}_all.deb"


def command(*args: str, cwd: Path = ROOT) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    return subprocess.check_output(args, cwd=cwd, text=True, env=env).strip()


@pytest.fixture(scope="module")
def package(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("debian archive")
    fixture = out / "source"
    fixture.mkdir()
    # A fixed fixture commit lets this test exercise changes under review. Only
    # these declared product inputs are copied into the disposable repository.
    for name in (
        "README.md",
        "hatch_build.py",
        "uv.lock",
        "src/setup_system/__init__.py",
        "src/setup_system_sdk/__init__.py",
        "pyproject.toml",
        "docker/plugin_artifacts.py",
        "src/setup_system_sdk/worker.py",
        "src/setup_system_sdk/apt.yml",
        "profiles/base.toml",
        "config/shared/palette.toml",
        "docs/administration.md",
        "debian/setup-system.1",
        "debian/build.py",
        "debian/publish",
        "debian/control",
        "debian/setup-system.sh",
        "debian/guard.sh",
        "debian/setup-system.conf",
        "Makefile",
        "mk/package.mk",
        "install.sh",
        "ansible.cfg",
        "libexec/run.sh",
        "src/setup_system/app.py",
        "LICENSE.md",
        "THIRD_PARTY_NOTICES.md",
    ):
        target = fixture / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    shutil.copytree(
        ROOT / "plugins/applications/git",
        fixture / "plugins/applications/git",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    command("git", "init", "--quiet", "--template=", cwd=fixture)
    command("git", "add", ".", cwd=fixture)
    command(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "user.name=Package Test",
        "-c",
        "user.email=package@example.invalid",
        "-c",
        "commit.gpgSign=false",
        "commit",
        "--quiet",
        "-m",
        "Prepare package fixture",
        cwd=fixture,
    )
    builder = runpy.run_path(str(ROOT / "debian/build.py"))
    sha = command("git", "rev-parse", "HEAD", cwd=fixture)
    artifact = builder["build"](fixture, out, sha, VERSION)
    assert artifact == out / FILENAME
    return artifact


def test_deb_metadata_payload_and_reproducibility(package: Path, tmp_path: Path) -> None:
    fixture = package.parent / "source"
    sha = command("git", "rev-parse", "HEAD", cwd=fixture)
    for field, value in {
        "Version": VERSION,
        "Package": "setup-system",
        "Architecture": "all",
        "X-Setup-System-Source": sha,
    }.items():
        assert command("dpkg-deb", "-f", str(package), field) == value
    dependencies = command("dpkg-deb", "-f", str(package), "Depends")
    runtime = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    names = {canonicalize_name(Requirement(requirement).name) for requirement in runtime}
    debian_names = {"ansible-core": "ansible-core", "pyyaml": "python3-yaml"}
    required = {debian_names.get(name, f"python3-{name}") for name in names}
    assert required | {"make", "git", "ca-certificates"} <= {
        part.split(" (")[0] for part in dependencies.split(", ")
    }
    raw = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(package)])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        names = set(archive.getnames())
        for name in (
            "usr/bin/setup-system",
            "usr/share/doc/setup-system/README.md",
            "usr/share/doc/setup-system/docs/administration.md",
            "usr/share/man/man1/setup-system.1.gz",
            "usr/lib/setup-system/Makefile",
            "usr/lib/setup-system/plugin_catalog/git.json",
            "usr/lib/setup-system/plugins/git/setup_plugin_git/Makefile",
            "usr/lib/setup-system/sdk/apt.yml",
            "usr/lib/python3/dist-packages/setup_system_sdk/worker.py",
            "usr/lib/python3/dist-packages/setup_system-1.2.3.dist-info/METADATA",
            "usr/lib/setup-system/ansible.cfg",
            "usr/lib/setup-system/libexec/run.sh",
            "usr/lib/python3/dist-packages/setup_system/app.py",
            "usr/lib/tmpfiles.d/setup-system.conf",
        ):
            assert f"./{name}" in names
        manual = archive.extractfile("./usr/share/man/man1/setup-system.1.gz")
        assert manual is not None
        with manual:
            assert manual.read() == subprocess.check_output(
                ["/usr/bin/gzip", "-n", "-9", "-c"],
                input=(fixture / "debian/setup-system.1").read_bytes(),
                env={"LC_ALL": "C"},
            )
        for entry in archive.getmembers():
            assert entry.uid == entry.gid == 0
            assert not entry.mode & 0o022
            assert ".git" not in Path(entry.name).parts
            assert "__pycache__" not in entry.name
    control = tmp_path / "control"
    command("dpkg-deb", "-e", str(package), str(control))
    assert (control / "preinst").read_bytes() == (control / "prerm").read_bytes()
    assert not (control / "postinst").exists()
    (fixture / "config/private.txt").write_text("untracked user data")
    (fixture / "plugins/applications/git/src/setup_plugin_git/Makefile").write_text(
        "dirty file without commit identity"
    )
    (fixture / "debian/control").write_text("invalid dirty control")
    # Locally installed Git replacement objects and export attributes must not
    # alter the package while it continues to report the original commit.
    replaced = command("git", "rev-parse", sha + ":config/shared/palette.toml", cwd=fixture)
    changed = command("git", "hash-object", "-w", "config/private.txt", cwd=fixture)
    command("git", "replace", replaced, changed, cwd=fixture)
    (fixture / ".git/info").mkdir(exist_ok=True)
    (fixture / ".git/info/attributes").write_text(
        "plugins/applications/git/src/setup_plugin_git/* export-ignore\n"
    )
    command(
        "make",
        "-f",
        "mk/package.mk",
        "package",
        f"VERSION={VERSION}",
        f"PACKAGE_DIR={tmp_path}",
        f"BUILD_PYTHON={sys.executable}",
        cwd=fixture,
    )
    assert (tmp_path / FILENAME).read_bytes() == package.read_bytes()


def plugin_members(package: Path) -> dict[str, bytes]:
    prefix = "./usr/lib/setup-system/plugin_catalog/"
    raw = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(package)])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return {
            m.name.removeprefix(prefix): stream.read()
            for m in archive.getmembers()
            if m.isfile() and m.name.startswith(prefix) and (stream := archive.extractfile(m))
        }


def executable(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content)
    path.chmod(0o755)
    return path


def test_fixed_make_entrypoint_rejects_caller_flags(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from setup_system import entrypoint

    wrapper = (ROOT / "debian/setup-system.sh").read_text()
    assert 'exec /usr/bin/python3 -I -B -m setup_system "$@"' in wrapper
    monkeypatch.setenv("MAKEFLAGS", "--eval=bad")
    monkeypatch.setenv("MAKEFILES", "/untrusted")
    monkeypatch.setenv("BASH_ENV", "/untrusted")
    monkeypatch.setenv("ENV", "/untrusted")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    observed = []
    monkeypatch.setattr(os, "execve", lambda *arguments: observed.append(arguments))
    monkeypatch.setattr(entrypoint, "resource_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    cli = CliRunner()
    assert cli.invoke(entrypoint.cli, ["base-apply"]).exit_code == 2 and not observed
    assert cli.invoke(entrypoint.cli, []).exit_code == 0
    [(executable, args, env)] = observed
    assert executable == "/usr/bin/make" and args[-2:] == [str(tmp_path / "Makefile"), "run"]
    assert not {"MAKEFLAGS", "MAKEFILES", "BASH_ENV", "ENV", "PYTHONPATH"} & env.keys()


def test_guard_admits_owner_ancestor_and_refuses_parallel_writer(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.mkdir(mode=0o755)
    lock.chmod(0o755)
    content = (
        (ROOT / "debian/guard.sh")
        .read_text()
        .replace("lock=/run/setup-system", f"lock={shlex.quote(str(lock))}")
    )
    guard = executable(
        tmp_path,
        "guard",
        content.replace("0:0:755", f"{os.getuid()}:{os.getgid()}:755").replace(
            "owner_uid=0", f"owner_uid={os.getuid()}"
        ),
    )
    subprocess.run(["flock", "-x", lock, guard], check=True)
    with subprocess.Popen(
        ["flock", "-x", lock, "sh", "-c", "echo ready; read -r end"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        assert holder.stdin and holder.stdout
        assert holder.stdout.readline() == "ready\n"
        denied = subprocess.run([guard], capture_output=True, text=True, check=False)
        holder.stdin.write("end\n")
        holder.stdin.close()
    assert denied.returncode != 0 and "is busy" in denied.stderr


@pytest.mark.parametrize(
    "variant",
    [
        "valid",
        "digest",
        "checksum",
        "signature",
        "different_source",
        "missing_companion",
        "wrong_codename",
        "unknown_redirect",
        "misdirected_api",
        "trusted_cdn",
        "oversized_checksum",
    ],
)
@pytest.mark.parametrize("auth", ["sudo", "su"])
def test_remote_delivery_checks_precede_privilege(
    variant: str, auth: str, package: Path, tmp_path: Path
) -> None:
    sha = command("git", "rev-parse", "HEAD", cwd=package.parent / "source")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    assets = tmp_path / "responses"
    assets.mkdir()
    (assets / FILENAME).write_bytes(package.read_bytes())
    (assets / (FILENAME + ".sha256")).write_text(
        f"{'bad' if variant == 'checksum' else digest}  {FILENAME}\n"
    )
    (assets / (FILENAME + ".sigstore.json")).write_text(variant)
    for tag in (f"v{VERSION}", f"deb-v{VERSION}"):
        source = "f" * 40 if variant == "different_source" and tag.startswith("deb-") else sha
        (assets / f"tag_{tag}").write_text(
            json.dumps({"ref": f"refs/tags/{tag}", "object": {"type": "commit", "sha": source}})
        )
        (assets / f"release_{tag}").write_text(
            json.dumps(
                {
                    "tag_name": tag,
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-11-24T10:00:00Z",
                    "assets": [
                        {
                            "name": FILENAME,
                            "digest": "sha256:" + ("1" * 64 if variant == "digest" else digest),
                        },
                        {"name": FILENAME + ".sha256"},
                        {"name": FILENAME + ".sigstore.json"},
                        *(
                            {"name": n, "digest": "sha256:" + hashlib.sha256(b).hexdigest()}
                            for n, b in plugin_members(package).items()
                        ),
                    ],
                }
            )
        )
    tools = tmp_path / "tools"
    tools.mkdir()
    executable(
        tools,
        "curl",
        """#!/bin/sh
set -eu
location= destination=
while [ "$#" -gt 0 ]; do
    case "$1" in --output) shift; destination=$1 ;; https:*) location=$1 ;; esac
    shift
done
name=${location##*/}
case "$location" in
    */releases/latest)
        echo latest >> "$TRACE"
        printf '%s' "https://github.com/serrja/setupSystem/releases/tag/v1.2.3"
        exit ;;
    */git/ref/tags/*)
        if [ "$VARIANT" = misdirected_api ]; then
            printf '%s' '307 https://release-assets.githubusercontent.com/metadata'; exit
        fi
        [ "$VARIANT:$name" != missing_companion:deb-v1.2.3 ] || exit 22
        name=tag_$name ;;
    */releases/tags/*) name=release_$name ;;
    */releases/download/*.deb)
        case "$VARIANT" in
            unknown_redirect) printf '%s' '302 https://untrusted.invalid/PRIVATE_QUERY'; exit ;;
            trusted_cdn)
                printf '%s' "302 https://release-assets.githubusercontent.com/$name"; exit ;;
        esac ;;
    https://release-assets.githubusercontent.com/*.deb) echo cdn >> "$TRACE" ;;
    */releases/download/*.sha256)
        if [ "$VARIANT" = oversized_checksum ]; then
            head -c 257 /dev/zero > "$destination"; printf '200 '; exit
        fi ;;
    https://untrusted.invalid/*|*/metadata) echo unexpected >> "$TRACE"; exit 1 ;;
esac
cp "$RESPONSES/$name" "$destination"
printf '200 '
""",
    )
    executable(
        tools,
        "gh",
        """#!/bin/sh
set -eu
case "$*" in *--help*)
    echo "--bundle --signer-workflow --signer-digest --source-ref --source-digest"
    echo "--deny-self-hosted-runners --predicate-type"
    exit ;;
esac
printf '%s\\n' "$@" > "$VERIFY"
[ "$VARIANT" != signature ]
""",
    )
    executable(tools, "id", '#!/bin/sh\n[ "$1" = -u ] && echo 1000\n')
    for method in ("su", "sudo"):
        executable(tools, method, f'#!/bin/sh\necho {method} >> "$TRACE"\nexit 91\n')
    executable(tools, "dpkg", '#!/bin/sh\n[ "$1" = --print-architecture ] && echo amd64\n')
    release = tmp_path / "os-release"
    codename = "unknown" if variant == "wrong_codename" else "trixie"
    release.write_text(f"ID=debian\nVERSION_ID=13\nVERSION_CODENAME={codename}\n")
    content = (
        (ROOT / "install.sh").read_text().replace("/etc/os-release", shlex.quote(str(release)))
    )
    content = content.replace(
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"PATH={shlex.quote(str(tools))}:/usr/bin:/bin"
    )
    script = executable(tmp_path, "install.sh", content)
    home = tmp_path / "desktop HOME"
    home.mkdir(mode=0o700)
    old = tmp_path / "old user releases"
    old.mkdir()
    (home / ".local").symlink_to(old)
    (home / ".ecryptfs").mkdir()
    personal = old / "setup-system"
    personal.write_text("owner customization")
    trace, verify = tmp_path / "trace", tmp_path / "verify"
    env = dict(
        os.environ,
        HOME=str(home),
        TRACE=str(trace),
        VERIFY=str(verify),
        RESPONSES=str(assets),
        VARIANT=variant,
        TMPDIR=str(tmp_path),
    )
    result = subprocess.run(
        ["sh", script, "update", *(["--auth", "su"] if auth == "su" else [])],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert personal.read_text() == "owner customization"
    assert sorted(p.name for p in home.iterdir()) == [".ecryptfs", ".local"]
    assert list(old.iterdir()) == [personal]
    if variant == "wrong_codename":
        assert result.returncode != 0 and "trixie" in result.stderr and not trace.exists()
        return
    assert trace.read_text().splitlines().count("latest") == 1
    assert "unexpected" not in trace.read_text() and "PRIVATE_QUERY" not in result.stderr
    if variant in ("valid", "trusted_cdn"):
        assert result.returncode == 91, result.stderr
        assert trace.read_text().splitlines() == (
            ["latest", "cdn", auth] if variant == "trusted_cdn" else ["latest", auth]
        )
        args = verify.read_text().splitlines()
        for flag, expected in {
            "--signer-workflow": "serrja/setupSystem/.github/workflows/debian.yml",
            "--source-ref": "refs/heads/main",
            "--source-digest": sha,
            "--signer-digest": sha,
        }.items():
            assert args[args.index(flag) + 1] == expected
        assert "--deny-self-hosted-runners" in args
    else:
        assert result.returncode not in (0, 91)
        assert {"su", "sudo"}.isdisjoint(trace.read_text().splitlines())


def test_reject_prerelease_and_malformed_tags_without_side_effects() -> None:
    for tag in ("v2.0.0-dev.0", "v1.2.3\nv2.3.4", "latest", "../v1.2.3"):
        result = subprocess.run(
            ["sh", ROOT / "install.sh", "install", "--release", tag],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0 and "stable tag" in result.stderr


def signed_subjects(package: Path) -> str:
    subjects = {FILENAME: hashlib.sha256(package.read_bytes()).hexdigest()}
    subjects.update(
        {name: hashlib.sha256(data).hexdigest() for name, data in plugin_members(package).items()}
    )
    return json.dumps(subjects, sort_keys=True)


@pytest.fixture
def publication(package: Path, tmp_path: Path):
    # The service double stores real package bytes. Publication still checks dpkg
    # metadata, release identities, the complete inventory and each downloaded byte.
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / FILENAME).write_bytes(package.read_bytes())
    sha = command("git", "rev-parse", "HEAD", cwd=package.parent / "source")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (artifacts / (FILENAME + ".sha256")).write_text(f"{digest}  {FILENAME}\n")
    (artifacts / (FILENAME + ".sigstore.json")).write_text(signed_subjects(package))
    service = tmp_path / "service"
    service.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    executable(
        tools,
        "gh",
        """#!/usr/bin/python3
import hashlib
import json
import os
import runpy
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
root = Path(os.environ["SERVICE"])
state = root / "draft"
stable = "v1.2.3"
native = "deb-" + stable
sha = os.environ["GITHUB_SHA"]
names = sorted(p.name for p in root.iterdir() if p.name.startswith("setup-system")
               or p.name.endswith((".json",".whl")) and p.name != "notes.json")
def emit(value):
    print(json.dumps(value))
def release():
    assets = [{"name": name, "id": i+1, "state": "uploaded",
               "digest": "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest(),
               "size": (root / name).stat().st_size} for i, name in enumerate(names)]
    fault = root / "fault"
    if assets and fault.exists():
        phase, field = fault.read_text().split()
        if phase == "download" or (root / "trigger").exists():
            asset = assets[0]
            asset[field] = "sha256:" + "0"*64 if field == "digest" else asset[field] + 100
    if (root / "duplicate-asset").exists():
        assets.append(assets[0])
    return {"id": 17, "tag_name": native, "prerelease": False,
            "target_commitish": (root / "target").read_text(),
            "draft": state.read_text() == "yes", "body": (root / "notes").read_text(),
            "assets": assets}
if args[:2] == ["attestation", "verify"]:
    digest = hashlib.sha256(Path(args[2]).read_bytes()).hexdigest()
    proof = json.loads(Path(args[args.index("--bundle")+1]).read_text())
    sys.exit(0 if proof.get(Path(args[2]).name) == digest else 1)
elif args[:2] == ["attestation", "download"]:
    digest = hashlib.sha256(Path(args[2]).read_bytes()).hexdigest()
    proof = (root / "attested-original").read_text()
    assert json.loads(proof)[Path(args[2]).name] == digest
    Path("sha256:" + digest + ".jsonl").write_text(proof + chr(10))
elif args[0] == "api":
    endpoint = next(arg for arg in args if arg.startswith(("repos/", "https://uploads.github.com/")))
    item = endpoint.split("/")[-1]
    if args[1:3] in (["-X", "POST"], ["-X", "PATCH"]):
        with (root / "writes").open("a") as log:
            print(args[2], endpoint, file=log)
    if "/matching-refs/" in endpoint:
        emit([] if item == native and not (root / "ref").exists() else
             [{"ref": "refs/tags/"+item, "object": {"type":"commit", "sha":sha}}])
    elif endpoint.endswith("git/refs") and args[1:3] == ["-X","POST"]:
        (root / "ref").touch(exist_ok=False)
    elif endpoint.endswith("releases/tags/"+stable):
        emit({"tag_name":stable, "draft":False, "prerelease":False, "body":json.dumps({
            "runId":"17", "eventCreatedAt":"2026-11-24T10:00:00Z",
            "schemaVersion":2, "contractSchema":"h02-python/v1", "extraField":"accepted",
            "repository":"serrja/setupSystem",
            "version":"1.2.3", "sha":sha, "branch":"main"})})
    elif "releases?" in endpoint:
        # Listing may still omit the draft even though creation returned its ID.
        visible = state.exists() and not (root / "hide-created-draft").exists()
        emit([release()] if visible else [])
    elif endpoint == "repos/serrja/setupSystem/releases" and args[1:3] == ["-X", "POST"]:
        assert not state.exists() and "tag_name="+native in args and "target_commitish="+sha in args
        assert "draft=true" in args and "make_latest=false" in args
        notes = next(arg.removeprefix("body=@") for arg in args if arg.startswith("body=@"))
        shutil.copyfile(notes, root / "notes")
        (root / "target").write_text(sha)
        state.write_text("yes")
        emit(release())
    elif endpoint == "repos/serrja/setupSystem/releases/17":
        if args[1:3] == ["-X", "PATCH"]:
            assert "draft=false" in args and "make_latest=false" in args
            assert state.read_text() == "yes" and len(names) == int(os.environ["ASSET_COUNT"])
            state.write_text("no")
            if (root / "fault").exists() and (root / "fault").read_text().startswith("after "):
                (root / "trigger").touch()
        response = release()
        if args[1] != "-X" and len(names) == int(os.environ["ASSET_COUNT"]):
            assets_lag = root / "hide-upload-readback"
            draft_lag = root / "show-draft-readback"
            if response["draft"] and assets_lag.exists():
                response["assets"] = response["assets"][:-1]
                assets_lag.unlink()
            elif not response["draft"] and draft_lag.exists():
                response["draft"] = True
                draft_lag.unlink()
        emit(response)
    elif endpoint.startswith("https://uploads.github.com/repos/serrja/setupSystem/releases/17/assets?name="):
        assert args[1:3] == ["-X", "POST"] and "Content-Type: application/octet-stream" in args
        src = Path(args[args.index("--input")+1])
        assert src.name == endpoint.partition("?name=")[2]
        assert not (root / src.name).exists() and state.read_text() == "yes"
        if src.name.endswith(".sigstore.json") and (root / "interrupt").exists():
            sys.exit(73)
        shutil.copyfile(src, root / src.name)
        emit({"name": src.name, "state": "uploaded"})
    elif "releases/assets/" in endpoint:
        name = names[int(item)-1]
        sys.stdout.buffer.write((root / name).read_bytes())
        if name.endswith(".sigstore.json") and (root / "fault").exists():
            if (root / "fault").read_text().startswith("before "):
                (root / "trigger").touch()
    else:
        sys.exit("Unexpected endpoint: "+endpoint)
else:
    sys.exit(str(args))
""",
    )
    env = dict(
        os.environ,
        SERVICE=str(service),
        ASSET_COUNT=str(3 + len(plugin_members(package))),
        GITHUB_SHA=sha,
        GITHUB_REF="refs/heads/main",
        GITHUB_EVENT_NAME="push",
        GITHUB_REPOSITORY="serrja/setupSystem",
        PATH=f"{tools}:/usr/bin:/bin",
        TMPDIR=str(tmp_path),
    )

    def publish() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "debian/publish", VERSION, str(artifacts)],
            cwd=package.parent / "source",
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    return service, artifacts, publish


def test_partial_debian_publication_resumes_without_overwrite(package, tmp_path, publication):
    service, artifacts, publish = publication
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    interruption = service / "interrupt"
    interruption.touch()
    delayed = service / "hide-created-draft"
    delayed.touch()
    first = publish()
    assert first.returncode == 73, first.stderr
    assert (service / "draft").read_text() == "yes" and (service / FILENAME).is_file()
    interruption.unlink()
    delayed.unlink()
    # Existing drafts created with the previous CLI carry the default branch name.
    (service / "target").write_text("main")
    for conflict in ("duplicate-asset", "foreign-asset"):
        marker = service / ("setup-system-unexpected" if conflict == "foreign-asset" else conflict)
        marker.touch()
        rejected = publish()
        assert rejected.returncode != 0 and (service / "draft").read_text() == "yes"
        assert not (service / (FILENAME + ".sigstore.json")).exists()
        marker.unlink()
    notes = service / "notes"
    original_notes = notes.read_text()
    notes.write_text("{}")
    assert publish().returncode != 0 and notes.read_text() == "{}"
    notes.write_text(original_notes)
    (service / "attested-original").write_text(signed_subjects(package))
    # A runner upgrade can change the container encoding of the same signed
    # payload. Keep the original remote package and recover its original proof.
    unpacked = tmp_path / "older signed package"
    command("dpkg-deb", "--raw-extract", str(package), str(unpacked))
    rebuilt = artifacts / FILENAME
    command("dpkg-deb", "--root-owner-group", "-Zgzip", "--build", str(unpacked), str(rebuilt))
    rebuilt_digest = hashlib.sha256(rebuilt.read_bytes()).hexdigest()
    assert rebuilt_digest != digest
    (artifacts / (FILENAME + ".sha256")).write_text(f"{rebuilt_digest}  {FILENAME}\n")
    (artifacts / (FILENAME + ".sigstore.json")).write_text(signed_subjects(rebuilt))
    for marker in ("hide-upload-readback", "show-draft-readback"):
        (service / marker).touch()
    completed = publish()
    assert completed.returncode == 0, completed.stderr
    assert not (service / "hide-upload-readback").exists()
    assert not (service / "show-draft-readback").exists()
    assert (service / "draft").read_text() == "no"
    assert (service / FILENAME).read_bytes() == package.read_bytes()
    assert publish().returncode == 0
    proof = service / (FILENAME + ".sigstore.json")
    proof.write_text("invalid existing proof")
    assert publish().returncode != 0 and proof.read_text() == "invalid existing proof"


@pytest.mark.parametrize(
    ("phase", "field"),
    [
        ("download", "digest"),
        ("download", "size"),
        ("before", "id"),
        ("before", "digest"),
        ("after", "id"),
        ("after", "digest"),
    ],
)
def test_publication_detects_replaced_verified_assets(publication, phase, field):
    service, _, publish = publication
    assert publish().returncode == 0
    (service / "draft").write_text("yes")
    (service / "writes").write_text("")
    (service / "fault").write_text(f"{phase} {field}")
    result = publish()
    assert result.returncode != 0
    writes = (service / "writes").read_text().splitlines()
    assert writes == (["PATCH repos/serrja/setupSystem/releases/17"] if phase == "after" else [])
    assert (service / "draft").read_text() == ("no" if phase == "after" else "yes")


@pytest.mark.parametrize(
    "scenario",
    [
        "new",
        "missing_update",
        "downgrade",
        "same",
        "same_shadow",
        "conflict",
        "tampered",
        "su_denied",
        "symlink",
        "fifo",
    ],
)
@pytest.mark.parametrize("auth", ["sudo", "su"])
def test_package_transaction_checks_exact_bytes_and_version(
    scenario: str, auth: str, package: Path, tmp_path: Path
) -> None:
    """Exercise the package-stage shell as the current uid without installing packages."""
    tools = tmp_path / "tools"
    tools.mkdir()
    lock = tmp_path / "runtime"
    lock.mkdir(mode=0o755)
    lock.chmod(0o755)
    source = tmp_path / FILENAME
    source.write_bytes(package.read_bytes())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    sha = command("dpkg-deb", "-f", str(source), "X-Setup-System-Source")
    legacy_dir = tmp_path / "old user HOME/.local/bin"
    legacy_dir.mkdir(parents=True)
    legacy = executable(legacy_dir, "setup-system", '#!/bin/sh\ntouch -- "$STARTED"\n')
    original = (
        legacy.read_bytes(),
        legacy.stat().st_ino,
        legacy.stat().st_mode,
        legacy.stat().st_mtime_ns,
    )
    state = tmp_path / "status"
    package_source = tmp_path / "source"
    if scenario in ("same", "same_shadow", "conflict", "downgrade"):
        state.write_text("installed " + ("9.9.9" if scenario == "downgrade" else VERSION))
        package_source.write_text("a" * 40 if scenario == "conflict" else sha)
    invoked = tmp_path / "apt"
    started = tmp_path / "app-started"
    launcher = executable(tools, "setup-system", '#!/bin/sh\ntouch -- "$STARTED"\n')
    executable(
        tools,
        "sudo",
        """#!/bin/sh
set -eu
echo sudo > "$AUTH_LOG"
[ "$SCENARIO" != su_denied ] || exit 1
[ "$SCENARIO" != tampered ] || printf corrupted > "$PACKAGE"
exec "$@"
""",
    )
    executable(
        tools,
        "su",
        """#!/bin/sh
set -eu
echo su > "$AUTH_LOG"
[ "$1" = --shell ] && [ "$2" = /bin/sh ] && [ "$3" = --command ]
program=$4
[ "$5" = -- ] && [ "$6" = root ] && [ "$7" = sh ] || exit 2
shift 7
[ "$SCENARIO" != su_denied ] || exit 1
[ "$SCENARIO" != tampered ] || printf corrupted > "$PACKAGE"
exec /bin/sh -ceu "$program" sh "$@"
""",
    )
    executable(
        tools,
        "setpriv",
        """#!/bin/sh
set -eu
[ "$1" = "--reuid=$SUDO_UID" ] && [ "$2" = "--regid=$SUDO_GID" ] || exit 2
[ "$3" = --init-groups ] && [ "$4" = --no-new-privs ] || exit 2
shift 4
exec "$@"
""",
    )
    executable(
        tools,
        "dpkg-query",
        """#!/bin/sh
set -eu
case "$*" in
    *X-Setup-System-Source*) cat "$PACKAGE_SOURCE" ;;
    *) cat "$STATE" ;;
esac
""",
    )
    executable(
        tools,
        "apt-get",
        """#!/bin/sh
set -eu
[ "$#" = 3 ] && [ "$1" = --no-remove ] && [ "$2" = install ] || exit 2
[ "$(dpkg-deb -f "$3" X-Setup-System-Source)" = "$SHA" ]
if flock -xn "$LOCK" true; then exit 3; fi
printf '%s\\n' "$3" > "$INVOKED"
printf 'installed 1.2.3' > "$STATE"
""",
    )
    content = (ROOT / "install.sh").read_text()
    content = content.replace(
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"PATH={shlex.quote(str(tools))}:/usr/bin:/bin"
    )
    content = content.replace("exec /usr/bin/setup-system", "exec " + shlex.quote(str(launcher)))
    content = content.replace(
        " = /usr/bin/setup-system ]; then", f" = {shlex.quote(str(launcher))} ]; then"
    )
    content = content.replace("lock=/run/setup-system", 'lock="$LOCK"').replace(
        "mktemp -d /run/setup-system/deb.XXXXXXXX", 'mktemp -d "$LOCK/deb.XXXXXXXX"'
    )
    content = content.replace("0:0:755", f"{os.getuid()}:{os.getgid()}:755")
    # Keep only the fixed test-fixture inputs across the production clean environment.
    content = content.replace(
        "HOME=/root LANG=C flock",
        'HOME=/nonexistent LANG=C SCENARIO="$SCENARIO" SUDO_UID="$SUDO_UID" SUDO_GID="$SUDO_GID" '
        'PACKAGE_SOURCE="$PACKAGE_SOURCE" STATE="$STATE" SHA="$SHA" '
        'INVOKED="$INVOKED" LOCK="$LOCK" TMPDIR="$LOCK" flock',
    )
    (tmp_path / "install.sh").write_text(content)
    env = dict(
        os.environ,
        PACKAGE=str(source),
        SUDO_UID=str(os.getuid()),
        SUDO_GID=str(os.getgid()),
        SCENARIO=scenario,
        AUTH_LOG=str(tmp_path / "auth"),
        SHA=sha,
        STATE=str(state),
        PACKAGE_SOURCE=str(package_source),
        INVOKED=str(invoked),
        STARTED=str(started),
        PATH=(
            f"{legacy_dir}:/usr/bin:/bin"
            if scenario in ("new", "same_shadow")
            else f"{tools}:/usr/bin:/bin"
        ),
        LOCK=str(lock),
    )
    if scenario in {"symlink", "fifo"}:
        moved = source.rename(source.with_suffix(".saved"))
        if scenario == "symlink":
            source.symlink_to(moved)
        else:
            os.mkfifo(source, 0o600)
    action = "update" if scenario in ("missing_update", "downgrade", "same_shadow") else "install"
    result = subprocess.run(
        [
            "timeout",
            "--kill-after=1s",
            "5s",
            "sh",
            "-c",
            '. ./install.sh; package_auth=$1; shift; package_transaction "$@"',
            "install-local.sh",
            auth,
            str(source),
            digest,
            VERSION,
            sha,
            action,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ("Start it separately with: /usr/bin/setup-system" in result.stdout) == (
        scenario in ("new", "same_shadow")
    )
    assert ("Start it separately with: setup-system" in result.stdout) == (scenario == "same")
    if scenario in ("new", "same", "same_shadow"):
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, result.stdout
    assert result.returncode not in {124, 137}, (
        "The artifact reader blocked while holding the package lock"
    )
    assert (tmp_path / "auth").read_text().strip() == auth
    assert invoked.exists() == (scenario == "new")
    assert not started.exists()
    assert (
        legacy.read_bytes(),
        legacy.stat().st_ino,
        legacy.stat().st_mode,
        legacy.stat().st_mtime_ns,
    ) == original
    assert ("shadows /usr/bin/setup-system" in result.stderr) == (
        scenario in ("new", "same_shadow")
    )
    assert list(lock.iterdir()) == []


@pytest.mark.parametrize("writer", ["dpkg", "frontend", "setup-system"])
def test_apply_reports_a_live_writer_before_starting_the_operation(tmp_path, writer):
    import sys

    native = tmp_path / "native lock"
    native.mkdir()
    database = tmp_path / "db"
    database.mkdir()
    locks = [database / "lock", database / "lock-frontend"]
    for item in locks:
        item.touch(mode=0o600)
    stage = tmp_path / "operation"
    contents = (
        (ROOT / "libexec/locked.sh")
        .read_text()
        .replace("lock=/run/setup-system", "lock=" + shlex.quote(str(native)))
    )
    helper = (
        (ROOT / "libexec/dpkg_lock.py")
        .read_text()
        .replace("/var/lib/dpkg/lock", str(database / "lock"))
    )
    (tmp_path / "dpkg_lock.py").write_text(helper)
    task = executable(tmp_path, "locked", contents)
    if writer == "setup-system":
        probe = (
            "import fcntl,os,sys; f=os.open(sys.argv[1],os.O_RDONLY); fcntl.flock(f,fcntl.LOCK_EX)"
        )
        selected = native
    else:
        probe = "import fcntl,sys; f=open(sys.argv[1], 'r+b'); fcntl.lockf(f, fcntl.LOCK_EX)"
        selected = locks[int(writer == "frontend")]
    probe += "; print('ready',flush=True); input()"
    run = tmp_path / "private request"
    run.mkdir(mode=0o700)
    request = run / "request.json"
    request.write_bytes(b"{}")
    request.chmod(0o600)
    report = run / "result.json"
    env = dict(os.environ, SETUP_SYSTEM_REQUEST=str(request), SETUP_SYSTEM_RESULT=str(report))
    with subprocess.Popen(
        [sys.executable, "-I", "-c", probe, str(selected)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        assert holder.stdout and holder.stdin
        assert holder.stdout.readline() == "ready\n"
        denied = subprocess.run(
            [task, "/usr/bin/touch", stage], env=env, capture_output=True, text=True, check=False
        )
        existing = report.read_bytes()
        repeated = subprocess.run(
            [task, "/usr/bin/touch", stage], env=env, capture_output=True, check=False
        )
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert repeated.returncode == 75 and report.read_bytes() == existing
        assert set(run.iterdir()) == {request, report}
        assert denied.returncode == 75
        if writer != "setup-system":
            assert str(holder.pid) in denied.stderr
        response = json.loads(report.read_bytes())
        assert response["status"] == "blocked" and "writer lock" in response["blockers"][0]
        assert report.stat().st_mode & 0o777 == 0o600
        assert not stage.exists()
    subprocess.run([task, "/usr/bin/touch", stage], check=True)
    assert stage.exists()


def test_draft_stays_private_if_a_standalone_plugin_is_missing_from_the_bundle(publication):
    service, artifacts, publish = publication
    file = artifacts / (FILENAME + ".sigstore.json")
    proof = json.loads(file.read_bytes())
    del proof["git.json"]
    file.write_text(json.dumps(proof))
    result = publish()
    assert result.returncode and (service / "draft").read_text() == "yes"


def test_resource_verifier_cross_checks_archives_and_rejects_changed_bytes(
    package, tmp_path, monkeypatch
):
    import zipfile

    import hatchling.build

    # Start from the exact fixture commit even if another case dirtied its checkout.
    source = tmp_path / "source"
    command(
        "git",
        "clone",
        "--quiet",
        "--local",
        "--no-hardlinks",
        "--",
        str(package.parent / "source"),
        str(source),
    )
    monkeypatch.chdir(source)
    output = tmp_path / "release"
    output.mkdir()
    wheel = output / hatchling.build.build_wheel(str(output))
    sdist = output / hatchling.build.build_sdist(str(output))
    verify = runpy.run_path(str(ROOT / "docker/verify_native.py"))["verify_resources"]
    verify(source, wheel, sdist, package)
    with zipfile.ZipFile(wheel) as valid:
        originals = {item.filename: valid.read(item) for item in valid.infolist()}
    for number, (target, reason) in enumerate(
        (
            ("setup_system/app.py", "Wheel modules"),
            ("setup_system/bundle/config/shared/palette.toml", "Wheel runtime resources"),
            ("setup_system/bundle/plugin_catalog/git.json", "release differs"),
        )
    ):
        broken = tmp_path / f"broken-{number}.whl"
        with zipfile.ZipFile(broken, "w") as edited:
            for name, data in originals.items():
                if name == target:
                    if name.endswith(".json"):
                        document = json.loads(data)
                        document["releases"][0]["wheels"][0]["sha256"] = "0" * 64
                        data = json.dumps(document).encode()
                    else:
                        data += b"changed"
                edited.writestr(name, data)
        with pytest.raises(ValueError, match=reason):
            verify(source, broken, sdist, package)

    # A valid dpkg archive with an altered protected payload must fail independently.
    tree = tmp_path / "package-tree"
    command("dpkg-deb", "--raw-extract", str(package), str(tree))
    asset = tree / "usr/lib/setup-system/plugins/git/setup_plugin_git/Makefile"
    asset.chmod(0o666)
    rewrapped = tmp_path / "changed.deb"
    command("dpkg-deb", "--root-owner-group", "--build", str(tree), str(rewrapped))
    with pytest.raises(ValueError, match="Invalid Debian resource"):
        verify(source, wheel, sdist, rewrapped)
    asset.chmod(0o644)
    asset.write_bytes(b"changed")
    command("dpkg-deb", "--root-owner-group", "--build", str(tree), str(rewrapped))
    with pytest.raises(ValueError, match="Debian resources disagree"):
        verify(source, wheel, sdist, rewrapped)
