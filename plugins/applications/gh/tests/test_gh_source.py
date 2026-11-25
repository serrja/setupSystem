"""Check the publisher keyring and package-specific APT policy without network access."""

import hashlib
import subprocess

import pytest
from setup_plugin_gh import Plugin

from setup_system_sdk import apt

FOLDER = Plugin.package_folder


def test_inline_keyring_matches_the_publisher_and_remains_scoped(tmp_path):
    source = (FOLDER / "sources/github-cli.sources").read_text()
    header, key = source.split("Signed-By:\n", 1)
    assert header.splitlines() == [
        "Types: deb",
        "URIs: https://cli.github.com/packages",
        "Suites: stable",
        "Components: main",
        "Architectures: amd64 arm64",
    ]
    armored = (
        "\n".join("" if line == " ." else line.removeprefix(" ") for line in key.splitlines())
        + "\n"
    )
    assert (
        hashlib.sha256(armored.encode()).hexdigest()
        == "cec6e9ed82d3949ca5f4428cc968b41ef5e7416cb3653cdfc2a421977663bbfd"
    )
    key_file = tmp_path / "publisher.asc"
    key_file.write_text(armored)
    snapshot = subprocess.run(
        [
            "/usr/bin/gpg",
            "--batch",
            "--no-options",
            "--homedir",
            str(tmp_path),
            "--with-colons",
            "--show-keys",
            str(key_file),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    lines = snapshot.stdout.splitlines()
    primary = {lines[i + 1].split(":")[9] for i, row in enumerate(lines) if row.startswith("pub:")}
    assert primary == {
        "2C6106201985B60E6C7AC87323F3D4EA75716059",
        "7F38BBB59D064DBCB3D84D725612B36462313325",
    }


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_apt_prefers_only_the_vendors_gh_and_debian_dependencies(tmp_path, arch):
    config = tmp_path / "config/base"
    config.mkdir(parents=True)
    repo = tmp_path / apt.SOURCE
    repo.write_text(
        "Types: deb\nURIs: https://debian.fixture.invalid/debian\nSuites: trixie\n"
        "Components: main\nArchitectures: " + arch + "\n"
    )
    (config / "apt.conf").write_bytes((FOLDER.parents[4] / "config/base/apt.conf").read_bytes())
    stage = tmp_path / "stage"
    lists = stage / "lists"
    lists.mkdir(parents=True)
    status = stage / "status"
    status.write_text(
        f"Package: gh\nStatus: install ok installed\nArchitecture: {arch}\n"
        "Version: 7\nDescription: Previously installed distribution client\n\n"
    )
    for prefix, suite, origin, records in [
        (
            "cli.github.com_packages",
            "stable",
            "GitHub CLI",
            [("gh", "8"), ("libglib2.0-bin", "99")],
        ),
        (
            "debian.fixture.invalid_debian",
            "trixie",
            "Debian",
            [("gh", "9"), ("libglib2.0-bin", "1")],
        ),
    ]:
        basename = f"{prefix}_dists_{suite}"
        (lists / (basename + "_Release")).write_text(
            f"Origin: {origin}\nSuite: {suite}\nCodename: {suite}\n"
            f"Architectures: {arch}\nComponents: main\n"
        )
        (lists / (basename + f"_main_binary-{arch}_Packages")).write_text(
            "".join(
                f"Package: {name}\nVersion: {version}\nArchitecture: {arch}\n"
                f"Filename: pool/{name}.deb\nSize: 1\nDescription: Fixture\n\n"
                for name, version in records
            )
        )
    policy = apt.Packages.model_validate_json((FOLDER / "apt.json").read_bytes())
    assert policy.vendor_source
    opts = apt.options(tmp_path, stage, FOLDER, policy)
    base = [
        "/usr/bin/apt-cache",
        *opts,
        "-o",
        f"Dir::State::status={status}",
        "-o",
        f"APT::Architecture={arch}",
        "-o",
        "Debug::NoLocking=true",
        "policy",
    ]
    index = apt.query([*base, "gh"], tmp_path, stage=stage)
    assert "Installed: 7\n" in index and "Candidate: 8\n" in index and "8 990\n" in index
    assert "Candidate: 1\n" in apt.query([*base, "libglib2.0-bin"], tmp_path, stage=stage)
    solve = apt.query(
        ["/usr/bin/apt-get", *base[1:-1], "--simulate", "--no-remove", "install", "--", "gh"],
        tmp_path,
        stage=stage,
    )
    assert apt.closure(solve) == {"gh": ("7", "8")}
