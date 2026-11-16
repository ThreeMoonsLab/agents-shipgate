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
* the property: replacing a changed host configuration (Claude Code
  settings, `.mcp.json`, Codex config, an OpenShell policy) with an
  unreadable variant (size, comment, truncation, depth, and for JSON a top
  level that is not an object) never yields a verdict weaker than
  ``insufficient_evidence``, while the readable change, with or without a
  final newline, is judged on its rows;
* a settings shape the reader cannot interpret, and an unparseable base;
* the rule for an unreadable configuration the change does not touch;
* the depth refusal, typed and naming the file, on all four commands;
* detached diffs, and the model rules that keep ``decision``,
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
from tests.test_openshell_inputs import POLICY as OPENSHELL_BASE
from tests.test_openshell_inputs import REGISTRATION as OPENSHELL_REGISTRATION
from tests.test_openshell_inputs import selection as openshell_selection

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
    empty_base: bool = False,
) -> Path:
    """The issue's sample with `base` on `main` and `head` on `change`, left checked out.

    The base holds `.claude/settings.json` as ``BASE_SETTINGS`` unless ``base``
    names its files, or ``empty_base`` says it holds none of its own.
    """

    repo = tmp_path / "repo"
    shutil.copytree(SAMPLE, repo, ignore=shutil.ignore_patterns("expected"))
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n.agents-shipgate/\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    for path, text in ({} if empty_base else base or {SETTINGS: BASE_SETTINGS}).items():
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


def _pad(text: str, path: str) -> str:
    return text + " " * PAST_LIMIT


def _comment(text: str, path: str) -> str:
    return "// widened\n" + text


def _truncate(text: str, path: str) -> str:
    return text.rstrip()[:-1]


def _nest(text: str, path: str) -> str:
    deep = "[" * 5000 + "]" * 5000
    if path.endswith(".toml"):
        return f"x = {deep}\n" + text
    if text.lstrip().startswith("{"):
        return '{"x": ' + deep + ", " + text.lstrip()[1:]
    return text.rstrip("\n") + f"\nx: {deep}\n"


def _shape(text: str, path: str) -> str:
    """Valid JSON whose top level is not the object every host reads."""

    return "[" + text.strip() + "]"


UNREADABLE: dict[str, Callable[[str, str], str]] = {
    "size": _pad,
    "comment": _comment,
    "truncation": _truncate,
    "depth": _nest,
    "shape": _shape,
}

OPENSHELL_POLICY = "arbitrary.rules"

# A readable change that widens what the agent may do, per host reader:
# (path, base, head, the check its row raises, other base files).
WIDENINGS: dict[str, tuple[str, str, str, str, dict[str, str]]] = {
    "claude_wildcard_allow": (SETTINGS, BASE_SETTINGS, WIDENED, WILDCARD, {}),
    "mcp_server_added": (
        ".mcp.json",
        '{"mcpServers": {}}',
        '{"mcpServers": {"shell": {"command": "bash", "args": ["-lc", "x"]}}}',
        "SHIP-HOST-BOUNDARY-MCP-SERVER-ADDED",
        {},
    ),
    "codex_full_access": (
        ".codex/config.toml",
        'sandbox_mode = "read-only"\n',
        'sandbox_mode = "danger-full-access"\n',
        "SHIP-CODEX-BOUNDARY-DANGER-FULL-ACCESS",
        {},
    ),
    "openshell_enforcement_relaxed": (
        OPENSHELL_POLICY,
        OPENSHELL_BASE,
        OPENSHELL_BASE.replace("enforcement: enforce", "enforcement: audit"),
        "SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED",
        {OPENSHELL_REGISTRATION: json.dumps(openshell_selection(OPENSHELL_POLICY))},
    ),
}
#: The transformations that mean something for a reader's format: a TOML or
#: YAML document has no JSON top level to get wrong.
_JSON_WIDENINGS = {"claude_wildcard_allow", "mcp_server_added"}
PROPERTY_CASES = [
    (widening, transform)
    for widening in sorted(WIDENINGS)
    for transform in sorted(UNREADABLE)
    if transform != "shape" or widening in _JSON_WIDENINGS
]


