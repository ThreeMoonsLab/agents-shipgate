"""Guards for the Cut A strata inventory (issue #456).

``benchmark/safety-qualification/strata-inventory.csv`` is the corpus owner's
sourcing plan for the 56-case ``pre_1_0`` artifact. It is not evidence and
nothing here can qualify a release; what these tests protect is that the plan
stays aimed at the shape the approved policy actually requires, and that every
row can still be traced to the in-tree source it cites.

The cells are *derived* from ``pre_release_safety_requirements()``, never
restated. ``docs/release-evidence-policy-decision.md`` names this directory as
the one definition site whose omission no gate can detect: a policy change that
left the runbook behind would aim the corpus-delivery effort at the wrong shape
entirely, and nothing would fail. Deriving the grid here is what makes that
failure loud.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import get_args

import pytest

from agents_shipgate.schemas.safety_qualification import (
    SafetyCaseOrigin,
    pre_release_safety_requirements,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
INVENTORY = REPO_ROOT / "benchmark" / "safety-qualification" / "strata-inventory.csv"
REGISTER = REPO_ROOT / "benchmark" / "safety-qualification" / "strata-inventory.md"
MINER_RESULTS = REPO_ROOT / "benchmark" / "miner" / "results"
REEVAL = MINER_RESULTS / "2026-W27-reeval.csv"

EXPECTED_HEADER = [
    "slot_id",
    "profile",
    "target_decision",
    "origin_class",
    "status",
    "split_eligibility",
    "candidate_ref",
    "pinned_base",
    "pinned_head",
    "target_basis",
    "evidence_ref",
    "mining_lead",
    "notes",
]

SOURCING_STATUSES = frozenset({"pinned", "unpinned", "gap"})
# Deliberately closed, and deliberately without a verifier-derived member: a
# corpus assembled to match the engine's own verdicts cannot measure the engine.
TARGET_BASES = frozenset({"human_label", "diff_substance", "sample_design", "unsourced"})
SPLIT_ELIGIBILITIES = frozenset({"tuning_only", "either"})
QUALIFYING_ORIGINS = frozenset({"real_history", "rejected_or_reverted", "design_partner"})

EXTERNAL_CANDIDATE = re.compile(r"^github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#[1-9][0-9]*$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

# The miner's three-way vocabulary is coarser than the corpus's four-way one:
# ``needs_human`` does not distinguish "a human should look" from "there is not
# enough evidence to decide". Drawing that line is Cut C calibration work, so a
# ``needs_human`` candidate may legitimately be aimed at either cell.
LABEL_TO_DECISIONS = {
    "safe_to_merge": frozenset({"passed"}),
    "must_block": frozenset({"blocked"}),
    "needs_human": frozenset({"review_required", "insufficient_evidence"}),
}


def _rows() -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return _rows()


def _candidate_ref_for(pr_url: str) -> str:
    """Spell a miner ``pr_url`` the way the inventory spells a candidate."""

    if pr_url.startswith("fixture://"):
        return f"samples/{pr_url.removeprefix('fixture://')}"
    owner_repo, _, number = pr_url.removeprefix("https://github.com/").partition("/pull/")
    return f"github.com/{owner_repo}#{number}"


def _miner_labels() -> dict[str, dict[str, str]]:
    """Every miner label, keyed by the candidate ref it is a label *for*.

    A subject labeled in more than one sweep keeps one entry per sweep, so a
    row citing any of them can be checked against the sweep it actually cites.
    """

    labels: dict[str, dict[str, str]] = {}
    for source in sorted(MINER_RESULTS.glob("*.labels.csv")):
        if source.name.endswith(".template.csv"):
            continue
        relative = source.relative_to(REPO_ROOT).as_posix()
        with source.open(encoding="utf-8", newline="") as handle:
            for entry in csv.DictReader(handle):
                labels.setdefault(_candidate_ref_for(entry["pr_url"]), {})[relative] = entry["label"]
    return labels


def test_the_inventory_columns_are_exactly_the_documented_ones() -> None:
    """A pinned header is what keeps a *label* column out of a sourcing plan.

    The inventory names a target decision for every slot. If a column could be
    added freely, the natural next one is the rater's answer -- at which point
    the plan stops being a plan and starts being unadjudicated evidence.
    """

    with INVENTORY.open(encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle))

    assert header == EXPECTED_HEADER


def test_the_inventory_covers_the_policy_grid_and_takes_its_cells_from_the_policy(
    rows: list[dict[str, str]],
) -> None:
    """Every stratum the pre-1.0 policy requires, with at least its case count.

    The floor is ``>=`` on purpose. Over-supplying a cell is how the plan
    survives a relabel that moves a case out of it, and a plan is never worse
    for holding more candidates than the corpus will use.
    """

    required = {
        (stratum.profile, stratum.expected_decision): stratum.count
        for stratum in pre_release_safety_requirements().required_strata
    }
    assert len(required) == 28

    present = Counter((row["profile"], row["target_decision"]) for row in rows)

    assert set(present) == set(required), "the inventory grid is not the policy's grid"
    understocked = {
        cell: (present[cell], count)
        for cell, count in required.items()
        if present[cell] < count
    }
    assert understocked == {}


def test_every_slot_id_is_derived_from_its_own_cell(rows: list[dict[str, str]]) -> None:
    """The id restates the row; it never carries information the row does not.

    A hand-written id is a second spelling of the cell that can disagree with
    the first one, and the disagreement is invisible in a spreadsheet.
    """

    counters: Counter[tuple[str, str]] = Counter()
    for row in rows:
        cell = (row["profile"], row["target_decision"])
        counters[cell] += 1
        assert row["slot_id"] == f"{row['profile']}.{row['target_decision']}.{counters[cell]}"

    assert len({row["slot_id"] for row in rows}) == len(rows)


def test_the_row_vocabularies_are_closed(rows: list[dict[str, str]]) -> None:
    """Including the one that has no verifier-derived member.

    ``target_basis`` is where a shortcut would appear: aiming a cell with the
    engine's own verdict is fast, and it silently turns the corpus into a
    measurement of the engine against itself.
    """

    origins = set(get_args(SafetyCaseOrigin))
    for row in rows:
        assert row["origin_class"] in origins, row["slot_id"]
        assert row["status"] in SOURCING_STATUSES, row["slot_id"]
        assert row["target_basis"] in TARGET_BASES, row["slot_id"]
        assert row["split_eligibility"] in SPLIT_ELIGIBILITIES, row["slot_id"]


def test_a_gap_carries_a_lead_and_nothing_else(rows: list[dict[str, str]]) -> None:
    """``status`` and the reference columns cannot disagree about the same row.

    A row that claims a candidate it does not have is the failure that makes a
    cell look full while nobody is mining it.
    """

    for row in rows:
        if row["status"] == "gap":
            assert row["target_basis"] == "unsourced", row["slot_id"]
            assert row["candidate_ref"] == "", row["slot_id"]
            assert row["evidence_ref"] == "", row["slot_id"]
            assert row["pinned_base"] == "" and row["pinned_head"] == "", row["slot_id"]
            assert row["mining_lead"].strip(), f"{row['slot_id']} is a gap with nowhere to look"
        else:
            assert row["target_basis"] != "unsourced", row["slot_id"]
            assert row["candidate_ref"].strip(), row["slot_id"]
            assert row["evidence_ref"].strip(), row["slot_id"]
            assert row["mining_lead"] == "", row["slot_id"]


def test_a_pin_is_a_full_sha_or_the_slot_is_not_pinned(rows: list[dict[str, str]]) -> None:
    """An abbreviated SHA is a display form, never a machine route.

    A walk note recording ``bfb59bb7..`` is a lead, not a pin: two refs that
    share a prefix are a different repository state, and a rater handed a short
    prefix is being asked to guess. Such a candidate stays ``unpinned``.
    """

    for row in rows:
        base, head = row["pinned_base"], row["pinned_head"]
        in_tree = row["candidate_ref"].startswith("samples/")

        if row["status"] == "unpinned":
            assert not in_tree, f"{row['slot_id']}: an in-tree sample is pinned by this repository"
            assert base == "" and head == "", row["slot_id"]
        elif row["status"] == "pinned" and in_tree:
            assert base == "" and head == "", row["slot_id"]
        elif row["status"] == "pinned":
            assert FULL_SHA.match(base), f"{row['slot_id']} base is not a full SHA"
            assert FULL_SHA.match(head), f"{row['slot_id']} head is not a full SHA"
            assert base != head, row["slot_id"]


def test_every_candidate_resolves_and_no_subject_fills_two_slots(
    rows: list[dict[str, str]],
) -> None:
    """56 cases means 56 distinct subjects.

    The same PR at the same two SHAs in two cells is one piece of evidence
    counted twice, and it would satisfy a stratum floor without adding an
    observation to it.
    """

    sourced = [row for row in rows if row["status"] != "gap"]
    refs = [row["candidate_ref"] for row in sourced]
    duplicates = sorted({ref for ref in refs if refs.count(ref) > 1})
    assert duplicates == []

    for row in sourced:
        ref = row["candidate_ref"]
        if ref.startswith("samples/"):
            assert (REPO_ROOT / ref).is_dir(), f"{row['slot_id']} names a sample that does not exist"
        else:
            assert EXTERNAL_CANDIDATE.match(ref), f"{row['slot_id']} candidate_ref is malformed"

        path, _, anchor = row["evidence_ref"].partition("#")
        target = REPO_ROOT / path
        assert target.exists(), f"{row['slot_id']} cites {path}, which does not exist"
        if anchor:
            headings = {
                re.sub(r"[^a-z0-9 -]", "", line.lstrip("#").strip().lower()).replace(" ", "-")
                for line in target.read_text(encoding="utf-8").splitlines()
                if line.startswith("#")
            }
            assert anchor in headings, f"{row['slot_id']} cites a section that is not there: #{anchor}"


def test_a_diff_substance_row_is_written_down_in_the_register(
    rows: list[dict[str, str]],
) -> None:
    """``diff_substance`` means "we read the change" -- so the reading is in-tree.

    Adoption walks live in session notes and private write-ups. A basis that
    points at one of those is unreviewable: the next person cannot tell whether
    the cell was aimed from the diff or from the verdict the walk also produced.
    """

    register = REGISTER.read_text(encoding="utf-8")
    for row in rows:
        if row["target_basis"] != "diff_substance":
            continue
        assert row["evidence_ref"].split("#", 1)[0] == REGISTER.relative_to(REPO_ROOT).as_posix()
        assert f"`{row['candidate_ref']}`" in register, (
            f"{row['slot_id']}: {row['candidate_ref']} has no register entry"
        )


def test_every_cell_can_still_supply_its_holdout_case(rows: list[dict[str, str]]) -> None:
    """The policy's holdout floor has to be reachable from the plan, per cell.

    Holdout means evidence the engine was never tuned on, and every
    ``samples/*`` path is engine-tuning material -- the goldens under
    ``samples/*/expected/`` are what the engine is developed against. A cell
    filled entirely from ``samples/`` cannot honestly mark either case holdout,
    and the shortfall would only surface at freeze, after the labeling is paid
    for.
    """

    for row in rows:
        expected = "tuning_only" if row["candidate_ref"].startswith("samples/") else "either"
        assert row["split_eligibility"] == expected, row["slot_id"]

    eligible: defaultdict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        if row["split_eligibility"] == "either":
            eligible[(row["profile"], row["target_decision"])] += 1

    requirements = pre_release_safety_requirements()
    starved = sorted(
        (stratum.profile, stratum.expected_decision)
        for stratum in requirements.required_strata
        if eligible[(stratum.profile, stratum.expected_decision)] < 1
    )
    assert starved == [], "these cells are planned entirely from engine-tuning material"


def test_the_plan_clears_the_origin_floor_it_is_planning_for(
    rows: list[dict[str, str]],
) -> None:
    """The binding constraint on this corpus is origins, not case count.

    23 of 56 cases must be real history, rejected-or-reverted, or design
    partner. Slots planned as ``synthetic`` are cheap to add and each one eats
    margin that the labeled pool -- 19 PRs from five repositories -- does not
    have to spare.
    """

    requirements = pre_release_safety_requirements()
    total_cases = sum(stratum.count for stratum in requirements.required_strata)
    planned_qualifying = sum(1 for row in rows if row["origin_class"] in QUALIFYING_ORIGINS)
    planned_synthetic = sum(1 for row in rows if row["origin_class"] == "synthetic")

    assert planned_qualifying >= requirements.minimum_qualified_origins

    # Independent of the floor once a cell is over-supplied: the corpus takes
    # 56 cases whatever the plan holds, so a plan may not intend more
    # synthetics than 56 cases can absorb alongside the origin floor.
    assert planned_synthetic <= total_cases - requirements.minimum_qualified_origins


def test_a_human_label_row_agrees_with_the_csv_it_cites(rows: list[dict[str, str]]) -> None:
    """The basis is checked against the source, not trusted as transcription.

    A ``human_label`` row asserts that a specific miner row says a specific
    thing. Copying it by hand is how a plan ends up aiming a cell at a label
    that was never given.
    """

    labels = _miner_labels()

    for row in rows:
        if row["status"] == "gap":
            continue
        sweeps = labels.get(row["candidate_ref"])

        if sweeps is None:
            assert row["target_basis"] != "human_label", (
                f"{row['slot_id']} claims a miner label its subject does not have"
            )
            continue

        # The escape this closes: hitting a mismatch and quietly restating the
        # basis as `diff_substance` so nothing cross-checks it any more. A
        # labeled subject is checked against its label wherever it is placed.
        assert row["target_basis"] == "human_label", (
            f"{row['slot_id']}: {row['candidate_ref']} is labeled in {sorted(sweeps)}, "
            "so the row must cite that label"
        )
        cited = row["evidence_ref"]
        assert cited in sweeps, f"{row['slot_id']} cites {cited}, which does not label its subject"
        assert row["target_decision"] in LABEL_TO_DECISIONS[sweeps[cited]], (
            f"{row['slot_id']} targets {row['target_decision']}, "
            f"but {cited} labels it {sweeps[cited]}"
        )


def test_a_pinned_external_candidate_matches_the_sweep_that_recorded_it(
    rows: list[dict[str, str]],
) -> None:
    """Pins are re-read from the sweep rather than trusted in this file.

    Every candidate drawn from the 19-PR pool already has its base and head
    recorded once. A second hand-written copy is a second thing that can be
    wrong, and a wrong pin sends a rater to a diff nobody adjudicated.
    """

    with REEVAL.open(encoding="utf-8", newline="") as handle:
        sweep = {
            f"github.com/{entry['repo']}#{entry['pr_number']}": (
                entry["base_sha"],
                entry["head_sha"],
            )
            for entry in csv.DictReader(handle)
        }

    for row in rows:
        pins = sweep.get(row["candidate_ref"])
        if pins is None:
            continue
        # Downgrading to `unpinned` is not an escape either: the sweep already
        # resolved this subject, so leaving the pins out is lost work, not an
        # unknown.
        assert row["status"] == "pinned", f"{row['slot_id']} has recorded pins available"
        assert (row["pinned_base"], row["pinned_head"]) == pins, row["slot_id"]
