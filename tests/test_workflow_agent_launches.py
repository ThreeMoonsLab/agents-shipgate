"""#823: how a coding agent is launched inside a workflow job is read as text.

The workflow grant already read triggers, token permissions, reusable calls and
step `uses:` references (#771), and nothing that says how an agent is started.
It now lists each documented agent action's permission inputs, the permission
flags of a literal `claude -p` / `codex exec` run step, and each
`actions/checkout` step's `with.ref`. Nothing is executed, fetched or
evaluated. Only a documented rule a job's launches gain widens; every other
edit is `changed`; a shape this reader does not read is a named limit, never a
row that claims an effect; and a workflow row that runs an agent names the job
facts beside it.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    _claude_argument_input,
    _codex_argument_input,
    _literal_argument_words,
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


def _workflow(*steps, trigger="pull_request", permissions=None, jobs=None, env=None):
    data = {
        "on": trigger,
        "permissions": permissions if permissions is not None else {"contents": "read", "pull-requests": "read"},
        "jobs": jobs if jobs is not None else {"review": {"runs-on": "ubuntu-latest", "steps": list(steps)}},
    }
    if env is not None:
        data["env"] = env
    return data


def _agent(claude_args='--allowedTools "Read"', **extra):
    return {"uses": CLAUDE, "with": {"claude_args": claude_args, **extra}}


def _reproduction(trigger="pull_request", pr="read", claude_args='--allowedTools "Read"', run="echo done", ref=None):
    """The workflow of #823's reproduction, as its `wf` shell function writes it."""

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


# --- the four cases of the reproduction --------------------------------------------


def test_args_gaining_bypass_permissions_is_one_widened_row_naming_job_step_and_both_values():
    row, = _rows(
        _reproduction(),
        _reproduction(claude_args='--permission-mode bypassPermissions --allowedTools "Bash(*)"'),
    )

    assert row.subject == f"github {SOURCE}"
    assert (row.direction, row.expands) == ("widened", True)
    assert 'review/steps[1]: runs anthropics/claude-code-action with claude_args: --allowedTools "Read"' in row.before
    assert (
        'review/steps[1]: runs anthropics/claude-code-action with claude_args: '
        '--permission-mode bypassPermissions --allowedTools "Bash(*)"'
    ) in row.after
    assert "an agent launch now skips permission checks (bypassPermissions) (review/steps[1])" in row.why


def test_a_literal_claude_run_step_is_one_changed_row_with_its_permission_flags():
    row, = _rows(
        _reproduction(),
        _reproduction(run='claude -p --permission-mode acceptEdits --allowedTools "Bash(*)" "Summarize this change"'),
    )

    assert (row.direction, row.expands) == ("changed", False)
    assert "review/steps[2]" not in row.before
    # A variadic flag reads every following word up to the next flag, as the
    # CLI reads it, so the trailing quoted word is part of --allowedTools.
    assert (
        "review/steps[2]: runs claude -p with --allowedTools 'Bash(*)' 'Summarize this change'; "
        "--permission-mode acceptEdits"
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
            "claude --print --allowed-tools=Read --disallowedTools 'Bash(rm *)' "
            "--model sonnet --dangerously-skip-permissions --add-dir ../docs \"secret prompt text\""
        ),
    }))

    assert (launch["agent"], launch["step"], launch["form"]) == ("claude", "Review", "read")
    assert launch["settings"] == [
        {"name": "--add-dir", "value": "../docs 'secret prompt text'", "unresolved_reason": None},
        {"name": "--allowedTools", "value": "Read", "unresolved_reason": None},
        {"name": "--dangerously-skip-permissions", "value": None, "unresolved_reason": None},
        {"name": "--disallowedTools", "value": "'Bash(rm *)'", "unresolved_reason": None},
    ]
    assert "sonnet" not in json.dumps(launch)


def test_a_literal_codex_exec_command_publishes_its_permission_flags():
    launch, = _launches(_workflow({"run": "codex e -s danger-full-access --yolo -c model=o3 'fix it'"}))

    assert (launch["agent"], launch["form"]) == ("codex", "read")
    assert launch["settings"] == [
        {"name": "--config", "value": "model=o3", "unresolved_reason": None},
        {"name": "--dangerously-bypass-approvals-and-sandbox", "value": None, "unresolved_reason": None},
        {"name": "--sandbox", "value": "danger-full-access", "unresolved_reason": None},
    ]


def test_literal_assignments_before_the_command_are_skipped_and_never_published():
    launch, = _launches(_workflow({"run": "CI=true claude -p --permission-mode plan 'go'"}))
    assert launch["form"] == "read"
    assert launch["settings"] == [{"name": "--permission-mode", "value": "plan", "unresolved_reason": None}]


@pytest.mark.parametrize(
    "run",
    [
        "claude mcp add github -- npx server",
        "claude --version",
        "codex login --api-key sk-test",
        'echo "claude -p --dangerously-skip-permissions"',
        "echo claude -p done",
        "npm test",
    ],
    ids=["claude-mcp", "claude-version", "codex-login", "echo-quoted", "echo-bare", "unrelated"],
)
def test_a_command_that_launches_no_headless_agent_is_not_listed(run):
    assert _launches(_workflow({"run": run})) == []


@pytest.mark.parametrize(
    ("run", "reason"),
    [
        ("npm ci && claude -p --dangerously-skip-permissions 'go'", "compound_command"),
        ("npm ci\nclaude -p 'go'", "compound_command"),
        ("claude -p 'go' | tee review.md", "compound_command"),
        ("cat <<EOF | claude -p\nreview\nEOF", "compound_command"),
        ("claude -p $CLAUDE_FLAGS 'go'", "shell_expansion"),
        ('claude -p "$(cat prompt.md)"', "shell_expansion"),
        ('claude -p "Fix ${{ github.event.issue.title }}"', "expression"),
        ("cat <<EOF > prompt.md\nIt's broken\nEOF\nclaude -p --dangerously-skip-permissions 'go'", "compound_command"),
        ("claude -p --dangerously-skip-permissions \"go", "compound_command"),
    ],
    ids=["and", "lines", "pipe", "heredoc", "variable", "substitution", "expression", "unbalanced-heredoc",
         "unbalanced"],
)
def test_a_shape_this_reader_does_not_read_is_unresolved_and_publishes_no_text(run, reason):
    launch, = _launches(_workflow({"run": run}))

    assert (launch["agent"], launch["form"], launch["unresolved_reason"]) == ("claude", "unresolved", reason)
    assert launch["settings"] == []
    assert "dangerously" not in json.dumps(launch) and "go" not in json.dumps(launch["settings"])
    limit, = uncompared_agent_launch_texts(_grant(_workflow({"run": run})))
    assert limit.startswith("the agent launch at review/steps[0] (claude) is ")
    assert "not reported" in limit


