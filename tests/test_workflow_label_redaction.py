"""#802: a workflow's job ids, step labels, triggers and scope names publish redacted.

GitHub accepts a job id shaped like a credential (`ghp_…`, `AKIA…`), and a step
name may carry registry userinfo (`Pull docker://ci:<password>@gcr.io/…`). Every
such label is published by one rule — the report redactor, the host sanitizer,
then a `scheme://userinfo@` rule — where the grant is built, so the inventory,
saved baselines, drift, `diff`, `check`, `verify` and the PR comment hold the
same label and none holds the raw text. `config_sha256` is computed over that
published projection. A lone redacted label still compares, so it refuses
nothing; two distinct labels in one workflow that publish alike would compare
as one job, trigger or scope, so they refuse instead (#767).
"""

from __future__ import annotations

import hashlib
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
    _uncompared_workflow_text,
    _workflow_grant,
    diff_host_grants,
    host_grant_expansion_signals,
    published_workflow_label,
)

SOURCE = ".github/workflows/ci.yml"
SETTINGS = ".claude/settings.json"
ENV = {"NO_COLOR": "1", "GITHUB_ACTIONS": None, "FORCE_COLOR": None, "COLUMNS": "200"}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

_GH_JOB = "ghp_" + "J" * 36
_AWS_JOB = "AKIA" + "J" * 16
_SLACK_JOB = "xoxb-" + "J" * 16
_PASSWORD = "hunter2Sup3rS3cret"
_GH_TRIGGER = "ghp_" + "T" * 36
_GH_SCOPE = "ghp_" + "S" * 36
_GH_A, _GH_B = "ghp_" + "A" * 36, "ghp_" + "B" * 36
_DIGEST = "sha256:" + "0123abcd" * 8

#: Ordinary names the issue requires never to be rewritten.
ORDINARY_JOBS = ("build", "test", "deploy-prod", "release_notes", "secret-scan", "token-refresh")


def _digests(canary: str) -> list[str]:
    digest = hashlib.sha256(canary.encode()).hexdigest()
    return [digest, digest[:24], digest[:12]]


def _grant(value, collided=None):
    return _workflow_grant(value, source=SOURCE, collided=collided)


def _rows(before, after):
    def snapshot(value):
        return {"grants": [_grant(value)]}

    changes = diff_host_grants(snapshot(before), snapshot(after))
    return capability_diff_rows({"changes": changes, "expansion_signals": host_grant_expansion_signals(changes)})


# --- the label rule -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "published", "secrets"),
    [
        (_GH_JOB, "[REDACTED:github_token]", (_GH_JOB,)),
        (_AWS_JOB, "[REDACTED:aws_access_key]", (_AWS_JOB,)),
        (_SLACK_JOB, "[REDACTED:slack_token]", (_SLACK_JOB,)),
        (f"deploy-{_GH_JOB}", "deploy-[REDACTED:github_token]", (_GH_JOB,)),
        (
            f"Pull docker://ci:{_PASSWORD}@gcr.io/proj/img",
            "Pull docker://<redacted>@gcr.io/proj/img",
            (_PASSWORD, "ci:"),
        ),
        (
            f"Pull DOCKER://ci:{_PASSWORD}@gcr.io/proj/img",
            "Pull DOCKER://<redacted>@gcr.io/proj/img",
            (_PASSWORD, "ci:"),
        ),
        (
            "login oci://robot:SLASH1CANARY/AT1CANARY@COLON1CANARY:X@registry.example.com/img:1 now",
            "login oci://<redacted>@registry.example.com/img:1 now",
            ("robot", "SLASH1CANARY", "AT1CANARY", "COLON1CANARY"),
        ),
        (
            f"Run docker://robot:DIGESTCANARY@registry.example.com/img@{_DIGEST}",
            f"Run docker://<redacted>@registry.example.com/img@{_DIGEST}",
            ("robot", "DIGESTCANARY"),
        ),
        (
            "(docker://robot:PAREN1CANARY)PAREN2CANARY@registry.example.com/img)",
            "(docker://<redacted>@registry.example.com/img)",
            ("robot", "PAREN1CANARY", "PAREN2CANARY"),
        ),
        (
            "copy s3://key:FIRSTCANARY@bucket then docker://u:SECONDCANARY@host",
            "copy s3://<redacted>@bucket then docker://<redacted>@host",
            ("FIRSTCANARY", "SECONDCANARY"),
        ),
        (
            "fetch https://user:HTTPSCANARY@example.com/archive",
            "fetch https://example.com/<redacted-path>",
            ("HTTPSCANARY",),
        ),
        ("login token=ASSIGNCANARY", "login token=<redacted>", ("ASSIGNCANARY",)),
        # Any userinfo after `scheme://` is redacted, credential or not.
        ("Clone ssh://USERCANARY@example.com/org/repo", "Clone ssh://<redacted>@example.com/org/repo", ("USERCANARY",)),
    ],
    ids=[
        "github-token", "aws-key", "slack-token", "token-inside-id", "docker-userinfo",
        "uppercase-scheme", "slash-at-colon-in-password", "userinfo-and-digest",
        "punctuation-in-password", "two-tokens",
        "https-userinfo", "token-assignment", "any-userinfo",
    ],
)
def test_a_credential_shaped_label_publishes_redacted(value, published, secrets):
    assert published_workflow_label(value) == published
    for secret in secrets:
        assert secret not in published_workflow_label(value)


