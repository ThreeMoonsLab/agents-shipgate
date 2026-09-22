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

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
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
        (_workflow(_agent()), _workflow(_agent(allowed_bots="*")),
         "accepts runs triggered by any user (allowed_bots: *)"),
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
    ],
    ids=["skip-flag", "mode-flag", "gate", "bots", "codex-sandbox", "codex-unsafe", "codex-args", "codex-users",
         "codex-cli"],
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
        # an expression is text, never a rule
        (_workflow(_agent()), _workflow(_agent("--dangerously-skip-permissions ${{ inputs.extra }}"))),
        (_workflow(_agent()), _workflow(_agent(allowed_non_write_users="${{ vars.USERS }}, *"))),
        # widened by a tool rule, which is #824's to rate
        (_workflow(_agent('--allowedTools "Read"')), _workflow(_agent('--allowedTools "Bash(*)"'))),
        (_workflow({"run": "claude -p --permission-mode default 'x'"}),
         _workflow({"run": "claude -p --permission-mode acceptEdits 'x'"})),
    ],
    ids=["respelled", "moved-to-action", "narrowed", "gate-closed", "expression", "gate-expression", "tool-rule",
         "accept-edits"],
)
def test_any_other_edit_is_changed(before, after):
    assert host_grant_expansion_signals(_changes(before, after)) == []
    row, = _rows(before, after)
    assert (row.direction, row.expands) == ("changed", False)


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


def test_a_credential_in_an_input_value_is_neither_published_nor_compared():
    value = '--mcp-config \'{"headers":{"Authorization":"Bearer sk-CANARYTOKEN123"}}\''
    grant = _grant(_workflow(_agent(value)))
    launch, = grant["agent_launches"]

    assert launch["settings"] == [{"name": "claude_args", "value": None, "unresolved_reason": "redacted"}]
    assert "CANARY" not in json.dumps(grant)
    limit, = uncompared_agent_launch_texts(grant)
    assert "claude_args value of the agent launch at review/steps[0]" in limit
    assert "credential-shaped" in limit


def test_a_token_in_a_run_line_is_never_published():
    run = "claude -p --settings ghp_" + "A" * 36 + " 'review token=SECRETCANARY'"
    grant = _grant(_workflow({"run": run}))
    launch, = grant["agent_launches"]

    assert launch["settings"] == [{"name": "--settings", "value": None, "unresolved_reason": "redacted"}]
    text = json.dumps(grant)
    assert "ghp_" not in text and "SECRETCANARY" not in text


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


def test_no_canary_reaches_any_published_output(tmp_path):
    canary = "sk-ant-api03-" + "Z" * 40
    job = "ghp_" + "D" * 36
    base = _workflow(jobs={job: {"steps": [_agent()]}})
    head = _workflow(jobs={job: {"steps": [
        {"name": "Pull docker://ci:" + "p4ssCANARY" + "@gcr.io/x", "uses": "actions/checkout@v4",
         "with": {"ref": "token=REFCANARY"}},
        _agent(f"--mcp-config '{{\"headers\":{{\"Authorization\":\"Bearer {canary}\"}}}}'"),
        {"run": f"ANTHROPIC_API_KEY={canary} claude -p --allowedTools Read 'go'"},
    ]}})
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head)})
    _git(repo, "commit", "-qam", "canaries")

    outputs = [json.dumps(_diff(repo))]
    for args in (
        ["diff", "--workspace", str(repo), "--base", "main"],
        ["audit", "--host", "--workspace", str(repo), "--json"],
        ["check", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json"],
        ["verify", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "text"],
    ):
        result = CliRunner().invoke(app, args)
        outputs.append(result.output)
    outputs.append((repo / "agents-shipgate-reports/pr-comment.md").read_text())
    outputs.append((repo / "agents-shipgate-reports/verifier.json").read_text())
    joined = "\n".join(outputs)

    for secret in (canary, "p4ssCANARY", "REFCANARY", job):
        assert secret not in joined
    assert "runs claude -p with --allowedTools Read" in joined
