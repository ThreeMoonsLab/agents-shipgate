"""#1011: a workflow token widened outside every agent job is not the agent's.

A workflow whose agent job (`analyze`, read-only) gains a sibling non-agent job
(`publish`, `issues: write`) is still a widened workflow token: the row keeps
`expands`, `direction`, severity, the drift signal and every gate. What changes
is the attribution. `diff`'s summary counted it as "widening what the agent may
do" while the agent job's token was byte-for-byte unchanged.

One engine predicate (`workflow_widening_outside_agent_jobs`) decides it, and
fails closed: the change stays the agent's unless every gained write scope is in
a job where this audit finds no agent launch, read or unread, no reusable
workflow call and no unreadable steps, every job where it finds one kept its
token, and the row carries no expansion signal but the token's. The row's `why`
names the gained scopes and the jobs that kept their tokens, and holds
:data:`OUTSIDE_AGENT_JOBS` as a whole `; ` clause, which `diff`'s summary and
the Stop hook read.
"""

from __future__ import annotations

import json

import pytest
import yaml

from agents_shipgate.core.capability_diff_rows import (
    DIRECTION_UNKNOWN,
    OUTSIDE_AGENT_JOBS,
    widens_outside_agent_jobs,
)
from agents_shipgate.core.host_grants import (
    _workflow_grant,
    workflow_widening_outside_agent_jobs,
)
from tests.test_actions_token_scope import (
    WORKFLOWS,
    _diff,
    _drift_signals,
    _invoke,
    _output,
    _pr,
    _row,
    _rows,
    _signals,
)

SOURCE = f"{WORKFLOWS}/agent.yml"

#: The issue's base workflow, as written in it.
BASE = """name: Agent review
on: issues
permissions: {}
jobs:
  analyze:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: read
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: Summarize the issue
"""

#: The issue's head: only the sibling job is appended.
PUBLISH = """  publish:
    runs-on: ubuntu-latest
    permissions:
      issues: write
    steps:
      - run: echo non-agent publisher
"""
HEAD = BASE + PUBLISH

EXPECTED_ROW = {
    "subject": f"github {SOURCE}",
    "direction": "widened",
    "expands": True,
    "severity": "high",
    "before": "access: read, analyze: contents: read, analyze: issues: read, on: issues",
    "after": (
        "access: write, analyze: contents: read, analyze: issues: read, "
        "publish: issues: write, on: issues"
    ),
    "why": (
        "grants write permissions to workflow jobs; "
        "publish gained the write scope issues, and this audit finds no agent launch or reusable "
        "workflow call in that job; "
        "analyze (agent launch) kept its token; "
        f"{OUTSIDE_AGENT_JOBS}; "
        "an agent runs at analyze/steps[0] (anthropics/claude-code-action) beside the "
        "untrusted-input trigger issues and the secret ANTHROPIC_API_KEY"
    ),
}

OUTSIDE_SUMMARY = "1 change(s), 1 widening a workflow job's token outside any agent job (⚠)."
AGENT_SUMMARY = "1 change(s), 1 widening what the agent may do (⚠)."


def _row_for(before: str | None, after: str):
    row, = _rows({} if before is None else {"agent.yml": before}, {"agent.yml": after})
    return row


def _outside(row) -> bool:
    return OUTSIDE_AGENT_JOBS in row.why.split("; ")


@pytest.fixture
def issue_pr(tmp_path):
    return _pr(tmp_path, {SOURCE: BASE}, {SOURCE: HEAD})


# --- the issue's reproduction, the same on every route ------------------------------


def test_diff_json_keeps_the_widening_and_names_where_it_is(issue_pr):
    payload = _diff(issue_pr)
    row, = payload["rows"]
    assert _row(row) == EXPECTED_ROW
    # Nothing in the published shape moves: the change is one widening.
    assert payload["review"]["summary"] == {"changes": 1, "rows": 1, "widenings": 1}
    change, = payload["review"]["changes"]
    assert change["expands"] is True and change["why"] == EXPECTED_ROW["why"]