def test_a_quoted_word_that_starts_with_a_hash_is_not_a_comment():
    launch, = _launches(_workflow({"run": 'claude -p --allowedTools Read "#123 review"'}))
    assert launch["form"] == "read"
    assert launch["settings"] == [{"name": "--allowedTools", "value": "Read '#123 review'", "unresolved_reason": None}]

    commented, = _launches(_workflow({"run": "claude -p --allowedTools Read # review"}))
    assert (commented["form"], commented["unresolved_reason"]) == ("unresolved", "compound_command")


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


def test_single_quoted_dollars_are_literal_and_do_not_stop_the_read():
    launch, = _launches(_workflow({"run": "claude -p --allowedTools 'Bash(echo $HOME)' 'go'"}))
    assert launch["form"] == "read"
    assert launch["settings"][0]["value"] == "'Bash(echo $HOME)' go"


def test_inputs_that_are_not_a_mapping_are_unresolved():
    launch, = _launches(_workflow({"uses": CLAUDE, "with": ["claude_args"]}))
    assert (launch["form"], launch["unresolved_reason"], launch["settings"]) == (
        "unresolved", "inputs_not_a_mapping", [],
    )


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
    assert "agent_launches" not in grant and "checkout_refs" not in grant


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


# --- how an agent action splits its argument input (#823 review cycle 1) -----------------
#
# `claude_args` and `codex-args` are not shell text. The Claude actions split
# `claude_args` with shell-quote after dropping full `#` lines and making
# `()|&;<>` literal (base-action/src/parse-sdk-options.ts); `openai/codex-action`
# reads `codex-args` as a JSON array of strings or with string-argv. A widening
# rule is read from the words the action passes on.


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("--max-turns 5\n--allowedTools Read", "--max-turns 5\n--dangerously-skip-permissions"),
        ("--allowedTools Bash(git:*)", "--allowedTools Bash(git:*) --dangerously-skip-permissions"),
        ("# review agent\n--max-turns 5", "# review agent\n--dangerously-skip-permissions"),
        ("--max-turns 5", "--max-turns 5\n--permission-mode\nbypassPermissions"),
        # A word starting with `--` is always a flag to the action, never a value.
        ("--allowedTools Read", "--settings --dangerously-skip-permissions"),
    ],
    ids=["several-lines", "unquoted-parentheses", "comment-line", "mode-over-lines", "never-a-value"],
)
def test_claude_args_are_split_as_the_claude_actions_split_them(before, after):
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
        "  # an indented comment line is dropped too\n--max-turns 5",
        # An unquoted `#` later in the input ends it, as shell-quote reads it.
        "--max-turns 5 # --dangerously-skip-permissions",
        "--max-turns 5 notes#--dangerously-skip-permissions",
    ],
    ids=["comment-line", "indented-comment", "inline-comment", "hash-in-a-word"],
)
def test_a_flag_the_action_drops_as_a_comment_meets_no_rule(after):
    launch, = _launches(_workflow(_agent(after)))
    assert "widening_rules" not in launch
    assert host_grant_expansion_signals(_changes(_workflow(_agent("--max-turns 5")), _workflow(_agent(after)))) == []


def test_editing_only_a_comment_line_the_action_drops_is_quiet():
    before = _workflow(_agent("# reviewer: alice\n--max-turns 5"))
    after = _workflow(_agent("# reviewer: bob\n# --dangerously-skip-permissions\n--max-turns 5"))

    assert _launches(after)[0]["settings"] == [
        {"name": "claude_args", "value": "--max-turns 5", "unresolved_reason": None},
    ]
    assert _rows(before, after) == []


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ('--allowedTools "Bash(git status)" \'Read\'', ["--allowedTools", "Bash(git status)", "Read"]),
        ("--allowedTools Bash(gh:*)|Read;x", ["--allowedTools", "Bash(gh:*)|Read;x"]),
        ('cost$5 "$HOME/x" $', ["cost", "/x", "$"]),
        ('--append-system-prompt "unbalanced --dangerously-skip-permissions',
         ["--append-system-prompt", "unbalanced", "--dangerously-skip-permissions"]),
        ('a\\ b "c\\"d"', ["a b", 'c"d']),
        ("--x a#b --y", ["--x", "a"]),
        ("--x ${}", None),
    ],
    ids=["quotes", "metacharacters", "variables", "unbalanced-quote", "escapes", "hash", "bad-substitution"],
)
def test_the_claude_args_splitter_reads_as_shell_quote_does(value, words):
    assert _claude_argument_input(value).words == (None if words is None else tuple(words))


