"""#823: how a coding agent is launched inside a workflow job is read as text.

The workflow grant already read triggers, token permissions, reusable calls and
step `uses:` references (#771), and nothing that says how an agent is started.
It now lists each documented agent action's permission inputs, the permission
flags of a `run:` that is one plain `claude -p` / `codex exec` command, and each
`actions/checkout` step's `with.ref`. Nothing is executed, fetched or
evaluated. Only a documented rule a job's launches gain widens; every other
edit is `changed`; and a workflow row that runs an agent names the job facts
beside it.

Shell is not parsed (#823 review cycle 4). A `run:` is read only as one line of
plain words whose program is a known agent CLI, and `claude_args` /
`codex-args` only as a plain list of words; every other form is a named,
non-blocking limit that publishes none of its text, and an unread `run:` step
is never compared, so it gives no row.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    _argument_words,
    _run_command,
    _uncompared_workflow_text,
    _workflow_grant,
    diff_host_grants,
    host_grant_expansion_signals,
    uncompared_agent_launch_texts,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ".github/workflows/agent.yml"
CLAUDE = "anthropics/claude-code-action@v1"
HEAD_SHA = "${{ github.event.pull_request.head.sha }}"


def _workflow(*steps, trigger="pull_request", permissions=None, jobs=None, env=None, defaults=None):
    data = {
        "on": trigger,
        "permissions": permissions if permissions is not None else {"contents": "read", "pull-requests": "read"},
        "jobs": jobs if jobs is not None else {"review": {"runs-on": "ubuntu-latest", "steps": list(steps)}},
    }
    if env is not None:
        data["env"] = env
    if defaults is not None:
        data["defaults"] = defaults
    return data


def _agent(claude_args="--allowedTools Read", **extra):
    return {"uses": CLAUDE, "with": {"claude_args": claude_args, **extra}}


def _reproduction(trigger="pull_request", pr="read", claude_args="--allowedTools Read", run="echo done", ref=None):
    """The workflow of #823's reproduction, as its `wf` shell function writes it, with plain `claude_args`."""

    checkout = {"uses": "actions/checkout@v4", **({"with": {"ref": ref}} if ref else {})}
    return _workflow(
        checkout, _agent(claude_args), {"run": run},
        trigger=trigger, permissions={"contents": "read", "pull-requests": pr},
    )


def _grant(value):
    return _workflow_grant(value, source=SOURCE)


def _changes(before, after):
    return diff_host_grants({"grants": [_grant(before)]}, {"grants": [_grant(after)]})


def _rows(before, after):
    changes = _changes(before, after)
    payload = {"changes": changes, "expansion_signals": host_grant_expansion_signals(changes)}
    return capability_diff_rows(payload)


def _launches(value):
    return _grant(value).get("agent_launches", [])


def _unread(value):
    return _grant(value).get("unread_agent_runs", [])


def _digest(text):
    """What a withheld string publishes: a digest of what the host readers digest for it."""

    from agents_shipgate.core.host_grants import redacted_config_sha256

    return f"<withheld:{redacted_config_sha256(text)[:12]}>"


# --- the four cases of the reproduction --------------------------------------------


def test_args_gaining_bypass_permissions_is_one_widened_row_naming_job_step_and_both_values():
    row, = _rows(
        _reproduction(),
        _reproduction(claude_args="--permission-mode bypassPermissions --allowedTools Bash(git:status)"),
    )

    assert row.subject == f"github {SOURCE}"
    assert (row.direction, row.expands) == ("widened", True)
    assert "review/steps[1]: runs anthropics/claude-code-action with claude_args: --allowedTools Read" in row.before
    assert (
        "review/steps[1]: runs anthropics/claude-code-action with claude_args: "
        "--permission-mode bypassPermissions --allowedTools Bash(git:status)"
    ) in row.after
    assert "an agent launch now skips permission checks (bypassPermissions) (review/steps[1])" in row.why


def test_the_quoted_args_of_the_reproduction_are_a_changed_row_that_publishes_only_digests():
    """#823 review cycle 4 scope: quoted `claude_args` is not read, only compared by a digest."""

    before, after = '--allowedTools "Read"', '--permission-mode bypassPermissions --allowedTools "Bash(*)"'
    assert host_grant_expansion_signals(_changes(_reproduction(claude_args=before), _reproduction(claude_args=after))) == []
    row, = _rows(_reproduction(claude_args=before), _reproduction(claude_args=after))

    assert (row.direction, row.expands) == ("changed", False)
    assert f"claude_args (not read; digest {_digest(before)})" in row.before
    assert f"claude_args (not read; digest {_digest(after)})" in row.after
    assert "an agent launch's declared settings changed (review/steps[1])" in row.why
    assert (
        "an agent launch's argument input is not a plain list of words this audit reads (claude_args at "
        "review/steps[1]); none of its text is published and it is compared by a digest only, so this row "
        "does not say whether it meets a documented widening rule"
    ) in row.why
    for text in ("bypassPermissions", "Bash(*)", '"Read"'):
        assert text not in row.before + row.after
    limit, = uncompared_agent_launch_texts(_grant(_reproduction(claude_args=after)))
    assert limit.startswith(
        "the claude_args value of the agent launch at review/steps[1] (anthropics/claude-code-action) is not "
        "a plain list of words this audit reads"
    )


def test_a_literal_claude_run_step_is_one_changed_row_with_its_permission_flags():
    row, = _rows(
        _reproduction(),
        _reproduction(run="claude -p --permission-mode acceptEdits --allowedTools Edit Summarize"),
    )

    assert (row.direction, row.expands) == ("changed", False)
    assert "review/steps[2]" not in row.before
    # A variadic flag reads every following word up to the next flag, as the
    # CLI reads it, so the trailing prompt word is part of --allowedTools.
    assert (
        "review/steps[2]: runs claude -p with --allowedTools Edit Summarize; --permission-mode acceptEdits"
    ) in row.after
    assert "a step now launches an agent (review/steps[2])" in row.why
    assert "not counted as a widening" in row.why


def test_a_head_ref_checkout_is_one_changed_row_naming_the_default_and_the_new_ref():
    row, = _rows(
        _reproduction(trigger="pull_request_target"),
        _reproduction(trigger="pull_request_target", ref=HEAD_SHA),
    )

    assert (row.direction, row.expands) == ("changed", False)
    assert "review/steps[0]: checkout of the default ref" in row.before
    assert f"review/steps[0]: checkout of ref {HEAD_SHA}" in row.after
    assert "a checkout's declared ref changed (review/steps[0])" in row.why
    assert (
        "an agent runs at review/steps[1] (anthropics/claude-code-action) beside the "
        "untrusted-input trigger pull_request_target and a checkout of pull request code (review/steps[0])"
    ) in row.why


def test_a_checkout_step_on_one_side_only_is_worded_as_added_or_removed():
    """#823 review cycle 3 (P3): a default checkout in an added job is not a changed ref."""

    checkout = {"uses": "actions/checkout@v4"}
    before = _jobs(test=[checkout, {"run": "make test"}])
    after = _jobs(test=[checkout, {"run": "make test"}], lint=[checkout, {"run": "make lint"}])

    row, = _rows(before, after)
    assert "lint/steps[0]: checkout of the default ref" in row.after
    assert "a step now declares a checkout (lint/steps[0]); a ref names which commit's code" in row.why
    assert "declared ref changed" not in row.why

    removed, = _rows(after, before)
    assert "a step no longer declares a checkout (lint/steps[0])" in removed.why
    assert "declared ref changed" not in removed.why


def test_an_untrusted_trigger_with_a_write_scope_names_the_agent_step_it_now_reaches():
    row, = _rows(_reproduction(), _reproduction(trigger="issue_comment", pr="write"))

    assert (row.direction, row.expands) == ("widened", True)
    assert row.why.startswith("grants write permissions to workflow jobs")
    assert (
        "an agent runs at review/steps[1] (anthropics/claude-code-action) beside the "
        "untrusted-input trigger issue_comment and the write scope pull-requests"
    ) in row.why


# --- what is read ------------------------------------------------------------------


def test_only_the_documented_inputs_of_a_known_action_are_listed():
    launch, = _launches(_workflow({
        "uses": "Anthropics/Claude-Code-Action@0123456789abcdef0123456789abcdef01234567",
        "with": {
            "prompt": "Review this",
            "anthropic_api_key": "${{ secrets.ANTHROPIC_API_KEY }}",
            "claude_args": "--max-turns 5",
            "Allowed_Non_Write_Users": "octocat",
            "use_sticky_comment": True,
        },
    }))

    assert launch["agent"] == "anthropics/claude-code-action"
    assert (launch["job"], launch["step"], launch["form"]) == ("review", "steps[0]", "read")
    assert launch["settings"] == [
        {"name": "allowed_non_write_users", "value": "octocat", "unresolved_reason": None},
        {"name": "claude_args", "value": "--max-turns 5", "unresolved_reason": None},
    ]
    assert launch["job_secrets"] == ["ANTHROPIC_API_KEY"]


def test_the_codex_action_inputs_are_listed():
    launch, = _launches(_workflow({
        "uses": "openai/codex-action@v1",
        "with": {"sandbox": "workspace-write", "safety-strategy": "drop-sudo", "prompt": "x", "allow-bots": False},
    }))

    assert launch["agent"] == "openai/codex-action"
    assert launch["settings"] == [
        {"name": "allow-bots", "value": "false", "unresolved_reason": None},
        {"name": "safety-strategy", "value": "drop-sudo", "unresolved_reason": None},
        {"name": "sandbox", "value": "workspace-write", "unresolved_reason": None},
    ]


def test_an_action_step_with_no_inputs_is_still_an_agent_launch():
    launch, = _launches(_workflow({"uses": "anthropics/claude-code-base-action@beta"}))
    assert (launch["agent"], launch["form"], launch["settings"]) == ("anthropics/claude-code-base-action", "read", [])


def test_a_literal_claude_command_publishes_its_permission_flags_under_their_primary_spelling():
    launch, = _launches(_workflow({
        "name": "Review",
        "run": (
            "claude --print --allowed-tools=Read --disallowedTools Bash "
            "--model sonnet --dangerously-skip-permissions --add-dir ../docs secret-prompt-text"
        ),
    }))

    assert (launch["agent"], launch["step"], launch["form"]) == ("claude", "Review", "read")
    assert launch["settings"] == [
        {"name": "--add-dir", "value": "../docs secret-prompt-text", "unresolved_reason": None},
        {"name": "--allowedTools", "value": "Read", "unresolved_reason": None},
        {"name": "--dangerously-skip-permissions", "value": None, "unresolved_reason": None},
        {"name": "--disallowedTools", "value": "Bash", "unresolved_reason": None},
    ]
    assert "sonnet" not in json.dumps(launch)


def test_a_literal_codex_exec_command_publishes_its_permission_flags():
    launch, = _launches(_workflow({"run": "codex e -s danger-full-access --yolo -c model=o3 fix-it"}))

    assert (launch["agent"], launch["form"]) == ("codex", "read")
    assert launch["settings"] == [
        {"name": "--config", "value": "model=o3", "unresolved_reason": None},
        {"name": "--dangerously-bypass-approvals-and-sandbox", "value": None, "unresolved_reason": None},
        {"name": "--sandbox", "value": "danger-full-access", "unresolved_reason": None},
    ]


def test_literal_assignments_before_the_command_are_skipped_and_never_published():
    launch, = _launches(_workflow({"run": "CI=true ANTHROPIC_API_KEY=sk-canary claude -p --permission-mode plan go"}))
    assert launch["form"] == "read"
    assert launch["settings"] == [{"name": "--permission-mode", "value": "plan", "unresolved_reason": None}]
    assert "sk-canary" not in json.dumps(_grant(_workflow({"run": "CI=true ANTHROPIC_API_KEY=sk-canary claude -p go"})))


def test_an_agent_cli_named_by_its_path_is_read_by_its_file_name():
    for run in ("./node_modules/.bin/claude -p --dangerously-skip-permissions go", "/usr/local/bin/codex exec --yolo go"):
        launch, = _launches(_workflow({"run": run}))
        assert launch["form"] == "read" and "widening_rules" in launch
        assert run.split()[0] not in json.dumps(launch)


def test_the_base_action_directory_of_the_claude_action_is_read_as_the_base_action():
    launch, = _launches(_workflow({
        "uses": "anthropics/claude-code-action/base-action@v1",
        "with": {"claude_args": "--dangerously-skip-permissions", "allowed_bots": "*"},
    }))

    assert (launch["agent"], launch["form"]) == ("anthropics/claude-code-action/base-action", "read")
    # `allowed_bots` is not a base-action input, so it is neither listed nor a rule.
    assert launch["settings"] == [
        {"name": "claude_args", "value": "--dangerously-skip-permissions", "unresolved_reason": None},
    ]
    assert launch["widening_rules"] == [{"rule": "bypass_permissions", "setting": "claude_args"}]


def test_inputs_that_are_not_a_mapping_are_unresolved():
    launch, = _launches(_workflow({"uses": CLAUDE, "with": ["claude_args"]}))
    assert (launch["form"], launch["unresolved_reason"], launch["settings"]) == (
        "unresolved", "inputs_not_a_mapping", [],
    )
    limit, = uncompared_agent_launch_texts(_grant(_workflow({"uses": CLAUDE, "with": ["claude_args"]})))
    assert "a step whose `with:` is not a mapping" in limit


def test_every_checkout_step_records_its_declared_ref():
    grant = _grant(_workflow(
        {"uses": "actions/checkout@v4"},
        {"id": "head", "uses": "actions/checkout@v4", "with": {"ref": HEAD_SHA, "fetch-depth": 0}},
        {"uses": "actions/checkout@v4", "with": {"ref": ""}},
        {"uses": "actions/checkout@v4", "with": {"ref": ["main"]}},
        {"uses": "actions/setup-node@v4", "with": {"ref": "main"}},
    ))

    assert grant["checkout_refs"] == [
        {"job": "review", "step": "steps[0]", "ref": None, "unresolved_reason": None},
        {"job": "review", "step": "head", "ref": HEAD_SHA, "unresolved_reason": None},
        {"job": "review", "step": "steps[2]", "ref": None, "unresolved_reason": None},
        {"job": "review", "step": "steps[3]", "ref": None, "unresolved_reason": "not_a_string"},
    ]


