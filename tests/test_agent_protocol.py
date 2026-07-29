from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.mcp_server import shipgate_check
from agents_shipgate.schemas.codex_boundary_result import CodexBoundaryResultV2

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "docs" / "codex-boundary-result-schema.v2.json"
GOLDEN = ROOT / "tests" / "golden" / "agent_protocol"
EXAMPLES = ROOT / "examples" / "agent-protocol"

runner = CliRunner()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))


def _control(payload: dict[str, Any]) -> dict[str, Any]:
    assert payload["schema_version"] == "shipgate.codex_boundary_result/v2"
    for retired in (
        "completion_allowed",
        "must_stop",
        "verify_required",
        "first_next_action",
        "human_review",
        "exit_code_hint",
    ):
        assert retired not in payload
    control = payload["control"]
    assert set(control) == {
        "state",
        "reason",
        "completion_allowed",
        "must_stop",
        "verify_required",
        "next_action",
        "allowed_next_commands",
        "human_review",
        "stop_reason",
    }
    return control


def test_agent_protocol_golden_fixtures_validate_schema() -> None:
    validator = _validator()
    for path in sorted(GOLDEN.glob("*.json")) + sorted((EXAMPLES / "expected").glob("*.json")):
        payload = _load_json(path)
        validator.validate(payload)
        CodexBoundaryResultV2.model_validate(payload)


def test_codex_block_stop_fixture_stops_for_human() -> None:
    payload = _load_json(GOLDEN / "codex-block-stop.json")

    assert payload["decision"] == "block"
    control = _control(payload)
    assert control["state"] == "human_review_required"
    assert control["completion_allowed"] is False
    assert control["must_stop"] is True
    assert control["next_action"]["actor"] == "human"
    assert control["next_action"]["kind"] == "stop"
    assert control["human_review"]["required"] is True
    assert payload["repair"]["safe_to_attempt"] is False


def test_cursor_and_claude_variants_match_codex_shape_and_decision() -> None:
    codex = _load_json(GOLDEN / "codex-block-stop.json")
    for agent in ("claude-code", "cursor"):
        payload = _load_json(GOLDEN / f"{agent}-block-stop.json")
        assert payload["agent"] == agent
        assert payload["subject"]["agent"] == agent
        normalized = json.loads(json.dumps(payload))
        normalized["agent"] = "codex"
        normalized["subject"]["agent"] = "codex"
        assert normalized == codex


def test_policy_weakening_blocks_and_is_not_agent_repairable(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "check",
            "--agent",
            "codex",
            "--workspace",
            str(tmp_path),
            "--diff",
            str(EXAMPLES / "diffs" / "block-stop.diff"),
            "--format",
            "codex-boundary-json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"] == "block"
    control = _control(payload)
    assert control["state"] == "human_review_required"
    assert control["must_stop"] is True
    assert control["next_action"]["kind"] == "stop"
    assert control["human_review"]["required"] is True
    assert payload["violated_rules"][0]["id"] == "CODEX-POLICY-WEAKENED"