@pytest.mark.parametrize(
    "value",
    [
        *ORDINARY_JOBS,
        "contents", "id-token", "pull-requests", "security-events",
        "push", "pull_request", "pull_request_target", "workflow_dispatch",
        "Check out", "steps[0]", "Pull docker://alpine:3.18",
        f"Run docker://ghcr.io/org/image@{_DIGEST}",
        "Run docker://registry.example.com:5000/team/image:1.0",
        # Scheme-less prose is never read as userinfo: labels are not references.
        "Tag v1:beta@2", "Notify ci@example.com",
        "[REDACTED:github_token]",
    ],
)
def test_an_ordinary_label_is_never_rewritten(value):
    assert published_workflow_label(value) == value


# --- one label in every published field ------------------------------------------------


def _full_workflow(job: str) -> dict:
    return {
        "on": {"pull_request": {}},
        "permissions": {"contents": "read"},
        "jobs": {
            job: {
                "runs-on": "ubuntu-latest",
                "permissions": {"contents": "write", "id-token": "write"},
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    {"name": f"Pull docker://ci:{_PASSWORD}@gcr.io/proj/img", "uses": "actions/setup-node@v4"},
                    "not a step",
                ],
            },
            f"{job}-call": {
                "uses": "./.github/workflows/deploy.yml",
                "secrets": {"credential": "${{ secrets.STAGING_TOKEN }}", "literal": "x"},
            },
            f"{job}-broken": {"steps": {"uses": "actions/checkout@v4"}},
        },
    }


def test_every_field_that_names_a_job_holds_the_same_published_label():
    collided: set[str] = set()
    grant = _grant(_full_workflow(_GH_JOB), collided)

    label = "[REDACTED:github_token]"
    assert {context["job"] for context in grant["permission_contexts"]} == {
        label, f"{label}-call", f"{label}-broken",
    }
    call, = grant["reusable_calls"]
    assert call["job"] == f"{label}-call"
    assert {item["job"] for item in grant["step_actions"]} == {label, f"{label}-broken"}
    assert f"{label}: contents: write" in grant["write_scopes"]
    assert f"{label}: id-token: write" in grant["effective_write_scopes"]
    assert "Pull docker://<redacted>@gcr.io/proj/img" in {item["step"] for item in grant["step_actions"]}
    # One redacted job id refuses nothing.
    assert collided == set() and _uncompared_workflow_text(grant, collided) is None
    published = json.dumps(grant)
    for canary in (_GH_JOB, _PASSWORD):
        assert canary not in published
        for digest in _digests(canary):
            assert digest not in published


def test_ordinary_job_ids_keep_every_field_as_written():
    jobs = {
        name: {"runs-on": "ubuntu-latest", "permissions": {"contents": "write"}, "steps": [{"uses": "org/tool@v1"}]}
        for name in ORDINARY_JOBS
    }
    jobs["token-refresh"] = {"uses": "org/repo/.github/workflows/refresh.yml@v1", "secrets": "inherit"}
    collided: set[str] = set()
    grant = _grant(
        {"on": ["push", "pull_request"], "permissions": {"id-token": "write"}, "jobs": jobs}, collided
    )

    assert [context["job"] for context in grant["permission_contexts"]] == sorted(ORDINARY_JOBS)
    assert [item["job"] for item in grant["step_actions"]] == sorted(set(ORDINARY_JOBS) - {"token-refresh"})
    assert [call["job"] for call in grant["reusable_calls"]] == ["token-refresh"]
    assert grant["triggers"] == ["pull_request", "push"]
    assert "<top-level>: id-token: write" in grant["write_scopes"]
    assert "token-refresh: id-token: write" in grant["effective_write_scopes"]
    assert {f"{name}: contents: write" for name in ORDINARY_JOBS if name != "token-refresh"} <= set(
        grant["effective_write_scopes"]
    )
    assert "REDACTED" not in json.dumps(grant) and "<redacted>" not in json.dumps(grant)
    assert collided == set()


