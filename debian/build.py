#!/usr/bin/python3
"""Build a root-owned Debian archive from the checked-out runtime files."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from email.parser import Parser
from pathlib import Path
from zipfile import ZipFile

GIT_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/dev/null",
    "LANG": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_NO_REPLACE_OBJECTS": "1",
}

RUNTIME_TREES = ("mk", "config", "libexec", "profiles")
PACKAGES = ("setup_system", "setup_system_sdk")


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], env=GIT_ENVIRONMENT)


def export_source(root: Path, sha: str, source_root: Path) -> None:
    """Read each payload and control file from the identified commit."""
    inputs = (
        *RUNTIME_TREES,
        *(f"src/{name}" for name in PACKAGES),
        "plugins",
        "docker/plugin_artifacts.py",
        "pyproject.toml",
        "Makefile",
        "ansible.cfg",
        "install.sh",
        "LICENSE.md",
        "docs",
        "README.md",
        "debian/setup-system.1",
        "debian/setup-system.desktop",
        "THIRD_PARTY_NOTICES.md",
        "debian/control",
        "debian/setup-system.sh",
        "debian/guard.sh",
        "debian/setup-system.conf",
    )
    entries = git(root, "ls-tree", "-rz", "--full-tree", sha, "--", *inputs)
    for entry in entries.split(b"\0")[:-1]:
        header, encoded_path = entry.split(b"\t", 1)
        mode, kind, object_id = header.decode("ascii").split()
        path = Path(os.fsdecode(encoded_path))
        if path.is_absolute() or any(part.startswith(".") for part in path.parts):
            raise ValueError(f"Unexpected release path: {path}")
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Expected a regular committed file: {path}")
        content = git(root, "cat-file", "blob", object_id)
        target = source_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(int(mode[-3:], 8))


def copy_file(src: Path, dst: Path, mode: int | None = None) -> None:
    if src.is_symlink() or not src.is_file():
        raise ValueError(f"Expected a regular release file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    dst.chmod(mode if mode is not None else 0o755 if src.stat().st_mode & 0o111 else 0o644)


def prepare_package(source_root: Path, base: Path, sha: str, version: str) -> str:
    """Stage the package layout, permissions and dpkg control files."""
    metadata = (source_root / "debian/control").read_text()
    package_name = Parser().parsestr(metadata)["Package"]
    if not package_name or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package_name):
        raise ValueError("Invalid Debian package name")
    runtime = base / "usr/lib" / package_name
    documentation = base / "usr/share/doc" / package_name
    control = base / "DEBIAN"

    distribution = base / "usr/lib/python3/dist-packages"
    pairs = [(source_root / folder, runtime / folder) for folder in RUNTIME_TREES]
    pairs.extend((source_root / "src" / name, distribution / name) for name in PACKAGES)
    pairs.append((source_root / "src/setup_system_sdk", runtime / "sdk"))
    project = tomllib.loads((source_root / "pyproject.toml").read_text())["project"]
    public_version = project["version"] if version.startswith("0~local+") else version
    info = distribution / f"setup_system-{public_version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.3\nName: setup-system\nVersion: {public_version}\n"
        + f"Requires-Python: {project['requires-python']}\n"
        + "".join(f"Requires-Dist: {dep}\n" for dep in project["dependencies"])
        + "\n"
    )
    catalog = runtime / "plugin_catalog"
    subprocess.run(
        [
            sys.executable,
            str(source_root / "docker/plugin_artifacts.py"),
            "--source-root",
            str(source_root),
            "--output",
            str(catalog),
        ],
        check=True,
    )
    for wheel in catalog.glob("*.whl"):
        with ZipFile(wheel) as archive:
            (manifest_path,) = [
                name
                for name in archive.namelist()
                if re.fullmatch(r"[a-z][a-z0-9_]*/manifest\.json", name)
            ]
            manifest = json.loads(archive.read(manifest_path))
            plugin_id = manifest["id"]
            module = manifest_path.partition("/")[0]
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin_id):
                raise ValueError("Invalid protected plugin identity")
            for entry in archive.infolist():
                path = Path(entry.filename)
                if entry.is_dir() or not path.parts or path.parts[0] != module:
                    continue
                if ".." in path.parts or path.is_absolute():
                    raise ValueError("Invalid protected resource path")
                target = runtime / "plugins" / plugin_id / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
                target.chmod(0o755 if (entry.external_attr >> 16) & 0o111 else 0o644)
        wheel.chmod(0o644)
    for document in catalog.glob("*.json"):
        document.chmod(0o644)
    for source, dest in pairs:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                if dest == runtime / "sdk" and path.suffix not in {".yml", ".mk"}:
                    continue
                copy_file(path, dest / path.relative_to(source))
    copy_file(source_root / "Makefile", runtime / "Makefile")
    copy_file(source_root / "install.sh", runtime / "install.sh", 0o755)
    copy_file(source_root / "ansible.cfg", runtime / "ansible.cfg")
    copy_file(source_root / "debian/setup-system.sh", base / "usr/bin" / package_name, 0o755)
    copy_file(
        source_root / "debian/setup-system.conf",
        base / "usr/lib/tmpfiles.d" / f"{package_name}.conf",
    )
    copy_file(
        source_root / "debian/setup-system.desktop",
        base / "usr/share/applications/setup-system.desktop",
    )
    copy_file(source_root / "LICENSE.md", documentation / "copyright")
    copy_file(
        source_root / "THIRD_PARTY_NOTICES.md",
        documentation / "THIRD_PARTY_NOTICES.md",
    )
    copy_file(source_root / "README.md", documentation / "README.md")
    for article in sorted((source_root / "docs").glob("*.md")):
        copy_file(article, documentation / "docs" / article.name)
    manual = base / "usr/share/man/man1" / f"{package_name}.1.gz"
    manual.parent.mkdir(parents=True, exist_ok=True)
    with manual.open("wb") as compressed:
        subprocess.run(
            ["/usr/bin/gzip", "-n", "-9", "-c", source_root / "debian/setup-system.1"],
            stdout=compressed,
            env={"LC_ALL": "C"},
            check=True,
        )
    manual.chmod(0o644)
    for script in ("preinst", "prerm"):
        copy_file(source_root / "debian/guard.sh", control / script, 0o755)
    (control / "control").write_text(
        f"Version: {version}\nX-Setup-System-Source: {sha}\n{metadata}"
    )
    files = sorted(path for path in (base / "usr").rglob("*") if path.is_file())
    with (control / "md5sums").open("w") as checksums:
        for path in files:
            digest = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
            checksums.write(f"{digest}  {path.relative_to(base)}\n")
    (info / "METADATA").chmod(0o644)
    for name in ("control", "md5sums"):
        (control / name).chmod(0o644)
    for directory in (base, *base.rglob("*")):
        if directory.is_dir():
            directory.chmod(0o755)
    return f"{package_name}_{version}_all.deb"


def build(root: Path, output: Path, sha: str, version: str) -> Path:
    """Publish one archive from the supplied committed identity."""
    environment = dict(
        os.environ, SOURCE_DATE_EPOCH=git(root, "show", "-s", "--format=%ct", sha).decode().strip()
    )
    out = output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".setup-system-", dir=out) as temp:
        source = Path(temp) / "source"
        base = Path(temp) / "root"
        export_source(root, sha, source)
        package = prepare_package(source, base, sha, version)
        artifact = Path(temp) / package
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(base), str(artifact)],
            env=environment,
            check=True,
        )
        artifact.replace(out / package)
        return out / package


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sha = git(root, "rev-parse", "HEAD").decode().strip()
    version = options.version or f"0~local+g{sha[:12]}"
    if options.version and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        parser.error("expected a stable Debian release version such as 1.2.3")
    print(build(root, options.output, sha, version))


if __name__ == "__main__":
    main()
