from __future__ import annotations

import json

import pytest
from test_openshell_inputs import POLICY, REGISTRATION, selection
from test_partial_host_comparison import _git, _repository
from typer.testing import CliRunner

from agents_shipgate.cli.main import app


def invoke(root, *args):
    result = CliRunner().invoke(app, [*args, "--workspace", str(root)])
    assert result.exit_code in (0, 10, 20), result.output
    return json.loads(result.output)


@pytest.mark.parametrize("actor", ["codex", "claude-code", "cursor"])
def test_audit_transition_routes_same_selected_policy_for_every_caller(tmp_path, actor):
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY},
        {"arbitrary.rules": POLICY.replace("enforcement: enforce", "enforcement: audit")})
    checked = invoke(root, "check", "--agent", actor, "--base", "main", "--head", "HEAD",
                     "--format", "agent-boundary-json")
    assert "openshell" in checked["affected_hosts"]
    assert checked["input_coverage"] == "complete"
    violation, = [row for row in checked["violations"] if row["evidence"]["kind"] == "openshell_policy_changed"]
    assert violation["evidence"]["direction"] == "widened"
    assert checked["control"]["state"] != "complete"
    diff = invoke(root, "diff", "--base", "main", "--json")
    preview = invoke(root, "verify", "--preview", "--base", "main", "--head", "HEAD", "--json")
    verified = invoke(root, "verify", "--base", "main", "--head", "HEAD", "--json")
    assert diff["rows"] == preview["host_comparison"]["rows"] == verified["host_comparison"]["rows"]
    assert any(row["expands"] for row in diff["rows"])
    handoff = json.loads((root / "agents-shipgate-reports/agent-handoff.json").read_text())
    assert handoff["control"]["state"] != "complete"


def test_proven_narrowing_names_direction_and_never_clears_other_trust_roots(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY.replace("enforcement: enforce", "enforcement: audit")},
        {"arbitrary.rules": POLICY, "AGENTS.md": "new instructions"})
    checked = invoke(root, "check", "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json")
    assert any(row["direction"] == "narrowed" for row in checked["rows"])
    assert checked["control"]["state"] != "complete"


@pytest.mark.parametrize("value", ["version: [", POLICY.replace("enforcement: enforce", "enforcement: mystery")])
def test_changed_unread_policy_cannot_complete_without_grants(tmp_path, value):
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY}, {"arbitrary.rules": value})
    checked = invoke(root, "check", "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json")
    assert checked["input_coverage"] == "partial"
    assert checked["control"]["permissions"]["report_complete"] is False


def test_committed_check_ignores_dirty_policy_content(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY},
        {"arbitrary.rules": POLICY.replace("enforcement: enforce", "enforcement: audit")})
    (root / "arbitrary.rules").write_text("version: [")
    checked = invoke(root, "check", "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json")
    assert checked["input_coverage"] == "complete"
    assert any(row["evidence"].get("direction") == "widened" for row in checked["violations"])


def test_selection_removal_keeps_the_base_selected_input_visible(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY}, {"README.md": "change"})
    (root / REGISTRATION).unlink()
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "remove selection")
    checked = invoke(root, "check", "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json")
    assert "openshell" in checked["affected_hosts"]
    assert checked["control"]["state"] != "complete"


def test_selected_docs_filename_overrides_docs_only_trigger(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection("README.md"), "README.md": POLICY},
        {"README.md": POLICY.replace("enforcement: enforce", "enforcement: audit")})
    triggered = invoke(root, "trigger", "--base", "main", "--head", "HEAD", "--json")
    assert triggered["run_shipgate"] is True
    assert any(row["id"] == "TRIGGER-OPENSHELL-SELECTED-INPUT" for row in triggered["matched_rules"])


def test_preflight_protects_exact_selected_filename_and_binds_its_graph(tmp_path):
    from agents_shipgate.core.preflight import build_trust_root_graph, classify_protected_touches

    root = _repository(tmp_path, {REGISTRATION: selection("policy[1]"), "policy[1]": POLICY,
        "policy1": POLICY}, {"README.md": "change"})
    touches = classify_protected_touches(["policy[1]", "policy1"], workspace=root)
    assert [touch.path for touch in touches] == ["policy[1]"]
    first = build_trust_root_graph(root)
    node, = [node for node in first.nodes if node.pattern == "policy[1]"]
    assert node.present_paths == ["policy[1]"]
    (root / "policy[1]").write_text(POLICY.replace("enforcement: enforce", "enforcement: audit"))
    assert first.graph_hash != build_trust_root_graph(root).graph_hash


def test_link_retarget_with_same_policy_bytes_still_requires_reference_review(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection("link"), "one": POLICY, "two": POLICY},
        {"README.md": "change"}, links={"link": "one"})
    (root / "link").unlink()
    (root / "link").symlink_to("two")
    checked = invoke(root, "check", "--base", "main", "--format", "agent-boundary-json")
    assert checked["control"]["state"] != "complete"
    assert any(row["path"] == "link" for row in checked["violations"])


def test_configured_verifier_publishes_review_and_sarif_for_audit_transition(tmp_path):
    from pathlib import Path

    sample = Path(__file__).resolve().parent.parent / "samples/clean_read_only_agent"
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY,
        "shipgate.yaml": (sample / "shipgate.yaml").read_text(),
        "tools.json": (sample / "tools.json").read_text()},
        {"arbitrary.rules": POLICY.replace("enforcement: enforce", "enforcement: audit")})
    verified = invoke(root, "verify", "--base", "main", "--head", "HEAD", "--json")
    assert verified["release_decision"]["decision"] == "review_required"
    assert verified["control"]["permissions"]["merge"] is False
    report = json.loads((root / "agents-shipgate-reports/report.json").read_text())
    assert any(row["evidence"].get("direction") == "widened" for row in report["findings"])
    sarif = json.loads((root / "agents-shipgate-reports/report.sarif").read_text())
    assert any(row["ruleId"] == "SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED"
               for row in sarif["runs"][0]["results"])