@pytest.mark.parametrize(
    ("codex_args", "rule"),
    [
        ("--json\n--dangerously-bypass-approvals-and-sandbox", "bypasses approvals and the sandbox"),
        ("--full-auto --yolo", "bypasses approvals and the sandbox"),
        ('["--json", "--yolo"]', "bypasses approvals and the sandbox"),
        ("-s 'danger-full-access'", "runs without a sandbox (danger-full-access)"),
        ("--json\n--sandbox=danger-full-access", "runs without a sandbox (danger-full-access)"),
    ],
    ids=["several-lines", "yolo", "json-array", "quoted-short-sandbox", "attached-sandbox"],
)
def test_codex_args_are_read_as_the_codex_action_reads_them(codex_args, rule):
    before = _workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": "--json"}})
    after = _workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": codex_args}})

    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("widened", True)
    assert rule in row.why


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("--json\n--full-auto", ["--json", "--full-auto"]),
        ("--flag=\"a b\" 'c d' e", ['--flag="a b"', "c d", "e"]),
        ('["-c", "x=1"]', ["-c", "x=1"]),
        ("[1, 2]", None),
        ("[not json", None),
    ],
    ids=["lines", "string-argv-quotes", "json-array", "not-strings", "invalid-json"],
)
def test_the_codex_args_reader_reads_as_the_codex_action_does(value, words):
    assert _codex_argument_input(value).words == (None if words is None else tuple(words))


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
        _workflow({"run": "claude -p 'a different prompt' --model opus --allowedTools Read"}),
        # renamed, respelled and reordered flags
        _workflow({"name": "Renamed", "run": "claude --allowed-tools=Read --print 'review'"}),
    ],
    ids=["prompt-and-model", "rename-and-reorder"],
)
def test_an_edit_outside_the_compared_flags_is_quiet(after):
    before = _workflow({"run": "claude -p 'review' --allowedTools Read"})
    assert _rows(before, after) == []


def test_a_word_after_a_variadic_flag_is_read_as_its_value_as_the_cli_reads_it():
    before = _workflow({"run": "claude -p 'review' --allowedTools Read"})
    after = _workflow({"run": "claude -p --allowedTools Read 'review'"})

    row, = _rows(before, after)
    assert "--allowedTools Read review" in row.after and "--allowedTools Read," in row.before + ","


def test_an_edit_inside_an_unresolved_command_is_quiet_and_named_as_a_limit():
    before = _workflow({"run": "npm ci && claude -p --allowedTools Read 'go'"})
    after = _workflow({"run": "npm ci && claude -p --dangerously-skip-permissions 'go'"})

    assert _rows(before, after) == []
    assert uncompared_agent_launch_texts(_grant(after))


def test_adding_an_unresolved_launch_is_a_row_that_claims_no_effect():
    row, = _rows(_workflow({"run": "npm test"}), _workflow({"run": "npm ci && claude -p --yolo 'go'"}))

    assert (row.direction, row.expands) == ("changed", False)
    assert "review/steps[0]: runs claude -p (unresolved: compound command)" in row.after
    assert "a step launches an agent in a form this audit does not read (review/steps[0])" in row.why
    assert "does not say what that agent may do" in row.why


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
        (_workflow({"run": "claude -p 'x'"}), _workflow({"run": "claude -p --permission-mode=bypassPermissions 'x'"}),
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
         _workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": '["--yolo"]'}}),
         "bypasses approvals and the sandbox"),
        (_workflow({"uses": "openai/codex-action@v1", "with": {"allow-users": "a"}}),
         _workflow({"uses": "openai/codex-action@v1", "with": {"allow-users": "*"}}),
         "accepts runs triggered by any user (allow-users: *)"),
        (_workflow({"run": "codex exec 'x'"}), _workflow({"run": "codex exec --sandbox danger-full-access 'x'"}),
         "runs without a sandbox"),
        # Literal text an expression cannot reach still meets a rule (#823 review F4).
        (_workflow(_agent()), _workflow(_agent("--dangerously-skip-permissions ${{ inputs.extra }}")),
         "skips permission checks"),
        (_workflow(_agent()), _workflow(_agent(allowed_non_write_users="${{ vars.USERS }}, *")),
         "accepts runs triggered by any user (allowed_non_write_users: *)"),
        # #823 review C2-F2: the documented bypasses written through inputs the reader lists.
        (_workflow(_agent(settings=json.dumps({"permissions": {"defaultMode": "default"}}))),
         _workflow(_agent(settings=json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}))),
         "skips permission checks (bypassPermissions)"),
        (_workflow(_agent()), _workflow(_agent(settings=json.dumps({"defaultMode": "bypassPermissions"}))),
         "skips permission checks (bypassPermissions)"),
        (_workflow(_agent()),
         _workflow(_agent("--settings '{\"permissions\":{\"defaultMode\":\"bypassPermissions\"}}'")),
         "skips permission checks (bypassPermissions)"),
        (_workflow({"run": "claude -p 'x'"}),
         _workflow({"run": "claude -p --settings '{\"permissions\":{\"defaultMode\":\"bypassPermissions\"}}' 'x'"}),
         "skips permission checks (bypassPermissions)"),
        (_workflow({"uses": "openai/codex-action@v1", "with": {"permission-profile": ":workspace"}}),
         _workflow({"uses": "openai/codex-action@v1", "with": {"permission-profile": ":danger-full-access"}}),
         "runs without a sandbox (danger-full-access)"),
    ],
    ids=["skip-flag", "mode-flag", "gate", "bots", "codex-sandbox", "codex-unsafe", "codex-args", "codex-users",
         "codex-cli", "words-before-an-expression", "gate-entry-beside-an-expression", "settings-default-mode",
         "settings-top-level-default-mode", "args-settings", "cli-settings", "codex-permission-profile"],
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
        (_workflow({"run": "claude -p --dangerously-skip-permissions 'x'"}),
         _workflow(_agent("--dangerously-skip-permissions"))),
        # narrowed
        (_workflow(_agent("--dangerously-skip-permissions")), _workflow(_agent('--allowedTools "Read"'))),
        (_workflow(_agent(allowed_non_write_users="*")), _workflow(_agent(allowed_non_write_users="octocat"))),
        # text an expression can reach is never read for a rule
        (_workflow(_agent()), _workflow(_agent("${{ inputs.extra }} --dangerously-skip-permissions"))),
        (_workflow(_agent()), _workflow(_agent("--dangerously-skip-permissions${{ inputs.extra }}"))),
        # a quote still open at the expression, which its substituted text may close
        (_workflow(_agent()),
         _workflow(_agent('--append-system-prompt "never pass --dangerously-skip-permissions ${{ inputs.p }}'))),
        (_workflow(_agent()), _workflow(_agent(allowed_non_write_users="*${{ vars.USERS }}"))),
        (_workflow({"uses": "openai/codex-action@v1"}),
         _workflow({"uses": "openai/codex-action@v1", "with": {"sandbox": "${{ vars.SANDBOX }}"}})),
        # widened by a tool rule, which is #824's to rate
        (_workflow(_agent('--allowedTools "Read"')), _workflow(_agent('--allowedTools "Bash(*)"'))),
        (_workflow({"run": "claude -p --permission-mode default 'x'"}),
         _workflow({"run": "claude -p --permission-mode acceptEdits 'x'"})),
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
    ],
    ids=["respelled", "moved-to-action", "narrowed", "gate-closed", "after-an-expression", "touching-an-expression",
         "quoted-past-an-expression", "gate-entry-holding-an-expression", "mode-expression", "tool-rule",
         "accept-edits", "flag-to-settings", "settings-expression", "settings-path", "settings-accept-edits",
         "codex-workspace-profile"],
)
def test_any_other_edit_is_changed(before, after):
    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)