@pytest.mark.parametrize(
    ("ref", "pull_request_code"),
    [
        (HEAD_SHA, True),
        ("${{github.event.pull_request.head.ref}}", True),
        ("${{ github.event.pull_request.merge_commit_sha }}", True),
        ("${{ github.head_ref }}", True),
        ("${{ github.event.workflow_run.head_sha }}", True),
        ("refs/pull/${{ github.event.issue.number }}/head", True),
        ("refs/pull/123/merge", True),
        (None, False),
        ("main", False),
        ("${{ github.sha }}", False),
        ("${{ github.event.pull_request.base.sha }}", False),
        ("${{ steps.pr.outputs.sha }}", False),
    ],
)
def test_pull_request_code_is_the_documented_head_refs_only(ref, pull_request_code):
    from agents_shipgate.core.host_grants import pull_request_code_ref

    assert pull_request_code_ref(ref) is pull_request_code


def test_a_workflow_without_agents_or_checkouts_keeps_its_v0_6_shape():
    grant = _grant(_workflow({"run": "make test"}, {"uses": "actions/setup-python@v5"}))
    assert "agent_launches" not in grant and "checkout_refs" not in grant and "unread_agent_runs" not in grant


def test_job_secrets_name_what_the_agent_job_and_the_workflow_env_reference():
    workflows = _workflow(
        jobs={
            "review": {"env": {"GH": "${{ secrets.REVIEW_TOKEN }}"}, "steps": [
                {"run": "echo ${{ secrets.DEPLOY_KEY }}"},
                _agent(anthropic_api_key="${{ secrets.ANTHROPIC_API_KEY }}"),
            ]},
            "other": {"steps": [{"run": "echo ${{ secrets.OTHER_JOB_ONLY }}"}]},
        },
        env={"SHARED": "${{ secrets.WORKFLOW_ENV }}"},
    )
    launch, = _launches(workflows)
    assert launch["job_secrets"] == ["ANTHROPIC_API_KEY", "DEPLOY_KEY", "REVIEW_TOKEN", "WORKFLOW_ENV"]


def _job_env_workflow(env_lines: list[str]) -> str:
    return "\n".join([
        "on: pull_request",
        "permissions: {contents: read}",
        "jobs:",
        "  review:",
        "    runs-on: ubuntu-latest",
        "    env:",
        *(f"      {line}" for line in env_lines),
        "    steps:",
        f"      - run: claude -p {BYPASS} Review",
    ]) + "\n"


def test_job_secrets_read_a_yaml_alias_that_holds_itself_or_fans_out_once():
    """#823 review cycle 5 (P3): a self-referential alias raised RecursionError, a fan-out took minutes."""

    holds_itself = yaml.safe_load(_job_env_workflow(["&env", "A: ${{ secrets.TOKEN }}", "B: *env"]))
    launch, = _launches(holds_itself)
    assert launch["job_secrets"] == ["TOKEN"]

    # Ten references at each of eight levels: 10**8 leaves if each is walked.
    levels = ["l0: &l0 '${{ secrets.DEEP }}'"] + [
        f"l{level}: &l{level} [{', '.join([f'*l{level - 1}'] * 10)}]" for level in range(1, 9)
    ]
    started = time.monotonic()
    launch, = _launches(yaml.safe_load(_job_env_workflow(levels)))
    assert launch["job_secrets"] == ["DEEP"]
    assert time.monotonic() - started < 5


def test_job_secrets_read_unterminated_expressions_in_linear_time():
    """#823 review cycle 7 (C7-F2): every unterminated `${{` scanned to the end, 92 s at 300 KB.

    An unterminated expression names no secret, as before; a closed one
    after it still does.
    """

    unterminated = "${{ secrets.NEVER " * 20_000  # about 360 KB, no closing `}}`
    job = {"env": {"X": unterminated, "Y": "${{ secrets.CLOSED }}"}, "steps": [{"run": "claude -p Review"}]}
    started = time.monotonic()
    launch, = _launches(_workflow(jobs={"review": job}))
    assert launch["job_secrets"] == ["CLOSED"]
    # The same text in an agent action's settings input, which took as long.
    row, = _rows(_workflow(_agent()), _workflow(_agent(settings="${{" * 100_000)))
    assert (row.direction, row.expands) == ("changed", False)
    assert time.monotonic() - started < 5
    # The body of an expression that closes after an unterminated one is still read.
    launch, = _launches(_workflow(jobs={"review": {
        "env": {"X": "${{ vars.A ${{ secrets.INNER }}"}, "steps": [{"run": "claude -p Review"}],
    }}))
    assert launch["job_secrets"] == ["INNER"]


# --- the only forms read: plain lists of words (#823 review cycle 4) --------------------
#
# Four review cycles each found a shell form the `run:` reader mis-read, so no
# shell is parsed. A `run:` is read only as one line of plain words — letters,
# digits and `_ . / : = , % + -` — whose program is a known agent CLI;
# `claude_args` and `codex-args` only as such words (parentheses too) across
# blanks and newlines, with no `--settings` or `--mcp-config` flag.


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("--max-turns 5\n--allowedTools Read", ["--max-turns", "5", "--allowedTools", "Read"]),
        ("  --allowedTools\tBash(git:status),Read  ", ["--allowedTools", "Bash(git:status),Read"]),
        ("--permission-mode=bypassPermissions --add-dir ../docs", ["--permission-mode=bypassPermissions", "--add-dir", "../docs"]),
        ("-c model=o3 -csandbox_mode=read-only --json", ["-c", "model=o3", "-csandbox_mode=read-only", "--json"]),
    ],
    ids=["lines", "blanks-and-parentheses", "attached-values", "codex-config"],
)
def test_a_plain_argument_input_is_its_words(value, words):
    assert _argument_words(value) == words


@pytest.mark.parametrize(
    "value",
    [
        '--allowedTools "Read"',
        "--allowedTools 'Read'",
        "--model ${{ vars.CLAUDE_MODEL }}",
        "--append-system-prompt $PROMPT",
        "--append-system-prompt `cat prompt.md`",
        "--append-system-prompt $(cat prompt.md)",
        "# reviewer: alice\n--max-turns 5",
        "--max-turns 5 # --dangerously-skip-permissions",
        "--max-turns 5 notes#x",
        "a\\ b",
        "--allowedTools Read;Edit",
        "--allowedTools Read|Edit",
        "--allowedTools Read&Edit",
        "--x <in",
        "--allowedTools Bash(git:*)",
        "--allowedTools Bash(git?)",
        '["--yolo"]',
        "--mcp-config {}",
        "--settings ./settings.json",
        "--settings=./settings.json",
        "--mcp-config .mcp.json",
        "--mcp-config=.mcp.json",
        "--add-dir ~/docs",
        "--append-system-prompt résumé",
        "--allowedTools Read",
        "--x !y",
        "--x @file",
        "--x {a,b}",
    ],
    ids=["double-quote", "single-quote", "expression", "variable", "backtick", "substitution", "comment-line",
         "inline-comment", "hash-in-a-word", "backslash", "semicolon", "pipe", "ampersand", "redirection",
         "glob-star", "glob-question", "json-array", "json-object", "settings", "settings-attached", "mcp-config",
         "mcp-config-attached", "tilde", "non-ascii", "no-break-space", "bang", "at", "brace"],
)
def test_any_other_argument_input_is_not_read(value):
    assert _argument_words(value) is None


@pytest.mark.parametrize(
    ("run", "command"),
    [
        ("claude -p --dangerously-skip-permissions Review", ("claude", ["-p", "--dangerously-skip-permissions", "Review"])),
        ("claude --print go\n", ("claude", ["--print", "go"])),
        ("  codex exec --yolo review  ", ("codex", ["--yolo", "review"])),
        ("codex e -s workspace-write go", ("codex", ["-s", "workspace-write", "go"])),
        ("CI=1 A_B=x:y claude -p go", ("claude", ["-p", "go"])),
        ("./node_modules/.bin/claude -p go", ("claude", ["-p", "go"])),
    ],
    ids=["claude", "print", "codex", "codex-alias", "assignments", "path"],
)
def test_a_plain_one_command_run_is_read(run, command):
    assert _run_command(run) == command


#: `run:` steps that mention an agent CLI and are not read as an agent launch.
#: The first eight are the forms of #823 review cycle 4.
UNREAD_RUNS = [
    "# Don't run this on forks\nnpm ci && claude -p --dangerously-skip-permissions \"Review\"",
    "# Don't run this on forks\nclaude -p --dangerously-skip-permissions \"Review this PR\" > out.md\n"
    "# We'll post the result below\ngh pr comment \"$PR\" --body-file out.md",
    "# it's gated\nif [ -n \"$X\" ]; then claude -p --dangerously-skip-permissions go; fi",
    "# we don't pipe secrets\ngit diff | claude -p 'Review'",
    "npm ci && claude -p --dangerously-skip-permissions go # it's fine",
    "# can't\nset -e; codex exec --yolo 'review'",
    "# To reproduce locally: npm ci; claude -p \"review this change\"\nnpm test",
    "cat <<MD\n'$(claude -p x)'",
    "npm ci && claude -p --dangerously-skip-permissions go",
    "npm ci\nclaude -p go",
    "claude -p go | tee review.md",
    "claude -p go > review.md",
    "claude -p go 2>&1",
    "claude -p \\\n  --dangerously-skip-permissions go",
    "claude -p $CLAUDE_FLAGS go",
    "claude -p \"$(cat prompt.md)\"",
    "claude -p 'go'",
    'claude -p "Fix ${{ github.event.issue.title }}"',
    'gh pr comment "$PR" --body "$(claude -p --dangerously-skip-permissions \'go\')"',
    "REVIEW=`claude -p --dangerously-skip-permissions go`",
    "cat <<'EOF'\nReproduce locally with `claude -p \"review this change\"`.\nEOF",
    "cat <<EOF | claude -p\nreview\nEOF",
    "if true; then claude -p --dangerously-skip-permissions go; fi",
    "{ claude -p --dangerously-skip-permissions go; }",
    "! claude -p --dangerously-skip-permissions go",
    "time claude -p --dangerously-skip-permissions go",
    "exec claude -p go",
    "npx @anthropic-ai/claude-code -p --dangerously-skip-permissions go",
    "timeout 600 claude -p --dangerously-skip-permissions go",
    "sudo claude -p go",
    "bash -c claude",
    "codex -c sandbox_mode=danger-full-access exec review",
    "codex --yolo exec review",
    "claude mcp add github -- npx server",
    "claude --version",
    "codex login --api-key sk-test-canary",
    "echo claude -p done",
    'echo "claude -p --dangerously-skip-permissions"',
    "npm install -g @anthropic-ai/claude-code",
    "./scripts/claude-review.sh --dangerously-skip-permissions",
    "claude -p *.md",
    "claude -p --add-dir ~/docs go",
    "claude -p --allowedTools {Read,Edit} go",
    "claude -p --settings ./ci/settings.json go",
    "claude -p --mcp-config=.mcp.json go",
    "claude -p go",
    "claude -p go\r",
    "CI=true then claude -p go",
]


@pytest.mark.parametrize("run", UNREAD_RUNS)
def test_a_run_this_reader_does_not_parse_is_a_named_limit_that_gives_no_row_and_publishes_nothing(run):
    """#823 review cycle 4 scope: never a launch, never a row, never a claim, and none of its text."""

    step = {"run": run}
    grant = _grant(_workflow(step))
    agents = sorted({name for name in ("claude", "codex") if name in run})

    assert grant.get("agent_launches", []) == []
    assert grant["unread_agent_runs"] == [{"job": "review", "step": "steps[0]", "agent": name} for name in agents]
    published = json.dumps(grant)
    for text in ("dangerously", "yolo", "sk-test-canary", "Review", "review this change", "danger-full-access"):
        assert text not in published
    limits = uncompared_agent_launch_texts(grant)
    assert limits == [
        f"the `run:` at review/steps[0] mentions {name} and is not read as an agent launch: only a "
        "single-line command of plain words run by bash or sh, whose program is `claude -p` or `codex exec`, "
        "is read, so this step may start an agent that is neither published nor compared, and adding, "
        "removing or editing it gives no row"
        for name in agents
    ]
    assert _uncompared_workflow_text(grant) is None
    # Adding, editing or removing it gives no row.
    assert _rows(_workflow({"run": "echo done"}), _workflow(step)) == []
    assert _rows(_workflow(step), _workflow({"run": run + "\necho edited"})) == []
    assert _rows(_workflow(step), _workflow({"run": "echo done"})) == []


@pytest.mark.parametrize(
    "shell",
    [
        {"step": "pwsh"},
        {"step": "python"},
        {"step": "cmd"},
        {"step": "${{ matrix.shell }}"},
        {"job": "powershell"},
        {"workflow": "pwsh"},
        # #823 review cycle 7: a bash or sh template that may run a command of its own
        {"step": "bash -c 'claude -p --dangerously-skip-permissions Review' {0}"},
        {"step": "bash -ec true {0}"},
        {"job": "bash --rcfile .ci/rc {0}"},
        {"step": "bash --init-file .ci/rc {0}"},
        {"step": "sh -e {0} extra"},
        {"step": "bash -e"},
        {"step": "bash -o {0}"},
        {"workflow": "bash -O ${{ vars.OPTION }} {0}"},
        # one that runs no command, or reads its commands from elsewhere
        {"step": "bash -n {0}"},
        {"step": "bash -o noexec {0}"},
        {"step": "sh -s {0}"},
        {"step": "bash --version {0}"},
    ],
    ids=["step-pwsh", "step-python", "step-cmd", "step-expression", "job-default", "workflow-default",
         "bash-c-template", "short-cluster-with-c", "rcfile", "init-file", "words-after-the-script",
         "no-script-slot", "option-without-its-name", "option-name-from-an-expression",
         "noexec-flag", "noexec-option", "stdin", "version"],
)
def test_a_run_under_a_declared_shell_other_than_bash_or_sh_is_not_read(shell):
    step = {"run": "claude -p --dangerously-skip-permissions go"}
    if "step" in shell:
        step["shell"] = shell["step"]
    job = {"runs-on": "ubuntu-latest", "steps": [step]}
    if "job" in shell:
        job["defaults"] = {"run": {"shell": shell["job"]}}
    workflow = _workflow(
        jobs={"review": job}, defaults={"run": {"shell": shell["workflow"]}} if "workflow" in shell else None,
    )

    assert _launches(workflow) == []
    assert _unread(workflow) == [{"job": "review", "step": "steps[0]", "agent": "claude"}]


@pytest.mark.parametrize(
    "shell",
    ["bash", "sh", "bash -e {0}", "/bin/bash --noprofile --norc -eo pipefail {0}", "sh -eu {0}",
     "bash -l {0}", "bash -O extglob -o pipefail {0}"],
)
def test_a_run_under_bash_or_sh_is_read(shell):
    step = {"run": "claude -p --dangerously-skip-permissions go", "shell": shell}
    launch, = _launches(_workflow(step))
    assert launch["widening_rules"] == [
        {"rule": "bypass_permissions", "setting": "--dangerously-skip-permissions"}
    ]
    # A step's own `shell:` is read before its job's default.
    job = {"defaults": {"run": {"shell": "pwsh"}}, "steps": [step]}
    assert len(_launches(_workflow(jobs={"review": job}))) == 1


