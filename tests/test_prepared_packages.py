"""Verify actual staged Debian archives, signed stable metadata and current target identity."""

import hashlib
import json
import runpy
import subprocess
import tomllib
from pathlib import Path

import pytest

from setup_system.infrastructure.upstream_asset import github_deb

ROOT = Path(__file__).resolve().parents[1]


def archive(tmp_path, name, version, arch):
    package = tmp_path / "fixture"
    (package / "DEBIAN").mkdir(parents=True)
    (package / "DEBIAN").chmod(0o755)
    control = (
        f"Package: {name}\nVersion: {version}\nArchitecture: {arch}\n"
        "Maintainer: Test <test@invalid.tld>\nDescription: fixture\n"
    )
    (package / "DEBIAN/control").write_text(control)
    result = tmp_path / "vendor.deb"
    subprocess.run(
        ["/usr/bin/dpkg-deb", "--root-owner-group", "--build", str(package), str(result)],
        check=True,
        capture_output=True,
    )
    return result


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_deb_root_checks_actual_headers_and_digest(tmp_path, arch):
    validate = runpy.run_path(str(ROOT / "ansible/files/apt_apply.py"))["validate_deb"]
    deb = archive(tmp_path, "happ", "1.2-123", arch)
    release = {
        "checksum": "sha256:" + hashlib.sha256(deb.read_bytes()).hexdigest(),
        "version": "1.2",
    }
    assert validate(deb, "happ", release, arch) == "1.2-123"
    for key, details, target in [
        ("happ", dict(release, checksum="sha256:" + "0" * 64), arch),
        ("happ", dict(release, version="2.0"), arch),
        ("unrelated", release, arch),
        ("happ", release, "amd64" if arch == "arm64" else "arm64"),
    ]:
        with pytest.raises(ValueError, match=r"changed|identity"):
            validate(deb, key, details, target)


@pytest.mark.parametrize(
    "kind",
    ["draft", "prerelease", "duplicate", "digest", "redirect", "missing", "unsupported", "bad-tag"],
)
def test_only_current_official_stable_asset_metadata_is_accepted(kind):
    entry = tomllib.loads((ROOT / "config/workspace/applications.toml").read_text())["happ"]
    filename = entry["asset"]["amd64"]
    url = "https://github.com/" + entry["github"] + "/releases/download/1.2/" + filename
    asset = {"name": filename, "browser_download_url": url, "digest": "sha256:" + "0" * 64}
    info = {
        "draft": False,
        "prerelease": False,
        "tag_name": "1.2",
        "published_at": "2026-01-01",
        "assets": [asset],
    }
    assert github_deb(info, entry, "amd64")["url"] == url
    arch = "amd64"
    if kind in {"draft", "prerelease"}:
        info[kind] = True
    elif kind == "duplicate":
        info["assets"].append(asset)
    elif kind == "missing":
        info["assets"] = []
    elif kind == "digest":
        asset["digest"] = None
    elif kind == "redirect":
        asset["browser_download_url"] = "https://example.invalid/fixture.deb"
    elif kind == "bad-tag":
        info["tag_name"] = "nightly"
    else:
        arch = "i386"
    with pytest.raises(ValueError):
        github_deb(info, entry, arch)


def test_root_rejects_external_or_unprotected_transaction_paths(tmp_path):
    reviewed = runpy.run_path(str(ROOT / "ansible/files/apt_apply.py"))["reviewed"]
    (tmp_path / "approved.json").write_text(json.dumps([]))
    with pytest.raises(ValueError, match="private installed"):
        reviewed(tmp_path)
    assert list(tmp_path.iterdir()) == [tmp_path / "approved.json"]


@pytest.mark.parametrize("change", ["newer", "removed", "stale-release", "active"])
def test_happ_change_after_plan_stops_before_apt(tmp_path, monkeypatch, change):
    from setup_system.infrastructure import happ_facts

    module = runpy.run_path(str(ROOT / "ansible/files/apt_apply.py"))
    apply = module["run"]
    globals_ = apply.__globals__
    entry = tomllib.loads((ROOT / "config/workspace/applications.toml").read_text())["happ"]
    name = entry["asset"]["amd64"]
    deb = tmp_path / name
    archive(tmp_path, "happ", "2.0-10", "amd64").rename(deb)
    digest = "sha256:" + hashlib.sha256(deb.read_bytes()).hexdigest()
    release = {
        "draft": False,
        "prerelease": False,
        "tag_name": "2.0",
        "published_at": "2026-01-01",
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://github.com/{entry['github']}/releases/download/2.0/{name}",
                "digest": digest,
            }
        ],
    }
    unit = {
        "LoadState": "loaded",
        "ActiveState": "inactive",
        "FragmentPath": "/etc/systemd/system/happd.service",
    }
    record = dict(github_deb(release, entry, "amd64"), before={"unit": unit, "installed": "1.0-1"})
    (tmp_path / "upstream.json").write_text(json.dumps({"happ": record}))
    (tmp_path / "transaction.json").write_text(
        json.dumps(["--no-remove", "--no-install-recommends", "install", "--", str(deb)])
    )
    (tmp_path / "approved.json").write_text(
        json.dumps(["Inst happ [1.0-1] (2.0-10 local-deb [amd64])"])
    )
    if change == "stale-release":
        release["assets"][0]["digest"] = "sha256:" + "0" * 64

    def release_metadata(url, policy):
        assert url == f"https://api.github.com/repos/{entry['github']}/releases/latest"
        assert policy.allowed_hosts == {"api.github.com"} and not policy.follow_redirects
        return json.dumps(release).encode()

    monkeypatch.setitem(globals_, "ROOT", ROOT)
    monkeypatch.setitem(globals_, "reviewed", lambda stage: None)
    monkeypatch.setitem(globals_, "protected", lambda path, **kw: None)
    monkeypatch.setitem(globals_, "current_target", lambda: ("amd64", True))
    monkeypatch.setitem(globals_, "read_https", release_metadata)
    monkeypatch.setitem(
        globals_, "defer_services", lambda path: pytest.fail("APT reached a stale vendor archive")
    )
    monkeypatch.setattr(
        happ_facts,
        "unit_info",
        lambda: dict(unit, ActiveState="active") if change == "active" else unit,
    )
    monkeypatch.setattr(
        happ_facts,
        "installed_version",
        lambda: None if change == "removed" else "1.3-1" if change == "newer" else "1.0-1",
    )
    with pytest.raises(ValueError, match=r"changed|differs"):
        apply(tmp_path)


def test_masked_vendor_service_preserves_administrator_choice(tmp_path, monkeypatch):
    from setup_system.infrastructure import happ_facts

    mask = tmp_path / "happd.service"
    mask.symlink_to("/dev/null")
    monkeypatch.setattr(happ_facts, "UNIT_PATHS", (mask,))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: pytest.fail("Tried to change or unmask a protected service"),
    )
    with pytest.raises(ValueError, match="mask"):
        happ_facts.unit_info()
    assert mask.readlink() == Path("/dev/null")
