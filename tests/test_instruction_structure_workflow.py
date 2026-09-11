"""Same real committed change through final, local, host and proactive routes."""

from __future__ import annotations

import difflib
import json

import pytest
from test_current_control import _git, _live, _verify
from test_current_control import repo as repo

from agents_shipgate.core.agent_boundary import build_agent_boundary_result, evaluate_agent_boundary
from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.core.host_grants import (
    build_host_drift_payload,
    build_host_grants_baseline,
    host_audit_inventory,
    host_grants_sha256,
)
from agents_shipgate.core.preflight import build_preflight_result


def _diff(path, before, after):
    return f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))


@pytest.mark.parametrize("path,before,after", [
    ("AGENTS.md", "You must run Shipgate.\n", "Explain the result.\n"),
    ("CLAUDE.md", "Run the tests.\n", "Explain the shell examples.\n"),
    (".cursor/rules/demo.mdc", "---\nalwaysApply: true\n---\nOld prose.\n", "---\nalwaysApply: true\n---\nNew prose.\n"),
    (".claude/skills/demo/SKILL.md", "---\nname: demo\ndescription: Fixture\nallowed-tools: Read\n---\nOld prose.\n", "---\nname: demo\ndescription: Fixture\nallowed-tools: Read\n---\nNew prose.\n"),
])
def test_committed_prose_change_keeps_all_review_routes_aligned(repo, tmp_path, path, before, after):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(before)
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", "instruction base")
    baseline = build_host_grants_baseline(host_audit_inventory(repo))
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))
    base_preflight = build_preflight_result(workspace=repo)
    target.write_text(after)
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", "prose change")
    diff = _diff(path, before, after)

    local = build_agent_boundary_result(evaluate_agent_boundary(
        workspace=repo, diff_text=diff, config_path=repo / "shipgate.yaml",
    ))
    assert local.control.state == "complete"
    preflight = build_preflight_result(
        workspace=repo, changed_files=[path], diff_text=diff,
        base_preflight=base_preflight, host_baseline=baseline_path,
    )
    assert not preflight.requires_human_review
    assert preflight.control.state == "agent_action_required"
    assert not preflight.trust_root_graph_diff.changed
    assert preflight.trust_root_graph_hash != base_preflight.trust_root_graph_hash
    assert preflight.host_grant_drift["has_drift"] is False

    report, _verifier, exit_code = _verify(repo, base="HEAD~1", head="HEAD")
    assert exit_code == 0
    assert report.release_decision.decision == "passed"
    control = read_current_control(repo / "agents-shipgate-reports", live=_live(repo))
    assert control.pointer.control.state == "complete"
    target.write_text(after + "One more prose edit.\n")
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(repo / "agents-shipgate-reports", live=_live(repo))


def test_legacy_instruction_baseline_remains_incomparable(repo):
    (repo / "AGENTS.md").write_text("Explain the result.\n")
    current = host_audit_inventory(repo)
    legacy = build_host_grants_baseline(current)
    # Reconstruct the published 0.2 shape before the additive projection. This
    # is a synthetic compatibility fixture, never a user baseline migration.
    for artifact in legacy["inventory"]["artifacts"]:
        artifact.pop("instruction_structure", None)
    legacy["inventory_sha256"] = host_grants_sha256(legacy["inventory"])
    drift = build_host_drift_payload(baseline=legacy, inventory=current, baseline_file="legacy.json")
    assert drift["comparison_status"] == "incomparable"
    assert "baseline_instruction_structure_unavailable" in drift["incomparable_reasons"]
    assert drift["has_drift"] is None


@pytest.mark.parametrize("contents", [
    "---\nhooks: [\n---\nText\n",
    "---\nname: demo\ndescription: Fixture\nhooks: 42\n---\nText\n",
])
def test_unchanged_malformed_skill_stays_visible_to_coverage_and_review(repo, contents):
    path = ".claude/skills/demo/SKILL.md"
    target = repo / path
    target.parent.mkdir(parents=True)
    target.write_text(contents)
    inventory = host_audit_inventory(repo)
    assert any(item["blocking"] and item["source"] == path for item in inventory["issues"])
    with pytest.raises(ValueError, match="incomplete"):
        build_host_grants_baseline(inventory)
    diff = _diff(path, contents, contents + "New prose.\n")
    local = build_agent_boundary_result(evaluate_agent_boundary(workspace=repo, diff_text=diff))
    assert not local.control.permissions.merge
    proactive = build_preflight_result(workspace=repo, diff_text=diff)
    assert proactive.requires_human_review