def test_a_large_run_is_read_in_linear_time():
    runs = [
        'REVIEW="' + "$(" * 20000 + "claude -p go" + ")" * 20000 + '"',
        "cat <<X\n" * 20000 + "claude -p go",
        "claude -p " + "word " * 50000,
    ]
    start = time.perf_counter()
    for run in runs[:2]:
        assert _unread(_workflow({"run": run})) == [{"job": "review", "step": "steps[0]", "agent": "claude"}]
    launch, = _launches(_workflow({"run": runs[2]}))
    assert launch["settings"] == []
    assert time.perf_counter() - start < 5


def test_beside_an_unread_step_a_new_bypass_launch_is_a_widening():
    """#823 review cycle 4 (b): a step this audit does not read takes no gain from another launch."""

    for unread in (
        "# To reproduce locally: npm ci; claude -p \"review this change\"\nnpm test",
        "cat > comment.md <<'EOF'\nReproduce locally with `claude -p \"review this change\"`.\nEOF\n",
        "npm install -g @anthropic-ai/claude-code",
    ):
        step = {"run": unread}
        before = _workflow(step, permissions={"contents": "read", "pull-requests": "write"})
        after = _workflow(
            step, {"run": "claude -p --dangerously-skip-permissions Review"},
            permissions={"contents": "read", "pull-requests": "write"},
        )
        assert host_grant_expansion_signals(_changes(before, after)) == [f"workflow_agent_widened_changed: {SOURCE}"]
        row, = _rows(before, after)
        assert (row.direction, row.expands) == ("widened", True)
        assert "an agent launch now skips permission checks (bypassPermissions) (review/steps[1])" in row.why
        assert "not counted as a widening" not in row.why.split("; an agent runs at")[0]
        assert "review/steps[0]" not in row.why + row.before + row.after


def test_an_unread_step_rewritten_as_a_read_launch_does_not_claim_the_rule_it_may_already_have_met():
    before = _workflow({"run": 'npm ci && claude -p --dangerously-skip-permissions "Review"'})
    after = _workflow({"run": "npm ci"}, {"run": "claude -p --dangerously-skip-permissions Review"})

    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)
    assert (
        "an agent launch now skips permission checks (bypassPermissions) (review/steps[1]), which is not "
        "counted as a widening: a step in this job that may launch the agent in a form this audit does not "
        "read is gone (review/steps[0]), and this launch may be that step rewritten in a form this audit "
        "reads, which may already have done the same"
    ) in row.why


def test_an_unread_step_that_goes_while_a_read_launch_gains_a_rule_still_widens():
    """The read launch was read on both sides, so the gain is its own."""

    before = _workflow({"run": "npm ci && claude -p go"}, _agent())
    after = _workflow(_agent("--dangerously-skip-permissions"))

    assert host_grant_expansion_signals(_changes(before, after)) == [f"workflow_agent_widened_changed: {SOURCE}"]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("claude -p --allowedTools Read Review",
         "npx @anthropic-ai/claude-code -p --dangerously-skip-permissions Review"),
        ("claude -p --allowedTools Read Review", 'claude -p --dangerously-skip-permissions "Review"'),
        ("codex exec -s workspace-write review", "codex -c sandbox_mode=danger-full-access exec review"),
    ],
    ids=["npx", "quoted", "codex-root-options"],
)
def test_a_read_launch_that_becomes_a_form_this_audit_does_not_read_is_not_called_gone(before, after):
    """#823 review: what was established is that no launch this audit reads is declared."""

    row, = _rows(_workflow({"run": before}), _workflow({"run": after}))
    assert (row.direction, row.expands) == ("changed", False)
    assert "a step no longer declares an agent launch this audit reads (review/steps[0])" in row.why
    assert (
        "a step that no longer declares one may still start an agent in a way this audit does not read, "
        "such as an action outside its table, a script, or a `run:` this audit does not read as a launch "
        "(more than one command, quoting, an expansion, `npx`, `codex` options before `exec`), so this row "
        "does not say that it no longer starts one"
    ) in row.why
    assert "no longer launches an agent" not in row.why
    assert "dangerously" not in row.after and "danger-full-access" not in row.after


# --- an agent action's argument input (#823 review cycles 1 and 4) ------------------------
#
# `claude_args` and `codex-args` are not shell text: each action splits its own
# input. A plain list of words is split alike by all of them, so only that is
# read; a widening rule is read from those words.


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("--max-turns 5\n--allowedTools Read", "--max-turns 5\n--dangerously-skip-permissions"),
        ("--allowedTools Bash(git:status)", "--allowedTools Bash(git:status) --dangerously-skip-permissions"),
        ("--max-turns 5", "--max-turns 5\n--permission-mode\nbypassPermissions"),
        ("--max-turns 5", "--permission-mode=bypassPermissions"),
        # A word starting with `--` is always a flag to the action, never a value.
        ("--allowedTools Read", "--model --dangerously-skip-permissions"),
    ],
    ids=["several-lines", "parentheses", "mode-over-lines", "mode-attached", "never-a-value"],
)
def test_plain_claude_args_gaining_a_bypass_is_a_widening(before, after):
    changes = _changes(_workflow(_agent(before)), _workflow(_agent(after)))
    assert host_grant_expansion_signals(changes) == [f"workflow_agent_widened_changed: {SOURCE}"]
    row, = _rows(_workflow(_agent(before)), _workflow(_agent(after)))

    assert (row.direction, row.expands) == ("widened", True)
    assert "an agent launch now skips permission checks (bypassPermissions) (review/steps[0])" in row.why
    assert "not counted as a widening" not in row.why


@pytest.mark.parametrize(
    "after",
    [
        "# --dangerously-skip-permissions\n--max-turns 5",
        "--max-turns 5 # --dangerously-skip-permissions",
        '--dangerously-skip-permissions --append-system-prompt "Review"',
        "--dangerously-skip-permissions --model ${{ vars.CLAUDE_MODEL }}",
        "--dangerously-skip-permissions --settings ./ci/settings.json",
        "--dangerously-skip-permissions --mcp-config '{\"mcpServers\":{}}'",
        "--dangerously-skip-permissions --allowedTools Bash(*)",
    ],
    ids=["comment-line", "inline-comment", "quoted-prompt", "expression", "settings", "mcp-config", "glob"],
)
def test_argument_input_this_audit_does_not_read_meets_no_rule_and_is_a_changed_row(after):
    before = _workflow(_agent("--max-turns 5"))
    changed = _workflow(_agent(after))
    launch, = _launches(changed)

    assert launch["settings"] == [{"name": "claude_args", "value": _digest(after), "unresolved_reason": "unread_arguments"}]
    assert "widening_rules" not in launch
    assert host_grant_expansion_signals(_changes(before, changed)) == []
    row, = _rows(before, changed)
    assert (row.direction, row.expands) == ("changed", False)
    assert "dangerously" not in row.after and "an agent launch now" not in row.why
    # Editing it is still a change, compared by its digest.
    edited, = _rows(changed, _workflow(_agent(after + "\n--max-turns 9")))
    assert (edited.direction, edited.expands) == ("changed", False)
    assert f"claude_args (not read; digest {_digest(after)})" in edited.before


@pytest.mark.parametrize(
    ("codex_args", "rule"),
    [
        ("--json\n--dangerously-bypass-approvals-and-sandbox", "bypasses approvals and the sandbox"),
        ("--full-auto --yolo", "bypasses approvals and the sandbox"),
        ("--json\n--sandbox=danger-full-access", "runs without a sandbox (danger-full-access)"),
        ("-s danger-full-access", "runs without a sandbox (danger-full-access)"),
        ("-sdanger-full-access", "runs without a sandbox (danger-full-access)"),
    ],
    ids=["several-lines", "yolo", "attached-sandbox", "short-sandbox", "attached-short-sandbox"],
)
def test_plain_codex_args_gaining_a_rule_is_a_widening(codex_args, rule):
    before = _workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": "--json"}})
    after = _workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": codex_args}})

    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("widened", True)
    assert rule in row.why


@pytest.mark.parametrize(
    "codex_args",
    [
        '["--json", "--yolo"]',
        "-s 'danger-full-access'",
        "--yolo ${{ vars.EXTRA }}",
        # The action appends its own --sandbox, or its own default_permissions
        # override for a permission-profile, after codex-args, and either
        # takes precedence over a sandbox --config override written before it.
        "-c sandbox_mode=danger-full-access",
        "--config=default_permissions=:danger-full-access",
    ],
    ids=["json-array", "quoted-sandbox", "expression", "sandbox-mode-override", "profile-override"],
)
def test_codex_args_this_audit_does_not_read_or_that_selects_nothing_is_changed(codex_args):
    before = _workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": "--json"}})
    after = _workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": codex_args}})

    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)


def test_the_multi_line_widening_reaches_diff_and_the_review_summary(tmp_path):
    repo = _repo(tmp_path, {SOURCE: _yaml(_workflow(_agent("--max-turns 5\n--allowedTools Read")))})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(_workflow(_agent("--max-turns 5\n--dangerously-skip-permissions")))})
    _git(repo, "commit", "-qam", "bypass on its own line")

    payload = _diff(repo)
    row, = payload["rows"]
    assert (row["direction"], row["expands"]) == ("widened", True)
    assert payload["review"]["summary"]["widenings"] == 1


# --- what is compared --------------------------------------------------------------


@pytest.mark.parametrize(
    "after",
    [
        # the prompt and an undocumented flag are not compared
        _workflow({"run": "claude -p a-different-prompt --model opus --allowedTools Read"}),
        # renamed, respelled and reordered flags
        _workflow({"name": "Renamed", "run": "claude --allowed-tools=Read --print review"}),
    ],
    ids=["prompt-and-model", "rename-and-reorder"],
)
def test_an_edit_outside_the_compared_flags_is_quiet(after):
    before = _workflow({"run": "claude -p review --allowedTools Read"})
    assert _rows(before, after) == []


def test_a_word_after_a_variadic_flag_is_read_as_its_value_as_the_cli_reads_it():
    before = _workflow({"run": "claude -p review --allowedTools Read"})
    after = _workflow({"run": "claude -p --allowedTools Read review"})

    row, = _rows(before, after)
    assert "--allowedTools Read review" in row.after and "--allowedTools Read," in row.before + ","


def test_plain_claude_args_are_compared_by_their_words():
    """Reformatting the list is quiet; a new word is a change."""

    assert _rows(_workflow(_agent("--max-turns 5 --allowedTools Read")), _workflow(_agent("--max-turns 5\n  --allowedTools   Read"))) == []
    row, = _rows(_workflow(_agent("--allowedTools Read")), _workflow(_agent("--allowedTools Read,Edit")))
    assert (row.direction, row.expands) == ("changed", False)
    assert "claude_args: --allowedTools Read,Edit" in row.after


def test_an_action_ref_bump_is_a_step_reference_change_only():
    row, = _rows(_workflow(_agent()), _workflow({**_agent(), "uses": "anthropics/claude-code-action@v2"}))

    assert "action reference changed (review/steps[0])" in row.why
    assert "agent launch" not in row.why.split("; an agent runs at")[0]
    assert "claude_args" not in row.before + row.after


def test_renaming_or_moving_an_agent_step_within_its_job_is_quiet():
    before = _workflow({"run": "make"}, _agent())
    after = _workflow({**_agent(), "name": "Claude review"}, {"run": "make"})
    assert _rows(before, after) == []


# --- direction ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "rule"),
    [
        (_workflow(_agent()), _workflow(_agent("--dangerously-skip-permissions")), "skips permission checks"),
        (_workflow({"run": "claude -p x"}), _workflow({"run": "claude -p --permission-mode=bypassPermissions x"}),
         "skips permission checks"),
        (_workflow(_agent(allowed_non_write_users="octocat")), _workflow(_agent(allowed_non_write_users="octocat, *")),
         "accepts runs triggered by any user (allowed_non_write_users: *)"),
        # The bot gate admits any bot, not any user (#823 review cycle 2).
        (_workflow(_agent()), _workflow(_agent(allowed_bots="dependabot,*")),
         "accepts runs triggered by any bot (allowed_bots: *)"),
        (_workflow({"uses": "openai/codex-action@v1", "with": {"sandbox": "read-only"}}),
         _workflow({"uses": "openai/codex-action@v1", "with": {"sandbox": "danger-full-access"}}),
         "runs without a sandbox (danger-full-access)"),
        (_workflow({"uses": "openai/codex-action@v1"}),
         _workflow({"uses": "openai/codex-action@v1", "with": {"safety-strategy": "unsafe"}}),
         "runs without privilege restrictions"),
        (_workflow({"uses": "openai/codex-action@v1"}),
         _workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": "--yolo"}}),
         "bypasses approvals and the sandbox"),
        (_workflow({"uses": "openai/codex-action@v1", "with": {"allow-users": "a"}}),
         _workflow({"uses": "openai/codex-action@v1", "with": {"allow-users": "*"}}),
         "accepts runs triggered by any user (allow-users: *)"),
        (_workflow({"run": "codex exec x"}), _workflow({"run": "codex exec --sandbox danger-full-access x"}),
         "runs without a sandbox"),
        # A gate entry an expression cannot remove still opens it (#823 review F4).
        (_workflow(_agent()), _workflow(_agent(allowed_non_write_users="${{ vars.USERS }}, *")),
         "accepts runs triggered by any user (allowed_non_write_users: *)"),
        # #823 review C2-F2: the documented bypasses written through inputs the reader lists.
        (_workflow(_agent(settings=json.dumps({"permissions": {"defaultMode": "default"}}))),
         _workflow(_agent(settings=json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}))),
         "skips permission checks (bypassPermissions)"),
        (_workflow(_agent()), _workflow(_agent(settings=json.dumps({"defaultMode": "bypassPermissions"}))),
         "skips permission checks (bypassPermissions)"),
        (_workflow({"uses": "openai/codex-action@v1", "with": {"permission-profile": ":workspace"}}),
         _workflow({"uses": "openai/codex-action@v1", "with": {"permission-profile": ":danger-full-access"}}),
         "runs without a sandbox (danger-full-access)"),
        # the last of a repeated `--permission-mode` counts (#823 review cycle 5)
        (_workflow(_agent("--permission-mode bypassPermissions --permission-mode default")),
         _workflow(_agent("--permission-mode default --permission-mode bypassPermissions")),
         "skips permission checks (bypassPermissions)"),
        (_workflow({"run": "claude -p --permission-mode bypassPermissions --permission-mode default x"}),
         _workflow({"run": "claude -p --permission-mode default --permission-mode=bypassPermissions x"}),
         "skips permission checks (bypassPermissions)"),
    ],
    ids=["skip-flag", "mode-flag", "gate", "bots", "codex-sandbox", "codex-unsafe", "codex-args", "codex-users",
         "codex-cli", "gate-entry-beside-an-expression", "settings-default-mode",
         "settings-top-level-default-mode", "codex-permission-profile", "last-mode-args", "last-mode-cli"],
)
def test_a_documented_rule_gained_is_a_widening(before, after, rule):
    changes = _changes(before, after)
    assert host_grant_expansion_signals(changes) == [f"workflow_agent_widened_changed: {SOURCE}"]
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("widened", True)
    assert rule in row.why