def test_diff_text_labels_it_a_workflow_job_token_widening(issue_pr):
    result = _invoke("diff", "--workspace", str(issue_pr), "--base", "main")
    assert result.exit_code == 0, _output(result)
    text = _output(result)
    assert OUTSIDE_SUMMARY in text
    assert "what the agent may do" not in text
    assert "⚠ high" in text and "widened" in text


def test_the_drift_signal_is_unchanged(issue_pr):
    assert _drift_signals(issue_pr) == [f"workflow_write_changed: {SOURCE}"]


def test_check_and_verify_publish_the_same_row(issue_pr):
    args = ["check", "--workspace", str(issue_pr), "--base", "main", "--head", "HEAD"]
    machine = _invoke(*args, "--format", "agent-boundary-json")
    assert machine.exit_code == 0, _output(machine)
    row, = json.loads(machine.stdout)["rows"]
    assert _row(row) == EXPECTED_ROW

    verify = _invoke(
        "verify", "--workspace", str(issue_pr), "--base", "main", "--head", "HEAD",
        "--format", "text",
    )
    assert verify.exit_code == 0, _output(verify)
    verifier = json.loads((issue_pr / "agents-shipgate-reports/verifier.json").read_text())
    row, = verifier["host_comparison"]["rows"]
    assert _row(row) == EXPECTED_ROW
    assert verifier["host_comparison"]["review"]["summary"]["widenings"] == 1


def test_the_stop_hook_names_it_under_its_own_heading(issue_pr, tmp_path):
    from tests.test_install_hooks import _host_diff_workspace, _run_hook

    hooked = tmp_path / "hooked"
    hooked.mkdir()
    _host_diff_workspace(hooked)
    result = _run_hook(hooked, "verify", {}, diff_payload=json.dumps(_diff(issue_pr)))
    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert "These rows widen a workflow job's token outside any agent job:" in message
    assert "These rows widen what the agent can do" not in message
    assert SOURCE in message and "publish gained the write scope issues" in message


# --- the engine predicate, fail-closed -----------------------------------------------


def test_the_predicate_names_the_gained_scopes_and_the_agent_jobs():
    before = _workflow_grant(yaml.safe_load(BASE), source=SOURCE)
    after = _workflow_grant(yaml.safe_load(HEAD), source=SOURCE)
    found = workflow_widening_outside_agent_jobs(before, after)
    assert found is not None
    assert found.gained == (("publish", ("issues",)),)
    assert found.agent_jobs == (("analyze", "agent launch"),)


def test_a_legacy_grant_without_job_contexts_is_the_agents():
    after = _workflow_grant(yaml.safe_load(HEAD), source=SOURCE)
    legacy = {key: value for key, value in after.items() if key != "permission_contexts"}
    assert workflow_widening_outside_agent_jobs(None, legacy) is None
    assert workflow_widening_outside_agent_jobs(legacy, after) is None


def _agent_job(permissions: str = "      contents: read\n      issues: read\n") -> str:
    lines = [
        "name: Agent review",
        "on: issues",
        "permissions: {}",
        "jobs:",
        "  analyze:",
        "    runs-on: ubuntu-latest",
    ]
    text = "\n".join(lines) + "\n"
    if permissions:
        text += "    permissions:\n" + permissions
    return text + (
        "    steps:\n"
        "      - uses: anthropics/claude-code-action@v1\n"
        "        with:\n"
        "          prompt: Summarize the issue\n"
    )


def test_control_the_agent_job_gaining_a_scope_is_the_agents():
    row = _row_for(BASE, BASE.replace("      issues: read\n", "      issues: write\n"))
    assert (row.direction, row.expands, row.severity) == ("widened", True, "high")
    assert not _outside(row)
    assert "gained" not in row.why


def test_control_the_agent_job_gaining_a_scope_beside_a_publisher_is_the_agents():
    head = HEAD.replace("      issues: read\n", "      issues: write\n", 1)
    row = _row_for(BASE, head)
    assert row.expands and not _outside(row)


