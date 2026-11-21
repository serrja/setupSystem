"""Synthetic mount, filesystem and clock observations never invoke host setup."""

from __future__ import annotations

import errno
import json
import os
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from setup_system.domain.system_facts import ClockSnapshot, EncryptionState, InspectionPaths
from setup_system.infrastructure import clock, encryption, inspection, inspection_commands


def timezone_bytes(offset: int = 0) -> bytes:
    return (
        b"TZif\0"
        + b"\0" * 15
        + struct.pack(">6l", 0, 0, 0, 0, 1, 4)
        + struct.pack(">lBB", offset, 0, 0)
        + b"UTC\0"
    )


@dataclass
class Queries:
    home: Path
    root_device: str = "/dev/root"
    home_device: str = "/dev/root"
    home_filesystem: str = "ext4"
    root_filesystem: str = "ext4"
    root_tree: object = field(
        default_factory=lambda: [{"name": "root", "type": "part", "fstype": "ext4"}]
    )
    home_tree: object = field(
        default_factory=lambda: [{"name": "home", "type": "part", "fstype": "ext4"}]
    )
    clock: str = "Timezone=UTC\nNTP=yes\nNTPSynchronized=no\nLocalRTC=no\nCanNTP=yes\n"
    attributes: str = "--------------e-------"
    providers: tuple[str, ...] = ()
    fscrypt_status: str | None = "Unlocked: Yes\n"
    calls: list[list[str]] = field(default_factory=list)
    failed_command: str | None = None

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        command = Path(argv[0]).name
        if command == self.failed_command:
            raise OSError("fixture private diagnostic must not enter observations")
        if command == "findmnt":
            path = argv[argv.index("--target") + 1]
            assert path in {"/", str(self.home)}
            data = {
                "source": self.root_device if path == "/" else self.home_device,
                "fstype": self.root_filesystem if path == "/" else self.home_filesystem,
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps({"filesystems": [data]}), "")
        if command == "lsblk":
            assert "--inverse" in argv and "--tree" in argv and argv[-2] == "--"
            tree = (
                self.root_tree if argv[-1] == self.root_device.partition("[")[0] else self.home_tree
            )
            return subprocess.CompletedProcess(argv, 0, json.dumps({"blockdevices": tree}), "")
        if command == "lsattr":
            assert argv == ["/usr/bin/lsattr", "-d", "--", str(self.home)]
            return subprocess.CompletedProcess(argv, 0, self.attributes + " " + str(self.home), "")
        if command == "fscrypt":
            assert argv == ["/usr/bin/fscrypt", "status", str(self.home)]
            return subprocess.CompletedProcess(
                argv, 0 if self.fscrypt_status is not None else 1, self.fscrypt_status or "", ""
            )
        if command == "timedatectl":
            assert argv[1] == "show" and not any("set-" in item for item in argv)
            return subprocess.CompletedProcess(argv, 0, self.clock, "")
        if command == "dpkg-query":
            assert argv[-1] in inspection_commands.TIME_PROVIDERS
            present = argv[-1] in self.providers
            return subprocess.CompletedProcess(
                argv, 0 if present else 1, "install ok installed" if present else "", ""
            )
        raise AssertionError(f"Unexpected inspection command {command}")


@pytest.fixture
def context(tmp_path: Path) -> tuple[Path, InspectionPaths, Queries]:
    home = tmp_path / "home with spaces"
    home.mkdir()
    zones = tmp_path / "zoneinfo"
    (zones / "Region").mkdir(parents=True)
    (zones / "UTC").write_bytes(timezone_bytes())
    (zones / "Region/City").write_bytes(timezone_bytes())
    timezone = tmp_path / "timezone"
    timezone.write_text("UTC\n")
    localtime = tmp_path / "localtime"
    localtime.symlink_to(zones / "UTC")
    return (
        home,
        InspectionPaths(zoneinfo=zones, timezone=timezone, localtime=localtime),
        Queries(home),
    )


