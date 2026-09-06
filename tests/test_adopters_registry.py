"""Guards for the opt-in adopters registry.

This project collects nothing, so `ADOPTERS.md` is the only source for any
adoption number it publishes. That makes the file's failure mode specific: not
a typo, but a number that drifts upward one edit at a time — a dogfooding row
counted as external, a count left behind when a row was removed, a tier raised
from "we ran it once" to "it gates our merges", a badge read as an adoption
claim, or a marketing number that no row can source.

Each of those is pinned here, mechanically rather than by review attention. The
tier vocabulary, the entry shape and the claims-policy rules live in this file
and in the registry at once, so neither can be weakened alone.

Issue #475.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "ADOPTERS.md"
README = REPO_ROOT / "README.md"
TEMPLATE = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "adopter_entry.yml"
PILOT_RESULTS = REPO_ROOT / "docs" / "design-partner-pilot-results.md"
PILOT_RUNBOOK = REPO_ROOT / "docs" / "design-partner-verifier-pilot.md"

REPO_URL = "https://github.com/ThreeMoonsLab/agents-shipgate"
BADGE_TARGET = f"{REPO_URL}/blob/main/ADOPTERS.md"

#: The closed vocabulary for the ``Use`` column. Three tiers, ordered by what
#: the gate can do — not by how much anyone likes it. Adding a fourth means
#: changing the registry's definitions, this tuple and the issue form together,
#: which is the point: a tier nobody defined is a tier nobody can check.
USE_TIERS = ("local evaluation", "advisory CI", "blocking CI")

#: Every entry has all six. ``Since`` and ``Entry`` are deliberately absent
#: from the issue form: the date is when the row lands, not when the requester
#: wrote it, and the entry link is the request itself.
COLUMNS = ("Adopter", "Repository", "What it gates", "Use", "Since", "Entry")
REQUESTER_COLUMNS = ("Adopter", "Repository", "What it gates", "Use")

#: The claims policy, keyed by the bold lead-in of each rule. Wording inside a
#: rule may be improved; a rule may not quietly disappear.
CLAIMS_RULES = (
    "No entry, no claim.",
    "Every number carries its as-of date.",
    "External and dogfooding are never summed.",
    "An entry is a dated statement, not a measurement.",
    "The tier is self-reported and never upgraded.",
    "A badge is not an entry.",
    "The pilot ledger stays separate.",
    "Removal is unconditional.",
)

EXTERNAL_HEADING = "## External adopters"
DOGFOOD_HEADING = "## Maintainer dogfooding"
EMPTY_MARKER = "No external entries yet."

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Dates are compared against the runner's local today plus one day. The guard
#: is here to catch "2027-03-01", not to fail a contributor in Auckland whose
#: today is still tomorrow in the UTC runner that reviews their pull request.
CLOCK_SKEW = dt.timedelta(days=1)


# --------------------------------------------------------------------------
# Reading the registry
# --------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Markdown here is hard-wrapped, so any phrase asserted on below is
    usually split across lines. Compare against the unwrapped form."""
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    """The body under ``heading``, up to the next same-or-higher heading.

    Fences are tracked: a ``#`` in column one inside a fenced block is a shell
    comment, not a heading, and truncating there would make every assertion
    past it vacuous (the same defect found in the pilot guards, #521).
    """
    level = len(heading) - len(heading.lstrip("#"))
    assert text.count(heading + "\n") == 1, f"{heading!r} must appear exactly once"
    start = text.index(heading + "\n")
    body = text[start + len(heading) :]
    pattern = re.compile(rf"#{{1,{level}}} ")
    fenced = False
    offset = 0
    for line in body.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and pattern.match(line):
            return body[:offset]
        offset += len(line)
    return body


