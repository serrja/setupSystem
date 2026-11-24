"""Exercise the native archive and the delivery boundaries without host installation."""

import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

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
        "debian/build",
        "debian/publish",
        "debian/control",
        "debian/setup-system",
        "debian/guard",
        "debian/setup-system.conf",
        "Makefile",
        "mk/package.mk",
        "mk/workspace.mk",
        "install.sh",
        "ansible.cfg",
        "ansible/base.yml",
        "ansible/workspace.yml",
        "ansible/templates/nvim-init.lua",
        "assets/hyprland/keys.txt",
        "config/workspace/applications.toml",
        "libexec/run",
        "src/setup_system/app.py",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
    ):
        target = fixture / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
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
    command(
        "make",
        "-f",
        "mk/package.mk",
        "package",
        f"VERSION={VERSION}",
        f"PACKAGE_DIR={out}",
        cwd=fixture,
    )
    return out / FILENAME


def test_deb_metadata_payload_and_reproducibility(package: Path, tmp_path: Path) -> None:
    sha = command("git", "rev-parse", "HEAD", cwd=package.parent / "source")
    for field, value in {
        "Version": VERSION,
        "Package": "setup-system",
        "Architecture": "all",
        "X-Setup-System-Source": sha,
    }.items():
        assert command("dpkg-deb", "-f", str(package), field) == value
    dependencies = command("dpkg-deb", "-f", str(package), "Depends")
    assert {"ansible-core", "make", "python3-textual", "git", "ca-certificates"} <= {
        part.split(" (")[0] for part in dependencies.split(", ")
    }
    raw = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(package)])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        names = set(archive.getnames())
        for name in (
            "usr/bin/setup-system",
            "usr/lib/setup-system/Makefile",
            "usr/lib/setup-system/ansible.cfg",
            "usr/lib/setup-system/ansible/base.yml",
            "usr/lib/setup-system/ansible/workspace.yml",
            "usr/lib/setup-system/config/workspace/applications.toml",
            "usr/lib/setup-system/mk/workspace.mk",
            "usr/lib/setup-system/libexec/run",
            "usr/lib/setup-system/ansible/templates/nvim-init.lua",
            "usr/lib/python3/dist-packages/setup_system/app.py",
            "usr/lib/tmpfiles.d/setup-system.conf",
        ):
            assert f"./{name}" in names
        for entry in archive.getmembers():
            assert entry.uid == entry.gid == 0
            assert not entry.mode & 0o022
            assert ".git" not in Path(entry.name).parts
            assert "__pycache__" not in entry.name
    control = tmp_path / "control"
    command("dpkg-deb", "-e", str(package), str(control))
    assert (control / "preinst").read_bytes() == (control / "prerm").read_bytes()
    assert not (control / "postinst").exists()
    fixture = package.parent / "source"
    (fixture / "config/private.txt").write_text("untracked user data")
    (fixture / "ansible/templates/nvim-init.lua").write_text("dirty file without commit identity")
    (fixture / "debian/control").write_text("invalid dirty control")
    # Locally installed Git replacement objects and export attributes must not
    # alter the package while it continues to report the original commit.
    replaced = command("git", "rev-parse", sha + ":assets/hyprland/keys.txt", cwd=fixture)
    changed = command("git", "hash-object", "-w", "config/private.txt", cwd=fixture)
    command("git", "replace", replaced, changed, cwd=fixture)
    (fixture / ".git/info").mkdir(exist_ok=True)
    (fixture / ".git/info/attributes").write_text("ansible/templates/nvim-init.lua export-ignore\n")
    command(
        "make",
        "-f",
        "mk/package.mk",
        "package",
        f"VERSION={VERSION}",
        f"PACKAGE_DIR={tmp_path}",
        cwd=fixture,
    )
    assert (tmp_path / FILENAME).read_bytes() == package.read_bytes()


def executable(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content)
    path.chmod(0o755)
    return path


