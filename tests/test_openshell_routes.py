from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_openshell_inputs import POLICY, REGISTRATION, selection
from test_partial_host_comparison import _git, _repository
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.errors import InputParseError


def configured_source():
    sample = Path(__file__).resolve().parent.parent / "samples/clean_read_only_agent"
    return {".gitignore": "agents-shipgate-reports/\n",
            **{name: (sample / name).read_text() for name in ("shipgate.yaml", "tools.json")}}


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
    assert verified["base_status"] == "succeeded"
    assert verified["head_status"] == "succeeded"
    assert verified["release_decision"]["decision"] == "review_required"
    assert verified["control"]["permissions"]["merge"] is False
    report = json.loads((root / "agents-shipgate-reports/report.json").read_text())
    assert any(row["evidence"].get("direction") == "widened" for row in report["findings"])
    sarif = json.loads((root / "agents-shipgate-reports/report.sarif").read_text())
    assert any(row["ruleId"] == "SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED"
               for row in sarif["runs"][0]["results"])


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
@pytest.mark.parametrize("actor", ["codex", "claude-code", "cursor"])
@pytest.mark.parametrize("expanded", ["policy-link", "independent.rules"])
def test_link_retarget_keeps_reference_review_and_every_supported_expansion(tmp_path, actor, expanded):
    audit = POLICY.replace("enforcement: enforce", "enforcement: audit")
    registration = selection("policy-link")
    registration["policies"].append({"path": "independent.rules", "role": "authored"})
    root = _repository(tmp_path, {REGISTRATION: registration, "one": POLICY,
        "two": audit if expanded == "policy-link" else POLICY,
        "independent.rules": POLICY, **configured_source()},
        {"independent.rules": audit} if expanded == "independent.rules" else {"README.md": "change"},
        links={"policy-link": "one"})
    (root / "policy-link").unlink()
    (root / "policy-link").symlink_to("two")
    _git(root, "add", "policy-link")
    _git(root, "commit", "-m", "retarget selected policy")
    checked = invoke(root, "check", "--agent", actor, "--base", "main", "--head", "HEAD",
                     "--format", "agent-boundary-json")
    assert any(row["path"] == "policy-link" and row["evidence"].get("direction") == "unknown"
               for row in checked["violations"])
    assert any(row["path"] == expanded and row["evidence"].get("direction") == "widened"
               for row in checked["violations"])
    assert checked["control"]["permissions"]["merge"] is False
    assert checked["control"]["permissions"]["report_complete"] is False
    diff = invoke(root, "diff", "--base", "main", "--json")
    assert any(row["subject"] == f"openshell {expanded}" and row["expands"] for row in diff["rows"])
    verified = invoke(root, "verify", "--base", "main", "--head", "HEAD", "--json")
    assert verified["release_decision"]["decision"] == "review_required"
    report = json.loads((root / "agents-shipgate-reports/report.json").read_text())
    assert any(row["source"]["path"] == expanded and row["evidence"].get("direction") == "widened"
               for row in report["findings"])


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
@pytest.mark.parametrize("mutation", ["retarget", "policy"])
def test_committed_selected_link_receipt_binds_hops_and_rejects_live_drift(tmp_path, mutation):
    from test_current_control import _live

    from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control

    root = _repository(tmp_path, {REGISTRATION: selection("policy-link"), "one": POLICY,
        "two": POLICY, **configured_source()},
        {"two": POLICY.replace("enforcement: enforce", "enforcement: audit")},
        links={"policy-link": "middle", "middle": "one"})
    (root / "middle").unlink()
    (root / "middle").symlink_to("two")
    _git(root, "add", "middle")
    _git(root, "commit", "-m", "retarget selected chain")
    verified = invoke(root, "verify", "--base", "main", "--head", "HEAD", "--json")
    assert verified["base_status"] == verified["head_status"] == "succeeded"
    out = root / "agents-shipgate-reports"
    plan = json.loads((out / "verification-plan.json").read_text())
    dependencies = plan["inputs"]["options"]["dependency_inputs"]
    assert {item["path"] for item in dependencies["links"]} == {"policy-link", "middle"}
    assert {REGISTRATION, "two"} <= {item["path"] for item in dependencies["files"]}
    read_current_control(out, live=lambda: _live(root))
    if mutation == "retarget":
        (root / "middle").unlink()
        (root / "middle").symlink_to("one")
    else:
        (root / "two").write_text(POLICY)
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(out, live=lambda: _live(root))


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
@pytest.mark.parametrize("mutation", ["retarget", "policy"])
def test_portable_prepare_binds_unchanged_nested_selection_and_worker_rejects_drift(tmp_path, mutation):
    registration = "project/.shipgate/openshell.json"
    root = _repository(tmp_path, {registration: selection("selected-link"), "one": POLICY,
        "two": POLICY, **configured_source()}, {"README.md": "unrelated change"},
        links={"selected-link": "middle", "middle": "one"})
    out = root / "agents-shipgate-reports"
    plan_path = out / "verification-plan.json"
    invoke(root, "verification", "prepare", "--base", "main", "--head", "HEAD", "--out", str(plan_path))
    plan = json.loads(plan_path.read_text())
    dependencies = plan["inputs"]["options"]["dependency_inputs"]
    assert {item["path"] for item in dependencies["links"]} == {"selected-link", "middle"}
    assert {registration, "one"} <= {item["path"] for item in dependencies["files"]}
    invoke(root, "verification", "worker", "--plan", str(plan_path), "--out", str(out / "unit.json"))
    if mutation == "retarget":
        (root / "middle").unlink()
        (root / "middle").symlink_to("two")
    else:
        (root / "one").write_text(POLICY.replace("enforcement: enforce", "enforcement: audit"))
    replay = CliRunner().invoke(app, ["verification", "worker", "--workspace", str(root),
        "--plan", str(plan_path), "--out", str(out / "changed-unit.json")])
    assert replay.exit_code != 0
    assert isinstance(replay.exception, InputParseError)
    assert "changed since verification" in str(replay.exception)
    assert not (out / "changed-unit.json").exists()
