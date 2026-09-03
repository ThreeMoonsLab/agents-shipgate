"""Guards for the Cut A strata inventory (issue #456).

``benchmark/safety-qualification/strata-inventory.csv`` is the corpus owner's
sourcing plan for the 56-case ``pre_1_0`` artifact. It is not evidence and
nothing here can qualify a release; what these tests protect is that the plan
stays aimed at the shape the approved policy actually requires, and that every
row can still be traced to the in-tree source it cites.

The cells and the holdout floor are *derived* from
``pre_release_safety_requirements()``, never restated.
``docs/release-evidence-policy-decision.md`` names this directory as the one
definition site whose omission no gate can detect: a policy change that left the
runbook behind would aim the corpus-delivery effort at the wrong shape entirely,
and nothing would fail. Deriving them here is what makes that failure loud.
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache
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
SEARCHED_FOR_EXPOSURE = ("tests", "src")

EXPECTED_HEADER = [
    "slot_id",
    "profile",
    "target_decision",
    "origin_class",
    "exposure",
    "split_eligibility",
    "status",
    "candidate_ref",
    "pinned_base",
    "pinned_head",
    "target_basis",
    "evidence_ref",
    "mining_lead",
    "notes",
]

SOURCING_STATUSES = frozenset({"pinned", "unpinned", "gap"})
# Closed, and with no member *taken from* verifier output: a corpus assembled to
# match the engine's own verdicts cannot measure the engine. `miner_label` is
# not thereby verifier-*independent* -- see
# `test_the_miner_label_basis_is_disclosed_as_verifier_exposed`.
TARGET_BASES = frozenset(
    {"miner_label", "diff_substance", "sample_design", "constructed_design", "unsourced"}
)

# Cut B constructions live here, never under `samples/`: the goldens under
# `samples/*/expected/` are what the engine is developed against, so a
# corpus-built synthetic committed there would become tuning material the
# moment it landed. Each case is a change -- `base/` and `head/` trees -- with
# its design record (`CASE.md`) beside them, outside both trees, so a rater
# packet built from the trees can never carry it.
CONSTRUCTED_PREFIX = "benchmark/safety-qualification/constructed/"
CONSTRUCTED_TREES = ("base", "head")
# Files that must never appear inside a constructed case's trees: the design
# record names the target decision, and the others are engine output.
CONSTRUCTED_TREE_CONTRABAND = ("CASE.md", "agents-shipgate-reports", ".agents-shipgate")

# Holdout means evidence the engine was never tuned on. That is a fact about
# this project's development history, not about where the candidate's bytes
# live, so it is recorded per row and only *partly* detectable.
EXPOSURES = frozenset(
    {"engine_tests", "maintainer_walk", "shipped_sample", "benchmark_scored", "miner_label"}
)
EXPOSURES_BLOCKING_HOLDOUT = frozenset({"engine_tests", "maintainer_walk", "shipped_sample"})

# A merged PR is history; a closed-unmerged or reverted one is the rejected
# vein; an open PR is neither and cannot fill a slot. `reverted` is landed
# history whose rejection came afterwards: the register names the revert PR,
# and the candidate is pinned at its own merge like any merged PR.
STATE_ORIGINS = {
    "merged": frozenset({"real_history", "design_partner"}),
    "closed": frozenset({"rejected_or_reverted"}),
    "reverted": frozenset({"rejected_or_reverted"}),
    "in_tree": frozenset({"synthetic"}),
}
SPLIT_ELIGIBILITIES = frozenset({"tuning_only", "either"})
QUALIFYING_ORIGINS = frozenset({"real_history", "rejected_or_reverted", "design_partner"})

EXTERNAL_CANDIDATE = re.compile(r"^github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#[1-9][0-9]*$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

# Two of the seven profiles are *scenario* profiles: what puts a candidate in
# them is what the change does, not what source type it declares. A sample in
# either one is justified by its register entry and by nothing mechanical.
SCENARIO_PROFILES = frozenset({"coding_agent_trust_roots", "multi_agent_handoffs"})
# Samples that ship their artifact without a manifest, so there is no declared
# source type to compare a profile against. Listed rather than skipped, so a
# newly manifest-less sample has to be considered instead of silently admitted.
PROFILE_UNCHECKED_SAMPLES = frozenset({"samples/n8n_workflow_agent"})
MANIFEST_TYPE_PROFILES = {
    "mcp": "mcp_openapi_declared_binding",
    "openapi": "mcp_openapi_declared_binding",
    "openai-agents": "openai_agents_sdk",
    "openai_agents_sdk": "openai_agents_sdk",
    "langchain": "langchain_crewai",
    "crewai": "langchain_crewai",
    "google_adk": "google_adk",
    "n8n": "n8n",
    "conductor": "multi_agent_handoffs",
}

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


def _declared_exposure(row: dict[str, str]) -> set[str]:
    return {mark for mark in row["exposure"].split(";") if mark and mark != "none"}


def _is_in_tree(candidate_ref: str) -> bool:
    """A candidate this repository's own history pins: a sample or a construction."""

    return candidate_ref.startswith("samples/") or candidate_ref.startswith(CONSTRUCTED_PREFIX)