def _table(section: str) -> tuple[tuple[str, ...], list[list[str]]]:
    """The pipe table in ``section`` as (header, raw rows).

    Deliberately total: a row with the wrong number of cells comes back as-is
    rather than raising, so it fails :func:`test_every_row_has_the_columns_it
    _claims` by name instead of breaking collection with a ``zip()`` error that
    names neither the file nor the row.
    """
    lines = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]
    if not lines:
        return (), []
    cells = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
    header = tuple(cells[0])
    assert len(cells) > 1 and all(set(cell) <= set("-: ") for cell in cells[1]), (
        f"expected a separator row under {header}"
    )
    return header, cells[2:]


def _raw_rows(heading: str) -> list[list[str]]:
    return _table(_section(_read(REGISTRY), heading))[1]


def _entries(heading: str) -> list[dict[str, str]]:
    """Well-formed rows only; malformed ones are reported by their own test."""
    header, rows = _table(_section(_read(REGISTRY), heading))
    return [dict(zip(header, row, strict=False)) for row in rows if len(row) == len(header)]


def _all_entries() -> list[tuple[str, dict[str, str]]]:
    return [
        (heading, row)
        for heading in (EXTERNAL_HEADING, DOGFOOD_HEADING)
        for row in _entries(heading)
    ]


def _entry_ids() -> list[str]:
    return [
        f"{heading.strip('# ').replace(' ', '-')}::{row.get('Adopter', '?')}"
        for heading, row in _all_entries()
    ]


def _counts() -> dict[str, str]:
    """The labelled numbers in ``## Counts``, by label."""
    section = _section(_read(REGISTRY), "## Counts")
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"\*\*([A-Z][A-Za-z ]+): ([^*]+?)\.\*\*", section)
    }


def _slug(heading: str) -> str:
    text = heading.lstrip("# ").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text)


def _headings(text: str) -> set[str]:
    return {_slug(line) for line in text.splitlines() if line.startswith("#")}


def _template() -> dict:
    return yaml.safe_load(_read(TEMPLATE))


def _template_fields() -> dict[str, dict]:
    return {
        block["attributes"]["label"]: block
        for block in _template()["body"]
        if "label" in block.get("attributes", {})
    }


# --------------------------------------------------------------------------
# The file exists, and the README carries the stance
# --------------------------------------------------------------------------


def test_registry_exists_and_the_readme_links_it():
    """A registry nobody can find counts nobody."""
    assert REGISTRY.is_file(), "ADOPTERS.md must exist at the repository root"
    assert "](ADOPTERS.md)" in _read(README), "README.md must link ADOPTERS.md"


def test_readme_links_into_the_registry_resolve():
    """The README sends readers to `#add-yourself` and `#badge`. A renamed
    heading turns both into a scroll to the top of a file about counting."""
    headings = _headings(_read(REGISTRY))
    targets = {
        href.split("#", 1)[1]
        for href in LINK_RE.findall(_read(README))
        if href.startswith("ADOPTERS.md#")
    }
    assert targets, "README.md must deep-link the registry's how-to sections"
    assert targets <= headings, f"README links dead registry anchors: {sorted(targets - headings)}"


@pytest.mark.parametrize(
    "phrase",
    (
        "no automatic collection",
        "user-initiated and consenting",
        "removed on request",
        "no entry, no claim",
    ),
)
def test_readme_states_the_privacy_and_claims_stance(phrase: str):
    """The stance has to be readable where adoption is discussed, not only in
    the file being defended. A README that links the registry without saying
    nothing is collected leaves a reader to assume the usual thing."""
    section = _flat(_section(_read(README), "## Adopters")).lower()
    assert phrase in section, f"README § Adopters must state {phrase!r}"


def test_readme_separates_public_entries_from_private_pilot_observations():
    """Acceptance §3: a private observation must never read as a public entry."""
    section = _flat(_section(_read(README), "## Adopters")).lower()
    assert "separate ledger" in section and "separate consent" in section, (
        "README § Adopters must say private pilot observations are a separate "
        "ledger under separate consent"
    )


