"""Guards for the design-partner pilot runbook and its public results ledger.

The pilot's whole value is that its numbers can be trusted, so the two pages
carry claims that are easy to soften by accident: a denominator quietly
dropped, dogfooding folded into the external count, a target restated as an
achievement, or a build-dated finding left standing after that build ships.
Each of those is pinned here.

Issue #521.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
RUNBOOK = DOCS_DIR / "design-partner-verifier-pilot.md"
RESULTS = DOCS_DIR / "design-partner-pilot-results.md"

# The six denominators the experiment reports. They are named in the runbook's
# table and reported in the results ledger; neither page may drop one.
DENOMINATORS = (
    "invited",
    "attempted",
    "first_valid_result",
    "first_value",
    "second_change_eligible",
    "second_change_observed",
)

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)#:][^)#]*)(?:#[^)]*)?\)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Markdown here is hard-wrapped, so a phrase this file asserts on is
    usually split across two lines. Compare against the unwrapped form."""
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    """The body under ``heading``, up to the next same-or-higher heading."""
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(heading)
    body = text[start + len(heading) :]
    nxt = re.search(rf"^#{{1,{level}}} ", body, flags=re.MULTILINE)
    return body[: nxt.start()] if nxt else body


def _latest_published_tag() -> str:
    """The newest tag that actually exists, from the discovery surface that
    already tracks it (`test_public_surface_contract` pins this field to
    `LATEST_PUBLISHED_VERSION`, so there is one source of truth, not two)."""
    data = json.loads(_read(REPO_ROOT / ".well-known" / "agents-shipgate.json"))
    return str(data["release_status"]["latest_release"])


@pytest.mark.parametrize("path", (RUNBOOK, RESULTS))
def test_pilot_pages_exist_and_link_each_other(path: Path):
    """The runbook is how to observe and the ledger is what was observed.
    Either alone reads as a complete document, which is how a reader ends up
    citing a protocol with no results or results with no method."""
    assert path.is_file(), f"{path} must exist"
    text = _read(path)
    other = RESULTS.name if path is RUNBOOK else RUNBOOK.name
    assert other in text, f"{path.name} must link {other}"


@pytest.mark.parametrize("path", (RUNBOOK, RESULTS))
def test_pilot_page_internal_links_resolve(path: Path):
    for href in LINK_RE.findall(_read(path)):
        href = href.strip()
        if not href or href.startswith(("http://", "https://", "mailto:")):
            continue
        target = (path.parent / href).resolve()
        assert target.exists(), f"{path.name} links to non-existent {href}"


@pytest.mark.parametrize("denominator", DENOMINATORS)
def test_every_denominator_is_defined_and_reported(denominator: str):
    """A denominator that is defined but never reported is how a shortfall
    disappears: the count nobody publishes is the count nobody has."""
    assert denominator in _read(RUNBOOK), (
        f"docs/{RUNBOOK.name} must define the `{denominator}` denominator."
    )
    reported = _section(_read(RESULTS), "## Denominators")
    assert denominator in reported, (
        f"docs/{RESULTS.name} § Denominators must report `{denominator}`. "
        "Every denominator the runbook defines is reported, including the "
        "ones that are zero."
    )


def test_results_denominator_table_gives_a_number_for_every_row():
    """Each denominator row carries an actual count. An empty cell or a dash
    reads as 'not applicable' when it means 'zero', and those are different
    claims."""
    section = _section(_read(RESULTS), "## Denominators")
    for denominator in DENOMINATORS:
        row = next(
            (
                line
                for line in section.splitlines()
                if line.startswith("|") and f"`{denominator}`" in line
            ),
            None,
        )
        assert row is not None, f"no table row for `{denominator}`"
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert len(cells) >= 2, f"row for `{denominator}` has no count cell: {row!r}"
        assert re.fullmatch(r"\d+", cells[1]), (
            f"the count cell for `{denominator}` is {cells[1]!r}; it must be a "
            "number. Zero is a result; a blank is not."
        )