def _constructed_case_name(candidate_ref: str) -> str:
    return candidate_ref.removeprefix(CONSTRUCTED_PREFIX)


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

    # The `.labels.template.csv` files alongside these are unlabeled scaffolds.
    # This pattern already excludes them by name; widening it to `*.csv` would
    # admit their placeholder rows as real labels.
    labels: dict[str, dict[str, str]] = {}
    for source in sorted(MINER_RESULTS.glob("*.labels.csv")):
        relative = source.relative_to(REPO_ROOT).as_posix()
        with source.open(encoding="utf-8", newline="") as handle:
            for entry in csv.DictReader(handle):
                labels.setdefault(_candidate_ref_for(entry["pr_url"]), {})[relative] = entry[
                    "label"
                ]
    return labels


def _swept_candidates() -> set[str]:
    """Every candidate ref named by a committed miner sweep."""

    swept: set[str] = set()
    for source in sorted(MINER_RESULTS.glob("*.csv")):
        with source.open(encoding="utf-8", newline="") as handle:
            for entry in csv.DictReader(handle):
                url = entry.get("pr_url")
                if url:
                    swept.add(_candidate_ref_for(url))
    return swept


def _swept_pins() -> dict[str, dict[tuple[str, str], set[str]]]:
    """Every base/head a committed sweep resolved: candidate -> pins -> sweeps.

    A sweep records an unresolved candidate with both columns blank; those are
    not pins and are dropped here, so a row is never checked against a
    recording that resolved nothing.

    Keying by the *pins* rather than by the sweep is what makes a contradiction
    visible. Keyed by sweep, a file holding one subject twice with different
    SHAs would keep whichever row came last, and a corrupt duplicate ordered
    before the good one would read as agreement.
    """

    pins: dict[str, dict[tuple[str, str], set[str]]] = {}
    for source in sorted(MINER_RESULTS.glob("*.csv")):
        relative = source.relative_to(REPO_ROOT).as_posix()
        with source.open(encoding="utf-8", newline="") as handle:
            for entry in csv.DictReader(handle):
                url, base, head = (
                    entry.get("pr_url"),
                    entry.get("base_sha"),
                    entry.get("head_sha"),
                )
                if url and base and head:
                    recorded = pins.setdefault(_candidate_ref_for(url), {})
                    recorded.setdefault((base, head), set()).add(relative)
    return pins


@lru_cache(maxsize=1)
def _engine_source_lines() -> tuple[tuple[str, str], ...]:
    """Every ``tests/``/``src/`` line, read once for every detector below.

    One pass over the tree rather than one per candidate: the per-candidate
    form took 34 seconds, which is a guard nobody keeps.
    """

    this_file = Path(__file__).resolve()
    lines: list[tuple[str, str]] = []
    for root in SEARCHED_FOR_EXPOSURE:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            # This file names every candidate by construction. Its own
            # docstrings are not evidence that the engine was built against
            # them, and counting them would make every row self-exposing.
            if path == this_file:
                continue
            location = path.relative_to(REPO_ROOT).as_posix()
            for offset, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                lines.append((f"{location}:{offset}", line))
    return tuple(lines)


@lru_cache(maxsize=1)
def _lines_citing_a_candidate_number() -> dict[str, tuple[tuple[str, str], ...]]:
    """PR number -> the ``tests/``/``src/`` lines that mention it."""

    numbers = sorted(
        {
            row["candidate_ref"].partition("#")[2]
            for row in _rows()
            if row["candidate_ref"].startswith("github.com/")
        }
    )
    if not numbers:
        return {}

    wanted = re.compile(rf"(?<![0-9])({'|'.join(map(re.escape, numbers))})(?![0-9])")
    found: dict[str, list[tuple[str, str]]] = {number: [] for number in numbers}
    for location, line in _engine_source_lines():
        for match in wanted.finditer(line):
            found[match.group(1)].append((location, line))
    return {number: tuple(hits) for number, hits in found.items()}


