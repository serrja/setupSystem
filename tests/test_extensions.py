"""Exercise offline metadata discovery, HTTPS trust and actual isolated uv environments."""

import asyncio
import base64
import hashlib
import json
import stat
import subprocess
import sys
from email.parser import Parser
from importlib import metadata, resources
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import httpx
import pytest
from packaging.requirements import Requirement

from setup_system import extensions
from setup_system.extensions import (
    CatalogEntry,
    Release,
    ReleaseDocument,
    ReleaseRecord,
    TrustPolicy,
    Wheel,
    active,
    builtins,
    install_release,
    installed,
    preview_link,
    wheel_manifest,
)
from setup_system_sdk import PLUGIN_GROUP, Manifest, PluginSettings


def descriptor(plugin, version="1.0", **changes):
    fields = dict(
        id=plugin,
        name=plugin.capitalize(),
        author="Fixture",
        kind="application",
        category="Fixtures",
        version=version,
        entry_point=f"ext_{plugin}:Backend",
        source="https://example.test/releases",
        sdk=">=1,<2",
        permissions=[],
        capabilities=[],
        dependencies=[],
        actions=[],
        settings_schema=PluginSettings.model_json_schema(),
        input_schemas={},
    )
    return fields | changes


def wheel(
    root, dist, version, module, content, *, manifest=None, requires=(), entries=None, extras=None
):
    stem = dist.replace("-", "_")
    name = f"{stem}-{version}-py3-none-any.whl"
    metadata = f"{stem}-{version}.dist-info"
    files = {
        f"{module}/__init__.py": content.encode(),
        f"{metadata}/METADATA": (
            f"Metadata-Version: 2.3\nName: {dist}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {r}\n" for r in requires)
            + "\n"
        ).encode(),
        f"{metadata}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    if manifest is not None:
        files[f"{module}/manifest.json"] = json.dumps(manifest).encode()
        files[f"{metadata}/entry_points.txt"] = (
            entries or f"[{PLUGIN_GROUP}]\n{manifest['id']} = {manifest['entry_point']}\n"
        ).encode()
    files.update(extras or {})
    records = ""
    for p, data in sorted(files.items()):
        signature = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        records += f"{p},sha256={signature},{len(data)}\n"
    record = f"{metadata}/RECORD"
    files[record] = (records + f"{record},,\n").encode()
    path = root / name
    with ZipFile(path, "w") as archive:
        for p, blob in sorted(files.items()):
            archive.writestr(ZipInfo(p), blob)
    return path


def sdk_wheels(root):
    """Repack the locked test environment to exercise the real SDK without network access."""
    sdk = resources.files("setup_system_sdk")
    archives = [
        wheel(
            root,
            "setup-system-sdk",
            "1.0",
            "setup_system_sdk",
            sdk.joinpath("__init__.py").read_text(),
            requires=["packaging", "pydantic-settings"],
            extras={
                f"setup_system_sdk/{name}": sdk.joinpath(name).read_bytes()
                for name in ("worker.py", "protocol.py")
            },
        )
    ]
    pending = ["packaging", "pydantic-settings"]
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        dist = metadata.distribution(name)
        for requirement in map(Requirement, dist.requires or []):
            if not requirement.marker or requirement.marker.evaluate():
                pending.append(requirement.name)
        saved = Parser().parsestr(dist.read_text("WHEEL"))
        tag = saved["Tag"]
        identity = next(
            str(f).split("/")[0] for f in dist.files if str(f).endswith(".dist-info/WHEEL")
        )
        path = root / (identity.removesuffix(".dist-info") + "-" + tag + ".whl")
        if not path.is_file():
            with ZipFile(path, "w") as archive:
                for item in sorted(dist.files, key=str):
                    relative = Path(item)
                    if (
                        relative.is_absolute()
                        or ".." in relative.parts
                        or "__pycache__" in relative.parts
                        or relative.name
                        in {"INSTALLER", "uv_cache.json", "direct_url.json", "REQUESTED"}
                    ):
                        continue
                    source = Path(dist.locate_file(item))
                    if source.is_file():
                        archive.writestr(ZipInfo(str(item)), source.read_bytes())
        archives.append(path)
    return archives