def _widening_repo(tmp_path: Path, widening: str, head_text: str, *, new: bool = False) -> Path:
    path, base, _head, _check_id, extra = WIDENINGS[widening]
    files = {**extra, **({} if new else {path: base})}
    return _repository(tmp_path, {path: head_text}, base=files, empty_base=not files)


def test_a_readable_openshell_widening_is_judged_on_its_rows(tmp_path: Path) -> None:
    path, _base, head, check_id, _extra = WIDENINGS["openshell_enforcement_relaxed"]
    read, _exit = _verify(_widening_repo(tmp_path, "openshell_enforcement_relaxed", head))
    assert _unread_gaps(read) == []
    assert check_id in {item["check_id"] for item in read["release_decision"]["review_items"]}


@pytest.mark.parametrize("ending", ["newline", "no_final_newline"])
@pytest.mark.parametrize("change", ["edited", "new"])
@pytest.mark.parametrize("widening", sorted(_JSON_WIDENINGS | {"codex_full_access"}))
def test_each_readable_widening_is_judged_on_its_rows(
    tmp_path: Path, widening: str, change: str, ending: str
) -> None:
    """The control for the property below: read, the change is a finding, not a gap.

    Whether the file ends in a newline changes nothing (#1154 review): Git's
    `\\ No newline at end of file` marker was dropped, the rebuilt file got a
    newline it does not have, and a readable new file read as unresolved.
    """

    path, _base, head, check_id, _extra = WIDENINGS[widening]
    text = head.rstrip("\n") + ("\n" if ending == "newline" else "")
    read, _exit = _verify(_widening_repo(tmp_path, widening, text, new=change == "new"))
    assert _unread_gaps(read) == []
    rows = {
        item["check_id"]
        for key in ("blockers", "review_items")
        for item in read["release_decision"][key]
    }
    assert check_id in rows, read["release_decision"]
    assert "SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED" not in rows
    assert "SHIP-CODEX-BOUNDARY-CONFIG-PARSE-FAILED" not in rows
    if widening == "claude_wildcard_allow":
        assert read["decision"] == "blocked"


@pytest.mark.parametrize(
    "path,text",
    [
        (SETTINGS, '{"permissions": {"deny": ["Bash(rm:*)"]}}'),
        (".mcp.json", '{"mcpServers": {}}'),
        (".codex/config.toml", 'sandbox_mode = "read-only"'),
    ],
    ids=["claude_settings", "mcp", "codex_config"],
)
def test_a_new_file_without_a_final_newline_is_judged_like_one_with(
    tmp_path: Path, path: str, text: str
) -> None:
    without, without_exit = _verify(
        _repository(tmp_path / "without", {path: text}, empty_base=True)
    )
    with_newline, with_exit = _verify(
        _repository(tmp_path / "with", {path: text + "\n"}, empty_base=True)
    )
    assert _unread_gaps(without) == []
    assert (without["decision"], without_exit) == (with_newline["decision"], with_exit)


@pytest.mark.parametrize("widening,transform", PROPERTY_CASES)
def test_an_unreadable_variant_never_weakens_the_verdict(
    tmp_path: Path, widening: str, transform: str
) -> None:
    """Whatever the readable change earned, its unreadable variant earns at least
    `insufficient_evidence`, fails strict mode and is never published."""

    path, _base, head, _check_id, _extra = WIDENINGS[widening]
    unread, exit_code = _verify(
        _widening_repo(tmp_path, widening, UNREADABLE[transform](head, path))
    )

    assert _DECISION_RANK[unread["decision"]] >= _DECISION_RANK["insufficient_evidence"]
    assert exit_code == 20
    assert unread["control"]["state"] == "human_review_required"
    assert not any(unread["control"]["permissions"].values())
    assert [gap["source_ref"] for gap in _unread_gaps(unread)] == [path]
    assert "RecursionError" not in json.dumps(unread)