def _constructed_case_named_in_engine_sources(candidate_ref: str) -> str | None:
    """The first ``tests/`` or ``src/`` line naming this constructed case.

    This is the corollary rule made mechanical: a construction built for the
    corpus stays holdout-eligible only while nothing in the engine or its
    tests is written against it. The day one is, its row has to say
    ``engine_tests`` or fail here.
    """

    name = re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(_constructed_case_name(candidate_ref))}(?![A-Za-z0-9_])"
    )
    for location, line in _engine_source_lines():
        if name.search(line):
            return location
    return None


def _named_in_engine_sources(candidate_ref: str) -> str | None:
    """The first ``tests/`` or ``src/`` line naming this PR, if any.

    Matched as the repository's own short name plus the PR number on one line,
    which is how this codebase cites upstream cases (``adk-samples#1745``,
    ``github/github-mcp-server#3076``, ``Stripe stripe/ai PR #232``). A bare
    number would match line offsets and hashes and mean nothing.
    """

    owner_repo, _, number = candidate_ref.removeprefix("github.com/").partition("#")
    repo = re.compile(rf"\b{re.escape(owner_repo.split('/')[-1])}\b")
    for location, line in _lines_citing_a_candidate_number().get(number, ()):
        if repo.search(line):
            return location
    return None


def _register_section(heading: str) -> list[str]:
    """The lines under one register heading, stopping at the next heading."""

    lines = REGISTER.read_text(encoding="utf-8").splitlines()
    start = lines.index(heading) + 1
    end = next(
        (offset for offset, line in enumerate(lines[start:], start) if line.startswith("#")),
        len(lines),
    )
    return lines[start:end]


def _register_entries() -> dict[str, tuple[str | None, str]]:
    """Candidate -> (profile, state), uniquely, across both register tables.

    The register is where a candidate's profile assignment and merge state are
    *stated*; the CSV is where they are used. Binding the two is what stops a
    profile from being changed on one side only, which would silently move a
    case between per-profile coverage counts.

    The two tables are read separately on purpose: they share a `State` column
    but the Reserve's second column is an *origin*, not a profile. Reading them
    as one table would file `real_history` as a profile name and quietly hand a
    bogus profile to anything that looked one up.
    """

    entries: dict[str, tuple[str | None, str]] = {}
    for heading, has_profile in (("## Candidate register", True), ("### Reserve", False)):
        for line in _register_section(heading):
            if not line.startswith("| `"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) < 3:
                continue
            candidate = cells[0].strip("`")
            state = cells[2].strip("`")
            if state not in STATE_ORIGINS and state != "open":
                continue
            assert candidate not in entries, (
                f"the register names {candidate!r} twice; a reserved candidate is not a placed one"
            )
            entries[candidate] = (cells[1].strip("`") if has_profile else None, state)
    return entries


def _reserve_claims() -> list[tuple[str, str, str]]:
    """``(candidate, origin, state)`` for every reserve row that states an origin.

    The Reserve's second column is an origin where the register above it holds
    a profile, and one row may list several candidates that share the claim.
    A row whose origin is left ``—`` states nothing and is skipped. Everything
    else in that column is read as an origin, and its state is required to be
    one that can supply an origin at all -- otherwise the one shape this rule
    exists to catch, a still-`open` PR reserved as ``real_history``, would drop
    out of the check rather than fail it.
    """

    claims: list[tuple[str, str, str]] = []
    for line in _register_section("### Reserve"):
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        origin, state = cells[1].strip("`"), cells[2].strip("`")
        if origin == "—":
            continue
        assert origin in set(get_args(SafetyCaseOrigin)), f"the reserve states origin {origin!r}"
        assert state in STATE_ORIGINS, (
            f"the reserve gives {cells[0]} an origin, but {state!r} can supply none"
        )
        claims.extend(
            (candidate, origin, state) for candidate in re.findall(r"`([^`]+)`", cells[0])
        )
    return claims


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
        cell: (present[cell], count) for cell, count in required.items() if present[cell] < count
    }
    assert understocked == {}