@pytest.mark.parametrize(
    ("before", "after"),
    [
        # one rule, two spellings
        (_workflow(_agent("--dangerously-skip-permissions")), _workflow(_agent("--permission-mode bypassPermissions"))),
        # the same rule moved from the CLI to the action
        (_workflow({"run": "claude -p --dangerously-skip-permissions x"}),
         _workflow(_agent("--dangerously-skip-permissions"))),
        # narrowed
        (_workflow(_agent("--dangerously-skip-permissions")), _workflow(_agent("--allowedTools Read"))),
        (_workflow(_agent(allowed_non_write_users="*")), _workflow(_agent(allowed_non_write_users="octocat"))),
        # text an expression can reach is never read for a rule
        (_workflow(_agent()), _workflow(_agent("${{ inputs.extra }} --dangerously-skip-permissions"))),
        (_workflow(_agent()), _workflow(_agent("--dangerously-skip-permissions ${{ inputs.extra }}"))),
        (_workflow(_agent()), _workflow(_agent(allowed_non_write_users="*${{ vars.USERS }}"))),
        (_workflow({"uses": "openai/codex-action@v1"}),
         _workflow({"uses": "openai/codex-action@v1", "with": {"sandbox": "${{ vars.SANDBOX }}"}})),
        # widened by a tool rule, which is #824's to rate
        (_workflow(_agent("--allowedTools Read")), _workflow(_agent("--allowedTools Bash"))),
        (_workflow({"run": "claude -p --permission-mode default x"}),
         _workflow({"run": "claude -p --permission-mode acceptEdits x"})),
        # one rule, written as a flag and as the settings it passes
        (_workflow(_agent("--dangerously-skip-permissions")),
         _workflow(_agent(settings=json.dumps({"permissions": {"defaultMode": "bypassPermissions"}})))),
        # settings holding an expression, or naming a file, meet no rule
        (_workflow(_agent()),
         _workflow(_agent(settings='{"permissions":{"defaultMode":"${{ vars.MODE }}"}}'))),
        (_workflow(_agent()), _workflow(_agent(settings=".github/claude-settings.json"))),
        (_workflow(_agent(settings=json.dumps({"permissions": {"defaultMode": "default"}}))),
         _workflow(_agent(settings=json.dumps({"permissions": {"defaultMode": "acceptEdits"}})))),
        (_workflow({"uses": "openai/codex-action@v1", "with": {"permission-profile": ":read-only"}}),
         _workflow({"uses": "openai/codex-action@v1", "with": {"permission-profile": ":workspace"}})),
        # a `--permission-mode` a later one replaces meets no rule (#823 review cycle 5)
        (_workflow(_agent("--permission-mode default")),
         _workflow(_agent("--permission-mode bypassPermissions --permission-mode default"))),
        (_workflow({"run": "claude -p --permission-mode default x"}),
         _workflow({"run": "claude -p --permission-mode=bypassPermissions --permission-mode default x"})),
    ],
    ids=["respelled", "moved-to-action", "narrowed", "gate-closed", "after-an-expression", "before-an-expression",
         "gate-entry-holding-an-expression", "mode-expression", "tool-rule", "accept-edits", "flag-to-settings",
         "settings-expression", "settings-path", "settings-accept-edits", "codex-workspace-profile",
         "replaced-mode-args", "replaced-mode-cli"],
)
def test_any_other_edit_is_changed(before, after):
    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)


# --- codex exec's full-access sandbox, however the CLI reads it (#823 review cycle 3) ---

CODEX_WORKSPACE = _workflow({"run": "codex exec -s workspace-write review"})


@pytest.mark.parametrize(
    "run",
    [
        "codex exec -s danger-full-access review",
        "codex exec --sandbox=danger-full-access review",
        # clap reads a short option's attached value, with or without `=`.
        "codex exec -sdanger-full-access review",
        "codex exec -s=danger-full-access review",
        # `sandbox_mode` is the setting --sandbox sets, in each way -c is written.
        "codex exec -c sandbox_mode=danger-full-access review",
        "codex exec --config=sandbox_mode=danger-full-access review",
        "codex exec -csandbox_mode=danger-full-access review",
        "codex exec -c=sandbox_mode=danger-full-access review",
        # The built-in full-access profile, which is what the action's
        # `permission-profile: :danger-full-access` passes the CLI.
        "codex exec -c default_permissions=:danger-full-access review",
        "codex exec --config default_permissions=:danger-full-access review",
        # The last override of a key counts; a profile override outranks a sandbox one.
        "codex exec -c sandbox_mode=read-only -c sandbox_mode=danger-full-access review",
        "codex exec -c sandbox_mode=read-only -c default_permissions=:danger-full-access review",
    ],
    ids=["short", "long-attached", "short-attached", "short-equals", "config", "config-attached",
         "c-attached", "c-equals", "profile", "profile-long", "last-override", "profile-over-sandbox-mode"],
)
def test_codex_exec_full_access_widens_in_every_spelling_the_cli_reads(run):
    after = _workflow({"run": run})

    assert host_grant_expansion_signals(_changes(CODEX_WORKSPACE, after)) == [
        f"workflow_agent_widened_changed: {SOURCE}"
    ]
    row, = _rows(CODEX_WORKSPACE, after)
    assert (row.direction, row.expands) == ("widened", True)
    assert "an agent launch now runs without a sandbox (danger-full-access) (review/steps[0])" in row.why
    assert "no permission flags" not in row.after


@pytest.mark.parametrize(
    "run",
    [
        # --sandbox takes precedence over a --config override.
        "codex exec -s workspace-write -c sandbox_mode=danger-full-access review",
        "codex exec -sworkspace-write -c default_permissions=:danger-full-access review",
        # The last override counts, and a profile override outranks sandbox_mode.
        "codex exec -c sandbox_mode=danger-full-access -c sandbox_mode=read-only review",
        "codex exec -c sandbox_mode=danger-full-access -c default_permissions=:workspace review",
        # A key under another table, or another value, is not the setting.
        "codex exec -c profiles.ci.sandbox_mode=danger-full-access review",
        "codex exec -c default_permissions=danger-full-access review",
        "codex exec -sread-only review",
    ],
    ids=["sandbox-flag-wins", "attached-sandbox-flag-wins", "last-override", "profile-over-sandbox-mode",
         "profile-scoped-key", "custom-profile-name", "read-only"],
)
def test_a_codex_exec_sandbox_the_cli_does_not_select_is_changed(run):
    after = _workflow({"run": run})

    assert host_grant_expansion_signals(_changes(CODEX_WORKSPACE, after)) == []
    row, = _rows(CODEX_WORKSPACE, after)
    assert (row.direction, row.expands) == ("changed", False)


@pytest.mark.parametrize(
    "run",
    [
        "codex exec -c sandbox_mode=" + "1" * 5000 + " Review",
        "codex exec -c default_permissions=" + "1" * 4400 + " Review",
        "codex e --config=sandbox_mode=" + "1" * 4301 + " Review",
    ],
    ids=["sandbox-mode", "default-permissions", "attached-config"],
)
def test_a_codex_config_integer_past_the_digit_limit_is_read_as_text_and_selects_nothing(run):
    """#823 review cycle 6 (C6-F2): `tomllib` raises a plain `ValueError` for such an integer."""

    before, after = _workflow({"run": "echo hi"}), _workflow({"run": run})

    launch, = _launches(after)
    assert not launch.get("widening_rules")
    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)


def test_attached_short_values_publish_under_the_primary_spelling():
    launch, = _launches(_workflow({"run": "codex exec -sdanger-full-access -c=model=o3 -pci review"}))

    assert launch["settings"] == [
        {"name": "--config", "value": "model=o3", "unresolved_reason": None},
        {"name": "--profile", "value": "ci", "unresolved_reason": None},
        {"name": "--sandbox", "value": "danger-full-access", "unresolved_reason": None},
    ]
    assert launch["widening_rules"] == [{"rule": "danger_full_access", "setting": "--sandbox"}]
    # One setting, two spellings: respelling it is quiet.
    assert _rows(
        _workflow({"run": "codex exec -s danger-full-access review"}),
        _workflow({"run": "codex exec -sdanger-full-access review"}),
    ) == []
    # One rule, two spellings: moving between the flag and the override is not a widening.
    before = _workflow({"run": "codex exec -s danger-full-access review"})
    after = _workflow({"run": "codex exec -c sandbox_mode=danger-full-access review"})
    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)


def _jobs(**steps):
    return _workflow(jobs={job: {"runs-on": "ubuntu-latest", "steps": list(items)} for job, items in steps.items()})


def _named(args, name="agent"):
    return {"name": name, **_agent(args)}


BYPASS = "--dangerously-skip-permissions"


def test_renaming_a_job_that_launches_a_bypassing_agent_is_not_a_widening():
    """#823 review F3: a rule the launch already met in the job it left is moved, not gained."""

    before, after = _jobs(review=[_named(BYPASS)]), _jobs(**{"code-review": [_named(BYPASS)]})

    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)
    assert (
        "an agent launch that skips permission checks (bypassPermissions) moved between jobs "
        "(review/agent → code-review/agent), which is not counted as a widening"
    ) in row.why
    assert "an agent launch now" not in row.why


@pytest.mark.parametrize(
    ("before", "after"),
    [
        # the agent step moved to another job, which launched no agent before
        (_jobs(lint=[_named(BYPASS)], review=[{"run": "make"}]),
         _jobs(lint=[{"run": "make"}], review=[_named(BYPASS)])),
        # renamed and edited in the same change
        (_jobs(review=[_named(BYPASS)]), _jobs(**{"code-review": [_named(f"{BYPASS} --max-turns 5")]})),
        # the same launch now runs in the other job, and the other job's in this one
        (_jobs(lint=[_named(BYPASS)], review=[_named("--allowedTools Read")]),
         _jobs(lint=[_named("--allowedTools Read")], review=[_named(BYPASS)])),
        # a gate the launch opened, moved with it
        (_jobs(review=[{"name": "agent", **_agent(allowed_bots="*")}]),
         _jobs(triage=[{"name": "agent", **_agent(allowed_bots="*")}])),
        # the job it left keeps a step that does not mention the agent
        (_jobs(lint=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}, {"run": "make"}],
               review=[{"run": "make"}]),
         _jobs(lint=[{"run": "make"}],
               review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}])),
        # #823 review cycle 7: a job renamed with the install step it had
        # beside the launch; the unread step is in the job gaining the rule
        (_jobs(review=[{"run": "npm i -g @anthropic-ai/claude-code"}, {"name": "agent", "run": f"claude -p {BYPASS} Review"}]),
         _jobs(**{"code-review": [{"run": "npm i -g @anthropic-ai/claude-code"},
                                  {"name": "agent", "run": f"claude -p {BYPASS} Review"}]})),
        # a job renamed beside another job that keeps the unread step it had
        (_jobs(review=[_named(BYPASS)], setup=[{"run": "claude mcp add x"}]),
         _jobs(**{"code-review": [_named(BYPASS)]}, setup=[{"run": "claude mcp add x"}])),
    ],
    ids=["step-moved", "renamed-and-edited", "swapped", "gate-moved", "moved-beside-a-step-that-is-no-launch",
         "renamed-with-its-install-step", "renamed-beside-an-unread-step-another-job-keeps"],
)
def test_a_launch_that_left_one_job_for_another_moves_its_rules(before, after):
    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)
    assert "moved between jobs" in row.why