def test_readme_counts_match_the_registry():
    """Two places publishing the same number is how one of them goes stale.
    The registry is the source; the README is checked against it."""
    counts = _counts()
    section = _flat(_section(_read(README), "## Adopters"))
    external = counts["External adopter entries"]
    dogfood = counts["Maintainer dogfooding entries"]
    # Both counts accept "entry" and "entries": the first real adopter makes
    # the external count 1, and a guard that demanded "1 external adopter
    # entries" would meet that contributor with a red build over grammar.
    for count, noun in ((external, "external adopter"), (dogfood, "maintainer dogfooding")):
        assert re.search(rf"\b{re.escape(count)} {noun} entr(?:y|ies)\b", section), (
            f"README § Adopters must state the registry's {noun} count ({count})"
        )
    assert counts["Counts as of"] in section, (
        "README § Adopters must carry the registry's as-of date beside its numbers"
    )


# --------------------------------------------------------------------------
# Entry shape
# --------------------------------------------------------------------------


def test_both_tables_use_the_same_columns():
    """One row shape, one parser. Two tables that drift apart are two
    definitions of what an entry is, and the weaker one wins by accident."""
    text = _read(REGISTRY)
    for heading in (EXTERNAL_HEADING, DOGFOOD_HEADING):
        header, _ = _table(_section(text, heading))
        assert header == COLUMNS, f"{heading} columns are {header}, expected {COLUMNS}"


@pytest.mark.parametrize("heading", (EXTERNAL_HEADING, DOGFOOD_HEADING))
def test_every_row_has_the_columns_it_claims(heading: str):
    """A stray `|` in a description silently shifts every later cell, so a
    date lands in `Use` and a link lands in `Since`. Cell count is checked
    before any cell is read, and the rest of this file only ever sees rows
    that survived it."""
    for row in _raw_rows(heading):
        assert len(row) == len(COLUMNS), (
            f"{heading} has a row with {len(row)} cells, expected {len(COLUMNS)}: {row}. "
            "Escape any literal '|' in a cell as '\\|'."
        )


@pytest.mark.parametrize(("heading", "row"), _all_entries(), ids=_entry_ids())
def test_every_field_of_every_entry_is_filled(heading: str, row: dict[str, str]):
    """A blank cell is an unstated claim, and an unstated claim gets read
    generously later."""
    for column in COLUMNS:
        assert row.get(column, "").strip(), f"{heading} row {row} has an empty {column!r}"


@pytest.mark.parametrize(("heading", "row"), _all_entries(), ids=_entry_ids())
def test_every_entry_states_a_defined_use_tier(heading: str, row: dict[str, str]):
    """`Use` is the only field anyone will quote. It comes from the closed
    vocabulary or the row does not merge."""
    assert row["Use"] in USE_TIERS, (
        f"{heading} row {row['Adopter']!r} states Use={row['Use']!r}; "
        f"the defined tiers are {USE_TIERS}"
    )


@pytest.mark.parametrize(("heading", "row"), _all_entries(), ids=_entry_ids())
def test_every_entry_is_dated_in_the_past(heading: str, row: dict[str, str]):
    """The date is what every published number is quoted against. A free-text
    date ("2026", "since launch") cannot be compared, and a future one cannot
    be true."""
    since = row["Since"]
    assert DATE_RE.match(since), f"{heading} row {row['Adopter']!r} has Since={since!r}, want YYYY-MM-DD"
    assert dt.date.fromisoformat(since) <= dt.date.today() + CLOCK_SKEW, (
        f"{heading} row {row['Adopter']!r} is dated in the future: {since}"
    )


@pytest.mark.parametrize(("heading", "row"), _all_entries(), ids=_entry_ids())
def test_every_entry_links_the_public_act_that_created_it(heading: str, row: dict[str, str]):
    """Consent is what the count rests on, so every row names where it was
    given — an issue, discussion or PR in this repository, not a private
    conversation somebody remembers."""
    hrefs = LINK_RE.findall(row["Entry"])
    assert len(hrefs) == 1, f"{heading} row {row['Adopter']!r} must link exactly one entry record"
    href = hrefs[0]
    assert re.fullmatch(rf"{re.escape(REPO_URL)}/(issues|pull|discussions)/\d+", href), (
        f"{heading} row {row['Adopter']!r} links {href!r}; the entry record must be an "
        "issue, pull request or discussion in this repository"
    )