def test_every_slot_id_is_derived_from_its_own_cell(rows: list[dict[str, str]]) -> None:
    """The id restates the row; it never carries information the row does not.

    A hand-written id is a second spelling of the cell that can disagree with
    the first one, and the disagreement is invisible in a spreadsheet.
    """

    numbered: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        cell = (row["profile"], row["target_decision"])
        prefix, _, index = row["slot_id"].rpartition(".")
        assert prefix == f"{row['profile']}.{row['target_decision']}", (
            f"{row['slot_id']} names a cell that is not its own"
        )
        numbered[cell].append(index)

    # Set equality, not file order: a plan sorted by origin to review coverage
    # is the same plan, and renumbering ids to match an arbitrary sort would
    # reintroduce exactly the hand-written second spelling this rules out.
    for cell, indices in numbered.items():
        expected = [str(n) for n in range(1, len(indices) + 1)]
        assert sorted(indices) == sorted(expected), f"{cell} is not numbered 1..{len(indices)}"

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
        assert row["exposure"], f"{row['slot_id']} records no exposure, not even none"
        assert _declared_exposure(row) <= EXPOSURES, row["slot_id"]
        if row["exposure"] != "none":
            assert _declared_exposure(row), f"{row['slot_id']} spells an empty exposure list"


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
            assert row["exposure"] == "none", f"{row['slot_id']} has no candidate to expose"
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
        in_tree = _is_in_tree(row["candidate_ref"])

        if row["status"] == "unpinned":
            assert not in_tree, f"{row['slot_id']}: an in-tree sample is pinned by this repository"
            assert base == "" and head == "", row["slot_id"]
        elif row["status"] == "pinned" and in_tree:
            assert base == "" and head == "", row["slot_id"]
        elif row["status"] == "pinned":
            assert FULL_SHA.match(base), f"{row['slot_id']} base is not a full SHA"
            assert FULL_SHA.match(head), f"{row['slot_id']} head is not a full SHA"
            assert base != head, row["slot_id"]


def test_a_pinned_external_candidate_matches_the_sweep_that_recorded_it(
    rows: list[dict[str, str]],
) -> None:
    """Pins are re-read from the sweep rather than trusted in this file.

    A hand-written pin is a second copy of a fact this project already
    resolved once, and a wrong one sends a rater to a diff nobody adjudicated.
    ``github-mcp-server#3076`` is the live example: its walk note abbreviated
    the head as ``5ea9a0e8…``, which is ``refs/pull/3076/head`` and is not
    reachable from the default branch at all -- the merge commit the
    convention asks for is ``8ec62491…``.

    So an external candidate is pinned from a committed sweep, and the two
    escapes are closed with it: pinning a subject no sweep resolved, which
    nothing can check, and leaving one a sweep *did* resolve ``unpinned``,
    which is lost work rather than an unknown.
    """

    swept = _swept_pins()

    for row in rows:
        ref = row["candidate_ref"]
        if row["status"] == "gap" or _is_in_tree(ref):
            continue
        recorded = swept.get(ref)
        assert recorded, (
            f"{row['slot_id']}: no committed sweep resolved {ref}, so its pins "
            "cannot be checked against anything. Mine it (`--pr <n>`) first."
        )
        # Two recordings that resolved the same subject differently -- in two
        # sweeps or twice in one -- would make "matches the sweep" mean
        # "matches whichever recording agrees".
        sources = sorted({sweep for sweeps in recorded.values() for sweep in sweeps})
        assert len(recorded) == 1, f"{ref} is pinned differently within {sources}"
        assert row["status"] == "pinned", f"{row['slot_id']} has recorded pins available"
        assert (row["pinned_base"], row["pinned_head"]) in recorded, (
            f"{row['slot_id']} pins disagree with {sources}"
        )


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
        if _is_in_tree(ref):
            assert (REPO_ROOT / ref).is_dir(), f"{row['slot_id']} names a tree that does not exist"
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
            assert anchor in headings, (
                f"{row['slot_id']} cites a section that is not there: #{anchor}"
            )


