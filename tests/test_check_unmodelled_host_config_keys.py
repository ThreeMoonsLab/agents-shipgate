"""#810: ``check`` on host settings that set a key Shipgate does not model.

A Claude Code or Cursor settings file that parses but sets a top-level key
outside the host-boundary allow-list (``enabledPlugins``,
``extraKnownMarketplaces``, ``outputStyle``, ...) produces a
``HOST-CONFIG-PARSE-FAILED`` row with evidence kind
``unknown_host_config_key``. The publication predicate counted that row as
read input, while boundary input coverage counted it as unread, so the
projection built a result whose control could publish with ``partial``
coverage and the schema rejected it: every maintained ``check`` format exited
1 with a traceback.

Both now share one answer (``parse_failure_kind_was_read``). These tests pin:

* every variant answers in text, agent-boundary-json and agent-control-json,
  in worktree and git_range modes, with ``complete`` coverage;
* the authority those answers carry: never ``allow``, never ``complete``,
  never merge or report_complete, and a wildcard beside the key still blocks;
* content that was not read stays ``partial`` and unpublished;
* the audit id the deprecated projection already published is unchanged;
* the two intended changes on input that did not crash: a mixed change now
  reports complete coverage, and ``check --diff`` / MCP ``shipgate.check``
  become comparable with rows.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agents_shipgate.core.codex_boundary as codex_boundary
from agents_shipgate.cli.main import app
from agents_shipgate.core.agent_boundary import _coverage_for
from agents_shipgate.core.codex_boundary import (
    boundary_assessment_is_evidence_backed,
    violations_within_agent_actionable_band,
)
from agents_shipgate.mcp_server.server import shipgate_check
from agents_shipgate.schemas.agent_result_v1 import AgentResultViolatedRule

runner = CliRunner()

_READ = {"permissions": {"allow": ["Read(**)"]}}


def _settings(**extra: object) -> str:
    return json.dumps({**_READ, **extra})


# name -> (path, base text, head text, unmodelled key, expected decision,
# expected control state). The key-only changes route through the graded
# review band; a high-risk allow rule beside the key keeps its human route.
_VARIANTS = {
    "enabled_plugins_added": (
        ".claude/settings.json",
        _settings(),
        _settings(enabledPlugins={"x@y": True}),
        "enabledPlugins",
        "require_review",
        "agent_action_required",
    ),
    "enabled_plugins_added_disabled": (
        ".claude/settings.json",
        _settings(),
        _settings(enabledPlugins={"x@y": False}),
        "enabledPlugins",
        "require_review",
        "agent_action_required",
    ),
    "enabled_plugins_true_to_false": (
        ".claude/settings.json",
        _settings(enabledPlugins={"x@y": True}),
        _settings(enabledPlugins={"x@y": False}),
        "enabledPlugins",
        "require_review",
        "agent_action_required",
    ),
    "extra_known_marketplaces_only": (
        ".claude/settings.json",
        _settings(),
        _settings(
            extraKnownMarketplaces={
                "m": {"source": {"source": "github", "repo": "o/r"}}
            }
        ),
        "extraKnownMarketplaces",
        "require_review",
        "agent_action_required",
    ),
    "output_style": (
        ".claude/settings.json",
        _settings(),
        _settings(outputStyle="Explanatory"),
        "outputStyle",
        "require_review",
        "agent_action_required",
    ),
    "enable_all_project_mcp_servers": (
        ".claude/settings.json",
        _settings(),
        _settings(enableAllProjectMcpServers=True),
        "enableAllProjectMcpServers",
        "require_review",
        "agent_action_required",
    ),
    "enabled_mcpjson_servers": (
        ".claude/settings.json",
        _settings(),
        _settings(enabledMcpjsonServers=["local"]),
        "enabledMcpjsonServers",
        "require_review",
        "agent_action_required",
    ),
    "local_settings_enabled_plugins": (
        ".claude/settings.local.json",
        _settings(),
        _settings(enabledPlugins={"x@y": True}),
        "enabledPlugins",
        "require_review",
        "agent_action_required",
    ),
    "cursor_cli_unknown_key": (
        ".cursor/cli.json",
        _settings(),
        _settings(editor={"vimMode": True}),
        "editor",
        "require_review",
        "agent_action_required",
    ),
    "shell_allow_rule_plus_unknown_key": (
        ".claude/settings.json",
        _settings(),
        json.dumps(
            {
                "permissions": {"allow": ["Read(**)", "Bash(npm test:*)"]},
                "outputStyle": "Explanatory",
            }
        ),
        "outputStyle",
        "require_review",
        "review_publishable",
    ),
}

_ADAPTER_FOR_PATH = {
    ".claude/settings.json": "claude_code",
    ".claude/settings.local.json": "claude_code",
    ".cursor/cli.json": "cursor",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(
    tmp_path: Path,
    path: str,
    base: str,
    head: str,
    *,
    mode: str,
    extra_base_files: dict[str, str] | None = None,
) -> list[str]:
    """Commit ``base`` on main, apply ``head`` for ``mode``; return range args."""

    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(base + "\n", encoding="utf-8")
    for relative, text in (extra_base_files or {}).items():
        extra = tmp_path / relative
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text(text, encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    if mode == "worktree":
        target.write_text(head + "\n", encoding="utf-8")
        return []
    _git(tmp_path, "checkout", "-qb", "change")
    target.write_text(head + "\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "change")
    return ["--base", "main", "--head", "change"]


def _check(tmp_path: Path, range_args: list[str], format_: str, agent: str = "claude-code"):
    invoked = runner.invoke(
        app,
        [
            "check",
            "--agent",
            agent,
            "--workspace",
            str(tmp_path),
            *range_args,
            "--format",
            format_,
        ],
    )
    assert invoked.exit_code == 0, invoked.output
    return invoked


def _no_merge_authority(permissions: dict[str, bool]) -> None:
    assert permissions["merge"] is False
    assert permissions["report_complete"] is False


@pytest.mark.parametrize("mode", ["worktree", "git_range"])
@pytest.mark.parametrize("variant", sorted(_VARIANTS))
def test_check_answers_an_unmodelled_settings_key_in_every_format(
    tmp_path: Path, variant: str, mode: str
) -> None:
    path, base, head, key, decision, state = _VARIANTS[variant]
    range_args = _repo(tmp_path, path, base, head, mode=mode)

    boundary = json.loads(_check(tmp_path, range_args, "agent-boundary-json").output)
    assert boundary["input_mode"] == mode
    # The file was read: coverage is complete even though the key's meaning
    # is not modelled, which is what the row below still owes review for.
    assert boundary["input_coverage"] == "complete"
    assert boundary["issues"] == []
    assert {
        item["adapter"]: (item["status"], item["paths"])
        for item in boundary["host_coverage"]
        if item["status"] != "not_applicable"
    } == {_ADAPTER_FOR_PATH[path]: ("complete", [path])}
    assert {
        "id": "HOST-CONFIG-PARSE-FAILED",
        "evidence": {"kind": "unknown_host_config_key", "key": key},
    } in [
        {"id": item["id"], "evidence": item["evidence"]}
        for item in boundary["violations"]
    ]
    # Authority: never allow, never complete, never merge.
    assert boundary["decision"] == decision
    assert boundary["control"]["state"] == state
    assert boundary["control"]["completion_allowed"] is False
    _no_merge_authority(boundary["control"]["permissions"])
    if state == "agent_action_required":
        assert boundary["control"]["next_action"]["kind"] == "verify"
        assert "HOST-CONFIG-PARSE-FAILED" in [
            item["rule_id"] for item in boundary["pending_review"]
        ]

    control = json.loads(_check(tmp_path, range_args, "agent-control-json").output)
    assert control["decision"] == decision
    assert control["control_state"] == state
    assert control["permissions"] == boundary["control"]["permissions"]
    assert control["input_id"] == boundary["audit_id"]

    text = _check(tmp_path, range_args, "text").output
    assert f"Control: {state}" in text
    assert "You may not: merge, report_complete" in text


@pytest.mark.parametrize("agent", ["codex", "cursor"])
def test_every_calling_agent_gets_the_same_answer(tmp_path: Path, agent: str) -> None:
    path, base, head, _key, decision, state = _VARIANTS["enabled_plugins_added"]
    range_args = _repo(tmp_path, path, base, head, mode="git_range")

    caller = json.loads(
        _check(tmp_path, range_args, "agent-boundary-json", agent=agent).output
    )
    claude = json.loads(_check(tmp_path, range_args, "agent-boundary-json").output)

    assert (caller["decision"], caller["control"]["state"]) == (decision, state)
    assert caller["input_coverage"] == "complete"
    assert caller["host_coverage"] == claude["host_coverage"]


@pytest.mark.parametrize("mode", ["worktree", "git_range"])
def test_audit_id_matches_the_deprecated_projection_that_never_crashed(
    tmp_path: Path, mode: str
) -> None:
    """The fix changes coverage, not the assessment's identity.

    ``codex-boundary-json`` bypassed the v3 validator and already published
    this input's audit id; the current contract must name the same one.
    """

    path, base, head, *_ = _VARIANTS["enabled_plugins_added"]
    range_args = _repo(tmp_path, path, base, head, mode=mode)

    legacy = json.loads(_check(tmp_path, range_args, "codex-boundary-json").output)
    current = json.loads(_check(tmp_path, range_args, "agent-boundary-json").output)

    assert current["audit_id"] == legacy["audit_id"]
    assert legacy["decision"] == current["decision"] == "require_review"


@pytest.mark.parametrize("mode", ["worktree", "git_range"])
def test_wildcard_beside_an_unmodelled_key_still_blocks_with_complete_coverage(
    tmp_path: Path, mode: str
) -> None:
    """Intended change on an input that did not crash.

    ``Bash(*)`` plus ``enabledPlugins`` was already a block; its coverage read
    ``partial`` only because the plugin key was miscounted as unread. The
    decision, control, permissions and audit id are unchanged.
    """

    range_args = _repo(
        tmp_path,
        ".claude/settings.json",
        _settings(),
        json.dumps(
            {
                "permissions": {"allow": ["Read(**)", "Bash(*)"]},
                "enabledPlugins": {"x@y": True},
            }
        ),
        mode=mode,
    )

    boundary = json.loads(_check(tmp_path, range_args, "agent-boundary-json").output)
    assert boundary["decision"] == "block"
    assert boundary["control"]["state"] == "human_review_required"
    assert not any(boundary["control"]["permissions"].values())
    assert boundary["input_coverage"] == "complete"
    assert [
        item["status"]
        for item in boundary["host_coverage"]
        if item["adapter"] == "claude_code"
    ] == ["complete"]
    assert {item["id"] for item in boundary["violations"]} == {
        "HOST-PERMISSION-WILDCARD-ALLOW",
        "HOST-CONFIG-PARSE-FAILED",
    }

    control = json.loads(_check(tmp_path, range_args, "agent-control-json").output)
    assert (control["decision"], control["control_state"]) == (
        "block",
        "human_review_required",
    )
    assert not any(control["permissions"].values())

    legacy = json.loads(_check(tmp_path, range_args, "codex-boundary-json").output)
    assert legacy["audit_id"] == boundary["audit_id"]

    assert "Control: human_review_required" in _check(tmp_path, range_args, "text").output


@pytest.mark.parametrize("mode", ["worktree", "git_range"])
def test_unreadable_settings_stay_partial_and_unpublished(
    tmp_path: Path, mode: str
) -> None:
    """Sharing the read/unread answer must not reach content that was unread."""

    range_args = _repo(
        tmp_path,
        ".claude/settings.json",
        _settings(),
        '{"permissions": {"allow": ["Read(**)"]}, "enabledPlugins": ',
        mode=mode,
    )

    boundary = json.loads(_check(tmp_path, range_args, "agent-boundary-json").output)
    assert boundary["input_coverage"] == "partial"
    assert [
        item["status"]
        for item in boundary["host_coverage"]
        if item["adapter"] == "claude_code"
    ] == ["partial"]
    assert "json_parse_failed" in [
        item["evidence"].get("kind")
        for item in boundary["violations"]
        if item["id"] == "HOST-CONFIG-PARSE-FAILED"
    ]
    assert boundary["control"]["state"] == "human_review_required"
    assert not any(boundary["control"]["permissions"].values())

    control = json.loads(_check(tmp_path, range_args, "agent-control-json").output)
    assert control["control_state"] == "human_review_required"
    assert not any(control["permissions"].values())
    assert "Control: human_review_required" in _check(tmp_path, range_args, "text").output


def test_an_unmodelled_key_does_not_explain_an_input_issue(tmp_path: Path) -> None:
    """A read row cannot stand in for the generic incomplete-input row.

    An unreadable legacy host policy is a warning-level input issue with no
    row of its own. The unknown-key row used to suppress
    ``BOUNDARY-INPUT-INCOMPLETE`` as though it explained that issue, leaving
    partial coverage beside a publishable control: the same crash. It now
    stops for a human exactly as a shell change beside the same policy does.
    """

    range_args = _repo(
        tmp_path,
        ".claude/settings.json",
        _settings(),
        _settings(enabledPlugins={"x@y": True}),
        mode="worktree",
        extra_base_files={"policies/host-boundary.shipgate.yaml": "rules: [unclosed\n"},
    )

    boundary = json.loads(_check(tmp_path, range_args, "agent-boundary-json").output)
    assert boundary["input_coverage"] == "partial"
    assert boundary["issues"] == ["policy_load_failed"]
    assert {item["id"] for item in boundary["violations"]} == {
        "BOUNDARY-INPUT-INCOMPLETE",
        "HOST-CONFIG-PARSE-FAILED",
    }
    assert boundary["control"]["state"] == "human_review_required"
    assert not any(boundary["control"]["permissions"].values())

    control = json.loads(_check(tmp_path, range_args, "agent-control-json").output)
    assert control["control_state"] == "human_review_required"
    assert "Control: human_review_required" in _check(tmp_path, range_args, "text").output


@pytest.mark.parametrize(
    ("head", "decision", "row_count"),
    [
        (_settings(enabledPlugins={"x@y": True}), "require_review", 1),
        (
            json.dumps(
                {
                    "permissions": {"allow": ["Read(**)", "Bash(*)"]},
                    "enabledPlugins": {"x@y": True},
                }
            ),
            "block",
            2,
        ),
    ],
    ids=["enabled_plugins_added", "wildcard_plus_enabled_plugins"],
)
def test_provided_diff_with_an_unmodelled_key_is_comparable(
    tmp_path: Path, head: str, decision: str, row_count: int
) -> None:
    """Intended change on an input that did not crash.

    A detached diff never publishes, so ``check --diff`` and MCP
    ``shipgate.check`` answered, but ``partial`` coverage withheld the host
    comparison: ``incomparable`` / ``diff_input_coverage_incomplete`` with no
    rows. Complete coverage now carries the rows.
    """

    _repo(tmp_path, ".claude/settings.json", _settings(), head, mode="worktree")
    diff_text = subprocess.run(
        ["git", "diff"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout
    diff_path = tmp_path.parent / f"{tmp_path.name}.diff"
    diff_path.write_text(diff_text, encoding="utf-8")

    cli = json.loads(
        _check(tmp_path, ["--diff", str(diff_path)], "agent-boundary-json").output
    )
    mcp = shipgate_check(agent="claude-code", workspace=str(tmp_path), diff_text=diff_text)

    for payload in (cli, mcp):
        assert payload["input_mode"] == "provided_diff"
        assert payload["input_coverage"] == "complete"
        assert payload["comparison_status"] == "comparable"
        assert payload["incomparable_reasons"] == []
        assert len(payload["rows"]) == row_count
        assert payload["decision"] == decision
        # Unbound to a checkout, so still nothing to publish.
        assert payload["control"]["state"] == "human_review_required"
        assert not any(payload["control"]["permissions"].values())
    assert cli["audit_id"] == mcp["audit_id"]


# --- the shared read/unread answer ------------------------------------------


def _row(kind: str, *, rule: str = "HOST-CONFIG-PARSE-FAILED") -> AgentResultViolatedRule:
    return AgentResultViolatedRule(
        id=rule,
        check_id=f"SHIP-{rule}",
        action="require_review",
        risk_level="medium",
        title="Host configuration could not be parsed",
        path=".claude/settings.json",
        evidence={"kind": kind},
        recommendation="Review the change.",
    )


def _claude_status(rows: list[AgentResultViolatedRule]) -> str:
    coverage = _coverage_for(
        changed_files=[".claude/settings.json"], violations=rows, issues=[]
    )
    return next(item.status for item in coverage if item.adapter == "claude_code")


def test_coverage_and_publication_read_parse_failure_rows_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coverage cannot drift from publication again.

    A kind the publication predicate treats as read input is read input for
    coverage too, including one added later whose name happens to say
    ``parse`` — the substring coverage otherwise uses to spot unread content.
    """

    monkeypatch.setattr(
        codex_boundary,
        "_PARSEABLE_EVIDENCE_KINDS",
        codex_boundary._PARSEABLE_EVIDENCE_KINDS | {"parsed_unmodelled_value"},
    )
    for kind in sorted(codex_boundary._PARSEABLE_EVIDENCE_KINDS):
        rows = [_row(kind)]
        assert boundary_assessment_is_evidence_backed(rows) is True, kind
        assert violations_within_agent_actionable_band(rows) is True, kind
        assert _claude_status(rows) == "complete", kind


@pytest.mark.parametrize(
    ("rule", "kind"),
    [
        ("HOST-CONFIG-PARSE-FAILED", "json_parse_failed"),
        ("HOST-CONFIG-PARSE-FAILED", "old_json_parse_failed"),
        ("HOST-CONFIG-PARSE-FAILED", "host_config_content_unresolved"),
        ("CODEX-CONFIG-PARSE-FAILED", "toml_parse_failed"),
        ("CODEX-CONFIG-PARSE-FAILED", "codex_config_content_unresolved"),
        ("CODEX-CONFIG-PARSE-FAILED", "hooks_json_parse_failed"),
    ],
)
def test_unread_parse_failure_rows_stay_partial_and_unpublished(
    rule: str, kind: str
) -> None:
    rows = [_row(kind, rule=rule)]

    assert boundary_assessment_is_evidence_backed(rows) is False
    assert violations_within_agent_actionable_band(rows) is False
    assert _claude_status(rows) == "partial"