@pytest.mark.parametrize(
    ("before", "after"),
    [
        # a second job now bypasses, beside the one that still does
        (_jobs(lint=[_named(BYPASS)], review=[_named("--allowedTools Read")]),
         _jobs(lint=[_named(BYPASS)], review=[_named(BYPASS)])),
        # the job that met the rule still launches the agent, and a different launch meets it elsewhere
        (_jobs(lint=[_named(BYPASS)], review=[_named("--allowedTools Read")]),
         _jobs(lint=[_named("--allowedTools Read")], review=[_named(f"{BYPASS} --max-turns 5")])),
        # the job that met it remains, running its launch in a form this audit
        # does not read (#823 review cycle 3), so that launch has not left it
        (_jobs(lint=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}], review=[_named("--allowedTools Read")]),
         _jobs(lint=[{"name": "agent", "run": f"npx @anthropic-ai/claude-code -p {BYPASS} Review"}],
               review=[_named(BYPASS)])),
        # the same, with the other job's launch edited in place into the one this job had
        (_jobs(lint=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}],
               review=[{"name": "agent", "run": "claude -p --allowedTools Read Review"}]),
         _jobs(lint=[{"name": "agent", "run": f"npx @anthropic-ai/claude-code -p {BYPASS} Review"}],
               review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}])),
        # a step moved and edited while the job it left remains
        (_jobs(lint=[_named(BYPASS)], review=[{"run": "make"}]),
         _jobs(lint=[{"run": "make"}], review=[_named(f"{BYPASS} --max-turns 5")])),
        # #823 review cycle 5 (M5): the job it met it in still runs it, now
        # quoted and so unread, while the other job adds the same plain launch
        (_jobs(lint=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}], review=[{"run": "echo hi"}]),
         _jobs(lint=[{"name": "agent", "run": f'claude -p {BYPASS} "Review"'}],
               review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}])),
        # (M6) the same, the job it met it in now running it through `npx`
        (_jobs(lint=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}], review=[{"run": "echo hi"}]),
         _jobs(lint=[{"name": "agent", "run": f"npx @anthropic-ai/claude-code -p {BYPASS} Review"}],
               review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}])),
        # the same, the unread step now standing at another step label
        (_jobs(lint=[{"run": f"claude -p {BYPASS} Review"}], review=[{"run": "echo hi"}]),
         _jobs(lint=[{"run": "echo hi"}, {"run": f'claude -p {BYPASS} "Review"'}],
               review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}])),
        # #823 review cycle 6 (V1): the install step merged into the launch,
        # so the job it met it in has as many unread steps as before, at a
        # step the launch did not hold
        (_jobs(lint=[{"run": "npm i -g @anthropic-ai/claude-code"}, {"run": f"claude -p {BYPASS} Review"}],
               review=[{"run": "echo hi"}]),
         _jobs(lint=[{"run": f"npm i -g @anthropic-ai/claude-code && claude -p {BYPASS} Review"}],
               review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}])),
        # (V2) an unread step removed while the launch becomes unread at another step
        (_jobs(lint=[{"run": f"claude -p {BYPASS} Review"}, {"run": 'echo "claude"'}], review=[{"run": "echo hi"}]),
         _jobs(lint=[{"run": "echo hi"}, {"run": f'claude -p {BYPASS} "Review"'}],
               review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}])),
        # the job it left keeps an unread step it already had, at another
        # step: that step carries no text to tell it is not the launch (#823
        # review cycle 6 flips the cycle 5 guard, in the safe direction)
        (_jobs(lint=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}, {"name": "mcp", "run": "claude mcp add x"}],
               review=[{"run": "make"}]),
         _jobs(lint=[{"name": "mcp", "run": "claude mcp add x"}],
               review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}])),
        # the job it met it in still runs the action, with an argument input
        # it no longer reads, or a settings input holding an expression
        (_jobs(lint=[_named(BYPASS)], review=[{"run": "make"}]),
         _jobs(lint=[_named(f'{BYPASS} --append-system-prompt "Review"')], review=[_named(BYPASS)])),
        (_jobs(lint=[{"name": "agent", **_agent(settings='{"permissions":{"defaultMode":"bypassPermissions"}}')}],
               review=[{"run": "make"}]),
         _jobs(lint=[{"name": "agent", **_agent(settings='{"permissions":{"defaultMode":"${{ vars.MODE }}"}}')}],
               review=[{"name": "agent", **_agent(settings='{"permissions":{"defaultMode":"bypassPermissions"}}')}])),
        # #823 review cycle 7 (R6): the job it met it in is renamed and quotes
        # the prompt, so under its new name it still runs the launch, unread
        (_jobs(lint=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}], review=[{"run": "echo hi"}]),
         _jobs(**{"lint-renamed": [{"name": "agent", "run": f'claude -p {BYPASS} "Review"'}]},
               review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}])),
        # (R7) renamed and run through `npx`, the other job spelling the bypass as a mode
        (_jobs(lint=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}], review=[{"run": "echo hi"}]),
         _jobs(**{"lint-renamed": [{"name": "agent", "run": f"npx @anthropic-ai/claude-code -p {BYPASS} Review"}]},
               review=[{"name": "agent", "run": "claude -p --permission-mode bypassPermissions Review"}])),
        # (R3) the job it met it in is removed, and an existing job gains the quoted launch
        (_jobs(lint=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}], review=[{"run": "echo hi"}],
               docs=[{"run": "make"}]),
         _jobs(review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}],
               docs=[{"run": "make"}, {"name": "agent", "run": f'claude -p {BYPASS} "Review"'}])),
        # the same while the job it left remains
        (_jobs(lint=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}, {"run": "make"}],
               review=[{"run": "echo hi"}], docs=[{"run": "make"}]),
         _jobs(lint=[{"run": "make"}], review=[{"name": "agent", "run": f"claude -p {BYPASS} Review"}],
               docs=[{"run": "make"}, {"name": "agent", "run": f'claude -p {BYPASS} "Review"'}])),
        # renamed into an action whose argument input this audit does not read
        (_jobs(lint=[_named(BYPASS)], review=[{"run": "echo hi"}]),
         _jobs(**{"lint-renamed": [_named(f'{BYPASS} --append-system-prompt "Review"')]},
               review=[_named(BYPASS)])),
        # Two jobs renamed at once, one holding an unread step: which new job
        # is which is not told, so the gain is claimed (the safe direction).
        (_jobs(lint=[_named(BYPASS)], setup=[{"run": "npm i -g @anthropic-ai/claude-code"}]),
         _jobs(review=[_named(BYPASS)], prepare=[{"run": "npm i -g @anthropic-ai/claude-code"}])),
    ],
    ids=["second-job", "narrowed-there-widened-here", "unread-in-the-job-it-met-it", "edited-into-the-same-launch",
         "moved-and-edited", "quoted-in-the-job-it-met-it", "npx-in-the-job-it-met-it",
         "unread-at-another-step-in-the-job-it-met-it", "install-merged-into-the-launch",
         "unread-step-removed-while-the-launch-becomes-unread", "beside-an-unread-step-that-stays",
         "arguments-unread-in-the-job-it-met-it",
         "setting-expression-in-the-job-it-met-it",
         "renamed-and-quoted", "renamed-and-npx", "removed-while-another-job-gains-it-quoted",
         "left-for-another-job-quoted", "renamed-into-unread-arguments", "two-jobs-renamed"],
)
def test_a_rule_another_job_gains_while_no_launch_left_is_a_widening(before, after):
    assert host_grant_expansion_signals(_changes(before, after)) == [f"workflow_agent_widened_changed: {SOURCE}"]
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("widened", True)
    assert "an agent launch now skips permission checks (bypassPermissions) (review/agent)" in row.why
    assert "the launch already met that rule in the job it left" not in row.why


@pytest.mark.parametrize("renamed_first", [True, False], ids=["renamed-declared-first", "renamed-declared-last"])
def test_a_renamed_job_takes_the_move_whatever_order_the_jobs_are_declared_in(renamed_first):
    """#823 review cycle 2 (P3): the rule moved to the job running the same launch, not the first one declared."""

    renamed = ("code-review", [_named(BYPASS)])
    added = ("triage", [_named(f"{BYPASS} --max-turns 5")])
    before = _jobs(review=[_named(BYPASS)])
    after = _jobs(**dict([renamed, added] if renamed_first else [added, renamed]))

    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("widened", True)
    assert "moved between jobs (review/agent → code-review/agent)" in row.why
    assert "an agent launch now skips permission checks (bypassPermissions) (triage/agent)" in row.why


def test_a_setting_holding_an_expression_is_marked_and_the_row_says_what_it_leaves_unread():
    """#823 review F4: the row no longer reads as though no documented rule was gained."""

    before = _workflow(_agent(allowed_non_write_users="${{ vars.USERS }}"))
    after = _workflow(_agent(allowed_non_write_users="${{ vars.OTHER_USERS }}"))

    launch, = _launches(after)
    gate = next(item for item in launch["settings"] if item["name"] == "allowed_non_write_users")
    assert gate["holds_expression"] is True
    assert "widening_rules" not in launch
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)
    assert (
        "an agent launch setting holds a `${{ }}` expression (allowed_non_write_users at review/steps[0]), "
        "which GitHub substitutes before the action reads it; documented widening rules are read only from "
        "the literal text the expression cannot reach, so this row does not say whether the text it reaches "
        "meets one"
    ) in row.why
    # A setting without one says nothing of the kind, and the key is omitted;
    # an argument input holding one is not read at all.
    plain, = _launches(_workflow(_agent()))
    assert "holds_expression" not in plain["settings"][0]
    args, = _launches(_workflow(_agent("--model ${{ vars.M }}")))
    assert args["settings"] == [
        {"name": "claude_args", "value": _digest("--model ${{ vars.M }}"), "unresolved_reason": "unread_arguments"},
    ]


@pytest.mark.parametrize(
    ("before", "after", "rule", "held"),
    [
        (_workflow(_agent(allowed_non_write_users="${{ vars.EXTRA_USERS }}")),
         _workflow(_agent(allowed_non_write_users="${{ vars.EXTRA_USERS }}, *")),
         "accepts runs triggered by any user (allowed_non_write_users: *)",
         "allowed_non_write_users held a `${{ }}` expression, whose substituted text this audit does not read"),
        # the settings input is read for the rule only when it holds no expression (#823 review C2-F2)
        (_workflow(_agent(settings='{"permissions":{"defaultMode":"${{ vars.MODE }}"}}')),
         _workflow(_agent(settings='{"permissions":{"defaultMode":"bypassPermissions"}}')),
         "skips permission checks (bypassPermissions)",
         "settings held a `${{ }}` expression, whose substituted text this audit does not read"),
        # an argument input this audit did not read may have met it (#823 review cycle 4)
        (_workflow(_agent("--allowedTools Read --model ${{ vars.CLAUDE_MODEL }}")),
         _workflow(_agent("--dangerously-skip-permissions --model opus")),
         "skips permission checks (bypassPermissions)",
         "claude_args was not a plain list of words this audit reads, so no rule was read from it"),
        (_workflow(_agent('--dangerously-skip-permissions --append-system-prompt "Review"')),
         _workflow(_agent("--dangerously-skip-permissions")),
         "skips permission checks (bypassPermissions)",
         "claude_args was not a plain list of words this audit reads, so no rule was read from it"),
    ],
    ids=["gate", "settings", "claude-args-expression", "claude-args-quoted"],
)
def test_a_rule_gained_where_the_setting_was_not_read_before_is_named_and_not_claimed(before, after, rule, held):
    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)
    assert (
        f"an agent launch now {rule} (review/steps[0]), which is not counted as a widening: before, this "
        f"job's {held}, and it may already have done the same"
    ) in row.why


def test_a_new_workflow_that_bypasses_permissions_is_an_added_widening():
    after = _grant(_workflow(_agent("--dangerously-skip-permissions")))
    changes = diff_host_grants({"grants": []}, {"grants": [after]})

    assert host_grant_expansion_signals(changes) == [f"workflow_agent_widened_added: {SOURCE}"]
    row, = capability_diff_rows({"changes": changes, "expansion_signals": host_grant_expansion_signals(changes)})
    assert (row.direction, row.expands) == ("added", True)
    assert "an agent launch now skips permission checks (bypassPermissions) (review/steps[0])" in row.why


def test_access_and_risk_still_describe_the_token_and_triggers_alone():
    plain, bypass = (_grant(_workflow(_agent(args))) for args in ("", "--dangerously-skip-permissions"))
    assert (plain["access"], plain["risk"]) == (bypass["access"], bypass["risk"])


# --- negative controls -------------------------------------------------------------


def test_a_non_agent_actions_inputs_are_not_read():
    before = _workflow({"uses": "someone/ai-review@v1", "with": {"claude_args": "--allowedTools Read"}})
    after = _workflow({"uses": "someone/ai-review@v1", "with": {"claude_args": "--dangerously-skip-permissions"}})

    assert _launches(after) == []
    assert _rows(before, after) == []


def test_a_run_that_does_not_mention_an_agent_cli_is_nothing():
    for run in ("npm test", "echo claudette", "CLAUDE_MODEL=opus make", "echo my_codex_notes"):
        grant = _grant(_workflow({"run": run}))
        assert "agent_launches" not in grant and "unread_agent_runs" not in grant
        assert uncompared_agent_launch_texts(grant) == []


def test_a_pull_request_workflow_with_a_default_checkout_claims_no_pull_request_code():
    row, = _rows(_reproduction(), _reproduction(pr="write"))

    assert "checkout" not in row.why
    assert "untrusted-input" not in row.why
    assert row.why.endswith(
        "an agent runs at review/steps[1] (anthropics/claude-code-action) beside the write scope pull-requests"
    )


def test_a_composite_action_is_neither_a_launch_nor_a_limit():
    step = {"uses": "./.github/actions/claude-review", "with": {"claude_args": "--dangerously-skip-permissions"}}
    grant = _grant(_workflow(step))
    assert "agent_launches" not in grant and "unread_agent_runs" not in grant
    assert _rows(_workflow({"run": "echo"}), _workflow(step)) == []


def test_a_removed_workflow_gets_no_note():
    before = _grant(_reproduction(trigger="issue_comment", pr="write"))
    changes = diff_host_grants({"grants": [before]}, {"grants": []})
    row, = capability_diff_rows({"changes": changes, "expansion_signals": []})
    assert row.direction == "removed"
    assert "an agent runs" not in row.why


# --- redaction (#802) --------------------------------------------------------------


SETTINGS_JSON = json.dumps({
    "env": {"DB_PASSWORD": "hunter2-canary", "INTERNAL_KEY": "canary-9f8e7d"},
    "apiKeyHelper": "echo canary-helper-value",
    "permissions": {"allow": ["Bash(npm test)"]},
})
MCP_JSON = json.dumps({"mcpServers": {"db": {
    "command": "db-mcp", "env": {"DB_API_TOKEN": "canary-tok-123"}, "headers": {"X-API-Key": "canary-hdr-456"},
}}})
#: What the host readers publish for them: key names, every such value withheld.
SETTINGS_PUBLISHED = (
    '{"apiKeyHelper":"<redacted>","env":{"DB_PASSWORD":"<redacted>","INTERNAL_KEY":"<redacted>"},'
    '"permissions":{"allow":["Bash(npm test)"]}}'
)
MCP_PUBLISHED = (
    '{"mcpServers":{"db":{"command":"db-mcp","env":{"DB_API_TOKEN":"<redacted>"},'
    '"headers":{"X-API-Key":"<redacted>"}}}}'
)
JSON_CANARIES = ("hunter2-canary", "canary-9f8e7d", "canary-helper-value", "canary-tok-123", "canary-hdr-456")

#: #823 review C2-F1: text the host readers never publish, in no secret-named
#: key: an `mcp-remote` bearer header among a server's `args`, and a hook's command.
REMOTE_MCP_JSON = json.dumps({"mcpServers": {"remote": {"command": "npx", "args": [
    "mcp-remote", "https://mcp.example.com/sse", "--header", "Authorization: Bearer tokCANARY0123456789abcdef",
]}}}, separators=(",", ":"))
HOOK_JSON = json.dumps({"hooks": {"Stop": [{"hooks": [{
    "type": "command", "command": 'curl -H "X-Auth-Token: hookCANARY77" https://hooks.example.com/notify',
}]}]}}, separators=(",", ":"))
REMOTE_CODEX_CONFIG = (
    'mcp_servers.remote={command="npx", args=["mcp-remote", "https://mcp.example.com/sse", '
    '"--header", "Authorization: Bearer tokCANARY-codex-0123456789"]}'
)
SHAPE_CANARIES = (
    "tokCANARY0123456789abcdef", "tokCANARY-codex-0123456789", "hookCANARY77", "hooks.example.com",
    "mcp.example.com", "mcp-remote", "X-Auth-Token",
)