def test_declared_exposure_is_at_least_what_the_tree_shows(
    rows: list[dict[str, str]],
) -> None:
    """The detector is a floor, and a floor is the only honest shape for it.

    Some exposure cannot be found by searching: ``grafana/mcp-grafana#1080``
    appears nowhere in this repository and still drove the
    ``tool_sources[].binding`` design. So a row may declare more exposure than
    the tree shows, never less -- and the moment a candidate is written into a
    test or a sweep, its row has to say so or fail here.
    """

    labels = _miner_labels()
    swept = _swept_candidates()

    for row in rows:
        if row["status"] == "gap":
            continue
        ref = row["candidate_ref"]
        declared = _declared_exposure(row)
        found: set[str] = set()
        witness: dict[str, str] = {}

        if ref.startswith("samples/"):
            found.add("shipped_sample")
            witness["shipped_sample"] = ref
        elif ref.startswith(CONSTRUCTED_PREFIX):
            cited = _constructed_case_named_in_engine_sources(ref)
            if cited is not None:
                found.add("engine_tests")
                witness["engine_tests"] = cited
        else:
            cited = _named_in_engine_sources(ref)
            if cited is not None:
                found.add("engine_tests")
                witness["engine_tests"] = cited
        # Applied to samples too: the constructed sweep scores fixtures under
        # `fixture://`, and scoping this to external candidates would leave a
        # whole class of sweep participation undeclared.
        if ref in swept:
            found.add("benchmark_scored")
            witness["benchmark_scored"] = "a committed miner sweep"
        if ref in labels:
            found.add("miner_label")
            witness["miner_label"] = sorted(labels[ref])[0]

        # `maintainer_walk` has no detector, but `diff_substance` is the
        # record of one: the only way this project read that diff was by
        # walking the repository, and every such walk so far produced an issue,
        # a fix, or a regression test. Without this, the one exposure nothing
        # can find is also the one anybody can quietly drop.
        if row["target_basis"] == "diff_substance":
            found.add("maintainer_walk")
            witness["maintainer_walk"] = "its own diff_substance basis"

        missing = sorted(found - declared)
        assert not missing, (
            f"{row['slot_id']} ({ref}) does not declare {missing}; "
            f"found at {[witness[mark] for mark in missing]}"
        )


def test_exposure_decides_the_split_and_nothing_else_does(
    rows: list[dict[str, str]],
) -> None:
    """``split_eligibility`` is a consequence, never an independent claim.

    Before this was derived, a shipped sample could be marked ``either`` and a
    walked upstream PR could not be marked ``tuning_only`` at all -- so the
    plan counted engine-development inputs as holdout-capable evidence, and the
    shortfall would only have surfaced at freeze.
    """

    for row in rows:
        blocking = _declared_exposure(row) & EXPOSURES_BLOCKING_HOLDOUT
        expected = "tuning_only" if blocking else "either"
        assert row["split_eligibility"] == expected, (
            f"{row['slot_id']} is {row['split_eligibility']} with exposure {row['exposure']}"
        )


def test_a_sample_design_row_cites_the_sample_it_is_about(
    rows: list[dict[str, str]],
) -> None:
    """The one basis with no independent source still gets cross-checked.

    ``miner_label`` is checked against a miner CSV and ``diff_substance``
    against the register. ``sample_design`` has neither, so the only thing that
    can be checked is that the row cites the sample it actually names -- and
    without that it can cite any directory in the tree and still resolve.
    """

    for row in rows:
        if row["target_basis"] != "sample_design":
            continue
        assert row["candidate_ref"].startswith("samples/"), row["slot_id"]
        assert row["evidence_ref"] == row["candidate_ref"], (
            f"{row['slot_id']} cites {row['evidence_ref']}, but it is about {row['candidate_ref']}"
        )


def test_a_constructed_case_is_a_change_with_its_design_record_beside_it(
    rows: list[dict[str, str]],
) -> None:
    """A construction is a PR-shaped pair of trees, never a single tree.

    The corpus labels a *change*, so a case with no diff has nothing to label.
    The design record names the target decision; it lives beside the trees, not
    in them, so a rater packet built from ``base/`` and ``head/`` cannot carry
    it -- and neither tree may hold engine output. A construction sharing a
    name with a shipped sample is one copy away from being tuning material,
    so that is refused too.
    """

    for row in rows:
        if row["target_basis"] != "constructed_design":
            continue
        ref = row["candidate_ref"]
        assert ref.startswith(CONSTRUCTED_PREFIX), (
            f"{row['slot_id']}: a constructed_design row must name a construction, not {ref}"
        )
        assert row["origin_class"] == "synthetic", row["slot_id"]
        case_dir = REPO_ROOT / ref
        assert row["evidence_ref"] == f"{ref}/CASE.md", (
            f"{row['slot_id']} must cite its own design record, not {row['evidence_ref']}"
        )
        assert (case_dir / "CASE.md").is_file(), f"{ref} has no CASE.md"
        assert not (REPO_ROOT / "samples" / _constructed_case_name(ref)).exists(), (
            f"{ref} shares its name with a shipped sample"
        )

        trees = {}
        for tree in CONSTRUCTED_TREES:
            root = case_dir / tree
            assert root.is_dir(), f"{ref} has no {tree}/ tree"
            files = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            assert files, f"{ref}/{tree} is empty"
            contraband = sorted(
                name
                for name in files
                if any(part in CONSTRUCTED_TREE_CONTRABAND for part in Path(name).parts)
            )
            assert contraband == [], f"{ref}/{tree} carries {contraband}"
            trees[tree] = files
        assert trees["base"] != trees["head"], f"{ref} is not a change: base and head are identical"


