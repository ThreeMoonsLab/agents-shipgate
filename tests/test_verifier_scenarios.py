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
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=repo, check=True
    )
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


def test_scenario_docs_only_no_shipgate_skips(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _commit(repo, "base docs")
    _set_origin_main(repo)
    (repo / "README.md").write_text("hello world\n", encoding="utf-8")
    _commit(repo, "docs only")

    payload = _verify(repo)

    assert payload["trigger"]["should_run"] is False
    assert payload["head_status"] == "skipped"
    assert payload["merge_verdict"] == "mergeable"
    assert payload["can_merge_without_human"] is True


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
