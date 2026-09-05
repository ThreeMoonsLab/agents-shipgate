"""The labeling guide is a rater input, so it must carry nothing a rater may not see.

Amendment 1 condition 2 hands every rater session exactly three things: the
pinned repository state, the PR diff, and ``benchmark/miner/LABELING.md``. The
guide is therefore the one in-tree file that travels into every rater packet,
and anything it names becomes something every rater has seen. It must name no
corpus candidate (a rater who has read another case's description has seen a
hint about that case), no verifier check ID (a rater who knows what the
verifier looks for is no longer independent of it), and it must carry the four
corpus decisions as headings so the rubric is the four-way one, not the miner's
three-way one.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUIDE = REPO_ROOT / "benchmark" / "miner" / "LABELING.md"
INVENTORY = REPO_ROOT / "benchmark" / "safety-qualification" / "strata-inventory.csv"

DECISIONS = ("passed", "review_required", "insufficient_evidence", "blocked")
PR_REF = re.compile(r"(?:github\.com/)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)")


def _guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _inventory_rows() -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _inventory_pr_refs() -> set[tuple[str, str]]:
    """Every ``owner/repo#n`` the inventory mentions, in any column.

    ``candidate_ref`` is the slot's candidate; ``mining_lead`` and ``notes``
    name PRs a gap should be filled from. A rater must see none of them.
    """

    refs: set[tuple[str, str]] = set()
    for row in _inventory_rows():
        for cell in row.values():
            refs.update(PR_REF.findall(cell))
    return refs


def _inventory_sample_refs() -> set[str]:
    return {
        row["candidate_ref"]
        for row in _inventory_rows()
        if row["candidate_ref"].startswith("samples/")
    }


def test_the_inventory_still_names_candidates() -> None:
    # If this set were empty the guard below would pass vacuously.
    assert _inventory_pr_refs(), "the inventory names no PR; re-derive this guard"


def test_no_slot_is_a_shipped_sample() -> None:
    """A shipped sample is cold start, not a change, so it cannot be a case.

    A rater judges a diff. A sample under ``samples/`` has no base and no head
    -- there is no change to judge -- so twelve slots that named one were
    retired from the inventory. Asserting it here rather than only in
    ``test_strata_inventory.py`` also retires a leak this file used to guard:
    a sample name in the guide cannot expose a corpus candidate once no
    candidate is a sample.
    """

    named = sorted(_inventory_sample_refs())
    assert not named, f"the inventory targets shipped samples as slots: {named}"


def test_the_guide_names_no_corpus_candidate_in_either_spelling() -> None:
    guide = _guide()
    corpus = _inventory_pr_refs()
    named = set(PR_REF.findall(guide))
    leaked = sorted(named & corpus)
    assert not leaked, f"LABELING.md names corpus candidates: {leaked}"

    # Belt and braces: the regex above is one spelling family; check the two
    # documented spellings literally as well, so a change to the regex cannot
    # silently widen what counts as clean.
    for repo, number in sorted(corpus):
        for spelling in (f"github.com/{repo}#{number}", f"{repo}#{number}"):
            assert spelling not in guide, f"LABELING.md names {spelling}"


def test_the_guide_names_no_verifier_check_id() -> None:
    hits = re.findall(r"SHIP-[A-Z0-9-]*", _guide())
    assert not hits, f"LABELING.md names verifier check IDs: {sorted(set(hits))}"


def test_the_guide_carries_the_four_corpus_decisions_as_headings() -> None:
    headings = {
        match.group(1)
        for match in re.finditer(r"^#{1,6}\s+`?([a-z_]+)`?\s*$", _guide(), flags=re.MULTILINE)
    }
    missing = [decision for decision in DECISIONS if decision not in headings]
    assert not missing, f"LABELING.md lacks headings for {missing}"


def test_the_rater_rubric_precedes_the_miner_process() -> None:
    """A rater reads from the top; the miner material must come after the rubric."""

    guide = _guide()
    rubric = guide.index("# Rater rubric")
    miner = guide.index("# Miner process (not a rater input)")
    assert rubric < miner
    for decision in DECISIONS:
        first_heading = re.search(rf"^#{{1,6}}\s+`?{decision}`?\s*$", guide, flags=re.MULTILINE)
        assert first_heading is not None
        assert rubric < first_heading.start() < miner, decision