def test_every_sourced_candidate_is_registered_with_its_profile_and_state(
    rows: list[dict[str, str]],
) -> None:
    """Profile and origin are claims about the candidate, stated once.

    Per-profile coverage is counted from the corpus-declared profile, so a
    candidate silently moved between profiles satisfies a cell it does not
    belong to. And an origin is a fact about the PR: swapping two candidates'
    profiles, or planning ``real_history`` for a PR that never merged, both
    pass every per-row guard and are caught only here.
    """

    entries = _register_entries()

    for row in rows:
        if row["status"] == "gap":
            continue
        ref = row["candidate_ref"]
        assert ref in entries, f"{row['slot_id']}: {ref} has no register entry"
        profile, state = entries[ref]
        assert profile is not None, (
            f"{row['slot_id']}: {ref} is listed under Reserve, which is where candidates "
            "that are deliberately not placed in a cell live"
        )
        assert profile == row["profile"], (
            f"{row['slot_id']} is filed under {row['profile']}, "
            f"but the register assigns {ref} to {profile}"
        )
        assert state in STATE_ORIGINS, (
            f"{row['slot_id']}: {ref} is {state!r} and cannot fill a slot -- "
            "an open PR is not history and has no decision to validate against"
        )
        assert row["origin_class"] in STATE_ORIGINS[state], (
            f"{row['slot_id']} plans {row['origin_class']} for a {state} candidate"
        )


def test_a_gap_that_names_a_pull_request_plans_the_origin_that_pr_can_supply(
    rows: list[dict[str, str]],
) -> None:
    """A named lead is a claim, and its origin is not free to choose.

    ``google/adk-python#6605`` was closed without merge, so a gap naming it
    cannot be planned as ``real_history``: the lead and the origin would
    describe two different candidates, and the shortfall surfaces only when
    someone tries to fill the slot. The reserve states the same pair for the
    same reason, and is checked here too -- see the second loop.
    """

    entries = _register_entries()
    named = re.compile(r"github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#[1-9][0-9]*")

    checked = 0
    for row in rows:
        if row["status"] != "gap":
            continue
        for ref in named.findall(row["mining_lead"]):
            assert ref in entries, f"{row['slot_id']} names {ref} with no register entry"
            _, state = entries[ref]
            if state == "open":
                continue  # named as a shape to match, not as the candidate
            assert row["origin_class"] in STATE_ORIGINS[state], (
                f"{row['slot_id']} plans {row['origin_class']} but names {ref}, which is {state}"
            )
            checked += 1

    # Every gap is filled, so the rule now lives where the next candidate comes
    # from. A reserve row states an origin beside a state exactly the way a gap
    # did, and it is the pool a relabel draws on -- picking one up and finding
    # its origin was never available is the same shortfall, discovered later.
    for candidate, origin, state in _reserve_claims():
        assert origin in STATE_ORIGINS[state], (
            f"the reserve lists {candidate} as {origin}, which a {state} candidate cannot supply"
        )
        checked += 1
    assert checked, "nothing states an origin beside a state any more; drop this guard"


