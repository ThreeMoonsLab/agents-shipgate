"""#972 (and #971): a hook row names a change to its exec-form ``args`` or to a documented setting.

Both edits were detected, since ``config_sha256`` moved, but the row read
``no difference in the matcher, command or timeout; the change is in a detail
this output does not show``: the grant published neither the argument vector
(``command: "python3", args: ["guard-readonly.py"]`` → ``["guard-write.py"]``)
nor a boolean setting (``"async": true`` → ``"asyncRewake": true``,
RocketChat/Rocket.Chat.Electron#3542).

Host-grants ``0.9`` (unreleased, extended in place) publishes on each handler
that declares them:

- ``args``: the first argument shaped as a relative script path, when no
  redaction rule rewrites it, alone or after its neighbours, and a digest of the
  rest as ``config_sha256``'s input holds them, exactly as #819 publishes an MCP
  server's package and ``args_sha256``. No other argument text is published.
- ``type``, ``async``, ``asyncRewake``, ``shell`` and ``once``, the documented
  boolean and enumerated handler settings
  (https://code.claude.com/docs/en/hooks#common-fields), by the rule a timeout
  is published.

Pinned here: both issue shapes on every route, with the row value, direction
and count unchanged; every other setting; the credential cases (withheld, and a
rotation #987 redacts stays quiet); an identical declaration written another way
is no row; the script-path rule's refusals; the cells of an added hook; that a
saved baseline holds none of it; and the generated schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.host_grants import (
    MAX_DETAIL_MATCHER_INPUT_CHARS,
    MAX_DETAIL_SCRIPT_CHARS,
    build_host_grants_baseline,
    redacted_config_sha256,
)
from tests.test_hook_mcp_detail_fields import (
    GITHUB_TOKEN,
    HOOK_HEADER,
    SETTINGS,
    _boundary,
    _digest,
    _every_route,
    _grants,
    _inventory,
)
from tests.test_host_diff_review_changes import (
    _diff,
    _git,
    _repository,
    _table_entry,
)

ROOT = Path(__file__).resolve().parents[1]

#: The rows the issues quoted, which named no field.
UNNAMED = "no difference in the matcher, command or timeout"


def _exec_form(script: str, **extra: object) -> dict:
    """#972's reproduction: an exec-form `PreToolUse` command hook."""

    return {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "python3", "args": [script], "timeout": 10, **extra},
    ]}]}}


def _post_tool_use(**settings: object) -> dict:
    """#971's reproduction: only a background setting differs."""

    return {"hooks": {"PostToolUse": [{"matcher": "Edit|Write", "hooks": [
        {"type": "command", "command": "bin/check.sh", "timeout": 30, **settings},
    ]}]}}


def _run(args: list[str]) -> str:
    """A command whose exit code may report drift or a review route."""

    result = CliRunner().invoke(app, args, env={"NO_COLOR": "1", "COLUMNS": "200"})
    assert result.exit_code in (0, 10, 20), result.output
    return result.stdout


def _args(args: list) -> dict:
    return {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "node", "args": args}]}]}}


def _script_digest(args: list, script: str | None) -> str:
    """A hook's `args.sha256`: the arguments, the script marked, beside its position."""

    if script is None:
        return redacted_config_sha256(args)
    index = args.index(script)
    marked = [*args[:index], "<script>", *args[index + 1 :]]
    return redacted_config_sha256({"args": marked, "script_index": index})


# --- the two issue shapes, on every route ----------------------------------


ISSUE_SHAPES = {
    "args": (
        _exec_form("guard-readonly.py"), _exec_form("guard-write.py"), "PreToolUse",
        "PreToolUse: args script guard-readonly.py → guard-write.py",
    ),
    "async": (
        _post_tool_use(**{"async": True}), _post_tool_use(asyncRewake=True), "PostToolUse",
        "PostToolUse: async true → (none); asyncRewake (none) → true",
    ),
}