def test_policy_weakening_detection_parses_yaml_scalars(tmp_path: Path) -> None:
    for replacement in (
        '    action: "require_review"',
        "    action: require_review # ok",
    ):
        diff_text = f"""diff --git a/policies/codex-boundary.shipgate.yaml b/policies/codex-boundary.shipgate.yaml
index 1111111..2222222 100644
--- a/policies/codex-boundary.shipgate.yaml
+++ b/policies/codex-boundary.shipgate.yaml
@@ -1,9 +1,9 @@
 id: codex-boundary
 version: "1"
 rules:
   - id: CODEX-MCP-AUTO-APPROVE-WRITE
     check_id: SHIP-CODEX-BOUNDARY-MCP-AUTO-APPROVE-WRITE
-    action: block
+{replacement}
     risk_level: critical
     recommendation: Do not auto-approve write/destructive MCP tools.
"""

        result = runner.invoke(
            app,
            [
                "check",
                "--workspace",
                str(tmp_path),
                "--diff",
                "-",
                "--format",
                "codex-boundary-json",
            ],
            input=diff_text,
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["decision"] == "block"
        assert payload["violated_rules"][0]["id"] == "CODEX-POLICY-WEAKENED"
        assert payload["violated_rules"][0]["evidence"]["weakened_action"] is True


def test_policy_weakening_detection_uses_resolved_full_policy_text(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "policies" / "codex-boundary.shipgate.yaml"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        """id: codex-boundary
version: "1"
rules:
  - id: CODEX-MCP-AUTO-APPROVE-WRITE
    check_id: SHIP-CODEX-BOUNDARY-MCP-AUTO-APPROVE-WRITE
    title: Codex MCP default auto approval changed
    recommendation: Do not auto-approve write/destructive MCP tools.
    notes:
      - a
      - b
      - c
      - d
      - e
    action: block
    risk_level: critical
""",
        encoding="utf-8",
    )
    diff_text = """diff --git a/policies/codex-boundary.shipgate.yaml b/policies/codex-boundary.shipgate.yaml
index 1111111..2222222 100644
--- a/policies/codex-boundary.shipgate.yaml
+++ b/policies/codex-boundary.shipgate.yaml
@@ -11,5 +11,5 @@
       - c
       - d
       - e
-    action: block
+    action: "require_review"
     risk_level: critical
"""

    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(tmp_path),
            "--diff",
            "-",
            "--format",
            "codex-boundary-json",
        ],
        input=diff_text,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"] == "block"
    evidence = payload["violated_rules"][0]["evidence"]
    assert evidence["weakened_action"] is True
    assert evidence["weakened_rules"][0]["id"] == "CODEX-MCP-AUTO-APPROVE-WRITE"


def test_codex_mcp_auto_approval_requires_human_then_clean_diff_completes(
    tmp_path: Path,
) -> None:
    before = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(tmp_path),
            "--diff",
            str(EXAMPLES / "diffs" / "repair-before.diff"),
            "--format",
            "codex-boundary-json",
        ],
    )
    after = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(tmp_path),
            "--diff",
            str(EXAMPLES / "diffs" / "repair-after.diff"),
            "--format",
            "codex-boundary-json",
        ],
    )

    assert before.exit_code == 0, before.output
    assert after.exit_code == 0, after.output
    before_payload = json.loads(before.output)
    after_payload = json.loads(after.output)
    assert before_payload["decision"] == "block"
    before_control = _control(before_payload)
    assert before_control["state"] == "human_review_required"
    assert before_control["next_action"]["kind"] == "stop"
    assert before_control["next_action"]["actor"] == "human"
    assert before_payload["repair"]["safe_to_attempt"] is False
    assert after_payload["decision"] == "allow"
    after_control = _control(after_payload)
    assert after_control["state"] == "complete"
    assert after_control["completion_allowed"] is True
    assert after_control["must_stop"] is False