def test_a_sample_profile_matches_the_source_type_it_declares(
    rows: list[dict[str, str]],
) -> None:
    """For the five source-type profiles, the manifest is the check.

    ``coding_agent_trust_roots`` and ``multi_agent_handoffs`` are scenario
    profiles -- ``samples/agent_weakens_gate`` declares ``type: mcp`` and
    belongs to neither MCP cell -- so for those the register entry is the
    justification and there is nothing mechanical to compare it against.
    """

    for row in rows:
        ref = row["candidate_ref"]
        if not _is_in_tree(ref) or row["profile"] in SCENARIO_PROFILES:
            continue
        # A construction is judged by the state the change produces.
        searched = (
            REPO_ROOT / ref / "head" if ref.startswith(CONSTRUCTED_PREFIX) else REPO_ROOT / ref
        )
        manifests = sorted(searched.rglob("shipgate.yaml"))
        if ref in PROFILE_UNCHECKED_SAMPLES:
            assert not manifests, f"{ref} has a manifest now; drop it from the unchecked list"
            continue
        assert manifests, (
            f"{row['slot_id']}: {ref} declares no manifest to check against. "
            "Add it to PROFILE_UNCHECKED_SAMPLES only with a register entry that "
            "justifies its profile some other way."
        )
        declared: set[str] = set()
        for manifest in manifests:
            text = manifest.read_text(encoding="utf-8")
            declared |= {
                match.group(1) for match in re.finditer(r"^\s*type:\s*([A-Za-z0-9_-]+)", text, re.M)
            }
            # n8n is a top-level manifest section, not a tool-source type.
            if re.search(r"^n8n:", text, re.M):
                declared.add("n8n")
        admissible = {MANIFEST_TYPE_PROFILES.get(kind) for kind in declared}
        assert row["profile"] in admissible, (
            f"{row['slot_id']} files {ref} under {row['profile']}, "
            f"but it declares {sorted(declared)}"
        )


def test_a_diff_substance_row_is_written_down_in_the_register(
    rows: list[dict[str, str]],
) -> None:
    """``diff_substance`` means "we read the change" -- so the reading is in-tree.

    Adoption walks live in session notes and private write-ups. A basis that
    points at one of those is unreviewable: the next person cannot tell whether
    the cell was aimed from the diff or from the verdict the walk also produced.
    """

    # Only the register itself. The Reserve subsection beneath it lists
    # candidates that are deliberately *not* placed in a cell, so a row
    # matching there would be citing evidence that it has no slot.
    register = "\n".join(_register_section("## Candidate register"))

    for row in rows:
        if row["target_basis"] != "diff_substance":
            continue
        assert row["evidence_ref"].split("#", 1)[0] == REGISTER.relative_to(REPO_ROOT).as_posix()
        assert f"`{row['candidate_ref']}`" in register, (
            f"{row['slot_id']}: {row['candidate_ref']} has no register entry"
        )


def test_every_cell_can_still_supply_the_holdout_the_policy_demands(
    rows: list[dict[str, str]],
) -> None:
    """The holdout floor is computed from the policy, not assumed to be one.

    ``ceil(stratum.count * minimum_holdout_fraction_per_stratum)`` is one case
    per cell today. Hard-coding that number would let the fraction move to 0.60
    -- two holdout cases per cell -- while every cell here still offered a
    single eligible slot and this test still passed.
    """

    requirements = pre_release_safety_requirements()
    eligible: defaultdict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        if row["split_eligibility"] == "either":
            eligible[(row["profile"], row["target_decision"])] += 1

    starved = {}
    for stratum in requirements.required_strata:
        cell = (stratum.profile, stratum.expected_decision)
        floor = math.ceil(stratum.count * requirements.minimum_holdout_fraction_per_stratum)
        if eligible[cell] < floor:
            starved[cell] = (eligible[cell], floor)

    assert starved == {}, "these cells cannot supply the holdout cases the policy requires"


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


def test_a_miner_label_row_agrees_with_the_csv_it_cites(rows: list[dict[str, str]]) -> None:
    """The basis is checked against the source, not trusted as transcription.

    A ``miner_label`` row asserts that a specific miner row says a specific
    thing. Copying it by hand is how a plan ends up aiming a cell at a label
    that was never given.
    """

    labels = _miner_labels()

    for row in rows:
        if row["status"] == "gap":
            continue
        sweeps = labels.get(row["candidate_ref"])

        if sweeps is None:
            assert row["target_basis"] != "miner_label", (
                f"{row['slot_id']} claims a miner label its subject does not have"
            )
            continue

        # The escape this closes: hitting a mismatch and quietly restating the
        # basis as `diff_substance` so nothing cross-checks it any more. A
        # labeled subject is checked against its label wherever it is placed.
        assert row["target_basis"] == "miner_label", (
            f"{row['slot_id']}: {row['candidate_ref']} is labeled in {sorted(sweeps)}, "
            "so the row must cite that label"
        )
        cited = row["evidence_ref"]
        assert cited in sweeps, f"{row['slot_id']} cites {cited}, which does not label its subject"
        assert row["target_decision"] in LABEL_TO_DECISIONS[sweeps[cited]], (
            f"{row['slot_id']} targets {row['target_decision']}, "
            f"but {cited} labels it {sweeps[cited]}"
        )