@pytest.mark.parametrize("name", list(ISSUE_SHAPES))
def test_each_issue_shape_names_the_field_on_every_route(tmp_path: Path, name: str) -> None:
    base, head, event, change = ISSUE_SHAPES[name]
    repo = _repository(tmp_path, {SETTINGS: base}, {SETTINGS: head})

    # `diff` text and JSON, `verify`'s text, the PR comment, `verifier.json`
    # and `check`'s text.
    _every_route(repo, tmp_path / "out", change)
    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER) == [
        HOOK_HEADER, change, "hook edit; authority direction is unknown",
    ]
    assert UNNAMED not in text
    # The row is the one it was: the edit infers no direction and no widening.
    assert [(row["before"], row["after"], row["direction"], row["expands"]) for row in payload["rows"]] == [
        (event, event, "changed", False)
    ]
    assert payload["review"]["summary"]["widenings"] == 0
    # `check`'s rows agree with `diff`'s.
    boundary = _boundary(repo)
    assert [(row["before"], row["after"], row["direction"], row["expands"], row["why"]) for row in boundary["rows"]] == [
        (row["before"], row["after"], row["direction"], row["expands"], row["why"]) for row in payload["rows"]
    ]


@pytest.mark.parametrize("name", list(ISSUE_SHAPES))
def test_each_issue_shape_is_no_expansion_in_drift_or_preflight(tmp_path: Path, name: str) -> None:
    """Drift compares a saved baseline, which holds no handler detail, and claims no expansion."""

    base, head, event, _change = ISSUE_SHAPES[name]
    repo = _repository(tmp_path, {SETTINGS: base}, {SETTINGS: head})
    _git(repo, "checkout", "-q", "main")
    _run(["audit", "--host", "--workspace", str(repo), "--save-baseline", "--json"])
    _git(repo, "checkout", "-q", "change")
    drift = json.loads(_run(["audit", "--host", "--workspace", str(repo), "--drift", "--json"]))
    preflight = json.loads(_run(["preflight", "--workspace", str(repo), "--json"]))
    assert drift["comparison_status"] == "comparable" and drift["has_drift"] is True
    changed = [change for change in drift["changes"] if (change.get("current") or {}).get("kind") == "hook"]
    assert [change["current"]["event"] for change in changed] == [event]
    assert drift["expansion_signals"] == []
    assert preflight["host_grant_drift"]["expansion_signals"] == []
    saved = (repo / ".agents-shipgate").rglob("*.json")
    for path in saved:
        content = path.read_text(encoding="utf-8")
        assert "guard-readonly.py" not in content and "asyncRewake" not in content, path


# --- the published fields ---------------------------------------------------


