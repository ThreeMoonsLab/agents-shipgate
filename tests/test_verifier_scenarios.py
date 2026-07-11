"""End-to-end AI-coding-verifier scenarios (spec section 10).

Each scenario builds a small git repo with a base and a head commit, runs
``agents-shipgate verify --base ... --head ...`` through the real engine, and
asserts the merge-oriented verifier.json behaviour. These exercise the full
trigger -> scan -> capability-projection -> merge-verdict path, complementing
the unit tests in test_capability_projection.py / test_verify.py.

Scenarios (docs/engineering/ai-coding-workflow-verifier.md):
- codex_adds_refund_tool        -> blocked, money-moving capability added
- agent_weakens_shipgate_policy -> trust_root_touched
- docs_only_no_shipgate         -> skip (mergeable, no scan)
- docs_only_with_shipgate_yaml  -> force_run (manifest present)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app

runner = CliRunner()

_MANIFEST = """
version: "0.1"
project:
  name: refund-bot
agent:
  name: refund-bot
  declared_purpose:
    - help customers
environment:
  target: production_like
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
    trust: internal
ci:
  mode: advisory
""".lstrip()

_BASE_TOOLS = {
    "tools": [
        {
            "name": "support.search_kb",
            "description": "Search the support knowledge base.",
            "annotations": {"readOnlyHint": True, "idempotentHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
            },
        }
    ]
}

# Head adds a real money-moving action with a broad scope and no controls.
_REFUND_TOOL = {
    "name": "stripe.create_refund",
    "description": "Create a real money refund to the customer payment method.",
    "annotations": {"readOnlyHint": False, "destructiveHint": True},
    "inputSchema": {
        "type": "object",
        "required": ["charge_id", "amount"],
        "properties": {
            "charge_id": {"type": "string"},
            "amount": {"type": "number"},
        },
    },
    "auth": {"type": "oauth2", "scopes": ["stripe:*"]},
}


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    return repo


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _set_origin_main(repo: Path) -> None:
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )


def _export_capability_lock(repo: Path) -> None:
    result = runner.invoke(
        app,
        [
            "capability",
            "export",
            "-c",
            str(repo / "shipgate.yaml"),
            "--out",
            str(repo / ".agents-shipgate" / "capabilities.lock.json"),
            "--no-report-copy",
        ],
    )
    assert result.exit_code == 0, result.output


def _write_tools(repo: Path, payload: dict) -> None:
    (repo / "tools.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _verify(repo: Path) -> dict:
    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )
    assert result.exit_code in {0, 20}, result.output
    return json.loads(result.output)


def test_scenario_codex_adds_refund_tool_blocks(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    _write_tools(repo, _BASE_TOOLS)
    _commit(repo, "base agent")
    _set_origin_main(repo)

    head_tools = {"tools": [*_BASE_TOOLS["tools"], _REFUND_TOOL]}
    _write_tools(repo, head_tools)
    _commit(repo, "codex adds refund tool")

    payload = _verify(repo)

    assert payload["head_status"] == "succeeded"
    assert payload["merge_verdict"] == "blocked"
    assert payload["can_merge_without_human"] is False
    assert payload["capability_review"]["trust_root_touched"] is False
    # The top capability change references the money-moving refund action.
    refund_adds = [
        c
        for c in payload["capability_review"]["top_changes"]
        if "refund" in c["subject"] and c["change_type"] == "action_added"
    ]
    assert refund_adds, payload["capability_review"]["top_changes"]
    change = refund_adds[0]
    assert change["impact"] == "blocks_release"
    artifacts = payload["artifacts"]
    assert artifacts["capability_lock_json"] == "agents-shipgate-reports/capabilities.lock.json"
    assert (
        artifacts["base_capability_lock_json"]
        == "agents-shipgate-reports/base.capabilities.lock.json"
    )
    assert (
        artifacts["capability_lock_diff_json"]
        == "agents-shipgate-reports/capability-lock-diff.json"
    )
    reports = repo / "agents-shipgate-reports"
    assert (reports / "capabilities.lock.json").is_file()
    assert (reports / "base.capabilities.lock.json").is_file()
    assert (reports / "capability-lock-diff.json").is_file()
    comment = (reports / "pr-comment.md").read_text(encoding="utf-8")
    assert "### Human summary" in comment
    assert "### Agent instruction block" in comment
    assert "- Capability lock diff: +1, -0, 0 changed" in comment
    assert "### Capability Diff" not in comment
    assert any(
        "Capability lock diff compared scan-derived base/head locks." in note
        for note in payload["base_notes"]
    )


def test_scenario_committed_base_lock_emits_semantic_capability_diff(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    _write_tools(repo, _BASE_TOOLS)
    _export_capability_lock(repo)
    _commit(repo, "base agent with reviewed capability lock")
    _set_origin_main(repo)

    head_tools = {"tools": [*_BASE_TOOLS["tools"], _REFUND_TOOL]}
    _write_tools(repo, head_tools)
    _commit(repo, "codex adds refund tool")

    payload = _verify(repo)

    assert payload["head_status"] == "succeeded"
    assert payload["merge_verdict"] == "blocked"
    artifacts = payload["artifacts"]
    assert artifacts["capability_lock_json"] == "agents-shipgate-reports/capabilities.lock.json"
    assert (
        artifacts["base_capability_lock_json"]
        == "agents-shipgate-reports/base.capabilities.lock.json"
    )
    assert (
        artifacts["capability_lock_diff_json"]
        == "agents-shipgate-reports/capability-lock-diff.json"
    )
    assert (
        artifacts["capability_lock_diff_markdown"]
        == "agents-shipgate-reports/capability-lock-diff.md"
    )
    reports = repo / "agents-shipgate-reports"
    diff_payload = json.loads((reports / "capability-lock-diff.json").read_text(encoding="utf-8"))
    assert diff_payload["capability_lock_diff_schema_version"] == "0.5"
    assert diff_payload["summary"]["added"] == 1
    assert diff_payload["summary"]["changed"] == 0
    assert diff_payload["added"][0]["identity"]["tool_name"] == "stripe.create_refund"
    diff_markdown = (reports / "capability-lock-diff.md").read_text(encoding="utf-8")
    assert "## Capability Diff" in diff_markdown
    assert "| stripe.create_refund |" in diff_markdown
    comment = (reports / "pr-comment.md").read_text(encoding="utf-8")
    assert "### Human summary" in comment
    assert "### Agent instruction block" in comment
    assert "- Capability lock diff: +1, -0, 0 changed" in comment
    assert "### Capability Diff" not in comment
    assert comment.index("Release gate: `blocked`") < comment.index("### Agent instruction block")


def test_scenario_agent_weakens_shipgate_policy_touches_trust_root(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    _write_tools(repo, _BASE_TOOLS)
    _commit(repo, "base agent")
    _set_origin_main(repo)

    # The PR edits the release manifest itself — a trust root.
    with (repo / "shipgate.yaml").open("a", encoding="utf-8") as handle:
        handle.write("\n# loosen later\n")
    _commit(repo, "edit shipgate.yaml")

    payload = _verify(repo)

    assert payload["capability_review"]["trust_root_touched"] is True


def test_scenario_docs_only_no_shipgate_fails_closed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _commit(repo, "base docs")
    _set_origin_main(repo)
    (repo / "README.md").write_text("hello world\n", encoding="utf-8")
    _commit(repo, "docs only")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["trigger"]["should_run"] is False
    assert payload["head_status"] == "failed"
    assert payload["merge_verdict"] == "unknown"
    assert payload["applicability"] == "unknown"
    assert payload["can_merge_without_human"] is False
    assert not (repo / "agents-shipgate-reports" / "report.json").exists()


def test_scenario_docs_only_with_shipgate_yaml_force_runs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    _write_tools(repo, _BASE_TOOLS)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _commit(repo, "base agent")
    _set_origin_main(repo)
    # A docs-only change, but the repo has opted in (shipgate.yaml present).
    (repo / "README.md").write_text("hello world\n", encoding="utf-8")
    _commit(repo, "docs only")

    payload = _verify(repo)

    assert payload["trigger"]["should_run"] is True
    assert payload["trigger"]["force_run"] is True
    assert payload["head_status"] == "succeeded"


# --- Additional capability-transition scenarios -----------------------------

# An external-communication action with no approval/idempotency controls.
_EMAIL_TOOL = {
    "name": "messaging.send_customer_email",
    "description": "Send an email to a customer's email address.",
    "annotations": {
        "readOnlyHint": False,
        "x-agents-shipgate-permissions": ["external"],
    },
    "inputSchema": {
        "type": "object",
        "required": ["to", "subject", "body"],
        "properties": {
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
    },
    "auth": {"type": "oauth2", "scopes": ["email:send"]},
}

_WORKFLOW = """\
name: agents-shipgate
on: [pull_request]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: ThreeMoonsLab/agents-shipgate@v0.14.0
"""


def _write_workflow(repo: Path) -> None:
    wf = repo / ".github" / "workflows" / "agents-shipgate.yml"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(_WORKFLOW, encoding="utf-8")


def test_scenario_agent_adds_email_tool_is_a_gated_capability(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    _write_tools(repo, _BASE_TOOLS)
    _commit(repo, "base agent")
    _set_origin_main(repo)

    head_tools = {"tools": [*_BASE_TOOLS["tools"], _EMAIL_TOOL]}
    _write_tools(repo, head_tools)
    _commit(repo, "agent adds customer-email tool")

    payload = _verify(repo)

    assert payload["head_status"] == "succeeded"
    # An external-communication action with no approval is a blocker, pinned to
    # the external-communication audit check — not a generic side-effect finding.
    assert payload["merge_verdict"] == "blocked"
    assert payload["can_merge_without_human"] is False
    blocker_checks = {b["check_id"] for b in payload["release_decision"]["blockers"]}
    assert "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING" in blocker_checks, blocker_checks
    email_adds = [
        c
        for c in payload["capability_review"]["top_changes"]
        if "email" in c["subject"] and c["change_type"] == "action_added"
    ]
    assert email_adds, payload["capability_review"]["top_changes"]


def test_scenario_agent_removes_ci_gate_blocks(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    _write_tools(repo, _BASE_TOOLS)
    _write_workflow(repo)
    _commit(repo, "base agent with shipgate CI")
    _set_origin_main(repo)

    # The PR deletes the Shipgate CI workflow — a reward-hacking move to dodge
    # the gate. Verify must route it to a human, not let it self-merge.
    (repo / ".github" / "workflows" / "agents-shipgate.yml").unlink()
    _commit(repo, "remove shipgate CI")

    payload = _verify(repo)

    assert payload["head_status"] == "succeeded"
    # The flagship anti-bypass case: deleting the gate is a blocker, pinned to
    # SHIP-VERIFY-CI-GATE-REMOVED — not merely a generic trust-root touch.
    assert payload["merge_verdict"] == "blocked"
    assert payload["can_merge_without_human"] is False
    blocker_checks = {b["check_id"] for b in payload["release_decision"]["blockers"]}
    assert "SHIP-VERIFY-CI-GATE-REMOVED" in blocker_checks, blocker_checks
    assert payload["capability_review"]["trust_root_touched"] is True


def test_scenario_agent_adds_suppression_weakens_policy(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    _write_tools(repo, _BASE_TOOLS)
    _commit(repo, "base agent")
    _set_origin_main(repo)

    # The PR suppresses a check to silence a finding rather than fix it — the
    # canonical reward-hacking move. Verify must flag the policy as weakened.
    with (repo / "shipgate.yaml").open("a", encoding="utf-8") as handle:
        handle.write(
            "checks:\n"
            "  ignore:\n"
            "    - check_id: SHIP-POLICY-APPROVAL-MISSING\n"
            "      reason: accepted for now\n"
        )
    _commit(repo, "suppress approval check")

    payload = _verify(repo)

    # A suppression expansion is flagged specifically (not just as a generic
    # manifest touch): the waiver-expanded verify check fires and the
    # policy_broadened change names the suppressed check.
    assert payload["merge_verdict"] == "human_review_required"
    assert payload["can_merge_without_human"] is False
    review_checks = {r["check_id"] for r in payload["release_decision"]["review_items"]}
    assert "SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED" in review_checks, review_checks
    suppression_changes = [
        c
        for c in payload["capability_review"]["top_changes"]
        if c["change_type"] == "policy_broadened"
        and "suppression:SHIP-POLICY-APPROVAL-MISSING" in c["subject"]
    ]
    assert suppression_changes, payload["capability_review"]["top_changes"]
