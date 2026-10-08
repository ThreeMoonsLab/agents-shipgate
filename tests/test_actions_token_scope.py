"""#920, #921, #924: the GitHub Actions token-scope model.

Three facts a workflow row used to fuse are said apart:

- **The event context.** `pull_request_target` is privileged whatever the
  scopes, and keeps its warning, its critical severity and its widening when
  it arrives. It is not a token scope: an explicitly read-only workflow under
  it reads `access: read` and is never said to grant write (#920).
- **A calling job's ceiling.** A job that calls a reusable workflow runs no
  step; its `permissions` bound the called workflow's jobs, which GitHub lets
  keep or reduce them. A same-repository callee's own permissions are read
  (bounded, never guessed), and a remote or unread one leaves the ceiling
  counted as reaching its jobs, said as such (#921).
- **The called reference.** Re-pinning an inheriting call from a branch to a
  commit names other code for the same workflow and passes the same secrets:
  a `changed` row naming the reference, never a new recipient (#924).

Ordinary-job inheritance (#685) and named secret forwarding (#693) are
unchanged, and every control here still widens.
"""

from __future__ import annotations

import json
import re
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
    resolve_reusable_workflow_ceilings,
)
from agents_shipgate.schemas.agent_control_envelope import truncate_prose

FIXTURES = Path(__file__).parent / "fixtures/workflows/921"
ROOT = Path(__file__).resolve().parents[1]
ENV = {
    "NO_COLOR": "1", "GITHUB_ACTIONS": None, "FORCE_COLOR": None, "COLUMNS": "400",
    "AGENTS_SHIPGATE_CLI": None, "AGENTS_SHIPGATE_ENABLE_PLUGINS": None,
    "CLAUDECODE": None, "CURSOR_TRACE_ID": None,
}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
WORKFLOWS = ".github/workflows"

# --- the issues' minimal reproductions, as written in them ------------------------

#: #920: an explicitly read-only `pull_request_target` workflow, newly added.
READ_ONLY_TARGET = """name: CODEOWNERS
on:
  pull_request_target:
    types: [opened, synchronize, reopened, edited]
permissions:
  contents: read
  pull-requests: read
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - run: echo validation
"""

#: #921: only the caller's `contents: read -> write` changes.
CALLER = """name: Caller
on: workflow_dispatch
permissions:
  contents: {level}
jobs:
  inspect:
    uses: ./.github/workflows/read.yml
"""
READ_CALLEE = """name: Read only
on: workflow_call
permissions:
  contents: read
jobs:
  read:
    runs-on: ubuntu-latest
    steps:
      - run: echo read-only fixture
"""

#: #924: only the reusable workflow's reference changes.
CLA = """name: CLA
on:
  pull_request_target:
permissions:
  pull-requests: write
  statuses: write
jobs:
  cla:
    uses: blacklanternsecurity/CLA/.github/workflows/cla-reusable.yml@{ref}
    secrets: inherit
"""
PIN = "4f85d3c525483f0f846ef4384a8ece24a3c74375"

#: (base files, head files) for each issue's reproduction.
REPRODUCTIONS = {
    "920": ({}, {f"{WORKFLOWS}/codeowners-validation.yml": READ_ONLY_TARGET}),
    "921": (
        {f"{WORKFLOWS}/caller.yml": CALLER.format(level="read"), f"{WORKFLOWS}/read.yml": READ_CALLEE},
        {f"{WORKFLOWS}/caller.yml": CALLER.format(level="write")},
    ),
    "924": (
        {f"{WORKFLOWS}/cla.yml": CLA.format(ref="main")},
        {f"{WORKFLOWS}/cla.yml": CLA.format(ref=PIN)},
    ),
}

CLA_TARGET = "blacklanternsecurity/CLA/.github/workflows/cla-reusable.yml"

