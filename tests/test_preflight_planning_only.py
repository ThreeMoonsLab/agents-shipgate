"""An empty preflight plan completes planning and authorizes nothing (#610).

Before preflight ``0.6`` a plan that named nothing returned the shared
``complete`` state, whose permission vector grants merge and completion, with
no evaluation of any change behind it. These tests pin the replacement from
both sides: the runtime payload for every route, and the published schema it
claims, accepting the real payloads first and only then refusing each tampered
one.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from test_current_control import repo as repo  # noqa: F401  (fixture)
from typer.testing import CliRunner

from agents_shipgate.cli.install_hooks import _hook_script_text
from agents_shipgate.cli.main import app
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.host_grants import build_host_grants_baseline, host_audit_inventory
from agents_shipgate.core.preflight import build_preflight_result
from agents_shipgate.mcp_server.server import shipgate_preflight
from agents_shipgate.schemas.agent_control import PERMISSION_FIELDS
from agents_shipgate.schemas.preflight import (
    PreflightResultV5,
    PreflightResultV6,
    parse_preflight_result,
)
from tests.test_preflight import _workspace as _preflight_workspace
from tests.test_preflight import _write

ROOT = Path(__file__).resolve().parent.parent
CURRENT = Draft202012Validator(
    json.loads((ROOT / "docs" / "preflight-schema.v0.6.json").read_text(encoding="utf-8"))
)
FROZEN_0_5 = Draft202012Validator(
    json.loads((ROOT / "docs" / "preflight-schema.v0.5.json").read_text(encoding="utf-8"))
)
runner = CliRunner()


def _workspace(tmp_path: Path) -> Path:
    root = _preflight_workspace(tmp_path)
    _write(root, "README.md", "Support agent.\n")
    return root


def _assert_authorizes_nothing(payload: dict[str, Any]) -> None:
    control = payload["control"]
    assert control["completion_allowed"] is False
    assert set(control["permissions"]) == set(PERMISSION_FIELDS)
    assert not any(control["permissions"].values())


def _built(**kwargs: Any) -> Callable[[Path], dict[str, Any]]:
    return lambda root: build_preflight_result(workspace=root, **kwargs).model_dump(mode="json")


EMPTY_PLAN_FORMS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "no_inputs": _built(),
    "empty_plan_object": _built(plan={}),
    "empty_changed_files_plan": _built(plan={"changed_files": []}),
    "blank_changed_file": _built(changed_files=[""]),
    "empty_changed_files_flag": _built(changed_files=[]),
    "mcp_no_inputs": lambda root: shipgate_preflight(workspace=str(root)),
    "mcp_empty_plan": lambda root: shipgate_preflight(
        workspace=str(root), plan={"changed_files": []}
    ),
}


@pytest.mark.parametrize("form", sorted(EMPTY_PLAN_FORMS))
def test_an_empty_plan_completes_planning_and_authorizes_nothing(tmp_path, form):
    payload = EMPTY_PLAN_FORMS[form](_workspace(tmp_path))

    assert payload["preflight_schema_version"] == "0.6"
    control = payload["control"]
    assert control["state"] == "planning_only"
    assert control["next_action"] is None
    assert control["allowed_next_commands"] == []
    assert control["verify_required"] is False
    assert control["must_stop"] is False
    _assert_authorizes_nothing(payload)
    assert payload["requires_verify"] is False
    assert payload["verification_command"] is None
    assert payload["first_next_action"]["kind"] == "continue"
    assert "authorizes no edit" in control["reason"]
    assert not list(CURRENT.iter_errors(payload))


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
    assert payload["control"]["state"] == "planning_only"
    _assert_authorizes_nothing(payload)
    assert text_result.exit_code == 0, text_result.output
    assert "Agents Shipgate preflight: planning only" in text_result.output
    assert "Authorizes: nothing" in text_result.output


def test_a_docs_only_plan_still_routes_to_verify_and_authorizes_nothing(tmp_path):
    payload = _built(changed_files=["README.md"])(_workspace(tmp_path))

    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["next_action"]["kind"] == "verify"
    assert payload["requires_verify"] is True
    _assert_authorizes_nothing(payload)


def _route_payloads(tmp_path: Path) -> dict[str, dict[str, Any]]:
    root = _workspace(tmp_path)
    return {
        "planning_only": _built()(root),
        "agent_action_required": _built(changed_files=["README.md"])(root),
        "human_review_required": _built(changed_files=["shipgate.yaml"])(root),
    }


def test_every_route_validates_against_the_published_schema(tmp_path):
    for state, payload in _route_payloads(tmp_path).items():
        assert payload["control"]["state"] == state
        _assert_authorizes_nothing(payload)
        assert not list(CURRENT.iter_errors(payload)), state
        assert PreflightResultV6.model_validate(payload).model_dump(mode="json") == payload


def _set_permission(field: str, state: str = "planning_only"):
    def mutate(payloads):
        payload = payloads[state]
        payload["control"]["permissions"][field] = True
        return payload

    return mutate


def _as_shared_complete(payloads):
    # The exact shape an empty plan produced before 0.6.
    payload = payloads["planning_only"]
    payload["control"].update(state="complete", completion_allowed=True)
    payload["control"]["permissions"] = dict.fromkeys(PERMISSION_FIELDS, True)
    return payload


def _vector(state: str, vector: dict[str, bool] | None):
    def mutate(payloads):
        payload = payloads[state]
        if vector is None:
            del payload["control"]["permissions"]
        else:
            payload["control"]["permissions"] = vector
        return payload

    return mutate


def _planning_carries(field: str):
    # "Nothing to route" beside what the protected-surface route carries is an
    # answer that contradicts itself, and a reader switching on the state alone
    # would skip the stop (#610 review).
    def mutate(payloads):
        payload = payloads["planning_only"]
        payload[field] = copy.deepcopy(payloads["human_review_required"][field])
        assert payload[field], field
        return payload

    return mutate


def _planning_owes_verify(payloads):
    payload = payloads["planning_only"]
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
    "planning_owes_verify": _planning_owes_verify,
    **{
        f"planning_carries_{field}": _planning_carries(field)
        for field in ("changed_files", "protected_surface_touches", "signals")
    },
    "verify_route_publishes": _publish_only_verify_route,
    "human_route_grants_merge": _set_permission("merge", "human_review_required"),
    "review_publishable": _as_review_publishable,
    # A stored 0.6 vector must state all six permissions on every route; the
    # model refuses what the schema refuses rather than filling it in.
    **{
        f"{state}_{name}_vector": _vector(state, vector)
        for state in ("planning_only", "agent_action_required", "human_review_required")
        for name, vector in (("absent", None), ("empty", {}), ("partial", {"merge": False}))
    },
}


@pytest.mark.parametrize("mutation", sorted(NEGATIVE_CONTROLS))
def test_a_payload_that_claims_authority_is_refused_by_model_and_schema(tmp_path, mutation):
    payloads = _route_payloads(tmp_path)
    # Positive first: the untampered payloads are what both validators accept.
    for payload in payloads.values():
        assert not list(CURRENT.iter_errors(payload))

    tampered = NEGATIVE_CONTROLS[mutation](copy.deepcopy(payloads))

    assert list(CURRENT.iter_errors(tampered)), mutation
    with pytest.raises(ValidationError):
        PreflightResultV6.model_validate(tampered)


def _pre_0_6_empty_plan_payload(tmp_path: Path) -> dict[str, Any]:
    payload = _as_shared_complete({"planning_only": _built()(_workspace(tmp_path))})
    payload["preflight_schema_version"] = "0.5"
    return payload


def test_a_stored_0_5_answer_still_reads_as_what_it_was(tmp_path):
    stored = _pre_0_6_empty_plan_payload(tmp_path)

    # Predecessor compatibility: the frozen grammar and model keep reading it.
    # (0.5 accepted this merge-granting answer; the pin on update_pr applied
    # only while the state was not ``complete``.)
    assert not list(FROZEN_0_5.iter_errors(stored))
    assert isinstance(parse_preflight_result(stored), PreflightResultV5)
    assert not isinstance(parse_preflight_result(stored), PreflightResultV6)

    # Relabelled as current, the same claim is refused.
    relabelled = {**stored, "preflight_schema_version": "0.6"}
    assert list(CURRENT.iter_errors(relabelled))
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
    assert empty.control.state == "planning_only"
    for result in (protected, docs_only, empty):
        _assert_authorizes_nothing(result.model_dump(mode="json"))


def test_an_empty_plan_does_not_clear_a_host_grant_drift_route(tmp_path):
    root = _workspace(tmp_path)
    baseline = build_host_grants_baseline(host_audit_inventory(root))
    _write(root, ".agents-shipgate/host-grants.json", json.dumps(baseline, indent=2, sort_keys=True) + "\n")
    _write(root, ".claude/settings.json", json.dumps({"permissions": {"allow": ["Bash(*)"]}}))

    payload = _built(plan={})(root)

    assert payload["changed_files"] == []
    assert payload["control"]["state"] == "human_review_required"
    _assert_authorizes_nothing(payload)
    assert not list(CURRENT.iter_errors(payload))


def test_an_empty_plan_does_not_clear_a_trust_root_drift_route(tmp_path):
    root = _workspace(tmp_path)
    base = build_preflight_result(workspace=root)
    # A new trust root, not a prose edit: unchanged instruction structure is
    # deliberately not drift (#612).
    _write(root, "policies/release.yaml", "rules: []\n")

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
    assert payload["control"]["state"] == "planning_only"


def test_a_base_held_in_any_mapping_is_read(tmp_path):
    from types import MappingProxyType

    root = _workspace(tmp_path)
    stored = _built()(root)

    result = build_preflight_result(
        workspace=root, changed_files=["README.md"], base_preflight=MappingProxyType(stored)
    )

    assert result.trust_root_graph_diff is not None
    assert result.trust_root_graph_diff.changed is False


@pytest.mark.parametrize("value", [["not", "an", "object"], "0.6", 7])
def test_a_base_that_is_not_a_json_object_is_refused_by_name(tmp_path, value):
    with pytest.raises(ConfigError, match="expected a JSON object"):
        build_preflight_result(workspace=_workspace(tmp_path), base_preflight=value)


@pytest.mark.parametrize("version", [["0.6"], {"v": 1}, None, 6])
def test_a_base_whose_version_is_not_a_string_is_a_config_error(tmp_path, version):
    root = _workspace(tmp_path)
    stored = {**_built()(root), "preflight_schema_version": version}

    with pytest.raises(ConfigError, match="Invalid base preflight"):
        build_preflight_result(workspace=root, base_preflight=stored)

    saved = tmp_path / "base.json"
    saved.write_text(json.dumps(stored), encoding="utf-8")
    result = runner.invoke(
        app, ["preflight", "--workspace", str(root), "--base-preflight", str(saved), "--json"]
    )
    # Exit 2, a named input error, never exit 4's "internal error".
    assert result.exit_code == 2, result.output
    assert "Invalid Base preflight" in result.output


def _hook_with_mutation(tmp_path: Path, mutation: dict[str, Any]) -> dict[str, Any]:
    """The generated hook, asking the real CLI through a wrapper that changes
    one field of its genuine answer, so nothing else about the proof differs."""

    wrapper = tmp_path / "mutating_cli.py"
    wrapper.write_text(
        "import json, subprocess, sys\n"
        f"real = {str(ROOT / 'shipgate')!r}\n"
        f"mutation = json.loads({json.dumps(json.dumps(mutation))})\n"
        "done = subprocess.run([sys.executable, real, *sys.argv[1:]],\n"
        "                      input=sys.stdin.buffer.read(), capture_output=True)\n"
        "answer = json.loads(done.stdout)\n"
        "if 'version' in mutation:\n"
        "    answer['preflight_schema_version'] = mutation['version']\n"
        "if 'kind' in mutation:\n"
        "    answer['protected_surface_touches'][0]['kind'] = mutation['kind']\n"
        "sys.stdout.write(json.dumps(answer))\n",
        encoding="utf-8",
    )
    namespace: dict[str, Any] = {"__name__": "hook_under_test"}
    exec(compile(_hook_script_text(), "generated-hook.py", "exec"), namespace)
    namespace["_cli"] = lambda: [sys.executable, str(wrapper)]
    return namespace


@pytest.mark.parametrize(
    "mutation",
    [{}, {"version": ["0.6"]}, {"version": {"v": "0.6"}}, {"kind": ["agent_instructions"]}],
    ids=["genuine_proof", "version_list", "version_object", "touch_kind_list"],
)
def test_the_generated_hook_keeps_the_prompt_for_a_malformed_answer(
    repo, tmp_path, capsys, mutation
):
    # Set membership hashed these values and crashed the hook, which Claude
    # Code treats as a non-blocking error: the edit went ahead unprompted.
    target = repo / "AGENTS.md"
    target.write_text("Explain the result.\n", encoding="utf-8")
    hook = _hook_with_mutation(tmp_path, mutation)
    event = {"tool_name": "Edit", "cwd": str(repo), "tool_input": {
        "file_path": str(target),
        "old_string": "Explain the result.", "new_string": "Explain the outcome.",
    }}
    args = argparse.Namespace(
        config="shipgate.yaml", base="origin/main", head="", ci_mode="advisory"
    )

    hook["_pretooluse"](event, repo, args)

    out = capsys.readouterr().out
    if not mutation:
        # The genuine proof of an unchanged instruction structure: no prompt.
        assert out == ""
    else:
        assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask"
