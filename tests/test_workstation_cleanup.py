"""Verify the actual native removal boundary with synthetic dpkg observations."""

import json
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment

from setup_system.domain.intentions import CleanupMode, WorkspaceOptions
from setup_system.infrastructure.processes import run_command
from setup_system.models import CleanupOptions

ROOT = Path(__file__).resolve().parents[1]


def review(monkeypatch, selected, closure, records, mode="remove"):
    module = runpy.run_path(str(ROOT / "ansible/files/removals.py"))

    def inventory(argv, **kwargs):
        assert argv[0:2] == ["/usr/bin/dpkg-query", "-W"]
        assert argv[argv.index("--") + 1 :] == sorted(selected)
        missing = set(selected) - {row.split("|")[0] for row in records}
        return subprocess.CompletedProcess(
            argv,
            int(bool(missing)),
            "\n".join(records),
            "\n".join(f"dpkg-query: no packages found matching {name}" for name in missing),
        )

    monkeypatch.setitem(module["check"].__globals__, "run_command", inventory)
    module["check"]({"selected": selected, "closure": closure, "mode": mode})


@pytest.mark.parametrize(
    "name",
    ["apt", "systemd", "cryptsetup", "linux-image-amd64", "network-manager", "hyprland", "neovim"],
)
def test_critical_package_cannot_be_selected(monkeypatch, name):
    with pytest.raises(ValueError):
        review(monkeypatch, [name], [f"Remv {name} [1]"], [])


@pytest.mark.parametrize("protected", ["install|yes|no", "install|no|yes", "hold|no|no"])
def test_dpkg_protection_overrides_catalog(monkeypatch, protected):
    with pytest.raises(ValueError, match=r"held|protected"):
        review(
            monkeypatch,
            ["reportbug"],
            ["Remv reportbug [1]"],
            ["reportbug|all|installed|" + protected],
        )


def test_extra_dependency_is_rejected_even_if_reviewed(monkeypatch):
    with pytest.raises(ValueError, match="outside"):
        review(
            monkeypatch,
            ["reportbug"],
            ["Remv reportbug [1]", "Remv nano [1]"],
            ["reportbug|all|installed|install|no|no"],
        )


@pytest.mark.parametrize(
    "mode,action", [("remove", "Remv"), ("purge", "Purg"), ("autoremove", "Remv")]
)
def test_exact_native_closure_is_accepted(monkeypatch, mode, action):
    review(
        monkeypatch,
        ["reportbug"],
        [f"{action} reportbug [1]"],
        ["reportbug|all|installed|install|no|no"],
        mode,
    )


def test_absent_candidate_only_accepts_empty_plan(monkeypatch):
    review(monkeypatch, ["reportbug"], [], [])
    with pytest.raises(ValueError, match="outside"):
        review(monkeypatch, ["reportbug"], ["Remv reportbug [1]"], [])


@pytest.mark.parametrize(
    ("status", "records", "diagnostic", "error"),
    [
        (0, "", "", "Incomplete"),
        (1, "", "dpkg-query: failed to read status", "Cannot verify"),
        (0, "reportbug|riscv64|installed|install|no|no", "", "architecture"),
        (0, "unselected|all|installed|install|no|no", "", "Unexpected package"),
    ],
)
def test_unverified_removal_inventory_stops_before_approval(
    monkeypatch, status, records, diagnostic, error
):
    inventory = runpy.run_path(str(ROOT / "ansible/files/removals.py"))["installed_candidates"]
    monkeypatch.setitem(
        inventory.__globals__,
        "run_command",
        lambda argv, **options: subprocess.CompletedProcess(argv, status, records, diagnostic),
    )
    with pytest.raises(ValueError, match=error):
        inventory({"reportbug"}, "amd64", CleanupMode.REMOVE)


def task(name, match):
    for item in yaml.safe_load((ROOT / f"ansible/tasks/{name}.yml").read_text()):
        if item.get("name", "").startswith(match):
            return item
    raise AssertionError(match)