# --- codex exec's full-access sandbox, however the CLI reads it (#823 review cycle 3) ---

CODEX_WORKSPACE = _workflow({"run": "codex exec -s workspace-write 'review'"})


@pytest.mark.parametrize(
    "run",
    [
        "codex exec -s danger-full-access 'review'",
        "codex exec --sandbox=danger-full-access 'review'",
        # clap reads a short option's attached value, with or without `=`.
        "codex exec -sdanger-full-access 'review'",
        "codex exec -s=danger-full-access 'review'",
        # `sandbox_mode` is the setting --sandbox sets, in each way -c is written.
        "codex exec -c sandbox_mode=\"danger-full-access\" 'review'",
        "codex exec -c 'sandbox_mode=\"danger-full-access\"' 'review'",
        "codex exec --config=sandbox_mode=danger-full-access 'review'",
        "codex exec -csandbox_mode=danger-full-access 'review'",
        "codex exec -c=sandbox_mode=danger-full-access 'review'",
        "codex exec -c ' sandbox_mode = \"danger-full-access\" ' 'review'",
        # The built-in full-access profile, which is what the action's
        # `permission-profile: :danger-full-access` passes the CLI.
        "codex exec -c default_permissions=\":danger-full-access\" 'review'",
        "codex exec --config 'default_permissions=\":danger-full-access\"' 'review'",
        # The last override of a key counts; a profile override outranks a sandbox one.
        "codex exec -c sandbox_mode=read-only -c sandbox_mode=danger-full-access 'review'",
        "codex exec -c sandbox_mode=read-only -c default_permissions=:danger-full-access 'review'",
    ],
    ids=["short", "long-attached", "short-attached", "short-equals", "config", "config-quoted", "config-attached",
         "c-attached", "c-equals", "config-spaced", "profile", "profile-quoted", "last-override",
         "profile-over-sandbox-mode"],
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
        "codex exec -s workspace-write -c sandbox_mode=danger-full-access 'review'",
        "codex exec -sworkspace-write -c default_permissions=:danger-full-access 'review'",
        # The last override counts, and a profile override outranks sandbox_mode.
        "codex exec -c sandbox_mode=danger-full-access -c sandbox_mode=read-only 'review'",
        "codex exec -c sandbox_mode=danger-full-access -c default_permissions=:workspace 'review'",
        # A key under another table, or another value, is not the setting.
        "codex exec -c profiles.ci.sandbox_mode=danger-full-access 'review'",
        "codex exec -c default_permissions=danger-full-access 'review'",
        "codex exec -sread-only 'review'",
    ],
    ids=["sandbox-flag-wins", "attached-sandbox-flag-wins", "last-override", "profile-over-sandbox-mode",
         "profile-scoped-key", "custom-profile-name", "read-only"],
)
def test_a_codex_exec_sandbox_the_cli_does_not_select_is_changed(run):
    after = _workflow({"run": run})

    assert host_grant_expansion_signals(_changes(CODEX_WORKSPACE, after)) == []
    row, = _rows(CODEX_WORKSPACE, after)
    assert (row.direction, row.expands) == ("changed", False)


def test_attached_short_values_publish_under_the_primary_spelling():
    launch, = _launches(_workflow({"run": "codex exec -sdanger-full-access -c=model=o3 -pci 'review'"}))

    assert launch["settings"] == [
        {"name": "--config", "value": "model=o3", "unresolved_reason": None},
        {"name": "--profile", "value": "ci", "unresolved_reason": None},
        {"name": "--sandbox", "value": "danger-full-access", "unresolved_reason": None},
    ]
    assert launch["widening_rules"] == [{"rule": "danger_full_access", "setting": "--sandbox"}]
    # One setting, two spellings: respelling it is quiet.
    assert _rows(
        _workflow({"run": "codex exec -s danger-full-access 'review'"}),
        _workflow({"run": "codex exec -sdanger-full-access 'review'"}),
    ) == []
    # One rule, two spellings: moving between the flag and the override is not a widening.
    before = _workflow({"run": "codex exec -s danger-full-access 'review'"})
    after = _workflow({"run": "codex exec -c sandbox_mode=danger-full-access 'review'"})
    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)


@pytest.mark.parametrize(
    ("codex_args", "direction"),
    [
        # The action appends its own --sandbox, or its own default_permissions
        # override for a permission-profile, after codex-args, and either
        # takes precedence over a sandbox --config override written before it.
        ("-c sandbox_mode=danger-full-access", "changed"),
        ('--config=default_permissions=":danger-full-access"', "changed"),
        # A --sandbox word is read as the flag it is, attached or not, as before.
        ("-sdanger-full-access", "widened"),
    ],
    ids=["sandbox-mode-override", "profile-override", "attached-short-sandbox"],
)
def test_codex_args_sandbox_overrides_are_read_as_the_action_passes_them(codex_args, direction):
    before = _workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": "--json"}})
    after = _workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": codex_args}})

    row, = _rows(before, after)
    assert row.direction == direction


def test_a_rule_gained_where_the_job_launched_the_agent_only_unread_before_is_not_claimed():
    before = _workflow({"run": "npm ci && claude -p --dangerously-skip-permissions 'review'"})
    after = _workflow({"run": "claude -p --dangerously-skip-permissions 'review'"})

    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)
    assert (
        "an agent launch now skips permission checks (bypassPermissions) (review/steps[0]), which is not "
        "counted as a widening: before, this job launched the agent in a form this audit does not read"
    ) in row.why


