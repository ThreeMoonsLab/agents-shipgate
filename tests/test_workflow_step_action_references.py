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
