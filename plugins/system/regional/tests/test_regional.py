"""Independent regional operations preserve unrelated Debian configuration."""

import json
from pathlib import Path

import pytest
from setup_plugin_regional import regional


def test_keyboard_apply_preserves_other_fields_and_records_backup(tmp_path, monkeypatch):
    layout = tmp_path / "keyboard"
    original = '# retained\nXKBMODEL="pc105"\nXKBLAYOUT="us"\n'
    layout.write_text(original)
    monkeypatch.setattr(regional, "KEYBOARD", layout)
    monkeypatch.setattr(regional, "STATE", tmp_path / "private")
    monkeypatch.setattr(regional, "protected", lambda *args, **kwargs: None)
    calls = []
    monkeypatch.setattr(regional, "_command", lambda argv: calls.append(argv))
    regional.apply("keyboard", "us,ru", "us")
    assert layout.read_text() == original.replace('"us"', '"us,ru"')
    receipt = json.loads((regional.STATE / "keyboard.json").read_text())
    assert receipt["status"] == "complete" and Path(receipt["backup"]).read_text() == original
    regional.apply("keyboard", "us,ru", "us,ru")
    assert len(calls) == 1 and "--save-only" in calls[0]


def test_timezone_rejects_false_success_after_command(tmp_path, monkeypatch):
    monkeypatch.setattr(regional, "STATE", tmp_path)
    monkeypatch.setattr(regional, "actual", lambda kind: "UTC")
    monkeypatch.setattr(regional, "protected", lambda *args, **kwargs: None)
    monkeypatch.setattr(regional, "_command", lambda argv: "")
    with pytest.raises(RuntimeError, match="readback"):
        regional.apply("timezone", "Europe/Moscow", "UTC")
    assert json.loads((tmp_path / "timezone.json").read_text())["status"] == "partial"


def test_external_change_between_plan_apply_stops_before_write(tmp_path, monkeypatch):
    monkeypatch.setattr(regional, "STATE", tmp_path)
    monkeypatch.setattr(regional, "actual", lambda kind: "Asia/Tokyo")
    with pytest.raises(ValueError, match="changed"):
        regional.apply("timezone", "Europe/Moscow", "UTC")
    assert not list(tmp_path.iterdir())


def test_missing_supported_locale_is_generated_without_dropping_existing_locales(
    tmp_path, monkeypatch
):
    locale = tmp_path / "locale.conf"
    locale.write_text("LANG=C.UTF-8\n")
    supported = tmp_path / "SUPPORTED"
    supported.write_text("en_GB.UTF-8 UTF-8\n")
    generator = tmp_path / "locale.gen"
    generator.write_text("de_DE.UTF-8 UTF-8\n")
    for name, path in (
        ("LOCALE", locale),
        ("SUPPORTED", supported),
        ("LOCALE_GEN", generator),
        ("STATE", tmp_path / "state"),
    ):
        monkeypatch.setattr(regional, name, path)
    monkeypatch.setattr(regional, "protected", lambda *args, **kwargs: None)
    generated = ["C.utf8"]

    def command(args):
        if args == ["/usr/bin/locale", "-a"]:
            return "\n".join(generated)
        if args == ["/usr/sbin/locale-gen", "--keep-existing"]:
            generated.append("en_GB.utf8")
        else:
            assert args == ["/usr/sbin/update-locale", "LANG=en_GB.UTF-8"]
            locale.write_text("LANG=en_GB.UTF-8\n")
        return ""

    monkeypatch.setattr(regional, "_command", command)
    regional.apply("locale", "en_GB.UTF-8", "C.UTF-8")
    assert generator.read_text().splitlines() == ["de_DE.UTF-8 UTF-8", "en_GB.UTF-8 UTF-8"]
    record = json.loads((regional.STATE / "locale.json").read_text())
    assert record["status"] == "complete"
    assert Path(record["locale_gen_backup"]).read_text() == "de_DE.UTF-8 UTF-8\n"


def test_previous_unknown_receipt_stops_native_command(tmp_path, monkeypatch):
    monkeypatch.setattr(regional, "STATE", tmp_path)
    monkeypatch.setattr(regional, "actual", lambda kind: "UTC")
    monkeypatch.setattr(regional, "protected", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        regional, "_command", lambda argv: pytest.fail("Unknown receipt reached native command")
    )
    path = tmp_path / "timezone.json"
    original = b'{"schema_version":2,"status":"running"}'
    path.write_bytes(original)
    with pytest.raises(ValueError):
        regional.apply("timezone", "Europe/Moscow", "UTC")
    assert path.read_bytes() == original