def render(expression, **values):
    env = NativeEnvironment(undefined=StrictUndefined)
    env.filters["from_json"] = json.loads
    return env.from_string(expression).render(**values)


def simulate_args(mode):
    choose = task("apt_plan", "Choose the explicit native package plan")
    args = render(
        choose["ansible.builtin.set_fact"]["apt_args"],
        intent={"cleanup_mode": mode},
        cleanup=["reportbug"],
    )
    simulation = task("apt_plan", "Simulate the entire selected Debian transaction")
    return render(
        simulation["ansible.builtin.command"]["argv"], apt=["/usr/bin/apt-get"], apt_args=args
    )


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("remove", ["remove", "--", "reportbug"]),
        ("purge", ["purge", "--", "reportbug"]),
        ("autoremove", ["autoremove"]),
    ],
)
def test_each_cleanup_mode_has_its_own_simulation(mode, expected):
    workspace = WorkspaceOptions.model_validate({"cleanup_mode": mode})
    plan = CleanupOptions.model_validate({"cleanup": ["reportbug"], "cleanup_mode": mode})
    assert workspace.cleanup_mode is plan.cleanup_mode is CleanupMode(mode)
    assert plan.model_dump(mode="json")["cleanup_mode"] == mode
    assert simulate_args(mode) == ["/usr/bin/apt-get", "--simulate", *expected]


def test_preview_uses_dpkg_and_simulation_without_mutation(tmp_path, monkeypatch, capsys):
    status = tmp_path / "status"
    status.write_text(
        "Package: reportbug\nStatus: install ok installed\nArchitecture: all\n"
        "Version: 1.0\nMaintainer: Test <test@example.invalid>\n"
        "Description: Synthetic removal preview\n\n"
    )
    before = status.read_bytes()
    base, *args = simulate_args("remove")
    simulation = subprocess.run(
        [
            base,
            "-o",
            "Dir::Etc::sourcelist=/dev/null",
            "-o",
            "Dir::Etc::sourceparts=-",
            "-o",
            f"Dir::State::status={status}",
            "-o",
            f"Dir::State::lists={tmp_path}",
            "-o",
            "Dir::Cache::pkgcache=",
            "-o",
            "Dir::Cache::srcpkgcache=",
            "-o",
            "Debug::NoLocking=true",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    filters = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))
    closure = filters["apt_closure"](simulation.stdout, True)
    assert closure == ["Remv reportbug [1.0]"]
    check = runpy.run_path(str(ROOT / "ansible/files/removals.py"))["check"]

    def observe(argv, **options):
        assert argv[:2] == ["/usr/bin/dpkg-query", "-W"]
        return run_command([argv[0], "--admindir=" + str(tmp_path), *argv[1:]], **options)

    monkeypatch.setitem(check.__globals__, "run_command", observe)
    check({"mode": "remove", "selected": ["reportbug", "doc-debian"], "closure": closure})
    assert json.loads(capsys.readouterr().out) == ["reportbug"]
    assert status.read_bytes() == before


@pytest.mark.parametrize(
    ("mode", "step"), [("remove", "--remove"), ("autoremove", "--remove"), ("purge", "--purge")]
)
def test_apply_removes_exact_confirmed_set_without_another_resolver(
    monkeypatch, capsys, mode, step
):
    line = ("Purg" if mode == "purge" else "Remv") + " reportbug [1]"
    review(monkeypatch, ["reportbug"], [line], ["reportbug|all|installed|install|no|no"], mode)
    approved = capsys.readouterr().out
    remove = task("apply", "Remove exactly the approved packages")
    argv = render(
        remove["ansible.builtin.command"]["argv"],
        intent={"cleanup_mode": mode},
        removals={"stdout": approved},
    )
    assert argv == ["/usr/bin/dpkg", step, "--", "reportbug"]
    with pytest.raises(ValueError, match="outside"):
        review(
            monkeypatch,
            ["reportbug"],
            [line, line.replace("reportbug", "nano")],
            ["reportbug|all|installed|install|no|no"],
            mode,
        )
    assert capsys.readouterr().out == ""


