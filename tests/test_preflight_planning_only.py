"""An empty preflight plan completes planning and authorizes nothing (#610).

Before preflight ``0.6`` a plan that named nothing returned the shared
``complete`` state, whose permission vector grants merge and completion, with
no verifier identity behind it. These tests pin the replacement from both
sides: the runtime payload for every route, and the published schema it
claims, accepting the real payloads first and only then refusing each
tampered one.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.host_grants import build_host_grants_baseline, host_audit_inventory
from agents_shipgate.core.preflight import build_preflight_result
from agents_shipgate.mcp_server.server import shipgate_preflight
from agents_shipgate.schemas.agent_control import PERMISSION_FIELDS
from agents_shipgate.schemas.preflight import (
    PreflightResultV5,
    PreflightResultV6,
    parse_preflight_result,
)

ROOT = Path(__file__).resolve().parent.parent
CURRENT_SCHEMA = ROOT / "docs" / "preflight-schema.v0.6.json"
FROZEN_SCHEMA = ROOT / "docs" / "preflight-schema.v0.5.json"
runner = CliRunner()


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "shipgate.yaml").write_text(
        'version: "0.1"\nproject:\n  name: planning-only\nagent:\n  name: support-agent\n'
        "  declared_purpose:\n    - answer support questions\nenvironment:\n  target: local\n"
        "tool_sources:\n  - id: tools\n    type: mcp\n    path: tools.json\n",
        encoding="utf-8",
    )
    (root / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (root / "AGENTS.md").write_text("Run Shipgate.\n", encoding="utf-8")
    (root / "README.md").write_text("Support agent.\n", encoding="utf-8")
    return root


def _validator(path: Path = CURRENT_SCHEMA) -> Draft202012Validator:
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def _assert_authorizes_nothing(payload: dict[str, Any]) -> None:
    control = payload["control"]
    assert control["completion_allowed"] is False
    assert set(control["permissions"]) == set(PERMISSION_FIELDS)
    assert not any(control["permissions"].values())


def _empty_plan_forms(root: Path) -> dict[str, Callable[[], dict[str, Any]]]:
    def built(**kwargs: Any) -> Callable[[], dict[str, Any]]:
        return lambda: build_preflight_result(workspace=root, **kwargs).model_dump(mode="json")

    return {
        "no_inputs": built(),
        "empty_plan_object": built(plan={}),
        "empty_changed_files_plan": built(plan={"changed_files": []}),
        "blank_changed_file": built(changed_files=[""]),
        "empty_changed_files_flag": built(changed_files=[]),
        "mcp_no_inputs": lambda: shipgate_preflight(workspace=str(root)),
        "mcp_empty_plan": lambda: shipgate_preflight(workspace=str(root), plan={"changed_files": []}),
    }


@pytest.mark.parametrize(
    "form",
    [
        "no_inputs",
        "empty_plan_object",
        "empty_changed_files_plan",
        "blank_changed_file",
        "empty_changed_files_flag",
        "mcp_no_inputs",
        "mcp_empty_plan",
    ],
)
def test_an_empty_plan_completes_planning_and_authorizes_nothing(tmp_path, form):
    payload = _empty_plan_forms(_workspace(tmp_path))[form]()

    assert payload["preflight_schema_version"] == "0.6"
    control = payload["control"]
    assert control["state"] == "planning_complete"
    assert control["next_action"] is None
    assert control["allowed_next_commands"] == []
    assert control["verify_required"] is False
    assert control["must_stop"] is False
    _assert_authorizes_nothing(payload)
    assert payload["requires_verify"] is False
    assert payload["verification_command"] is None
    assert payload["first_next_action"]["kind"] == "continue"
    assert "authorizes no edit" in control["reason"]
    assert not list(_validator().iter_errors(payload))


def test_cli_empty_plan_says_it_authorizes_nothing(tmp_path):
    root = _workspace(tmp_path)

    json_result = runner.invoke(
        app, ["preflight", "--workspace", str(root), "--plan", "-", "--json"], input=""
    )
    text_result = runner.invoke(
        app, ["preflight", "--workspace", str(root), "--plan", "-"], input=""
    )

    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["control"]["state"] == "planning_complete"
    _assert_authorizes_nothing(payload)
    assert text_result.exit_code == 0, text_result.output
    assert "Agents Shipgate preflight: planning complete" in text_result.output
    assert "Authorizes: nothing (only verify can authorize merge or completion)" in (
        text_result.output
    )


def test_a_docs_only_plan_still_routes_to_verify_and_authorizes_nothing(tmp_path):
    payload = build_preflight_result(
        workspace=_workspace(tmp_path), changed_files=["README.md"]
    ).model_dump(mode="json")

    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["next_action"]["kind"] == "verify"
    assert payload["requires_verify"] is True
    _assert_authorizes_nothing(payload)


def _route_payloads(tmp_path: Path) -> dict[str, dict[str, Any]]:
    root = _workspace(tmp_path)
    return {
        "planning_complete": build_preflight_result(workspace=root),
        "agent_action_required": build_preflight_result(
            workspace=root, changed_files=["README.md"]
        ),
        "human_review_required": build_preflight_result(
            workspace=root, changed_files=["shipgate.yaml"]
        ),
    }


def test_every_route_validates_against_the_published_schema(tmp_path):
    payloads = {
        state: result.model_dump(mode="json")
        for state, result in _route_payloads(tmp_path).items()
    }

    for state, payload in payloads.items():
        assert payload["control"]["state"] == state
        _assert_authorizes_nothing(payload)
        assert not list(_validator().iter_errors(payload)), state
        assert PreflightResultV6.model_validate(payload).model_dump(mode="json") == payload


def _set_permission(field: str, state: str = "planning_complete"):
    def mutate(payloads):
        payload = payloads[state]
        payload["control"]["permissions"][field] = True
        return payload

    return mutate


def _as_shared_complete(payloads):
    # The exact shape an empty plan produced before 0.6.
    payload = payloads["planning_complete"]
    payload["control"].update(state="complete", completion_allowed=True)
    payload["control"]["permissions"] = dict.fromkeys(PERMISSION_FIELDS, True)
    return payload


def _without_permissions(payloads):
    payload = payloads["planning_complete"]
    del payload["control"]["permissions"]
    return payload


def _planning_owes_verify(payloads):
    payload = payloads["planning_complete"]
    payload["requires_verify"] = True
    return payload


def _publish_only_verify_route(payloads):
    payload = payloads["agent_action_required"]
    payload["control"]["permissions"].update(edit=True, commit=True, push=True, update_pr=True)
    return payload


def _as_review_publishable(payloads):
    payload = payloads["human_review_required"]
    control = payload["control"]
    control.update(state="review_publishable", must_stop=False, stop_reason=None)
    control["next_action"]["kind"] = "review"
    control["permissions"].update(edit=True, commit=True, push=True, update_pr=True)
    return payload


NEGATIVE_CONTROLS = {
    **{f"planning_grants_{field}": _set_permission(field) for field in PERMISSION_FIELDS},
    "pre_0_6_shared_complete": _as_shared_complete,
    "planning_without_permissions": _without_permissions,
    "planning_owes_verify": _planning_owes_verify,
    "verify_route_publishes": _publish_only_verify_route,
    "human_route_grants_merge": _set_permission("merge", "human_review_required"),
    "review_publishable": _as_review_publishable,
}


@pytest.mark.parametrize("mutation", sorted(NEGATIVE_CONTROLS))
def test_a_payload_that_claims_authority_is_refused_by_model_and_schema(tmp_path, mutation):
    payloads = {
        state: result.model_dump(mode="json")
        for state, result in _route_payloads(tmp_path).items()
    }
    # Positive first: the untampered payloads are what both validators accept.
    for payload in payloads.values():
        assert not list(_validator().iter_errors(payload))

    tampered = NEGATIVE_CONTROLS[mutation](copy.deepcopy(payloads))

    assert list(_validator().iter_errors(tampered)), mutation
    with pytest.raises(ValidationError):
        PreflightResultV6.model_validate(tampered)


def _pre_0_6_empty_plan_payload(tmp_path: Path) -> dict[str, Any]:
    payload = build_preflight_result(workspace=_workspace(tmp_path)).model_dump(mode="json")
    payload["preflight_schema_version"] = "0.5"
    payload["control"] = {
        "state": "complete",
        "reason": payload["first_next_action"]["why"],
        "completion_allowed": True,
        "must_stop": False,
        "verify_required": False,
        "next_action": None,
        "allowed_next_commands": [],
        "permissions": dict.fromkeys(PERMISSION_FIELDS, True),
        "human_review": {"required": False, "why": None, "required_reviewers": []},
        "stop_reason": None,
    }
    return payload


def test_a_stored_0_5_answer_still_reads_as_what_it_was(tmp_path):
    stored = _pre_0_6_empty_plan_payload(tmp_path)

    # Predecessor compatibility: the frozen grammar and model keep reading it.
    assert not list(_validator(FROZEN_SCHEMA).iter_errors(stored))
    assert isinstance(parse_preflight_result(stored), PreflightResultV5)
    assert not isinstance(parse_preflight_result(stored), PreflightResultV6)

    # Relabelled as current, the same claim is refused.
    relabelled = {**stored, "preflight_schema_version": "0.6"}
    assert list(_validator().iter_errors(relabelled))
    with pytest.raises(ValidationError):
        parse_preflight_result(relabelled)


def test_replaying_a_stored_complete_answer_cannot_clear_a_route(tmp_path):
    stored = _pre_0_6_empty_plan_payload(tmp_path)
    root = tmp_path / "repo"

    protected = build_preflight_result(
        workspace=root, changed_files=["shipgate.yaml"], base_preflight=stored
    )
    docs_only = build_preflight_result(
        workspace=root, changed_files=["README.md"], base_preflight=stored
    )
    empty = build_preflight_result(workspace=root, base_preflight=stored)

    assert protected.control.state == "human_review_required"
    assert docs_only.control.state == "agent_action_required"
    assert empty.control.state == "planning_complete"
    for result in (protected, docs_only, empty):
        _assert_authorizes_nothing(result.model_dump(mode="json"))


def test_an_empty_plan_does_not_clear_a_host_grant_drift_route(tmp_path):
    root = _workspace(tmp_path)
    baseline = build_host_grants_baseline(host_audit_inventory(root))
    baseline_path = root / ".agents-shipgate" / "host-grants.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(*)"]}}), encoding="utf-8"
    )

    payload = build_preflight_result(workspace=root, plan={}).model_dump(mode="json")

    assert payload["changed_files"] == []
    assert payload["control"]["state"] == "human_review_required"
    _assert_authorizes_nothing(payload)
    assert not list(_validator().iter_errors(payload))


def test_an_empty_plan_does_not_clear_a_trust_root_drift_route(tmp_path):
    root = _workspace(tmp_path)
    base = build_preflight_result(workspace=root)
    # A new trust root, not a prose edit: unchanged instruction structure is
    # deliberately not drift (#612).
    (root / "policies").mkdir()
    (root / "policies" / "release.yaml").write_text("rules: []\n", encoding="utf-8")

    result = build_preflight_result(workspace=root, base_preflight=base)

    assert result.changed_files == []
    assert result.trust_root_graph_diff is not None and result.trust_root_graph_diff.changed
    assert result.control.state == "human_review_required"
    _assert_authorizes_nothing(result.model_dump(mode="json"))


def test_the_cli_reads_a_current_answer_back_as_a_base(tmp_path):
    root = _workspace(tmp_path)
    saved = tmp_path / "base.json"
    first = runner.invoke(app, ["preflight", "--workspace", str(root), "--json"])
    assert first.exit_code == 0, first.output
    saved.write_text(first.output, encoding="utf-8")

    second = runner.invoke(
        app,
        ["preflight", "--workspace", str(root), "--base-preflight", str(saved), "--json"],
    )

    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    assert payload["trust_root_graph_diff"]["changed"] is False
    assert payload["control"]["state"] == "planning_complete"


@pytest.mark.parametrize("value", [["not", "an", "object"], "0.6", 7])
def test_a_base_that_is_not_a_json_object_is_refused_by_name(tmp_path, value):
    from agents_shipgate.core.errors import ConfigError

    with pytest.raises(ConfigError, match="expected a JSON object"):
        build_preflight_result(workspace=_workspace(tmp_path), base_preflight=value)