def test_a_json_value_publishes_its_shape_and_none_of_its_free_text():
    """#823 review C2-F1: every string a host reader does not publish is withheld, and still compared.

    The same server in `.mcp.json` publishes `remote (command name npx)`, and
    the same hook in `.claude/settings.json` publishes `Stop`; neither
    publishes an argument or a command. The same JSON passed through
    `claude_args` or a `run:` is not read at all (#823 review cycle 4).
    """

    args = f"--allowedTools Read --mcp-config '{REMOTE_MCP_JSON}'"
    grant = _grant(_workflow(
        _agent(args, settings=HOOK_JSON, mcp_config=REMOTE_MCP_JSON),
        {"run": f"codex exec -c '{REMOTE_CODEX_CONFIG}' 'go'"},
    ))
    action, = grant["agent_launches"]
    server_args = ["mcp-remote", "https://mcp.example.com/sse", "--header", "Authorization: Bearer tokCANARY0123456789abcdef"]
    server = json.dumps(
        {"mcpServers": {"remote": {"args": [_digest(arg) for arg in server_args], "command": "npx"}}},
        separators=(",", ":"),
    )
    hook = (
        '{"hooks":{"Stop":[{"hooks":[{"command":"'
        + _digest('curl -H "X-Auth-Token: hookCANARY77" https://hooks.example.com/notify')
        + '","type":"' + _digest("command") + '"}]}]}}'
    )
    assert {item["name"]: item["value"] for item in action["settings"]} == {
        "claude_args": _digest(args),
        "mcp_config": server,
        "settings": hook,
    }
    assert grant["unread_agent_runs"] == [{"job": "review", "step": "steps[1]", "agent": "codex"}]
    text = json.dumps(grant)
    for canary in SHAPE_CANARIES:
        assert canary not in text
    limits = uncompared_agent_launch_texts(grant)
    assert [limit.split(";")[0] for limit in limits] == [
        "the claude_args value of the agent launch at review/steps[0] (anthropics/claude-code-action) is not a "
        "plain list of words this audit reads: it holds a quote, a `${{ }}` expression, `$`, a backtick, a "
        "comment, a shell operator, JSON, `--settings` or `--mcp-config`, or another character outside the "
        "plain set",
        "the `run:` at review/steps[1] mentions codex and is not read as an agent launch: only a "
        "single-line command of plain words run by bash or sh, whose program is `claude -p` or `codex exec`, "
        "is read, so this step may start an agent that is neither published nor compared, and adding, "
        "removing or editing it gives no row",
    ]

    # A withheld string is still compared: a new argument or command is a row.
    edited = HOOK_JSON.replace("curl -H", "wget --header")
    row, = _rows(_workflow(_agent(settings=HOOK_JSON)), _workflow(_agent(settings=edited)))
    assert (row.direction, row.expands) == ("changed", False)
    assert row.before != row.after and "wget" not in row.after
    # A documented setting's value, a permission rule and an enabled server are
    # published as the settings reader publishes them; other strings are not.
    launch, = _launches(_workflow(_agent(settings=json.dumps({
        "permissions": {"defaultMode": "acceptEdits", "allow": ["Bash(npm test)"], "additionalDirectories": ["../x"]},
        "enabledMcpjsonServers": ["github"], "model": "claude-opus",
    }))))
    setting = next(item for item in launch["settings"] if item["name"] == "settings")
    assert setting["value"] == (
        '{"enabledMcpjsonServers":["github"],"model":"' + _digest("claude-opus") + '",'
        '"permissions":{"additionalDirectories":["' + _digest("../x") + '"],"allow":["Bash(npm test)"],'
        '"defaultMode":"acceptEdits"}}'
    )


def test_an_mcp_server_url_publishes_its_scheme_and_host_and_compares_its_query_as_the_mcp_reader_does():
    def config(url):
        return _workflow(_agent(mcp_config=json.dumps({"mcpServers": {"db": {"url": url}}})))

    launch, = _launches(config("https://mcp.example.com/v1/sse"))
    assert {
        "name": "mcp_config", "value": '{"mcpServers":{"db":{"url":"https://mcp.example.com/<redacted-path>"}}}',
        "unresolved_reason": None,
    } in launch["settings"]
    # A query decides which tools a server exposes, so it is compared (#723); a path is not.
    row, = _rows(config("https://mcp.example.com/sse?read_only=true"), config("https://mcp.example.com/sse"))
    assert "read_only" not in row.before + row.after
    assert _rows(config("https://mcp.example.com/a"), config("https://mcp.example.com/b")) == []


def test_a_json_value_publishes_only_what_the_host_readers_publish():
    grant = _grant(_workflow(
        _agent(f"--mcp-config '{MCP_JSON}' --allowedTools Read", settings=SETTINGS_JSON, mcp_config=MCP_JSON),
        {"run": f"claude -p --mcp-config '{MCP_JSON}' --settings '{SETTINGS_JSON}' 'go'"},
    ))
    action, = grant["agent_launches"]

    assert {item["name"]: item["value"] for item in action["settings"]} == {
        "claude_args": _digest(f"--mcp-config '{MCP_JSON}' --allowedTools Read"),
        "mcp_config": MCP_PUBLISHED,
        "settings": SETTINGS_PUBLISHED,
    }
    assert grant["unread_agent_runs"] == [{"job": "review", "step": "steps[1]", "agent": "claude"}]
    text = json.dumps(grant)
    for canary in JSON_CANARIES:
        assert canary not in text
        assert hashlib.sha256(canary.encode()).hexdigest() not in text
    assert _uncompared_workflow_text(grant) is None


@pytest.mark.parametrize(
    "value",
    [
        f"--allowedTools Read --settings='{SETTINGS_JSON}'",
        f"--allowedTools Read --mcp-config='{MCP_JSON}'",
        "--allowedTools Read --settings=./ci/settings.json",
        "--allowedTools Read --mcp-config .mcp.json",
    ],
    ids=["settings-attached-json", "mcp-config-attached-json", "settings-path", "mcp-config-path"],
)
def test_a_settings_or_mcp_config_flag_leaves_claude_args_unread(value):
    """#823 review cycle 4 scope: such a value is never published, however it is attached."""

    launch, = _launches(_workflow(_agent(value)))
    assert launch["settings"] == [{"name": "claude_args", "value": _digest(value), "unresolved_reason": "unread_arguments"}]
    for canary in JSON_CANARIES:
        assert canary not in json.dumps(launch)


def test_the_word_after_a_secret_named_argument_is_withheld():
    """As the host readers withhold it among an MCP server's arguments."""

    grant = _grant(_workflow(
        _agent("--allowedTools Read --token canary-arg-1 --max-turns 5"),
        {"run": "claude -p --allowedTools Read password canary-arg-2 go"},
    ))
    action, run = grant["agent_launches"]
    assert action["settings"] == [{
        "name": "claude_args", "value": "--allowedTools Read --token <redacted> --max-turns 5",
        "unresolved_reason": "redacted",
    }]
    assert run["settings"] == [
        {"name": "--allowedTools", "value": "Read password <redacted> go", "unresolved_reason": "redacted"},
    ]
    assert "canary-arg" not in json.dumps(grant)
    # An edit inside what is redacted is not reported, so it is named as a limit.
    assert [limit.split(";")[0] for limit in uncompared_agent_launch_texts(grant)] == [
        "the claude_args value of the agent launch at review/steps[0] (anthropics/claude-code-action) contains "
        "credential-shaped text",
        "the --allowedTools value of the agent launch at review/steps[1] (claude) contains credential-shaped text",
    ]


def test_a_withheld_json_value_compares_as_the_host_readers_compare_it():
    def settings(env):
        return _workflow(_agent(settings=json.dumps({"env": env})))

    # An env value is not compared, as in `.claude/settings.json`; an added key is.
    assert _rows(settings({"DB": "one"}), settings({"DB": "two"})) == []
    row, = _rows(settings({"DB": "one"}), settings({"DB": "one", "EXTRA": "three"}))
    assert '"EXTRA":"<redacted>"' in row.after
    assert "three" not in row.after


@pytest.mark.parametrize("name", ["settings", "mcp_config"])
def test_a_json_or_path_input_that_is_neither_publishes_only_a_digest(name):
    """#823 review cycle 5 (P3): text before the JSON, such as a comment line, published the env value it holds."""

    value = '// ci\n{"env": {"DB_PASSWORD_PLAIN": "canary-env-value"}}'
    launch, = _launches(_workflow(_agent(**{name: value})))
    setting = next(item for item in launch["settings"] if item["name"] == name)
    assert setting == {"name": name, "value": _digest(value), "unresolved_reason": None}
    assert "canary-env-value" not in json.dumps(launch)
    # Still compared: an edit to it is a row, and one showing neither text.
    row, = _rows(_workflow(_agent(**{name: value})), _workflow(_agent(**{name: value.replace("canary", "other")})))
    assert (row.direction, row.expands) == ("changed", False)
    assert "canary" not in row.before + row.after and "other-env" not in row.after
    # A plain path, an expression naming one included, is published as written.
    for path in (".github/claude-settings.json", "${{ github.workspace }}/ci/settings.json"):
        launch, = _launches(_workflow(_agent(**{name: path})))
        assert next(item for item in launch["settings"] if item["name"] == name)["value"] == path


def test_a_codex_config_override_publishes_its_key_and_withholds_its_value():
    """#823 review cycle 4: only a rule-bearing key's value, the approval policy and the model are published."""

    run = (
        "codex exec -c mcp_servers.db.env.TOKEN=canary-cfg -c mcp_servers.gh.command=/opt/canary-bin/gh "
        "-c shell_environment_policy.set.LEVEL=canary-env -c model=o3 go"
    )
    action_args = "-cmcp_servers.db.env.REGION=canary-short -c=mcp_servers.x.url=https://canary.example/p --yolo"
    grant = _grant(_workflow(
        {"run": run}, {"uses": "openai/codex-action@v1", "with": {"codex-args": action_args}},
    ))
    cli, action = grant["agent_launches"]

    assert cli["settings"] == [
        {"name": "--config", "value": "mcp_servers.db.env.TOKEN=<redacted>", "unresolved_reason": None},
        {"name": "--config", "value": f"mcp_servers.gh.command={_digest('/opt/canary-bin/gh')}",
         "unresolved_reason": None},
        {"name": "--config", "value": "model=o3", "unresolved_reason": None},
        {"name": "--config", "value": f"shell_environment_policy.set.LEVEL={_digest('canary-env')}",
         "unresolved_reason": None},
    ]
    assert action["settings"] == [{
        "name": "codex-args",
        "value": (
            "-cmcp_servers.db.env.REGION=<redacted> "
            f"-c=mcp_servers.x.url={_digest('https://canary.example/p')} --yolo"
        ),
        "unresolved_reason": None,
    }]
    assert action["widening_rules"] == [{"rule": "bypass_approvals_and_sandbox", "setting": "codex-args"}]
    assert "canary" not in json.dumps(grant)
    # Rotating a redacted value is quiet; editing a withheld one is a change.
    assert _rows(_workflow({"run": run}), _workflow({"run": run.replace("canary-cfg", "rotated")})) == []
    row, = _rows(_workflow({"run": run}), _workflow({"run": run.replace("canary-env", "edited")}))
    assert row.direction == "changed"


def test_text_that_starts_like_json_and_does_not_parse_is_withheld_and_named():
    value = '{"env": {"T": "canary-unparsed"}'
    grant = _grant(_workflow(_agent(settings=value)))
    launch, = grant["agent_launches"]

    setting = next(item for item in launch["settings"] if item["name"] == "settings")
    assert setting == {"name": "settings", "value": None, "unresolved_reason": "unparsed_json"}
    assert "canary-unparsed" not in json.dumps(grant)
    limit, = uncompared_agent_launch_texts(grant)
    assert limit.startswith("the settings value of the agent launch at review/steps[0] (anthropics/claude-code-action)")
    assert "holds text that starts like JSON and does not parse" in limit
    assert _uncompared_workflow_text(grant) is None


def test_a_url_path_is_withheld_while_the_rest_of_the_setting_and_a_rule_beside_it_are_read():
    before = _workflow(_agent("--append-system-prompt Follow https://example.com/style-guide --allowedTools Read"))
    after = _workflow(_agent("--append-system-prompt Follow https://example.com/style-guide --dangerously-skip-permissions"))

    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("widened", True)
    assert (
        "claude_args: --append-system-prompt Follow https://example.com/<redacted-path> "
        "--dangerously-skip-permissions"
    ) in row.after
    assert "style-guide" not in row.before + row.after
    assert uncompared_agent_launch_texts(_grant(after)) == []
    assert _uncompared_workflow_text(_grant(after)) is None


def test_a_marketplace_url_compares_by_scheme_and_host_as_an_mcp_server_url_does():
    def marketplace(url):
        return _workflow(_agent(plugin_marketplaces=url))

    launch, = _launches(marketplace("https://github.com/anthropics/claude-code.git"))
    assert {
        "name": "plugin_marketplaces", "value": "https://github.com/<redacted-path>", "unresolved_reason": None,
    } in launch["settings"]
    row, = _rows(marketplace("https://github.com/org/a.git"), marketplace("https://gitlab.example.com/org/a.git"))
    assert "plugin_marketplaces: https://gitlab.example.com/<redacted-path>" in row.after
    # The path is withheld as an MCP server URL's is (#723), so a change only
    # there is not reported; the support page says so.
    assert _rows(marketplace("https://github.com/org/a.git"), marketplace("https://github.com/org/b.git")) == []


def test_credential_shaped_text_in_a_setting_is_published_redacted_and_named_while_a_ref_blocks():
    """#823 review F2: a redacted setting compares by its published text and rules, not blocking.

    A checkout ref names the code a job runs, as a step reference does, so a
    redacted one still refuses (#767).
    """

    grant = _grant(_workflow(
        _agent("--append-system-prompt use token=ARGCANARY --dangerously-skip-permissions",
               plugin_marketplaces="https://robot:PWCANARY@github.com/org/repo.git"),
        {"uses": "actions/checkout@v4", "with": {"ref": "token=REFCANARY"}},
        {"run": "claude -p --permission-prompt-tool ghp_" + "A" * 36 + " review token=SECRETCANARY"},
    ))
    action, cli = grant["agent_launches"]

    assert action["settings"] == [
        {"name": "claude_args",
         "value": "--append-system-prompt use token=<redacted> --dangerously-skip-permissions",
         "unresolved_reason": "redacted"},
        {"name": "plugin_marketplaces", "value": "https://github.com/<redacted-path>", "unresolved_reason": "redacted"},
    ]
    # The rule is read from the declared text, so redaction does not hide it.
    assert action["widening_rules"] == [{"rule": "bypass_permissions", "setting": "claude_args"}]
    assert cli["settings"] == [
        {"name": "--permission-prompt-tool", "value": "[REDACTED:github_token]", "unresolved_reason": "redacted"},
    ]
    assert grant["checkout_refs"] == [
        {"job": "review", "step": "steps[1]", "ref": "token=<redacted>", "unresolved_reason": "redacted"},
    ]
    text = json.dumps(grant)
    for canary in ("ARGCANARY", "PWCANARY", "REFCANARY", "SECRETCANARY", "ghp_"):
        assert canary not in text
    assert uncompared_agent_launch_texts(grant) == [
        f"the {name} value of the agent launch at {where} contains credential-shaped text; it is published "
        "redacted and compared as published, so an edit inside what is redacted that gains no documented "
        "widening rule is not reported"
        for name, where in (
            ("claude_args", "review/steps[0] (anthropics/claude-code-action)"),
            ("plugin_marketplaces", "review/steps[0] (anthropics/claude-code-action)"),
            ("--permission-prompt-tool", "review/steps[2] (claude)"),
        )
    ]
    assert _uncompared_workflow_text(grant) == (
        "a checkout ref contains credential-shaped text; it is published redacted and cannot be compared"
    )