def test_config_sha256_is_computed_over_the_published_labels():
    """The decided digest: a published label, never the raw text, reaches `config_sha256`."""

    first, second = (_grant(_full_workflow(job)) for job in (_GH_A, _GH_B))
    # Two lone token-shaped ids publish the same grant, digest included, so the
    # digest says nothing about either raw id.
    assert first == second
    ordinary = _grant(_full_workflow("build"))
    assert ordinary["config_sha256"] != first["config_sha256"]


def test_triggers_and_scope_names_publish_redacted_and_still_compare():
    before = {
        "on": {"pull_request": {}, _GH_TRIGGER: {}},
        "permissions": {"contents": "read"},
        "jobs": {"build": {"permissions": {"contents": "read"}, "steps": []}},
    }
    after = {**before, "jobs": {"build": {"permissions": {"contents": "read", _GH_SCOPE: "write"}, "steps": []}}}

    grant = _grant(after)
    assert grant["triggers"] == ["[REDACTED:github_token]", "pull_request"]
    assert grant["permission_contexts"][0]["permissions"] == {"[REDACTED:github_token]": "write", "contents": "read"}
    assert "build: [REDACTED:github_token]: write" in grant["effective_write_scopes"]
    row, = _rows(before, after)
    assert row.expands and "build: [REDACTED:github_token]: write" in row.after
    for canary in (_GH_TRIGGER, _GH_SCOPE):
        assert canary not in row.before + row.after + row.why


def test_the_reported_step_name_row_names_the_step_without_its_password():
    def workflow(ref):
        return {
            "on": "pull_request",
            "permissions": {"contents": "read"},
            "jobs": {"build": {"steps": [{"name": f"Pull docker://ci:{_PASSWORD}@gcr.io/proj/img", "uses": ref}]}},
        }

    row, = _rows(workflow("actions/checkout@v4"), workflow("actions/checkout@main"))
    assert "a step's action reference changed (build/Pull docker://<redacted>@gcr.io/proj/img)" in row.why
    assert _PASSWORD not in row.before + row.after + row.why


# --- labels that publish alike ------------------------------------------------------------


def _collision(kind: str, first: str, second: str) -> dict:
    job = {"runs-on": "ubuntu-latest", "steps": [{"uses": "actions/checkout@v4"}]}
    if kind == "job ids":
        return {"on": "push", "permissions": {"contents": "read"}, "jobs": {first: job, second: {**job}}}
    if kind == "trigger names":
        return {"on": {first: {}, second: {}}, "permissions": {"contents": "read"}, "jobs": {"build": job}}
    return {"on": "push", "permissions": {first: "write", second: "none"}, "jobs": {"build": job}}


@pytest.mark.parametrize("kind", ["job ids", "trigger names", "permission scope names"])
def test_two_distinct_labels_that_publish_alike_are_a_blocking_limit(kind):
    collided: set[str] = set()
    grant = _grant(_collision(kind, _GH_A, _GH_B), collided)

    assert collided == {kind}
    assert _uncompared_workflow_text(grant, collided) == (
        f"distinct {kind} in this workflow publish alike once credential-shaped text is redacted, "
        "so they cannot be compared apart"
    )
    for canary in (_GH_A, _GH_B):
        assert canary not in json.dumps(grant)


