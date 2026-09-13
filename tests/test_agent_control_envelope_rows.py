"""#662: the compact control envelope names the host capability change.

`shipgate.agent_control/v1` carries an optional, bounded `capability_rows`
block beside the control. These tests hold four things:

* it is a copy of the rows the producer already published, on every route
  that emits the envelope;
* it is bounded, and the bound keeps widenings before narrowings and says how
  much it cut;
* it is evidence, never authority: the control is identical with or without it;
* the widening is deliberate and visible: an envelope with no comparison is
  unchanged, and a reader holding the v37 schema rejects one that carries rows.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_control_envelope import (
    capability_rows_block,
    project_agent_control_envelope,
    render_agent_control_envelope,
)
from agents_shipgate.schemas.agent_control import CodingAgentCommandAction
from agents_shipgate.schemas.agent_control_envelope import (
    AGENT_CONTROL_ENVELOPE_BUDGET_BYTES,
    MAX_ENVELOPE_CAPABILITY_ROWS,
    MAX_ENVELOPE_PROSE_BYTES,
    AgentControlArtifactRef,
    EnvelopeCapabilityRows,
    validate_agent_control_envelope,
)
from agents_shipgate.schemas.capability_diff import CapabilityDiffRow

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLISHED_SCHEMA = json.loads((REPO_ROOT / "docs/agent-control-schema.v1.json").read_text())

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=_GIT_ENV)


def _write(repo: Path, name: str, value: object) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "host"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _write(root, ".gitignore", "agents-shipgate-reports/\n")
    _write(root, ".claude/settings.json", {"permissions": {"allow": ["Bash(npm test)"], "deny": ["Bash(rm *)"]}})
    _write(root, ".mcp.json", {"mcpServers": {}})
    _commit(root, "base")
    _git(root, "checkout", "-q", "-b", "change")
    return root


def _widen(repo: Path) -> None:
    # Two widenings and one narrowing, so ordering under the cap is observable.
    _write(repo, ".claude/settings.json", {"permissions": {"allow": ["Bash(*)"], "deny": []}})
    _write(repo, ".mcp.json", {"mcpServers": {"postgres": {"command": "fixture-postgres"}}})
    _commit(repo, "widen")


def _cli(*args: str) -> dict:
    result = CliRunner().invoke(app, list(args))
    assert result.exit_code in (0, 1), result.output
    return json.loads(result.stdout)


def _check(repo: Path, format_: str) -> dict:
    return _cli("check", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", format_)


def _pairs(rows: list[dict]) -> list[tuple[str, str, str, bool]]:
    return [(row["subject"], row["before"], row["after"], row["expands"]) for row in rows]


def _validate(payload: dict, schema: dict = PUBLISHED_SCHEMA) -> list:
    return list(Draft202012Validator(schema).iter_errors(payload))


def _row(index: int, *, expands: bool, why: str = "a reason") -> CapabilityDiffRow:
    return CapabilityDiffRow(
        subject=f"claude-code .claude/settings.json #{index}",
        before="—",
        after=f"Bash(tool-{index} *)",
        direction="added",
        why=why,
        severity="high",
        expands=expands,
    )


def _envelope(capability_rows: EnvelopeCapabilityRows | None, **extra):
    control = derive_agent_control(
        reason="A human must approve the widened host configuration.",
        human_review_required=True,
        publication_allowed=True,
        human_review_why="Review the widened host configuration.",
        required_reviewers=["security"],
    )
    return project_agent_control_envelope(
        control=control,
        operation="check",
        source="run",
        execution="succeeded",
        exit_code=0,
        decision="require_review",
        decision_source="agent_boundary",
        input_id="agent_boundary_fixture",
        capability_rows=capability_rows,
        **extra,
    )


# --- the block copies what each route already published ---------------------


def test_check_envelope_names_the_rows_the_boundary_result_names(repo: Path) -> None:
    _widen(repo)
    boundary = _check(repo, "agent-boundary-json")
    envelope = _check(repo, "agent-control-json")

    block = envelope["capability_rows"]
    assert boundary["comparison_status"] == block["comparison_status"] == "comparable"
    assert block["omitted_rows"] == 0
    assert sorted(_pairs(block["rows"])) == sorted(_pairs(boundary["rows"]))
    assert any(row["after"] == "Bash(*)" and row["expands"] for row in block["rows"])
    # Widenings first, whatever order the producer used.
    flags = [row["expands"] for row in block["rows"]]
    assert flags == sorted(flags, reverse=True)
    assert not _validate(envelope)


def test_verify_control_envelope_names_the_verifier_rows(repo: Path) -> None:
    _widen(repo)
    args = ("verify", "--preview", "--workspace", str(repo), "--base", "main", "--head", "HEAD")
    comparison = _cli(*args, "--json")["host_comparison"]
    envelope = _cli(*args, "--format", "control")

    block = envelope["capability_rows"]
    assert block["comparison_status"] == comparison["comparison_status"] == "comparable"
    assert sorted(_pairs(block["rows"])) == sorted(_pairs(comparison["rows"]))
    assert block["unchanged_limit_count"] == len(comparison["unchanged_limits"])
    assert not _validate(envelope)


def test_verify_control_envelope_counts_the_unchanged_limits_it_compared_past(repo: Path) -> None:
    """#721's limits reach the envelope as a count, so empty rows never read as complete."""

    # `effort` is a documented skill field and `extreme` is not one of its values,
    # so this skill is an unchanged `parse_failed`/`unsupported` limit on both sides.
    _git(repo, "checkout", "-q", "main")
    _write(repo, ".claude/skills/helper/SKILL.md", "---\nname: helper\ndescription: A helper.\neffort: extreme\n---\n\nBody.\n")
    _commit(repo, "unresolved skill")
    _git(repo, "checkout", "-q", "-B", "change")
    _widen(repo)
    args = ("verify", "--preview", "--workspace", str(repo), "--base", "main", "--head", "HEAD")
    comparison = _cli(*args, "--json")["host_comparison"]
    envelope = _cli(*args, "--format", "control")

    assert comparison["comparison_status"] == "comparable"
    assert len(comparison["unchanged_limits"]) == 1
    assert envelope["capability_rows"]["unchanged_limit_count"] == 1
    assert envelope["capability_rows"]["rows"]

