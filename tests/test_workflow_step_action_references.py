"""#771: a workflow step's remote action reference is compared as declared text.

A reference names code, not authority. Moving `actions/checkout` from a pinned
SHA to `@main` changes what runs with the job's token, so it is a row; it adds
no token scope, so the row never widens. Permission direction stays with the
permission contexts (#685), local composites stay unread (#701), and a value
Shipgate cannot publish without redaction refuses rather than collide (#767).
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
)

SOURCE = ".github/workflows/ci.yml"
PINNED = "11bd71901bbe5b1630ceea73d27597364c9af683"


def _workflow(*steps, permissions=None, jobs=None):
    return {
        "on": "pull_request",
        "permissions": permissions if permissions is not None else {"contents": "read"},
        "jobs": jobs if jobs is not None else {"test": {"runs-on": "ubuntu-latest", "steps": list(steps)}},
    }


def _changes(before, after):
    def snapshot(value):
        return {"grants": [_workflow_grant(value, source=SOURCE)]}

    return diff_host_grants(snapshot(before), snapshot(after))


def _rows(before, after):
    changes = _changes(before, after)
    payload = {"changes": changes, "expansion_signals": host_grant_expansion_signals(changes)}
    return capability_diff_rows(payload)


# --- the comparison ---------------------------------------------------------------


def test_a_pinned_sha_moved_to_a_branch_is_one_changed_row_that_does_not_widen():
    row, = _rows(
        _workflow({"uses": f"actions/checkout@{PINNED}"}),
        _workflow({"uses": "actions/checkout@main"}),
    )

    assert row.subject == f"github {SOURCE}"
    assert row.direction == "changed"
    assert row.expands is False
    assert f"test/steps[0]: uses actions/checkout@{PINNED}" in row.before
    assert "test/steps[0]: uses actions/checkout@main" in row.after
    assert "action reference changed (test/steps[0])" in row.why
    assert "adds no scope" in row.why


def test_a_reference_change_emits_no_expansion_signal_and_leaves_access_alone():
    before = _workflow({"uses": f"actions/checkout@{PINNED}"})
    after = _workflow({"uses": "actions/checkout@main"})

    assert host_grant_expansion_signals(_changes(before, after)) == []
    old, new = (_workflow_grant(value, source=SOURCE) for value in (before, after))
    assert (old["access"], old["risk"]) == (new["access"], new["risk"])


def test_an_unchanged_reference_is_quiet():
    workflow = _workflow({"uses": f"actions/checkout@{PINNED}"}, {"run": "echo one"})
    edited = _workflow({"uses": f"actions/checkout@{PINNED}"}, {"run": "echo two"})

    assert _rows(workflow, edited) == []


@pytest.mark.parametrize(
    "after",
    [
        # reordered
        _workflow({"uses": "actions/setup-node@v4"}, {"uses": f"actions/checkout@{PINNED}"}),
        # renamed and given ids
        _workflow(
            {"id": "checkout", "name": "Check out", "uses": f"actions/checkout@{PINNED}"},
            {"id": "node", "uses": "actions/setup-node@v4"},
        ),
        # a run step inserted between them moves their positions
        _workflow({"uses": f"actions/checkout@{PINNED}"}, {"run": "echo"}, {"uses": "actions/setup-node@v4"}),
    ],
    ids=["reordered", "renamed", "shifted"],
)
def test_reordering_or_relabelling_steps_that_declare_the_same_references_is_quiet(after):
    before = _workflow({"uses": f"actions/checkout@{PINNED}"}, {"uses": "actions/setup-node@v4"})

    assert _rows(before, after) == []


def test_added_and_removed_references_keep_their_job_and_step():
    base = _workflow({"id": "checkout", "uses": "actions/checkout@v4"})
    head = _workflow(
        {"id": "checkout", "uses": "actions/checkout@v4"},
        {"name": "Set up node", "uses": "actions/setup-node@v4"},
    )

    added, = _rows(base, head)
    assert "test/Set up node: uses actions/setup-node@v4" in added.after
    assert "setup-node" not in added.before
    # An unchanged reference is not repeated on either side.
    assert "actions/checkout@v4" not in added.before + added.after
    assert added.direction == "changed" and not added.expands
    assert "test/Set up node" in added.why

    removed, = _rows(head, base)
    assert "test/Set up node: uses actions/setup-node@v4" in removed.before
    assert "setup-node" not in removed.after
    assert not removed.expands


def test_a_change_names_the_job_it_happened_in():
    def jobs(ref):
        return {
            "lint": {"steps": [{"uses": "actions/checkout@v4"}]},
            "deploy": {"steps": [{"id": "fetch", "uses": ref}]},
        }

    row, = _rows(_workflow(jobs=jobs("actions/checkout@v4")), _workflow(jobs=jobs("actions/checkout@main")))

    assert "deploy/fetch: uses actions/checkout@v4" in row.before
    assert "deploy/fetch: uses actions/checkout@main" in row.after
    assert "lint/" not in row.before + row.after


def test_moving_a_reference_to_another_job_is_a_change():
    before = _workflow(jobs={"a": {"steps": [{"uses": "org/tool@v1"}]}, "b": {"steps": []}})
    after = _workflow(jobs={"a": {"steps": []}, "b": {"steps": [{"uses": "org/tool@v1"}]}})

    row, = _rows(before, after)
    assert "a/steps[0]: uses org/tool@v1" in row.before
    assert "b/steps[0]: uses org/tool@v1" in row.after
    # The same reference, not different code (review nit 1).
    assert "moved between jobs (a/steps[0] → b/steps[0])" in row.why
    assert "names different code" not in row.why
    assert not row.expands


def test_a_moved_reference_and_a_changed_one_are_explained_separately():
    before = _workflow(jobs={
        "a": {"steps": [{"uses": "org/tool@v1"}, {"id": "fetch", "uses": "actions/checkout@v4"}]},
        "b": {"steps": []},
    })
    after = _workflow(jobs={
        "a": {"steps": [{"id": "fetch", "uses": "actions/checkout@main"}]},
        "b": {"steps": [{"uses": "org/tool@v1"}]},
    })

    row, = _rows(before, after)
    assert "moved between jobs (a/steps[0] → b/steps[0])" in row.why
    assert "action reference changed (a/fetch)" in row.why
    assert "a/steps[0]" not in row.why.split("action reference changed")[1]


def test_a_duplicate_reference_is_counted():
    once = _workflow({"uses": "actions/checkout@v4"})
    twice = _workflow({"uses": "actions/checkout@v4"}, {"uses": "actions/checkout@v4"})

    row, = _rows(once, twice)
    # One surplus reference; which duplicate it is cannot be told apart.
    assert row.after.count("uses actions/checkout@v4") == 1
    assert "checkout" not in row.before


def test_permission_evidence_alone_decides_widening_beside_a_reference_change():
    widened, = _rows(
        _workflow({"uses": "org/tool@v1"}, permissions={"contents": "read"}),
        _workflow({"uses": "org/tool@main"}, permissions={"contents": "write"}),
    )
    assert widened.expands and widened.direction == "widened"
    assert "grants write permissions" in widened.why and "action reference changed" in widened.why

    narrowed, = _rows(
        _workflow({"uses": "org/tool@v1"}, permissions={"contents": "write"}),
        _workflow({"uses": "org/tool@main"}, permissions={"contents": "read"}),
    )
    assert not narrowed.expands and narrowed.direction == "changed"


def test_a_reference_change_under_write_access_still_does_not_widen():
    row, = _rows(
        _workflow({"uses": f"org/tool@{PINNED}"}, permissions="write-all"),
        _workflow({"uses": "org/tool@main"}, permissions="write-all"),
    )
    assert row.expands is False and row.direction == "changed"
    assert row.severity == "critical"  # The workflow's own risk, not a new claim.


def test_reusable_calls_and_inherited_secrets_are_unchanged_by_the_step_read():
    before = _workflow(jobs={"deploy": {"uses": "org/repo/.github/workflows/deploy.yml@v1", "secrets": "inherit"}})
    after = _workflow(jobs={"deploy": {"uses": "org/repo/.github/workflows/deploy.yml@v2", "secrets": "inherit"}})

    row, = _rows(before, after)
    assert row.expands and "secrets: inherit" in row.after
    assert "step_actions" not in _workflow_grant(after, source=SOURCE)


# --- what is and is not a supported reference ------------------------------------


def test_a_local_action_reference_stays_unread():
    before = _workflow({"uses": "./.github/actions/build"})
    after = _workflow({"uses": "./.github/actions/deploy"})

    assert "step_actions" not in _workflow_grant(after, source=SOURCE)
    assert _rows(before, after) == []


def test_replacing_a_local_action_with_a_remote_one_shows_the_remote_one_only():
    row, = _rows(_workflow({"uses": "./.github/actions/build"}), _workflow({"uses": "org/build@v1"}))

    assert "test/steps[0]: uses org/build@v1" in row.after
    assert "./.github/actions" not in row.before + row.after


def test_a_docker_reference_is_compared():
    before = _workflow({"uses": "docker://alpine:3.18"})
    after = _workflow({"uses": "docker://alpine:latest"})

    entry, = _workflow_grant(after, source=SOURCE)["step_actions"]
    assert entry["form"] == "docker" and entry["unresolved_reason"] is None
    row, = _rows(before, after)
    assert "uses docker://alpine:latest" in row.after and not row.expands


def test_a_remote_reference_with_a_subdirectory_is_remote():
    entry, = _workflow_grant(_workflow({"uses": "org/actions/sub/dir@v1"}), source=SOURCE)["step_actions"]
    assert entry["form"] == "remote" and entry["uses"] == "org/actions/sub/dir@v1"


@pytest.mark.parametrize(
    ("value", "reason", "published"),
    [
        ("${{ matrix.action }}", "expression", "${{ matrix.action }}"),
        ("org/tool@${{ inputs.ref }}", "expression", "org/tool@${{ inputs.ref }}"),
        ("actions/checkout", "unsupported_reference", "actions/checkout"),
        ("", "unsupported_reference", ""),
        (123, "not_a_string", None),
        (None, "not_a_string", None),
        ({"owner": "actions"}, "not_a_string", None),
    ],
    ids=["expression", "expression-ref", "no-ref", "empty", "number", "null", "mapping"],
)
def test_a_value_that_names_no_action_is_listed_as_unresolved_rather_than_guessed(value, reason, published):
    entry, = _workflow_grant(_workflow({"uses": value}), source=SOURCE)["step_actions"]

    assert entry["form"] == "unresolved"
    assert entry["unresolved_reason"] == reason
    assert entry["uses"] == published


def test_an_expression_change_is_visible_as_unresolved_and_does_not_widen():
    row, = _rows(
        _workflow({"uses": "${{ matrix.first }}"}),
        _workflow({"uses": "${{ matrix.second }}"}),
    )

    assert "uses ${{ matrix.second }} (unresolved: expression)" in row.after
    assert row.expands is False


def test_resolving_an_expression_to_a_reference_is_a_change():
    row, = _rows(_workflow({"uses": "${{ matrix.action }}"}), _workflow({"uses": "actions/checkout@v4"}))

    assert "(unresolved: expression)" in row.before
    assert "test/steps[0]: uses actions/checkout@v4" in row.after


# --- redaction ----------------------------------------------------------------------


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


def _yaml(*steps, permissions=None) -> str:
    return yaml.safe_dump(_workflow(*steps, permissions=permissions), sort_keys=False)


def _diff(repo: Path, *args: str) -> dict:
    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", "main", *args, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_two_credential_shaped_references_redact_alike_so_the_changed_workflow_refuses(tmp_path):
    first, second = "org/tool@token=FIRSTCANARY", "org/tool@token=SECONDCANARY"
    # The display collision is real: both publish the same text.
    published = {
        _workflow_grant(_workflow({"uses": value}), source=SOURCE)["step_actions"][0]["uses"]
        for value in (first, second)
    }
    assert published == {"org/tool@token=<redacted>"}

    repo = _repo(tmp_path, {SOURCE: _yaml({"uses": first})})
    _write(repo, {SOURCE: _yaml({"uses": second})})
    payload = _diff(repo)

    # A distinct change is refused, never compared as equal.
    assert payload["comparison_status"] == "incomparable"
    assert {"base_inventory_incomplete", "head_inventory_incomplete"} <= set(payload["incomparable_reasons"])
    assert payload["rows"] == []

    audit = CliRunner().invoke(app, ["audit", "--host", "--workspace", str(repo), "--json"])
    text = audit.output
    assert "FIRSTCANARY" not in text and "SECONDCANARY" not in text
    inventory = json.loads(text[text.index("{"):]) if "{" in text else {}
    issues = [issue for issue in inventory.get("issues", []) if issue["host"] == "github"]
    assert issues and all(issue["blocking"] and issue["kind"] == "unsupported" for issue in issues)
    assert "FIRSTCANARY" not in json.dumps(payload) and "SECONDCANARY" not in json.dumps(payload)


def test_an_unchanged_credential_shaped_reference_is_a_named_limit_beside_other_rows(tmp_path):
    other = ".github/workflows/release.yml"
    repo = _repo(
        tmp_path,
        {SOURCE: _yaml({"uses": "org/tool@token=CANARY"}), other: _yaml({"uses": f"actions/checkout@{PINNED}"})},
    )
    _write(repo, {other: _yaml({"uses": "actions/checkout@main"})})
    payload = _diff(repo)

    assert payload["comparison_status"] == "comparable"
    limit, = payload["unchanged_limits"]
    assert (limit["host"], limit["limit"], limit["source"]) == ("github", "unsupported", SOURCE)
    row, = payload["rows"]
    assert row["subject"].endswith("release.yml") and "actions/checkout@main" in row["after"]
    assert "CANARY" not in json.dumps(payload)


def test_steps_whose_redacted_names_collide_still_show_a_distinct_change():
    before = _workflow(
        {"name": "deploy token=AAA", "uses": "org/deploy@v1"},
        {"name": "deploy token=BBB", "uses": "org/deploy@v2"},
    )
    after = _workflow(
        {"name": "deploy token=AAA", "uses": "org/deploy@v1"},
        {"name": "deploy token=BBB", "uses": "org/deploy@main"},
    )

    row, = _rows(before, after)
    assert "uses org/deploy@v2" in row.before and "uses org/deploy@main" in row.after
    assert "AAA" not in row.before + row.after + row.why
    assert "BBB" not in row.before + row.after + row.why


# --- saved baselines ----------------------------------------------------------------


def _legacy_baseline(tmp_path: Path, version: str):
    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import build_host_grants_baseline, host_grants_sha256

    current = host_audit_inventory(tmp_path)
    legacy = build_host_grants_baseline(current)
    legacy["host_grants_schema_version"] = version
    for grant in legacy["inventory"]["grants"]:
        if grant["kind"] == "workflow":
            del grant["step_actions"]
    legacy["inventory_sha256"] = host_grants_sha256(legacy["inventory"])
    return current, legacy


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_a_legacy_baseline_holding_a_workflow_does_not_assert_no_step_references(tmp_path, version):
    from agents_shipgate.core.host_grants import build_host_drift_payload, load_host_grants_baseline

    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text(_yaml({"uses": "actions/checkout@v4"}))
    current, legacy = _legacy_baseline(tmp_path, version)
    baseline_path = tmp_path / "baseline.json"
    original = json.dumps(legacy)
    baseline_path.write_text(original)

    loaded = load_host_grants_baseline(baseline_path)
    assert loaded == legacy
    drift = build_host_drift_payload(baseline=loaded, inventory=current, baseline_file=str(baseline_path))

    assert drift["comparison_status"] == "incomparable"
    assert drift["incomparable_reasons"] == ["baseline_workflow_step_actions_unavailable"]
    assert drift["has_drift"] is None and drift["changes"] == []
    assert baseline_path.read_text() == original


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_a_legacy_baseline_without_a_workflow_stays_comparable(tmp_path, version):
    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import build_host_drift_payload

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(json.dumps({"permissions": {"allow": ["Read(**)"]}}))
    current, legacy = _legacy_baseline(tmp_path, version)

    unchanged = build_host_drift_payload(baseline=legacy, inventory=current, baseline_file="b.json")
    assert unchanged["comparison_status"] == "comparable" and unchanged["has_drift"] is False

    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text(_yaml({"uses": "actions/checkout@main"}))
    added = build_host_drift_payload(
        baseline=legacy, inventory=host_audit_inventory(tmp_path), baseline_file="b.json"
    )
    assert added["comparison_status"] == "comparable"
    change, = added["changes"]
    assert change["baseline"] is None and change["current"]["step_actions"][0]["uses"] == "actions/checkout@main"


def test_a_current_baseline_compares_step_references(tmp_path):
    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import (
        build_host_drift_payload,
        build_host_grants_baseline,
    )

    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text(_yaml({"uses": f"actions/checkout@{PINNED}"}))
    baseline = build_host_grants_baseline(host_audit_inventory(tmp_path))
    assert baseline["host_grants_schema_version"] == "0.6"

    path.write_text(_yaml({"uses": "actions/checkout@main"}))
    drift = build_host_drift_payload(baseline=baseline, inventory=host_audit_inventory(tmp_path), baseline_file="b.json")

    assert drift["comparison_status"] == "comparable" and drift["has_drift"] is True
    assert len(drift["changes"]) == 1 and drift["expansion_signals"] == []


# --- the same row on every route ----------------------------------------------------


@pytest.fixture
def pr(tmp_path):
    repo = _repo(tmp_path, {SOURCE: _yaml({"uses": f"actions/checkout@{PINNED}"}, {"run": "make test"})})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml({"uses": "actions/checkout@main"}, {"run": "make test"})})
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "move checkout to main")
    return repo


def _assert_the_row(row: dict) -> None:
    assert row["subject"] == f"github {SOURCE}"
    assert f"test/steps[0]: uses actions/checkout@{PINNED}" in row["before"]
    assert "test/steps[0]: uses actions/checkout@main" in row["after"]
    assert row["direction"] == "changed"
    assert row["expands"] is False


def test_diff_names_the_change_in_json_and_text(pr):
    payload = _diff(pr)
    assert payload["comparison_status"] == "comparable"
    row, = payload["rows"]
    _assert_the_row(row)

    text = CliRunner().invoke(app, ["diff", "--workspace", str(pr), "--base", "main"])
    assert text.exit_code == 0, text.output
    assert "actions/checkout@main" in text.output and "test/steps[0]" in text.output
    assert "⚠" not in text.output
    assert "1 change(s)." in text.output


def test_diff_is_quiet_for_the_same_commit(pr):
    result = CliRunner().invoke(app, ["diff", "--workspace", str(pr), "--base", "HEAD", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["comparison_status"] == "comparable" and payload["rows"] == []


@pytest.mark.parametrize("preview", [True, False])
def test_manifest_free_verify_and_its_pr_comment_name_the_change(pr, preview):
    args = ["verify", "--workspace", str(pr), "--base", "main", "--head", "HEAD", "--format", "text"]
    result = CliRunner().invoke(app, [*args, *(["--preview"] if preview else [])])
    assert result.exit_code == 0, result.output
    assert "actions/checkout@main" in result.output

    comment = (pr / "agents-shipgate-reports/pr-comment.md").read_text()
    assert "actions/checkout@main" in comment and "test/steps[0]" in comment

    verifier = json.loads((pr / "agents-shipgate-reports/verifier.json").read_text())
    comparison = verifier["host_comparison"]
    assert comparison["comparison_status"] == "comparable"
    row, = comparison["rows"]
    _assert_the_row(row)
    assert not verifier["control"]["permissions"]["merge"]


def test_check_names_the_same_change(pr):
    args = ["check", "--workspace", str(pr), "--base", "main", "--head", "HEAD"]
    machine = CliRunner().invoke(app, [*args, "--format", "agent-boundary-json"])
    assert machine.exit_code == 0, machine.output
    payload = json.loads(machine.output)
    assert payload["comparison_status"] == "comparable"
    row, = payload["rows"]
    _assert_the_row(row)

    text = CliRunner().invoke(app, [*args, "--format", "text"])
    assert text.exit_code == 0, text.output
    assert "actions/checkout@main" in text.output

    control = CliRunner().invoke(app, [*args, "--format", "agent-control-json"])
    assert control.exit_code == 0, control.output
    block = json.loads(control.output)["capability_rows"]
    assert block["comparison_status"] == "comparable"
    envelope_row, = block["rows"]
    _assert_the_row(envelope_row)


def test_the_stop_hook_does_not_invent_an_interruption_for_the_change(pr, tmp_path):
    from tests.test_install_hooks import _cli_calls, _host_diff_workspace, _run_hook

    payload = _diff(pr)
    assert [row["expands"] for row in payload["rows"]] == [False]

    hooked = tmp_path / "hooked"
    hooked.mkdir()
    _host_diff_workspace(hooked)
    result = _run_hook(hooked, "verify", {}, diff_payload=json.dumps(payload))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert any(call[0] == "diff" for call in _cli_calls(hooked))


# --- review cycle 1: unreadable steps (P2-1) ---------------------------------------


@pytest.mark.parametrize(
    ("steps", "label", "reason"),
    [
        ({"uses": "actions/checkout@v4"}, "steps", "steps_not_a_list"),
        ("actions/checkout@v4", "steps", "steps_not_a_list"),
        (None, "steps", "steps_not_a_list"),
        (["actions/checkout@v4"], "steps[0]", "step_not_a_mapping"),
        ([{"uses": "org/tool@v1"}, ["nested"]], "steps[1]", "step_not_a_mapping"),
    ],
    ids=["mapping", "string", "null", "string-item", "list-item"],
)
def test_steps_that_are_not_a_list_of_mappings_are_listed_as_unresolved(steps, label, reason):
    grant = _workflow_grant(_workflow(jobs={"test": {"steps": steps}}), source=SOURCE)

    entry, = [item for item in grant["step_actions"] if item["step"] == label]
    assert entry == {
        "job": "test", "step": label, "uses": None, "form": "unresolved", "unresolved_reason": reason,
    }


def test_a_job_without_steps_or_with_an_empty_list_declares_none():
    for job in ({"uses": "org/repo/.github/workflows/build.yml@v1"}, {"steps": []}, {"runs-on": "ubuntu-latest"}):
        assert "step_actions" not in _workflow_grant(_workflow(jobs={"test": job}), source=SOURCE)


def test_an_unreadable_steps_shape_appearing_is_a_change_that_publishes_no_text():
    canary = "ghp_" + "U" * 36
    before = _workflow({"uses": "actions/checkout@v4"})
    after = _workflow(jobs={"test": {"steps": {"uses": "actions/checkout@v4", "note": canary}}})

    row, = _rows(before, after)
    assert "test/steps: not a readable step (unresolved: steps not a list)" in row.after
    assert "test/steps[0]: uses actions/checkout@v4" in row.before
    assert not row.expands
    assert canary not in json.dumps(_workflow_grant(after, source=SOURCE))
    assert canary not in row.before + row.after + row.why


def test_an_unreadable_step_reaches_the_published_inventory(tmp_path):
    from agents_shipgate.cli.host_audit import host_audit_inventory

    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text("on: push\njobs:\n  test:\n    steps:\n      uses: actions/checkout@v4\n")

    workflow, = [grant for grant in host_audit_inventory(tmp_path)["grants"] if grant["kind"] == "workflow"]
    assert workflow["step_actions"] == [
        {"job": "test", "step": "steps", "uses": None, "form": "unresolved", "unresolved_reason": "steps_not_a_list"}
    ]


# --- review cycle 1: token shapes and docker userinfo (P2-2) -----------------------

_GITHUB_TOKEN = "ghp_" + "Q" * 36
_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
_SLACK_TOKEN = "xoxb-" + "123456789012-abcdefghij"


_DIGEST = "sha256:" + "0123abcd" * 8
#: A base64 key file as a registry password: ``/`` ``+`` ``=`` inside userinfo (review cycle 2).
_SLASH_PASSWORD_REF = "docker://_json_key:Q2FuYXJ5/U2VjcmV0+base64==@gcr.io/proj/img:1"
_SLASH_PASSWORD_SECRETS = ("_json_key", "Q2FuYXJ5", "U2VjcmV0", "base64==")


@pytest.mark.parametrize(
    ("value", "secrets", "published"),
    [
        (f"org/tool@{_GITHUB_TOKEN}", (_GITHUB_TOKEN,), "org/tool@[REDACTED:github_token]"),
        (f"org/{_AWS_KEY}@v1", (_AWS_KEY,), "org/[REDACTED:aws_access_key]@v1"),
        (f"org/tool@{_SLACK_TOKEN}", (_SLACK_TOKEN,), "org/tool@[REDACTED:slack_token]"),
        (
            "docker://robotuser:HUNTER2CANARYPASS@registry.example.com/team/image:1.0",
            ("robotuser", "HUNTER2CANARYPASS"),
            "docker://<redacted>@registry.example.com/team/image:1.0",
        ),
        (
            "docker://robotuser@registry.example.com/team/image:1.0",
            ("robotuser",),
            "docker://<redacted>@registry.example.com/team/image:1.0",
        ),
        (
            "DOCKER://robotuser:HUNTER2CANARYPASS@registry.example.com",
            ("robotuser", "HUNTER2CANARYPASS"),
            "docker://<redacted>@registry.example.com",
        ),
        (_SLASH_PASSWORD_REF, _SLASH_PASSWORD_SECRETS, "docker://<redacted>@gcr.io/proj/img:1"),
        (
            "docker://robotuser:SLASH1CANARY/SLASH2CANARY@registry.example.com",
            ("robotuser", "SLASH1CANARY", "SLASH2CANARY"),
            "docker://<redacted>@registry.example.com",
        ),
        (
            "docker://robotuser:AT1CANARY@AT2CANARY@registry.example.com/team/image:1.0",
            ("robotuser", "AT1CANARY", "AT2CANARY"),
            "docker://<redacted>@registry.example.com/team/image:1.0",
        ),
        (
            "docker://robotuser:COLON1CANARY:COLON2CANARY@registry.example.com/team/image:1.0",
            ("robotuser", "COLON1CANARY", "COLON2CANARY"),
            "docker://<redacted>@registry.example.com/team/image:1.0",
        ),
        (
            f"docker://robotuser:DIGEST1CANARY/DIGEST2CANARY@registry.example.com/team/image@{_DIGEST}",
            ("robotuser", "DIGEST1CANARY", "DIGEST2CANARY"),
            f"docker://<redacted>@registry.example.com/team/image@{_DIGEST}",
        ),
        (
            "DOCKER://robotuser:UPPER1CANARY/UPPER2CANARY@registry.example.com/img",
            ("robotuser", "UPPER1CANARY", "UPPER2CANARY"),
            "docker://<redacted>@registry.example.com/img",
        ),
        (
            "oci://robotuser:OCI1CANARY/OCI2CANARY@registry.example.com/img:1",
            ("robotuser", "OCI1CANARY", "OCI2CANARY"),
            "oci://<redacted>@registry.example.com/img:1",
        ),
        (
            "https://robotuser:HTTPS1CANARY/HTTPS2CANARY@host.example/org/repo@v1",
            ("robotuser", "HTTPS1CANARY", "HTTPS2CANARY"),
            "https://<invalid-host>/<redacted-path>",
        ),
        (
            "robotuser:BARE1CANARY/BARE2CANARY@org/repo@v1",
            ("robotuser", "BARE1CANARY", "BARE2CANARY"),
            "<redacted>@v1",
        ),
        (
            "org/repo@robotuser:REF1CANARY@v1",
            ("robotuser", "REF1CANARY"),
            "<redacted>@v1",
        ),
    ],
    ids=[
        "github-token", "aws-key", "slack-token", "docker-password", "docker-user", "docker-host-only",
        "docker-base64-key-file", "docker-slash-in-password", "docker-at-in-password",
        "docker-colon-in-password", "docker-userinfo-and-digest", "docker-uppercase-slash-in-password",
        "other-scheme-userinfo", "https-slash-in-password", "schemeless-userinfo", "schemeless-userinfo-in-ref",
    ],
)
def test_token_shapes_and_docker_userinfo_take_the_redacted_path(value, secrets, published):
    grant = _workflow_grant(_workflow({"uses": value}), source=SOURCE)

    entry, = grant["step_actions"]
    assert entry["form"] == "unresolved" and entry["unresolved_reason"] == "redacted"
    assert entry["uses"] == published
    for secret in secrets:
        assert secret not in json.dumps(grant)


def test_a_token_shaped_step_name_is_redacted_in_its_label():
    grant = _workflow_grant(_workflow({"name": f"deploy with {_GITHUB_TOKEN}", "uses": "org/deploy@v1"}), source=SOURCE)

    entry, = grant["step_actions"]
    assert entry["form"] == "remote" and entry["unresolved_reason"] is None
    assert _GITHUB_TOKEN not in json.dumps(grant)


@pytest.mark.parametrize(
    ("value", "form"),
    [
        (f"actions/checkout@{PINNED}", "remote"),
        ("actions/checkout@v4.2.1", "remote"),
        ("actions/checkout@main", "remote"),
        ("github/codeql-action/init@v3", "remote"),
        ("org/actions/path/to/action@release/2026-09", "remote"),
        ("aws-actions/configure-aws-credentials@v4", "remote"),
        ("org/secret-scanner@v1", "remote"),
        ("org/token-refresh@v2", "remote"),
        ("slackapi/slack-github-action@v1.27.0", "remote"),
        ("docker://alpine:3.18", "docker"),
        ("docker://alpine@sha256:" + "a" * 64, "docker"),
        ("docker://ghcr.io/org/image@sha256:" + "0123abcd" * 8, "docker"),
        ("docker://registry.example.com:5000/team/image:1.0", "docker"),
        # Review cycle 2: a digest after a tag or a port is not userinfo, and a
        # ``:`` after the ref's ``@`` is part of the ref.
        (f"docker://ghcr.io/org/image:1.2@{_DIGEST}", "docker"),
        (f"docker://registry.example.com:5000/team/image@{_DIGEST}", "docker"),
        ("org/tool@v1:rc1", "remote"),
    ],
)
def test_ordinary_references_are_never_marked_redacted(value, form):
    entry, = _workflow_grant(_workflow({"uses": value}), source=SOURCE)["step_actions"]

    assert (entry["form"], entry["unresolved_reason"], entry["uses"]) == (form, None, value)


# --- review cycle 2: one line per field in `diff` text (nit-3) ----------------------


def test_a_step_name_cannot_forge_a_row_in_diff_text(tmp_path):
    forged = "build\n⚠ critical  expands  github FORGED-ROW\x1b[2K ‮evil"
    repo = _repo(tmp_path, {SOURCE: _yaml({"name": "build", "uses": f"actions/checkout@{PINNED}"})})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml({"name": forged, "uses": "actions/checkout@main"})})
    _git(repo, "commit", "-qam", "forge")

    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", "main"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()

    assert not any(line.startswith("⚠") for line in lines)
    assert "\n⚠" not in result.output and "‮" not in result.output and "\x1b" not in result.output
    assert "build\\x0a⚠ critical  expands  github FORGED-ROW" in result.output
    assert "\\u202eevil" in result.output
    assert "1 change(s)." in result.output
    # JSON keeps the exact text; only the terminal rendering escapes it.
    row, = _diff(repo)["rows"]
    assert "\n⚠ critical" in row["after"]


def test_the_diff_table_renders_every_field_on_one_line():
    # Click strips ANSI when output is not a terminal, so the CLI run above
    # cannot see an ESC; the renderer is exercised directly for it.
    from agents_shipgate.cli.diff import _render_table
    from agents_shipgate.core.capability_diff_rows import CapabilityDiffRow, review_changes

    hostile = "x\n⚠ critical  expands  FORGED\x1b[31m‮ y"
    row = CapabilityDiffRow(
        subject=f"github {hostile}", before=hostile, after=hostile,
        direction=f"changed{hostile}", why=hostile, severity=f"low{hostile}",
    )
    lines = _render_table(review_changes([row]))

    assert len(lines) == 3 and not any(line.startswith("⚠") for line in lines)
    for raw in ("\n", "\x1b", "‮", " "):
        assert raw not in "".join(lines)
    escaped = "x\\x0a⚠ critical  expands  FORGED\\x1b[31m\\u202e\\u2028y"
    assert lines[0].count(escaped) == 3
    assert lines[1].strip() == f"{escaped} → {escaped}" and lines[2].strip() == escaped


# --- review cycle 2: re-saving over any legacy baseline (nit-1) ---------------------


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_saving_over_a_legacy_baseline_without_a_workflow_is_refused(tmp_path, version):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(json.dumps({"permissions": {"allow": ["Read(**)"]}}))
    _, legacy = _legacy_baseline(tmp_path, version)
    path = tmp_path / ".agents-shipgate" / "host-grants.json"
    path.parent.mkdir()
    original = json.dumps(legacy, indent=2, sort_keys=True) + "\n"
    path.write_text(original)
    audit = ["audit", "--host", "--workspace", str(tmp_path), "--baseline-file", str(path)]

    drift = CliRunner().invoke(app, [*audit, "--drift", "--json"])
    assert drift.exit_code == 0, _output(drift)
    assert (json.loads(drift.stdout)["comparison_status"], json.loads(drift.stdout)["has_drift"]) == (
        "comparable", False,
    )

    refused = CliRunner().invoke(app, [*audit, "--save-baseline"])
    assert refused.exit_code == 2, _output(refused)
    assert "unsupported_baseline_schema" in _output(refused)
    assert path.read_text() == original

    path.rename(path.with_name(f"host-grants.v{version}.json"))
    resaved = CliRunner().invoke(app, [*audit, "--save-baseline"])
    assert resaved.exit_code == 0, _output(resaved)
    assert json.loads(path.read_text())["host_grants_schema_version"] == "0.6"


def _output(result) -> str:
    text = result.output
    try:
        text += result.stderr
    except (AttributeError, ValueError):
        pass
    if result.exception is not None:
        text += repr(result.exception)
    return text


@pytest.mark.parametrize(
    ("reference", "canaries"),
    [
        (f"org/tool@{_GITHUB_TOKEN}", [_GITHUB_TOKEN]),
        (
            "docker://robotuser:HUNTER2CANARYPASS@registry.example.com/team/image:1.0",
            ["HUNTER2CANARYPASS", "robotuser"],
        ),
        (_SLASH_PASSWORD_REF, list(_SLASH_PASSWORD_SECRETS)),
        (
            "docker://robotuser:AT1CANARY@COLON1CANARY:SLASH1CANARY/X@registry.example.com/team/image:1.0",
            ["robotuser", "AT1CANARY", "COLON1CANARY", "SLASH1CANARY"],
        ),
        (
            f"docker://robotuser:DIGEST1CANARY/DIGEST2CANARY@registry.example.com/team/image@{_DIGEST}",
            ["robotuser", "DIGEST1CANARY", "DIGEST2CANARY"],
        ),
    ],
    ids=[
        "github-token", "docker-userinfo", "docker-base64-key-file",
        "docker-at-colon-slash-in-password", "docker-userinfo-and-digest",
    ],
)
def test_no_canary_reaches_json_markdown_baselines_or_errors(tmp_path, reference, canaries):
    repo = _repo(tmp_path, {SOURCE: _yaml({"uses": f"actions/checkout@{PINNED}"})})
    baseline = tmp_path / "baseline.json"
    saved = CliRunner().invoke(
        app, ["audit", "--host", "--workspace", str(repo), "--save-baseline", "--baseline-file", str(baseline)]
    )
    assert saved.exit_code == 0, saved.output
    # Committed on a branch, so manifest-free `verify --head HEAD` reads it too.
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml({"uses": reference})})
    _git(repo, "commit", "-qam", "reference")

    inventory_run = CliRunner().invoke(app, ["audit", "--host", "--workspace", str(repo), "--json"])
    inventory = json.loads(inventory_run.stdout)
    workflow, = [grant for grant in inventory["grants"] if grant["kind"] == "workflow"]
    assert workflow["step_actions"][0]["unresolved_reason"] == "redacted"
    assert any(
        issue["host"] == "github" and issue["kind"] == "unsupported" and issue["blocking"]
        for issue in inventory["issues"]
    )

    refused = tmp_path / "second.json"
    texts = [_output(inventory_run), baseline.read_text()]
    for args in (
        ["audit", "--host", "--workspace", str(repo)],
        ["audit", "--host", "--workspace", str(repo), "--drift", "--baseline-file", str(baseline), "--json"],
        ["audit", "--host", "--workspace", str(repo), "--drift", "--baseline-file", str(baseline)],
        ["audit", "--host", "--workspace", str(repo), "--save-baseline", "--baseline-file", str(refused)],
        ["diff", "--workspace", str(repo), "--base", "main", "--json"],
        ["diff", "--workspace", str(repo), "--base", "main"],
    ):
        texts.append(_output(CliRunner().invoke(app, args)))

    # An incomplete inventory is never acknowledged, so no baseline holds it.
    assert not refused.exists()
    combined = "\n".join(texts)
    for canary in canaries:
        assert canary not in combined


# --- review cycle 1: migrating a committed legacy baseline (P2-4) ------------------


def test_the_documented_migration_from_a_legacy_baseline_holding_a_workflow(tmp_path):
    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import build_host_grants_baseline, host_grants_sha256
    from tests.test_preflight import _workspace

    root = _workspace(tmp_path)
    workflow = root / SOURCE
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(_yaml({"uses": f"actions/checkout@{PINNED}"}))
    legacy = build_host_grants_baseline(host_audit_inventory(root))
    legacy["host_grants_schema_version"] = "0.5"
    for grant in legacy["inventory"]["grants"]:
        if grant["kind"] == "workflow":
            grant.pop("step_actions", None)
    legacy["inventory_sha256"] = host_grants_sha256(legacy["inventory"])
    path = root / ".agents-shipgate" / "host-grants.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(legacy, indent=2, sort_keys=True) + "\n"
    path.write_text(original)
    audit = ["audit", "--host", "--workspace", str(root), "--baseline-file", str(path)]

    drift = CliRunner().invoke(app, [*audit, "--drift", "--json"])
    payload = json.loads(drift.stdout)
    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == ["baseline_workflow_step_actions_unavailable"]
    assert payload["has_drift"] is None and payload["next_action"] is None
    assert CliRunner().invoke(app, [*audit, "--drift", "--fail-on-drift", "--json"]).exit_code == 20

    preflight = CliRunner().invoke(app, ["preflight", "--workspace", str(root), "--json"])
    assert preflight.exit_code == 0, preflight.output
    signal, = [item for item in json.loads(preflight.stdout)["signals"] if item["kind"] == "host_grant_drift"]
    assert (signal["severity"], signal["actor"]) == ("high", "human")
    assert "baseline_workflow_step_actions_unavailable" in json.dumps(signal)

    refused = CliRunner().invoke(app, [*audit, "--save-baseline"])
    assert refused.exit_code != 0
    assert "Refusing to overwrite existing host-grants baseline" in _output(refused)
    assert "unsupported_baseline_schema" in _output(refused)
    assert path.read_text() == original

    # Steps 2-4 of the note: move it aside, re-save, and drift is comparable.
    path.rename(path.with_name("host-grants.v0.5.json"))
    resaved = CliRunner().invoke(app, [*audit, "--save-baseline"])
    assert resaved.exit_code == 0, resaved.output
    assert json.loads(path.read_text())["host_grants_schema_version"] == "0.6"
    after = json.loads(CliRunner().invoke(app, [*audit, "--drift", "--json"]).stdout)
    assert (after["comparison_status"], after["has_drift"]) == ("comparable", False)
    assert path.with_name("host-grants.v0.5.json").read_text() == original