@pytest.mark.parametrize(
    "scopes",
    [{_GH_A: "write", _GH_B: "read"}, {_GH_A: "read", _GH_B: "write"}, {_GH_A: "write", _GH_B: "none"}],
    ids=["write-then-read", "read-then-write", "write-then-none"],
)
def test_scope_names_that_publish_alike_keep_the_widest_level(scopes):
    """A collision refuses, but the grant it publishes never reads a declared write as read."""

    collided: set[str] = set()
    grant = _grant(
        {"on": "push", "jobs": {"build": {"permissions": scopes, "steps": [{"uses": "actions/checkout@v4"}]}}},
        collided,
    )

    assert collided == {"permission scope names"}
    context, = grant["permission_contexts"]
    assert context["permissions"] == {"[REDACTED:github_token]": "write"}
    assert grant["effective_write_scopes"] == ["build: [REDACTED:github_token]: write"]
    assert (grant["access"], grant["risk"]) == ("write", "high")


def _swapped_steps(first: str, second: str) -> tuple[dict, dict]:
    def workflow(first_ref: str, second_ref: str) -> dict:
        return {
            "on": "push",
            "permissions": {"contents": "read"},
            "jobs": {first: {"steps": [{"uses": first_ref}]}, second: {"steps": [{"uses": second_ref}]}},
        }

    return workflow("org/lint@v1", "org/deploy@v1"), workflow("org/deploy@v1", "org/lint@v1")


def test_why_a_collision_must_refuse():
    # Two ordinary jobs trading their references is a row: each reference now
    # runs under the other job's token.
    row, = _rows(*_swapped_steps("build", "deploy-prod"))
    assert "moved between jobs" in row.why
    # The same trade between two jobs whose ids publish alike publishes the
    # same grant, so without the limit it would read as no change at all.
    assert _rows(*_swapped_steps(_GH_A, _GH_B)) == []


def test_a_collision_and_a_redacted_reference_are_named_in_one_limit():
    collided: set[str] = set()
    workflow = _collision("job ids", _GH_A, _GH_B)
    workflow["jobs"]["build"] = {"steps": [{"uses": "org/tool@token=REFCANARY"}]}
    workflow["permissions"] = {_GH_A: "read", _GH_B: "read"}
    grant = _grant(workflow, collided)

    assert _uncompared_workflow_text(grant, collided) == (
        "a step action reference contains credential-shaped text; it is published redacted and cannot be "
        "compared; distinct job ids and permission scope names in this workflow publish alike once "
        "credential-shaped text is redacted, so they cannot be compared apart"
    )


@pytest.mark.parametrize(
    ("workflow", "expected"),
    [
        # `sk-…` ids are redacted for display although no credential, so two collide.
        (
            {"on": "push", "jobs": {"sk-integration-tests-matrix": {}, "sk-integration-tests-linux-arm": {}}},
            {"job ids"},
        ),
        (
            {"on": "push", "jobs": {"build": {"steps": [
                {"name": f"Deploy {_GH_A}", "uses": "a/b@v1"}, {"name": f"Deploy {_GH_B}", "uses": "a/b@v2"},
            ]}}},
            set(),
        ),
        ({"on": "push", "jobs": {_GH_A: {"steps": []}, "build": {"steps": []}}}, set()),
        ({"on": {_GH_A: {}, "push": {}}, "jobs": {_GH_B: {"permissions": {_GH_A: "write"}}}}, set()),
    ],
    ids=["ordinary-prefixed-ids-collide", "step-labels-are-not-compared", "lone-job-id", "one-per-namespace"],
)
def test_only_a_collision_within_one_namespace_is_recorded(workflow, expected):
    collided: set[str] = set()
    _grant(workflow, collided)

    assert collided == expected


# --- the command routes ----------------------------------------------------------------


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


def _branch(repo: Path, files: dict[str, str]) -> None:
    _git(repo, "checkout", "-qb", "change")
    _write(repo, files)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "head")


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


def _settings(*rules: str) -> str:
    return json.dumps({"permissions": {"allow": list(rules)}}, indent=2) + "\n"


def _diff(repo: Path) -> dict:
    result = _invoke("diff", "--workspace", str(repo), "--base", "main", "--json")
    assert result.exit_code == 0, _output(result)
    return json.loads(result.stdout)


BASE_WORKFLOW = f"""on:
  pull_request: {{}}
  {_GH_TRIGGER}: {{}}
permissions:
  contents: read
jobs:
  {_GH_JOB}:
    runs-on: ubuntu-latest
    steps:
      - name: Pull docker://ci:{_PASSWORD}@gcr.io/proj/img
        uses: actions/checkout@v4
  {_SLACK_JOB}:
    uses: ./.github/workflows/deploy.yml
    secrets:
      DEPLOY_TOKEN: ${{{{ secrets.STAGING_TOKEN }}}}
  scoped:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/setup-node@v4
"""