@pytest.mark.parametrize(("heading", "row"), _all_entries(), ids=_entry_ids())
def test_every_entry_names_a_repository_or_says_private(heading: str, row: dict[str, str]):
    """Acceptance §1: private-repo entries are allowed at organization
    granularity. `private` is the whole vocabulary for that — a half-named
    private repository ("internal monorepo") discloses without being checkable."""
    repository = row["Repository"]
    if repository == "private":
        return
    hrefs = LINK_RE.findall(repository)
    assert len(hrefs) == 1 and re.fullmatch(
        r"https://github\.com/[\w.-]+/[\w.-]+", hrefs[0]
    ), (
        f"{heading} row {row['Adopter']!r} has Repository={repository!r}; "
        "expected a linked github.com/owner/repo or the single word 'private'"
    )


# --------------------------------------------------------------------------
# Counts
# --------------------------------------------------------------------------


def test_the_counts_are_the_row_counts():
    """The whole mechanism in one assertion: a published number equals rows
    somebody consented to. Removing a row without touching the count, or
    raising a count without a row, fails here."""
    counts = _counts()
    assert counts["External adopter entries"] == str(len(_entries(EXTERNAL_HEADING)))
    assert counts["Maintainer dogfooding entries"] == str(len(_entries(DOGFOOD_HEADING)))


def test_the_counts_section_publishes_exactly_two_numbers_and_a_date():
    """A third labelled number — a total, a "teams", a trend — is a claim with
    no denominator. There are two counts here and they are never summed."""
    assert set(_counts()) == {
        "Counts as of",
        "External adopter entries",
        "Maintainer dogfooding entries",
    }


def test_the_as_of_date_is_not_older_than_the_newest_entry():
    """Adding a row without re-dating the counts publishes yesterday's number
    as today's. This is the coupling that makes that impossible."""
    as_of = dt.date.fromisoformat(_counts()["Counts as of"])
    for heading, row in _all_entries():
        assert dt.date.fromisoformat(row["Since"]) <= as_of, (
            f"{heading} row {row['Adopter']!r} is dated {row['Since']}, after the "
            f"stated as-of date {as_of}. Re-date § Counts in the same change."
        )
    assert as_of <= dt.date.today() + CLOCK_SKEW, (
        f"§ Counts is dated in the future: {as_of}"
    )


def test_the_empty_external_table_says_so_in_words():
    """An empty table renders as nothing much and reads as an oversight. The
    zero is a result, so it is stated — and the sentence has to go when the
    first row arrives, or the file contradicts itself."""
    section = _flat(_section(_read(REGISTRY), EXTERNAL_HEADING))
    if _entries(EXTERNAL_HEADING):
        assert EMPTY_MARKER not in section, (
            "§ External adopters has rows but still says there are none"
        )
    else:
        assert EMPTY_MARKER in section, (
            f"§ External adopters is empty and must say so: {EMPTY_MARKER!r}"
        )


# --------------------------------------------------------------------------
# Dogfooding stays out of the external count
# --------------------------------------------------------------------------


def test_this_repository_is_never_an_external_adopter():
    """Acceptance §5. The self-entry is allowed and useful; counting it as
    external adoption is the single most tempting error this file can make."""
    for row in _entries(EXTERNAL_HEADING):
        blob = _flat(f"{row['Adopter']} {row['Repository']}").lower()
        assert "threemoonslab" not in blob and "three moons lab" not in blob, (
            f"{row['Adopter']!r} is a maintainer entry listed as external; "
            f"it belongs under {DOGFOOD_HEADING}"
        )


