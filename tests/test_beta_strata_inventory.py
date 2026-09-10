"""Keep #512's partial source inventory truthful without certifying a corpus.

The existing Cut A vocabulary and pin/exposure rules apply unchanged. The
production policy supplies the cells; gaps and reserves supply no observations.
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest
import test_strata_inventory as cut_a

from agents_shipgate.schemas.safety_qualification import production_safety_requirements

DIRECTORY = cut_a.REPO_ROOT / "benchmark" / "safety-qualification"
INVENTORY = DIRECTORY / "beta-strata-inventory.csv"
REGISTER = DIRECTORY / "beta-strata-inventory.md"


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == cut_a.EXPECTED_HEADER
        return list(reader)


def _cell(row: dict[str, str]) -> tuple[str, str]:
    return row["profile"], row["target_decision"]


def _coverage(rows: list[dict[str, str]]) -> tuple[Counter, Counter, Counter]:
    """Only a pinned candidate supplies capacity; eligibility alone cannot."""
    pinned = Counter(_cell(row) for row in rows if row["status"] == "pinned")
    potential = Counter(
        _cell(row)
        for row in rows
        if row["status"] == "pinned" and row["split_eligibility"] == "either"
    )
    gaps = Counter(_cell(row) for row in rows if row["status"] == "gap")
    return pinned, potential, gaps


def _tables() -> dict[str, list[list[str]]]:
    sections: dict[str, list[list[str]]] = defaultdict(list)
    heading = ""
    for line in REGISTER.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            heading = line
        elif line.startswith("|"):
            sections[heading].append(
                [cell.strip().strip("`") for cell in line.strip("|").split("|")]
            )
    return sections


def test_beta_uses_the_existing_sourcing_contract(rows: list[dict[str, str]]) -> None:
    # Reuse the guards, not a second spelling of their rules. These functions
    # accept the supplied rows; none reads the pre-1.0 register or its policy.
    cut_a.test_every_slot_id_is_derived_from_its_own_cell(rows)
    cut_a.test_the_row_vocabularies_are_closed(rows)
    cut_a.test_a_gap_carries_a_lead_and_nothing_else(rows)
    cut_a.test_a_pin_is_a_full_sha_or_the_slot_is_not_pinned(rows)
    cut_a.test_a_pinned_external_candidate_matches_the_sweep_that_recorded_it(rows)
    cut_a.test_every_candidate_resolves_and_no_subject_fills_two_slots(rows)
    cut_a.test_exposure_decides_the_split_and_nothing_else_does(rows)
    cut_a.test_a_miner_label_row_agrees_with_the_csv_it_cites(rows)
    for row in rows:
        # Direct source targeting uses Cut A's vocabulary; it is not a beta
        # primary label, constructed case, or accepted holdout.
        assert row["target_basis"] in {"miner_label", "diff_substance", "unsourced"}
        assert row["status"] in {"pinned", "gap"}
        if row["status"] == "pinned":
            assert cut_a.EXTERNAL_CANDIDATE.fullmatch(row["candidate_ref"])


def test_beta_grid_and_gap_counts_come_from_production_policy(rows: list[dict[str, str]]) -> None:
    required = {
        (stratum.profile, stratum.expected_decision): stratum.count
        for stratum in production_safety_requirements().required_strata
    }
    assert {_cell(row) for row in rows} == set(required)
    pinned, _, gaps = _coverage(rows)
    for cell, count in required.items():
        assert gaps[cell] == max(0, count - pinned[cell]), cell
    # Surplus in a cell is allowed. Missing candidates are also allowed; this
    # guard protects a truthful plan, not an assertion of release readiness.


def test_gap_and_unpinned_eligibility_cannot_be_counted_as_holdout_capacity() -> None:
    sample = {"profile": "fixture", "target_decision": "passed", "split_eligibility": "either"}
    pinned, potential, gaps = _coverage(
        [sample | {"status": status} for status in ("gap", "unpinned", "pinned")]
        + [sample | {"status": "pinned", "split_eligibility": "tuning_only"}]
    )
    assert pinned == {("fixture", "passed"): 2}
    assert potential == {("fixture", "passed"): 1}
    assert gaps == {("fixture", "passed"): 1}


def test_profile_and_origin_match_the_register_and_reserves_are_unplaced(
    rows: list[dict[str, str]],
) -> None:
    tables = _tables()
    entries = [
        cells for cells in tables["## Candidate register"] if cells[0].startswith("github.com/")
    ]
    reserve = [cells for cells in tables["### Reserve"] if cells[0].startswith("github.com/")]
    refs = [entry[0] for entry in entries + reserve]
    assert len(refs) == len(set(refs)), "a subject is placed twice or also reserved"
    by_ref = {row["candidate_ref"]: row for row in rows if row["status"] == "pinned"}
    assert set(by_ref) == {entry[0] for entry in entries}
    for ref, profile, state, head_link, context in entries:
        row = by_ref[ref]
        assert profile == row["profile"]
        assert row["origin_class"] in cut_a.STATE_ORIGINS[state]
        assert head_link == (
            f"[commit](https://{ref.partition('#')[0]}/commit/{row['pinned_head']})"
        )
        assert context.strip(), ref
    for ref, origin, state, reason in reserve:
        assert origin in cut_a.STATE_ORIGINS[state]
        assert reason.strip(), ref


def test_beta_exposure_preserves_known_history_and_scans_all_beta_candidates(
    rows: list[dict[str, str]],
) -> None:
    prior = {row["candidate_ref"]: row for row in cut_a._rows() if row["candidate_ref"]}
    sourced = [row for row in rows if row["status"] == "pinned"]
    numbers = {row["candidate_ref"].partition("#")[2] for row in sourced}
    wanted = re.compile(rf"(?<![0-9])({'|'.join(map(re.escape, sorted(numbers)))})(?![0-9])")
    mentions: dict[str, list[str]] = defaultdict(list)
    this_file = Path(__file__).relative_to(cut_a.REPO_ROOT).as_posix()
    # Cut A's number index covers only its own candidates. Index the beta
    # subjects here so a newly placed subject cannot escape the detector.
    for location, line in cut_a._engine_source_lines():
        if location.startswith(this_file + ":"):
            continue
        for match in wanted.finditer(line):
            mentions[match.group(1)].append(line)
    swept = cut_a._swept_candidates()
    labels = cut_a._miner_labels()
    for row in sourced:
        ref = row["candidate_ref"]
        observed = set()
        if ref in prior:
            observed |= cut_a._declared_exposure(prior[ref])
        if ref in swept:
            observed.add("benchmark_scored")
        if ref in labels:
            observed.add("miner_label")
        if row["target_basis"] == "diff_substance":
            observed.add("maintainer_walk")
        owner_repo, _, number = ref.removeprefix("github.com/").partition("#")
        repo = re.compile(rf"\b{re.escape(owner_repo.split('/')[-1])}\b")
        if any(repo.search(line) for line in mentions[number]):
            observed.add("engine_tests")
        assert observed <= cut_a._declared_exposure(row), ref


def test_direct_source_targets_cite_the_beta_register_and_keep_the_exposure_floor(
    rows: list[dict[str, str]],
) -> None:
    # Cut A's equivalent helper reads its own register; use the beta entries
    # here, so a reserve or a private inspection note cannot stand in for one.
    entries = {
        cells[0]: cells[4]
        for cells in _tables()["## Candidate register"]
        if cells[0].startswith("github.com/")
    }
    for row in rows:
        if row["target_basis"] != "diff_substance":
            continue
        assert row["evidence_ref"] == (
            "benchmark/safety-qualification/beta-strata-inventory.md#candidate-register"
        )
        context = entries[row["candidate_ref"]]
        assert f"{row['pinned_base']}...{row['pinned_head']}" in context
        assert f"/blob/{row['pinned_head']}/" in context
        assert "maintainer_walk" in cut_a._declared_exposure(row)
        assert row["split_eligibility"] == "tuning_only"


def test_sequential_harness_candidates_preserve_joint_admissibility(
    rows: list[dict[str, str]],
) -> None:
    refs = [f"github.com/google/adk-samples#{number}" for number in (2543, 2544)]
    by_ref = {row["candidate_ref"]: row for row in rows if row["status"] == "pinned"}
    first, second = (by_ref[ref] for ref in refs)
    assert first["pinned_head"] == second["pinned_base"]
    for row in (first, second):
        assert all(ref in row["notes"] for ref in refs)
        assert "#2561" in row["notes"]
        assert "one split" in row["notes"]
        assert row["split_eligibility"] == "tuning_only"


def test_register_counts_only_the_candidate_capacity_the_csv_has(
    rows: list[dict[str, str]],
) -> None:
    policy = production_safety_requirements()
    required = {(s.profile, s.expected_decision): s.count for s in policy.required_strata}
    floors = {
        cell: math.ceil(count * policy.minimum_holdout_fraction_per_stratum)
        for cell, count in required.items()
    }
    pinned, potential, gaps = _coverage(rows)
    table = _tables()["## What has actually been collected"]
    numbers = {cells[0]: int(cells[1]) for cells in table if len(cells) == 2 and cells[1].isdigit()}
    reserve = [r for r in _tables()["### Reserve"] if r[0].startswith("github.com/")]
    assert numbers == {
        "Required cases": sum(required.values()),
        "Required cells": len(required),
        "Inventory slots including gaps": len(rows),
        "Pinned candidates": pinned.total(),
        "Unfilled candidate slots": gaps.total(),
        "Pinned qualifying-origin candidates": sum(
            row["status"] == "pinned" and row["origin_class"] in cut_a.QUALIFYING_ORIGINS
            for row in rows
        ),
        "Required qualifying origins": policy.minimum_qualified_origins,
        "Pinned potential holdout candidates": potential.total(),
        "Pinned tuning-only candidates": pinned.total() - potential.total(),
        "Required holdout slots across cells": sum(floors.values()),
        "Holdout slots still without a potential candidate": sum(
            max(0, floor - potential[cell]) for cell, floor in floors.items()
        ),
        "Unplaced reserve candidates": len(reserve),
    }
    cells = [r for r in table if len(r) == 7 and (r[0], r[1]) in required]
    assert len(cells) == len(required)
    assert {(r[0], r[1]) for r in cells} == set(required)
    for profile, decision, *counts in cells:
        cell = profile, decision
        assert list(map(int, counts)) == [
            required[cell],
            pinned[cell],
            potential[cell],
            floors[cell],
            gaps[cell],
        ]


def test_documented_calibration_exposure_survives_generic_regression_fixture_names(
    rows: list[dict[str, str]],
) -> None:
    # These merged implementation records explicitly identify real-source
    # calibration/development. A generic derived fixture need not name its
    # source PR in Python, so a text search alone cannot preserve this history.
    calibrated = {
        256: {
            "github.com/stripe/ai": (400, 353, 338, 336, 332, 312),
            "github.com/aaif-goose/goose": (9798, 9684, 9637, 9717),
        },
        582: {"github.com/google/adk-samples": (1975, 1977)},
    }
    by_ref = {row["candidate_ref"]: row for row in rows if row["status"] == "pinned"}
    reserve = {r[0]: r[3] for r in _tables()["### Reserve"] if r[0].startswith("github.com/")}
    register = REGISTER.read_text(encoding="utf-8")
    for repair, repositories in calibrated.items():
        assert f"https://github.com/ThreeMoonsLab/agents-shipgate/pull/{repair}" in register
        for repository, numbers in repositories.items():
            for number in numbers:
                ref = f"{repository}#{number}"
                if ref in by_ref:
                    assert "maintainer_walk" in cut_a._declared_exposure(by_ref[ref]), ref
                    assert by_ref[ref]["split_eligibility"] == "tuning_only", ref
                else:
                    assert "maintainer_walk" in reserve[ref], ref
                    assert "tuning_only" in reserve[ref], ref


def test_known_reimplementation_family_does_not_lose_its_split_obligation(
    rows: list[dict[str, str]],
) -> None:
    first, second = "github.com/google/adk-samples#125", "github.com/google/adk-samples#2148"
    by_ref = {row["candidate_ref"]: row for row in rows if row["status"] == "pinned"}
    for ref, related in ((first, second), (second, first)):
        assert related in by_ref[ref]["notes"]
        assert "one split" in by_ref[ref]["notes"]
    register = REGISTER.read_text(encoding="utf-8")
    assert first in register and second in register
    assert "not accepted case or holdout coverage" in register


def test_beta_register_discloses_its_non_evidence_status() -> None:
    register = REGISTER.read_text(encoding="utf-8")
    for required in (
        "not verifier-independent",
        "not a beta corpus or release evidence",
        "Never pass this register",
        "not a frozen split or accepted independent evidence",
        "actual human primary raters",
        "#520",
    ):
        assert required in register