def test_fixed_make_entrypoint_rejects_caller_flags(tmp_path: Path) -> None:
    destination = tmp_path / "record"
    script = (ROOT / "debian/setup-system").read_text()
    make = executable(
        tmp_path, "make", '#!/bin/sh\nenv > "$RECORD"\nprintf "%s\\n" "$@" >> "$RECORD"\n'
    )
    launch = executable(
        tmp_path,
        "setup-system",
        script.replace("/usr/bin/make", shlex.quote(str(make))).replace(
            "/usr/bin/id -u", "echo 1000"
        ),
    )
    env = dict(os.environ, RECORD=str(destination), MAKEFLAGS="--eval=bad", MAKEFILES="/untrusted")
    run = subprocess.run([launch, "base-apply"], env=env, check=False)
    assert run.returncode == 2 and not destination.exists()
    subprocess.run([launch], env=env, check=True)
    text = destination.read_text()
    assert "MAKEFLAGS=" not in text and "MAKEFILES=" not in text
    assert text.endswith("/usr/lib/setup-system/Makefile\nrun\n")


def test_guard_admits_owner_ancestor_and_refuses_parallel_writer(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.mkdir(mode=0o755)
    content = (
        (ROOT / "debian/guard")
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
def test_remote_delivery_checks_precede_privilege(
    variant: str, package: Path, tmp_path: Path
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
    executable(tools, "sudo", '#!/bin/sh\necho sudo >> "$TRACE"\nexit 91\n')
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
        ["sh", script, "update"], env=env, capture_output=True, text=True, check=False
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
            ["latest", "cdn", "sudo"] if variant == "trusted_cdn" else ["latest", "sudo"]
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
        assert result.returncode not in (0, 91) and "sudo" not in trace.read_text()


def test_reject_prerelease_and_malformed_tags_without_side_effects() -> None:
    for tag in ("v2.0.0-dev.0", "v1.2.3\nv2.3.4", "latest", "../v1.2.3"):
        result = subprocess.run(
            ["sh", ROOT / "install.sh", "install", "--release", tag],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0 and "stable tag" in result.stderr


def test_partial_debian_publication_resumes_without_overwrite(
    package: Path, tmp_path: Path
) -> None:
    # The service double stores real package bytes. Publication still checks dpkg
    # metadata, release identities, the complete inventory and each downloaded byte.
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / FILENAME).write_bytes(package.read_bytes())
    sha = command("git", "rev-parse", "HEAD", cwd=package.parent / "source")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (artifacts / (FILENAME + ".sha256")).write_text(f"{digest}  {FILENAME}\n")
    (artifacts / (FILENAME + ".sigstore.json")).write_text(digest)
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
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
root = Path(os.environ["SERVICE"])
state = root / "draft"
stable = "v1.2.3"
native = "deb-" + stable
sha = os.environ["GITHUB_SHA"]
names = sorted(path.name for path in root.glob("setup-system*"))
def emit(value):
    print(json.dumps(value))
def release():
    return {"id": 17, "tag_name": native, "prerelease": False,
            "draft": state.read_text() == "yes", "body": (root / "notes").read_text(),
            "assets": [{"name": name, "id": i+1} for i, name in enumerate(names)]}
if args[:2] == ["attestation", "verify"]:
    digest = hashlib.sha256(Path(args[2]).read_bytes()).hexdigest()
    sys.exit(0 if Path(args[args.index("--bundle")+1]).read_text().strip() == digest else 1)
elif args[:2] == ["attestation", "download"]:
    digest = hashlib.sha256(Path(args[2]).read_bytes()).hexdigest()
    assert digest == (root / "attested-original").read_text()
    Path("sha256:" + digest + ".jsonl").write_text(digest + chr(10))
elif args[0] == "api":
    endpoint = next(arg for arg in args if arg.startswith("repos/"))
    item = endpoint.split("/")[-1]
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
        emit([release()] if state.exists() else [])
    elif endpoint.endswith("releases/17"):
        emit(release())
    elif "releases/assets/" in endpoint:
        sys.stdout.buffer.write((root / names[int(item)-1]).read_bytes())
    else:
        sys.exit("Unexpected endpoint: "+endpoint)
elif args[:3] == ["release","create",native]:
    assert "--draft" in args and "--latest=false" in args
    assert not state.exists()
    shutil.copyfile(args[args.index("--notes-file") + 1], root / "notes")
    state.write_text("yes")
elif args[:3] == ["release","upload",native]:
    src = Path(args[3])
    assert not (root / src.name).exists() and state.read_text() == "yes"
    if src.name.endswith(".sigstore.json") and (root / "interrupt").exists():
        sys.exit(73)
    shutil.copyfile(src, root / src.name)
elif args[:3] == ["release","edit",native]:
    assert "--draft=false" in args and "--latest=false" in args
    state.write_text("no")
else:
    sys.exit(str(args))
""",
    )
    env = dict(
        os.environ,
        SERVICE=str(service),
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

    interruption = service / "interrupt"
    interruption.touch()
    assert publish().returncode == 73
    assert (service / "draft").read_text() == "yes" and (service / FILENAME).is_file()
    interruption.unlink()
    (service / "attested-original").write_text(digest)
    # A runner upgrade can change the container encoding of the same signed
    # payload. Keep the original remote package and recover its original proof.
    unpacked = tmp_path / "older signed package"
    command("dpkg-deb", "--raw-extract", str(package), str(unpacked))
    rebuilt = artifacts / FILENAME
    command("dpkg-deb", "--root-owner-group", "-Zgzip", "--build", str(unpacked), str(rebuilt))
    rebuilt_digest = hashlib.sha256(rebuilt.read_bytes()).hexdigest()
    assert rebuilt_digest != digest
    (artifacts / (FILENAME + ".sha256")).write_text(f"{rebuilt_digest}  {FILENAME}\n")
    (artifacts / (FILENAME + ".sigstore.json")).write_text(rebuilt_digest)
    completed = publish()
    assert completed.returncode == 0, completed.stderr
    assert (service / "draft").read_text() == "no"
    assert (service / FILENAME).read_bytes() == package.read_bytes()
    assert publish().returncode == 0
    proof = service / (FILENAME + ".sigstore.json")
    proof.write_text("invalid existing proof")
    assert publish().returncode != 0 and proof.read_text() == "invalid existing proof"


@pytest.mark.parametrize(
    "scenario", ["new", "missing_update", "downgrade", "same", "conflict", "tampered"]
)
def test_package_transaction_checks_exact_bytes_and_version(
    scenario: str, package: Path, tmp_path: Path
) -> None:
    """Exercise the package-stage shell as the current uid without installing packages."""
    tools = tmp_path / "tools"
    tools.mkdir()
    lock = tmp_path / "runtime"
    lock.mkdir(mode=0o755)
    source = tmp_path / FILENAME
    source.write_bytes(package.read_bytes())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    sha = command("dpkg-deb", "-f", str(source), "X-Setup-System-Source")
    state = tmp_path / "status"
    package_source = tmp_path / "source"
    if scenario in ("same", "conflict", "downgrade"):
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
[ "$SCENARIO" != tampered ] || printf corrupted > "$PACKAGE"
exec "$@"
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
    content = content.replace("/usr/bin/setup-system", shlex.quote(str(launcher)))
    content = content.replace("lock=/run/setup-system", 'lock="$LOCK"').replace(
        "mktemp -d /run/setup-system/deb.XXXXXXXX", 'mktemp -d "$LOCK/deb.XXXXXXXX"'
    )
    content = content.replace("0:0:755", f"{os.getuid()}:{os.getgid()}:755")
    (tmp_path / "install.sh").write_text(content)
    env = dict(
        os.environ,
        PACKAGE=str(source),
        SUDO_UID=str(os.getuid()),
        SUDO_GID=str(os.getgid()),
        SCENARIO=scenario,
        SHA=sha,
        STATE=str(state),
        PACKAGE_SOURCE=str(package_source),
        INVOKED=str(invoked),
        STARTED=str(started),
        LOCK=str(lock),
    )
    action = "update" if scenario in ("missing_update", "downgrade") else "install"
    result = subprocess.run(
        [
            "sh",
            "-c",
            '. ./install.sh; package_transaction "$@"',
            "install-local",
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
    assert ("Start it separately with: setup-system" in result.stdout) == (
        scenario in ("new", "same")
    )
    if scenario in ("new", "same"):
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, result.stdout
    assert invoked.exists() == (scenario == "new")
    assert not started.exists()
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
        (ROOT / "libexec/locked")
        .read_text()
        .replace("lock=/run/setup-system", "lock=" + shlex.quote(str(native)))
    )
    contents = contents.replace("/var/lib/dpkg/lock", str(database / "lock"))
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
