"""Exercise the same consent and dispatch inputs as the installed plays."""

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment
from pydantic import ValidationError

from setup_system.models import PlanSnapshot, TaskResult

ROOT = Path(__file__).resolve().parents[1]


def test_localized_system_labels_do_not_change_dispatch_or_consent():
    actions = [
        {"kind": "happ_start", "label": "Запустить службу"},
        {"kind": "region", "field": "timezone", "value": "Etc/UTC", "label": "Часовой пояс"},
        {"kind": "graphical_login", "label": "Графический вход"},
        {"kind": "firefox_policy", "label": "Приватность браузера"},
    ]
    tasks = yaml.safe_load((ROOT / "ansible/tasks/apply.yml").read_text())
    guarded = {"happ-unit", "region", "login", "firefox"}
    env = Environment()
    actual = []
    for task in tasks:
        command = task.get("ansible.builtin.command", {})
        op = command.get("argv", [None])[-1]
        if op in guarded:
            enabled = env.compile_expression(task["when"])(
                section="base", system_actions=actions, system_kinds=[a["kind"] for a in actions]
            )
            if enabled:
                actual.append(op)
    assert actual == ["happ-unit", "region", "login", "firefox"]

    def result(rows):
        return TaskResult.model_validate(
            {
                "status": "ready",
                "actions": rows,
                "observed": {"needs_system": True, "needs_user": False},
            }
        )

    plan = result(actions)
    assert plan.same_plan(result([dict(a, label="Edited presentation") for a in actions]))
    assert not plan.same_plan(result([actions[1], actions[0], *actions[2:]]))
    assert not plan.same_plan(
        result([actions[0], dict(actions[1], value="Europe/Paris"), *actions[2:]])
    )


@pytest.mark.parametrize(
    "observed",
    [
        {"uid": True},
        {"needs_system": "true"},
        {"arbitrary": {"command": "anything"}},
        {"boot": {"firefox": [1, 2, 420, "bad hash"]}},
        {"files": {"before": {"../outside": None}, "changes": ["../outside"], "after": {}}},
    ],
)
def test_invalid_native_observation_rejected_before_authorization(observed):
    with pytest.raises(ValidationError):
        TaskResult.model_validate({"status": "ready", "observed": observed})


def test_public_snapshot_retains_original_structured_input():
    reply = {
        "status": "ready",
        "actions": [{"kind": "firefox_policy", "label": "Protect Firefox"}],
        "observed": {"uid": 1000, "needs_system": True, "needs_user": False},
    }
    plan = PlanSnapshot.capture("workspace", {"apps": ["firefox"]}, reply, None)
    reply["actions"][0]["kind"] = "happ_start"
    reply["observed"]["uid"] = 42
    assert plan.payload()["expected"]["actions"][0]["kind"] == "firefox_policy"
    assert plan.payload()["expected"]["observed"]["uid"] == 1000


@pytest.mark.parametrize(
    ("target", "kind", "needs_system"),
    [
        ("workspace-apply", "editor_verify", False),
        ("workspace-user-apply", "firefox_policy", True),
        ("workspace-user-apply", "firefox_policy", False),
    ],
)
def test_system_scope_is_bound_to_action_and_make_target(target, kind, needs_system):
    from setup_system.models import RequestEnvelope

    accepted = {
        "status": "ready",
        "actions": [{"kind": kind, "label": "Localized text"}],
        "observed": {"needs_system": needs_system, "needs_user": not needs_system},
    }
    request = RequestEnvelope.model_validate({"schema": 1, "settings": {}, "expected": accepted})
    with pytest.raises(ValueError, match="scope"):
        request.for_target(target)


def test_root_revalidates_semantic_stage_and_allows_localized_labels():
    from setup_system.domain.system_requests import StageApply

    source = {
        "section": "base",
        "settings": {"timezone": "Europe/Paris"},
        "observed": {"timezone": "UTC"},
        "actions": [
            {"kind": "region", "field": "timezone", "value": "Europe/Paris", "label": "Réglage"}
        ],
    }
    stage = StageApply.model_validate(source)
    stage.require_operation("region")
    with pytest.raises(ValueError, match="missing"):
        stage.require_operation("login")
    changed = dict(source["actions"][0], value="UTC")
    with pytest.raises(ValueError, match="differ"):
        StageApply.model_validate(source | {"actions": [changed]}).require_operation("region")
    for extra in ({"apt_work": "/tmp/foreign"}, {"shell": "/bin/false"}):
        with pytest.raises(ValueError):
            StageApply.model_validate(source | extra)