def test_the_miner_label_basis_is_disclosed_as_verifier_exposed() -> None:
    """The disclosure is bound to the thing that makes it necessary.

    The labeling worksheet carries the engine's own verdict columns, and
    LABELING.md tells the labeler they are enough to label most rows without
    opening the diff. So a ``miner_label`` row's cell targeting was made with
    the verdict in view -- which the register must say, and every such row must
    carry in ``exposure``. If the worksheet ever stops exposing verdicts this
    fails, and the disclosure should be revisited rather than left standing as
    a claim about a worksheet that no longer does it.
    """

    templates = sorted(MINER_RESULTS.glob("*.labels.template.csv"))
    assert templates, "the labeling worksheet is gone; re-derive this disclosure"

    verdict_columns = {"head_decision", "verify_verdict", "verify_can_merge"}
    for template in templates:
        with template.open(encoding="utf-8", newline="") as handle:
            header = set(next(csv.reader(handle)))
        assert verdict_columns <= header, (
            f"{template.name} no longer exposes verifier verdicts to labelers"
        )

    register = REGISTER.read_text(encoding="utf-8")
    assert "not verifier-independent" in register, (
        "the register must disclose that miner labels were made with the verdict in view"
    )

    for row in _rows():
        if row["target_basis"] == "miner_label":
            assert "miner_label" in _declared_exposure(row), row["slot_id"]


def _register_table_rows() -> dict[str, list[int]]:
    """Every numeric register table row, keyed by its label cell.

    The value is each integer in the rest of the row, in order, so a cell
    reading ``32 (floor is 23)`` yields both numbers and both are checked.
    """

    rows: dict[str, list[int]] = {}
    for line in REGISTER.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.replace("`", "").strip() for cell in line.strip("|").split("|")]
        numbers = [int(found) for cell in cells[1:] for found in re.findall(r"\d+", cell)]
        if not numbers:
            continue
        # Two tables labeling a row the same way would make one of them
        # unreachable, and the unchecked one is the one that goes stale.
        assert cells[0] not in rows, f"the register labels two numeric rows {cells[0]!r}"
        rows[cells[0]] = numbers
    return rows


def test_the_register_reports_the_plan_the_csv_actually_holds(
    rows: list[dict[str, str]],
) -> None:
    """The prose reading and the plan are one fact with two spellings.

    ``strata-inventory.md`` is what a corpus owner reads to decide where to
    mine, and every number on it restates the CSV. Left unchecked, the first
    edit that adds a candidate leaves the reading behind, and the next person
    mines a cell the plan says is empty and the file says is full.
    """

    requirements = pre_release_safety_requirements()
    sourced = [row for row in rows if row["status"] != "gap"]
    qualifying = [row for row in rows if row["origin_class"] in QUALIFYING_ORIGINS]
    synthetic = [row for row in rows if row["origin_class"] == "synthetic"]
    holdout_eligible = [row for row in rows if row["split_eligibility"] == "either"]
    total_cases = sum(stratum.count for stratum in requirements.required_strata)

    totals = _register_table_rows()
    assert totals["Slots with a candidate"] == [len(sourced), len(rows)]
    assert totals["Gaps to mine or construct"] == [len(rows) - len(sourced)]
    assert totals["Slots planned as a qualifying origin"] == [
        len(qualifying),
        requirements.minimum_qualified_origins,
    ]
    assert totals["…of those, already sourced"] == [
        sum(1 for row in qualifying if row["status"] != "gap")
    ]
    assert totals["…of those, still to find"] == [
        sum(1 for row in qualifying if row["status"] == "gap")
    ]
    assert totals["Slots planned as synthetic"] == [
        len(synthetic),
        total_cases - requirements.minimum_qualified_origins,
    ]
    assert totals["Slots that can be a cell's holdout case"] == [len(holdout_eligible)]
    assert totals["Slots that are engine-development inputs"] == [len(rows) - len(holdout_eligible)]

    for group in ("profile", "target_decision"):
        for name in {row[group] for row in rows}:
            member = [row for row in rows if row[group] == name]
            assert name in totals, f"the register has no row for {name}"
            assert totals[name] == [
                sum(1 for row in member if row["status"] != "gap"),
                sum(1 for row in member if row["origin_class"] in QUALIFYING_ORIGINS),
                sum(1 for row in member if row["split_eligibility"] == "either"),
                sum(1 for row in member if row["status"] == "gap"),
            ], f"the register's row for {name} disagrees with the CSV"
