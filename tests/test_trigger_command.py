"""`agents-shipgate trigger` subcommand + the M1 evaluate() output shape.

The trigger evaluator decides whether Shipgate should run on a diff. M1
promotes it to a first-class subcommand and extends the output with the
verifier-workflow fields (should_run, force_run, skip_reason,
changed_files, diff_tokens) while preserving the back-compat fields.
"""

import json

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.triggers import (
    SURFACE_CLASS_CAPABILITY,
    SURFACE_CLASS_HOST_BOUNDARY,
    evaluate,
    result_has_surface_class,
)

runner = CliRunner()


def _catalog(when: dict) -> dict:
    """A minimal one-rule catalog for predicate-isolation tests."""
    return {
        "schema_version": "test",
        "default_command": "agents-shipgate verify --preview --json",
        "rules": [
            {
                "id": "R",
                "action": "run_shipgate",
                "surface_class": "capability",
                "when": when,
                "rationale": "",
            }
        ],
    }

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

    unclassified = evaluate(paths=["src/internal/util.py"])
    assert unclassified["should_run"] is None
    assert unclassified["skip_reason"] is None
    assert unclassified["evaluation_status"] == "unclassified"

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
    assert payload["schema_version"] == "0.4"
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
    assert catalog["schema_version"] == "0.4"
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


# --- M1.1: skip / next_action / stop_conditions_evaluated -------------------


def test_evaluate_emits_skip_and_next_action_fields():
    run = evaluate(
        paths=["agent.py"], diff_text="+@function_tool\n", manifest_present=True
    )
    assert run["should_run"] is True
    assert run["skip"] is False
    assert run["stop_conditions_evaluated"] is False  # no detect_result supplied
    # An adopted repo (manifest present) is pointed at the verify gate.
    assert run["next_action"]["kind"] == "command"
    assert "verify" in run["next_action"]["command"]
    assert run["next_action"]["why"]

    skipped = evaluate(paths=["README.md"])
    assert skipped["skip"] is True
    assert skipped["next_action"]["kind"] == "none"
    assert skipped["next_action"]["command"] is None


def test_next_action_points_at_verify_preview_when_not_adopted():
    res = evaluate(paths=["tools/my_mcp.json"])  # run rule fires, no manifest
    assert res["should_run"] is True
    assert res["next_action"]["kind"] == "command"
    assert res["next_action"]["command"] == "agents-shipgate verify --preview --json"


def test_stop_conditions_not_evaluated_without_detect_result():
    # The stop block needs detect output; without it we must NOT stop, and
    # must report stop_conditions_evaluated=False (not an incorrect stop).
    res = evaluate(paths=["src/internal/util.py"])
    assert res["stop_conditions_evaluated"] is False
    assert res["stop_conditions_fired"] is False
    assert res["skip_reason"] is None


def test_stop_conditions_fire_with_detect_result():
    detect = {
        "is_agent_project": False,
        "suggested_sources": [],
        "codex_plugin_candidates": [],
        "python_parse_truncated": False,
    }
    res = evaluate(paths=["src/internal/util.py"], detect_result=detect)
    assert res["stop_conditions_evaluated"] is True
    assert res["stop_conditions_fired"] is True
    assert res["should_run"] is False
    assert res["skip_reason"] == "stop_conditions"
    assert res["next_action"]["kind"] == "stop"


def test_a_truncated_detect_cannot_fire_the_stop_conditions():
    """Every negative in the stop block is a claim about the whole workspace.
    A detect run whose Python parse stopped at its cap read part of one, so
    `is_agent_project: false` from it is not a reason to stop (#399 review)."""

    detect = {
        "is_agent_project": False,
        "suggested_sources": [],
        "codex_plugin_candidates": [],
        "python_parse_truncated": True,
    }
    res = evaluate(paths=["src/internal/util.py"], detect_result=detect)
    assert res["stop_conditions_evaluated"] is True
    assert res["stop_conditions_fired"] is False
    assert res["skip_reason"] != "stop_conditions"


def test_a_matched_capability_rule_survives_a_truncated_detect():
    """The stop block blocks everything, so a truncated negative could bury a
    rule that had already fired on the diff itself."""

    detect = {
        "is_agent_project": False,
        "suggested_sources": [],
        "codex_plugin_candidates": [],
        "python_parse_truncated": True,
    }
    res = evaluate(
        paths=["src/tools.py"],
        diff_text="+@function_tool\n+def refund(): ...\n",
        detect_result=detect,
    )
    assert res["stop_conditions_fired"] is False
    assert res["should_run"] is True


