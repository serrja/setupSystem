"""Assemble a Debian archive from reviewed Python wheels and release descriptors."""

import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Annotated

import typer
from packaging.utils import canonicalize_name, parse_wheel_filename
from setup_system_sdk.manifests import Manifest
from setup_system_sdk.native_execution import NativeFactProvider, TrustedExecution

PACKAGE = Path(__file__).resolve().parent
RESOURCES = PACKAGE.parent / "src/setup_plugin_debian/resources"
RUNTIME = Path("usr/lib/setup-system")


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack(wheel: Path, destination: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for entry in archive.infolist():
            rel = PurePosixPath(entry.filename)
            if (
                rel.is_absolute()
                or ".." in rel.parts
                or "\\" in entry.filename
                or any(n.endswith(".data") for n in rel.parts)
                or stat.S_ISLNK(entry.external_attr >> 16)
            ):
                raise ValueError("Unsupported wheel member")
            target = destination.joinpath(*rel.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as content, target.open("xb") as stream:
                shutil.copyfileobj(content, stream)
            target.chmod(0o644)


def stage(
    base: Path,
    host: Path,
    sdk: Path,
    releases: list[Path],
    runtime_assets: Path,
    version: str,
    source: str,
) -> None:
    runtime = base / RUNTIME
    runtime.mkdir(parents=True)
    for wheel, expected in ((host, "setup-system"), (sdk, "setup-system-sdk")):
        wheel_name, _, _, _ = parse_wheel_filename(wheel.name)
        if wheel_name != expected:
            raise ValueError(f"Expected wheel for {expected}")
    if not runtime_assets.is_dir():
        raise ValueError("Missing reviewed uv runtime closure")
    shutil.copytree(runtime_assets, runtime / "runtime")
    (runtime / "runtime/source").write_text(source + "\n")
    for arch in ("amd64", "arm64"):
        uv = runtime / "runtime/uv" / arch / "uv"
        if (uv.parent / "uv.sha256").read_text() != f"{sha(uv)}  uv\n":
            raise ValueError("Bundled uv executable differs from its reviewed digest")
        available = {sha(wheel) for wheel in (runtime / "runtime/wheelhouse" / arch).glob("*.whl")}
        for role in ("host", "native"):
            requirements = runtime / "runtime" / f"{role}-{arch}.txt"
            if not requirements.is_file() or not requirements.read_text().strip():
                raise ValueError("Missing locked runtime requirements")
            for line in requirements.read_text().splitlines():
                match = re.fullmatch(
                    r"[a-z0-9][a-z0-9._-]*==[a-zA-Z0-9.+!-]+ --hash=sha256:([a-f0-9]{64})",
                    line,
                )
                if not match:
                    raise ValueError("Invalid locked runtime requirement")
                digest = match.group(1)
                if digest not in available:
                    raise ValueError("Runtime requirement has no verified wheel")
    shutil.copytree(RESOURCES, runtime, dirs_exist_ok=True)
    catalog = runtime / "plugin_catalog"
    catalog.mkdir()
    installed = set()
    records: list[tuple[Manifest, TrustedExecution]] = []
    facts: dict[str, NativeFactProvider] = {}
    executor_records = base / "etc/setup-system/executors"
    executor_records.mkdir(parents=True)
    grants = tomllib.loads((PACKAGE / "executors.toml").read_text())["handlers"]
    for release in releases:
        raw = release.read_bytes()
        variants = json.loads(raw)["releases"]
        record = variants[0]
        if any(
            v["manifest"] != record["manifest"] or v["wheels"][0] != record["wheels"][0]
            for v in variants
        ):
            raise ValueError("Package variants have different primary wheels")
        manifest = Manifest.model_validate(record["manifest"])
        if manifest.id in installed:
            raise ValueError("Repeated plugin release")
        installed.add(manifest.id)
        for descriptor in (w for variant in variants for w in variant["wheels"]):
            filename = descriptor["name"]
            if filename != Path(filename).name:
                raise ValueError("Invalid wheel name")
            parse_wheel_filename(filename)
            src = release.parent / filename
            if sha(src) != descriptor["sha256"]:
                raise ValueError("Reviewed wheel digest differs")
            dest = catalog / filename
            if dest.exists() and sha(src) != sha(dest):
                raise ValueError("Conflicting release wheels")
            if not dest.exists():
                shutil.copyfile(src, dest)
        primary = catalog / record["wheels"][0]["name"]
        dist, rel_version, _, _ = parse_wheel_filename(primary.name)
        if str(rel_version) != manifest.version:
            raise ValueError("Wheel version differs from manifest")
        package = manifest.entry_point.split(":")[0].split(".")[0]
        plugin_root = runtime / "plugins" / manifest.id
        unpack(primary, plugin_root)
        packaged = Manifest.model_validate_json(
            (plugin_root / package / "manifest.json").read_bytes()
        )
        if packaged != manifest:
            raise ValueError("Primary wheel differs from release descriptor")
        if manifest.id == "debian":
            if dist != canonicalize_name("setup-system-plugin-debian"):
                raise ValueError("Unexpected Debian provider distribution")
            facts = {
                cap.id: NativeFactProvider(
                    plugin_id=manifest.id,
                    version=manifest.version,
                    release_digest=hashlib.sha256(raw).hexdigest(),
                    contract_version=cap.version,
                )
                for cap in manifest.provides
                if cap.id in {"system.platform", "system.packages"}
            }
        (catalog / (manifest.id + ".json")).write_bytes(raw)
        handlers = grants.get(manifest.id, ())
        if handlers:
            expected = "setup_plugin_" + manifest.id.replace("-", "_")
            if (
                dist != canonicalize_name("setup-system-plugin-" + manifest.id)
                or package != expected
            ):
                raise ValueError(
                    "Native grant requires its reviewed distribution and import package"
                )
            if manifest.id != "debian" and not set(handlers) <= {
                a.id for a in manifest.actions if {"root", "sudo", "su"}.intersection(a.permissions)
            }:
                raise ValueError("The release does not declare its reviewed native handlers")
            launcher = runtime / "libexec/launcher"
            digest = hashlib.sha256(raw).hexdigest()
            protected_files = {
                (Path("/") / p.relative_to(base)): sha(p)
                for p in sorted((plugin_root / package).rglob("*"))
                if p.is_file()
            }
            runtime_contract = runtime / "runtime"
            for path in (
                runtime_contract / "source",
                *(runtime_contract.glob("*.txt")),
                *(runtime_contract.glob("uv/*/uv")),
                *(runtime_contract.glob("uv/*/uv.sha256")),
            ):
                protected_files[Path("/") / path.relative_to(base)] = sha(path)
            trust = TrustedExecution(
                plugin_id=manifest.id,
                version=manifest.version,
                release_digest=digest,
                allowed_handlers=frozenset(handlers),
                launcher=Path("/") / launcher.relative_to(base),
                launcher_sha256=sha(launcher),
                resources=protected_files,
            )
            records.append((manifest, trust))
    if "debian" not in installed:
        raise ValueError("Select the reviewed Debian provider release")
    for manifest, trust in records:
        required = {
            action.id: {r.id: facts[r.id] for r in action.requires}
            for action in manifest.actions
            if action.id in trust.allowed_handlers
        }
        record = trust.model_copy(update={"fact_providers": required})
        fields = record.model_dump(mode="json")
        fields["allowed_handlers"] = sorted(record.allowed_handlers)
        (executor_records / (manifest.id + ".json")).write_text(json.dumps(fields, sort_keys=True))
    for name, target in [
        ("setup-system.sh", "usr/bin/setup-system"),
        ("setup-system.conf", "usr/lib/tmpfiles.d/setup-system.conf"),
        ("setup-system.desktop", "usr/share/applications/setup-system.desktop"),
    ]:
        path = base / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PACKAGE / name, path)
    (base / "usr/bin/setup-system").chmod(0o755)
    docs = base / "usr/share/doc/setup-system"
    docs.mkdir(parents=True)
    root = PACKAGE.parents[2]
    for src, dest in (
        ("LICENSE.md", "copyright"),
        ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
        ("README.md", "README.md"),
    ):
        shutil.copyfile(root / src, docs / dest)
    shutil.copytree(root / "docs", docs / "docs")
    for plugin_docs in sorted(PACKAGE.parent.parent.glob("*/docs")):
        shutil.copytree(plugin_docs, docs / "plugins" / plugin_docs.parent.name / "docs")
    manual = base / "usr/share/man/man1/setup-system.1.gz"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(gzip.compress((PACKAGE / "setup-system.1").read_bytes(), mtime=0))
    control = base / "DEBIAN"
    control.mkdir()
    description = (PACKAGE / "control").read_text()
    if Parser().parsestr(description)["Package"] != "setup-system":
        raise ValueError("Unexpected control identity")
    (control / "control").write_text(
        f"Version: {version}\nX-Setup-System-Source: {source}\n{description}"
    )
    for name in ("preinst", "prerm"):
        shutil.copyfile(PACKAGE / "guard.sh", control / name)
        (control / name).chmod(0o755)
    shutil.copyfile(PACKAGE / "postinst", control / "postinst")
    (control / "postinst").chmod(0o755)
    shutil.copyfile(PACKAGE / "postrm", control / "postrm")
    (control / "postrm").chmod(0o755)
    for path in base.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file() and path.parent != control:
            mode = (
                0o755
                if path.is_relative_to(runtime / "libexec")
                or path == base / "usr/bin/setup-system"
                or (path.name == "uv" and path.parent.parent == runtime / "runtime/uv")
                else 0o644
            )
            path.chmod(mode)
    with (control / "md5sums").open("w") as sums:
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.is_relative_to(control):
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(
                        stream, lambda: hashlib.md5(usedforsecurity=False)
                    ).hexdigest()
                sums.write(f"{digest}  {path.relative_to(base)}\n")


def main(
    host_wheel: Annotated[Path, typer.Option("--host-wheel")],
    sdk_wheel: Annotated[Path, typer.Option("--sdk-wheel")],
    release: Annotated[list[Path], typer.Option("--release")],
    runtime_assets: Annotated[Path, typer.Option("--runtime-assets")],
    output: Annotated[Path, typer.Option("--output")],
    version: Annotated[str, typer.Option("--version")],
    source: Annotated[str, typer.Option("--source", help="Reviewed source commit")],
) -> None:
    """Assemble the attested Debian package from verified inputs."""
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Expected a full source commit SHA")
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+~:-]*", version):
        raise ValueError("Invalid Debian version")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        base = Path(temporary) / "root"
        stage(
            base,
            host_wheel,
            sdk_wheel,
            release,
            runtime_assets,
            version,
            source,
        )
        artifact = Path(temporary) / f"setup-system_{version}_all.deb"
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "--build", str(base), str(artifact)], check=True
        )
        result = output / artifact.name
        os.link(artifact, result)
        print(result)


if __name__ == "__main__":
    typer.run(main)