def test_check_diff_input_failure_emits_schema_valid_boundary_result(tmp_path: Path) -> None:
    workspace = tmp_path / "work space; target"
    workspace.mkdir()
    config = Path("gate file; config.yml")
    policy = Path("policy file; rules.yml")
    missing_diff = tmp_path / "missing diff; printf INJECTED"
    result = runner.invoke(
        app,
        [
            "check",
            "--agent",
            "claude-code",
            "--workspace",
            str(workspace),
            "--config",
            str(config),
            "--policy",
            str(policy),
            "--diff",
            str(missing_diff),
            "--format",
            "codex-boundary-json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    _validator().validate(payload)
    CodexBoundaryResultV2.model_validate(payload)
    assert payload["agent"] == "claude-code"
    assert payload["schema_version"] == "shipgate.codex_boundary_result/v2"
    assert payload["decision"] == "block"
    control = _control(payload)
    assert control["state"] == "agent_action_required"
    assert control["completion_allowed"] is False
    assert control["must_stop"] is False
    assert control["next_action"]["actor"] == "coding_agent"
    assert control["next_action"]["kind"] == "repair"
    assert payload["repair"]["safe_to_attempt"] is True
    assert payload["diagnostics"][0]["code"] == "diff_input_unresolved"
    command = control["next_action"]["command"]
    assert control["allowed_next_commands"] == [command]
    assert payload["repair"]["command"] == command
    assert shlex.split(command) == [
        "agents-shipgate",
        "check",
        "--agent",
        "claude-code",
        "--workspace",
        str(workspace),
        "--config",
        str(config),
        "--policy",
        str(policy),
        "--diff",
        str(missing_diff),
        "--format",
        "codex-boundary-json",
    ]


def test_check_diff_input_failure_preserves_a_complete_quoted_range(
    tmp_path: Path,
) -> None:
    """Missing refs may be fetched; their exact range must survive recovery."""

    workspace = tmp_path / "range work space"
    workspace.mkdir()
    base = "missing-base; printf INJECTED"
    head = "missing-head; printf INJECTED"
    result = runner.invoke(
        app,
        [
            "check",
            "--agent",
            "cursor",
            "--workspace",
            str(workspace),
            "--config",
            "custom gate.yml",
            "--policy",
            "custom policy.yml",
            "--base",
            base,
            "--head",
            head,
            "--format",
            "codex-boundary-json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    control = _control(payload)
    assert control["state"] == "agent_action_required"
    assert shlex.split(control["next_action"]["command"]) == [
        "agents-shipgate",
        "check",
        "--agent",
        "cursor",
        "--workspace",
        str(workspace),
        "--config",
        "custom gate.yml",
        "--policy",
        "custom policy.yml",
        "--base",
        base,
        "--head",
        head,
        "--format",
        "codex-boundary-json",
    ]


def test_missing_install_fixture_is_schema_valid_and_actionable() -> None:
    payload = _load_json(GOLDEN / "missing-install.json")

    _validator().validate(payload)
    CodexBoundaryResultV2.model_validate(payload)
    control = _control(payload)
    assert control["state"] == "agent_action_required"
    assert control["next_action"]["kind"] == "install"
    assert control["next_action"]["command"] == "pipx install agents-shipgate"
    assert control["completion_allowed"] is False
    assert control["must_stop"] is False


def test_stale_install_fixture_is_schema_valid_and_routes_to_upgrade() -> None:
    payload = _load_json(GOLDEN / "stale-install.json")

    _validator().validate(payload)
    CodexBoundaryResultV2.model_validate(payload)
    # Reuses the install action kind; only the
    # command carries the upgrade. Stale binaries must fail closed, never green.
    assert payload["decision"] == "block"
    control = _control(payload)
    assert control["state"] == "agent_action_required"
    assert control["next_action"]["kind"] == "install"
    assert control["next_action"]["command"] == "pipx upgrade agents-shipgate"
    assert control["completion_allowed"] is False
    assert control["must_stop"] is False
    assert payload["diagnostics"][0]["code"] == "shipgate_stale_install"


def test_stale_install_fixture_mirrors_examples_copy() -> None:
    # The golden and the runnable examples/ copy must stay byte-identical, like
    # missing-install — agents may read either path.
    golden = (GOLDEN / "stale-install.json").read_text(encoding="utf-8")
    example = (EXAMPLES / "expected" / "stale-install.json").read_text(encoding="utf-8")
    assert golden == example


def test_protocol_state_machine_documents_install_branch() -> None:
    # The kind="install" block (missing- and stale-install) is a distinct
    # state-machine branch; a consumer implementing the protocol must have a
    # row for it, not just repairable/human blocks (PR #201 review).
    text = (ROOT / "docs" / "agents" / "protocol.md").read_text(encoding="utf-8")
    assert '`control.next_action.kind="install"`' in text
    assert "#missing-install" in text
    assert "#stale-install" in text


def test_mcp_shipgate_check_is_read_only_static_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    import agents_shipgate.cli.agent_result as agent_result_module

    def _fail_git(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("MCP adapter must not shell out to git")

    monkeypatch.setattr(agent_result_module, "_git_diff_context", _fail_git)
    diff_path = EXAMPLES / "diffs" / "repair-after.diff"
    diff_text = diff_path.read_text(encoding="utf-8")
    payload = shipgate_check(
        agent="cursor",
        workspace=str(tmp_path),
        diff_text=diff_text,
    )
    cli = runner.invoke(
        app,
        [
            "check",
            "--agent",
            "cursor",
            "--workspace",
            str(tmp_path),
            "--diff",
            "-",
            "--format",
            "agent-boundary-json",
        ],
        input=diff_text,
    )

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert before == after
    assert cli.exit_code == 0, cli.output
    assert payload == json.loads(cli.output)
    assert payload["schema_version"] == "shipgate.agent_boundary_result/v1"
    assert payload["agent"] == "cursor"
    assert payload["decision"] == "allow"
    assert payload["control"]["state"] == "complete"