def test_control_a_workflow_scope_the_agent_job_inherits_is_the_agents():
    base = _agent_job(permissions="").replace("permissions: {}", "permissions:\n  issues: read")
    head = base.replace("  issues: read", "  issues: write")
    row = _row_for(base, head)
    assert row.expands and not _outside(row)


def test_a_workflow_scope_only_a_non_agent_job_inherits_is_outside():
    """The agent job declares its own permissions, so the workflow default does not reach it."""

    base = BASE.replace("permissions: {}", "permissions:\n  issues: read") + (
        "  publish:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo publish\n"
    )
    head = base.replace("  issues: read\njobs", "  issues: write\njobs")
    row = _row_for(base, head)
    assert row.expands and _outside(row)
    assert "publish gained the write scope issues" in row.why
    assert "analyze (agent launch) kept its token" in row.why


def test_control_a_job_with_an_unread_agent_run_is_the_agents():
    unread = (
        "  triage:\n    runs-on: ubuntu-latest\n    permissions:\n      issues: {level}\n"
        "    steps:\n      - run: claude -p \"$(cat prompt.md)\"\n"
    )
    row = _row_for(BASE + unread.format(level="read"), BASE + unread.format(level="write"))
    assert row.expands and not _outside(row)


def test_an_unchanged_unread_agent_run_job_is_named_as_keeping_its_token():
    unread = (
        "  triage:\n    runs-on: ubuntu-latest\n    permissions:\n      issues: read\n"
        "    steps:\n      - run: claude -p \"$(cat prompt.md)\"\n"
    )
    row = _row_for(BASE + unread, BASE + unread + PUBLISH)
    assert row.expands and _outside(row)
    assert "analyze (agent launch) and triage (unread agent launch) kept their tokens" in row.why


def test_control_a_reusable_call_job_raising_its_ceiling_is_the_agents():
    call = (
        "  shared:\n    permissions:\n      contents: {level}\n"
        "    uses: org/shared/.github/workflows/release.yml@v1\n"
    )
    row = _row_for(BASE + call.format(level="read"), BASE + call.format(level="write"))
    assert row.expands and not _outside(row)


def test_an_unchanged_reusable_call_job_is_named_as_keeping_its_token():
    call = (
        "  shared:\n    permissions:\n      contents: write\n"
        "    uses: org/shared/.github/workflows/release.yml@v1\n"
    )
    row = _row_for(BASE + call, BASE + call + PUBLISH)
    assert row.expands and _outside(row)
    assert "shared (reusable workflow call) kept their tokens" in row.why


def test_control_a_retargeted_reusable_call_beside_a_publisher_is_the_agents():
    call = (
        "  shared:\n    permissions:\n      contents: write\n"
        "    uses: org/shared/.github/workflows/release.yml@{ref}\n"
    )
    row = _row_for(BASE + call.format(ref="v1"), BASE + call.format(ref="v2") + PUBLISH)
    assert row.expands and not _outside(row)


def test_control_write_all_in_the_publisher_is_the_agents():
    head = BASE + PUBLISH.replace("    permissions:\n      issues: write\n", "    permissions: write-all\n")
    row = _row_for(BASE, head)
    assert row.expands and not _outside(row)


def test_control_an_agent_job_on_repository_defaults_is_the_agents():
    """No `permissions` anywhere: the agent job's token is the repository's default, unknown."""

    base = _agent_job(permissions="").replace("permissions: {}\n", "")
    row = _row_for(base, base + PUBLISH)
    assert "analyze: repository defaults (unknown)" in row.after
    assert row.expands and not _outside(row)


def test_control_an_added_agent_job_is_the_agents():
    """An agent job new in this change has no before-state to have kept."""

    assert not _outside(_row_for(None, HEAD))
    publisher = "name: Agent review\non: issues\npermissions: {}\njobs:\n" + PUBLISH
    base = publisher.replace("      issues: write\n", "      issues: read\n")
    row = _row_for(base, publisher + BASE.split("jobs:\n", 1)[1])
    assert "publish gained" not in row.why
    assert row.expands and not _outside(row)