def encrypted_tree() -> list[dict[str, object]]:
    return [
        {
            "name": "vg-root",
            "type": "lvm",
            "fstype": "ext4",
            "children": [
                {
                    "name": "private-volume-label",
                    "type": "crypt",
                    "fstype": "LVM2_member",
                    "children": [{"name": "partition", "type": "part", "fstype": "crypto_LUKS"}],
                }
            ],
        }
    ]


def test_encrypted_lvm_ancestry_is_detected_without_exposing_device_labels(context) -> None:
    home, paths, query = context
    query.root_device = query.home_device = "/dev/mapper/private-device-label"
    query.root_tree = encrypted_tree()
    before = {p: p.read_bytes() for p in paths.zoneinfo.rglob("*") if p.is_file()}
    observed = inspection.inspect_system(home, run=query, paths=paths)
    assert (
        observed.encryption.root.state
        == observed.encryption.home.state
        == EncryptionState.ENCRYPTED
    )
    assert observed.encryption.root.layers == ("LUKS", "dm-crypt")
    assert "private-volume-label" not in repr(observed) and "private-device-label" not in repr(
        observed
    )
    assert {p: p.read_bytes() for p in before} == before
    assert list(home.iterdir()) == []


def test_separate_plain_home_does_not_inherit_root_encryption(context) -> None:
    home, paths, query = context
    query.root_tree = encrypted_tree()
    query.home_device = "/dev/separate-home"
    observed = inspection.inspect_system(home, run=query, paths=paths)
    assert observed.encryption.root.state == EncryptionState.ENCRYPTED
    assert observed.encryption.home.state == EncryptionState.UNKNOWN
    assert not observed.encryption.home.layers


def test_ecryptfs_and_fscrypt_are_independent_of_block_crypto(context) -> None:
    home, paths, query = context
    query.home_filesystem = "ecryptfs"
    ecryptfs = inspection.inspect_system(home, run=query, paths=paths)
    assert ecryptfs.encryption.home.layers == ("eCryptfs",)
    query.home_filesystem = "ext4"
    query.attributes = "--------------eE------"
    fscrypt = inspection.inspect_system(home, run=query, paths=paths)
    assert fscrypt.encryption.home.state == EncryptionState.ENCRYPTED
    assert fscrypt.encryption.home.layers == ("fscrypt",)
    assert "PAM" in fscrypt.encryption.home.detail


@pytest.mark.parametrize(
    "code,state", [(errno.ENOKEY, EncryptionState.LOCKED), (errno.EACCES, EncryptionState.UNKNOWN)]
)
def test_only_an_explicit_missing_key_is_reported_locked(context, code, state) -> None:
    home, paths, query = context

    def missing(_path: Path) -> int | None:
        raise OSError(code, "private error")

    observed = inspection.inspect_system(home, run=query, paths=paths, home_flags=missing)
    assert observed.encryption.home.state == state
    assert "private error" not in repr(observed)


@pytest.mark.parametrize(
    "tree",
    [
        None,
        "invalid",
        [],
        [{"name": "root", "type": "part", "children": "bad"}],
        [{"name": "cycle", "type": "crypt", "children": [{"name": "cycle", "type": "part"}]}],
    ],
)
def test_malformed_or_cyclic_topology_never_proves_encryption(context, tree) -> None:
    home, paths, query = context
    query.root_tree = tree
    observed = inspection.inspect_system(home, run=query, paths=paths)
    assert (
        observed.encryption.root.state == observed.encryption.home.state == EncryptionState.UNKNOWN
    )


def test_unknown_virtual_mount_does_not_mean_unencrypted(context) -> None:
    home, paths, query = context
    query.root_device = query.home_device = "overlay"
    query.root_filesystem = query.home_filesystem = "overlay"
    observed = inspection.inspect_system(home, run=query, paths=paths, home_flags=lambda _: None)
    assert (
        observed.encryption.root.state == observed.encryption.home.state == EncryptionState.UNKNOWN
    )
    assert not any(Path(call[0]).name == "lsblk" for call in query.calls)