HEAD_WORKFLOW = f"""on:
  pull_request: {{}}
  {_GH_TRIGGER}: {{}}
permissions:
  contents: read
jobs:
  {_GH_JOB}:
    runs-on: ubuntu-latest
    steps:
      - name: Pull docker://ci:{_PASSWORD}@gcr.io/proj/img
        uses: actions/checkout@main
  {_SLACK_JOB}:
    uses: ./.github/workflows/deploy.yml
    secrets:
      DEPLOY_TOKEN: ${{{{ secrets.PROD_TOKEN }}}}
  {_AWS_JOB}:
    runs-on: ubuntu-latest
    permissions: write-all
    steps:
      - uses: actions/checkout@v4
  scoped:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      {_GH_SCOPE}: write
    steps:
      - uses: actions/setup-node@v4
"""

CANARIES = (_GH_JOB, _AWS_JOB, _SLACK_JOB, _PASSWORD, _GH_TRIGGER, _GH_SCOPE)


def test_no_label_canary_or_digest_reaches_any_published_output(tmp_path):
    repo = _repo(tmp_path, {SOURCE: BASE_WORKFLOW, SETTINGS: _settings("Bash(npm test:*)")})
    baseline = tmp_path / "baseline.json"
    saved = _invoke("audit", "--host", "--workspace", str(repo), "--save-baseline", "--baseline-file", str(baseline))
    assert saved.exit_code == 0, _output(saved)
    _branch(repo, {SOURCE: HEAD_WORKFLOW, SETTINGS: _settings("Bash(npm *)")})

    inventory_run = _invoke("audit", "--host", "--workspace", str(repo), "--json")
    assert inventory_run.exit_code == 0, _output(inventory_run)
    inventory = json.loads(inventory_run.stdout)
    github, = [item for item in inventory["host_coverage"] if item["host"] == "github"]
    assert github["status"] == "complete" and inventory["issues"] == []

    texts = {"inventory": _output(inventory_run), "baseline": baseline.read_text()}
    head_baseline = tmp_path / "head-baseline.json"
    drift_args = ["audit", "--host", "--workspace", str(repo), "--drift", "--baseline-file", str(baseline)]
    check_args = ["check", "--workspace", str(repo), "--base", "main", "--head", "HEAD"]
    # Each command must run and say what it was asked, so an absent canary is
    # evidence rather than an empty or crashed output. The Markdown audit and
    # drift print no job label, so they are held to their own heading.
    labelled = "[REDACTED:aws_access_key]"
    for name, (args, marker) in {
        "audit-markdown": (["audit", "--host", "--workspace", str(repo)], "| github | complete | 1 |"),
        "drift-json": ([*drift_args, "--json"], labelled),
        "drift-markdown": (drift_args, f"workflow_write_changed: {SOURCE}"),
        "head-baseline": (
            ["audit", "--host", "--workspace", str(repo), "--save-baseline", "--baseline-file", str(head_baseline)],
            "Host-grants baseline created",
        ),
        "diff-json": (["diff", "--workspace", str(repo), "--base", "main", "--json"], labelled),
        "diff-text": (["diff", "--workspace", str(repo), "--base", "main"], labelled),
        "check-agent-boundary-json": ([*check_args, "--format", "agent-boundary-json"], labelled),
        "check-agent-control-json": ([*check_args, "--format", "agent-control-json"], labelled),
        "check-text": ([*check_args, "--format", "text"], labelled),
        "verify-text": (
            ["verify", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "text"], labelled,
        ),
    }.items():
        result = _invoke(*args)
        texts[name] = _output(result)
        assert result.exit_code == 0, (name, texts[name])
        assert marker in texts[name], (name, texts[name])
    texts["head-baseline-file"] = head_baseline.read_text()
    reports = repo / "agents-shipgate-reports"
    texts["pr-comment.md"] = (reports / "pr-comment.md").read_text()
    texts["verifier.json"] = (reports / "verifier.json").read_text()
    for name in ("inventory", "head-baseline-file", "pr-comment.md", "verifier.json"):
        assert labelled in texts[name], name
    assert "[REDACTED:github_token]" in texts["baseline"]

    # The change is still read and shown, under the published labels.
    drift = json.loads(texts["drift-json"])
    assert (drift["comparison_status"], drift["has_drift"]) == ("comparable", True)
    diff = json.loads(texts["diff-json"])
    assert diff["comparison_status"] == "comparable" and diff["unchanged_limits"] == []
    workflow_row, = [row for row in diff["rows"] if row["subject"] == f"github {SOURCE}"]
    assert workflow_row["expands"] is True
    assert "[REDACTED:aws_access_key]: write-all" in workflow_row["after"]
    assert "scoped: [REDACTED:github_token]: write" in workflow_row["after"]
    assert "on: [REDACTED:github_token], pull_request" in workflow_row["after"]
    assert "([REDACTED:slack_token]/DEPLOY_TOKEN)" in workflow_row["why"]
    assert "[REDACTED:github_token]/Pull docker://<redacted>@gcr.io/proj/img: uses actions/checkout@main" in (
        workflow_row["after"]
    )
    assert {row["subject"] for row in diff["rows"]} == {f"github {SOURCE}", f"claude-code {SETTINGS}"}
    boundary = json.loads(texts["check-agent-boundary-json"])
    assert boundary["comparison_status"] == "comparable"
    evidence = [item["evidence"] for item in boundary["violations"] if item["path"] == SOURCE]
    assert {"kind": "workflow_write_all", "job": "[REDACTED:aws_access_key]"} in evidence
    assert any(item.get("scope") == "[REDACTED:github_token]" for item in evidence)
    verifier = json.loads(texts["verifier.json"])
    assert verifier["host_comparison"]["comparison_status"] == "comparable"
    assert "[REDACTED:aws_access_key]: write-all" in texts["pr-comment.md"]

    for name, text in texts.items():
        for canary in CANARIES:
            assert canary not in text, (name, canary)
            for digest in _digests(canary):
                assert digest not in text, (name, canary, digest)