def test_the_grant_publishes_the_args_and_settings_the_rows_render(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    args = [".claude/hooks/guard.py", "--mode", "write"]
    (root / ".claude").mkdir(parents=True)
    (root / SETTINGS).write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "python3", "args": args, "async": False, "asyncRewake": True,
         "shell": "bash", "once": True, "statusMessage": "Checking", "if": "Bash(git *)"},
        {"type": "prompt", "prompt": "Review $ARGUMENTS"},
    ]}]}}))

    [hook] = _grants(root, "hook")
    assert hook["handlers"] == [
        {
            "matcher": "Bash",
            "command": {"executable": "python3", "sha256": redacted_config_sha256("python3")},
            "timeout": None,
            # #826: a PreToolUse handler with `args` is outside the inline-allow grammar.
            "inline_allow": False,
            "decision_limit": "script_or_command_behavior_not_read",
            "matcher_reach": "possible",
            "args": {"script": ".claude/hooks/guard.py", "sha256": _script_digest(args, ".claude/hooks/guard.py")},
            "type": "command",
            "async": False,
            "asyncRewake": True,
            "shell": "bash",
            "once": True,
        },
        {
            "matcher": "Bash", "command": None, "timeout": None, "inline_allow": False,
            "decision_limit": "script_or_command_behavior_not_read", "matcher_reach": "possible",
            "type": "prompt",
        },
    ]
    # `statusMessage`, `if` and a prompt are not published.
    published = json.dumps(_inventory(root))
    for text in ("Checking", "git *", "Review", "--mode", "write"):
        assert text not in published, text
    # The generated, published schema accepts the inventory.
    schema = json.loads((ROOT / "docs/host-grants-inventory-schema.v0.9.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(_inventory(root))


def test_a_saved_baseline_holds_no_args_or_settings(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / SETTINGS).write_text(json.dumps(_exec_form("guard-readonly.py", asyncRewake=True)))
    baseline = json.dumps(build_host_grants_baseline(_inventory(root)))
    assert "guard-readonly.py" not in baseline and "asyncRewake" not in baseline


@pytest.mark.parametrize(
    "base,head,change",
    [
        # Each documented boolean and enumerated setting, declared or not.
        ({"once": False}, {"once": True}, "Stop: once false → true"),
        ({"shell": "bash"}, {"shell": "powershell"}, 'Stop: shell "bash" → "powershell"'),
        ({}, {"async": False}, "Stop: async (none) → false"),
        # A value that is not a plain token is not shown, and a string is
        # never read as the boolean it spells.
        ({"async": True}, {"async": "true"}, 'Stop: async true → "true"'),
        ({"shell": "bash"}, {"shell": "/bin/zsh -l"}, 'Stop: shell "bash" → <not-shown>'),
    ],
)
def test_each_setting_is_named_with_its_before_and_after(
    tmp_path: Path, base: dict, head: dict, change: str
) -> None:
    def hook(settings: dict) -> dict:
        return {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bin/stop.sh", **settings}]}]}}

    repo = _repository(tmp_path, {SETTINGS: hook(base)}, {SETTINGS: hook(head)})
    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == change
    assert "/bin/zsh" not in text + json.dumps(payload)


def test_a_prompt_hook_moved_to_an_agent_hook_names_its_type(tmp_path: Path) -> None:
    """The prompt is not published, so `type` was the one difference no row could name."""

    def hook(kind: str) -> dict:
        return {"hooks": {"Stop": [{"hooks": [{"type": kind, "prompt": "Check the work."}]}]}}

    repo = _repository(tmp_path, {SETTINGS: hook("prompt")}, {SETTINGS: hook("agent")})
    text, _ = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == 'Stop: type "prompt" → "agent"'
    assert "Check the work" not in text


# --- args: what changes, and what is never shown ---------------------------


@pytest.mark.parametrize(
    "base,head,change",
    [
        # Script and the other arguments apart, as an MCP server's package and digest are.
        (
            ["guard.py", "--level", "canary-low"], ["guard.py", "--level", "canary-high"],
            "Stop: args changed ({0} → {1})",
        ),
        (
            ["guard.py", "--level", "canary-low"], ["audit.py", "--level", "canary-high"],
            "Stop: args script guard.py → audit.py; args changed ({0} → {1})",
        ),
        # Order is part of an argument vector: reordering it is a change.
        (["guard.py", "--canary"], ["--canary", "guard.py"], "Stop: args changed ({0} → {1})"),
        # No argument shaped as a script path: the digest alone.
        (["--level", "canary-low"], ["--level", "canary-high"], "Stop: args changed ({0} → {1})"),
        (
            ["guard.py"], ["--level", "canary-high"],
            "Stop: args script guard.py → (none shown); args changed ({0} → {1})",
        ),
    ],
)
def test_an_args_edit_names_the_script_and_the_digest_of_the_rest(
    tmp_path: Path, base: list, head: list, change: str
) -> None:
    def digest(args: list) -> str:
        script = next((arg for arg in args if arg.endswith(".py")), None)
        return "sha256:" + _script_digest(args, script)[:12]

    repo = _repository(tmp_path, {SETTINGS: _args(base)}, {SETTINGS: _args(head)})
    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == change.format(digest(base), digest(head))
    assert "canary" not in text + json.dumps(payload) + json.dumps(_inventory(repo))
    assert "--level" not in text + json.dumps(payload)


def test_adding_and_removing_args_prints_them_as_a_cell_does(tmp_path: Path) -> None:
    without = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3"}]}]}}
    repo = _repository(tmp_path, {SETTINGS: without}, {SETTINGS: _args(["guard.py"])})
    text, _ = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == (
        f"Stop: command changed (python3 {_digest('python3')} → node {_digest('node')}); "
        f"args (none) → guard.py sha256:{_script_digest(['guard.py'], 'guard.py')[:12]}"
    )


def test_an_identical_declaration_written_another_way_is_no_row(tmp_path: Path) -> None:
    """The handler's keys reordered and the file reformatted: the same args and settings."""

    handler = {"type": "command", "command": "python3", "args": ["guard.py", "--fast"], "async": True}
    reordered = dict(reversed(list(handler.items())))
    base = json.dumps({"hooks": {"Stop": [{"hooks": [handler]}]}})
    head = json.dumps({"hooks": {"Stop": [{"hooks": [reordered]}]}}, indent=4)
    repo = _repository(tmp_path, {SETTINGS: base}, {SETTINGS: head})
    text, payload = _diff(repo)
    assert payload["rows"] == []
    assert "No static host-grant changes detected." in text


#: An argument holding a credential #987 withholds: rotating it is no row.
REDACTED_ROTATIONS = {
    "--token": (["guard.py", "--token", "first-canary"], ["guard.py", "--token", "second-canary"]),
    "--api-key": (["--api-key", "first-canary", "guard.py"], ["--api-key", "second-canary", "guard.py"]),
    "--password=": (["guard.py", "--password=first-canary"], ["guard.py", "--password=second-canary"]),
    "Authorization: Bearer": (
        ["guard.py", "-H", "Authorization: Bearer first-canary"],
        ["guard.py", "-H", "Authorization: Bearer second-canary"],
    ),
    "curl -u": (["guard.py", "-u deploy:first-canary"], ["guard.py", "-u deploy:second-canary"]),
}


@pytest.mark.parametrize("name", list(REDACTED_ROTATIONS))
def test_rotating_a_credential_the_digest_redacts_is_no_row(tmp_path: Path, name: str) -> None:
    base, head = REDACTED_ROTATIONS[name]
    repo = _repository(tmp_path, {SETTINGS: _args(base)}, {SETTINGS: _args(head)})
    [hook] = _grants(repo, "hook")
    assert "canary" not in json.dumps(hook)
    text, payload = _diff(repo)
    assert payload["rows"] == [], name
    assert "canary" not in text


@pytest.mark.parametrize(
    "base,head",
    [
        # A bare positional value, beside a token the published-label rule knows.
        (["guard.py", GITHUB_TOKEN, "s3cr3t-first"], ["guard.py", GITHUB_TOKEN, "s3cr3t-second"]),
        # A password split from its `-u` into the next argument: #987 reads the
        # pair only inside one string.
        (["guard.py", "-u", "deploy:s3cr3t-first"], ["guard.py", "-u", "deploy:s3cr3t-second"]),
    ],
    ids=["positional", "split-user"],
)
def test_a_credential_no_rule_recognises_moves_the_digest_and_is_never_shown(
    tmp_path: Path, base: list, head: list
) -> None:
    """A value no rule redacts is never published: its rotation is a row of digests, as a command's is (#819)."""

    repo = _repository(tmp_path, {SETTINGS: _args(base)}, {SETTINGS: _args(head)})
    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == (
        f"Stop: args changed (sha256:{_script_digest(base, 'guard.py')[:12]} → "
        f"sha256:{_script_digest(head, 'guard.py')[:12]})"
    )
    everything = text + json.dumps(payload) + json.dumps(_inventory(repo)) + json.dumps(_boundary(repo))
    for secret in (GITHUB_TOKEN, "s3cr3t"):
        assert secret not in everything


@pytest.mark.parametrize(
    "args",
    [
        # A neighbour marks it as a credential, alone or with the words before it.
        ["Bearer", "token.py"],
        ["Authorization:", "Bearer", "token.py"],
        ["X-Api-Key:", "token.py"],
        ["--token", "token.py"],
        ["--api-key", "token.py", "guard.py"],
        # A redaction rule rewrites it.
        [GITHUB_TOKEN + ".py"],
        # Not a relative script path.
        ["/Users/someone/guard.py"],
        ["https://example.invalid/guard.py"],
        ["guard.PY"],
        ["-m", "guards.readonly"],
        ["a" * MAX_DETAIL_SCRIPT_CHARS + ".py"],
        # A neighbour too long to read with it.
        ["x" * (MAX_DETAIL_MATCHER_INPUT_CHARS + 1), "guard.py"],
        [{"not": "a string"}, "guard.py"],
    ],
)
def test_an_argument_not_safely_shown_publishes_no_script(tmp_path: Path, args: list) -> None:
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / SETTINGS).write_text(json.dumps(_args(args)))
    [hook] = _grants(root, "hook")
    [handler] = hook["handlers"]
    assert handler["args"] == {"script": None, "sha256": redacted_config_sha256(args)}


@pytest.mark.parametrize(
    "script",
    ["guard.py", "./guard.sh", ".claude/hooks/guard-write.mjs", "${CLAUDE_PROJECT_DIR}/.claude/hooks/check.ts",
     "${CLAUDE_PLUGIN_ROOT}/scripts/format.js"],
)
def test_a_relative_script_path_is_published(tmp_path: Path, script: str) -> None:
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    args = ["-u", "deploy:hunter2", script]
    (root / SETTINGS).write_text(json.dumps(_args(args)))
    [hook] = _grants(root, "hook")
    assert hook["handlers"][0]["args"] == {"script": script, "sha256": _script_digest(args, script)}
    assert "hunter2" not in json.dumps(hook)


def test_args_that_are_not_a_list_are_a_digest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / SETTINGS).write_text(json.dumps(_args("guard.py")))  # type: ignore[arg-type]
    [hook] = _grants(root, "hook")
    assert hook["handlers"][0]["args"] == {"script": None, "sha256": redacted_config_sha256("guard.py")}


# --- cells, and what does not move -----------------------------------------


def test_an_added_hook_lists_its_args_and_settings(tmp_path: Path) -> None:
    """A command handler's `type "command"` is not repeated beside its command; any other type is named."""

    head = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "python3", "args": ["guard.py"], "timeout": 10, "async": True},
        {"type": "prompt", "prompt": "Check it."},
    ]}]}}
    repo = _repository(tmp_path, {SETTINGS: {}}, {SETTINGS: head})
    text, _ = _diff(repo)
    entry = _table_entry(text, "⚠ high added claude-code .claude/settings.json")
    assert entry[1] == (
        f"PreToolUse (handler 1: matcher Bash, command python3 {_digest('python3')}, "
        f"args guard.py sha256:{_script_digest(['guard.py'], 'guard.py')[:12]}, timeout 10, "
        'async true; handler 2: matcher Bash, type "prompt")'
    )