def test_a_detect_payload_missing_a_stop_key_is_not_evaluable():
    """Absent is not false. A payload from a build that predates a key the
    block reads leaves the block unevaluable — the same answer the evaluator
    already gives when no payload is supplied at all — rather than silently
    concluding "not an agent project" from evidence nobody produced."""

    legacy = {
        "is_agent_project": False,
        "suggested_sources": [],
        "codex_plugin_candidates": [],
    }
    res = evaluate(paths=["src/internal/util.py"], detect_result=legacy)
    assert res["stop_conditions_evaluated"] is False
    assert res["stop_conditions_fired"] is False


def test_stop_conditions_suppressed_by_user_request():
    detect = {
        "is_agent_project": False,
        "suggested_sources": [],
        "codex_plugin_candidates": [],
        "python_parse_truncated": False,
    }
    res = evaluate(
        paths=["src/internal/util.py"], detect_result=detect, user_requested=True
    )
    assert res["stop_conditions_evaluated"] is True
    assert res["stop_conditions_fired"] is False  # user_did_not_request is False


def test_trigger_subcommand_detect_json_enables_stop(tmp_path):
    detect = tmp_path / "detect.json"
    detect.write_text(
        json.dumps(
            {
                "is_agent_project": False,
                "suggested_sources": [],
                "codex_plugin_candidates": [],
                "python_parse_truncated": False,
            }
        ),
        encoding="utf-8",
    )
    changed = tmp_path / "cf.txt"
    changed.write_text("src/util.py\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "trigger",
            "--workspace",
            str(tmp_path),
            "--changed-files",
            str(changed),
            "--detect-json",
            str(detect),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["stop_conditions_evaluated"] is True
    assert payload["stop_conditions_fired"] is True
    assert payload["skip_reason"] == "stop_conditions"


# --- M1.1: predicate isolation ---------------------------------------------


def test_predicate_diff_contains_isolated():
    # These read `matched_rules`, not `should_run`. A predicate-isolation test
    # asks whether the predicate fired; the verdict is a separate policy (a
    # change set no rule classified is now withheld, not skipped — #403) and
    # conflating the two made these fail for a reason they never tested.
    cat = _catalog({"diff_contains": "SECRET_MARKER"})
    assert evaluate(paths=["a.py"], diff_text="+SECRET_MARKER=1", triggers=cat)[
        "should_run"
    ] is True
    assert not evaluate(paths=["a.py"], diff_text="+nothing", triggers=cat)[
        "matched_rules"
    ]


def test_predicate_file_present_and_absent():
    present = _catalog({"file_present": "shipgate.yaml"})
    assert evaluate(paths=[], manifest_present=True, triggers=present)[
        "should_run"
    ] is True
    assert evaluate(paths=[], manifest_present=False, triggers=present)[
        "should_run"
    ] is False
    absent = _catalog({"file_absent": "shipgate.yaml"})
    assert evaluate(paths=[], manifest_present=False, triggers=absent)[
        "should_run"
    ] is True


def test_predicate_none_match_glob_any_of_all_of():
    nmg = _catalog({"none_match_glob": ["**/*.md"]})
    assert evaluate(paths=["a.py"], triggers=nmg)["should_run"] is True
    assert not evaluate(paths=["a.md"], triggers=nmg)["matched_rules"]

    any_of = _catalog({"any_of": [{"glob": "*.py"}, {"glob": "*.go"}]})
    assert evaluate(paths=["main.go"], triggers=any_of)["should_run"] is True
    assert not evaluate(paths=["main.rs"], triggers=any_of)["matched_rules"]

    all_of = _catalog({"all_of": [{"glob": "*.py"}, {"diff_contains": "X"}]})
    assert evaluate(paths=["a.py"], diff_text="X", triggers=all_of)[
        "should_run"
    ] is True
    assert not evaluate(paths=["a.py"], diff_text="", triggers=all_of)[
        "matched_rules"
    ]


@pytest.mark.parametrize(
    "paths,diff_text,expected_rule",
    [
        (["api/openapi.yaml"], "", "TRIGGER-OPENAPI-SPEC-CHANGED"),
        (["specs/swagger.json"], "", "TRIGGER-OPENAPI-SPEC-CHANGED"),
        (
            ["plugins/x/.codex-plugin/plugin.json"],
            "",
            "TRIGGER-CODEX-PLUGIN-CHANGED",
        ),
        (
            [".codex/config.toml"],
            "",
            "TRIGGER-CODEX-BOUNDARY-CONFIG-CHANGED",
        ),
        (
            [".codex/hooks.json"],
            "",
            "TRIGGER-CODEX-BOUNDARY-CONFIG-CHANGED",
        ),
        (
            [".claude/settings.json"],
            "",
            "TRIGGER-CLAUDE-BOUNDARY-CONFIG-CHANGED",
        ),
        (
            [".claude/settings.local.json"],
            "",
            "TRIGGER-CLAUDE-BOUNDARY-CONFIG-CHANGED",
        ),
        (
            [".cursor/cli.json"],
            "",
            "TRIGGER-CURSOR-BOUNDARY-CONFIG-CHANGED",
        ),
        (
            [".cursor/mcp.json"],
            "",
            "TRIGGER-CURSOR-BOUNDARY-CONFIG-CHANGED",
        ),
        (
            [".vscode/mcp.json"],
            "",
            "TRIGGER-VSCODE-MCP-BOUNDARY-CHANGED",
        ),
        (
            [".github/workflows/deploy.yml"],
            "",
            "TRIGGER-SHARED-HOST-BOUNDARY-CHANGED",
        ),
        (
            ["workflows/payment.json"],
            '+{"type": "CALL_MCP_TOOL"}',
            "TRIGGER-CONDUCTOR-WORKFLOW-CHANGED",
        ),
        (["tools/agent/.mcp.json"], "", "TRIGGER-CODEX-PLUGIN-CHANGED"),
        (["skills/x/SKILL.md"], "", "TRIGGER-CODEX-PLUGIN-CHANGED"),
        (
            [".github/workflows/agents-shipgate.yml"],
            "",
            "TRIGGER-SHIPGATE-CI-WORKFLOW",
        ),
        (["workflows/my.n8n.json"], "", "TRIGGER-N8N-WORKFLOW-CHANGED"),
        (
            ["wf.json"],
            "+ type: n8n-nodes-base.httpRequest\n",
            "TRIGGER-N8N-WORKFLOW-CHANGED",
        ),
    ],
)
def test_run_rules_fire_for_paths(paths, diff_text, expected_rule):
    res = evaluate(paths=paths, diff_text=diff_text)
    assert res["should_run"] is True, res["rationale"]
    assert expected_rule in {m["id"] for m in res["matched_rules"]}


def test_surface_class_is_a_stable_consumer_discriminator():
    conductor = evaluate(
        paths=["workflows/payment.json"],
        diff_text='+{"type": "CALL_MCP_TOOL"}',
    )
    assert result_has_surface_class(conductor, SURFACE_CLASS_CAPABILITY)
    assert not result_has_surface_class(conductor, SURFACE_CLASS_HOST_BOUNDARY)

    claude = evaluate(paths=[".claude/settings.json"])
    assert result_has_surface_class(claude, SURFACE_CLASS_HOST_BOUNDARY)
    assert not result_has_surface_class(claude, "not-a-real-class")


# --- M1.1: malformed-catalog robustness ------------------------------------


def test_catalog_without_rules_key_is_safe():
    res = evaluate(paths=["a.py"], triggers={"schema_version": "x"})
    assert res["matched_rules"] == []
    # A catalog with no rules classifies nothing, which is the unclassified
    # state rather than a skip — an empty catalog is not evidence about the PR.
    assert res["evaluation_status"] == "unclassified"
    assert res["should_run"] is None


def test_unknown_predicate_does_not_match():
    cat = _catalog({"unknown_predicate": "x"})
    res = evaluate(paths=["a.py"], triggers=cat)
    assert res["matched_rules"] == []
    assert res["should_run"] is None
    assert res["skip_reason"] is None


def test_unknown_action_falls_through_without_publishing_a_skip():
    """A rule the evaluator cannot act on decides nothing.

    The matched rule is still reported — a caller can see the catalog wanted
    to say something — but an action this build does not recognise is not
    evidence the change is irrelevant, so the verdict is withheld rather than
    published as a confident skip.
    """

    cat = _catalog({"glob": "*.py"})
    cat["rules"][0]["action"] = "bogus_action"
    res = evaluate(paths=["a.py"], triggers=cat)
    assert {m["id"] for m in res["matched_rules"]} == {"R"}
    assert res["should_run"] is None
    assert res["skip_reason"] is None
    assert res["evaluation_status"] == "unclassified"


# --- input completeness: a verdict may not outrun its evidence -------------


def test_complete_inputs_over_an_unrecognised_diff_withhold_the_skip():
    """Read in full, recognised by nothing — that is not a claim about the PR.

    The #308 rule withholds a skip reached from evidence that was never read.
    #403 applies the same argument one level up: `no_match` classified nothing,
    so "nothing in this PR signals a tool-surface change" is a statement about
    the catalog. `github/github-mcp-server#3076` is the case — a fully readable
    diff adding a destructive MCP tool as a `.snap` file, reported as a
    confident skip.
    """

    result = evaluate(paths=["src/internal/util.py"], input_status="complete")
    assert result["input_status"] == "complete"
    assert result["evaluation_status"] == "unclassified"
    assert result["should_run"] is None
    assert result["run_shipgate"] is None
    assert result["skip"] is None
    assert result["skip_reason"] is None
    # Forward, not back: the diff needs no repair, so the honest next step is
    # the scan rather than the `input_required` route a withheld read takes.
    assert result["next_action"]["kind"] == "command"
    assert result["next_action"]["command"] == "agents-shipgate verify --preview --json"
    ledger = result["surface_exclusions"]
    assert ledger["total"] == 1
    assert ledger["gated"] == 1
    assert ledger["entries"][0]["stage"] == "trigger"
    assert ledger["entries"][0]["subject"] == "src/internal/util.py"
    assert ledger["entries"][0]["accounting"] == "route_blocked"


def test_an_empty_change_set_is_still_an_evaluated_no_match():
    """Nothing changed is a fact about the PR, not about the catalog.

    The withholding above rests on there being changed files the catalog could
    not account for. With none, `no_match` classifies nothing because there is
    nothing to classify, and publishing the skip stays sound.
    """

    result = evaluate(paths=[], diff_text="", input_status="complete")
    assert result["evaluation_status"] == "evaluated"
    assert result["should_run"] is False
    assert result["skip_reason"] == "no_match"
    assert result["surface_exclusions"]["total"] == 0


def test_an_mcp_tool_schema_is_recognised_by_content_not_filename():
    """`__toolsnaps__/delete_repository.snap` is an MCP export (#403).

    `TRIGGER-MCP-EXPORT-CHANGED` matches `**/*mcp*.json`, which the file fails
    on both counts. Its content is unambiguous — an MCP input schema beside MCP
    annotation hints — and recognising that does not require the repository to
    have adopted a naming convention.
    """

    diff = (
        '+  "name": "delete_repository",\n'
        '+  "inputSchema": {"type": "object"},\n'
        '+  "annotations": {"destructiveHint": true, "readOnlyHint": false}\n'
    )
    result = evaluate(
        paths=["pkg/github/__toolsnaps__/delete_repository.snap"],
        diff_text=diff,
        input_status="complete",
    )
    assert result["should_run"] is True
    assert {m["id"] for m in result["matched_rules"]} == {
        "TRIGGER-MCP-TOOL-SCHEMA-CONTENT"
    }


@pytest.mark.parametrize("status", ["partial", "unavailable"])
def test_incomplete_inputs_withhold_the_skip_verdict(status):
    """`no_match` is a claim about a diff. Without the diff there is no claim."""

    result = evaluate(paths=[], diff_text="", input_status=status)
    assert result["evaluation_status"] == "not_evaluated"
    assert result["should_run"] is None
    assert result["run_shipgate"] is None
    assert result["skip"] is None
    assert result["skip_reason"] is None
    assert result["next_action"]["kind"] == "input_required"
    assert result["next_action"]["command"] is None
    assert "not evidence" in result["rationale"]


def test_incomplete_inputs_still_publish_a_run_verdict():
    """Rule matching is monotone: more evidence only adds matches.

    A run reached from partial evidence therefore stays sound, and suppressing
    it would turn a fail-closed gap into a missed gate.
    """

    result = evaluate(
        paths=["shipgate.yaml"],
        diff_text="",
        manifest_present=True,
        input_status="partial",
    )
    assert result["evaluation_status"] == "evaluated"
    assert result["should_run"] is True
    assert result["input_status"] == "partial"
    assert result["next_action"]["kind"] == "command"


def test_incomplete_inputs_cannot_fire_stop_conditions():
    """The stop block reasons over the very path evidence that is missing."""

    result = evaluate(
        paths=[],
        detect_result={"is_agent_project": False, "suggested_sources": []},
        input_status="unavailable",
    )
    assert result["stop_conditions_evaluated"] is False
    assert result["stop_conditions_fired"] is False
    assert result["evaluation_status"] == "not_evaluated"


def test_unknown_input_status_is_rejected():
    with pytest.raises(ConfigError):
        evaluate(paths=[], input_status="mostly")
