"""The selected editor action includes parser verification before completion."""

from pathlib import Path

import pytest

from setup_system.composition import build_service
from setup_system.domain.operations import ApplicationAction, WorkstationPlan
from setup_system.domain.settings import Section, SystemSettings, UserSettings
from setup_system.infrastructure import editor


def test_neovim_action_fails_if_parser_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(editor, "install_neovim", lambda *_: None)
    monkeypatch.setattr(editor, "install_plugins", lambda *_: [])
    monkeypatch.setattr("setup_system.infrastructure.editor.apply_files", lambda *_: None)

    def broken_parser(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Parser verification failed")

    monkeypatch.setattr(editor, "_headless", broken_parser)
    monkeypatch.setattr(editor.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        editor, "detect_platform", lambda **_kwargs: type("Platform", (), {"implemented": True})()
    )
    service = build_service(Path(__file__).resolve().parents[1])
    plan = WorkstationPlan(
        section=Section.WORKSPACE,
        system=SystemSettings(),
        user=UserSettings(apps=("neovim",)),
        actions=(ApplicationAction(id="neovim", description="Editor"),),
    )
    with pytest.raises(RuntimeError, match="Parser verification failed"):
        service._apply(plan.actions[0], plan)


def test_editor_locks_cover_selectable_language_parsers() -> None:
    import json
    import tomllib

    from setup_system.infrastructure.catalog_repository import load_components, load_editor
    from setup_system.resource_locator import ResourceLocator

    root = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((root / "manifests/editor.toml").read_text())
    assert set(manifest["parsers"]) - {"base"} == {
        item.id for item in load_components(root).components("language")
    }
    files = editor.editor_files(root, UserSettings(), load_editor(root))
    native = json.loads(
        next(content for path, content in files.items() if path.name == "lazy-lock.json")
    )
    assert native == {
        name: {key: entry[key] for key in ("commit", "branch")}
        for name, entry in manifest["plugins"].items()
        if name != "lazy"
    }
    resources = ResourceLocator.for_root(root)
    inputs = resources.resource("catalogs/python_tools.in").read_text().splitlines()
    locked = resources.resource("catalogs/python_tools.requirements.txt").read_text()
    assert all(any(row.startswith(name + "==") for row in locked.splitlines()) for name in inputs)


def test_missing_flutter_delivery_blocks_before_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.domain.operations import InstallRequest, Operation
    from setup_system.infrastructure.language_tools import language_blocker

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr("platform.machine", lambda: "aarch64")
    monkeypatch.setattr(
        "platform.freedesktop_os_release",
        lambda: {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"},
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    for kind in ("DATA", "CONFIG", "CACHE", "STATE"):
        monkeypatch.setenv("XDG_" + kind + "_HOME", str(tmp_path / kind.lower()))
    monkeypatch.delenv("FLUTTER_ROOT", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert "arm64" in str(language_blocker(root, "dart_flutter"))
    import os

    from setup_system.domain.identity import TargetUser

    target = TargetUser("fixture", os.getuid(), os.getgid(), tmp_path, "/bin/bash")
    service = build_service(
        root,
        identity=lambda _name: target,
        authorize=lambda: pytest.fail("authorization before SDK availability"),
    )

    request = InstallRequest(
        operation=Operation.APPLY,
        yes=True,
        section=Section.WORKSPACE,
        settings=UserSettings(language_profiles=("dart_flutter",)),
    )
    assert service.run(request) == 4
    assert any("Flutter archive" in item for item in service.plan(request).blockers)
    sdk = tmp_path / "flutter sdk"
    for entry in ("bin/flutter", "bin/cache/dart-sdk/bin/dart", "packages/flutter/pubspec.yaml"):
        path = sdk / entry
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o755)
    monkeypatch.setenv("FLUTTER_ROOT", str(sdk))
    assert language_blocker(root, "dart_flutter") is None