#: The exact row each reproduction gives, on every route.
EXPECTED = {
    "920": {
        "subject": f"github {WORKFLOWS}/codeowners-validation.yml",
        "direction": "added",
        "expands": True,
        "severity": "critical",
        "before": "—",
        "after": "access: read, validate: contents: read, validate: pull-requests: read, pull_request_target",
        "why": (
            "uses the privileged pull_request_target event context; "
            "every job declares read-only or no token permissions"
        ),
    },
    "921": {
        "subject": f"github {WORKFLOWS}/caller.yml",
        "direction": "changed",
        "expands": False,
        "severity": "low",
        "before": (
            "access: read, inspect: ceiling contents: read, on: workflow_dispatch, "
            "inspect: uses: ./.github/workflows/read.yml"
        ),
        "after": (
            "access: read, inspect: ceiling contents: write, on: workflow_dispatch, "
            "inspect: uses: ./.github/workflows/read.yml (called jobs hold no write scope)"
        ),
        "why": (
            "inspect's permissions are a ceiling for the workflow it calls (contents: write); "
            "that workflow's own permissions give none of its jobs a write scope from it"
        ),
    },
    "924": {
        "subject": f"github {WORKFLOWS}/cla.yml",
        "direction": "changed",
        "expands": False,
        "severity": "critical",
        "before": (
            "access: write, cla: ceiling pull-requests: write, cla: ceiling statuses: write, "
            f"pull_request_target, cla: secrets: inherit → {CLA_TARGET}@main"
        ),
        "after": (
            "access: write, cla: ceiling pull-requests: write, cla: ceiling statuses: write, "
            f"pull_request_target, cla: secrets: inherit → {CLA_TARGET}@{PIN}"
        ),
        "why": (
            "uses the privileged pull_request_target event context; "
            "cla's permissions are a ceiling for the workflow it calls "
            "(pull-requests: write, statuses: write); that workflow is in another repository and "
            "is not read, so whether its jobs hold them is not established; "
            f"the called code reference changed (cla: main → {PIN}); it adds no declared scope "
            "or secret, and this audit compares references as text, so it establishes neither "
            "what either one runs nor that they run the same code; "
            f"passes the caller's available secrets to {CLA_TARGET}@{PIN}"
        ),
    },
}

#: The drift expansion signals each reproduction gives.
EXPECTED_SIGNALS = {
    "920": [f"workflow_pull_request_target_added: {WORKFLOWS}/codeowners-validation.yml"],
    "921": [],
    "924": [],
}


