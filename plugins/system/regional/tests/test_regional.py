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


def test_keyboard_choice_through_native_plan_and_repeat(tmp_path, monkeypatch):
    import asyncio
    from importlib import import_module
    from types import SimpleNamespace

    import setup_plugin_regional as plugin

    from setup_system_sdk import Outcome, PluginSettings

    native = import_module("setup_plugin_regional.change")
    xkb = tmp_path / "xkb"
    (xkb / "rules").mkdir(parents=True)
    (xkb / "symbols").mkdir()
    # Symbols can also be internal composition files and variants.
    for name in ("us", "ru", "group"):
        (xkb / "symbols" / name).touch()
    (xkb / "rules/base.lst").write_text(
        "! layout\n us English\n ru Russian\n custom User-defined\n! variant\n group internals\n"
    )
    monkeypatch.setattr(regional.xkb, "XKB", xkb)
    keyboard = tmp_path / "keyboard"
    keyboard.write_text('XKBMODEL="pc105"\nXKBLAYOUT="us"\n')
    locale = tmp_path / "locale"
    locale.write_text("LANG=C.UTF-8\n")
    monkeypatch.setattr(regional, "KEYBOARD", keyboard)
    monkeypatch.setattr(regional, "LOCALE", locale)
    monkeypatch.setattr(regional, "STATE", tmp_path / "state")
    monkeypatch.setattr(regional, "protected", lambda path, **kw: path.relative_to(tmp_path))
    monkeypatch.setattr(native, "os", SimpleNamespace(geteuid=lambda: 0))
    checked = []
    monkeypatch.setattr(
        native, "require_installed_resource", lambda path, dest: checked.append(dest)
    )
    for module in (plugin, native):
        monkeypatch.setattr(module, "current_boot_identity", lambda: "synthetic-boot")
        monkeypatch.setattr(module, "current_target", lambda: ("amd64", True))
    calls = []
    monkeypatch.setattr(regional, "_command", lambda args: calls.append(args))

    def bridge(plugin_id, target, payload, method):
        assert (plugin_id, target, method) == ("regional", "configure.yml", "sudo")
        native.apply(native.Confirmation.model_validate(payload))

    monkeypatch.setattr(plugin, "run_native_play", bridge)
    backend, settings, selection = (
        plugin.Plugin(),
        PluginSettings(),
        plugin.Keyboard(layouts=["us", "ru"]),
    )
    assert asyncio.run(backend.choices("keyboard", "", settings)) == ["ru", "us"]
    assert asyncio.run(backend.choices("keyboard", "R", settings)) == ["ru"]
    assert "keyboard=us" in asyncio.run(backend.inspect(settings)).message
    plan = asyncio.run(backend.plan("keyboard", settings, selection))
    assert plan.outcome == Outcome.READY and plan.resources[0].before == "us"
    assert asyncio.run(backend.verify(plan, settings, selection)).outcome == Outcome.PARTIAL
    plugin.apply_native("keyboard", settings, selection, plan)
    assert asyncio.run(backend.verify(plan, settings, selection)).outcome == Outcome.CHANGED
    assert asyncio.run(backend.plan("keyboard", settings, selection)).outcome == Outcome.NO_CHANGES
    assert checked == ["plugins/regional/setup_plugin_regional/change.py"]
    assert calls == [["/usr/bin/setupcon", "--keyboard-only", "--save-only"]]
    assert 'XKBMODEL="pc105"' in keyboard.read_text()
    with pytest.raises(ValueError, match="changed"):
        plugin.apply_native("keyboard", settings, selection, plan)
    for value in ("us,us", "group", "custom", "us/../../etc", "us,ru,us,ru,us"):
        with pytest.raises(ValueError):
            native.apply(
                native.Confirmation(
                    setting="keyboard", value=value, before="us,ru", boot="synthetic-boot"
                )
            )
    assert len(calls) == 1