def test_dogfooding_is_excluded_from_the_external_denominators():
    """Dogfooding never fills the external denominator. Both pages have to say
    so, because the ledger is where the substitution would actually happen and
    the runbook is where someone looks up whether it is allowed."""
    runbook = _flat(_read(RUNBOOK))
    assert "Dogfooding is separate" in runbook
    assert "never enter these" in runbook

    results = _flat(_read(RESULTS))
    assert "Dogfooding is reported separately below and never enters these counts." in results
    assert "(dogfooding)" in results, (
        "the results ledger must label its own dry run as dogfooding at the "
        "section that reports it, not only in a distant caveat."
    )


def test_ten_minute_figure_stays_a_target():
    """`10 minutes` is an experiment target. The failure mode is not someone
    lying — it is the target being restated later as a measured result, so
    every page that names the number also has to name it as a target."""
    for path in (RUNBOOK, RESULTS):
        text = _read(path)
        assert "10 minutes" in text, f"{path.name} must state the 10-minute figure"
        for match in re.finditer(r"10 minutes", text):
            window = text[max(0, match.start() - 400) : match.end() + 400]
            assert "target" in window, (
                f"{path.name}: the 10-minute figure must be labelled a target "
                "within its own paragraph."
            )
            for sentence in re.split(r"(?<=[.;])\s+", _flat(window)):
                if not re.search(
                    r"\b(achieved|achieves|we reached|median|average|measured at)\b",
                    sentence,
                ):
                    continue
                assert re.search(r"\b(not|never|no|nothing)\b", sentence), (
                    f"{path.name}: the 10-minute figure reads as a measured "
                    f"result in {sentence!r}. It is a target until a run is "
                    "scored against it."
                )


def test_results_ledger_carries_a_status_date():
    text = _read(RESULTS)
    assert re.search(r"\*\*Status date: \d{4}-\d{2}-\d{2}\.\*\*", text), (
        "the results ledger must carry a status date; an undated ledger of "
        "zeros cannot be told apart from a stale one."
    )


def test_shortfall_report_is_dated():
    """Acceptance allows a dated shortfall report in place of three attempts.
    Dated is the load-bearing word."""
    text = _read(RESULTS)
    assert re.search(r"^## Enrollment and opportunity shortfall — \d{4}-\d{2}-\d{2}$", text, re.M)


def test_standing_decision_is_dated_and_names_one_of_the_three_outcomes():
    text = _read(RESULTS)
    match = re.search(
        r"^## Standing decision — (\d{4}-\d{2}-\d{2}): \*\*(continue|narrow|stop)\*\*$",
        text,
        re.M,
    )
    assert match, (
        "the results ledger must end on an explicit dated continue/narrow/stop "
        "decision, spelled as one of those three words."
    )
    decision = _flat(_section(text, match.group(0)))
    assert "Blocking CI" in decision, (
        "the decision must say whether any partner asked for blocking CI."
    )
    assert "What would change this decision" in decision, (
        "a standing decision that does not say what would overturn it cannot "
        "be revisited by anyone but its author."
    )


def test_decision_rule_is_pre_registered_in_the_runbook():
    """The rule lives with the method, not with the data, so the thresholds
    cannot be chosen after the counts are in."""
    rule = _flat(_section(_read(RUNBOOK), "## Decision rule"))
    for outcome in ("Continue", "Narrow", "Stop"):
        assert f"**{outcome}**" in rule, f"the decision rule must define {outcome}"
    assert "Pre-registered" in rule


def test_consent_is_three_separate_grants():
    """One blanket 'yes' must not unlock a name, a link and a raw bundle."""
    section = _flat(_section(_read(RUNBOOK), "## Consent and redaction"))
    assert "granted separately" in section
    for grant in ("Public naming", "Source and PR links", "Raw bundles"):
        assert grant in section, f"the consent rule must name `{grant}` as its own grant"
    assert "aggregate-only" in section
    assert "not permission for automated or unsolicited messages" in section


def test_first_value_requires_all_four_recognitions_and_a_decision():
    section = _flat(_section(_read(RUNBOOK), "## First value, defined"))
    for recognition in ("capability", "evidence", "coverage limit", "next action"):
        assert recognition in section, f"first value must require naming the {recognition}"
    assert "exit of zero is not this event" in section
    assert "records a concrete decision or fix" in section