def test_a_rule_gained_by_a_launch_read_on_both_sides_widens_beside_an_unread_one():
    unread = {"run": "npm ci && claude -p 'x'"}
    before = _workflow(unread, _agent())
    after = _workflow(unread, _agent("--dangerously-skip-permissions"))

    assert host_grant_expansion_signals(_changes(before, after)) == [f"workflow_agent_widened_changed: {SOURCE}"]
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("widened", True)


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
    ],
    ids=["step-moved", "renamed-and-edited", "swapped", "gate-moved"],
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
    ],
    ids=["second-job", "narrowed-there-widened-here"],
)
def test_a_rule_another_job_gains_while_no_launch_left_is_a_widening(before, after):
    assert host_grant_expansion_signals(_changes(before, after)) == [f"workflow_agent_widened_changed: {SOURCE}"]
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("widened", True)
    assert "an agent launch now skips permission checks (bypassPermissions) (review/agent)" in row.why


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


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("--dangerously-skip-permissions --model ${{ vars.M }}", ("--dangerously-skip-permissions", "--model")),
        ("--model ${{ vars.M }} --dangerously-skip-permissions", ("--model",)),
        # the word the expression touches
        ("--dangerously-skip-permissions${{ vars.X }}", ()),
        ("--permission-mode=${{ vars.MODE }}", ()),
        # a quoted run open at the expression, balanced or not in the literal text
        ('--append-system-prompt "--dangerously-skip-permissions ${{ vars.X }}"', ("--append-system-prompt",)),
        ('--append-system-prompt "a --dangerously-skip-permissions ${{ vars.X }}', ("--append-system-prompt",)),
        # a whole line before it, and a comment line the action drops whatever it holds
        ("--dangerously-skip-permissions\n# model: ${{ vars.M }}", ("--dangerously-skip-permissions",)),
        ("# don't\n--dangerously-skip-permissions ${{ vars.X }}", ("--dangerously-skip-permissions",)),
        # an unquoted `#` ends the input, so what follows it reaches nothing
        ("--dangerously-skip-permissions # ${{ vars.X }}", ("--dangerously-skip-permissions",)),
    ],
)
def test_claude_args_rules_are_read_only_from_words_an_expression_cannot_reach(value, words):
    """#823 review F4: GitHub substitutes the expression before the action splits the input."""

    assert _literal_argument_words("claude", value) == words


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("--yolo ${{ vars.X }}", ("--yolo",)),
        ("${{ vars.X }} --yolo", ()),
        ('--yolo "a ${{ vars.X }}', ("--yolo",)),
        ('["--yolo", "${{ vars.X }}"]', ("--yolo",)),
        ('["${{ vars.X }}", "--yolo"]', ()),
        # outside a JSON string, the substituted text decides whether the array parses
        ('["--yolo", ${{ vars.X }}]', None),
    ],
)
def test_codex_args_rules_are_read_only_from_words_an_expression_cannot_reach(value, words):
    assert _literal_argument_words("codex", value) == words


def test_a_setting_holding_an_expression_is_marked_and_the_row_says_what_it_leaves_unread():
    """#823 review F4: the row no longer reads as though no documented rule was gained."""

    before = _workflow(_agent("--model ${{ vars.CLAUDE_MODEL }}\n--allowedTools Read"))
    after = _workflow(_agent("--model ${{ vars.CLAUDE_MODEL }}\n--dangerously-skip-permissions"))

    launch, = _launches(after)
    setting, = launch["settings"]
    assert setting["holds_expression"] is True
    assert "widening_rules" not in launch
    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)
    assert (
        "an agent launch setting holds a `${{ }}` expression (claude_args at review/steps[0]), which GitHub "
        "substitutes before the action reads it; documented widening rules are read only from the literal "
        "text the expression cannot reach, so this row does not say whether the text it reaches meets one"
    ) in row.why
    # A setting without one says nothing of the kind, and the key is omitted.
    plain, = _launches(_workflow(_agent()))
    assert "holds_expression" not in plain["settings"][0]


@pytest.mark.parametrize(
    ("before", "after", "rule", "setting"),
    [
        (_workflow(_agent(allowed_non_write_users="${{ vars.EXTRA_USERS }}")),
         _workflow(_agent(allowed_non_write_users="${{ vars.EXTRA_USERS }}, *")),
         "accepts runs triggered by any user (allowed_non_write_users: *)", "allowed_non_write_users"),
        (_workflow(_agent("--allowedTools Read --model ${{ vars.CLAUDE_MODEL }}")),
         _workflow(_agent("--dangerously-skip-permissions --model ${{ vars.CLAUDE_MODEL }}")),
         "skips permission checks (bypassPermissions)", "claude_args"),
        (_workflow(_agent("--model ${{ vars.M }} --dangerously-skip-permissions")),
         _workflow(_agent("--model opus --dangerously-skip-permissions")),
         "skips permission checks (bypassPermissions)", "claude_args"),
        # the settings input is read for the rule only when it holds no expression (#823 review C2-F2)
        (_workflow(_agent(settings='{"permissions":{"defaultMode":"${{ vars.MODE }}"}}')),
         _workflow(_agent(settings='{"permissions":{"defaultMode":"bypassPermissions"}}')),
         "skips permission checks (bypassPermissions)", "settings"),
    ],
    ids=["gate", "claude-args", "expression-replaced", "settings"],
)
def test_a_rule_gained_where_the_setting_held_an_expression_before_is_named_and_not_claimed(
    before, after, rule, setting
):
    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)
    assert (
        f"an agent launch now {rule} (review/steps[0]), which is not counted as a widening: before, this "
        f"job's {setting} held a `${{{{ }}}}` expression, whose substituted text this audit does not read"
    ) in row.why


def test_replacing_an_expression_beside_a_rule_the_launch_already_met_gains_nothing():
    before = _workflow(_agent("--dangerously-skip-permissions --model ${{ vars.CLAUDE_MODEL }}"))
    after = _workflow(_agent("--dangerously-skip-permissions --model opus"))

    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)
    assert "an agent launch now" not in row.why


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