#: Prose the #802 label redaction rewrites, as security-review prompts write it:
#: ``claude_args``, and a ``run:`` whose prompt is a variadic flag's value.
PROSE = [
    ("--append-system-prompt Never print bearer tokens in review comments --allowedTools Read",
     "claude -p --allowedTools Read Never print bearer tokens in review comments"),
    ("--append-system-prompt Flag Authorization: headers logged in plain text --allowedTools Read",
     "claude -p --allowedTools Read Flag Authorization: headers logged in plain text"),
    ("--append-system-prompt check the secret=... assignment --allowedTools Read",
     "claude -p --allowedTools Read check the secret=... assignment"),
]


@pytest.mark.parametrize(("prose", "run"), PROSE, ids=["bearer", "authorization", "assignment"])
def test_prose_the_label_redaction_rewrites_is_a_named_limit_that_refuses_nothing(prose, run):
    """#823 review F2: such prose used to make the whole workflow a blocking limit."""

    for step in (_agent(prose), {"run": run}):
        grant = _grant(_workflow(step))
        launch, = grant["agent_launches"]
        assert [setting["unresolved_reason"] for setting in launch["settings"]] == ["redacted"]
        assert _uncompared_workflow_text(grant) is None
        limit, = uncompared_agent_launch_texts(grant)
        assert "contains credential-shaped text" in limit

    # A permission change beside it keeps its row, and a rule gained beside it widens.
    row, = _rows(_workflow(_agent(prose)), _workflow(_agent(prose), permissions={"pull-requests": "write"}))
    assert row.direction == "widened" and "grants write permissions to workflow jobs" in row.why
    row, = _rows(_workflow(_agent(prose)), _workflow(_agent(f"{prose} --dangerously-skip-permissions")))
    assert (row.direction, row.expands) == ("widened", True)
    # Each cell shows the redacted text it is compared by, so the two sides differ.
    assert "<redacted>" in row.before and row.after.endswith("--dangerously-skip-permissions")


def test_an_expression_in_a_url_is_read_as_one_word_so_the_url_is_withheld_whole():
    """#823 review (P3): the expression's spaces used to split the URL, publishing its path."""

    launch, = _launches(_workflow(_agent(
        plugin_marketplaces="https://x-access-token:${{ secrets.MARKET_TOKEN }}@github.com/acme/market.git",
    )))
    setting = next(item for item in launch["settings"] if item["name"] == "plugin_marketplaces")
    assert setting == {
        "name": "plugin_marketplaces", "value": "https://github.com/<redacted-path>",
        "unresolved_reason": "redacted", "holds_expression": True,
    }
    # An expression outside a URL is published as written.
    ref, = _grant(_reproduction(ref=HEAD_SHA))["checkout_refs"]
    assert ref["ref"] == HEAD_SHA


def test_token_shaped_job_and_step_labels_are_redacted_in_every_entry():
    job = "ghp_" + "B" * 36
    grant = _grant(_workflow(jobs={job: {"steps": [
        {"name": "Pull docker://ci:hunter2@gcr.io/x", "uses": "actions/checkout@v4"},
        {"name": "Run ghp_" + "C" * 36, "run": "claude -p go"},
        {"name": "Unread ghp_" + "E" * 36, "run": "npm ci && claude -p go"},
    ]}}))

    text = json.dumps({key: grant[key] for key in ("agent_launches", "checkout_refs", "unread_agent_runs")})
    assert "ghp_" not in text and "hunter2" not in text
    assert grant["checkout_refs"][0]["step"] == "Pull docker://<redacted>@gcr.io/x"
    assert grant["agent_launches"][0]["job"] == "[REDACTED:github_token]"
    assert grant["unread_agent_runs"][0]["job"] == "[REDACTED:github_token]"
    assert "ghp_" not in " ".join(uncompared_agent_launch_texts(grant))


# --- saved baselines ---------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _write(repo, {".gitignore": "agents-shipgate-reports/\n", **files})
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


def _write(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _yaml(value) -> str:
    return yaml.safe_dump(value, sort_keys=False)


def _v06_baseline(workspace: Path):
    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import build_host_grants_baseline, host_grants_sha256

    current = host_audit_inventory(workspace)
    legacy = build_host_grants_baseline(current)
    legacy["host_grants_schema_version"] = "0.6"
    for grant in legacy["inventory"]["grants"]:
        grant.pop("agent_launches", None)
        grant.pop("unread_agent_runs", None)
        grant.pop("checkout_refs", None)
    legacy["inventory_sha256"] = host_grants_sha256(legacy["inventory"])
    return current, legacy


def test_a_v0_6_baseline_holding_a_workflow_does_not_assert_no_agent_launches(tmp_path):
    from agents_shipgate.core.host_grants import build_host_drift_payload, load_host_grants_baseline

    _write(tmp_path, {SOURCE: _yaml(_reproduction())})
    current, legacy = _v06_baseline(tmp_path)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(legacy))

    drift = build_host_drift_payload(
        baseline=load_host_grants_baseline(path), inventory=current, baseline_file=str(path)
    )
    assert drift["comparison_status"] == "incomparable"
    assert drift["incomparable_reasons"] == ["baseline_workflow_agent_launches_unavailable"]
    assert drift["has_drift"] is None and drift["changes"] == []


def test_a_v0_6_baseline_without_a_workflow_stays_comparable_and_saving_over_it_is_refused(tmp_path):
    from agents_shipgate.core.host_grants import build_host_drift_payload

    _write(tmp_path, {".claude/settings.json": json.dumps({"permissions": {"allow": ["Read(**)"]}})})
    current, legacy = _v06_baseline(tmp_path)
    drift = build_host_drift_payload(baseline=legacy, inventory=current, baseline_file="b.json")
    assert (drift["comparison_status"], drift["has_drift"]) == ("comparable", False)

    path = tmp_path / ".agents-shipgate" / "host-grants.json"
    path.parent.mkdir()
    original = json.dumps(legacy, indent=2, sort_keys=True) + "\n"
    path.write_text(original)
    audit = ["audit", "--host", "--workspace", str(tmp_path), "--baseline-file", str(path)]
    refused = CliRunner().invoke(app, [*audit, "--save-baseline"])
    assert refused.exit_code == 2
    assert path.read_text() == original

    path.rename(path.with_name("host-grants.v0.6.json"))
    resaved = CliRunner().invoke(app, [*audit, "--save-baseline"])
    assert resaved.exit_code == 0, resaved.output
    assert json.loads(path.read_text())["host_grants_schema_version"] == "0.7"


def test_the_documented_migration_from_a_v0_6_baseline_holding_a_workflow(tmp_path):
    from tests.test_preflight import _workspace

    root = _workspace(tmp_path)
    _write(root, {SOURCE: _yaml(_reproduction())})
    _, legacy = _v06_baseline(root)
    path = root / ".agents-shipgate" / "host-grants.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(legacy, indent=2, sort_keys=True) + "\n"
    path.write_text(original)
    audit = ["audit", "--host", "--workspace", str(root), "--baseline-file", str(path)]

    payload = json.loads(CliRunner().invoke(app, [*audit, "--drift", "--json"]).stdout)
    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == ["baseline_workflow_agent_launches_unavailable"]
    assert payload["has_drift"] is None and payload["next_action"] is None
    assert CliRunner().invoke(app, [*audit, "--drift", "--fail-on-drift", "--json"]).exit_code == 20

    preflight = CliRunner().invoke(app, ["preflight", "--workspace", str(root), "--json"])
    assert preflight.exit_code == 0, preflight.output
    signal, = [item for item in json.loads(preflight.stdout)["signals"] if item["kind"] == "host_grant_drift"]
    assert (signal["severity"], signal["actor"]) == ("high", "human")
    assert "baseline_workflow_agent_launches_unavailable" in json.dumps(signal)

    refused = CliRunner().invoke(app, [*audit, "--save-baseline"])
    assert refused.exit_code == 2
    assert "unsupported_baseline_schema" in refused.output + (refused.stderr or "")
    assert path.read_text() == original

    path.rename(path.with_name("host-grants.v0.6.json"))
    resaved = CliRunner().invoke(app, [*audit, "--save-baseline"])
    assert resaved.exit_code == 0, resaved.output
    after = json.loads(CliRunner().invoke(app, [*audit, "--drift", "--json"]).stdout)
    assert (after["comparison_status"], after["has_drift"]) == ("comparable", False)
    assert path.with_name("host-grants.v0.6.json").read_text() == original


def test_a_current_baseline_compares_agent_launches_and_validates_against_the_schemas(tmp_path):
    from jsonschema import Draft202012Validator

    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import (
        build_host_drift_payload,
        build_host_grants_baseline,
    )

    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text(_yaml(_reproduction(run="npm ci && claude -p 'x'", ref=HEAD_SHA)))
    inventory = host_audit_inventory(tmp_path)
    baseline = build_host_grants_baseline(inventory)
    assert baseline["host_grants_schema_version"] == "0.7"
    workflow, = [grant for grant in baseline["inventory"]["grants"] if grant["kind"] == "workflow"]
    assert workflow["unread_agent_runs"] == [{"job": "review", "step": "steps[2]", "agent": "claude"}]
    for name, payload in (("inventory", inventory), ("baseline", baseline)):
        schema = json.loads((ROOT / f"docs/host-grants-{name}-schema.v0.7.json").read_text())
        Draft202012Validator(schema).validate(payload)

    path.write_text(_yaml(_reproduction(claude_args="--dangerously-skip-permissions", ref=HEAD_SHA)))
    drift = build_host_drift_payload(baseline=baseline, inventory=host_audit_inventory(tmp_path), baseline_file="b.json")
    assert (drift["comparison_status"], drift["has_drift"]) == ("comparable", True)
    assert drift["expansion_signals"] == [f"workflow_agent_widened_changed: {SOURCE}"]
    schema = json.loads((ROOT / "docs/host-grants-drift-schema.v0.7.json").read_text())
    Draft202012Validator(schema).validate(drift)


def test_an_unread_run_is_a_non_blocking_limit_that_leaves_coverage_complete(tmp_path):
    from agents_shipgate.cli.host_audit import host_audit_inventory

    _write(tmp_path, {SOURCE: _yaml(_workflow({"run": "npm ci && claude -p 'go'"}))})
    inventory = host_audit_inventory(tmp_path)

    github, = [item for item in inventory["host_coverage"] if item["host"] == "github"]
    assert github["status"] == "complete"
    issue, = [item for item in inventory["issues"] if item["host"] == "github"]
    assert (issue["kind"], issue["blocking"]) == ("unsupported", False)
    assert issue["message"].startswith(
        "the `run:` at review/steps[0] mentions claude and is not read as an agent launch"
    )


@pytest.mark.parametrize("run", UNREAD_RUNS[:8], ids=[f"review-cycle-4-{index}" for index in range(8)])
def test_the_run_forms_of_review_cycle_4_give_no_row_and_are_a_named_coverage_issue(tmp_path, run):
    """#823 review cycle 4 (a) end to end: named in `audit --host`, never a row, none of the text published."""

    from agents_shipgate.cli.host_audit import host_audit_inventory

    permissions = {"contents": "write", "pull-requests": "write"}
    base = _workflow({"uses": "actions/checkout@v4"}, trigger="issue_comment", permissions=permissions)
    head = _workflow({"uses": "actions/checkout@v4"}, {"run": run}, trigger="issue_comment", permissions=permissions)
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head)})
    _git(repo, "commit", "-qam", "an agent step")

    assert _diff(repo)["rows"] == []
    inventory = host_audit_inventory(repo)
    workflow, = [grant for grant in inventory["grants"] if grant.get("kind") == "workflow"]
    assert "agent_launches" not in workflow
    issues = [item for item in inventory["issues"] if item["host"] == "github"]
    assert issues and all(not item["blocking"] for item in issues)
    assert all("review/steps[1] mentions" in item["message"] for item in issues)
    audit = CliRunner().invoke(app, ["audit", "--host", "--workspace", str(repo)])
    assert "review/steps[1] mentions" in audit.output
    joined = json.dumps(inventory) + audit.output
    for text in ("dangerously", "yolo", "Review this PR", "review this change"):
        assert text not in joined


def test_a_change_that_only_adds_an_unread_step_says_what_the_workflow_does_not_compare(tmp_path):
    """#823 review cycle 7 (carried P3): the coverage line named env values and apiKeyHelper."""

    base = _workflow({"uses": "actions/checkout@v4"})
    head = _workflow({"uses": "actions/checkout@v4"}, {"run": f"claude -p {BYPASS} 'Review'"})
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head)})
    _git(repo, "commit", "-qam", "an unread agent step")

    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", "main"])
    assert result.exit_code == 0, result.output
    assert "No static host-grant changes detected." in result.output
    assert (
        f"{SOURCE} (github): compared; changed, but no grant this entry compares changed, so no row "
        "(text this entry does not read, such as a step's env or an unread agent step, is not "
        "compared; audit --host names each unread agent step)"
    ) in result.output
    assert "apiKeyHelper" not in result.output
    assert "dangerously" not in result.output


@pytest.mark.parametrize(
    "still_there",
    [f'claude -p {BYPASS} "Review"', f"npx @anthropic-ai/claude-code -p {BYPASS} Review"],
    ids=["quoted", "npx"],
)
def test_a_launch_the_job_still_runs_unread_has_not_moved_to_the_job_that_adds_it(tmp_path, still_there):
    """#823 review cycle 5 (C5-F1, M5 and M6) end to end: `diff` widens, `audit --host` names the unread step."""

    from agents_shipgate.cli.host_audit import host_audit_inventory

    permissions = {"contents": "write"}
    plain = f"claude -p {BYPASS} Review"
    base = _workflow(jobs={"a": {"steps": [{"run": plain}]}, "b": {"steps": [{"run": "echo hi"}]}},
                     permissions=permissions)
    head = _workflow(jobs={"a": {"steps": [{"run": still_there}]}, "b": {"steps": [{"run": plain}]}},
                     permissions=permissions)
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head)})
    _git(repo, "commit", "-qam", "an agent step in b")

    row, = _diff(repo)["rows"]
    assert (row["direction"], row["expands"]) == ("widened", True)
    assert "an agent launch now skips permission checks (bypassPermissions) (b/steps[0])" in row["why"]
    assert "a step no longer declares an agent launch this audit reads (a/steps[0])" in row["why"]
    assert "moved between jobs" not in row["why"]
    workflow, = [grant for grant in host_audit_inventory(repo)["grants"] if grant.get("kind") == "workflow"]
    assert workflow["unread_agent_runs"] == [{"job": "a", "step": "steps[0]", "agent": "claude"}]