def test_tracker_template_carries_every_required_observation_field():
    """The tracker is the only place these get written down. A field missing
    from the template is a field nobody records."""
    tracker = _flat(_section(_read(RUNBOOK), "## Success Tracker"))
    required = (
        "Entry point",
        "Build installed",
        "Contract (`contract_version`)",
        "Environment",
        "Installation attempted",
        "Setup steps performed",
        "Maintainer assistance given",
        "Time to first valid result",
        "Reviewer (distinct from author)",
        "Time to first value",
        "Existing alternative in place",
        "Work Agents Shipgate saved",
        "Work Agents Shipgate added",
        "Named request delivered",
        "Recorded continuation",
        "Ran again, unprompted",
        "CI still enabled",
        "Second reviewer acted without translation",
        "Disabled / bypassed, and why",
        "Consent: public naming",
        "Consent: raw bundle",
    )
    missing = [field for field in required if field not in tracker]
    assert not missing, f"docs/{RUNBOOK.name} § Success Tracker is missing rows: {missing}"


def test_second_change_window_is_stated_with_its_unobserved_rule():
    section = _flat(_section(_read(RUNBOOK), "## The second eligible change"))
    assert "four-week observation window" in section
    assert "Silence is not retention." in section


def test_review_required_outcomes_report_the_missing_continuation():
    """Before #337 there is no authenticated continuation. The honest record
    of that is a reported gap, not an omitted row."""
    section = _flat(_section(_read(RUNBOOK), "## Review-required outcomes"))
    assert "issues/337" in section
    assert "cannot record `continued`" in section
    assert "unresolved" in section


def test_runbook_does_not_assert_an_unpublished_contract_floor():
    """The runbook used to require "runtime contract 14" while telling the
    partner to `pipx install`, and no published build has ever carried
    contract 14. The repair is to record the installed build, so the doc may
    no longer assert a floor at all."""
    text = _flat(_read(RUNBOOK))
    assert not re.search(r"contract\s+1[0-9]\b", text), (
        "the runbook must not assert a numeric contract floor; published "
        "builds may not carry it. Tell the partner to record "
        "`contract --json` instead."
    )
    assert "agents-shipgate contract --json" in text
    assert "agents-shipgate --version" in text


def test_runbook_routes_host_boundary_partners_away_from_a_manifest():
    """#521 is explicit that the supported host-boundary route must not
    require a manifest. The dry run found `init --write` then `verify` dead-
    ending on exactly those repositories."""
    text = _flat(_read(RUNBOOK))
    section = _flat(_section(_read(RUNBOOK), "## Routes under test"))
    assert "Do not require a partner on Route H to maintain a manifest." in section
    assert "CHANGE_ME" in section
    assert "this repository is on Route H" in text, (
        "the pilot commands must tell a partner what to do when `init` emits "
        "a CHANGE_ME scaffold, not leave them to invent a manifest."
    )


def test_route_readiness_findings_name_the_build_they_were_measured_on():
    """These findings are true of one published build on one date. When a new
    tag ships they are unverified until re-run — so the ledger names the tag
    it measured, and this guard fails the moment that tag moves."""
    section = _flat(_section(_read(RESULTS), "### Route readiness dry run (dogfooding)"))
    tag = _latest_published_tag()
    version = tag.lstrip("v")
    assert version in section, (
        f"docs/{RESULTS.name} § Route readiness dry run measured a build that "
        f"is no longer the newest published one ({tag}). Re-run the documented "
        "route against the new release, then re-date the section and the "
        "standing decision. The published findings are build-dated claims and "
        "cannot be carried forward untested."
    )


def test_reproduced_blockers_route_to_existing_issues():
    """"Turn reproduced blockers into updates to existing issues" — so every
    row either cites an issue link or says the fix landed here."""
    section = _section(_read(RESULTS), "## Blockers reproduced, and where they belong")
    rows = [
        line
        for line in section.splitlines()
        if line.startswith("|") and "---" not in line and "Existing issue" not in line
    ]
    assert len(rows) >= 5, "the blocker ledger looks empty"
    for row in rows:
        assert "agents-shipgate/issues/" in row or "fixed in this runbook" in row, (
            f"blocker row cites no existing issue: {row!r}. A reproduced "
            "blocker updates an existing issue; it does not open a feature."
        )
