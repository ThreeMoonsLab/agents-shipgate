"""``registry report --control-map``: an audit-window evidence extract (#1121).

A team preparing an assessment turned the ledger into evidence by hand, and a
hand-made summary cannot be checked against the ledger it came from. The view
arranges the ledger's rows by a control map (data), prints every gap, and
writes the extract that recomputes it. It never says a control is met.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.registry import _row_from_attestation
from agents_shipgate.core.control_evidence import ControlMapError, load_control_map
from tests.test_registry_drift_observation import _observation_row
from tests.test_registry_ledger import _attestation_v03

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
AIUC_MAP = REPO_ROOT / "docs/control-maps/aiuc-1.yaml"
EVIDENCE_SCHEMA = json.loads(
    (REPO_ROOT / "docs/control-evidence-schema.v0.1.json").read_text(encoding="utf-8")
)
MAP_SCHEMA = json.loads(
    (REPO_ROOT / "docs/control-map-schema.v0.1.json").read_text(encoding="utf-8")
)
WINDOW = ("--since", "2026-11-01", "--until", "2026-11-07")

CLEAN = {
    "host_grants_schema_version": "0.9",
    "baseline_file": ".agents-shipgate/host-grants.json",
    "comparison_status": "comparable",
    "has_drift": False,
}
DRIFT = {
    **CLEAN,
    "has_drift": True,
    "changes": [
        {"grant_id": "host_grant_1", "baseline": None, "current": {"kind": "mcp_server"}}
    ],
}
INCOMPARABLE = {
    **CLEAN,
    "comparison_status": "incomparable",
    "has_drift": None,
    "incomparable_reasons": ["current_inventory_incomplete"],
    "issues": [{"host": "claude-code", "blocking": True}],
}
CAPABILITY_DIFF = {
    "capability_lock_diff_schema_version": "0.10",
    "base": {"definition_set_hash": "1" * 16},
    "head": {"definition_set_hash": "2" * 16},
    "summary": {"unchanged": 4},
}


def _observation(payload: dict, when: str, *, repo: str = "acme/agent", capability: bool = False):
    from agents_shipgate.core.drift_observation import drift_observation

    row = _observation_row(payload, when=when, repo=repo)
    if capability:
        observation = drift_observation(
            payload,
            source_sha256="0" * 64,
            capability_diff=CAPABILITY_DIFF,
            capability_diff_sha256="1" * 64,
        )
        row = row.model_copy(update={"observation": observation})
    return row


def _attestation_row(
    when: str,
    *,
    approval: str | None,
    definition: bool = True,
    repo: str = "acme/agent",
    run: str = "1",
):
    attestation = _attestation_v03(repo=repo)
    attestation["event_time"] = when
    attestation["run_id"] = "sha256:" + run * 64
    attestation["capability_lock"] = {
        "definition_set_hash": "3" * 16 if definition else None,
        "semantic_capability_set_hash": "4" * 16,
    }
    if approval is not None:
        attestation["approval"] = {"status": approval}
    return _row_from_attestation(attestation, repo=repo, source_attestation_sha256=run * 64)


def _write(path: Path, rows) -> Path:
    path.write_text(
        "".join(json.dumps(row.model_dump(mode="json"), sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _mixed_ledger(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "registry.jsonl",
        [
            _observation(CLEAN, "2026-10-28T06:00:00Z"),  # before the window
            _observation(CLEAN, "2026-11-01T06:00:00Z", capability=True),
            _observation(DRIFT, "2026-11-02T06:00:00Z"),
            _observation(INCOMPARABLE, "2026-11-06T06:00:00Z"),
            _observation(CLEAN, "2026-09-01T06:00:00Z", repo="acme/quiet"),
            _attestation_row("2026-11-02T12:00:00Z", approval="approved", run="1"),
            _attestation_row("2026-11-03T12:00:00Z", approval=None, run="2"),
            _attestation_row(
                "2026-11-04T12:00:00Z", approval="changes_requested", definition=False, run="3"
            ),
            _attestation_row("2026-11-20T12:00:00Z", approval="approved", run="4"),  # after
        ],
    )


def _report(ledger: Path, *args: str, map_path: Path = AIUC_MAP):
    return runner.invoke(
        app,
        [
            "registry", "report", "--control-map", str(map_path),
            "--registry", str(ledger), *WINDOW, *args,
        ],
    )


def _view(ledger: Path, *args: str, map_path: Path = AIUC_MAP) -> dict:
    result = _report(ledger, "--json", *args, map_path=map_path)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _control(view: dict, control_id: str) -> dict:
    return next(control for control in view["controls"] if control["id"] == control_id)


def _gap(item: dict, rule: str) -> dict:
    return next(gap for gap in item["gaps"] if gap["rule"] == rule)


# --------------------------------------------------------------------------
# The AIUC-1 map
# --------------------------------------------------------------------------


def test_every_aiuc_item_cites_rows_or_says_it_is_not_evidenced() -> None:
    control_map, _ = load_control_map(AIUC_MAP)
    jsonschema.validate(
        __import__("yaml").safe_load(AIUC_MAP.read_text(encoding="utf-8")), MAP_SCHEMA
    )

    assert [control.id for control in control_map.controls] == [
        "B006.1",
        "B006.2",
        "B006.3",
        "E004.1",
        "E004.2",
    ]
    for control in control_map.controls:
        if control.evidence:
            assert all(item.gaps for item in control.evidence), control.id
            assert all("no_rows_in_window" in item.gaps for item in control.evidence)
        else:
            assert control.shows.startswith("Not evidenced by this ledger"), control.id
    assert control_map.source.startswith("https://")


# --------------------------------------------------------------------------
# Counts and gaps
# --------------------------------------------------------------------------


def test_counts_and_gaps_on_a_mixed_ledger(tmp_path: Path) -> None:
    rows = [json.loads(line) for line in _mixed_ledger(tmp_path).read_text().splitlines()]
    ids = [row["row_id"] for row in rows]
    view = _view(tmp_path / "registry.jsonl", "--expected-interval-days", "2")

    jsonschema.validate(view, EVIDENCE_SCHEMA)
    (hosts,) = _control(view, "B006.1")["evidence"]
    assert hosts["row_count"] == 3
    assert hosts["counts"] == {"clean": 1, "drift": 1, "incomparable": 1}
    assert hosts["row_ids"] == sorted(ids[1:4])
    assert _gap(hosts, "no_rows_in_window")["status"] == "none"
    assert _gap(hosts, "observation_incomparable")["row_ids"] == [ids[3]]
    assert _gap(hosts, "inventory_issues")["row_ids"] == [ids[3]]
    interval = _gap(hosts, "observation_interval")
    # acme/agent: Nov 2 to Nov 6 is four days; acme/quiet: no run in the window.
    assert [(gap["repo"], gap["start"], gap["end"]) for gap in interval["stretches"]] == [
        ("acme/agent", "2026-11-02T06:00:00Z", "2026-11-06T06:00:00Z"),
        ("acme/quiet", "2026-11-01T00:00:00Z", "2026-11-08T00:00:00Z"),
    ]
    assert interval["count"] == 2

    attestations, observations = _control(view, "B006.3")["evidence"]
    assert attestations["row_count"] == 3
    assert _gap(attestations, "definition_digests_absent")["row_ids"] == [ids[7]]
    assert _gap(observations, "capability_comparison_absent")["row_ids"] == sorted(ids[2:4])

    (approvals,) = _control(view, "E004.1")["evidence"]
    assert approvals["counts"] == {
        "approval.approved": 1,
        "approval.not_recorded": 1,
        "approval.changes_requested": 1,
    }
    assert _gap(approvals, "approval_not_supplied")["row_ids"] == [ids[6]]
    assert _gap(approvals, "approval_not_approved")["row_ids"] == [ids[7]]

    for not_covered in ("B006.2", "E004.2"):
        control = _control(view, not_covered)
        assert control["evidence"] == [] and control["gap_count"] == 0
    assert _control(view, "B006.1")["gap_count"] == 4


def test_a_ledger_with_no_observations_prints_the_gap(tmp_path: Path) -> None:
    ledger = _write(
        tmp_path / "registry.jsonl",
        [_attestation_row("2026-11-02T12:00:00Z", approval="approved")],
    )

    view = _view(ledger)

    (hosts,) = _control(view, "B006.1")["evidence"]
    assert _gap(hosts, "no_rows_in_window") == {
        "rule": "no_rows_in_window",
        "status": "found",
        "count": 1,
        "row_ids": [],
        "stretches": [],
        "note": "no drift_observation row of the window",
    }
    _, observations = _control(view, "B006.3")["evidence"]
    assert _gap(observations, "no_rows_in_window")["status"] == "found"
    markdown = _report(ledger).output
    assert "B006.3" in markdown


def test_the_interval_is_not_checked_without_the_expected_interval(tmp_path: Path) -> None:
    view = _view(_mixed_ledger(tmp_path))

    (hosts,) = _control(view, "B006.1")["evidence"]
    assert _gap(hosts, "observation_interval") == {
        "rule": "observation_interval",
        "status": "not_checked",
        "count": 0,
        "row_ids": [],
        "stretches": [],
        "note": "pass --expected-interval-days to check the interval",
    }


def test_undated_rows_are_counted_and_placed_in_no_window(tmp_path: Path) -> None:
    ledger = _write(
        tmp_path / "registry.jsonl",
        [
            _observation(CLEAN, "2026-11-02T06:00:00Z"),
            _observation(CLEAN, "sometime"),
        ],
    )

    (hosts,) = _control(_view(ledger), "B006.1")["evidence"]

    assert hosts["row_count"] == 1
    assert hosts["counts"]["undated"] == 1


def test_repo_narrows_every_item(tmp_path: Path) -> None:
    view = _view(_mixed_ledger(tmp_path), "--repo", "acme/quiet")

    (hosts,) = _control(view, "B006.1")["evidence"]
    assert hosts["row_count"] == 0
    assert _gap(hosts, "no_rows_in_window")["status"] == "found"
    assert view["repo"] == "acme/quiet"


# --------------------------------------------------------------------------
# The extract
# --------------------------------------------------------------------------


def test_the_extract_recomputes_the_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _mixed_ledger(tmp_path)
    out = tmp_path / "evidence"

    first = _report(ledger, "--expected-interval-days", "2", "--out", str(out), "--json")
    assert first.exit_code == 0, first.output
    view = json.loads(first.output)
    extract = (out / "ledger-extract.jsonl").read_text(encoding="utf-8")

    # Byte for byte: every extract line is a line of the ledger.
    ledger_lines = set(ledger.read_text(encoding="utf-8").splitlines())
    assert extract and all(line in ledger_lines for line in extract.splitlines())
    # The silent repository's last observation rides along, the rows outside
    # the window that no rule needs do not.
    assert len(view["anchor_row_ids"]) == 1
    assert view["extract"]["row_count"] == len(extract.splitlines()) == view["cited_row_count"] + 1
    assert json.loads((out / "control-evidence.json").read_text(encoding="utf-8")) == view

    monkeypatch.chdir(out)
    command = shlex.split(view["recompute"])
    assert command[:4] == ["agents-shipgate", "registry", "report", "--control-map"]
    again = runner.invoke(app, command[1:])
    assert again.exit_code == 0, again.output
    assert json.loads(again.output)["controls"] == view["controls"]


def test_two_runs_write_the_same_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _mixed_ledger(tmp_path)
    outputs = []
    for name in ("one", "two"):
        workdir = tmp_path / name
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        result = _report(ledger, "--expected-interval-days", "2", "--out", "out")
        assert result.exit_code == 0, result.output
        outputs.append(
            {
                filename: (workdir / "out" / filename).read_bytes()
                for filename in (
                    "control-evidence.json",
                    "control-evidence.md",
                    "ledger-extract.jsonl",
                )
            }
        )

    assert outputs[0] == outputs[1]


def test_no_word_asserts_compliance(tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    assert _report(
        _mixed_ledger(tmp_path), "--expected-interval-days", "2", "--out", str(out)
    ).exit_code == 0

    claim = re.compile(
        r"\b(complian\w*|complies|conform\w*|certif\w*|satisf\w*|passe[sd]|"
        r"is met|are met|fulfil\w*|attests? that)\b",
        re.IGNORECASE,
    )
    for name in ("control-evidence.md", "control-evidence.json"):
        text = (out / name).read_text(encoding="utf-8")
        assert not claim.search(text), (name, claim.search(text))
    assert not claim.search(AIUC_MAP.read_text(encoding="utf-8"))


def test_the_markdown_names_every_item_and_gap(tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    _report(_mixed_ledger(tmp_path), "--expected-interval-days", "2", "--out", str(out))

    markdown = (out / "control-evidence.md").read_text(encoding="utf-8")

    for control_id in ("B006.1", "B006.2", "B006.3", "E004.1", "E004.2"):
        assert f"## {control_id}" in markdown
    assert "not evidenced by this ledger" in markdown
    assert "`observation_interval`: 2 stretch(es) with no observation" in markdown
    assert "`approval_not_approved`: 1 row(s)" in markdown
    assert "agents-shipgate registry verify --registry" in markdown


# --------------------------------------------------------------------------
# Inputs and flags
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("controls: [", "not valid YAML"),
        ("- a list", "must be a YAML mapping"),
        (
            "control_map_schema_version: '0.1'\nframework: x\nframework_label: x\n"
            "source: x\nnote: x\ncontrols:\n- id: A\n  title: t\n  status: s\n  shows: s\n"
            "  evidence:\n  - label: l\n    row_kind: attestation\n    gaps: [observation_interval]\n",
            "reads drift_observation rows",
        ),
        (
            "control_map_schema_version: '0.1'\nframework: x\nframework_label: x\n"
            "source: x\nnote: x\ncontrols:\n- id: A\n  title: t\n  status: s\n  shows: s\n"
            "- id: A\n  title: t\n  status: s\n  shows: s\n",
            "appear more than once",
        ),
        (
            "control_map_schema_version: '0.1'\nframework: x\nframework_label: x\n"
            "source: x\nnote: x\ncontrols:\n- id: A\n  title: t\n  status: s\n  shows: s\n"
            "  evidence:\n  - label: l\n    row_kind: attestation\n    gaps: [is_compliant]\n",
            "controls.0.evidence.0.gaps.0",
        ),
    ],
)
def test_a_map_that_cannot_be_read_is_refused(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "map.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ControlMapError, match=re.escape(message)):
        load_control_map(path)
    result = _report(tmp_path / "registry.jsonl", map_path=path)
    assert result.exit_code == 2
    assert message in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--control-map", str(AIUC_MAP)], "needs --since and --until"),
        (["--control-map", str(AIUC_MAP), *WINDOW, "--fail-on-drift"], "do not apply"),
        (["--control-map", str(AIUC_MAP), "--drift", *WINDOW], "exactly one of"),
        (["--drift", "--out", "x"], "--out only applies to --control-map"),
        (["--control-map", str(AIUC_MAP), "--since", "2026-11-07", "--until", "2026-11-01"], "after"),
    ],
)
def test_report_flags_for_the_control_map(tmp_path: Path, args: list[str], message: str) -> None:
    result = runner.invoke(
        app, ["registry", "report", "--registry", str(tmp_path / "r.jsonl"), *args]
    )

    assert result.exit_code == 2
    assert message in result.output


def test_text_output_lists_each_item(tmp_path: Path) -> None:
    result = _report(_mixed_ledger(tmp_path), "--expected-interval-days", "2")

    assert result.exit_code == 0
    assert "5 control(s) from AIUC-1 evidence list, April 2026 edition" in result.output
    assert "- B006.2 Config: Agent security monitoring and alerting: not evidenced" in (
        result.output
    )