def test_the_registry_says_a_dogfood_entry_cannot_carry_an_external_claim():
    """Acceptance §5 again, in the file itself: the separation has to survive
    somebody reading only the counts."""
    flat = _flat(_read(REGISTRY)).lower()
    assert "never added together" in flat
    assert "cannot satisfy an external adoption claim or an external repeat-use claim" in flat


def test_the_maintainer_row_agrees_with_the_paragraph_that_explains_it():
    """The self-entry states `advisory CI` and the paragraph under it explains
    why that is the honest tier when `main` requires no check. Raising the row
    without rewriting the explanation would leave the file arguing against its
    own table — so it fails instead."""
    section = _flat(_section(_read(REGISTRY), DOGFOOD_HEADING))
    for row in _entries(DOGFOOD_HEADING):
        # "That row says `x`" today; "the Acme row says `x`" once there are
        # two. What is pinned is the claim-plus-tier, not one sentence opener —
        # a guard that only fit one row would be rewritten out on the day a
        # second one arrived.
        assert re.search(rf"row says `{re.escape(row['Use'])}`", section), (
            f"the maintainer row states Use={row['Use']!r}, which the prose "
            "beside it does not explain. State the tier and say why it is that one."
        )


def test_the_dogfood_section_is_marked_as_maintainer_operated():
    section = _flat(_section(_read(REGISTRY), DOGFOOD_HEADING)).lower()
    assert "operated by the maintainers" in section
    assert "never counted as external" in section


# --------------------------------------------------------------------------
# The tier vocabulary, defined once
# --------------------------------------------------------------------------


def test_the_registry_defines_exactly_the_tiers_the_guard_knows():
    """Two tables of tiers eventually disagree, and the disagreement is always
    resolved in the flattering direction."""
    section = _section(_read(REGISTRY), "## What the Use column means")
    defined = tuple(re.findall(r"\*\*`([^`]+)`\*\*", section))
    assert defined == USE_TIERS, f"registry defines {defined}, guard knows {USE_TIERS}"


def test_the_blocking_tier_requires_a_required_check():
    """`blocking CI` is the claim worth having and therefore the one worth
    defining tightly: failing a run that nothing requires blocks nothing. This
    repository's own row is `advisory CI` for exactly that reason."""
    section = _flat(_section(_read(REGISTRY), "## What the Use column means")).lower()
    assert "required on the protected branch" in section
    assert "fails on at least one merge verdict" in section


def test_the_registry_refuses_to_infer_a_tier():
    """Acceptance: do not infer a stronger tier from a badge."""
    flat = _flat(_read(REGISTRY)).lower()
    assert "self-reported" in flat
    assert "not from a badge" in flat


# --------------------------------------------------------------------------
# The issue form: the low-friction path, and the one that records consent
# --------------------------------------------------------------------------


def test_the_issue_form_offers_exactly_the_registry_tiers():
    """A form offering a tier the registry does not define produces rows that
    cannot be merged — and a form missing one silently discourages the honest
    answer."""
    dropdowns = [
        block for block in _template()["body"] if block.get("type") == "dropdown"
    ]
    assert len(dropdowns) == 1, "the adopter form has exactly one tier dropdown"
    assert tuple(dropdowns[0]["attributes"]["options"]) == USE_TIERS


def test_the_issue_form_asks_for_every_field_the_requester_owns():
    """`Since` and `Entry` are not asked for on purpose: the date is when the
    row lands and the entry link is this issue. Every other column is the
    requester's to state, so the form must ask for it."""
    fields = _template_fields()
    asked = tuple(label for label in fields if label != "Consent")
    assert asked == REQUESTER_COLUMNS, f"form asks {asked}, expected {REQUESTER_COLUMNS}"
    assert set(COLUMNS) - set(REQUESTER_COLUMNS) == {"Since", "Entry"}
    for label in REQUESTER_COLUMNS:
        assert fields[label].get("validations", {}).get("required") is True, (
            f"{label!r} is part of every row, so the form must require it"
        )