@pytest.mark.parametrize("job", [_GH_JOB, "sk-integration-tests-matrix"], ids=["github-token", "ordinary-sk-prefix"])
@pytest.mark.parametrize("workflow_changes", [True, False], ids=["workflow-changed", "workflow-unchanged"])
def test_a_lone_redacted_job_id_never_refuses_the_shell_permission_rows(tmp_path, job, workflow_changes):
    def workflow(ref):
        return yaml.safe_dump(
            {"on": "pull_request", "permissions": {"contents": "read"}, "jobs": {job: {"steps": [{"uses": ref}]}}},
            sort_keys=False,
        )

    repo = _repo(tmp_path, {SOURCE: workflow("actions/checkout@v4"), SETTINGS: _settings("Bash(npm test:*)")})
    head = {SETTINGS: _settings("Bash(npm *)")}
    if workflow_changes:
        head[SOURCE] = workflow("actions/checkout@main")
    _branch(repo, head)

    payload = _diff(repo)
    assert payload["comparison_status"] == "comparable"
    assert payload["unchanged_limits"] == [] and payload["incomparable_reasons"] == []
    settings_rows = [row for row in payload["rows"] if row["subject"] == f"claude-code {SETTINGS}"]
    assert {row["direction"] for row in settings_rows} == {"added", "removed"}
    workflow_rows = [row for row in payload["rows"] if row["subject"] == f"github {SOURCE}"]
    assert len(workflow_rows) == int(workflow_changes)
    if workflow_changes:
        label = published_workflow_label(job)
        assert label != job and f"{label}/steps[0]: uses actions/checkout@main" in workflow_rows[0]["after"]

    boundary = json.loads(
        _invoke("check", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json").stdout
    )
    assert boundary["comparison_status"] == "comparable"
    assert any(row["subject"] == f"claude-code {SETTINGS}" for row in boundary["rows"])
    inventory = json.loads(_invoke("audit", "--host", "--workspace", str(repo), "--json").stdout)
    github, = [item for item in inventory["host_coverage"] if item["host"] == "github"]
    assert github["status"] == "complete" and inventory["issues"] == []
    assert job not in json.dumps(payload) + json.dumps(boundary) + json.dumps(inventory)


@pytest.mark.parametrize("edit", ["rename-job", "step-password"])
def test_an_edit_only_the_raw_file_holds_is_no_row_but_drifts_once_as_an_artifact(tmp_path, edit):
    """The documented limit: the grant cannot tell the two apart, the artifact `redacted_sha256` can.

    Renaming a lone `ghp_A…` job to `ghp_B…`, or editing only the password in
    a step name, publishes the same grant, so `diff` shows no row. The file's
    artifact digest is still taken over the whole parsed file, as on `1.0.0`,
    so drift reports it once with no grant change. `check` evaluates the raw
    declarations, so a renamed `write-all` job reads there as a new one.
    """

    def workflow(job: str, password: str) -> str:
        return yaml.safe_dump(
            {
                "on": "pull_request",
                "permissions": {"contents": "read"},
                "jobs": {job: {"permissions": "write-all", "steps": [
                    {"name": f"Pull docker://ci:{password}@gcr.io/proj/img", "uses": "actions/checkout@v4"},
                ]}},
            },
            sort_keys=False,
        )

    repo = _repo(tmp_path, {SOURCE: workflow(_GH_A, "FIRSTPASSWORD")})
    baseline = tmp_path / "baseline.json"
    saved = _invoke("audit", "--host", "--workspace", str(repo), "--save-baseline", "--baseline-file", str(baseline))
    assert saved.exit_code == 0, _output(saved)
    _branch(repo, {SOURCE: workflow(_GH_B, "FIRSTPASSWORD") if edit == "rename-job" else workflow(_GH_A, "SECONDPASSWORD")})

    payload = _diff(repo)
    assert (payload["comparison_status"], payload["rows"], payload["unchanged_limits"]) == ("comparable", [], [])
    boundary = json.loads(
        _invoke("check", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json").stdout
    )
    assert (boundary["comparison_status"], boundary["rows"]) == ("comparable", [])
    write_all = [item for item in boundary["violations"] if item["check_id"] == "SHIP-HOST-BOUNDARY-WORKFLOW-WRITE-ALL"]
    if edit == "rename-job":
        assert boundary["decision"] == "block"
        assert [item["evidence"] for item in write_all] == [{"kind": "workflow_write_all", "job": "[REDACTED:github_token]"}]
    else:
        assert write_all == []

    drift_args = ["audit", "--host", "--workspace", str(repo), "--drift", "--baseline-file", str(baseline)]
    drift = json.loads(_invoke(*drift_args, "--json").stdout)
    assert (drift["comparison_status"], drift["has_drift"], drift["changes"]) == ("comparable", True, [])
    change, = drift["artifact_changes"]
    assert change["baseline"]["path"] == SOURCE
    assert change["baseline"]["redacted_sha256"] != change["current"]["redacted_sha256"]
    assert _invoke(*drift_args, "--fail-on-drift", "--json").exit_code == 20
    published = json.dumps(payload) + json.dumps(boundary) + json.dumps(drift)
    for canary in (_GH_A, _GH_B, "FIRSTPASSWORD", "SECONDPASSWORD"):
        assert canary not in published


def test_a_changed_workflow_whose_job_ids_collide_refuses_and_an_unchanged_one_is_named(tmp_path):
    base, head = (yaml.safe_dump(value, sort_keys=False) for value in _swapped_steps(_GH_A, _GH_B))
    other = ".github/workflows/release.yml"
    ordinary = yaml.safe_dump(
        {"on": "push", "permissions": {"contents": "read"}, "jobs": {"release_notes": {"steps": [{"uses": "a/b@v1"}]}}}
    )
    repo = _repo(tmp_path, {SOURCE: base, other: ordinary})

    # Unchanged: named beside the other workflow's row.
    _branch(repo, {other: ordinary.replace("a/b@v1", "a/b@v2")})
    unchanged = _diff(repo)
    assert unchanged["comparison_status"] == "comparable"
    limit, = unchanged["unchanged_limits"]
    assert (limit["host"], limit["limit"], limit["source"]) == ("github", "unsupported", SOURCE)
    assert "distinct job ids in this workflow publish alike" in limit["detail"]
    row, = unchanged["rows"]
    assert row["subject"] == f"github {other}" and "release_notes/steps[0]: uses a/b@v2" in row["after"]

    # Changed: the trade would otherwise compare as no change, so it refuses.
    _write(repo, {SOURCE: head})
    changed = _diff(repo)
    assert changed["comparison_status"] == "incomparable" and changed["rows"] == []
    assert {"base_inventory_incomplete", "head_inventory_incomplete"} <= set(changed["incomparable_reasons"])
    audit = _invoke("audit", "--host", "--workspace", str(repo), "--json")
    issue, = [item for item in json.loads(audit.stdout)["issues"] if item["host"] == "github"]
    assert issue["blocking"] and issue["kind"] == "unsupported"
    text = _invoke("diff", "--workspace", str(repo), "--base", "main")
    assert "Cannot compare against main" in _output(text)
    combined = json.dumps(unchanged) + json.dumps(changed) + _output(audit) + _output(text)
    for canary in (_GH_A, _GH_B):
        assert canary not in combined


def test_an_unchanged_collision_refuses_check_save_baseline_and_drift_until_a_job_is_renamed(tmp_path):
    """Only `diff` and `verify` compare past an unchanged collision; `check`, the baseline and drift refuse.

    Two ordinary `sk-…` job ids both publish as a redacted OpenAI key, so the
    workflow carries a blocking limit on every run while both ids exist, even
    when the pull request changes nothing but a Claude Code shell permission.
    """

    def workflow(second: str) -> str:
        return yaml.safe_dump(
            {
                "on": "pull_request",
                "permissions": {"contents": "read"},
                "jobs": {
                    "sk-integration-tests-matrix": {"steps": [{"uses": "actions/checkout@v4"}]},
                    second: {"steps": [{"uses": "actions/checkout@v4"}]},
                },
            },
            sort_keys=False,
        )

    colliding, renamed = workflow("sk-integration-tests-linux-arm"), workflow("integration-tests-linux-arm")
    repo = _repo(tmp_path, {SOURCE: colliding, SETTINGS: _settings("Bash(npm test:*)")})
    _branch(repo, {SETTINGS: _settings("Bash(npm *)")})
    check_args = ["check", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json"]

    # `diff` compares past the unchanged workflow and names it.
    payload = _diff(repo)
    assert payload["comparison_status"] == "comparable"
    assert {row["subject"] for row in payload["rows"]} == {f"claude-code {SETTINGS}"}
    limit, = payload["unchanged_limits"]
    assert limit["source"] == SOURCE and "distinct job ids in this workflow publish alike" in limit["detail"]

    # `check` cannot carry the limit, so it refuses on every such run (#721).
    boundary = json.loads(_invoke(*check_args).stdout)
    assert (boundary["comparison_status"], boundary["incomparable_reasons"], boundary["rows"]) == (
        "incomparable", ["unchanged_limits_not_representable"], [],
    )
    assert boundary["decision"] == "require_review"
    assert {"check_id": "SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE", "path": SOURCE} in [
        {"check_id": item["check_id"], "path": item["path"]} for item in boundary["violations"]
    ]

    # A baseline cannot acknowledge the limit.
    baseline = tmp_path / "baseline.json"
    save = ["audit", "--host", "--workspace", str(repo), "--save-baseline", "--baseline-file", str(baseline)]
    refused = _invoke(*save)
    assert refused.exit_code == 2, _output(refused)
    assert "incomplete or experimental" in _output(refused) and not baseline.exists()

    # Drift against a baseline saved before the collision is incomparable.
    _write(repo, {SOURCE: renamed})
    saved = _invoke(*save)
    assert saved.exit_code == 0, _output(saved)
    _write(repo, {SOURCE: colliding})
    drift = _invoke("audit", "--host", "--workspace", str(repo), "--drift", "--json", "--baseline-file", str(baseline))
    report = json.loads(drift.stdout)
    assert (report["comparison_status"], report["has_drift"]) == ("incomparable", None)
    assert "current_inventory_incomplete" in report["incomparable_reasons"]
    failing = _invoke(
        "audit", "--host", "--workspace", str(repo), "--drift", "--fail-on-drift", "--json", "--baseline-file", str(baseline)
    )
    assert failing.exit_code == 20, _output(failing)

    # Once the rename is on the base branch, every route compares again.
    _git(repo, "checkout", "-q", "main")
    _write(repo, {SOURCE: renamed})
    _git(repo, "commit", "-qam", "rename a job")
    _git(repo, "checkout", "-q", "change")
    _git(repo, "merge", "-q", "--no-edit", "main")
    renamed_boundary = json.loads(_invoke(*check_args).stdout)
    assert renamed_boundary["comparison_status"] == "comparable"
    assert any(row["subject"] == f"claude-code {SETTINGS}" for row in renamed_boundary["rows"])
    baseline.unlink()
    resaved = _invoke(*save)
    assert resaved.exit_code == 0, _output(resaved)
