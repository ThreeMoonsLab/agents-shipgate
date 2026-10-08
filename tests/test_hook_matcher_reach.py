"""#940: a Claude Code tool-event hook whose matcher matches no tool name is not a widening.

Claude Code compares a ``PreToolUse`` (and every other tool event's) matcher
with the tool's name; a matcher holding any character other than letters,
digits, ``_``, ``-``, spaces, ``,`` and ``|`` is an unanchored JavaScript
regular expression (https://code.claude.com/docs/en/hooks#matcher-patterns).
``Bash(git push*)`` reads like a permission rule, but as a pattern every match
holds ``"git pus"`` and its space, so no tool name can match it. The
declaration stays a row; it is not counted as a widening, and the row names
the mismatch. Any matcher the reader cannot decide keeps today's reading.
"""

from __future__ import annotations

import json
import os
import re
import string
import subprocess
import time

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import DIRECTION_UNKNOWN, capability_diff_rows
from agents_shipgate.core.hook_matcher_reach import (
    CLAUDE_TOOL_NAME_EVENTS,
    MAX_MATCHER_CHARS,
    NO_TOOL_NAME,
    POSSIBLE,
    TOOL_NAME_CHARACTERS,
    claude_tool_matcher_reach,
)
from agents_shipgate.core.host_grants import (
    _hooks_grants,
    compared_grant,
    diff_host_grants,
    host_grant_direction_unknown,
    host_grant_expansion_signals,
)
from agents_shipgate.core.preflight import signals_for_host_grant_drift

SETTINGS = ".claude/settings.json"
PERMISSION_STYLE = "Bash(git push*)"

#: The built-in tool names https://code.claude.com/docs/en/tools-reference
#: lists (2026-10-07), the names a hook matcher is compared with.
BUILT_IN_TOOL_NAMES = (
    "Agent", "Artifact", "AskUserQuestion", "Bash", "CronCreate", "CronDelete", "CronList",
    "Edit", "EndConversation", "EnterPlanMode", "EnterWorktree", "ExitPlanMode", "ExitWorktree",
    "Glob", "Grep", "ListAgents", "ListMcpResourcesTool", "LSP", "Monitor", "NotebookEdit",
    "PowerShell", "PushNotification", "Read", "ReadMcpResourceTool", "RemoteTrigger",
    "ReportFindings", "ScheduleWakeup", "SendFeedback", "SendMessage", "SendUserFile",
    "ShareOnboardingGuide", "Skill", "SubagentHandback", "TaskCreate", "TaskGet", "TaskList",
    "TaskOutput", "TaskStop", "TaskUpdate", "TodoWrite", "ToolSearch", "WaitForMcpServers",
    "WebFetch", "WebSearch", "Workflow", "Write",
)
#: MCP tool names as https://code.claude.com/docs/en/hooks#match-mcp-tools and
#: https://code.claude.com/docs/en/mcp spell them, a plugin's scoped one included.
MCP_TOOL_NAMES = (
    "mcp__memory__create_entities", "mcp__filesystem__read_file",
    "mcp__github__search_repositories", "mcp__plugin_my-plugin_database-tools__query",
    "mcp__brave-search__web_search", "mcp__Claude_Browser__navigate",
)

NO_TOOL = [
    PERMISSION_STYLE,
    "Bash(git push *)",
    "Bash(npm run test:*)",
    "Bash(rm -rf:*)",
    "WebFetch(domain:example.com)",
    "Bash git push",           # exact characters only: one name, holding spaces
    "mcp__srv__do it",
    "Bash\\s+push",
    "Bash\\(push\\)",
    "Bash( )",
    "[ ]+",
    "[]",
    "\\b \\b",
    "x{0} y",
    "Edit(src/a+/x)",
    PERMISSION_STYLE * 60,     # long, still within the bound
]
STILL_POSSIBLE = [
    None, "", "*", " ", ".*", "Bash", "Edit|Write", "Edit, Write", " Bash ", "Bash|",
    "^Edit$", "Ba.*", "mcp__.*", "mcp__github__.*", "mcp__.*__write.*", "Notebook",
    # A space in one branch only: the other matches a tool name.
    PERMISSION_STYLE + "|Edit",
    # `git` followed by no colon at all: "Bashgit" can be (part of) an MCP tool name.
    "Bash(git:*)",
    # Not decided: a quantifier with nothing to repeat, an unbalanced group, a
    # modifier, a named group, a backreference, escapes whose meaning depends
    # on flags, an Annex B literal brace, a non-ASCII character.
    "Edit(*.ts)", "Read(./secrets/**)", "Bash(git push", "Bash)", "(?i:ba sh)",
    "(?<x>a b)", "(a b)\\1", "\\x20", "\\u0020", "\\ x", "a{", "a{3,2}", "é", "Bash é",
    "Bash\t|\tEdit",
    # Past the bounds.
    "(" * 40 + "a b" + ")" * 40,
    "a b" + "c" * MAX_MATCHER_CHARS,
    # Negated classes are read as possible.
    "[^ ]", "[^a-zA-Z0-9_.-]",
    # Assertions are zero-width, and any tool-name character keeps it possible.
    "(?=x)", "(?! )", "\\b", "^$",
]