def test_a_launch_merged_into_an_unread_step_it_did_not_hold_has_not_moved(tmp_path):
    """#823 review cycle 6 (C6-F1, V1) end to end: the job keeps as many unread steps, at another step."""

    from agents_shipgate.cli.host_audit import host_audit_inventory

    permissions = {"contents": "write"}
    plain = f"claude -p {BYPASS} Review"
    install = "npm i -g @anthropic-ai/claude-code"
    base = _workflow(jobs={"a": {"steps": [{"run": install}, {"run": plain}]}, "b": {"steps": [{"run": "echo hi"}]}},
                     permissions=permissions)
    head = _workflow(jobs={"a": {"steps": [{"run": f"{install} && {plain}"}]}, "b": {"steps": [{"run": plain}]}},
                     permissions=permissions)
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head)})
    _git(repo, "commit", "-qam", "merge the install into the launch, and launch in b")

    payload = _diff(repo)
    row, = payload["rows"]
    assert (row["direction"], row["expands"]) == ("widened", True)
    assert "an agent launch now skips permission checks (bypassPermissions) (b/steps[0])" in row["why"]
    assert "a step no longer declares an agent launch this audit reads (a/steps[1])" in row["why"]
    assert "may still start an agent in a way this audit does not read" in row["why"]
    assert "moved between jobs" not in row["why"]
    workflow, = [grant for grant in host_audit_inventory(repo)["grants"] if grant.get("kind") == "workflow"]
    assert workflow["unread_agent_runs"] == [{"job": "a", "step": "steps[0]", "agent": "claude"}]


def test_a_codex_config_integer_past_the_digit_limit_crashes_no_route(tmp_path):
    """#823 review cycle 6 (C6-F2): `diff`, `audit --host` and `check` exit 0, and `verify` is no internal error."""

    repo = _repo(tmp_path, {SOURCE: _yaml(_workflow({"run": "echo hi"}))})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(_workflow({"run": "codex exec -c sandbox_mode=" + "1" * 5000 + " Review"}))})
    _git(repo, "commit", "-qam", "a codex step")

    for args in (
        ["diff", "--workspace", str(repo), "--base", "main", "--json"],
        ["audit", "--host", "--workspace", str(repo), "--json"],
        ["check", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json"],
        ["verify", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "text"],
    ):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, (args[0], result.output, result.exception)
    row, = _diff(repo)["rows"]
    assert (row["direction"], row["expands"]) == ("changed", False)


# --- the same row on every route ---------------------------------------------------


@pytest.fixture
def pr(tmp_path):
    repo = _repo(tmp_path, {SOURCE: _yaml(_reproduction())})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(_reproduction(claude_args="--permission-mode bypassPermissions --allowedTools Bash"))})
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "bypass permissions")
    return repo


def _assert_the_row(row: dict) -> None:
    assert row["subject"] == f"github {SOURCE}"
    assert "review/steps[1]: runs anthropics/claude-code-action with claude_args: --allowedTools Read" in row["before"]
    assert "--permission-mode bypassPermissions" in row["after"]
    assert (row["direction"], row["expands"]) == ("widened", True)
    assert "skips permission checks" in row["why"]


def _diff(repo: Path, *args: str) -> dict:
    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", "main", *args, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_diff_names_the_widening_in_json_and_text(pr):
    payload = _diff(pr)
    assert payload["comparison_status"] == "comparable"
    row, = payload["rows"]
    _assert_the_row(row)

    text = CliRunner().invoke(app, ["diff", "--workspace", str(pr), "--base", "main"])
    assert text.exit_code == 0, text.output
    assert "⚠" in text.output and "review/steps[1]" in text.output
    assert "1 widening what the agent may do" in text.output


def test_manifest_free_verify_and_its_pr_comment_name_the_widening(pr):
    args = ["verify", "--workspace", str(pr), "--base", "main", "--head", "HEAD", "--format", "text"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "bypassPermissions" in result.output

    comment = (pr / "agents-shipgate-reports/pr-comment.md").read_text()
    assert "bypassPermissions" in comment and "review/steps[1]" in comment
    verifier = json.loads((pr / "agents-shipgate-reports/verifier.json").read_text())
    row, = verifier["host_comparison"]["rows"]
    _assert_the_row(row)


def test_check_and_the_control_envelope_carry_the_same_row(pr):
    args = ["check", "--workspace", str(pr), "--base", "main", "--head", "HEAD"]
    machine = CliRunner().invoke(app, [*args, "--format", "agent-boundary-json"])
    assert machine.exit_code == 0, machine.output
    row, = json.loads(machine.output)["rows"]
    _assert_the_row(row)

    control = CliRunner().invoke(app, [*args, "--format", "agent-control-json"])
    assert control.exit_code == 0, control.output
    envelope_row, = json.loads(control.output)["capability_rows"]["rows"]
    _assert_the_row(envelope_row)


def test_the_stop_hook_announces_the_widening(pr, tmp_path):
    from tests.test_install_hooks import _host_diff_workspace, _run_hook

    payload = _diff(pr)
    hooked = tmp_path / "hooked"
    hooked.mkdir()
    _host_diff_workspace(hooked)
    result = _run_hook(hooked, "verify", {}, diff_payload=json.dumps(payload))

    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert "These rows widen what the agent can do" in message
    assert SOURCE in message


def _published_outputs(repo: Path) -> str:
    """Every route's output for the change on ``repo``: diff, audit, check, verify and its reports."""

    outputs = []
    for args in (
        ["diff", "--workspace", str(repo), "--base", "main", "--json"],
        ["diff", "--workspace", str(repo), "--base", "main"],
        ["audit", "--host", "--workspace", str(repo), "--json"],
        ["audit", "--host", "--workspace", str(repo)],
        ["check", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json"],
        ["check", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "agent-control-json"],
        ["verify", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "text"],
    ):
        result = CliRunner().invoke(app, args)
        outputs.append(result.output)
    outputs.append((repo / "agents-shipgate-reports/pr-comment.md").read_text())
    outputs.append((repo / "agents-shipgate-reports/verifier.json").read_text())
    return "\n".join(outputs)


def _assert_absent(joined: str, canaries) -> None:
    for canary in canaries:
        assert canary not in joined, canary
        digest = hashlib.sha256(canary.encode()).hexdigest()
        for prefix in (digest, digest[:24], digest[:12]):
            assert prefix not in joined, canary


def test_no_canary_reaches_any_published_output(tmp_path):
    """The #802 sweep for agent launches, JSON-shaped canaries included (#823 review F3, cycle 4)."""

    canary = "sk-ant-api03-" + "Z" * 40
    job = "ghp_" + "D" * 36
    mcp = json.dumps({"mcpServers": {"db": {
        "command": "db-mcp", "env": {"DB_API_TOKEN": "canary-tok-123"},
        "headers": {"X-API-Key": "canary-hdr-456", "Authorization": f"Bearer {canary}"},
    }}})
    codex_args = (
        "-c mcp_servers.db.env.TOKEN=canary-cfg-789 -cmcp_servers.db.env.REGION=canary-short-c "
        "-c=mcp_servers.db.env.ZONE=canary-eq-c -c mcp_servers.db.command=/opt/canary-cmd-c --full-auto"
    )
    # The `=` spellings of #823 review F1, beside the separate-word ones.
    equals = json.dumps({"env": {"DB_PASSWORD": "hunter2-eqcanary"}, "apiKeyHelper": "echo helper-eqcanary"})
    equals_mcp = json.dumps({"mcpServers": {"db": {
        "command": "db-mcp", "env": {"DB_API_TOKEN": "tok-eqcanary"}, "headers": {"X-API-Key": "hdr-eqcanary"},
    }}})
    base = _workflow(jobs={job: {"steps": [_agent()]}})
    head = _workflow(jobs={job: {"steps": [
        {"name": "Pull docker://ci:" + "p4ssCANARY" + "@gcr.io/x", "uses": "actions/checkout@v4"},
        _agent(
            "--allowedTools Read --dangerously-skip-permissions",
            settings=SETTINGS_JSON, mcp_config=mcp,
            plugin_marketplaces="https://github.com/canary-org/canary-repo.git",
        ),
        # #823 review cycle 4: `claude_args` or a `run:` holding JSON, `--settings`
        # or `--mcp-config` is not read, and publishes only a digest or nothing.
        _agent(f"--mcp-config '{mcp}' --dangerously-skip-permissions"),
        _agent(f"--allowedTools Read --settings='{equals}' --mcp-config='{equals_mcp}'"),
        {"run": f"ANTHROPIC_API_KEY={canary} claude -p --allowedTools Read --mcp-config '{mcp}' 'go'"},
        {"run": f"ANTHROPIC_API_KEY={canary} claude -p --allowedTools Read go"},
        {"uses": "openai/codex-action@v1", "with": {"codex-args": codex_args}},
        # #823 review C2-F1: an MCP server's arguments and a hook's command,
        # which the host readers never publish, in every spelling.
        _agent("--allowedTools Read", mcp_config=REMOTE_MCP_JSON, settings=HOOK_JSON),
        _agent(f"--allowedTools Read\n--mcp-config '{REMOTE_MCP_JSON}'"),
        {"run": f"claude -p --settings '{HOOK_JSON}' --mcp-config '{REMOTE_MCP_JSON}' 'go'"},
        {"run": f"codex exec -c '{REMOTE_CODEX_CONFIG}' 'go'"},
    ]}})
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head)})
    _git(repo, "commit", "-qam", "canaries")

    payload = _diff(repo)
    assert payload["comparison_status"] == "comparable"
    row, = payload["rows"]
    assert (row["direction"], row["expands"]) == ("widened", True)
    assert '"headers":{"Authorization":"<redacted>","X-API-Key":"<redacted>"}' in row["after"]
    assert f"settings: {SETTINGS_PUBLISHED}" in row["after"]
    joined = _published_outputs(repo)

    _assert_absent(joined, (
        canary, "p4ssCANARY", job, "canary-org", "canary-repo", "canary-cfg-789", *JSON_CANARIES,
        "hunter2-eqcanary", "helper-eqcanary", "tok-eqcanary", "hdr-eqcanary", "canary-short-c", "canary-eq-c",
        "canary-cmd-c", *SHAPE_CANARIES,
    ))
    assert "runs claude -p with --allowedTools Read" in joined
    assert "mcp_servers.db.env.TOKEN=<redacted>" in joined
    assert '"command":"npx"' in joined and '"Stop":[{"hooks":[{"command":"<withheld:' in joined
    assert "not read; digest <withheld:" in joined


def test_a_credential_shaped_ref_refuses_a_changed_workflow_and_publishes_no_canary(tmp_path):
    base = _workflow(_agent())
    head = _workflow({"uses": "actions/checkout@v4", "with": {"ref": "token=REFCANARY"}}, _agent())
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head)})
    _git(repo, "commit", "-qam", "credential-shaped ref")

    payload = _diff(repo)
    assert payload["comparison_status"] == "incomparable" and payload["rows"] == []
    assert "head_inventory_incomplete" in payload["incomparable_reasons"]
    audit = json.loads(CliRunner().invoke(app, ["audit", "--host", "--workspace", str(repo), "--json"]).stdout)
    issue, = [item for item in audit["issues"] if item["host"] == "github"]
    assert (issue["kind"], issue["blocking"]) == ("unsupported", True)
    assert "a checkout ref contains credential-shaped text" in issue["message"]
    _assert_absent(_published_outputs(repo), ("REFCANARY",))


def test_a_credential_shaped_setting_compares_on_every_route_and_publishes_no_canary(tmp_path):
    base = _workflow(_agent())
    head = _workflow(_agent("--append-system-prompt use token=ARGCANARY --dangerously-skip-permissions"))
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head)})
    _git(repo, "commit", "-qam", "credential-shaped setting")

    payload = _diff(repo)
    assert payload["comparison_status"] == "comparable"
    row, = payload["rows"]
    assert (row["direction"], row["expands"]) == ("widened", True)
    assert "--append-system-prompt use token=<redacted> --dangerously-skip-permissions" in row["after"]
    audit = json.loads(CliRunner().invoke(app, ["audit", "--host", "--workspace", str(repo), "--json"]).stdout)
    issue, = [item for item in audit["issues"] if item["host"] == "github"]
    assert (issue["kind"], issue["blocking"]) == ("unsupported", False)
    github, = [item for item in audit["host_coverage"] if item["host"] == "github"]
    assert github["status"] == "complete"
    _assert_absent(_published_outputs(repo), ("ARGCANARY",))


def _prose_repo(tmp_path: Path, head: dict, extra: dict[str, str] | None = None) -> Path:
    base = _workflow(_agent(PROSE[0][0]))
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head), **(extra or {})})
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")
    return repo


def test_a_permission_change_beside_redacted_prose_keeps_its_row_on_every_route(tmp_path):
    """#823 review F2 (a): the prose used to refuse the comparison and hide this row."""

    repo = _prose_repo(tmp_path, _workflow(_agent(PROSE[0][0]), permissions={"contents": "read", "pull-requests": "write"}))

    payload = _diff(repo)
    assert payload["comparison_status"] == "comparable"
    row, = payload["rows"]
    assert row["direction"] == "widened" and "grants write permissions to workflow jobs" in row["why"]
    result = CliRunner().invoke(app, ["verify", "--workspace", str(repo), "--base", "main", "--head", "HEAD"])
    assert result.exit_code == 0, result.output
    verifier = json.loads((repo / "agents-shipgate-reports/verifier.json").read_text())
    assert verifier["host_comparison"]["comparison_status"] == "comparable"
    verified, = verifier["host_comparison"]["rows"]
    assert verified["direction"] == "widened"
    assert "Host capability comparison unavailable" not in (repo / "agents-shipgate-reports/pr-comment.md").read_text()


def test_an_unchanged_workflow_holding_redacted_prose_leaves_check_comparable(tmp_path):
    """#823 review F2 (b): an unchanged workflow used to make every `check` incomparable (#721)."""

    mcp = json.dumps({"mcpServers": {"docs": {"command": "docs-mcp"}}})
    repo = _prose_repo(tmp_path, _workflow(_agent(PROSE[0][0])), extra={".mcp.json": mcp})

    args = ["check", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    boundary = json.loads(result.output)
    assert boundary["comparison_status"] == "comparable", boundary
    row, = boundary["rows"]
    assert row["direction"] == "added" and ".mcp.json" in row["subject"]