def test_an_incomparable_comparison_names_its_reasons_and_no_rows(repo: Path) -> None:
    _widen(repo)
    _write(repo, ".mcp.json", "{broken")
    _commit(repo, "break")

    boundary = _check(repo, "agent-boundary-json")
    block = _check(repo, "agent-control-json")["capability_rows"]

    assert block == {
        "comparison_status": "incomparable",
        "incomparable_reasons": boundary["incomparable_reasons"],
        "rows": [],
        "omitted_rows": 0,
        "unchanged_limit_count": 0,
    }
    assert block["incomparable_reasons"]


def test_no_host_comparison_omits_the_block_entirely(repo: Path) -> None:
    _write(repo, "README.md", "docs only\n")
    _commit(repo, "docs")
    boundary = _check(repo, "agent-boundary-json")
    envelope = _check(repo, "agent-control-json")

    if boundary["comparison_status"] == "not_attempted":
        assert "capability_rows" not in envelope
    else:
        assert envelope["capability_rows"]["rows"] == []
    assert "capability_rows" not in json.loads(render_agent_control_envelope(_envelope(None)))


def test_the_deprecated_codex_projection_publishes_no_rows(repo: Path) -> None:
    _widen(repo)
    assert "capability_rows" not in _check(repo, "codex-boundary-json")


# --- bounded, widenings first ---------------------------------------------------


def test_the_cap_keeps_widenings_first_and_counts_what_it_cut() -> None:
    expanding = {1, 4, 6}
    rows = [_row(index, expands=index in expanding) for index in range(8)]

    block = capability_rows_block(comparison_status="comparable", incomparable_reasons=(), rows=rows)

    assert block is not None
    assert len(block.rows) == MAX_ENVELOPE_CAPABILITY_ROWS
    assert [row.subject.rsplit("#", 1)[1] for row in block.rows] == ["1", "4", "6", "0", "2"]
    assert block.omitted_rows == 3


def test_only_the_prose_is_capped() -> None:
    long_after = "Bash(" + "x" * 2000 + ")"
    row = CapabilityDiffRow(
        subject="claude-code .claude/settings.json", before="—", after=long_after,
        direction="added", why="W" * (MAX_ENVELOPE_PROSE_BYTES * 2), severity="critical", expands=True,
    )

    block = capability_rows_block(comparison_status="comparable", incomparable_reasons=(), rows=[row])

    assert block is not None
    assert block.rows[0].after == long_after
    assert len(block.rows[0].why.encode()) <= MAX_ENVELOPE_PROSE_BYTES


def test_a_comparison_that_never_ran_has_no_block() -> None:
    assert capability_rows_block(comparison_status="not_attempted", incomparable_reasons=(), rows=[]) is None


# --- evidence, never authority --------------------------------------------------