def test_btrfs_source_suffix_is_not_passed_as_device(context) -> None:
    home, paths, query = context
    query.root_device = query.home_device = "/dev/root[/@subvolume]"
    query.root_tree = encrypted_tree()
    observed = inspection.inspect_system(home, run=query, paths=paths)
    assert observed.encryption.root.state == EncryptionState.ENCRYPTED
    assert next(call for call in query.calls if Path(call[0]).name == "lsblk")[-1] == "/dev/root"


@pytest.mark.parametrize("command", ["findmnt", "lsblk", "lsattr", "timedatectl", "dpkg-query"])
def test_unavailable_readonly_tools_leave_honest_unknown_observations(context, command) -> None:
    home, paths, query = context
    query.failed_command = command
    observed = inspection.inspect_system(home, run=query, paths=paths)
    assert "fixture private diagnostic" not in repr(observed)
    if command in {"findmnt", "lsblk"}:
        assert observed.encryption.root.state == EncryptionState.UNKNOWN
    if command == "timedatectl":
        assert observed.clock.timezone == "UTC" and observed.clock.synchronized is None
    if command == "dpkg-query":
        assert observed.clock.providers == () and not observed.clock.providers_known


def test_clock_uses_machine_properties_and_installed_provider_without_setting_them(context) -> None:
    home, paths, query = context
    query.clock = "Timezone=Region/City\nNTP=yes\nNTPSynchronized=no\nLocalRTC=yes\nCanNTP=yes\n"
    query.providers = ("chrony",)
    observed = inspection.inspect_system(home, run=query, paths=paths)
    assert observed.clock == ClockSnapshot(
        timezone="Region/City",
        ntp_enabled=True,
        synchronized=False,
        rtc_local=True,
        can_ntp=True,
        providers=("chrony",),
        providers_known=True,
    )
    assert all(
        Path(call[0]).name in {"findmnt", "lsblk", "lsattr", "timedatectl", "dpkg-query"}
        for call in query.calls
    )
    assert not any(
        "set-" in argument or argument in {"sudo", "apt-get", "cryptsetup", "fscrypt"}
        for call in query.calls
        for argument in call
    )


def test_ambiguous_clock_output_falls_back_without_inventing_sync_state(context) -> None:
    home, paths, query = context
    query.clock = "Timezone=UTC\nTimezone=Region/City\nNTP=yes\n"
    observed = inspection.inspect_system(home, run=query, paths=paths)
    assert observed.clock.timezone == "UTC"
    assert observed.clock.ntp_enabled is None and observed.clock.synchronized is None
    paths.timezone.unlink()
    query.clock = "malformed"
    assert inspection.inspect_system(home, run=query, paths=paths).clock.timezone == "UTC"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_timezone_choice_preserves_current_setting(context, value) -> None:
    _, paths, _ = context
    assert clock.validate_timezone(value, zoneinfo_root=paths.zoneinfo) is None


def test_timezone_uses_real_tzif_with_aliases_and_normalizes_outer_spaces(context) -> None:
    _, paths, _ = context
    (paths.zoneinfo / "Alias").symlink_to(paths.zoneinfo / "UTC")
    assert clock.validate_timezone(" Region/City ", zoneinfo_root=paths.zoneinfo) == "Region/City"
    assert clock.validate_timezone("Alias", zoneinfo_root=paths.zoneinfo) == "Alias"


@pytest.mark.parametrize(
    "value", ["../UTC", "/etc/shadow", "Region//City", "UTC\nprivate", "missing", "posix/UTC"]
)
def test_unsafe_or_missing_timezone_is_rejected_before_any_apply(context, value) -> None:
    _, paths, _ = context
    with pytest.raises(ValueError, match="installed IANA timezone"):
        clock.validate_timezone(value, zoneinfo_root=paths.zoneinfo)


def test_timezone_external_symlink_and_non_tzif_are_rejected(context, tmp_path: Path) -> None:
    _, paths, _ = context
    outside = tmp_path / "outside"
    outside.write_bytes(timezone_bytes())
    (paths.zoneinfo / "External").symlink_to(outside)
    (paths.zoneinfo / "Text").write_text("private marker")
    for value in ("External", "Text"):
        with pytest.raises(ValueError):
            clock.validate_timezone(value, zoneinfo_root=paths.zoneinfo)