def test_a_run_that_mentions_claude_in_an_echo_is_no_row():
    before = _workflow({"run": "echo done"})
    after = _workflow({"run": 'echo "claude -p --dangerously-skip-permissions"'})
    assert _rows(before, after) == []


def test_a_pull_request_workflow_with_a_default_checkout_claims_no_pull_request_code():
    row, = _rows(_reproduction(), _reproduction(pr="write"))

    assert "checkout" not in row.why
    assert "untrusted-input" not in row.why
    assert row.why.endswith(
        "an agent runs at review/steps[1] (anthropics/claude-code-action) beside the write scope pull-requests"
    )


@pytest.mark.parametrize(
    "step",
    [
        {"run": "./scripts/claude-review.sh --dangerously-skip-permissions"},
        {"uses": "./.github/actions/claude-review", "with": {"claude_args": "--dangerously-skip-permissions"}},
        {"run": "npx @anthropic-ai/claude-code -p --dangerously-skip-permissions 'go'"},
        {"run": "timeout 600 claude -p --dangerously-skip-permissions 'go'"},
    ],
    ids=["script", "composite", "npx", "wrapped"],
)
def test_an_unread_surface_is_neither_a_launch_nor_a_row(step):
    assert _launches(_workflow(step)) == []
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


def _digest(text):
    """What one withheld string publishes: a digest of what the host readers digest for it."""

    from agents_shipgate.core.host_grants import redacted_config_sha256

    return f"<withheld:{redacted_config_sha256(text)[:12]}>"


def test_a_json_value_publishes_its_shape_and_none_of_its_free_text():
    """#823 review C2-F1: every string a host reader does not publish is withheld, and still compared.

    The same server in `.mcp.json` publishes `remote (command name npx)`, and
    the same hook in `.claude/settings.json` publishes `Stop`; neither
    publishes an argument or a command.
    """

    grant = _grant(_workflow(
        _agent(f"--allowedTools Read --mcp-config '{REMOTE_MCP_JSON}'", settings=HOOK_JSON),
        {"run": f"codex exec -c '{REMOTE_CODEX_CONFIG}' 'go'"},
    ))
    action, cli = grant["agent_launches"]
    args = ["mcp-remote", "https://mcp.example.com/sse", "--header", "Authorization: Bearer tokCANARY0123456789abcdef"]
    server = json.dumps(
        {"mcpServers": {"remote": {"args": [_digest(arg) for arg in args], "command": "npx"}}},
        separators=(",", ":"),
    )
    hook = (
        '{"hooks":{"Stop":[{"hooks":[{"command":"'
        + _digest('curl -H "X-Auth-Token: hookCANARY77" https://hooks.example.com/notify')
        + '","type":"' + _digest("command") + '"}]}]}}'
    )
    assert {item["name"]: item["value"] for item in action["settings"]} == {
        "claude_args": f"--allowedTools Read --mcp-config '{server}'",
        "settings": hook,
    }
    codex_args = ["mcp-remote", "https://mcp.example.com/sse", "--header", "Authorization: Bearer tokCANARY-codex-0123456789"]
    assert cli["settings"] == [{
        "name": "--config",
        "value": 'mcp_servers.remote={"args":[' + ",".join(f'"{_digest(arg)}"' for arg in codex_args)
                 + '],"command":"npx"}',
        "unresolved_reason": None,
    }]
    text = json.dumps(grant)
    for canary in SHAPE_CANARIES:
        assert canary not in text
    # Nothing was redacted, so no limit is named: nothing of the text is published to redact.
    assert uncompared_agent_launch_texts(grant) == []

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
    action, cli = grant["agent_launches"]

    assert {item["name"]: item["value"] for item in action["settings"]} == {
        "claude_args": f"--mcp-config '{MCP_PUBLISHED}' --allowedTools Read",
        "mcp_config": MCP_PUBLISHED,
        "settings": SETTINGS_PUBLISHED,
    }
    assert {item["name"]: item["value"] for item in cli["settings"]} == {
        "--mcp-config": f"'{MCP_PUBLISHED}'",
        "--settings": SETTINGS_PUBLISHED,
    }
    text = json.dumps(grant)
    for canary in JSON_CANARIES:
        assert canary not in text
        assert hashlib.sha256(canary.encode()).hexdigest() not in text
    assert uncompared_agent_launch_texts(grant) == [] and _uncompared_workflow_text(grant) is None


def test_a_value_attached_to_its_flag_is_withheld_as_a_separate_word_is():
    """#823 review F1: `--settings=…`, `--mcp-config=…` and codex's `-c<value>` published verbatim."""

    grant = _grant(_workflow(
        _agent(f"--allowedTools Read --settings='{SETTINGS_JSON}' --mcp-config='{MCP_JSON}'"),
        {"uses": "openai/codex-action@v1", "with": {
            "codex-args": '-cmcp_servers.db.env.REGION="canary-short" -c=mcp_servers.x.env.T=canary-eq --json',
        }},
    ))
    claude, codex = grant["agent_launches"]

    assert claude["settings"] == [{
        "name": "claude_args",
        "value": f"--allowedTools Read '--settings={SETTINGS_PUBLISHED}' '--mcp-config={MCP_PUBLISHED}'",
        "unresolved_reason": None,
    }]
    assert codex["settings"] == [{
        "name": "codex-args",
        "value": "'-cmcp_servers.db.env.REGION=<redacted>' '-c=mcp_servers.x.env.T=<redacted>' --json",
        "unresolved_reason": None,
    }]
    text = json.dumps(grant)
    for canary in (*JSON_CANARIES, "canary-short", "canary-eq"):
        assert canary not in text
    # Each spelling compares as the host readers compare it: rotating an env value is quiet.
    rotated = SETTINGS_JSON.replace("hunter2-canary", "rotated")
    assert _rows(
        _workflow(_agent(f"--settings='{SETTINGS_JSON}'")), _workflow(_agent(f"--settings='{rotated}'"))
    ) == []