# --- helpers ---------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _write(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _pr(tmp_path: Path, base: dict[str, str], head: dict[str, str]) -> Path:
    """A repository whose `main` holds ``base`` and whose `change` branch adds ``head``."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _write(repo, {".gitignore": "agents-shipgate-reports/\n", "README.md": "fixture\n", **base})
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "change")
    _write(repo, head)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")
    return repo


def _fixture_pr(tmp_path: Path, case: str) -> Path:
    def tree(side: str) -> dict[str, str]:
        directory = FIXTURES / case / side
        if not directory.is_dir():
            return {}
        return {f"{WORKFLOWS}/{path.name}": path.read_text() for path in sorted(directory.iterdir())}

    return _pr(tmp_path, tree("base"), tree("head"))


def _invoke(*args: str):
    return CliRunner().invoke(app, list(args), env=ENV)


def _output(result) -> str:
    text = result.output
    try:
        text += result.stderr
    except (AttributeError, ValueError):
        pass
    if result.exception is not None:
        text += repr(result.exception)
    return _ANSI.sub("", text)


def _diff(repo: Path) -> dict:
    result = _invoke("diff", "--workspace", str(repo), "--base", "main", "--json")
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.stdout)
    assert payload["comparison_status"] == "comparable", payload
    return payload


def _row(row: dict) -> dict:
    return {key: row[key] for key in ("subject", "direction", "expands", "severity", "before", "after", "why")}


def _drift_signals(repo: Path) -> list[str]:
    """The saved-baseline route: `main` saved, the change compared against it."""

    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import (
        build_host_drift_payload,
        build_host_grants_baseline,
    )

    _git(repo, "checkout", "-q", "main")
    baseline = build_host_grants_baseline(host_audit_inventory(repo))
    _git(repo, "checkout", "-q", "change")
    drift = build_host_drift_payload(baseline=baseline, inventory=host_audit_inventory(repo), baseline_file="b.json")
    assert drift["comparison_status"] == "comparable"
    return drift["expansion_signals"]


def _grants(files: dict[str, str]) -> list[dict]:
    """Workflow grants read from YAML text, resolved as the inventory resolves them."""

    grants = [
        _workflow_grant(yaml.safe_load(text), source=f"{WORKFLOWS}/{name}")
        for name, text in files.items()
    ]
    resolve_reusable_workflow_ceilings(grants, [])
    return grants


def _rows(before: dict[str, str], after: dict[str, str]):
    changes = diff_host_grants({"grants": _grants(before)}, {"grants": _grants(after)})
    return capability_diff_rows({"changes": changes, "expansion_signals": host_grant_expansion_signals(changes)})


def _signals(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return host_grant_expansion_signals(diff_host_grants({"grants": _grants(before)}, {"grants": _grants(after)}))


def _workflow(permissions=None, *, on="push", **jobs) -> str:
    data: dict = {"on": on, "jobs": jobs}
    if permissions is not None:
        data["permissions"] = permissions
    return yaml.safe_dump(data, sort_keys=False)


def _step_job(permissions=None) -> dict:
    job: dict = {"runs-on": "ubuntu-latest", "steps": [{"run": "echo fixture"}]}
    if permissions is not None:
        job["permissions"] = permissions
    return job


def _call_job(uses: str, permissions=None, secrets=None) -> dict:
    job: dict = {"uses": uses}
    if permissions is not None:
        job["permissions"] = permissions
    if secrets is not None:
        job["secrets"] = secrets
    return job


# --- the three reproductions, the same on every route ------------------------------


@pytest.fixture(params=sorted(REPRODUCTIONS))
def reproduction(request, tmp_path):
    base, head = REPRODUCTIONS[request.param]
    return request.param, _pr(tmp_path, base, head)


def test_diff_json_gives_the_exact_row(reproduction):
    case, repo = reproduction
    row, = _diff(repo)["rows"]
    assert _row(row) == EXPECTED[case]


def test_diff_text_says_the_same_row(reproduction):
    case, repo = reproduction
    result = _invoke("diff", "--workspace", str(repo), "--base", "main")
    assert result.exit_code == 0, _output(result)
    text = " ".join(_output(result).split())
    expected = EXPECTED[case]
    assert " ".join(expected["why"].split()) in text
    assert expected["after"] in text
    if expected["expands"]:
        assert "1 widening what the agent may do (⚠)" in text and "⚠ critical" in text
    else:
        assert "⚠" not in text and "1 change(s)." in text
    # The read-only token is never worded as a write grant (#920).
    assert "grants write permissions" not in text


def test_saved_baseline_drift_raises_the_same_signals(reproduction):
    case, repo = reproduction
    assert _drift_signals(repo) == EXPECTED_SIGNALS[case]


def test_check_rows_and_control_envelope_agree(reproduction):
    case, repo = reproduction
    args = ["check", "--workspace", str(repo), "--base", "main", "--head", "HEAD"]
    machine = _invoke(*args, "--format", "agent-boundary-json")
    assert machine.exit_code == 0, _output(machine)
    row, = json.loads(machine.stdout)["rows"]
    assert _row(row) == EXPECTED[case]
    control = _invoke(*args, "--format", "agent-control-json")
    assert control.exit_code == 0, _output(control)
    envelope_row, = json.loads(control.stdout)["capability_rows"]["rows"]
    # The envelope caps every `why` at the same byte budget; nothing else differs.
    assert _row(envelope_row) == {**EXPECTED[case], "why": truncate_prose(EXPECTED[case]["why"])}


def test_manifest_free_verify_publishes_the_same_row(reproduction):
    case, repo = reproduction
    result = _invoke("verify", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "text")
    assert result.exit_code == 0, _output(result)
    verifier = json.loads((repo / "agents-shipgate-reports/verifier.json").read_text())
    comparison = verifier["host_comparison"]
    assert comparison["comparison_status"] == "comparable"
    row, = comparison["rows"]
    assert _row(row) == EXPECTED[case]


@pytest.mark.parametrize("case,rule", [
    ("920", "HOST-WORKFLOW-PULL-REQUEST-TARGET-ADDED"),
    ("921", "HOST-WORKFLOW-PERMISSIONS-EXPANDED"),
])
def test_check_keeps_its_declaration_rules(tmp_path, case, rule):
    """`check`'s workflow rules read declarations and are unchanged.

    The privileged trigger arriving, and a declared scope rising, still go to
    review. The rule names the declaration (`<top-level>` `contents` read to
    write); the row beside it says what that declaration reaches.
    """

    repo = _pr(tmp_path, *REPRODUCTIONS[case])
    result = _invoke(
        "check", "--workspace", str(repo), "--base", "main", "--head", "HEAD",
        "--format", "agent-boundary-json",
    )
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.stdout)
    assert [item["id"] for item in payload["violations"]] == [rule]
    assert payload["control"]["state"] != "complete"


# --- #920: the privileged event and the token, said apart ------------------------


def test_a_trigger_only_variant_reads_the_same_token_without_the_privileged_event():
    pull_request = READ_ONLY_TARGET.replace("pull_request_target:", "pull_request:")
    row, = _rows({}, {"ci.yml": pull_request})
    assert (row.direction, row.expands, row.severity) == ("added", False, "low")
    assert row.after == "access: read, validate: contents: read, validate: pull-requests: read, on: pull_request"
    target, = _rows({}, {"ci.yml": READ_ONLY_TARGET})
    # Only the event context separates them: same access, same scopes.
    assert target.after.replace("pull_request_target", "on: pull_request") == row.after
    assert target.severity == "critical" and target.expands


def test_gaining_the_privileged_event_on_a_read_only_workflow_still_widens_without_a_write_claim():
    before = READ_ONLY_TARGET.replace("pull_request_target:", "pull_request:")
    row, = _rows({"ci.yml": before}, {"ci.yml": READ_ONLY_TARGET})
    assert (row.direction, row.expands, row.severity) == ("widened", True, "critical")
    assert "uses the privileged pull_request_target event context" in row.why
    assert "grants write" not in row.why
    assert _signals({"ci.yml": before}, {"ci.yml": READ_ONLY_TARGET}) == [
        f"workflow_pull_request_target_changed: {WORKFLOWS}/ci.yml"
    ]


def test_control_pull_request_to_pull_request_target_on_a_write_job_still_widens():
    before = _workflow({"contents": "write"}, on="pull_request", build=_step_job())
    after = _workflow({"contents": "write"}, on="pull_request_target", build=_step_job())
    row, = _rows({"ci.yml": before}, {"ci.yml": after})
    assert (row.direction, row.expands, row.severity) == ("widened", True, "critical")
    assert row.after.startswith("access: write")
    assert "uses the privileged pull_request_target event context" in row.why
    assert "grants write permissions to workflow jobs" in row.why
    assert _signals({"ci.yml": before}, {"ci.yml": after}) == [
        f"workflow_pull_request_target_changed: {WORKFLOWS}/ci.yml",
        f"workflow_write_changed: {WORKFLOWS}/ci.yml",
    ]


def test_an_undeclared_token_under_the_privileged_event_is_documented_read_write():
    """GitHub documents the `pull_request_target` token as read/write, even from a fork."""

    after = _workflow(on="pull_request_target", build=_step_job())
    row, = _rows({}, {"ci.yml": after})
    assert row.after.startswith("access: write") and row.severity == "critical" and row.expands
    assert "a job that declares no token permissions may run with the token GitHub documents" in row.why
    assert "grants write permissions to workflow jobs" not in row.why
    assert _signals({}, {"ci.yml": after}) == [
        f"workflow_pull_request_target_added: {WORKFLOWS}/ci.yml",
        f"workflow_write_added: {WORKFLOWS}/ci.yml",
    ]


@pytest.mark.parametrize("case", ["azure-dev-10209", "openma-235"])
def test_real_read_only_pull_request_target_workflows(tmp_path, case):
    row, = _diff(_fixture_pr(tmp_path, case))["rows"]
    assert (row["direction"], row["expands"], row["severity"]) == ("added", True, "critical")
    assert row["after"].startswith("access: read, ")
    assert "pull_request_target" in row["after"] and ": write" not in row["after"]
    assert row["why"].startswith(
        "uses the privileged pull_request_target event context; "
        "every job declares read-only or no token permissions"
    )
    assert "grants write" not in row["why"]


# --- #921: a calling job's permissions are a ceiling --------------------------------


def test_control_an_ordinary_job_write_grant_still_widens():
    before = _workflow({"contents": "read"}, build=_step_job())
    after = _workflow({"contents": "write"}, build=_step_job())
    row, = _rows({"ci.yml": before}, {"ci.yml": after})
    assert (row.direction, row.expands, row.severity) == ("widened", True, "high")
    assert row.why == "grants write permissions to workflow jobs"
    assert "build: contents: write" in row.after and "ceiling" not in row.after


def test_a_callee_that_keeps_the_ceiling_widens_with_its_jobs_named():
    callee = _workflow(on="workflow_call", run=_step_job())  # declares nothing: keeps the ceiling
    base = {"caller.yml": CALLER.format(level="read"), "read.yml": callee}
    head = {"caller.yml": CALLER.format(level="write"), "read.yml": callee}
    row, = _rows(base, head)
    assert (row.direction, row.expands, row.severity) == ("widened", True, "high")
    assert row.after.startswith("access: write")
    assert "(called jobs may hold contents: write)" in row.after
    assert "a job in that workflow may hold all of it" in row.why


def test_removing_a_callee_restriction_widens_the_unchanged_caller():
    """A restriction removed in the called file is not hidden behind the caller."""

    caller = CALLER.format(level="write")
    open_callee = _workflow(on="workflow_call", read=_step_job())
    rows = _rows({"caller.yml": caller, "read.yml": READ_CALLEE}, {"caller.yml": caller, "read.yml": open_callee})
    by_subject = {row.subject: row for row in rows}
    caller_row = by_subject[f"github {WORKFLOWS}/caller.yml"]
    assert (caller_row.direction, caller_row.expands) == ("widened", True)
    assert caller_row.before.endswith("(called jobs hold no write scope)")
    assert caller_row.after.endswith("(called jobs may hold contents: write)")
    assert f"github {WORKFLOWS}/read.yml" in by_subject


def test_a_remote_callee_ceiling_raise_still_widens_and_says_it_was_not_read():
    remote = "org/shared/.github/workflows/release.yml@v1"
    before = _workflow({"contents": "read"}, release=_call_job(remote))
    after = _workflow({"contents": "write"}, release=_call_job(remote))
    row, = _rows({"ci.yml": before}, {"ci.yml": after})
    assert (row.direction, row.expands) == ("widened", True)
    assert row.why == (
        "release's permissions are a ceiling for the workflow it calls (contents: write); "
        "that workflow is in another repository and is not read, so whether its jobs hold them "
        "is not established"
    )
    assert "grants write permissions" not in row.why


@pytest.mark.parametrize("callee_files,status", [
    ({}, "not_read"),
    ({"read.yml": "{not: [valid"}, "not_read"),
])
def test_an_unread_local_callee_keeps_the_whole_ceiling(callee_files, status):
    files = {"caller.yml": CALLER.format(level="write")}
    for name, text in callee_files.items():
        try:
            yaml.safe_load(text)
        except yaml.YAMLError:
            continue  # An unparseable file yields no grant, as in the inventory.
        files[name] = text
    caller = next(grant for grant in _grants(files) if grant["source"].endswith("caller.yml"))
    call, = caller["reusable_calls"]
    assert call["callee_permissions"] == status and "callee_write_scopes" not in call
    assert caller["effective_write_scopes"] == ["inspect: contents: write"]
    assert caller["access"] == "write"


def test_a_limited_callee_is_not_read():
    grants = [
        _workflow_grant(yaml.safe_load(CALLER.format(level="write")), source=f"{WORKFLOWS}/caller.yml"),
        _workflow_grant(yaml.safe_load(READ_CALLEE), source=f"{WORKFLOWS}/read.yml"),
    ]
    issues = [{"host": "github", "source": f"{WORKFLOWS}/read.yml", "blocking": True}]
    resolve_reusable_workflow_ceilings(grants, issues)
    call, = grants[0]["reusable_calls"]
    assert call["callee_permissions"] == "limited"
    assert grants[0]["effective_write_scopes"] == ["inspect: contents: write"]


def test_nested_same_repository_calls_reduce_through_the_chain():
    middle = _workflow(on="workflow_call", hop=_call_job("./.github/workflows/leaf.yml"))
    leaf = _workflow({"contents": "read", "packages": "write"}, on="workflow_call", leaf=_step_job())
    caller = _workflow({"contents": "write", "packages": "write"}, run=_call_job("./.github/workflows/middle.yml"))
    grants = _grants({"caller.yml": caller, "middle.yml": middle, "leaf.yml": leaf})
    call, = grants[0]["reusable_calls"]
    assert call["callee_permissions"] == "read"
    assert call["callee_write_scopes"] == ["packages"]
    assert grants[0]["effective_write_scopes"] == ["run: packages: write"]


def test_a_write_all_ceiling_is_reduced_to_what_the_callee_declares():
    callee = _workflow({"contents": "write", "issues": "read"}, on="workflow_call", job=_step_job())
    caller = _workflow("write-all", run=_call_job("$/.github/workflows/callee.yml"))
    grants = _grants({"caller.yml": caller, "callee.yml": callee})
    assert grants[0]["reusable_calls"][0]["callee_write_scopes"] == ["contents"]
    assert grants[0]["effective_write_scopes"] == ["run: contents: write"]
    assert grants[0]["write_all"] is False and grants[0]["access"] == "write"


def test_a_loop_keeps_the_ceiling():
    a = _workflow({"contents": "write"}, on="workflow_call", hop=_call_job("./.github/workflows/b.yml"))
    b = _workflow(on="workflow_call", hop=_call_job("./.github/workflows/a.yml"))
    grants = _grants({"a.yml": a, "b.yml": b})
    call, = grants[0]["reusable_calls"]
    # b itself was read; its only job calls back into a, which is not followed.
    assert call["callee_permissions"] == "read"
    assert call["callee_write_scopes"] == ["contents"]
    self_call = _workflow({"contents": "write"}, on="workflow_call", hop=_call_job("./.github/workflows/self.yml"))
    grant, = _grants({"self.yml": self_call})
    assert grant["reusable_calls"][0]["callee_permissions"] == "cycle"
    assert grant["effective_write_scopes"] == ["hop: contents: write"]


def test_a_chain_past_ten_levels_is_not_followed_and_keeps_the_ceiling():
    files = {"w0.yml": _workflow({"contents": "write"}, run=_call_job("./.github/workflows/w1.yml"))}
    for level in range(1, 12):
        files[f"w{level}.yml"] = _workflow(on="workflow_call", run=_call_job(f"./.github/workflows/w{level + 1}.yml"))
    files["w12.yml"] = _workflow({"contents": "read"}, on="workflow_call", run=_step_job())
    caller = _grants(files)[0]
    assert caller["reusable_calls"][0]["callee_write_scopes"] == ["contents"]
    # Within ten levels the restriction is read.
    short = {"w0.yml": files["w0.yml"], "w1.yml": _workflow({"contents": "read"}, on="workflow_call", run=_step_job())}
    assert _grants(short)[0]["reusable_calls"][0]["callee_write_scopes"] == []


@pytest.mark.parametrize("uses", [
    "./.github/workflows/sub/read.yml", "./.github/workflows/read.yml@main", "./.github/workflows/",
])
def test_a_same_repository_spelling_github_does_not_run_is_not_read(uses):
    caller = _workflow({"contents": "write"}, inspect=_call_job(uses))
    grant = _grants({"caller.yml": caller, "read.yml": READ_CALLEE})[0]
    assert grant["reusable_calls"][0]["callee_permissions"] == "not_read"
    assert grant["effective_write_scopes"] == ["inspect: contents: write"]


def test_a_call_without_a_write_ceiling_keeps_its_shape():
    """Only a call whose ceiling holds a write scope gains callee facts."""

    for permissions in ({"contents": "read"}, {}, None):
        caller = _workflow(permissions, inspect=_call_job("./.github/workflows/read.yml"))
        grant = _grants({"caller.yml": caller, "read.yml": READ_CALLEE})[0]
        assert grant == _workflow_grant(yaml.safe_load(caller), source=f"{WORKFLOWS}/caller.yml")


def test_a_nested_copy_resolves_within_its_own_tree():
    grants = [
        _workflow_grant(yaml.safe_load(CALLER.format(level="write")), source="samples/x/.github/workflows/caller.yml"),
        _workflow_grant(yaml.safe_load(READ_CALLEE), source="samples/x/.github/workflows/read.yml"),
        _workflow_grant(yaml.safe_load(_workflow(on="workflow_call", read=_step_job())), source=f"{WORKFLOWS}/read.yml"),
    ]
    resolve_reusable_workflow_ceilings(grants, [])
    assert grants[0]["reusable_calls"][0]["callee_write_scopes"] == []


def test_resolution_is_bounded_on_a_large_dense_call_graph(monkeypatch):
    """Ten layers of 12 workflows, each calling all 12 of the next.

    Followed naively that is 12**9 paths from each top caller. A called
    workflow's answer is remembered per ceiling, so the walk is bounded by the
    calls in the graph, counted here rather than timed. Its cost is then the
    caller's digest, recomputed once, as when the workflow was read.
    """

    from agents_shipgate.core import host_grants

    layers, width = 10, 12
    files = {}
    for layer in range(layers):
        for index in range(width):
            if layer == layers - 1:
                jobs = {"leaf": _step_job({"contents": "read"})}
            else:
                jobs = {
                    f"j{target}": _call_job(f"./.github/workflows/l{layer + 1}w{target}.yml")
                    for target in range(width)
                }
            files[f"l{layer}w{index}.yml"] = _workflow({"contents": "write", "packages": "write"}, **jobs)
    grants = [
        _workflow_grant(yaml.safe_load(text), source=f"{WORKFLOWS}/{name}") for name, text in files.items()
    ]
    calls = 0
    reach = host_grants._CalleeReader.reach

    def counted(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return reach(self, *args, **kwargs)

    monkeypatch.setattr(host_grants._CalleeReader, "reach", counted)
    resolve_reusable_workflow_ceilings(grants, [])
    edges = (layers - 1) * width * width
    assert calls <= 3 * edges, calls
    first = next(grant for grant in grants if grant["source"].endswith("/l0w0.yml"))
    assert {call["callee_permissions"] for call in first["reusable_calls"]} == {"read"}
    assert {tuple(call["callee_write_scopes"]) for call in first["reusable_calls"]} == {()}
    assert first["effective_write_scopes"] == [] and first["access"] == "read"


def test_real_reusable_callers_whose_callees_restrict_are_not_widenings(tmp_path):
    payload = _diff(_fixture_pr(tmp_path, "hiero-27275"))
    by_subject = {row["subject"]: row for row in payload["rows"]}
    caller = by_subject[f"github {WORKFLOWS}/600-flow-pull-request-checks.yaml"]
    assert (caller["direction"], caller["expands"]) == ("changed", False)
    for job in ("extract-citr-vars", "evm-functional-tests"):
        assert f"{job}: ceiling id-token: write" in caller["after"]
        assert (
            f"{job}'s permissions are a ceiling for the workflow it calls (checks: write, "
            "id-token: write, issues: write, pull-requests: write, statuses: write); that "
            "workflow's own permissions give none of its jobs a write scope from it"
        ) in caller["why"]
    # The ordinary jobs keep their inherited writes, and say so.
    assert "grants write permissions to workflow jobs" in caller["why"]
    assert "detect-change-type: id-token: write" in caller["after"]
    # The named forwarding of #693 is still told, and still does not widen.
    assert "extract-citr-vars: secret github-token ← secrets.GITHUB_TOKEN" in caller["after"]
    # The new called workflow is its own row.
    added = by_subject[f"github {WORKFLOWS}/870-call-evm-functional-tests.yaml"]
    assert (added["direction"], added["expands"], added["severity"]) == ("added", False, "low")
    assert sum(row["expands"] for row in payload["rows"]) == 0


def test_real_new_workflow_keeps_its_ordinary_job_widening(tmp_path):
    payload = _diff(_fixture_pr(tmp_path, "rocm-120"))
    by_subject = {row["subject"]: row for row in payload["rows"]}
    row = by_subject[f"github {WORKFLOWS}/rocm-build.yml"]
    assert (row["direction"], row["expands"], row["severity"]) == ("added", True, "high")
    assert "read-version: id-token: write" in row["after"] and "results: actions: write" in row["after"]
    assert "grants write permissions to workflow jobs" in row["why"]
    for job in ("build-dra", "build-helm"):
        assert (
            f"{job}'s permissions are a ceiling for the workflow it calls (actions: write, "
            "id-token: write); a job in that workflow may hold actions: write, and its own "
            "permissions withhold the rest"
        ) in row["why"]
    for job in ("resolve", "publish"):
        assert (
            f"{job}'s permissions are a ceiling for the workflow it calls (actions: write, "
            "id-token: write); that workflow is in another repository and is not read"
        ) in row["why"]


def test_control_named_secret_forwarding_is_unchanged():
    before = _workflow(
        {"contents": "read"},
        deploy=_call_job("./.github/workflows/deploy.yml", secrets={"credential": "${{ secrets.STAGING_TOKEN }}"}),
    )
    after = before.replace("STAGING_TOKEN", "PRODUCTION_TOKEN")
    row, = _rows({"ci.yml": before}, {"ci.yml": after})
    assert (row.direction, row.expands) == ("changed", False)
    assert "different named source (deploy/credential)" in row.why


# --- #924: a re-pinned inheriting call --------------------------------------------


def test_real_re_pin_is_a_changed_row_naming_the_reference(tmp_path):
    row, = _diff(_fixture_pr(tmp_path, "bbot-3483"))["rows"]
    assert (row["direction"], row["expands"], row["severity"]) == ("changed", False, "critical")
    assert f"the called code reference changed (cla: main → {PIN})" in row["why"]
    assert f"cla: secrets: inherit → {CLA_TARGET}@main" in row["before"]
    assert f"cla: secrets: inherit → {CLA_TARGET}@{PIN}" in row["after"]
    assert "uses the privileged pull_request_target event context" in row["why"]


@pytest.mark.parametrize("old,new", [
    ("main", PIN), (PIN, "main"), ("v1", "v2"),
])
def test_a_reference_only_edit_never_widens_in_either_direction(old, new):
    row, = _rows({"cla.yml": CLA.format(ref=old)}, {"cla.yml": CLA.format(ref=new)})
    assert (row.direction, row.expands) == ("changed", False)
    assert "the called code reference changed" in row.why
    assert _signals({"cla.yml": CLA.format(ref=old)}, {"cla.yml": CLA.format(ref=new)}) == []


def test_control_a_new_inheriting_call_still_widens():
    before = CLA.format(ref="main")
    after = before + "  second:\n    uses: org/other/.github/workflows/x.yml@v1\n    secrets: inherit\n"
    row, = _rows({"cla.yml": before}, {"cla.yml": after})
    assert (row.direction, row.expands) == ("widened", True)
    assert "passes the caller's available secrets to org/other/.github/workflows/x.yml@v1" in row.why
    assert f"workflow_secrets_inherited_changed: {WORKFLOWS}/cla.yml" in _signals({"cla.yml": before}, {"cla.yml": after})


def test_control_adding_secrets_inherit_to_a_call_still_widens():
    before = CLA.format(ref="main").replace("    secrets: inherit\n", "")
    after = CLA.format(ref="main")
    row, = _rows({"cla.yml": before}, {"cla.yml": after})
    assert (row.direction, row.expands) == ("widened", True)
    assert _signals({"cla.yml": before}, {"cla.yml": after}) == [f"workflow_secrets_inherited_changed: {WORKFLOWS}/cla.yml"]


def test_control_a_re_pin_together_with_added_inherit_still_widens():
    before = CLA.format(ref="main").replace("    secrets: inherit\n", "")
    after = CLA.format(ref=PIN)
    row, = _rows({"cla.yml": before}, {"cla.yml": after})
    assert (row.direction, row.expands) == ("widened", True)
    assert "the called code reference changed" in row.why


def test_control_retargeting_an_inheriting_call_to_another_workflow_still_widens():
    before = CLA.format(ref="main")
    after = before.replace("cla-reusable.yml@main", "other.yml@main")
    row, = _rows({"cla.yml": before}, {"cla.yml": after})
    assert (row.direction, row.expands) == ("widened", True)
    assert "the called workflow changed (cla:" in row.why


# --- schema -----------------------------------------------------------------------


def test_resolved_calls_validate_against_the_published_schemas(tmp_path):
    from jsonschema import Draft202012Validator

    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import build_host_grants_baseline

    repo = _fixture_pr(tmp_path, "rocm-120")
    inventory = host_audit_inventory(repo)
    calls = [
        call
        for grant in inventory["grants"] if grant["kind"] == "workflow"
        for call in grant["reusable_calls"]
    ]
    assert {call.get("callee_permissions") for call in calls} == {None, "read"}
    baseline = build_host_grants_baseline(inventory)
    Draft202012Validator(json.loads((ROOT / "docs/host-grants-inventory-schema.v0.9.json").read_text())).validate(inventory)
    Draft202012Validator(json.loads((ROOT / "docs/host-grants-baseline-schema.v0.9.json").read_text())).validate(baseline)
    # Saved and reloaded, the resolution is compared, not dropped.
    assert any("callee_write_scopes" in json.dumps(grant) for grant in baseline["inventory"]["grants"])


def test_the_support_page_states_the_model_and_names_remote_callees_as_unread():
    page = (ROOT / "docs/host-boundary-support.md").read_text(encoding="utf-8")
    unread = page[page.index("### Known unread surfaces"):page.index("Review changes to those files")]
    assert "**A reusable workflow in another repository**" in unread
    section = " ".join(page[page.index('<a id="workflow-token-scopes"></a>'):].split())
    for fact in (
        "every job declares read-only or no token permissions",
        "`workflow_pull_request_target_<added|changed>`",
        "`job: ceiling scope: level`",
        "`callee_permissions` `not_read`, `limited`, `cycle`, `too_deep`",
        "the called code reference changed",
        "https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions",
        "https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations",
    ):
        assert fact in section, fact


def test_fixture_provenance_names_every_fixture_file():
    listed = {entry["fixture"] for entry in json.loads((FIXTURES / "provenance.json").read_text())}
    on_disk = {
        path.relative_to(FIXTURES).as_posix()
        for path in FIXTURES.rglob("*") if path.is_file() and path.parent.name in {"base", "head"}
    }
    assert listed == on_disk