def test_rows_change_nothing_about_authority() -> None:
    block = capability_rows_block(
        comparison_status="comparable", incomparable_reasons=(), rows=[_row(0, expands=True)]
    )
    with_rows = json.loads(render_agent_control_envelope(_envelope(block)))
    without = json.loads(render_agent_control_envelope(_envelope(None)))

    assert with_rows.pop("capability_rows")["rows"]
    assert with_rows == without


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(
            {"comparison_status": "incomparable", "incomparable_reasons": ["x"],
             "rows": [{"subject": "s", "before": "b", "after": "a", "direction": "added",
                       "severity": "high", "why": "w", "expands": True}],
             "omitted_rows": 0, "unchanged_limit_count": 0},
            id="incomparable_with_rows",
        ),
        pytest.param(
            {"comparison_status": "comparable", "incomparable_reasons": ["x"], "rows": [],
             "omitted_rows": 0, "unchanged_limit_count": 0},
            id="comparable_with_reasons",
        ),
        pytest.param(
            {"comparison_status": "comparable", "incomparable_reasons": [], "rows": [],
             "omitted_rows": 2, "unchanged_limit_count": 0},
            id="omitted_without_a_full_prefix",
        ),
        pytest.param(
            {"comparison_status": "incomparable", "incomparable_reasons": ["x"], "rows": [],
             "omitted_rows": 0, "unchanged_limit_count": 1},
            id="incomparable_with_limits",
        ),
    ],
)
def test_both_layers_refuse_a_block_that_contradicts_its_comparison(block: dict) -> None:
    payload = json.loads(render_agent_control_envelope(_envelope(None)))
    payload["capability_rows"] = block

    with pytest.raises(ValidationError):
        validate_agent_control_envelope(payload)
    assert _validate(payload)


# --- the deliberate widening ----------------------------------------------------


def _without_capability_rows(schema: dict) -> dict:
    """The published schema as a v37 reader holds it: no `capability_rows` member."""

    older = copy.deepcopy(schema)
    for definition in older.get("$defs", {}).values():
        definition.get("properties", {}).pop("capability_rows", None)
    older.get("properties", {}).pop("capability_rows", None)
    return older


def test_a_v37_validator_rejects_rows_and_still_accepts_an_envelope_without_them() -> None:
    older = _without_capability_rows(PUBLISHED_SCHEMA)
    assert "capability_rows" not in json.dumps(
        {key: value.get("properties", {}) for key, value in older["$defs"].items()}
    )
    block = capability_rows_block(
        comparison_status="comparable", incomparable_reasons=(), rows=[_row(0, expands=True)]
    )

    assert not _validate(json.loads(render_agent_control_envelope(_envelope(None))), older)
    assert _validate(json.loads(render_agent_control_envelope(_envelope(block))), older)


def test_a_full_row_block_fits_the_published_budget() -> None:
    artifacts = {
        key: AgentControlArtifactRef(
            path=f"agents-shipgate-reports/{key}.json", sha256=f"sha256:{'a' * 64}"
        )
        for key in (
            "verification_receipt", "verification_artifact_manifest", "verification_plan",
            "agent_handoff", "verifier", "verify_run", "human_authorization", "report",
            "report_markdown", "report_sarif", "packet", "pr_comment",
        )
    }
    command = "agents-shipgate verify --workspace . --config shipgate.yaml --json"
    control = derive_agent_control(
        reason="R" * MAX_ENVELOPE_PROSE_BYTES,
        next_action=CodingAgentCommandAction(
            kind="verify", command=command, why="W" * MAX_ENVELOPE_PROSE_BYTES
        ),
        verify_required=True,
        allowed_next_commands=[command],
    )
    rows = [
        CapabilityDiffRow(
            subject="claude-code .claude/settings.local.json",
            before="Bash(npm run test:unit -- --watch=false)",
            after="Bash(*)",
            direction="added",
            why="matches any command of this kind, without a prompt",
            severity="critical",
            expands=True,
        )
        for _ in range(MAX_ENVELOPE_CAPABILITY_ROWS + 3)
    ]
    envelope = project_agent_control_envelope(
        control=control,
        operation="verify",
        source="run",
        execution="succeeded",
        exit_code=0,
        decision=None,
        decision_source="none",
        artifacts=artifacts,
        current_control_id=f"sha256:{'b' * 64}",
        capability_rows=capability_rows_block(
            comparison_status="comparable", incomparable_reasons=(), rows=rows, unchanged_limit_count=2
        ),
    )

    assert len(render_agent_control_envelope(envelope).encode("utf-8")) <= AGENT_CONTROL_ENVELOPE_BUDGET_BYTES