def test_control_an_agent_launch_moved_into_the_publisher_is_the_agents():
    moved = HEAD.replace("      - run: echo non-agent publisher\n", (
        "      - uses: anthropics/claude-code-action@v1\n"
        "        with:\n"
        "          prompt: Summarize the issue\n"
    ))
    row = _row_for(BASE, moved)
    assert row.expands and not _outside(row)


def test_control_a_step_reference_moved_into_the_publisher_is_the_agents():
    base = BASE.replace(
        "    steps:\n", "    steps:\n      - uses: actions/github-script@v7\n", 1,
    )
    head = BASE + PUBLISH.replace(
        "    steps:\n", "    steps:\n      - uses: actions/github-script@v7\n",
    )
    row = _row_for(base, head)
    assert "moved between jobs" in row.why
    assert row.expands and not _outside(row)


def test_control_unreadable_steps_are_a_possible_agent_job():
    unreadable = "  other:\n    runs-on: ubuntu-latest\n    permissions:\n      issues: {level}\n    steps: run\n"
    row = _row_for(BASE + unreadable.format(level="read"), BASE + unreadable.format(level="write"))
    assert row.expands and not _outside(row)


def test_control_a_privileged_event_beside_the_publisher_is_the_agents():
    head = HEAD.replace("on: issues", "on: [issues, pull_request_target]")
    row = _row_for(BASE, head)
    assert row.expands and not _outside(row)
    assert f"workflow_pull_request_target_changed: {SOURCE}" in _signals(
        {"agent.yml": BASE}, {"agent.yml": head}
    )


def test_control_inherited_secrets_beside_the_publisher_are_the_agents():
    call = (
        "  shared:\n    permissions:\n      contents: read\n"
        "    uses: org/shared/.github/workflows/release.yml@v1\n"
    )
    head = BASE + call.replace("    uses", "    secrets: inherit\n    uses") + PUBLISH
    row = _row_for(BASE + call, head)
    assert row.expands and not _outside(row)


def test_control_a_gained_agent_rule_beside_the_publisher_is_the_agents():
    head = HEAD.replace(
        "          prompt: Summarize the issue\n",
        "          prompt: Summarize the issue\n"
        "          claude_args: --permission-mode bypassPermissions\n",
        1,
    )
    row = _row_for(BASE, head)
    assert row.expands and not _outside(row)


# --- a workflow with no agent at all -------------------------------------------------


AGENTLESS_BASE = """name: Example
on: [pull_request]
permissions:
  contents: read
jobs:
  inspect:
    runs-on: ubuntu-latest
    steps:
      - run: echo inspect
"""
AGENTLESS_HEAD = AGENTLESS_BASE.replace("contents: read", "contents: write")


def test_an_agentless_workflow_widening_is_outside_any_agent_job():
    row = _row_for(AGENTLESS_BASE, AGENTLESS_HEAD)
    assert (row.direction, row.expands, row.severity) == ("widened", True, "high")
    assert row.why == (
        "grants write permissions to workflow jobs; "
        "inspect gained the write scope contents, and this audit finds no agent launch or reusable "
        f"workflow call in this workflow; {OUTSIDE_AGENT_JOBS}"
    )


def test_an_added_agentless_workflow_is_outside_any_agent_job():
    row = _row_for(None, AGENTLESS_HEAD)
    assert (row.direction, row.expands) == ("added", True)
    assert _outside(row)