def test_invalid_home_never_enters_a_query(context) -> None:
    _, paths, query = context
    for path in (Path("relative"), Path("/home/../root")):
        with pytest.raises(ValueError):
            inspection.inspect_system(path, run=query, paths=paths)
    assert query.calls == []


def test_native_runner_has_fixed_timeout_and_no_ambient_credentials(monkeypatch) -> None:
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("GH_TOKEN", "fixture")
    monkeypatch.setenv("HTTP_PROXY", "fixture")
    monkeypatch.setattr(subprocess, "run", execute)
    inspection_commands.run_inspection(
        ["/usr/bin/findmnt", "--json", "--target", "/", "--output", "SOURCE,FSTYPE"]
    )
    assert calls[0][1] == {
        "check": False,
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "text": True,
        "timeout": 10,
        "env": {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
    }


@pytest.mark.parametrize("content", [b"TZif", b"TZif" + b"\0" * 20, b"TZif" + b"x" * 60])
def test_truncated_tzif_has_a_preflight_value_error(context, content) -> None:
    _, paths, _ = context
    (paths.zoneinfo / "Broken").write_bytes(content)
    with pytest.raises(ValueError, match="installed IANA timezone"):
        clock.validate_timezone("Broken", zoneinfo_root=paths.zoneinfo)


@pytest.mark.parametrize(
    "argv",
    [
        ["/usr/bin/timedatectl", "set-timezone", "UTC"],
        ["/usr/bin/dpkg-query", "--control-path", "package", "postinst"],
        ["/usr/bin/findmnt", "--poll"],
        ["/usr/bin/lsblk", "--json"],
        ["/usr/bin/lsattr", "-d", "--", "relative"],
        ["/usr/sbin/cryptsetup", "open", "device", "name"],
    ],
)
def test_inspection_runner_rejects_any_nonquery_operation(monkeypatch, argv) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("nonquery subprocess"))
    with pytest.raises(ValueError, match="read-only inspection query"):
        inspection_commands.run_inspection(argv)


def test_real_localtime_fallback_wins_stale_timezone_label(context) -> None:
    home, paths, query = context
    paths.localtime.unlink()
    paths.localtime.symlink_to(paths.zoneinfo / "Region/City")
    paths.timezone.write_text("UTC\n")
    query.failed_command = "timedatectl"
    observed = inspection.inspect_system(home, run=query, paths=paths)
    assert observed.clock.timezone == "Region/City"


@pytest.mark.parametrize(
    "reply,state",
    [
        ("Unlocked: Yes\n", EncryptionState.ENCRYPTED),
        ("Unlocked: No\n", EncryptionState.LOCKED),
        (None, EncryptionState.UNKNOWN),
        ("Policy exists; key state unavailable\n", EncryptionState.UNKNOWN),
        ("Unlocked: Yes\nUnlocked: No\n", EncryptionState.UNKNOWN),
    ],
)
def test_fscrypt_production_query_classifies_unlock_state_without_key_material(
    context, monkeypatch: pytest.MonkeyPatch, reply, state
) -> None:
    home, paths, query = context
    query.attributes = "--------------eE------"
    query.fscrypt_status = reply

    def execute(argv, **options):
        assert options["timeout"] == 10 and options["check"] is False
        assert options["env"] == {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
        }
        return query(argv)

    monkeypatch.setattr(subprocess, "run", execute)
    observed = inspection.inspect_system(home, paths=paths)
    assert observed.encryption.home.state == state
    assert "fscrypt" in observed.encryption.home.layers
    assert ["/usr/bin/fscrypt", "status", str(home)] in query.calls
    assert all(call[1] != "unlock" for call in query.calls)


def test_native_missing_key_during_attribute_query_reaches_locked(context, monkeypatch) -> None:
    home, paths, query = context

    def execute(argv, **_options):
        if argv[0] == "/usr/bin/lsattr":
            raise OSError(errno.ENOKEY, "private key identifier")
        return query(argv)

    monkeypatch.setattr(subprocess, "run", execute)
    observed = inspection.inspect_system(home, paths=paths)
    assert observed.encryption.home.state == EncryptionState.LOCKED
    assert "private key identifier" not in repr(observed)