@pytest.mark.parametrize("matcher", NO_TOOL)
def test_a_matcher_that_needs_a_character_no_tool_name_holds_matches_no_tool(matcher):
    assert claude_tool_matcher_reach(matcher) == NO_TOOL_NAME


@pytest.mark.parametrize("matcher", STILL_POSSIBLE)
def test_any_other_matcher_keeps_todays_reading(matcher):
    assert claude_tool_matcher_reach(matcher) == POSSIBLE


@pytest.mark.parametrize("value", [1, True, ["Bash"], {"tool": "Bash"}])
def test_a_matcher_that_is_not_a_string_is_not_decided(value):
    assert claude_tool_matcher_reach(value) == POSSIBLE


def test_every_documented_tool_name_is_inside_the_alphabet():
    for name in (*BUILT_IN_TOOL_NAMES, *MCP_TOOL_NAMES):
        assert set(name) <= TOOL_NAME_CHARACTERS, name


#: Python's `re` reads `[]` as an unterminated set; JavaScript as an empty class.
_PATTERNS = [m for m in NO_TOOL if m != "[]" and not set(m) <= set(string.ascii_letters + string.digits + "_- ,|")]


@pytest.mark.parametrize("matcher", _PATTERNS)
def test_a_decided_pattern_matches_no_documented_tool_name(matcher):
    """A cross-check on fixed inputs only: the documented names, never a user's text.

    Each pattern here is ASCII and inside the subset the reader decides, where
    Python's ``re`` and JavaScript agree; ``re.search`` is the unanchored
    ``RegExp.prototype.test``.
    """

    pattern = re.compile(matcher)
    for name in (*BUILT_IN_TOOL_NAMES, *MCP_TOOL_NAMES):
        assert pattern.search(name) is None, (matcher, name)


@pytest.mark.parametrize("matcher", ["Bash", "Edit|Write", ".*", "Ba.*", "mcp__.*", "mcp__github__.*", "^Edit$"])
def test_a_possible_control_does_match_a_documented_tool_name(matcher):
    names = (*BUILT_IN_TOOL_NAMES, *MCP_TOOL_NAMES)
    assert any(re.search(matcher, name) for name in names)


def test_reading_is_linear_and_never_runs_the_pattern():
    # Shapes that backtrack catastrophically when run take no time to read.
    for matcher in ["(a*)*b " * 9000, "(a|a)*" * 10000, "(x+x+)+y " * 7000]:
        matcher = matcher[:MAX_MATCHER_CHARS]
        started = time.perf_counter()
        claude_tool_matcher_reach(matcher)
        assert time.perf_counter() - started < 5


def hook(matcher="__omit__", *, event="PreToolUse", host="claude-code", handlers=1, kind="command"):
    groups = []
    matchers = matcher if isinstance(matcher, list) else [matcher]
    for item in matchers:
        group = {"hooks": [
            {"type": "command", "command": "echo static-only-never-run"} if kind == "command"
            else {"type": "prompt", "prompt": "Review before pushing."}
            for _ in range(handlers)
        ]}
        if item != "__omit__":
            group = {"matcher": item, **group}
        groups.append(group)
    return {"hooks": {event: groups}}


def grants(data, *, host="claude-code", source=SETTINGS, basis="host_configuration"):
    return _hooks_grants(data, host=host, scope="repository", source=source, basis=basis)


def compare(before, after, **kwargs):
    changes = diff_host_grants({"grants": grants(before, **kwargs)}, {"grants": grants(after, **kwargs)})
    signals = host_grant_expansion_signals(changes)
    return changes, signals, capability_diff_rows({"changes": changes, "expansion_signals": signals})