def test_the_issue_form_takes_consent_and_requires_it():
    """An optional consent box is not consent. Both statements — speaking for
    the adopter, and understanding what publication means — are required."""
    consent = _template_fields()["Consent"]
    assert consent["type"] == "checkboxes"
    options = consent["attributes"]["options"]
    assert len(options) == 2 and all(option.get("required") is True for option in options)
    joined = _flat(" ".join(option["label"] for option in options)).lower()
    assert "speak for" in joined
    assert "public" in joined and "removed on request" in joined


def test_the_issue_form_states_the_private_repository_route():
    """Somebody with a private repository must be able to see, before typing,
    that they can be counted without naming it."""
    markdown = _flat(
        " ".join(
            block["attributes"]["value"]
            for block in _template()["body"]
            if block.get("type") == "markdown"
        )
    ).lower()
    assert "private" in markdown and "organization granularity" in markdown


# --------------------------------------------------------------------------
# The badge
# --------------------------------------------------------------------------


def test_the_badge_snippet_links_back_to_the_registry():
    """Acceptance §2. A badge pointing anywhere else is decoration; pointing
    here it is at least a route to the rules it does not itself prove."""
    section = _section(_read(REGISTRY), "## Badge")
    fences = re.findall(r"```markdown\n(.*?)```", section, flags=re.DOTALL)
    assert len(fences) == 1, "§ Badge must offer exactly one snippet"
    snippet = fences[0].strip()
    match = re.fullmatch(r"\[!\[[^\]]+\]\((https://img\.shields\.io/[^)]+)\)\]\(([^)]+)\)", snippet)
    assert match, f"§ Badge snippet is not a linked shields.io badge: {snippet!r}"
    assert match.group(2) == BADGE_TARGET, (
        f"badge links {match.group(2)!r}, expected the registry at {BADGE_TARGET!r}"
    )


def test_the_badge_claims_no_tier_and_creates_no_entry():
    """The badge is the one artifact that travels without its context, so the
    rules that keep it from becoming evidence live beside it — and there is
    exactly one badge, because a second one would be a tier variant."""
    section = _flat(_section(_read(REGISTRY), "## Badge"))
    lowered = section.lower()
    assert "tier-neutral" in lowered
    assert "a badge is not an entry" in lowered
    badges = re.findall(r"https://img\.shields\.io/\S+", section)
    assert len(badges) == 1, (
        f"§ Badge offers {len(badges)} badges: {badges}. One, deliberately — a "
        "second is a tier variant, and a tier variant is an unverifiable claim."
    )
    for tier in USE_TIERS:
        token = tier.replace(" ", "%20").lower()
        assert token not in lowered, (
            f"a {tier!r} badge variant invites exactly the inference the policy forbids"
        )


# --------------------------------------------------------------------------
# Claims policy
# --------------------------------------------------------------------------


def test_every_claims_rule_is_present_and_numbered():
    """The policy is the deliverable; the rows are only its input. A rule that
    disappears takes its constraint with it and nothing else changes."""
    section = _section(_read(REGISTRY), "## Claims policy")
    found = tuple(re.findall(r"^\d+\. \*\*(.+?)\*\*", section, flags=re.MULTILINE))
    assert found == CLAIMS_RULES, f"claims rules are {found}, expected {CLAIMS_RULES}"


def test_the_registry_and_the_pilot_ledger_point_at_each_other():
    """Two public counting mechanisms, two consents, one reader. Whichever page
    that reader lands on has to say the other exists and is not addable to it."""
    assert "ADOPTERS.md" in _read(PILOT_RESULTS), (
        "the pilot ledger must name the adopters registry"
    )
    assert "ADOPTERS.md" in _read(PILOT_RUNBOOK), (
        "the pilot runbook must say enrollment does not create an entry"
    )
    assert "design-partner-pilot-results.md" in _read(REGISTRY)
    assert "never added together" in _flat(_read(PILOT_RESULTS)).lower()


# --------------------------------------------------------------------------
# No entry, no claim — enforced across the repository
# --------------------------------------------------------------------------