def test_an_agentless_workflow_diff_text_and_hook_say_so(tmp_path):
    from tests.test_install_hooks import _host_diff_workspace, _run_hook

    repo = _pr(tmp_path, {f"{WORKFLOWS}/example.yml": AGENTLESS_BASE}, {f"{WORKFLOWS}/example.yml": AGENTLESS_HEAD})
    result = _invoke("diff", "--workspace", str(repo), "--base", "main")
    assert result.exit_code == 0, _output(result)
    assert OUTSIDE_SUMMARY in _output(result)

    hooked = tmp_path / "hooked"
    hooked.mkdir()
    _host_diff_workspace(hooked)
    message = json.loads(
        _run_hook(hooked, "verify", {}, diff_payload=json.dumps(_diff(repo))).stdout
    )["systemMessage"]
    assert "These rows widen a workflow job's token outside any agent job:" in message
    assert "These rows widen what the agent can do" not in message


# --- the summary and the hook split, with both kinds ---------------------------------


def test_the_summary_counts_both_kinds_and_adds_up_to_the_published_widenings(tmp_path):
    other = f"{WORKFLOWS}/review.yml"
    repo = _pr(
        tmp_path,
        {SOURCE: BASE, other: BASE},
        {SOURCE: HEAD, other: BASE.replace("      issues: read\n", "      issues: write\n")},
    )
    payload = _diff(repo)
    assert payload["review"]["summary"] == {"changes": 2, "rows": 2, "widenings": 2}
    result = _invoke("diff", "--workspace", str(repo), "--base", "main")
    assert result.exit_code == 0, _output(result)
    assert (
        "2 change(s), 1 widening what the agent may do, "
        "1 widening a workflow job's token outside any agent job (⚠)."
    ) in _output(result)


def test_the_stop_hook_lists_each_kind_under_its_own_heading(tmp_path):
    from tests.test_install_hooks import _WIDENING, _host_diff_workspace, _run_hook

    _host_diff_workspace(tmp_path)
    outside = {
        **_WIDENING, "subject": f"github {SOURCE}", "before": "access: read", "after": "access: write",
        "direction": "widened", "severity": "high",
        "why": f"publish gained the write scope issues; {OUTSIDE_AGENT_JOBS}; an agent runs at analyze/steps[0]",
    }
    unknown = {**_WIDENING, "after": "PreToolUse", "expands": False, "why": f"hook edit; {DIRECTION_UNKNOWN}"}
    payload = json.dumps({"comparison_status": "comparable", "rows": [_WIDENING, outside, unknown]})
    message = json.loads(_run_hook(tmp_path, "verify", {}, diff_payload=payload).stdout)["systemMessage"]
    agent, rest = message.split("These rows widen a workflow job's token outside any agent job:")
    assert "These rows widen what the agent can do:" in agent and "Bash(*)" in agent
    assert SOURCE not in agent
    workflow, unknown_part = rest.split("their direction is not established:")
    assert SOURCE in workflow and "PreToolUse" in unknown_part
    # A clause that only mentions the words is not the clause.
    lookalike = {**outside, "why": f"publish gained the write scope issues ({OUTSIDE_AGENT_JOBS})"}
    other = tmp_path / "other"
    other.mkdir()
    _host_diff_workspace(other)
    payload = json.dumps({"comparison_status": "comparable", "rows": [lookalike]})
    message = json.loads(_run_hook(other, "verify", {}, diff_payload=payload).stdout)["systemMessage"]
    assert "These rows widen what the agent can do:" in message
    assert "outside any agent job:" not in message


def test_the_rendered_hook_holds_the_engines_clause(tmp_path):
    from tests.test_install_hooks import _rendered_hook_namespace

    assert _rendered_hook_namespace(tmp_path)["OUTSIDE_AGENT_JOBS"] == OUTSIDE_AGENT_JOBS


def test_the_clause_reader_needs_an_expanding_row_and_the_whole_clause():
    row = _row_for(BASE, HEAD)
    assert widens_outside_agent_jobs(row.expands, row.why)
    assert not widens_outside_agent_jobs(False, row.why)
    assert not widens_outside_agent_jobs(True, f"x ({OUTSIDE_AGENT_JOBS})")
    assert "; " not in OUTSIDE_AGENT_JOBS