def test_a_withheld_json_value_compares_as_the_host_readers_compare_it():
    def settings(env):
        return _workflow(_agent(settings=json.dumps({"env": env})))

    # An env value is not compared, as in `.claude/settings.json`; an added key is.
    assert _rows(settings({"DB": "one"}), settings({"DB": "two"})) == []
    row, = _rows(settings({"DB": "one"}), settings({"DB": "one", "EXTRA": "three"}))
    assert '"EXTRA":"<redacted>"' in row.after
    assert "three" not in row.after


def test_a_codex_config_override_withholds_env_header_and_secret_values():
    run = (
        "codex exec -c 'mcp_servers.db.env.TOKEN=\"canary-cfg\"' "
        "-c 'mcp_servers.gh={command=\"gh\", env={GH_TOKEN=\"canary-inline\"}}' -c model=o3 'go'"
    )
    action_args = '["-c", "mcp_servers.db.env.TOKEN=\\"canary-array\\"", "--yolo"]'
    grant = _grant(_workflow(
        {"run": run}, {"uses": "openai/codex-action@v1", "with": {"codex-args": action_args}},
    ))
    cli, action = grant["agent_launches"]

    assert cli["settings"] == [
        {"name": "--config", "value": "mcp_servers.db.env.TOKEN=<redacted>", "unresolved_reason": None},
        {"name": "--config", "value": 'mcp_servers.gh={"command":"gh","env":{"GH_TOKEN":"<redacted>"}}',
         "unresolved_reason": None},
        {"name": "--config", "value": "model=o3", "unresolved_reason": None},
    ]
    assert action["settings"] == [{
        "name": "codex-args", "value": '["-c","mcp_servers.db.env.TOKEN=<redacted>","--yolo"]',
        "unresolved_reason": None,
    }]
    assert action["widening_rules"] == [{"rule": "bypass_approvals_and_sandbox", "setting": "codex-args"}]
    assert "canary" not in json.dumps(grant)


def test_a_codex_config_table_that_does_not_parse_is_withheld_and_named():
    """#823 review (P3): string-argv keeps the quotes of `--config='k={…}'`, so it does not parse as TOML."""

    codex_args = "--config='mcp_servers.db={command=\"x\", env={T=\"canary-quoted\"}}' --json"
    grant = _grant(_workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": codex_args}}))
    launch, = grant["agent_launches"]

    assert launch["settings"] == [{"name": "codex-args", "value": None, "unresolved_reason": "unparsed_json"}]
    assert "canary-quoted" not in json.dumps(grant)
    limit, = uncompared_agent_launch_texts(grant)
    assert "a codex `--config` table or array, and does not parse" in limit


def test_text_that_starts_like_json_and_does_not_parse_is_withheld_and_named():
    # shell-quote strips the double quotes of an unquoted JSON word, so the
    # action reads it as a path; its values cannot be told from its keys.
    value = '--mcp-config {"mcpServers":{"db":{"env":{"T":"canary-unquoted"}}}} --dangerously-skip-permissions'
    grant = _grant(_workflow(_agent(value)))
    launch, = grant["agent_launches"]

    assert launch["settings"] == [{"name": "claude_args", "value": None, "unresolved_reason": "unparsed_json"}]
    # The rule is read from the declared text, so withholding it hides no rule.
    assert launch["widening_rules"] == [{"rule": "bypass_permissions", "setting": "claude_args"}]
    assert "canary-unquoted" not in json.dumps(grant)
    limit, = uncompared_agent_launch_texts(grant)
    assert limit.startswith("the claude_args value of the agent launch at review/steps[0] (anthropics/claude-code-action)")
    assert "starts like JSON, or a codex `--config` table or array, and does not parse" in limit
    assert _uncompared_workflow_text(grant) is None


def test_a_url_path_is_withheld_while_the_rest_of_the_setting_and_a_rule_beside_it_are_read():
    before = _workflow(_agent("--append-system-prompt 'Follow https://example.com/style-guide' --allowedTools Read"))
    after = _workflow(_agent(
        "--append-system-prompt 'Follow https://example.com/style-guide' --dangerously-skip-permissions"
    ))

    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("widened", True)
    assert (
        "claude_args: --append-system-prompt 'Follow https://example.com/<redacted-path>' "
        "--dangerously-skip-permissions"
    ) in row.after
    assert "style-guide" not in row.before + row.after
    assert uncompared_agent_launch_texts(_grant(after)) == []
    assert _uncompared_workflow_text(_grant(after)) is None


def test_a_quoted_url_in_a_json_array_codex_args_element_keeps_the_array_valid():
    """#823 review cycle 3: withholding the URL keeps the element's escaped closing quote."""

    codex_args = json.dumps(["-c", 'mcp_servers.x.url="https://h2.example.com/p?token=canary-q"', "--json"])
    launch, = _launches(_workflow({"uses": "openai/codex-action@v1", "with": {"codex-args": codex_args}}))
    setting, = launch["settings"]

    assert json.loads(setting["value"]) == [
        "-c", 'mcp_servers.x.url="https://h2.example.com/<redacted-path>"', "--json",
    ]
    assert setting["unresolved_reason"] is None
    assert "canary-q" not in json.dumps(launch)


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
        _agent("--append-system-prompt 'use token=ARGCANARY' --dangerously-skip-permissions",
               plugin_marketplaces="https://robot:PWCANARY@github.com/org/repo.git"),
        {"uses": "actions/checkout@v4", "with": {"ref": "token=REFCANARY"}},
        {"run": "claude -p --settings ghp_" + "A" * 36 + " 'review token=SECRETCANARY'"},
    ))
    action, cli = grant["agent_launches"]

    assert action["settings"] == [
        {"name": "claude_args",
         "value": "--append-system-prompt 'use token=<redacted>' --dangerously-skip-permissions",
         "unresolved_reason": "redacted"},
        {"name": "plugin_marketplaces", "value": "https://github.com/<redacted-path>", "unresolved_reason": "redacted"},
    ]
    # The rule is read from the declared text, so redaction does not hide it.
    assert action["widening_rules"] == [{"rule": "bypass_permissions", "setting": "claude_args"}]
    assert cli["settings"] == [
        {"name": "--settings", "value": "[REDACTED:github_token]", "unresolved_reason": "redacted"},
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
            ("--settings", "review/steps[2] (claude)"),
        )
    ]
    assert _uncompared_workflow_text(grant) == (
        "a checkout ref contains credential-shaped text; it is published redacted and cannot be compared"
    )