#: What makes a number an adoption claim. Getting this wrong in either
#: direction is fatal: too loose and it fires on "5 sentences of user
#: instructions" and "27 national company registries" — both real lines in
#: this repository — after which somebody deletes the guard rather than the
#: claim. Too tight and it is decoration. So a number counts as a claim only
#: when it reads as one: a use verb in front of it ("used by 40 teams"), a use
#: verb behind it ("40 teams run the gate"), or a noun that has no other
#: meaning here ("40 adopters").
_NUMBER = r"(\d[\d,]*)\+?\s+"
#: Up to two intervening words, but never a preposition or conjunction — that
#: is what separates "40 happy adopters" from "5 sentences of user".
_FILLER = r"(?:(?!of\b|in\b|for\b|to\b|from\b|with\b|per\b|and\b|or\b)\w+\s+){0,2}"
_UNAMBIGUOUS_NOUNS = r"(?:adopters?|customers?|users?)"
_NOUNS = rf"(?:{_UNAMBIGUOUS_NOUNS}|teams?|comp(?:any|anies)|organi[sz]ations?|orgs?)"
_USE_BEFORE = r"(?:used|run|adopted|trusted|deployed|gated)\s+by\s+"
_USE_AFTER = (
    r"\s+(?:use|uses|using|run|runs|running|gate|gates|rely|relies|ship|ships|"
    r"have\s+adopted|has\s+adopted|are\s+on)\b"
)

_CLAIM_RES = (
    re.compile(rf"\b{_USE_BEFORE}{_NUMBER}{_FILLER}{_NOUNS}\b", re.IGNORECASE),
    re.compile(rf"\b{_NUMBER}{_FILLER}{_NOUNS}{_USE_AFTER}", re.IGNORECASE),
    re.compile(rf"\b{_NUMBER}{_FILLER}{_UNAMBIGUOUS_NOUNS}\b", re.IGNORECASE),
)


def _claims(text: str):
    """Every adoption-claim match in ``text``, deduplicated by position."""
    seen: dict[int, re.Match[str]] = {}
    for pattern in _CLAIM_RES:
        for match in pattern.finditer(text):
            seen.setdefault(match.start(), match)
    return [seen[start] for start in sorted(seen)]


#: How much text around a number counts as its context when deciding whether
#: it is talking about dogfooding.
_CONTEXT = 120


def _untraceable_claims(text: str, counts: dict[str, str] | None = None) -> list[str]:
    """Adoption numbers in ``text`` that the registry cannot source.

    An unqualified adopter number must equal the **external** count. The
    dogfooding count only sources a number that says it is dogfooding — rule 3
    is that the two are never summed, and "used by 1 team" borrowing the
    maintainer row would be exactly that sum, one row large.

    ``counts`` is accepted so a caller sweeping hundreds of files parses the
    registry once rather than once per file.
    """
    counts = counts if counts is not None else _counts()
    external = counts["External adopter entries"]
    dogfood = counts["Maintainer dogfooding entries"]
    flat = _flat(text)
    untraceable = []
    for match in _claims(flat):
        number = match.group(1).replace(",", "")
        if number == external:
            continue
        context = flat[max(0, match.start() - _CONTEXT) : match.end() + _CONTEXT].lower()
        if number == dogfood and ("dogfood" in context or "maintainer" in context):
            continue
        untraceable.append(match.group(0))
    return untraceable


def _prose_files() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    return [
        REPO_ROOT / rel
        for rel in tracked
        if rel.endswith((".md", ".txt")) and not rel.startswith("tests/")
    ]


def test_no_published_adoption_number_is_larger_than_the_registry():
    """Rule 1, enforced rather than promised. Any adopter count stated anywhere
    in the repository's prose must be one the rows can source."""
    counts = _counts()
    offenders = {
        str(path.relative_to(REPO_ROOT)): claims
        for path in _prose_files()
        if (claims := _untraceable_claims(_read(path), counts))
    }
    assert not offenders, (
        f"adoption numbers with no rows behind them: {offenders}. Add the "
        "consenting entries to ADOPTERS.md, or do not publish the number."
    )