def test_prose_preflight_explains_the_instruction_edit_not_a_tool_source_addition(repo):
    path = "AGENTS.md"
    before, after = "Old prose.\n", "New prose.\n"
    (repo / path).write_text(before)
    result = build_preflight_result(workspace=repo, diff_text=_diff(path, before, after))
    signal = next(item for item in result.signals if item.kind == "protected_surface_touch")
    assert "unchanged supported instruction structure" in signal.reason
    assert "exact planned instruction edit" in signal.recommendation
    assert "tool-source" not in signal.reason + signal.recommendation


@pytest.mark.parametrize("body", ["New guidance.", "Explain shell and subprocess examples."])
def test_unresolved_instruction_comparison_never_judges_prose_words(repo, body):
    from agents_shipgate.core.codex_boundary import evaluate_codex_boundary_result

    path = ".agents/skills/demo/SKILL.md"
    before = "---\nname: demo\nhooks: [\n---\nOld prose.\n"
    after = before + body + "\n"
    target = repo / path
    target.parent.mkdir(parents=True)
    target.write_text(before)
    diff = _diff(path, before, after)
    legacy = evaluate_codex_boundary_result(workspace=repo, diff_text=diff)
    current = build_agent_boundary_result(evaluate_agent_boundary(workspace=repo, diff_text=diff))
    for rules in (legacy.violated_rules, current.violations):
        assert "BOUNDARY-INPUT-INCOMPLETE" in {row.id for row in rules}
        assert not {"CODEX-SKILL-COMMAND-CHANGED", "CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED"} & {row.id for row in rules}
    assert legacy.decision != "allow"
    assert current.control.state == "human_review_required"


@pytest.mark.parametrize("path,before,after,check_id", [
    (".claude/settings.json", '{"permissions":{"allow":["Read(src/**)"]}}\n', '{"permissions":{"allow":["Bash(*)"]}}\n', "SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW"),
    (".mcp.json", '{"mcpServers":{}}\n', '{"mcpServers":{"new":{"command":"new-server"}}}\n', "SHIP-HOST-BOUNDARY-MCP-SERVER-ADDED"),
    (".claude/settings.json", '{}\n', '{"hooks":{"PreToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"./review.sh"}]}]}}\n', "SHIP-HOST-BOUNDARY-HOOK-CHANGED"),
    (".github/workflows/agents-shipgate.yml", 'name: Gate\non: pull_request\njobs:\n  gate:\n    runs-on: ubuntu-latest\n    steps:\n      - run: agents-shipgate verify --config shipgate.yaml\n', 'name: Gate\non: pull_request\njobs:\n  gate:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo removed\n', "SHIP-CODEX-BOUNDARY-CI-GATE-REMOVED"),
])
def test_committed_mixed_prose_and_structural_change_retains_its_actual_finding(repo, path, before, after, check_id):
    prose_path = ".claude/skills/review/SKILL.md"
    prose_before = "---\nname: review\ndescription: Fixture\n---\nOld prose.\n"
    prose_after = prose_before.replace("Old prose.", "Explain shell examples.")
    for name, text in [(path, before), (prose_path, prose_before)]:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "mixed base")
    (repo / path).write_text(after)
    (repo / prose_path).write_text(prose_after)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "mixed head")
    diff = _diff(prose_path, prose_before, prose_after) + _diff(path, before, after)
    local = build_agent_boundary_result(evaluate_agent_boundary(workspace=repo, diff_text=diff))
    assert not local.control.permissions.merge
    assert check_id in {row.check_id for row in local.violations}
    assert not any(row.path == prose_path for row in local.violations)
    planned = build_preflight_result(workspace=repo, diff_text=diff)
    assert planned.requires_human_review
    assert next(row for row in planned.protected_surface_touches if row.path == prose_path).instruction_structure_unchanged
    verifier, report, _code = _verify(repo, base="HEAD~1", head="HEAD")
    assert not verifier.control.permissions.merge
    assert check_id in {row.check_id for row in report.findings}
    assert not any(row.evidence.get("changed_file") == prose_path for row in report.findings)
