"""Registry 0.6: scheduled post-approval comparisons as ledger rows (#1119).

``audit --host --drift`` and ``capability diff`` already compare the
repository with its acknowledged state. Their results lived only in workflow
logs, so a team could show a reviewer its monitoring configuration but not a
record that monitoring ran and what it found. ``registry ingest
--observation`` records one run as a ``drift_observation`` row in the same
hash-chained ledger as attestations; ``registry report --drift`` reads a
window of them back.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_lock import (
    diff_capability_locks,
    render_capability_lock_diff_json,
)
from agents_shipgate.core.drift_observation import (
    ObservationError,
    drift_observation,
    drift_report,
    parse_event_time,
)
from agents_shipgate.schemas.registry import (
    REGISTRY_SCHEMA_VERSION,
    RegistryDriftObservationV1,
    RegistryRowV1,
)
from tests.test_capability_definition import POISON, _variant
from tests.test_capability_lock import _lock
from tests.test_registry_ledger import _attestation_v03

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_SCHEMA = REPO_ROOT / "docs" / f"registry-schema.v{REGISTRY_SCHEMA_VERSION}.json"

SECRET_ENV = "s3cr3t-env-value-7f1c"
EVIL_URL = "https://evil.example.com/mcp"
OLD_PACKAGE = "tickets-mcp@1.2.0"
NEW_PACKAGE = "tickets-mcp@1.3.0"
DENY_RULE = "Bash(rm -rf /)"


# --------------------------------------------------------------------------
# Real audit payloads from a scratch repository
# --------------------------------------------------------------------------


def _write_host_config(workspace: Path, *, drifted: bool) -> None:
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)
    servers: dict = {
        "tickets": {
            "command": "npx",
            "args": ["-y", NEW_PACKAGE if drifted else OLD_PACKAGE],
            "env": {"TICKETS_TOKEN": SECRET_ENV},
        }
    }
    if drifted:
        servers["shell"] = {"url": EVIL_URL}
    (workspace / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    permissions = (
        {"allow": ["Bash(*)"], "deny": []}
        if drifted
        else {"allow": ["Bash(git status)"], "deny": [DENY_RULE]}
    )
    (workspace / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": permissions}), encoding="utf-8"
    )


@pytest.fixture
def audited(tmp_path: Path) -> dict[str, Path]:
    """A clean and a drifting ``audit --host --drift --json`` payload."""

    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_host_config(workspace, drifted=False)
    saved = runner.invoke(
        app, ["audit", "--host", "--workspace", str(workspace), "--save-baseline", "--json"]
    )
    assert saved.exit_code == 0, saved.output
    out: dict[str, Path] = {}
    for name, drifted in (("clean", False), ("drift", True)):
        _write_host_config(workspace, drifted=drifted)
        target = tmp_path / f"{name}.json"
        result = runner.invoke(
            app,
            [
                "audit", "--host", "--workspace", str(workspace), "--drift",
                "--json", "--out", str(target),
            ],
        )
        assert result.exit_code == 0, result.output
        out[name] = target
    return out


def _capability_diff(tmp_path: Path, *, moved: bool) -> Path:
    base = _variant()
    head = _variant(description=POISON) if moved else _variant()
    target = tmp_path / ("cap-moved.json" if moved else "cap-same.json")
    target.write_text(
        render_capability_lock_diff_json(diff_capability_locks(_lock([base]), _lock([head]))),
        encoding="utf-8",
    )
    return target


def _ingest(ledger: Path, observation: Path, *extra: str, when: str = "2026-11-02T06:17:00Z"):
    return runner.invoke(
        app,
        [
            "registry", "ingest", "--observation", str(observation),
            "--registry", str(ledger), "--repo", "acme/agent",
            "--event-time", when, "--json", *extra,
        ],
    )


def _rows(ledger: Path) -> list[dict]:
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]


def _report(ledger: Path, *args: str):
    return runner.invoke(
        app, ["registry", "report", "--drift", "--registry", str(ledger), "--json", *args]
    )


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


def test_a_drifting_and_a_clean_audit_each_ingest_as_one_row(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = tmp_path / "registry.jsonl"

    drift = _ingest(ledger, audited["drift"])
    clean = _ingest(ledger, audited["clean"], when="2026-11-03T06:17:00Z")

    assert drift.exit_code == 0, drift.output
    assert clean.exit_code == 0, clean.output
    assert json.loads(drift.output)["observation_status"] == "drift"
    assert json.loads(clean.output)["observation_status"] == "clean"
    first, second = _rows(ledger)
    assert first["row_kind"] == second["row_kind"] == "drift_observation"
    assert first["row_id"].startswith("obs_")
    host = first["observation"]["host"]
    assert host["comparison_status"] == "comparable"
    assert host["has_drift"] is True
    assert host["baseline_file"] == ".agents-shipgate/host-grants.json"
    assert sorted((change["kind"], change["change"]) for change in host["grant_changes"]) == [
        ("mcp_server", "added"),  # the new server
        ("mcp_server", "changed"),  # the bumped package
        ("permission_rule", "added"),  # Bash(*)
        ("permission_rule", "removed"),  # Bash(git status)
        ("permission_rule", "removed"),  # the deny rule
    ]
    assert {change["host"] for change in host["grant_changes"]} == {"claude-code"}
    assert all(change["grant_id"].startswith("host_grant_") for change in host["grant_changes"])
    assert host["expansion_signal_kinds"] == {
        "deny_rule_removed": 1,
        "mcp_server_added": 1,
        "permission_widened": 1,
        "wildcard_allow_added": 1,
    }
    assert second["observation"]["host"]["has_drift"] is False
    assert second["observation"]["host"]["grant_changes"] == []
    # The two rows chain like attestation rows of one repository.
    assert second["previous_row_id"] == first["row_id"]
    # Verdict and release fields stay empty on an observation.
    assert first["merge_verdict"] is None
    assert first["approval"] is None


def test_ingesting_the_same_run_twice_is_idempotent(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = tmp_path / "registry.jsonl"

    first = _ingest(ledger, audited["drift"], "--workflow-run-id", "41")
    again = _ingest(ledger, audited["drift"], "--workflow-run-id", "41")

    assert json.loads(first.output)["status"] == "ingested"
    assert json.loads(again.output) == {**json.loads(first.output), "status": "exists"}
    assert len(_rows(ledger)) == 1


def test_two_runs_that_found_the_same_nothing_are_two_rows(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    """Identical bytes on two days are two runs: the record is that each ran."""

    ledger = tmp_path / "registry.jsonl"

    _ingest(ledger, audited["clean"], when="2026-11-02T06:17:00Z")
    _ingest(ledger, audited["clean"], when="2026-11-03T06:17:00Z")

    assert [row["event_time"] for row in _rows(ledger)] == [
        "2026-11-02T06:17:00Z",
        "2026-11-03T06:17:00Z",
    ]


def test_no_grant_value_command_url_or_env_value_reaches_the_ledger(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    payload = audited["drift"].read_text(encoding="utf-8")
    # The audit payload itself names what changed (its endpoint already
    # without the URL path), which is why it must not be copied.
    for value in ("https://evil.example.com/", NEW_PACKAGE, "Bash(*)", DENY_RULE):
        assert value in payload, value
    ledger = tmp_path / "registry.jsonl"

    assert _ingest(ledger, audited["drift"]).exit_code == 0

    text = ledger.read_text(encoding="utf-8")
    for value in (
        SECRET_ENV,
        EVIL_URL,
        "evil.example.com",
        OLD_PACKAGE,
        NEW_PACKAGE,
        "tickets-mcp",
        "npx",
        "Bash(",
        "rm -rf",
        "git status",
        "TICKETS_TOKEN",
        "shell",
    ):
        assert value not in text, value
    report = _report(ledger).output
    assert EVIL_URL not in report and "Bash(" not in report


def test_an_incomparable_comparison_is_recorded_as_incomparable(tmp_path: Path) -> None:
    payload = {
        "host_grants_schema_version": "0.9",
        "baseline_file": ".agents-shipgate/host-grants.json",
        "scope": "repository",
        "comparison_status": "incomparable",
        "baseline_sha256": None,
        "current_sha256": "a" * 64,
        "has_drift": None,
        "changes": [],
        "artifact_changes": [],
        "coverage_changes": [],
        "expansion_signals": [],
        "issues": [{"host": "claude-code", "message": "unreadable", "blocking": True}],
        "incomparable_reasons": [
            "current_inventory_incomplete",
            "scope_mismatch:repository->local_static",
            "could not read /home/someone/.config/host-grants.json",
        ],
    }
    source = tmp_path / "incomparable.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    ledger = tmp_path / "registry.jsonl"

    result = _ingest(ledger, source)

    assert json.loads(result.output)["observation_status"] == "incomparable"
    (row,) = _rows(ledger)
    host = row["observation"]["host"]
    assert host["issue_count"] == 1
    # Reason codes are kept; a message that names a path is not.
    assert host["incomparable_reasons"] == [
        "current_inventory_incomplete",
        "scope_mismatch:repository->local_static",
        "unreadable_reason",
    ]
    assert "/home/someone" not in ledger.read_text(encoding="utf-8")


def test_an_absolute_baseline_path_is_reduced_to_its_name(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    payload = json.loads(audited["clean"].read_text(encoding="utf-8"))
    payload["baseline_file"] = "/home/someone/acme/.agents-shipgate/host-grants.json"
    source = tmp_path / "absolute.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    ledger = tmp_path / "registry.jsonl"

    _ingest(ledger, source)

    assert _rows(ledger)[0]["observation"]["host"]["baseline_file"] == "host-grants.json"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"host_grants_inventory_schema_version": "0.9", "grants": []}, "not a drift comparison"),
        ({"host_grants_schema_version": "0.9", "inventory": {}}, "not a drift comparison"),
        ({"hello": "world"}, "missing comparison_status"),
        (["not", "an", "object"], "JSON object"),
    ],
)
def test_a_file_that_is_not_a_drift_comparison_is_refused(
    tmp_path: Path, payload, message: str
) -> None:
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    ledger = tmp_path / "registry.jsonl"

    result = _ingest(ledger, source)

    assert result.exit_code == 3
    assert message in result.output
    assert not ledger.exists()


def test_a_drift_payload_whose_status_contradicts_its_changes_is_refused(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    payload = json.loads(audited["drift"].read_text(encoding="utf-8"))
    payload["has_drift"] = False
    source = tmp_path / "edited.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = _ingest(tmp_path / "registry.jsonl", source)

    assert result.exit_code == 3
    assert "does not hold together" in result.output


# --------------------------------------------------------------------------
# Capability comparison
# --------------------------------------------------------------------------


def test_a_capability_diff_from_the_same_run_records_ids_not_facts(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    diff = _capability_diff(tmp_path, moved=True)
    ledger = tmp_path / "registry.jsonl"

    result = _ingest(ledger, audited["clean"], "--capability-diff", str(diff))

    assert result.exit_code == 0, result.output
    # The host grants did not move; the tool definition did.
    assert json.loads(result.output)["observation_status"] == "drift"
    capability = _rows(ledger)[0]["observation"]["capability"]
    source = json.loads(diff.read_text(encoding="utf-8"))
    assert capability["definition_changed_ids"] == [source["definition_changed"][0]["id"]]
    assert capability["summary"]["definition_changed"] == 1
    assert capability["has_drift"] is True
    assert capability["base"]["definition_set_hash"] == source["base"]["definition_set_hash"]
    assert capability["head"]["definition_set_hash"] == source["head"]["definition_set_hash"]
    assert "read ~/.aws/credentials" not in ledger.read_text(encoding="utf-8")


def test_an_unchanged_capability_diff_keeps_a_clean_run_clean(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = tmp_path / "registry.jsonl"

    result = _ingest(
        ledger, audited["clean"], "--capability-diff", str(_capability_diff(tmp_path, moved=False))
    )

    assert json.loads(result.output)["observation_status"] == "clean"
    assert _rows(ledger)[0]["observation"]["capability"]["has_drift"] is False


def test_a_capability_diff_whose_counts_disagree_with_its_rows_is_refused(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    diff = _capability_diff(tmp_path, moved=True)
    payload = json.loads(diff.read_text(encoding="utf-8"))
    payload["summary"]["definition_changed"] = 0
    diff.write_text(json.dumps(payload), encoding="utf-8")

    result = _ingest(tmp_path / "registry.jsonl", audited["clean"], "--capability-diff", str(diff))

    assert result.exit_code == 3
    assert "summary.definition_changed" in result.output


def test_an_unreadable_capability_diff_names_the_diff_not_the_audit(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    diff = _capability_diff(tmp_path, moved=True)
    payload = json.loads(diff.read_text(encoding="utf-8"))
    payload["added"] = {"not": "a list"}
    diff.write_text(json.dumps(payload), encoding="utf-8")

    result = _ingest(tmp_path / "registry.jsonl", audited["clean"], "--capability-diff", str(diff))

    assert result.exit_code == 3
    assert f"Observation error in {diff}" in result.output
    assert "capability diff added must be a list" in result.output


def test_a_different_capability_diff_is_a_different_run(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = tmp_path / "registry.jsonl"
    same = _capability_diff(tmp_path, moved=False)
    moved = _capability_diff(tmp_path, moved=True)

    _ingest(ledger, audited["clean"], "--capability-diff", str(same))
    second = _ingest(ledger, audited["clean"], "--capability-diff", str(moved))

    assert json.loads(second.output)["status"] == "ingested"
    assert len(_rows(ledger)) == 2


# --------------------------------------------------------------------------
# Flags and context
# --------------------------------------------------------------------------


def test_the_event_time_is_required_and_stored_in_utc(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = tmp_path / "registry.jsonl"
    missing = runner.invoke(
        app,
        ["registry", "ingest", "--observation", str(audited["clean"]), "--registry", str(ledger)],
    )
    assert missing.exit_code == 2
    assert "--event-time" in missing.output

    naive = _ingest(ledger, audited["clean"], when="2026-11-02T06:17:00")
    assert naive.exit_code == 2
    assert "no UTC offset" in naive.output

    _ingest(ledger, audited["clean"], when="2026-11-02T07:17:30.250+01:00")
    assert _rows(ledger)[0]["event_time"] == "2026-11-02T06:17:30Z"


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--attestation", "a.json", "--observation", "b.json"],
    ],
)
def test_exactly_one_input_kind(tmp_path: Path, args: list[str]) -> None:
    result = runner.invoke(
        app, ["registry", "ingest", "--registry", str(tmp_path / "r.jsonl"), *args]
    )

    assert result.exit_code == 2
    assert "exactly one of --attestation" in result.output


def test_observation_flags_are_refused_with_an_attestation(tmp_path: Path) -> None:
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps(_attestation_v03()), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "registry", "ingest", "--attestation", str(attestation),
            "--registry", str(tmp_path / "r.jsonl"), "--event-time", "2026-11-02T00:00:00Z",
        ],
    )

    assert result.exit_code == 2
    assert "--event-time only apply to --observation" in result.output


def test_the_ci_context_names_the_run_and_explicit_flags_win(
    tmp_path: Path, audited: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/from-env")
    monkeypatch.setenv("GITHUB_RUN_ID", "9001")
    monkeypatch.setenv("GITHUB_SHA", "e" * 40)
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    ledger = tmp_path / "registry.jsonl"

    result = runner.invoke(
        app,
        [
            "registry", "ingest", "--observation", str(audited["clean"]),
            "--registry", str(ledger), "--ci-context", "github-actions",
            "--event-time", "2026-11-02T06:17:00Z", "--head-sha", "f" * 40,
        ],
    )

    assert result.exit_code == 0, result.output
    (row,) = _rows(ledger)
    assert row["repo"] == "acme/from-env"
    assert row["workflow_run_id"] == "9001"
    assert row["branch"] == "main"
    assert row["head_sha"] == "f" * 40
    assert row["observation"]["trigger"] == "schedule"


# --------------------------------------------------------------------------
# One ledger, two row kinds
# --------------------------------------------------------------------------


def _mixed_ledger(tmp_path: Path, audited: dict[str, Path]) -> Path:
    ledger = tmp_path / "registry.jsonl"
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps(_attestation_v03(repo="acme/agent")), encoding="utf-8")
    assert runner.invoke(
        app, ["registry", "ingest", "--attestation", str(attestation), "--registry", str(ledger)]
    ).exit_code == 0
    assert _ingest(ledger, audited["drift"]).exit_code == 0
    later = tmp_path / "attestation-2.json"
    later.write_text(
        json.dumps({**_attestation_v03(repo="acme/agent"), "run_id": "sha256:" + "5" * 64}),
        encoding="utf-8",
    )
    assert runner.invoke(
        app, ["registry", "ingest", "--attestation", str(later), "--registry", str(ledger)]
    ).exit_code == 0
    assert _ingest(ledger, audited["clean"], when="2026-11-03T06:17:00Z").exit_code == 0
    return ledger


def test_verify_checks_one_chain_across_both_row_kinds(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = _mixed_ledger(tmp_path, audited)
    rows = _rows(ledger)
    assert [row["row_kind"] for row in rows] == [
        "attestation",
        "drift_observation",
        "attestation",
        "drift_observation",
    ]
    assert [row["previous_row_id"] for row in rows] == [None] + [
        row["row_id"] for row in rows[:-1]
    ]

    verify = runner.invoke(app, ["registry", "verify", "--registry", str(ledger), "--json"])
    assert json.loads(verify.output)["ok"] is True

    # Editing what an observation found breaks its id and the next link.
    rows[1]["observation"]["host"]["expansion_signal_kinds"] = {}
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    tampered = json.loads(
        runner.invoke(app, ["registry", "verify", "--registry", str(ledger), "--json"]).output
    )
    assert {issue["kind"] for issue in tampered["issues"]} == {
        "row_id_mismatch",
        "previous_row_sha256_mismatch",
    }


def test_an_attestation_row_hashes_as_it_did_before_row_kinds(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    """``row_kind: attestation`` and ``observation: null`` are left out of the
    hashes, so a row written by registry 0.5 keeps its id."""

    ledger = _mixed_ledger(tmp_path, audited)
    first = _rows(ledger)[0]
    released = {key: value for key, value in first.items() if key not in {"row_kind", "observation"}}

    assert RegistryRowV1.model_validate(released).row_kind == "attestation"
    from agents_shipgate.cli.registry import _row_id

    assert _row_id(RegistryRowV1.model_validate(released)) == first["row_id"]


def test_an_observation_is_never_a_bypass_candidate(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = _mixed_ledger(tmp_path, audited)

    bypass = json.loads(
        runner.invoke(
            app, ["registry", "report", "--bypass", "--registry", str(ledger), "--json"]
        ).output
    )
    summary = json.loads(
        runner.invoke(app, ["registry", "summary", "--registry", str(ledger), "--json"]).output
    )

    # Both attestations are blocked and unacknowledged; neither observation
    # has a verdict, and neither is counted.
    assert bypass["bypass_count"] == 2
    assert {row["row_kind"] for row in bypass["rows"]} == {"attestation"}
    assert summary["count"] == 4
    assert summary["by_row_kind"] == {"attestation": 2, "drift_observation": 2}
    assert summary["by_observation_status"] == {"clean": 1, "drift": 1}
    assert summary["bypass_count"] == 2
    assert summary["by_merge_verdict"] == {"blocked": 2}


def test_query_output_validates_against_the_published_schema(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = _mixed_ledger(tmp_path, audited)
    diff = _capability_diff(tmp_path, moved=True)
    _ingest(ledger, audited["clean"], "--capability-diff", str(diff), when="2026-11-04T06:17:00Z")

    result = runner.invoke(app, ["registry", "query", "--registry", str(ledger), "--json"])

    payload = json.loads(result.output)
    jsonschema.validate(payload, json.loads(REGISTRY_SCHEMA.read_text(encoding="utf-8")))
    assert payload["count"] == 5


def test_a_hand_edited_observation_status_is_a_malformed_row(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = tmp_path / "registry.jsonl"
    _ingest(ledger, audited["drift"])
    (row,) = _rows(ledger)
    row["observation"]["status"] = "clean"
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")

    report = json.loads(_report(ledger).output)

    assert report["observation_count"] == 0
    assert report["skipped_count"] == 1
    with pytest.raises(ValidationError):
        RegistryDriftObservationV1.model_validate(row["observation"])


# --------------------------------------------------------------------------
# registry report --drift
# --------------------------------------------------------------------------


def _ledger_of(
    tmp_path: Path, audited: dict[str, Path], runs: list[tuple[str, str, str]]
) -> Path:
    """``runs``: (repo, event_time, clean|drift)."""

    ledger = tmp_path / "registry.jsonl"
    for repo, when, kind in runs:
        result = runner.invoke(
            app,
            [
                "registry", "ingest", "--observation", str(audited[kind]),
                "--registry", str(ledger), "--repo", repo, "--event-time", when,
            ],
        )
        assert result.exit_code == 0, result.output
    return ledger


def test_the_report_counts_a_window_of_runs(tmp_path: Path, audited: dict[str, Path]) -> None:
    ledger = _ledger_of(
        tmp_path,
        audited,
        [
            ("acme/agent", "2026-10-31T06:17:00Z", "drift"),  # before the window
            ("acme/agent", "2026-11-01T06:17:00Z", "clean"),
            ("acme/agent", "2026-11-02T06:20:00Z", "drift"),
            ("acme/agent", "2026-11-03T06:15:00Z", "clean"),
            ("acme/agent", "2026-11-04T00:00:00Z", "clean"),  # after the window
        ],
    )

    payload = json.loads(_report(ledger, "--since", "2026-11-01", "--until", "2026-11-03").output)

    assert payload["observation_count"] == 3
    assert (payload["clean_count"], payload["drift_count"], payload["incomparable_count"]) == (
        2,
        1,
        0,
    )
    assert payload["host_drift_count"] == 1
    assert [row["event_time"] for row in payload["rows"]] == [
        "2026-11-01T06:17:00Z",
        "2026-11-02T06:20:00Z",
        "2026-11-03T06:15:00Z",
    ]
    (repo,) = payload["repos"]
    assert repo["first_observed_at"] == "2026-11-01T06:17:00Z"
    assert repo["last_observed_at"] == "2026-11-03T06:15:00Z"


def test_a_gap_is_a_stretch_longer_than_the_expected_interval(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = _ledger_of(
        tmp_path,
        audited,
        [
            ("acme/agent", "2026-11-02T06:00:00Z", "clean"),
            # Exactly two days later: not longer than the interval.
            ("acme/agent", "2026-11-04T06:00:00Z", "clean"),
            # Two days and one second later: a gap.
            ("acme/agent", "2026-11-06T06:00:01Z", "clean"),
        ],
    )

    payload = json.loads(
        _report(
            ledger,
            "--since", "2026-11-01", "--until", "2026-11-09", "--expected-interval-days", "2",
        ).output
    )

    rows = {row["event_time"]: row["row_id"] for row in payload["rows"]}
    assert payload["gap_count"] == 2
    between, after_last = payload["gaps"]
    assert between == {
        "repo": "acme/agent",
        "start": "2026-11-04T06:00:00Z",
        "end": "2026-11-06T06:00:01Z",
        "duration_seconds": 2 * 86400 + 1,
        "after_row_id": rows["2026-11-04T06:00:00Z"],
        "before_row_id": rows["2026-11-06T06:00:01Z"],
    }
    # The window closes at the end of --until: the stretch after the last run.
    assert after_last["start"] == "2026-11-06T06:00:01Z"
    assert after_last["end"] == "2026-11-10T00:00:00Z"
    assert after_last["before_row_id"] is None
    # The window opens at the start of --since: 30 hours is within two days.
    assert all(gap["after_row_id"] is not None for gap in payload["gaps"])


def test_a_repository_with_no_run_in_the_window_is_one_gap(
    tmp_path: Path, audited: dict[str, Path]
) -> None:
    ledger = _ledger_of(
        tmp_path,
        audited,
        [
            ("acme/agent", "2026-11-02T06:00:00Z", "clean"),
            ("acme/quiet", "2026-09-01T06:00:00Z", "clean"),
        ],
    )

    payload = json.loads(
        _report(
            ledger,
            "--since", "2026-11-01", "--until", "2026-11-03", "--expected-interval-days", "2",
        ).output
    )

    quiet = [gap for gap in payload["gaps"] if gap["repo"] == "acme/quiet"]
    assert quiet == [
        {
            "repo": "acme/quiet",
            "start": "2026-11-01T00:00:00Z",
            "end": "2026-11-04T00:00:00Z",
            "duration_seconds": 3 * 86400,
            "after_row_id": None,
            "before_row_id": None,
        }
    ]
    assert [gap["repo"] for gap in payload["gaps"]] == ["acme/quiet"]
    by_repo = {item["repo"]: item for item in payload["repos"]}
    assert by_repo["acme/quiet"]["observation_count"] == 0


def test_repo_filters_the_report(tmp_path: Path, audited: dict[str, Path]) -> None:
    ledger = _ledger_of(
        tmp_path,
        audited,
        [
            ("acme/agent", "2026-11-02T06:00:00Z", "drift"),
            ("acme/other", "2026-11-02T06:00:00Z", "clean"),
        ],
    )

    payload = json.loads(_report(ledger, "--repo", "acme/other").output)

    assert payload["observation_count"] == 1
    assert payload["drift_count"] == 0
    assert [item["repo"] for item in payload["repos"]] == ["acme/other"]


@pytest.mark.parametrize(
    ("runs", "args", "exit_code"),
    [
        ([("acme/agent", "2026-11-02T06:00:00Z", "clean")], [], 0),
        ([("acme/agent", "2026-11-02T06:00:00Z", "drift")], [], 20),
        (
            [("acme/agent", "2026-11-01T06:00:00Z", "clean")],
            ["--since", "2026-11-01", "--until", "2026-11-05", "--expected-interval-days", "2"],
            20,
        ),
        (
            [("acme/agent", "2026-11-01T06:00:00Z", "clean")],
            ["--since", "2026-11-01", "--until", "2026-11-01", "--expected-interval-days", "2"],
            0,
        ),
    ],
)
def test_fail_on_drift_exits_20_on_drift_or_a_gap(
    tmp_path: Path, audited: dict[str, Path], runs, args, exit_code: int
) -> None:
    ledger = _ledger_of(tmp_path, audited, runs)

    result = _report(ledger, "--fail-on-drift", *args)

    assert result.exit_code == exit_code, result.output
    without = _report(ledger, *args)
    assert without.exit_code == 0


def test_fail_on_drift_exits_20_on_an_incomparable_run(tmp_path: Path) -> None:
    row = _observation_row(
        {
            "host_grants_schema_version": "0.9",
            "baseline_file": "b.json",
            "comparison_status": "incomparable",
            "has_drift": None,
            "incomparable_reasons": ["current_inventory_incomplete"],
        },
        when="2026-11-02T06:00:00Z",
    )
    ledger = tmp_path / "registry.jsonl"
    ledger.write_text(json.dumps(row.model_dump(mode="json")) + "\n", encoding="utf-8")

    assert _report(ledger, "--fail-on-drift").exit_code == 20


def test_an_observation_without_a_readable_time_is_undated(tmp_path: Path) -> None:
    clean = {
        "host_grants_schema_version": "0.9",
        "baseline_file": "b.json",
        "comparison_status": "comparable",
        "has_drift": False,
    }
    rows = [
        _observation_row(clean, when="2026-11-02T06:00:00Z"),
        _observation_row(clean, when="yesterday"),
    ]
    ledger = tmp_path / "registry.jsonl"
    ledger.write_text(
        "".join(json.dumps(row.model_dump(mode="json")) + "\n" for row in rows), encoding="utf-8"
    )

    unwindowed = json.loads(_report(ledger).output)
    windowed = json.loads(_report(ledger, "--since", "2026-11-01").output)

    assert (unwindowed["observation_count"], unwindowed["undated_count"]) == (2, 1)
    assert (windowed["observation_count"], windowed["undated_count"]) == (1, 1)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--since", "2026-11-05", "--until", "2026-11-01"], "is after --until"),
        (["--since", "11/05/2026"], "YYYY-MM-DD"),
        (["--expected-interval-days", "0"], "--expected-interval-days"),
        (["--fail-on-bypass"], "--fail-on-bypass only applies to --bypass"),
    ],
)
def test_report_flags_are_checked(tmp_path: Path, args: list[str], message: str) -> None:
    result = _report(tmp_path / "registry.jsonl", *args)

    assert result.exit_code == 2
    assert message in result.output


def test_report_needs_exactly_one_kind_and_drift_flags_need_drift(tmp_path: Path) -> None:
    ledger = str(tmp_path / "registry.jsonl")

    neither = runner.invoke(app, ["registry", "report", "--registry", ledger])
    both = runner.invoke(app, ["registry", "report", "--registry", ledger, "--bypass", "--drift"])
    misplaced = runner.invoke(
        app, ["registry", "report", "--registry", ledger, "--bypass", "--since", "2026-11-01"]
    )

    assert neither.exit_code == both.exit_code == misplaced.exit_code == 2
    assert "pass --bypass or --drift" in neither.output
    assert "exactly one of --bypass or --drift" in both.output
    assert "--since only apply to --drift" in misplaced.output


def test_the_report_text_names_drift_and_gaps(tmp_path: Path, audited: dict[str, Path]) -> None:
    ledger = _ledger_of(
        tmp_path,
        audited,
        [
            ("acme/agent", "2026-11-01T06:00:00Z", "drift"),
            ("acme/agent", "2026-11-05T06:00:00Z", "clean"),
        ],
    )

    result = runner.invoke(
        app,
        [
            "registry", "report", "--drift", "--registry", str(ledger),
            "--since", "2026-11-01", "--until", "2026-11-05", "--expected-interval-days", "2",
        ],
    )

    assert result.exit_code == 0
    assert "2 drift observation(s)" in result.output
    assert "1 with drift, 0 incomparable, 1 clean" in result.output
    assert "drift: host 5 grant/2 artifact change(s)" in result.output
    assert "gap repo=acme/agent 2026-11-01T06:00:00Z → 2026-11-05T06:00:00Z (4.0 days)" in (
        result.output
    )


# --------------------------------------------------------------------------
# Pure functions
# --------------------------------------------------------------------------


def _observation_row(host_drift: dict, *, when: str, repo: str = "acme/agent") -> RegistryRowV1:
    from agents_shipgate.cli.registry import _row_from_observation

    return _row_from_observation(
        drift_observation(host_drift, source_sha256="0" * 64),
        repo=repo,
        event_time=when,
        workflow_run_id=None,
        head_sha=None,
        branch=None,
        previous_row=None,
    )


def test_parse_event_time_normalizes_and_refuses() -> None:
    assert parse_event_time("2026-11-02T06:17:00Z") == "2026-11-02T06:17:00Z"
    assert parse_event_time(" 2026-11-01T22:17:00-08:00 ") == "2026-11-02T06:17:00Z"
    for bad in ("2026-11-02", "2026-11-02T06:17:00", "soon"):
        with pytest.raises(ObservationError):
            parse_event_time(bad)


def test_drift_report_is_deterministic_and_order_independent() -> None:
    clean = {
        "host_grants_schema_version": "0.9",
        "baseline_file": "b.json",
        "comparison_status": "comparable",
        "has_drift": False,
    }
    rows = [
        _observation_row(clean, when=when, repo=repo)
        for repo, when in (
            ("b", "2026-11-03T00:00:00Z"),
            ("a", "2026-11-01T00:00:00Z"),
            ("a", "2026-11-04T00:00:00Z"),
        )
    ]
    args = {
        "registry": "r",
        "skipped": [],
        "since": date(2026, 11, 1),
        "until": date(2026, 11, 4),
        "expected_interval_days": 1,
    }

    forward = drift_report(rows=rows, **args)
    backward = drift_report(rows=list(reversed(rows)), **args)

    assert forward == backward
    assert [(gap.repo, gap.start, gap.end) for gap in forward.gaps] == [
        ("a", "2026-11-01T00:00:00Z", "2026-11-04T00:00:00Z"),
        ("b", "2026-11-01T00:00:00Z", "2026-11-03T00:00:00Z"),
        ("b", "2026-11-03T00:00:00Z", "2026-11-05T00:00:00Z"),
    ]