#: Claim shapes the scanner must catch. The number is filled in at run time
#: from :func:`_untraceable_number` — hard-coding one would quietly stop being
#: a control the day the registry's own count reached it, which is exactly what
#: happened to an earlier draft of this file when a test adopter was added.
UNTRACEABLE_CLAIM_SHAPES = (
    "Agents Shipgate is used by {n} teams in production.",
    "Trusted by {n} organizations.",
    "More than {n} adopters run the gate on every PR.",
    "Now with {n} users.",
    "Agents Shipgate is used by {n} team.",
    "{n} companies gate their agent changes with it.",
)


def _untraceable_number() -> str:
    """A count no row in the registry can source, whatever the rows say."""
    published = {int(value) for label, value in _counts().items() if value.isdigit()}
    return str(max(published, default=0) + 41)


@pytest.mark.parametrize("shape", UNTRACEABLE_CLAIM_SHAPES)
def test_the_claim_scanner_catches_an_untraceable_number(shape: str):
    """Negative control. The guard above passes today because nobody has
    written such a sentence; this is what proves it would fail if they did."""
    claim = shape.format(n=_untraceable_number())
    assert _untraceable_claims(claim), f"the scanner missed {claim!r}"


def test_the_claim_scanner_accepts_a_number_the_rows_can_source():
    """The other half of the control: a sourced number is not an offence, or
    the registry could never publish its own count."""
    counts = _counts()
    sourced = f"{counts['External adopter entries']} external adopter entries are listed."
    assert _untraceable_claims(sourced) == []


def test_a_dogfooding_number_only_sources_a_dogfooding_sentence():
    """Rule 3, at the scanner. The maintainer row is a real row, so its count
    is publishable — as dogfooding. Lent to an unqualified sentence it becomes
    the sum the policy forbids, and the last test case above is that sentence."""
    counts = _counts()
    dogfood = counts["Maintainer dogfooding entries"]
    if dogfood == counts["External adopter entries"]:
        pytest.skip("the two counts are equal, so this number is traceable either way")
    assert _untraceable_claims(f"{dogfood} maintainer dogfooding adopters are listed.") == []
    assert _untraceable_claims(f"Agents Shipgate is run by {dogfood} teams.")


def test_the_claim_scanner_ignores_this_repositorys_other_denominators():
    """Benchmark and pilot prose count repositories, PRs and servers. Those are
    measurements with their own denominators, not adoption claims, and a guard
    that swept them in would be disabled rather than obeyed."""
    for benign in (
        "across 361 mined rows from 8 distinct real agent repos",
        "the 30-server survey behind the registry",
        "19 unique labeled engine-engaged PRs",
        # Both of these are real lines elsewhere in the repository, and both
        # were false positives until the pattern stopped accepting a
        # preposition as filler.
        "the first 5 sentences of user instructions are preserved",
        "27 national company registries",
    ):
        assert _untraceable_claims(benign) == []


# --------------------------------------------------------------------------
# Internal links
# --------------------------------------------------------------------------


def test_registry_links_resolve():
    """A registry that links a moved doc or a renamed anchor teaches the reader
    that its rules are decorative."""
    text = _read(REGISTRY)
    headings = _headings(text)
    for href in LINK_RE.findall(text):
        if href.startswith(("http://", "https://", "mailto:")):
            continue
        if href.startswith("#"):
            assert href[1:] in headings, f"ADOPTERS.md links dead anchor {href}"
            continue
        path, _, anchor = href.partition("#")
        target = (REPO_ROOT / path).resolve()
        assert target.exists(), f"ADOPTERS.md links non-existent {href}"
        if anchor:
            assert anchor in _headings(target.read_text(encoding="utf-8")), (
                f"ADOPTERS.md links dead anchor {href}"
            )