# A settings file the reader parses but cannot interpret.
# A host configuration the reader parses but cannot interpret:
# shape -> (path, base, head).
SHAPES: dict[str, tuple[str, str, str]] = {
    "allow_is_a_string": (
        SETTINGS, BASE_SETTINGS, '{"permissions": {"allow": "Bash(*)", "deny": []}}\n'
    ),
    "permissions_is_a_list": (SETTINGS, BASE_SETTINGS, '{"permissions": ["Bash(*)"]}\n'),
    "top_level_is_a_list": (SETTINGS, BASE_SETTINGS, "[" + WIDENED + "]\n"),
    "mcp_servers_is_a_list": (
        ".mcp.json",
        '{"mcpServers": {}}\n',
        '{"mcpServers": [{"name": "shell", "command": "bash"}]}\n',
    ),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_shape_the_reader_cannot_interpret_is_unread(tmp_path: Path, shape: str) -> None:
    path, base, head = SHAPES[shape]
    repo = _repository(tmp_path, {path: head}, base={path: base})
    verify, exit_code = _verify(repo)
    check = _check(repo)

    assert (verify["decision"], exit_code) == ("insufficient_evidence", 20)
    assert verify["control"]["state"] == "human_review_required"
    assert [gap["source_ref"] for gap in _unread_gaps(verify)] == [path]
    # `check` answers too: a top-level array used to fail it with a traceback.
    assert (check["decision"], check["input_coverage"]) == ("require_review", "partial")
    assert check["control"]["state"] == "human_review_required"


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
        SETTINGS, ".mcp.json", ".cursor/cli.json", ".claude/agents/reviewer.md",
        ".cursor/mcp.json", "retired.json", "hooks/run.sh",
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
        # The diff could not be turned back into text, but the reader read the
        # file: not unread input on its own.
        _row("HOST-CONFIG-PARSE-FAILED", ".mcp.json", kind="host_config_content_unresolved"),
        # Not read, and changed: one gap per path, naming the parser's error.
        _row(
            "BOUNDARY-INPUT-INCOMPLETE", SETTINGS,
            kind="boundary_input_unresolved", code="host_inventory_parse_failed",
        ),
        _row(
            "HOST-CONFIG-PARSE-FAILED", SETTINGS,
            kind="json_parse_failed", parser="JSONDecodeError", line=1,
        ),
        # The base side did not parse; the gap says so.
        _row(
            "HOST-CONFIG-PARSE-FAILED", ".cursor/cli.json",
            kind="old_json_parse_failed", parser="JSONDecodeError", line=1,
        ),
        # A file the reader did not read, by the assessment's own record.
        _row(
            "BOUNDARY-INPUT-INCOMPLETE", "hooks/run.sh",
            kind="boundary_input_unresolved", code="host_inventory_unsupported",
        ),
    ]
    assessment = SimpleNamespace(
        violations=rows,
        unread_input_paths=frozenset({SETTINGS, "hooks/run.sh"}),
        diagnostics=[],
    )
    gaps = unread_changed_input_gaps(assessment, changed)

    assert [(gap.source_ref, gap.policy_id) for gap in gaps] == [
        (".claude/settings.json", "SHIP-HOST-CONFIG-PARSE-FAILED"),
        (".cursor/cli.json", "SHIP-HOST-CONFIG-PARSE-FAILED"),
        ("hooks/run.sh", "SHIP-BOUNDARY-INPUT-INCOMPLETE"),
    ]
    assert "could not read its head version (JSONDecodeError at line 1)" in gaps[0].why
    assert "could not read its base version" in gaps[1].why
    assert "granted before the change" in gaps[1].why
    assert all(is_unread_changed_input_gap(gap) for gap in gaps)
    assert all(gap.kind == "unknown_policy_evidence" for gap in gaps)
    assert all(gap.next_action.authorable_by == "human" for gap in gaps)


