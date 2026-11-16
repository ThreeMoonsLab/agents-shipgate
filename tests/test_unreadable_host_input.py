"""#1154: a host configuration the reader cannot read never earns a weaker verdict.

Before this, a pull request that added ``Bash(*)`` to `.claude/settings.json`
was ``blocked`` (strict exit 20), and the same pull request with the file
padded past the reader's 1 MiB bound, given a leading ``//`` line or cut
short was ``review_required`` (strict exit 0, control ``review_publishable``):
the blocking row disappeared and two medium review rows stood in for it. A
5,000-deep file crashed `diff`, `check` and `audit --host` with a
``RecursionError`` and made `verify` report ``internal_error``. And
``check --diff`` on an empty file reported ``allow`` with ``complete``
coverage beside a control that stopped.

Pinned here:

* the six heads of the issue's table, through `verify` (strict) and `check`
  on the same refs;
* the property: replacing a changed host configuration with an unreadable
  variant (size, comment, truncation, depth) never yields a verdict weaker
  than ``insufficient_evidence``;
* the rule for an unreadable configuration the change does not touch;
* the depth refusal, typed and naming the file, on all four commands;
* the empty detached diff, and the model rule that keeps ``decision``,
  ``input_coverage`` and ``control`` from disagreeing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.agent_boundary import (
    is_unread_changed_input_gap,
    unread_changed_input_gaps,
)
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.codex_boundary import _decision_for
from agents_shipgate.core.host_grants import MAX_HOST_CONFIG_BYTES
from agents_shipgate.core.structured_depth import (
    MAX_HOST_CONFIG_DEPTH,
    NestingLimitExceeded,
    bounded_parse,
    deeper_than,
)
from agents_shipgate.mcp_server.server import shipgate_check
from agents_shipgate.schemas.agent_boundary import AgentBoundaryResultV1
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.agent_result_v1 import AgentResultViolatedRule

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "samples" / "simple_langchain_agent"
SETTINGS = ".claude/settings.json"
WILDCARD = "SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW"
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
_CLI_ENV = {
    key: value
    for key, value in os.environ.items()
    if key not in {
        "AGENTS_SHIPGATE_CLI", "AGENTS_SHIPGATE_ENABLE_PLUGINS", "CLAUDECODE", "CURSOR_TRACE_ID",
    }
}

runner = CliRunner()

BASE_SETTINGS = '{"permissions": {"allow": ["Read"], "deny": ["Bash(rm:*)"]}}'
WIDENED = '{"permissions": {"allow": ["Read", "Bash(*)"], "deny": []}}'
PAST_LIMIT = MAX_HOST_CONFIG_BYTES + 150_000

# The issue's table: head -> (the head's `.claude/settings.json`, whether the
# reader can read it).
HEADS: dict[str, tuple[str, bool]] = {
    "widened": (WIDENED, True),
    "widened_extra_key": (WIDENED[:-1] + ', "x": 1}', True),
    "padded_past_the_limit": (WIDENED + " " * PAST_LIMIT, False),
    "one_large_string_value": (WIDENED[:-1] + ', "x": "' + "a" * PAST_LIMIT + '"}', False),
    "leading_comment_line": ("// widened\n" + WIDENED, False),
    "closing_braces_missing": (WIDENED[:-2], False),
}

_DECISION_RANK = {"passed": 0, "review_required": 1, "insufficient_evidence": 2, "blocked": 3}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _repository(
    tmp_path: Path,
    head: dict[str, str],
    *,
    base: dict[str, str] | None = None,
    touch_agent: bool = False,
) -> Path:
    """The issue's sample with `base` on `main` and `head` on `change`, left checked out."""

    repo = tmp_path / "repo"
    shutil.copytree(SAMPLE, repo, ignore=shutil.ignore_patterns("expected"))
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n.agents-shipgate/\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    for path, text in (base or {SETTINGS: BASE_SETTINGS}).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "change")
    for path, text in head.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    if touch_agent:
        with (repo / "agent.py").open("a", encoding="utf-8") as handle:
            handle.write("\n# touched\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    return repo


def _verify(repo: Path) -> tuple[dict, int]:
    result = runner.invoke(
        app,
        [
            "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--base", "main",
            "--head", "change", "--ci-mode", "strict", "--json",
        ],
    )
    return json.loads(result.output), result.exit_code


def _check(repo: Path) -> dict:
    result = runner.invoke(
        app,
        [
            "check", "--agent", "claude-code", "--workspace", str(repo), "--base", "main",
            "--head", "change", "--format", "agent-boundary-json",
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _unread_gaps(verify: dict) -> list[dict]:
    from agents_shipgate.schemas.report import EvidenceGap

    return [
        gap
        for gap in verify["release_decision"]["evidence_coverage"]["evidence_gaps"]
        if is_unread_changed_input_gap(EvidenceGap.model_validate(gap))
    ]


# --- The six heads ------------------------------------------------------------


@pytest.mark.parametrize("head", sorted(HEADS))
def test_the_six_heads_through_verify_and_check(tmp_path: Path, head: str) -> None:
    text, readable = HEADS[head]
    repo = _repository(tmp_path, {SETTINGS: text})
    verify, exit_code = _verify(repo)
    check = _check(repo)

    # Strict mode fails every head, and none may be published.
    assert exit_code == 20, verify["release_decision"]
    assert verify["control"]["state"] == "human_review_required"
    assert verify["control"]["permissions"]["update_pr"] is False
    if readable:
        assert verify["decision"] == "blocked"
        assert WILDCARD in {item["check_id"] for item in verify["release_decision"]["blockers"]}
        assert _unread_gaps(verify) == []
        assert (check["decision"], check["risk_level"], check["input_coverage"]) == (
            "block", "critical", "complete",
        )
    else:
        assert verify["decision"] == "insufficient_evidence"
        gaps = _unread_gaps(verify)
        assert [(gap["source_ref"], gap["kind"]) for gap in gaps] == [
            (SETTINGS, "unknown_policy_evidence")
        ]
        assert verify["release_decision"]["evidence_coverage"]["policy_gap_count"] == 1
        assert check["input_coverage"] == "partial"
    # `check` gives the matching answer for the same refs: the control verify
    # gives, and the decision `check` projects from verify's.
    assert check["control"]["state"] == verify["control"]["state"]
    assert check["decision"] == _decision_for([], release_decision={"decision": verify["decision"]})


# --- The property ---------------------------------------------------------------


def _pad(text: str) -> str:
    return text + " " * PAST_LIMIT


def _comment(text: str) -> str:
    return "// widened\n" + text


def _truncate(text: str) -> str:
    return text.rstrip()[:-1]


def _nest(text: str) -> str:
    deep = "[" * 5000 + "]" * 5000
    if text.startswith("{"):
        return '{"x": ' + deep + ", " + text[1:]
    return f"x = {deep}\n" + text


UNREADABLE: dict[str, Callable[[str], str]] = {
    "size": _pad,
    "comment": _comment,
    "truncation": _truncate,
    "depth": _nest,
}

# A readable change that widens what the agent may do, per host file.
WIDENINGS: dict[str, tuple[str, str, str]] = {
    "claude_wildcard_allow": (SETTINGS, BASE_SETTINGS, WIDENED),
    "mcp_server_added": (
        ".mcp.json",
        '{"mcpServers": {}}',
        '{"mcpServers": {"shell": {"command": "bash", "args": ["-lc", "x"]}}}',
    ),
    "codex_full_access": (
        ".codex/config.toml",
        'sandbox_mode = "read-only"\n',
        'sandbox_mode = "danger-full-access"\n',
    ),
}


@pytest.mark.parametrize("widening", sorted(WIDENINGS))
def test_each_readable_widening_is_judged_on_its_rows(tmp_path: Path, widening: str) -> None:
    """The control for the property below: read, the change is a finding, not a gap."""

    path, base, head = WIDENINGS[widening]
    read, _exit = _verify(_repository(tmp_path, {path: head}, base={path: base}))
    assert _unread_gaps(read) == []
    assert read["release_decision"]["blockers"] or read["release_decision"]["review_items"]


@pytest.mark.parametrize("transform", sorted(UNREADABLE))
@pytest.mark.parametrize("widening", sorted(WIDENINGS))
def test_an_unreadable_variant_never_weakens_the_verdict(
    tmp_path: Path, widening: str, transform: str
) -> None:
    """Whatever the readable change earned, its unreadable variant earns at least
    `insufficient_evidence`, fails strict mode and is never published."""

    path, base, head = WIDENINGS[widening]
    unread, exit_code = _verify(
        _repository(tmp_path, {path: UNREADABLE[transform](head)}, base={path: base})
    )

    assert _DECISION_RANK[unread["decision"]] >= _DECISION_RANK["insufficient_evidence"]
    assert exit_code == 20
    assert unread["control"]["state"] == "human_review_required"
    assert not any(unread["control"]["permissions"].values())
    assert [gap["source_ref"] for gap in _unread_gaps(unread)] == [path]
    assert "RecursionError" not in json.dumps(unread)


# --- What "changed" means -------------------------------------------------------


def test_an_unchanged_unreadable_config_keeps_its_review_row(tmp_path: Path) -> None:
    """Identical on both sides by git identity: no gap, and never weaker than read.

    A readable configuration the change does not touch contributes no row, so
    its unreadable copy keeps the review row it already raised; holding every
    pull request in the repository at `insufficient_evidence` over a file none
    of them touches would fail strict CI on noise.
    """

    unread_base = {SETTINGS: BASE_SETTINGS + " " * PAST_LIMIT}
    unread = _repository(tmp_path / "unread", {}, base=unread_base, touch_agent=True)
    read = _repository(tmp_path / "read", {}, touch_agent=True)
    assert _git(unread, "diff", "--name-only", "main", "change") == "agent.py"

    unread_verify, _ = _verify(unread)
    read_verify, _ = _verify(read)

    assert _unread_gaps(unread_verify) == []
    assert unread_verify["decision"] != "insufficient_evidence"
    assert _DECISION_RANK[unread_verify["decision"]] >= _DECISION_RANK[read_verify["decision"]]
    assert "SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE" in {
        item["check_id"] for item in unread_verify["release_decision"]["review_items"]
    }


def _row(rule_id: str, path: str, **evidence: object) -> AgentResultViolatedRule:
    return AgentResultViolatedRule(
        id=rule_id,
        check_id=f"SHIP-{rule_id}",
        action="require_review",
        risk_level="medium",
        title="row",
        path=path,
        evidence=evidence,
        recommendation="review",
    )


def test_only_a_changed_files_failed_read_is_a_gap() -> None:
    """Rows that do not say a changed file's bytes went unread keep their review route."""

    changed = [
        SETTINGS, ".mcp.json", ".claude/agents/reviewer.md", ".cursor/mcp.json", "retired.json",
    ]
    rows = [
        # Read: the structure of a subagent file is not modelled.
        _row(
            "BOUNDARY-INPUT-INCOMPLETE", ".claude/agents/reviewer.md",
            kind="instruction_structure_unresolved",
        ),
        # A trust root renamed out of a boundary path.
        _row(
            "BOUNDARY-INPUT-INCOMPLETE", "retired.json",
            kind="boundary_input_unresolved", code="boundary_rename_out_requires_review",
        ),
        # Read: a key outside the rule allow-list (#810).
        _row("HOST-CONFIG-PARSE-FAILED", ".cursor/mcp.json", kind="unknown_host_config_key"),
        # Not read, but git reports it unchanged.
        _row("HOST-CONFIG-PARSE-FAILED", ".vscode/mcp.json", kind="json_parse_failed"),
        # Not read, and changed: one gap per path, naming the parser's error.
        _row(
            "BOUNDARY-INPUT-INCOMPLETE", SETTINGS,
            kind="boundary_input_unresolved", code="host_inventory_parse_failed",
        ),
        _row(
            "HOST-CONFIG-PARSE-FAILED", SETTINGS,
            kind="json_parse_failed", parser="JSONDecodeError", line=1,
        ),
        _row("HOST-CONFIG-PARSE-FAILED", ".mcp.json", kind="host_config_content_unresolved"),
    ]
    gaps = unread_changed_input_gaps(SimpleNamespace(violations=rows), changed)

    assert [(gap.source_ref, gap.policy_id) for gap in gaps] == [
        (".claude/settings.json", "SHIP-HOST-CONFIG-PARSE-FAILED"),
        (".mcp.json", "SHIP-HOST-CONFIG-PARSE-FAILED"),
    ]
    assert "(JSONDecodeError at line 1)" in gaps[0].why
    assert all(is_unread_changed_input_gap(gap) for gap in gaps)
    assert all(gap.kind == "unknown_policy_evidence" for gap in gaps)
    assert all(gap.next_action.authorable_by == "human" for gap in gaps)


def test_a_change_that_makes_an_unreadable_config_readable_is_judged_on_its_rows(
    tmp_path: Path,
) -> None:
    unread_base = {SETTINGS: BASE_SETTINGS + " " * PAST_LIMIT}
    repo = _repository(tmp_path, {SETTINGS: WIDENED}, base=unread_base)
    verify, exit_code = _verify(repo)
    assert (verify["decision"], exit_code) == ("blocked", 20)
    assert _unread_gaps(verify) == []


# --- Depth ------------------------------------------------------------------------


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agents_shipgate", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**_CLI_ENV, "PYTHONPATH": str(REPO_ROOT / "src")},
    )


def test_a_deeply_nested_config_is_a_typed_refusal_on_every_command(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {SETTINGS: _nest(WIDENED)})
    workspace = ["--workspace", str(repo)]
    runs = {
        "diff": _cli(repo, "diff", *workspace, "--base", "main", "--json"),
        "check": _cli(
            repo, "check", "--agent", "claude-code", *workspace, "--base", "main",
            "--format", "agent-boundary-json",
        ),
        "audit": _cli(repo, "audit", "--host", *workspace, "--json"),
        "verify": _cli(
            repo, "verify", *workspace, "--config", "shipgate.yaml", "--base", "main",
            "--ci-mode", "strict", "--json",
        ),
    }
    refusal = f"nested more than {MAX_HOST_CONFIG_DEPTH} levels deep"
    for command, run in runs.items():
        assert "RecursionError" not in run.stderr, (command, run.stderr[-2000:])
        assert "internal_error" not in run.stdout, (command, run.stdout[:2000])
        assert run.returncode in {0, 20}, (command, run.returncode, run.stderr[-2000:])

    diff = json.loads(runs["diff"].stdout)
    assert diff["comparison_status"] == "incomparable"
    assert [
        (item["source"], item["limit"], item["detail"]) for item in diff["coverage"]["items"]
    ] == [(SETTINGS, "parse_failed", f"static parser rejected this json file ({refusal})")]

    audit = json.loads(runs["audit"].stdout)
    assert [
        (item["source"], item["kind"], item["message"], item["blocking"])
        for item in audit["issues"]
    ] == [(SETTINGS, "parse_failed", f"static parser rejected this json file ({refusal})", True)]

    check = json.loads(runs["check"].stdout)
    assert check["input_coverage"] == "partial"
    assert {
        (item["path"], item["evidence"].get("parser"), item["evidence"].get("max_depth"))
        for item in check["violations"]
        if item["id"] == "HOST-CONFIG-PARSE-FAILED"
    } == {(SETTINGS, "NestingLimitExceeded", MAX_HOST_CONFIG_DEPTH)}

    verify = json.loads(runs["verify"].stdout)
    assert (verify["decision"], runs["verify"].returncode) == ("insufficient_evidence", 20)
    assert [gap["source_ref"] for gap in _unread_gaps(verify)] == [SETTINGS]
    assert refusal in _unread_gaps(verify)[0]["why"]


@pytest.mark.parametrize(
    "loads,text",
    [
        (json.loads, "[" * 5000 + "]" * 5000),
        (json.loads, "{" + '"a": {' * (MAX_HOST_CONFIG_DEPTH) + "}" * (MAX_HOST_CONFIG_DEPTH + 1)),
        (__import__("tomllib").loads, "x = " + "[" * 5000 + "]" * 5000),
        (__import__("tomllib").loads, "[" + ".".join(["a"] * (MAX_HOST_CONFIG_DEPTH + 1)) + "]"),
        (__import__("yaml").safe_load, "[" * 5000 + "]" * 5000),
    ],
    ids=["json_array", "json_object", "toml_array", "toml_tables", "yaml_flow"],
)
def test_bounded_parse_refuses_past_the_bound(loads, text: str) -> None:
    with pytest.raises(NestingLimitExceeded):
        bounded_parse(loads, text)


def test_bounded_parse_keeps_what_is_within_the_bound() -> None:
    within = "[" * MAX_HOST_CONFIG_DEPTH + "]" * MAX_HOST_CONFIG_DEPTH
    assert not deeper_than(bounded_parse(json.loads, within))
    assert bounded_parse(json.loads, WIDENED) == json.loads(WIDENED)


def test_shared_and_cyclic_structures_are_bounded() -> None:
    import yaml

    cyclic = yaml.safe_load("&a [*a]")
    assert deeper_than(cyclic)
    # Nine levels of nine shared aliases: 9**9 paths through 81 nodes.
    lines = ["a0: &a0 [x]"]
    for level in range(1, 9):
        lines.append(f"a{level}: &a{level} [" + ", ".join([f"*a{level - 1}"] * 9) + "]")
    shared = yaml.safe_load("\n".join(lines))
    assert not deeper_than(shared)


# --- The empty detached diff ----------------------------------------------------


def test_an_empty_diff_reports_no_pass(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {SETTINGS: WIDENED})
    empty = tmp_path / "empty.diff"
    empty.write_text("", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "check", "--agent", "claude-code", "--workspace", str(repo), "--diff", str(empty),
            "--format", "agent-boundary-json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    mcp = shipgate_check(workspace=str(repo), diff_text="")
    for answer in (payload, mcp):
        assert answer["decision"] != "allow"
        assert answer["input_coverage"] != "complete"
        assert "boundary_diff_empty" in answer["issues"]
        assert answer["control"]["state"] == "human_review_required"
        assert not any(answer["control"]["permissions"].values())
        AgentBoundaryResultV1.model_validate(answer)


def test_a_bound_empty_change_is_still_an_evaluated_pass(tmp_path: Path) -> None:
    """`check --base HEAD` on a clean tree is an evaluated "nothing changed"."""

    repo = _repository(tmp_path, {SETTINGS: BASE_SETTINGS + "\n"})
    result = runner.invoke(
        app,
        [
            "check", "--agent", "claude-code", "--workspace", str(repo), "--base", "HEAD",
            "--format", "agent-boundary-json",
        ],
    )
    payload = json.loads(result.output)
    assert "boundary_diff_empty" not in payload["issues"]
    assert payload["input_coverage"] == "complete"


def _boundary_payload(**overrides: object) -> dict:
    """A valid stopped boundary result, before the fields a case overrides."""

    why = "Review the change."
    control = derive_agent_control(
        reason=why,
        next_action=HumanControlAction(kind="review", why=why),
        human_review_required=True,
        human_review_why=why,
        stop_reason=why,
    )
    payload = {
        "agent": "codex",
        "decision": "require_review",
        "risk_level": "medium",
        "audit_id": "agent_boundary_test",
        "policy_version": "1",
        "summary": why,
        "control": control.model_dump(mode="json"),
        "policy": {"id": "agent-boundary", "version": "1", "source": "packaged_default"},
        "actor": "codex",
        "input_mode": "provided_diff",
        "input_coverage": "complete",
        "host_coverage": [],
        "affected_hosts": [],
        "policies": [],
        "policy_set_sha256": "0" * 64,
        "violations": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("coverage", ["partial", "unknown"])
def test_the_model_refuses_a_pass_on_input_not_completely_read(coverage: str) -> None:
    AgentBoundaryResultV1.model_validate(_boundary_payload(input_coverage=coverage))
    with pytest.raises(ValidationError, match="allow decision requires complete"):
        AgentBoundaryResultV1.model_validate(
            _boundary_payload(decision="allow", risk_level="none", input_coverage=coverage)
        )


def test_decision_coverage_and_control_cannot_disagree() -> None:
    """The model's three pairwise rules, each refused alone."""

    complete = derive_agent_control(reason="Evaluated.", subject_evaluated=True)
    publishable = derive_agent_control(
        reason="Review.",
        next_action=HumanControlAction(kind="review", why="Review."),
        human_review_required=True,
        publication_allowed=True,
    )
    # decision vs coverage: no pass on input not completely read.
    with pytest.raises(ValidationError, match="allow decision requires complete"):
        AgentBoundaryResultV1.model_validate(
            _boundary_payload(decision="allow", risk_level="none", input_coverage="partial")
        )
    # coverage vs control: incomplete input neither completes nor publishes.
    with pytest.raises(ValidationError, match="incomplete boundary input cannot allow completion"):
        AgentBoundaryResultV1.model_validate(
            _boundary_payload(
                decision="allow", risk_level="none", input_coverage="unknown",
                control=complete.model_dump(mode="json"),
            )
        )
    with pytest.raises(ValidationError, match="publication authority requires complete"):
        AgentBoundaryResultV1.model_validate(
            _boundary_payload(
                input_coverage="partial", control=publishable.model_dump(mode="json")
            )
        )
    # decision vs control: a block is never published.
    with pytest.raises(ValidationError, match="blocked result cannot authorize publication"):
        AgentBoundaryResultV1.model_validate(
            _boundary_payload(decision="block", control=publishable.model_dump(mode="json"))
        )