def fixture_release(tmp, plugin, dependency_version, version="1.0", **changes):
    manifest = descriptor(plugin, version, **changes)
    dep = wheel(
        tmp,
        "conflicting-dependency",
        dependency_version,
        "conflicting",
        f"VALUE={dependency_version!r}",
    )
    impl = wheel(
        tmp,
        f"ext-{plugin}",
        version,
        f"ext_{plugin}",
        "from conflicting import VALUE\nfrom setup_system_sdk import Backend",
        manifest=manifest,
        requires=[f"conflicting-dependency=={dependency_version}", "setup-system-sdk"],
    )
    record = ReleaseRecord(
        manifest=Manifest.model_validate(manifest),
        wheels=tuple(
            Wheel(name=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            for p in (impl, dep, *sdk_wheels(tmp))
        ),
    )
    return Release(ReleaseDocument(releases=[record]).model_dump_json().encode()), impl


def activate(release, root, *, validate_settings=None):
    return asyncio.run(
        install_release(
            release,
            policy=TrustPolicy(digests=frozenset([release.digest])),
            env_root=root / "envs",
            wheelhouse=root,
            validate_settings=validate_settings,
        )
    )


def test_offline_fixture_wheels_have_reproducible_metadata_and_digests(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    release, _ = fixture_release(first, "rebuild", "1.0")
    rebuilt, _ = fixture_release(second, "rebuild", "1.0")
    assert rebuilt.payload == release.payload
    for artifact in release.selected().wheels:
        with ZipFile(first / artifact.name) as archive:
            assert all(member.date_time == (1980, 1, 1, 0, 0, 0) for member in archive.infolist())
        assert (first / artifact.name).read_bytes() == (second / artifact.name).read_bytes()


def test_conflicting_distributions_run_outside_the_host_and_failed_migration_keeps_old(tmp_path):
    before = list(sys.path)
    first, _ = fixture_release(tmp_path, "one", "1.0")
    second, _ = fixture_release(tmp_path, "two", "2.0")
    workers = [activate(release, tmp_path) for release in (first, second)]
    values = [
        subprocess.check_output(
            [str(w.python), "-I", "-c", "import conflicting; print(conflicting.VALUE)"],
            cwd=tmp_path,
            text=True,
        ).strip()
        for w in workers
    ]
    assert values == ["1.0", "2.0"]
    assert workers[0].resources != workers[1].resources
    assert active(tmp_path / "envs", "one") == workers[0]
    assert activate(first, tmp_path) == workers[0]
    assert {entry.manifest.id for entry in installed(tmp_path / "envs")} == {"one", "two"}
    changed, _ = fixture_release(tmp_path, "one", "3.0", version="2.0", settings_version=2)
    with pytest.raises(ValueError, match="migration"):
        activate(changed, tmp_path)

    def failed_migration(_):
        raise ValueError("invalid saved settings")

    with pytest.raises(ValueError, match="invalid saved"):
        activate(changed, tmp_path, validate_settings=failed_migration)
    assert active(tmp_path / "envs", "one") == workers[0]
    updated = activate(changed, tmp_path, validate_settings=lambda state: None)
    assert updated.digest == changed.digest and workers[0].python.is_file()
    with pytest.raises(ValueError, match="downgrade"):
        activate(first, tmp_path)
    assert sys.path == before
    assert "conflicting" not in sys.modules

    from setup_system.extension_lifecycle import toggle

    toggle(tmp_path / "envs", "one", enable=False)
    assert active(tmp_path / "envs", "one") is None
    assert {e.manifest.id: e.disabled for e in installed(tmp_path / "envs")} == {
        "one": True,
        "two": False,
    }
    with pytest.raises(ValueError, match="disabled"):
        activate(changed, tmp_path)
    toggle(tmp_path / "envs", "one", enable=True)
    assert active(tmp_path / "envs", "one") == updated


def test_preview_is_data_and_bad_entry_points_remain_local(tmp_path):
    catalog = tmp_path / "plugin_catalog"
    catalog.mkdir()
    marker = tmp_path / "ran"
    m = descriptor("extra")
    ok = wheel(
        catalog,
        "external-distribution",
        "1.0",
        "ext_extra",
        f"open({str(marker)!r},'w').close()\nclass Backend: pass",
        manifest=m,
    )
    assert builtins(tmp_path)[0].manifest.id == "extra"
    assert not marker.exists()
    wrong = wheel(
        catalog,
        "external-other",
        "1.0",
        "ext_extra",
        "",
        manifest=m,
        entries=f"[{PLUGIN_GROUP}]\nextra = wrong:Backend\n",
    )
    bad_sdk = wheel(catalog, "old-api", "1.0", "ext_extra", "", manifest=m | {"sdk": ">=5"})
    duplicate = wheel(catalog, "dupe", "1.0", "ext_extra", "", manifest=m)
    results = {e.source.name: e for e in builtins(tmp_path)}
    assert all(isinstance(e, CatalogEntry) and e.reason for e in results.values())
    assert results[ok.name].reason == results[duplicate.name].reason == "Duplicate plugin identity"
    assert results[wrong.name].manifest is None and results[bad_sdk.name].manifest is None
    assert not marker.exists()


def test_trust_closure_paths_and_unavailable_backend(tmp_path):
    release, main = fixture_release(tmp_path, "one", "1.0")
    with pytest.raises(ValueError, match="trust"):
        asyncio.run(install_release(release, policy=TrustPolicy(), env_root=tmp_path / "envs"))
    assert not (tmp_path / "envs").exists()
    dep = next(tmp_path.glob("conflicting*.whl"))
    dep.write_bytes(b"wrong wheel")
    with pytest.raises(ValueError, match="digest"):
        activate(release, tmp_path)
    assert active(tmp_path / "envs", "one") is None
    assert not list((tmp_path / "envs/one").iterdir())
    changed = wheel(
        tmp_path,
        "bad",
        "1.0",
        "ext_one",
        "",
        manifest=descriptor("one"),
        extras={"../host.py": b"bad"},
    )
    with pytest.raises(ValueError, match="Unsafe wheel"):
        wheel_manifest(changed.name, changed.read_bytes())
    symlink = ZipInfo("escape")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(main, "a") as archive:
        archive.writestr(symlink, b"/etc/example")
    with pytest.raises(ValueError, match="Unsafe wheel"):
        wheel_manifest(main.name, main.read_bytes())
    absent = wheel(tmp_path, "absent", "1.0", "ext_one", "pass", manifest=descriptor("one"))
    with pytest.raises(ValueError, match="verify"):
        activate(Release(absent.read_bytes(), direct_name=absent.name), tmp_path)
    assert active(tmp_path / "envs", "one") is None


def test_preview_follows_only_permitted_redirects_and_discards_cookies(tmp_path, monkeypatch):
    release, _ = fixture_release(tmp_path, "linked", "1.0")
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/from":
            return httpx.Response(
                302,
                headers={
                    "location": "https://cdn.example.test/release.json",
                    "set-cookie": "secret=private; Domain=example.test; Path=/",
                },
            )
        if request.url.path == "/elsewhere":
            return httpx.Response(302, headers={"location": "https://other.test/ext"})
        return httpx.Response(200, stream=httpx.ByteStream(release.payload))

    original = httpx.AsyncClient
    monkeypatch.setattr(
        extensions.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    policy = TrustPolicy(hosts=frozenset(["example.test", "cdn.example.test"]))
    preview = asyncio.run(preview_link("https://example.test/from", policy))
    assert preview.digest == release.digest and preview.manifest.id == "linked"
    assert all("cookie" not in r.headers and "authorization" not in r.headers for r in requests)
    with pytest.raises(ValueError, match="approved"):
        asyncio.run(preview_link("https://example.test/elsewhere", policy))
    assert requests[-1].url.host == "example.test"


def test_release_selects_latest_compatible_and_refuses_identity_ambiguity(tmp_path):
    release, _ = fixture_release(tmp_path, "selected", "1.0")
    record = json.loads(release.payload)["releases"][0]

    def version(version, **changes):
        primary = record["wheels"][0]
        return record | {
            "manifest": record["manifest"] | {"version": version} | changes,
            "wheels": [
                primary | {"name": primary["name"].replace("-1.0-", f"-{version}-")},
                *record["wheels"][1:],
            ],
        }

    data = {"format": 1, "releases": [version("2.0"), version("2.1b1"), version("3", sdk=">=10")]}
    assert Release(json.dumps(data).encode()).manifest.version == "2.0"
    data["releases"].append(version("1.2", id="foreign"))
    with pytest.raises(ValueError, match="unambiguous"):
        Release(json.dumps(data).encode()).selected()


def test_direct_link_rejects_the_wrong_interpreter_and_unsafe_generated_commands(tmp_path):
    release, _ = fixture_release(tmp_path, "example", "1.0")
    source = release.selected()
    primary = source.wheels[0]
    payload = (tmp_path / primary.name).read_bytes()
    with pytest.raises(ValueError, match="compatible"):
        Release(
            payload, direct_name=primary.name.replace("py3-none-any", "cp37-cp37-win32")
        ).selected()
    dependency = wheel(
        tmp_path,
        "shadow",
        "1.0",
        "shadow",
        "",
        extras={
            "shadow-1.0.dist-info/entry_points.txt": b"[console_scripts]\npython=shadow:main\n"
        },
    )
    data = dependency.read_bytes()
    stamp = Wheel(name=dependency.name, sha256=hashlib.sha256(data).hexdigest())
    stage = tmp_path / "verify"
    stage.mkdir()
    with pytest.raises(ValueError, match="unsafe or overlapping command"):
        extensions._verify_layout([(primary, payload), (stamp, data)], stage, source)


@pytest.mark.parametrize("limit", ["MAX_FILES", "EXPANDED_BYTES"])
def test_entire_wheel_closure_is_bounded_before_staging(tmp_path, monkeypatch, limit):
    manifest = descriptor("bounded")
    plugin = wheel(tmp_path, "bounded", "1.0", "ext_bounded", "", manifest=manifest)
    dependency = wheel(tmp_path, "dependency", "1.0", "dependency", "")
    contents = []
    maxima = []
    for archive in (plugin, dependency):
        data = archive.read_bytes()
        contents.append((Wheel(name=archive.name, sha256=hashlib.sha256(data).hexdigest()), data))
        with ZipFile(archive) as opened:
            entries = opened.infolist()
            maxima.append(
                len(entries) if limit == "MAX_FILES" else sum(e.file_size for e in entries)
            )
    monkeypatch.setattr(extensions, limit, max(maxima))
    selected = ReleaseRecord(
        manifest=Manifest.model_validate(manifest), wheels=tuple(w for w, _ in contents)
    )
    stage = tmp_path / "staging"
    stage.mkdir()
    with pytest.raises(ValueError, match="aggregate expansion"):
        extensions._verify_layout(contents, stage, selected)
    assert not list(stage.iterdir())


def test_debian_dependency_readback_requires_approved_origin_version_and_extras(monkeypatch):
    from types import SimpleNamespace

    def dist(requirements, *, version="1", root=None):
        return SimpleNamespace(
            requires=requirements,
            version=version,
            locate_file=lambda _: root or Path(sys.prefix),
        )

    installed = {
        "app": dist(["client[auth]>=1", "missing; python_version < '2'"]),
        "client": dist(["token; extra == 'auth'"]),
        "token": dist(["client"]),
    }
    monkeypatch.setattr(extensions, "distribution", installed.__getitem__)
    extensions.verify_debian_dependencies(["app", "app"])
    installed["token"] = dist([], root=Path("/unapproved-environment"))
    with pytest.raises(ValueError, match="unapproved Python"):
        extensions.verify_debian_dependencies(["app"])
    installed["token"] = dist([], root=Path("/usr/lib/python3/dist-packages"))
    extensions.verify_debian_dependencies(["app"])
    installed["client"] = dist([], version="0.5")
    with pytest.raises(ValueError, match="reviewed requirement"):
        extensions.verify_debian_dependencies(["app"])
    installed["app"] = dist(["client @ https://example.invalid/client"])
    with pytest.raises(ValueError, match="reviewed requirement"):
        extensions.verify_debian_dependencies(["app"])