def test_the_issue_fixture_is_a_row_naming_the_mismatch_and_no_widening():
    changes, signals, rows = compare({}, hook(PERMISSION_STYLE))
    assert signals == []
    (row,) = rows
    assert (row.direction, row.expands) == ("added", False)
    assert row.why.startswith("declares a hook no tool call can trigger; matcher " + PERMISSION_STYLE)
    assert "can match no tool name" in row.why and "if field" in row.why
    (handler,) = changes[0]["current"]["handlers"]
    assert handler["matcher_reach"] == NO_TOOL_NAME and handler["matcher"] == PERMISSION_STYLE


def test_the_published_fact_moves_no_grant_identity_or_digest():
    (grant,) = grants(hook(PERMISSION_STYLE))
    bare = {**grant, "handlers": [
        {key: value for key, value in handler.items() if key != "matcher_reach"}
        for handler in grant["handlers"]
    ]}
    assert compared_grant(grant) == compared_grant(bare)


@pytest.mark.parametrize("matcher", ["__omit__", "", "*", ".*", "Bash", "Bash|Edit", "Ba.*", "mcp__.*", "mcp__github__.*", "Bash(git push", "Edit(*.ts)", PERMISSION_STYLE + "|Edit"])
def test_controls_still_widen(matcher):
    _, signals, rows = compare({}, hook(matcher))
    assert signals == [f"hook_added: claude-code:{SETTINGS}"]
    (row,) = rows
    assert row.expands and "no tool call can trigger" not in row.why


@pytest.mark.parametrize("event", ["SessionStart", "Stop", "UserPromptSubmit", "SubagentStart", "Notification"])
def test_events_without_a_tool_name_matcher_are_unaffected(event):
    changes, signals, rows = compare({}, hook(PERMISSION_STYLE, event=event))
    assert signals == [f"hook_added: claude-code:{SETTINGS}"]
    assert "matcher_reach" not in changes[0]["current"]["handlers"][0]
    assert rows[0].expands and "no tool name" not in rows[0].why


@pytest.mark.parametrize("event", sorted(CLAUDE_TOOL_NAME_EVENTS))
def test_every_tool_event_reads_the_matcher(event):
    _, signals, rows = compare({}, hook(PERMISSION_STYLE, event=event))
    assert signals == [] and not rows[0].expands


def test_another_hosts_hooks_are_unaffected():
    changes, signals, _ = compare({}, hook(PERMISSION_STYLE), host="codex", source=".codex/hooks.json")
    assert signals == ["hook_added: codex:.codex/hooks.json"]
    assert "matcher_reach" not in changes[0]["current"]["handlers"][0]


def test_a_matching_handler_beside_it_still_widens_and_the_row_names_the_other():
    _, signals, rows = compare({}, hook([PERMISSION_STYLE, "Bash"]))
    assert signals == [f"hook_added: claude-code:{SETTINGS}"]
    (row,) = rows
    assert row.expands
    assert row.why.startswith("changes what runs around the agent's actions; matcher " + PERMISSION_STYLE)
    assert "so its handlers run for no tool call" in row.why


def test_adding_a_nonmatching_handler_to_an_event_is_not_a_widening():
    changes, signals, rows = compare(hook("Bash"), hook(["Bash", PERMISSION_STYLE]))
    assert signals == []
    (row,) = rows
    assert (row.direction, row.expands) == ("changed", False)
    assert host_grant_direction_unknown(changes[0])
    assert DIRECTION_UNKNOWN in row.why and "can match no tool name" in row.why


def test_a_nonmatching_matcher_edited_to_match_widens():
    """A hook added unable to run must not become one that runs unannounced."""

    _, signals, rows = compare(hook(PERMISSION_STYLE), hook("Bash"))
    assert signals == [f"hook_changed: claude-code:{SETTINGS}"]
    assert rows[0].direction == "widened" and rows[0].expands


def test_a_matching_matcher_edited_to_match_nothing_is_a_change_of_unknown_direction():
    changes, signals, rows = compare(hook("Bash"), hook(PERMISSION_STYLE))
    assert signals == []
    (row,) = rows
    assert (row.direction, row.expands) == ("changed", False)
    assert DIRECTION_UNKNOWN in row.why and "can match no tool name" in row.why


def test_an_unexamined_baseline_handler_falls_back_to_the_handler_count():
    """A grant read before #940 does not say which handlers can run: count them all."""

    (before,) = grants(hook(PERMISSION_STYLE))
    before = {**before, "handlers": [
        {key: value for key, value in item.items() if key != "matcher_reach"} for item in before["handlers"]
    ]}
    (after,) = grants(hook([PERMISSION_STYLE, "Bash"]))
    change = {"grant_id": after["grant_id"], "baseline": before, "current": after}
    assert host_grant_expansion_signals([change]) == [f"hook_changed: claude-code:{SETTINGS}"]


