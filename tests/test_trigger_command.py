"""`agents-shipgate trigger` subcommand + the M1 evaluate() output shape.

The trigger evaluator decides whether Shipgate should run on a diff. M1
promotes it to a first-class subcommand and extends the output with the
verifier-workflow fields (should_run, force_run, skip_reason,
changed_files, diff_tokens) while preserving the back-compat fields.
"""

import json

from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.triggers import evaluate

runner = CliRunner()

# The full M1 output contract: the spec's documented fields plus the
# back-compat fields the canonical-evaluator doc requires us to keep.
M1_KEYS = {
    "schema_version",
    "should_run",
    "run_shipgate",
    "force_run",
    "dry_run_recommended",
    "skip_reason",
    "stop_conditions_fired",
    "rationale",
    "matched_rules",
    "changed_files",
    "diff_tokens",
}


def test_evaluate_emits_full_m1_shape():
    result = evaluate(paths=["agent.py"], diff_text="+@function_tool\n")
    assert M1_KEYS <= set(result)
    assert result["should_run"] is True
    assert result["should_run"] == result["run_shipgate"]
    assert result["changed_files"] == ["agent.py"]
    assert result["diff_tokens"] == ["@function_tool"]
    assert result["skip_reason"] is None


def test_evaluate_skip_reason_tokens_track_precedence():
    forced = evaluate(paths=["README.md"], manifest_present=True)
    assert forced["should_run"] is True
    assert forced["force_run"] is True
    assert forced["skip_reason"] is None

    skipped = evaluate(paths=["README.md"])
    assert skipped["should_run"] is False
    assert skipped["skip_reason"] == "skip_rule"

    no_match = evaluate(paths=["src/internal/util.py"])
    assert no_match["should_run"] is False
    assert no_match["skip_reason"] == "no_match"

    dry = evaluate(paths=["requirements.txt"], diff_text="+langchain==0.3.0\n")
    assert dry["should_run"] is False
    assert dry["dry_run_recommended"] is True
    assert dry["skip_reason"] == "dry_run_only"


def test_evaluate_diff_tokens_only_reports_present_tokens():
    result = evaluate(paths=["a.py"], diff_text="+ no markers here\n")
    assert result["diff_tokens"] == []
    multi = evaluate(
        paths=["a.py"],
        diff_text="+@function_tool\n+ type: n8n-nodes-base.httpRequest\n",
    )
    assert multi["diff_tokens"] == ["@function_tool", "n8n-nodes-base."]


def test_trigger_subcommand_json_shape(tmp_path):
    (tmp_path / "shipgate.yaml").write_text("schema_version: '0.1'\n", encoding="utf-8")
    changed = tmp_path / "cf.txt"
    changed.write_text("shipgate.yaml\nagent.py\n", encoding="utf-8")
    diff = tmp_path / "df.txt"
    diff.write_text("+@function_tool\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "trigger",
            "--workspace",
            str(tmp_path),
            "--changed-files",
            str(changed),
            "--diff",
            str(diff),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert M1_KEYS <= set(payload)
    assert payload["schema_version"] == "0.1"
    assert payload["should_run"] is True
    assert payload["force_run"] is True  # shipgate.yaml present in workspace
    assert payload["skip_reason"] is None
    assert payload["changed_files"] == ["shipgate.yaml", "agent.py"]
    assert payload["diff_tokens"] == ["@function_tool"]
    matched_ids = {m["id"] for m in payload["matched_rules"]}
    assert "TRIGGER-EXISTING-MANIFEST-PRESENT" in matched_ids


def test_trigger_subcommand_runs_without_git_when_no_base_head(tmp_path):
    """tmp_path is not a git repo. With only --changed-files (no
    --base/--head) the command must never shell out to git."""
    changed = tmp_path / "cf.txt"
    changed.write_text("tools/my_mcp.json\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["trigger", "--workspace", str(tmp_path), "--changed-files", str(changed), "--json"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["should_run"] is True
    assert payload["force_run"] is False  # no shipgate.yaml in workspace


def test_trigger_subcommand_docs_only_skips(tmp_path):
    changed = tmp_path / "cf.txt"
    changed.write_text("README.md\ndocs/guide.md\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["trigger", "--workspace", str(tmp_path), "--changed-files", str(changed), "--json"],
    )
    payload = json.loads(result.stdout)
    assert payload["should_run"] is False
    assert payload["skip_reason"] == "skip_rule"


def test_trigger_subcommand_list_rules_json():
    result = runner.invoke(app, ["trigger", "--list-rules", "--json"])
    assert result.exit_code == 0
    catalog = json.loads(result.stdout)
    assert catalog["schema_version"] == "0.1"
    rule_ids = {r["id"] for r in catalog["rules"]}
    assert "TRIGGER-N8N-WORKFLOW-CHANGED" in rule_ids


def test_trigger_subcommand_human_readable_verdict(tmp_path):
    changed = tmp_path / "cf.txt"
    changed.write_text("tools/my_mcp.json\n", encoding="utf-8")
    result = runner.invoke(
        app, ["trigger", "--workspace", str(tmp_path), "--changed-files", str(changed)]
    )
    assert result.exit_code == 0
    assert "Verdict: RUN" in result.stdout


def test_trigger_missing_changed_files_exits_2(tmp_path):
    """An unreadable --changed-files path fails deterministically with a
    clean exit 2 (config/input error), not a Typer traceback."""
    result = runner.invoke(
        app, ["trigger", "--changed-files", str(tmp_path / "nope.txt"), "--json"]
    )
    assert result.exit_code == 2


def test_trigger_missing_diff_exits_2(tmp_path):
    result = runner.invoke(
        app, ["trigger", "--diff", str(tmp_path / "nope.diff"), "--json"]
    )
    assert result.exit_code == 2


def test_trigger_undecodable_changed_files_exits_2(tmp_path):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\xff\xfe\x00not utf-8")
    result = runner.invoke(
        app, ["trigger", "--changed-files", str(bad), "--json"]
    )
    assert result.exit_code == 2