def test_nested_request_fails_before_any_managed_file_is_opened():
    from setup_system.models import RequestEnvelope

    nested = {}
    for _ in range(26):
        nested = {"field": nested}
    with pytest.raises(ValueError, match="structure limits"):
        RequestEnvelope.model_validate({"schema": 1, "settings": nested, "expected": None})


def test_request_schema_and_fixed_target_reject_unregistered_modes():
    from setup_system.models import RequestEnvelope

    with pytest.raises(ValueError, match="integer"):
        RequestEnvelope.model_validate({"schema": True, "settings": {}, "expected": None})
    with pytest.raises(ValueError, match="Unknown"):
        RequestEnvelope.model_validate({"schema": 1, "settings": {}, "expected": None}).for_target(
            "restore-inspect"
        )


def test_installed_play_enforces_scope_without_the_textual_runner():
    import runpy

    validate = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))["validate_request"]
    request = {
        "schema": 1,
        "settings": {},
        "expected": {
            "status": "ready",
            "actions": [{"kind": "firefox_policy", "label": "Définir la confidentialité"}],
            "observed": {"needs_system": True, "needs_user": False},
        },
    }
    with pytest.raises(ValueError, match="scope"):
        validate(request, "workspace", {}, {}, "user-apply")


def test_private_operation_reader_rejects_links_and_excess_without_reading(tmp_path):
    import os

    from setup_system.domain.contracts import MAX_MESSAGE_BYTES
    from setup_system.infrastructure.operation_io import read_wire

    request = tmp_path / "request.json"
    request.write_bytes(b"{}")
    request.chmod(0o600)
    assert read_wire(request) == b"{}"
    link = tmp_path / "link"
    link.symlink_to(request)
    with pytest.raises(OSError):
        read_wire(link)
    link.unlink()
    os.link(request, link)
    with pytest.raises(ValueError):
        read_wire(request)
    link.unlink()
    with request.open("wb") as stream:
        stream.truncate(MAX_MESSAGE_BYTES + 1)
    with pytest.raises(ValueError):
        read_wire(request)


def test_account_description_comes_from_validated_facts():
    raw = {
        "username": "guest",
        "uid": 1042,
        "gid": 1099,
        "home": "/home/guest",
        "shell": "/bin/bash",
        "sudo_gid": 27,
        "member": True,
    }
    reply = TaskResult.model_validate(
        {"status": "no_changes", "observed": {"account": raw, "boot_identity": "c" * 64}}
    )
    details = reply.describe()
    for item in (
        "guest",
        "UID 1042",
        "GID 1099",
        "/home/guest",
        "/bin/bash",
        "GID 27",
        "yes",
        "Effective sudo access",
    ):
        assert item in details


@pytest.mark.parametrize("wire", ["ready", "plan_ready", "partial", "needs_reboot"])
def test_result_status_round_trips_native_protocol(wire):
    from setup_system.models import Status

    result = TaskResult.model_validate({"status": wire})
    assert result.status is Status(wire)
    assert result.model_dump(mode="json")["status"] == wire
    assert result.status.allows_confirmation == (wire in {"ready", "plan_ready"})


def test_unknown_native_status_is_rejected():
    with pytest.raises(ValidationError):
        TaskResult.model_validate({"status": "unrecognized"})


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_request_rejects_nonfinite_nested_json(invalid):
    from setup_system.models import RequestEnvelope

    with pytest.raises(ValidationError):
        RequestEnvelope.model_validate(
            {"schema": 1, "settings": {"nested": [invalid]}, "expected": None}
        )


def test_direct_request_bounds_utf8_byte_size():
    from setup_system.models import RequestEnvelope

    # Forty individually small strings together exceed the one MiB wire limit.
    settings = {"nested": ["界" * 10000] * 40}
    with pytest.raises(ValidationError):
        RequestEnvelope.model_validate({"schema": 1, "settings": settings, "expected": None})