def editor_fixture(tmp_path, monkeypatch, selected, fallback_owner, metadata):
    root = tmp_path / "release"
    catalog = root / "config/workspace/optional_cleanup.toml"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(f"[candidates.{selected}]\nrequires_verified_editor = {metadata}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("nvim", "vim.tiny"):
        (bin_dir / name).touch(mode=0o755)
    (bin_dir / "editor").symlink_to(bin_dir / "nvim")
    (bin_dir / "vi").symlink_to(bin_dir / "vim.tiny")
    home = tmp_path / "home"
    editor = home / "config/environment.d/90-setup-system-editor.conf"
    editor.parent.mkdir(parents=True)
    editor.write_text("EDITOR=nvim\nVISUAL=nvim\n")
    check = runpy.run_path(str(ROOT / "ansible/files/removals.py"))["check"]

    def fixture_path(value):
        path = Path(value)
        return bin_dir / path.name if path.parent == Path("/usr/bin") else path

    def run(argv, **options):
        if argv[:2] == ["/usr/bin/dpkg-query", "-W"]:
            return subprocess.CompletedProcess(
                argv, 0, f"{selected}|all|installed|install|no|no\n", ""
            )
        if argv[:2] == ["/usr/bin/dpkg-query", "-S"]:
            return subprocess.CompletedProcess(argv, 0, f"{fallback_owner}:amd64: {argv[-1]}\n", "")
        if argv[0] == "/usr/bin/grep":
            assert argv[-1] == str(editor)
            return run_command(argv, **options)
        assert argv == ["/usr/bin/nvim", "--clean", "--headless", "-n", "-i", "NONE", "+q"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setitem(check.__globals__, "__file__", str(root / "ansible/files/removals.py"))
    monkeypatch.setitem(check.__globals__, "Path", fixture_path)
    monkeypatch.setitem(check.__globals__, "run_command", run)
    monkeypatch.setitem(
        check.__globals__,
        "pwd",
        SimpleNamespace(getpwuid=lambda uid: SimpleNamespace(pw_dir=str(home), pw_name="fixture")),
    )
    return lambda: check(
        {
            "selected": [selected],
            "closure": [f"Remv {selected} [1]"],
            "mode": "remove",
            "home": str(home),
            "xdg": {"config": str(home / "config")},
        }
    )


@pytest.mark.parametrize("valid", [False, True])
def test_fallback_editor_replacement_is_required(tmp_path, monkeypatch, valid):
    check = editor_fixture(
        tmp_path, monkeypatch, "vim-tiny", "vim" if valid else "vim-tiny", "true"
    )
    if valid:
        check()
    else:
        with pytest.raises(ValueError, match="independent"):
            check()


@pytest.mark.parametrize("required", [False, True])
def test_catalog_controls_editor_requirement(tmp_path, monkeypatch, required):
    check = editor_fixture(tmp_path, monkeypatch, "reportbug", "reportbug", str(required).lower())
    if required:
        with pytest.raises(ValueError, match="independent"):
            check()
    else:
        check()


@pytest.mark.parametrize(
    ("mode", "closure"),
    [
        ("remove", ["Purg reportbug [1]"]),
        ("purge", ["Remv reportbug [1]"]),
        ("autoremove", ["Remv reportbug:riscv64 [1]"]),
        ("different", ["Remv reportbug [1]"]),
        ("remove", ["Remv reportbug [1]", "Remv reportbug [1]"]),
    ],
)
def test_changed_mode_architecture_or_repeated_removal_is_rejected(monkeypatch, mode, closure):
    with pytest.raises(ValueError, match="removal"):
        review(monkeypatch, ["reportbug"], closure, ["reportbug|all|installed|install|no|no"], mode)
