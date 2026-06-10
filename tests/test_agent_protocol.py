from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.mcp_server import shipgate_check
from agents_shipgate.schemas.agent_result_v1 import AgentResultV1

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "docs" / "agent-result-schema.v1.json"
GOLDEN = ROOT / "tests" / "golden" / "agent_protocol"
EXAMPLES = ROOT / "examples" / "agent-protocol"

runner = CliRunner()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))


def test_agent_protocol_golden_fixtures_validate_schema() -> None:
    validator = _validator()
    for path in sorted(GOLDEN.glob("*.json")) + sorted((EXAMPLES / "expected").glob("*.json")):
        payload = _load_json(path)
        validator.validate(payload)
        AgentResultV1.model_validate(payload)


def test_codex_block_stop_fixture_stops_for_human() -> None:
    payload = _load_json(GOLDEN / "codex-block-stop.json")

    assert payload["decision"] == "block"
    assert payload["completion_allowed"] is False
    assert payload["must_stop"] is True
    assert payload["first_next_action"]["actor"] == "human"
    assert payload["first_next_action"]["kind"] == "stop"
    assert payload["human_review"]["required"] is True
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
            "agent-json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"] == "block"
    assert payload["must_stop"] is True
    assert payload["first_next_action"]["kind"] == "stop"
    assert payload["human_review"]["required"] is True
    assert payload["violated_rules"][0]["id"] == "CODEX-POLICY-WEAKENED"


def test_repairable_boundary_violation_allows_after_rerun(tmp_path: Path) -> None:
    before = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(tmp_path),
            "--diff",
            str(EXAMPLES / "diffs" / "repair-before.diff"),
            "--format",
            "agent-json",
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
            "agent-json",
        ],
    )

    assert before.exit_code == 0, before.output
    assert after.exit_code == 0, after.output
    before_payload = json.loads(before.output)
    after_payload = json.loads(after.output)
    assert before_payload["decision"] == "block"
    assert before_payload["first_next_action"]["kind"] == "repair"
    assert before_payload["first_next_action"]["actor"] == "coding_agent"
    assert before_payload["repair"]["safe_to_attempt"] is True
    assert before_payload["repair"]["command"]
    assert after_payload["decision"] == "allow"
    assert after_payload["completion_allowed"] is True
    assert after_payload["must_stop"] is False


def test_missing_install_fixture_is_schema_valid_and_actionable() -> None:
    payload = _load_json(GOLDEN / "missing-install.json")

    _validator().validate(payload)
    AgentResultV1.model_validate(payload)
    assert payload["first_next_action"]["kind"] == "install"
    assert payload["first_next_action"]["command"] == "pipx install agents-shipgate"
    assert payload["completion_allowed"] is False
    assert payload["must_stop"] is True


def test_mcp_shipgate_check_is_read_only_static_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    import agents_shipgate.cli.agent_result as agent_result_module

    def _fail_git(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("MCP adapter must not shell out to git")

    monkeypatch.setattr(agent_result_module, "_git_diff_context", _fail_git)
    payload = shipgate_check(
        agent="cursor",
        workspace=str(tmp_path),
        diff_text=(EXAMPLES / "diffs" / "repair-after.diff").read_text(encoding="utf-8"),
    )

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert before == after
    assert payload["schema_version"] == "agent_result_v1"
    assert payload["agent"] == "cursor"
    assert payload["decision"] == "allow"