def test_a_plugin_selected_hook_keeps_its_basis_when_its_args_change(tmp_path: Path) -> None:
    plugin = {"name": "demo", "version": "0.1.0"}

    def hook(script: str) -> dict:
        return {"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "node", "args": [script]},
        ]}]}}

    repo = _repository(
        tmp_path,
        {".claude-plugin/plugin.json": plugin, "hooks/hooks.json": hook("start.js")},
        {".claude-plugin/plugin.json": plugin, "hooks/hooks.json": hook("start-v2.js")},
    )
    text, payload = _diff(repo)
    entry = _table_entry(text, "medium changed claude-code hooks/hooks.json")
    assert entry[1] == "SessionStart: args script start.js → start-v2.js"
    assert "installed or enabled is not established" in entry[2]
    assert [row["expands"] for row in payload["rows"]] == [False]


def test_a_codex_hook_names_its_args(tmp_path: Path) -> None:
    def hook(script: str) -> dict:
        return {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3", "args": [script]}]}]}}

    repo = _repository(tmp_path, {".codex/hooks.json": hook("a.py")}, {".codex/hooks.json": hook("b.py")})
    text, _ = _diff(repo)
    assert _table_entry(text, "high changed codex .codex/hooks.json")[1] == "Stop: args script a.py → b.py"



def test_null_args_are_no_args(tmp_path: Path) -> None:
    """`"args": null` declares nothing, as a `null` setting does: it publishes no `args`."""

    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / SETTINGS).write_text(json.dumps(_args(None)))  # type: ignore[arg-type]
    [hook] = _grants(root, "hook")
    assert "args" not in hook["handlers"][0]