def test_plain_luks_home_is_not_marked_locked_when_fscrypt_tool_is_absent(context) -> None:
    home, paths, query = context
    query.root_tree = encrypted_tree()
    query.failed_command = "fscrypt"
    observed = inspection.inspect_system(home, run=query, paths=paths)
    assert observed.encryption.home.state == EncryptionState.ENCRYPTED
    assert not any(call[0] == "/usr/bin/fscrypt" for call in query.calls)


def test_copied_localtime_does_not_accept_a_conflicting_timezone_label(context) -> None:
    home, paths, query = context
    paths.localtime.unlink()
    paths.localtime.write_bytes(timezone_bytes(3600))
    paths.timezone.write_text("UTC\n")
    query.failed_command = "timedatectl"
    assert inspection.inspect_system(home, run=query, paths=paths).clock.timezone is None
    paths.localtime.write_bytes(timezone_bytes())
    assert inspection.inspect_system(home, run=query, paths=paths).clock.timezone == "UTC"


def test_readonly_runner_cannot_unlock_fscrypt(monkeypatch, context) -> None:
    home, _, _ = context
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("unlock invoked"))
    with pytest.raises(ValueError, match="read-only inspection query"):
        inspection_commands.run_inspection(["/usr/bin/fscrypt", "unlock", str(home)])


def test_timezone_symlink_cycle_has_a_preflight_value_error(context) -> None:
    _, paths, _ = context
    (paths.zoneinfo / "Cycle").symlink_to(paths.zoneinfo / "Cycle")
    with pytest.raises(ValueError, match="installed IANA timezone"):
        clock.validate_timezone("Cycle", zoneinfo_root=paths.zoneinfo)


@pytest.mark.parametrize("marker_type", ["directory", "file", "broken-link"])
@pytest.mark.parametrize("mount_known", [True, False])
def test_ecryptfs_policy_marker_requires_mounted_home_through_native_queries(
    context, monkeypatch, marker_type, mount_known
) -> None:
    home, paths, query = context
    marker = home / ".ecryptfs"
    if marker_type == "directory":
        marker.mkdir()
    elif marker_type == "file":
        marker.write_text("private policy marker; must not be read")
    else:
        marker.symlink_to(home / "unavailable-private-policy")
    query.root_tree = encrypted_tree()
    if not mount_known:
        query.failed_command = "findmnt"

    def execute(argv, **options):
        assert options["timeout"] == 10 and options["check"] is False
        assert options["stdin"] == subprocess.DEVNULL
        return query(argv)

    monkeypatch.setattr(subprocess, "run", execute)
    original_read = Path.open

    def no_marker_content(path, *arguments, **options):
        assert path != marker, "The eCryptfs marker content must stay unread"
        return original_read(path, *arguments, **options)

    monkeypatch.setattr(Path, "open", no_marker_content)
    observed = inspection.inspect_system(home, paths=paths)
    assert observed.encryption.home.state == (
        EncryptionState.LOCKED if mount_known else EncryptionState.UNKNOWN
    )
    assert observed.encryption.home.layers == ("eCryptfs",)
    assert "unlock" in observed.encryption.home.detail.lower()
    assert "private policy marker" not in repr(observed)
    assert set(home.iterdir()) == {marker}
    assert [
        "/usr/bin/findmnt",
        "--json",
        "--target",
        str(home),
        "--output",
        "SOURCE,FSTYPE",
    ] in query.calls
    assert not any(call[0] in {"/usr/bin/fscrypt", "/usr/bin/lsattr"} for call in query.calls)