#: Prose the #802 label redaction rewrites, as security-review prompts write it:
#: ``claude_args``, and a ``run:`` whose prompt is a variadic flag's value.
PROSE = [
    ('--append-system-prompt "Never print bearer tokens in review comments" --allowedTools Read',
     "claude -p --allowedTools Read 'Never print bearer tokens in review comments'"),
    ('--append-system-prompt "Flag Authorization: headers logged in plain text" --allowedTools Read',
     "claude -p --allowedTools Read 'Flag Authorization: headers logged in plain text'"),
    ('--allowedTools "Bash(curl -H Authorization:*)"',
     "claude -p --allowedTools 'Bash(curl -H Authorization:*)' -- 'go'"),
    ('--append-system-prompt "check the secret=... assignment" --allowedTools Read',
     "claude -p --allowedTools Read 'check the secret=... assignment'"),
]


@pytest.mark.parametrize(("prose", "run"), PROSE, ids=["bearer", "authorization", "tool-rule", "assignment"])
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
        {"name": "Run ghp_" + "C" * 36, "run": "claude -p 'go'"},
    ]}}))

    text = json.dumps({key: grant[key] for key in ("agent_launches", "checkout_refs")})
    assert "ghp_" not in text and "hunter2" not in text
    assert grant["checkout_refs"][0]["step"] == "Pull docker://<redacted>@gcr.io/x"
    assert grant["agent_launches"][0]["job"] == "[REDACTED:github_token]"


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
    for name, payload in (("inventory", inventory), ("baseline", baseline)):
        schema = json.loads((ROOT / f"docs/host-grants-{name}-schema.v0.7.json").read_text())
        Draft202012Validator(schema).validate(payload)

    path.write_text(_yaml(_reproduction(claude_args="--dangerously-skip-permissions", ref=HEAD_SHA)))
    drift = build_host_drift_payload(baseline=baseline, inventory=host_audit_inventory(tmp_path), baseline_file="b.json")
    assert (drift["comparison_status"], drift["has_drift"]) == ("comparable", True)
    assert drift["expansion_signals"] == [f"workflow_agent_widened_changed: {SOURCE}"]
    schema = json.loads((ROOT / "docs/host-grants-drift-schema.v0.7.json").read_text())
    Draft202012Validator(schema).validate(drift)


def test_an_unresolved_launch_is_a_non_blocking_limit_that_leaves_coverage_complete(tmp_path):
    from agents_shipgate.cli.host_audit import host_audit_inventory

    _write(tmp_path, {SOURCE: _yaml(_workflow({"run": "npm ci && claude -p 'go'"}))})
    inventory = host_audit_inventory(tmp_path)

    github, = [item for item in inventory["host_coverage"] if item["host"] == "github"]
    assert github["status"] == "complete"
    issue, = [item for item in inventory["issues"] if item["host"] == "github"]
    assert (issue["kind"], issue["blocking"]) == ("unsupported", False)
    assert "review/steps[0] (claude)" in issue["message"]


# --- the same row on every route ---------------------------------------------------


@pytest.fixture
def pr(tmp_path):
    repo = _repo(tmp_path, {SOURCE: _yaml(_reproduction())})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(_reproduction(claude_args='--permission-mode bypassPermissions --allowedTools "Bash(*)"'))})
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "bypass permissions")
    return repo


def _assert_the_row(row: dict) -> None:
    assert row["subject"] == f"github {SOURCE}"
    assert 'review/steps[1]: runs anthropics/claude-code-action with claude_args: --allowedTools "Read"' in row["before"]
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
    """The #802 sweep for agent launches, JSON-shaped canaries included (#823 review F3)."""

    canary = "sk-ant-api03-" + "Z" * 40
    job = "ghp_" + "D" * 36
    mcp = json.dumps({"mcpServers": {"db": {
        "command": "db-mcp", "env": {"DB_API_TOKEN": "canary-tok-123"},
        "headers": {"X-API-Key": "canary-hdr-456", "Authorization": f"Bearer {canary}"},
    }}})
    codex_args = (
        "-c 'mcp_servers.db.env.TOKEN=\"canary-cfg-789\"' -cmcp_servers.db.env.REGION=canary-short-c "
        "-c=mcp_servers.db.env.ZONE=canary-eq-c --full-auto"
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
            f"--mcp-config '{mcp}' --dangerously-skip-permissions",
            settings=SETTINGS_JSON, mcp_config=mcp,
            plugin_marketplaces="https://github.com/canary-org/canary-repo.git",
        ),
        _agent(f"--allowedTools Read --settings='{equals}' --mcp-config='{equals_mcp}'"),
        {"run": f"ANTHROPIC_API_KEY={canary} claude -p --allowedTools Read --mcp-config '{mcp}' 'go'"},
        {"uses": "openai/codex-action@v1", "with": {"codex-args": codex_args}},
        # #823 review C2-F1: an MCP server's arguments and a hook's command,
        # which the host readers never publish, in every spelling.
        _agent(f"--allowedTools Read\n--mcp-config '{REMOTE_MCP_JSON}'", settings=HOOK_JSON),
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
        *SHAPE_CANARIES,
    ))
    assert "runs claude -p with --allowedTools Read" in joined
    assert "mcp_servers.db.env.TOKEN=<redacted>" in joined
    assert '"command":"npx"' in joined and '"Stop":[{"hooks":[{"command":"<withheld:' in joined


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
    head = _workflow(_agent("--append-system-prompt 'use token=ARGCANARY' --dangerously-skip-permissions"))
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head)})
    _git(repo, "commit", "-qam", "credential-shaped setting")

    payload = _diff(repo)
    assert payload["comparison_status"] == "comparable"
    row, = payload["rows"]
    assert (row["direction"], row["expands"]) == ("widened", True)
    assert "--append-system-prompt 'use token=<redacted>' --dangerously-skip-permissions" in row["after"]
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