def test_a_padded_base_made_readable_is_judged_on_its_rows(tmp_path: Path) -> None:
    """The size bound is the inventory's; the diff carries the base's text whole."""

    unread_base = {SETTINGS: BASE_SETTINGS + " " * PAST_LIMIT}
    repo = _repository(tmp_path, {SETTINGS: WIDENED}, base=unread_base)
    verify, exit_code = _verify(repo)
    assert (verify["decision"], exit_code) == ("blocked", 20)
    assert _unread_gaps(verify) == []


def test_an_unparseable_base_holds_a_readable_head_and_names_the_base(tmp_path: Path) -> None:
    """A base the reader cannot parse leaves what the change did unknown.

    The head reads, but whether its grants are new is not established, so no
    row claims an addition: the change holds at `insufficient_evidence`, and
    the gap names the base, not the head, as unread.
    """

    repo = _repository(tmp_path, {SETTINGS: WIDENED}, base={SETTINGS: "// base\n" + BASE_SETTINGS})
    verify, exit_code = _verify(repo)
    assert (verify["decision"], exit_code) == ("insufficient_evidence", 20)
    assert verify["control"]["state"] == "human_review_required"
    (gap,) = _unread_gaps(verify)
    assert gap["source_ref"] == SETTINGS
    assert "could not read its base version" in gap["why"]


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
    repo = _repository(tmp_path, {SETTINGS: _nest(WIDENED, SETTINGS)})
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


@pytest.mark.parametrize(
    "diff",
    [
        "",
        "diff --git a/README.md b/README.md\nnew file mode 100644\n"
        "--- /dev/null\n+++ b/README.md\n@@ -0,0 +1 @@\n+hello\n",
    ],
    ids=["empty", "readme_only"],
)
@pytest.mark.parametrize("adopted", [True, False], ids=["adopted", "unadopted"])
def test_a_detached_diff_reports_no_pass(tmp_path: Path, diff: str, adopted: bool) -> None:
    """A diff bound to no checkout establishes no change (#1154 scope item 5).

    Its control already stopped for a human (#930), while `decision` said
    `allow` and `input_coverage` said `complete`. Both now say what the
    control says; the control itself is the stop it was.
    """

    if adopted:
        repo = _repository(tmp_path, {SETTINGS: WIDENED})
    else:
        repo = tmp_path / "unadopted"
        repo.mkdir()
    supplied = tmp_path / "change.diff"
    supplied.write_text(diff, encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "check", "--agent", "claude-code", "--workspace", str(repo), "--diff", str(supplied),
            "--format", "agent-boundary-json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    mcp = shipgate_check(agent="claude-code", workspace=str(repo), diff_text=diff)
    reason = (
        "The supplied diff is not bound to a checkout state that verify can reconstruct."
        if adopted
        else "The supplied input does not establish an evaluated checkout state."
    )
    for answer in (payload, mcp):
        assert answer["decision"] == "require_review"
        assert answer["input_coverage"] == "unknown"
        assert answer["issues"] == ["boundary_diff_unbound"]
        assert answer["violations"] == []
        assert answer["control"]["state"] == "human_review_required"
        assert answer["control"]["must_stop"] is True
        assert answer["control"]["reason"].startswith(reason)
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
    assert payload["issues"] == []
    assert payload["input_coverage"] == "complete"
    assert payload["decision"] == "allow"
    assert payload["control"]["must_stop"] is False


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
    """The model's pairwise rules, each refused alone."""

    complete = derive_agent_control(reason="Evaluated.", subject_evaluated=True)
    publishable = derive_agent_control(
        reason="Review.",
        next_action=HumanControlAction(kind="review", why="Review."),
        human_review_required=True,
        publication_allowed=True,
    )
    # A complete, evaluated pass is the one `allow` that validates.
    AgentBoundaryResultV1.model_validate(
        _boundary_payload(
            decision="allow", risk_level="none", control=complete.model_dump(mode="json")
        )
    )
    # decision vs control: no pass beside a control that stops (#1154).
    with pytest.raises(ValidationError, match="allow decision cannot accompany a control that stops"):
        AgentBoundaryResultV1.model_validate(
            _boundary_payload(decision="allow", risk_level="none")
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