def test_handlers_past_the_bound_are_counted_as_possible():
    from agents_shipgate.core.host_grants import MAX_HOOK_HANDLERS

    _, signals, rows = compare({}, hook(PERMISSION_STYLE, handlers=MAX_HOOK_HANDLERS + 1))
    assert signals == [f"hook_added: claude-code:{SETTINGS}"] and rows[0].expands


def test_a_declared_only_hook_keeps_its_basis_and_names_the_mismatch():
    _, signals, rows = compare({}, hook(PERMISSION_STYLE), source=".claude/hooks/hooks.json", basis="declared_only")
    assert signals == []
    assert "no settings file, plugin manifest or marketplace entry" in rows[0].why
    assert "can match no tool name" in rows[0].why


def test_a_removed_hook_names_no_mismatch():
    _, signals, rows = compare(hook(PERMISSION_STYLE), {})
    assert signals == []
    (row,) = rows
    assert row.direction == "removed" and "no tool name" not in row.why


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def git(*args):
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
            env=env, check=True, capture_output=True, text=True,
        )

    def invoke(*args):
        result = CliRunner().invoke(app, [*args, "--workspace", str(repo)])
        assert result.exit_code in (0, 10, 20), result.output
        return result.stdout

    return repo, git, invoke


#: The real case's shape (free3et/dev-digest#5): a new project settings file
#: whose only hook is a `prompt` handler under a permission-style matcher.
REAL_CASE = {
    "hooks": {"PreToolUse": [{
        "matcher": PERMISSION_STYLE,
        "hooks": [{"type": "prompt", "prompt": "Before executing this push, run the self-review skill."}],
    }]},
    "env": {"ENABLE_TOOL_SEARCH": "auto:5"},
}


@pytest.mark.parametrize("head,expands", [
    (hook(PERMISSION_STYLE), False),
    (REAL_CASE, False),
    (hook("Bash"), True),
    (hook("mcp__.*"), True),
    (hook("Bash(git push"), True),
])
def test_every_route_agrees(tmp_path, head, expands):
    repo, git, invoke = _repo(tmp_path)
    git("init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n.agents-shipgate/\n")
    (repo / "README.md").write_text("fixture\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    invoke("audit", "--host", "--save-baseline", "--json")
    git("checkout", "-qb", "change")
    (repo / SETTINGS).write_text(json.dumps(head))
    git("add", "-A")
    git("commit", "-qm", "head")
    diff = json.loads(invoke("diff", "--base", "main", "--json"))
    text = invoke("diff", "--base", "main")
    check = json.loads(invoke("check", "--agent", "codex", "--base", "main", "--format", "agent-boundary-json"))
    verifier = json.loads(invoke("verify", "--base", "main", "--head", "HEAD", "--json"))
    drift = json.loads(invoke("audit", "--host", "--drift", "--json"))
    preflight = json.loads(invoke("preflight", "--json"))
    comment = (repo / "agents-shipgate-reports/pr-comment.md").read_text()

    hooks = [row for row in diff["rows"] if row["after"] == "PreToolUse"]
    assert len(hooks) == 1 and hooks[0]["direction"] == "added"
    assert hooks[0]["expands"] is expands
    assert diff["review"]["summary"]["widenings"] == int(expands)
    assert diff["rows"] == verifier["host_comparison"]["rows"]
    assert [(r["direction"], r["expands"], r["why"]) for r in check["rows"] if r["after"] == "PreToolUse"] == [
        (hooks[0]["direction"], hooks[0]["expands"], hooks[0]["why"])
    ]
    assert ("⚠" in text) is expands and ("⚠" in comment) is expands
    assert bool(drift["expansion_signals"]) is expands
    assert preflight["host_grant_drift"]["expansion_signals"] == drift["expansion_signals"]
    reasons = signals_for_host_grant_drift(drift)
    assert ("Expansion signals" in " ".join(reason.reason for reason in reasons)) is expands
    mismatch = f"matcher {PERMISSION_STYLE} can match no tool name"
    named = not expands and PERMISSION_STYLE in json.dumps(head)
    for surface in (hooks[0]["why"], text, comment):
        assert (mismatch in surface) is named
    if not expands:
        assert "1 change(s)." in text and "widening" not in text.split("1 change(s)")[1].splitlines()[0]
    # `check` still asks for review of the changed hook declaration: its
    # decision is not loosened by this reading.
    assert check["decision"] != "allow"