def test_mounted_ecryptfs_marker_remains_encrypted_when_attributes_are_unsupported(context) -> None:
    home, paths, query = context
    (home / ".ecryptfs").symlink_to(home / "private-policy")
    query.home_filesystem = "ecryptfs"
    query.failed_command = "lsattr"
    observed = inspection.inspect_system(home, run=query, paths=paths)
    assert observed.encryption.home.state == EncryptionState.ENCRYPTED
    assert observed.encryption.home.layers == ("eCryptfs",)
    assert not any(call[0] in {"/usr/bin/fscrypt", "/usr/bin/lsattr"} for call in query.calls)


def test_home_access_guard_observes_ecryptfs_unmount_again_before_each_worker(
    context, monkeypatch
) -> None:
    home, _, query = context
    (home / ".ecryptfs").symlink_to(home / "private-policy")
    query.home_filesystem = "ecryptfs"

    def execute(argv, **options):
        assert options["stdin"] == subprocess.DEVNULL and options["timeout"] == 10
        assert Path(argv[0]).name not in {"timedatectl", "dpkg-query"}
        return query(argv)

    monkeypatch.setattr(subprocess, "run", execute)
    before = set(home.iterdir())
    assert encryption.validate_home_access(home).state == EncryptionState.ENCRYPTED
    query.home_filesystem = "ext4"
    with pytest.raises(
        ValueError, match="encrypted HOME is locked or its unlock status is unknown"
    ):
        encryption.validate_home_access(home)
    assert sum(call[0] == "/usr/bin/findmnt" for call in query.calls) == 2
    assert set(home.iterdir()) == before


@pytest.mark.parametrize("reply", ["Unlocked: No\n", None, "Unknown\n"])
def test_home_access_guard_rechecks_existing_fscrypt_key_state(context, reply) -> None:
    home, _, query = context
    query.attributes = "--------------eE------"
    assert encryption.validate_home_access(home, run=query).state == EncryptionState.ENCRYPTED
    query.fscrypt_status = reply
    with pytest.raises(
        ValueError, match="encrypted HOME is locked or its unlock status is unknown"
    ):
        encryption.validate_home_access(home, run=query)
    assert not any(Path(call[0]).name in {"timedatectl", "dpkg-query"} for call in query.calls)
    assert list(home.iterdir()) == []


def test_home_access_guard_accepts_accessible_ordinary_unknown_without_creating_files(
    context,
) -> None:
    home, _, query = context
    private = home / "private filename must not be logged"
    private.write_bytes(b"existing content")
    before = private.stat()
    info = encryption.validate_home_access(home, run=query)
    assert info.state == EncryptionState.UNKNOWN and not info.layers
    assert private.read_bytes() == b"existing content"
    assert private.stat().st_mtime_ns == before.st_mtime_ns
    assert list(home.iterdir()) == [private]
    assert private.name not in repr(info)
    assert not any(Path(call[0]).name in {"timedatectl", "dpkg-query"} for call in query.calls)


@pytest.mark.parametrize("where", ["open", "read"])
def test_home_access_guard_rejects_read_errors_without_private_diagnostics_or_leaked_descriptors(
    context, monkeypatch, where
) -> None:
    home, _, query = context
    descriptors = []
    original_open = os.open

    def open_directory(path, flags):
        if where == "open":
            raise OSError(errno.EACCES, "private directory diagnostic")
        descriptor = original_open(path, flags)
        descriptors.append(descriptor)
        return descriptor

    def unreadable(_path):
        raise OSError(errno.ENOKEY, "private key description")

    monkeypatch.setattr(os, "open", open_directory)
    if where == "read":
        monkeypatch.setattr(os, "scandir", unreadable)
    with pytest.raises(ValueError, match="HOME is locked or unreadable") as error:
        encryption.validate_home_access(home, run=query)
    assert "private" not in str(error.value)
    for descriptor in descriptors:
        with pytest.raises(OSError) as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF


def test_home_access_guard_rejects_unsafe_home_before_native_queries(context) -> None:
    home, _, query = context
    alias = home.parent / "symlink HOME"
    alias.symlink_to(home, target_is_directory=True)
    for selected in (Path("relative"), home / ".." / home.name, alias):
        with pytest.raises(ValueError):
            encryption.validate_home_access(selected, run=query)
    assert query.calls == []
