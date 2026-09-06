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


def _sentence_around(text: str, index: int) -> str:
    """The sentence containing ``index`` in already-flattened text."""
    start = max((text.rfind(mark, 0, index) for mark in (". ", "? ", "! ", "; ")), default=-1)
    end_candidates = [pos for pos in (text.find(mark, index) for mark in (". ", "? ", "! ", "; "))
                      if pos != -1]
    end = min(end_candidates) + 1 if end_candidates else len(text)
    return text[start + 1 : end].strip()


def _logical_lines(text: str) -> list[str]:
    """Shell continuations joined, so a flag and the command it belongs to are
    on one line. `--save-baseline \\` / `--baseline-file <path>` is one
    instruction split across two source lines, and checking them separately
    would accept a snapshot that is written and never read."""
    joined: list[str] = []
    buffer = ""
    for line in text.splitlines():
        buffer += line.rstrip()
        if buffer.endswith("\\"):
            buffer = buffer[:-1] + " "
            continue
        joined.append(buffer)
        buffer = ""
    if buffer:
        joined.append(buffer)
    return joined


def _section(text: str, heading: str) -> str:
    """The body under ``heading``, up to the next same-or-higher heading.

    A shell comment inside a fenced block starts with ``# `` in column one and
    is not a heading — these pages contain several. Fences are tracked so a
    section is not silently truncated at one, which would make every assertion
    against the rest of it vacuous.
    """
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(heading)
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


def test_decision_rule_selects_exactly_one_outcome():
    """Three independently-worded conditions overlap. PR #523 review supplied
    the cohort that proves it: two repositories reach first value, one of them
    runs again, a third fails on a product defect — plainly-worded continue,
    narrow and stop are all true at once, so the "pre-registered" rule licenses
    any answer. The repair is precedence, and precedence only works if it is
    ordered, total, and stated."""
    rule = _flat(_section(_read(RUNBOOK), "## Decision rule"))
    assert "Evaluate the rungs in order and take the first that matches." in rule, (
        "the decision rule must state its precedence explicitly; three "
        "conditions with no ordering are not a pre-registered rule."
    )
    assert "anything else" in rule, (
        "one rung must be the default, or a cohort can match no rung at all "
        "and the rule stops being total."
    )

    # The ordered table itself: rung numbers ascending, one outcome each.
    section = _section(_read(RUNBOOK), "## Decision rule")
    rungs = re.findall(r"^\|\s*(\d+)\s*\|\s*\*\*(\w+)\*\*\s*\|", section, re.M)
    assert [n for n, _ in rungs] == ["1", "2", "3"], (
        f"the rungs must be numbered 1..3 in order; got {rungs}"
    )
    assert sorted(outcome for _, outcome in rungs) == ["Continue", "Narrow", "Stop"], (
        f"each rung must carry a distinct outcome; got {rungs}"
    )


def test_decision_rule_works_the_ambiguous_cohorts():
    """A precedence table is only checkable against examples. These two are the
    ones that were ambiguous before: the review's counterexample, and the
    all-failed-at-entry cohort where "stop" would read a fact about this
    repository as a fact about the market."""
    section = _flat(_section(_read(RUNBOOK), "## Decision rule"))
    assert "Worked evaluations" in section
    assert "the third fails on a product defect" in section, (
        "the worked evaluations must cover the mixed-success cohort from the "
        "PR #523 review, which satisfied all three plainly-worded conditions."
    )
    assert "all fail at install on an entry defect" in section, (
        "the worked evaluations must cover a cohort that never reached a valid "
        "result; rung 2 must not claim that as a demonstrated review failure."
    )
    assert "routes and only one repeated" in section, (
        "the worked evaluations must cover a single-route vs multi-route "
        "cohort, since 'only one route' is vacuous when only one was tested."
    )


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
        # The standing decision turns on the channel, so a row that does not
        # record which one the partner was on cannot be read against it later.
        "Channel (released / preview / source checkout)",
        "Qualification status told to the partner",
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


def test_route_h_makes_the_baseline_reachable_from_the_change():
    """Reproduced in the PR #523 review: a partner's PR branch is cut before
    the baseline commit, so checking it out removes the baseline file and
    `--drift` exits 2. The CLI's own recovery line then says to record a
    baseline — which, run from the changed checkout, acknowledges the very
    expansion under review and makes the drift report nothing. Both the
    command block and the agent prompt have to carry the remedy and the
    prohibition, because a partner follows whichever one they were handed."""
    text = _flat(_read(RUNBOOK))
    sections = {
        "Pilot Commands": _section(_read(RUNBOOK), "## Pilot Commands"),
        "Partner Agent Prompt": _section(_read(RUNBOOK), "## Partner Agent Prompt"),
    }

    for where, raw in sections.items():
        body = _flat(raw)
        # Mentioning `--baseline-file` is not the same as using it on both
        # ends. A snapshot written to a custom path and then never read back
        # leaves the drift command hitting the same missing-baseline error the
        # step exists to avoid, so both halves are required.
        uses = [line for line in _logical_lines(raw) if "--baseline-file" in line]
        assert any("--save-baseline" in line for line in uses), (
            f"§ {where} must record the snapshot with "
            "`--save-baseline --baseline-file <path>`."
        )
        assert any("drift" in line for line in uses), (
            f"§ {where} names `--baseline-file` but never passes it to a drift "
            "command; the snapshot would be written and then not read."
        )
        assert "merge" in body and "rebase" in body, (
            f"§ {where} must offer merging or rebasing the reviewed baseline "
            "into the change branch as the other remedy."
        )
        assert "exits 2" in body, (
            f"§ {where} must name the failure a partner will actually hit, not "
            "only the remedy — an unexplained extra step gets skipped."
        )
    assert "Never `--save-baseline` from the changed checkout" in text or (
        "Never run --save-baseline from the changed checkout" in text
    ), "the runbook must prohibit re-baselining from the changed checkout."
    assert "acknowledges the very expansion under review" in text or (
        "acknowledges the expansion being reviewed" in text
    ), "the prohibition must say why, or it reads as arbitrary."


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
    # The original defect was a *floating* number: "the verify and feedback
    # commands require Agents Shipgate runtime contract 14", in the paragraph
    # that installed it with `pipx install`, when no published build has ever
    # carried 14. What makes that unsafe is not the number — naming one is
    # often necessary, and the channel table names three — it is that the
    # number is attached to nothing, so no reader can check it. So the rule is
    # the one this repository already applies to stale claims: bind the claim
    # to what it claims. Every contract number names the build that carries it.
    subjects = re.compile(
        r"(v?\d+\.\d+\.\d+|released|release|preview|source checkout|this tree|"
        r"installed build|checkout|channel)",
        re.I,
    )
    for match in re.finditer(r"contract\s+\d+\b", text):
        sentence = _sentence_around(text, match.start())
        assert subjects.search(sentence), (
            f"an unbound contract number in {sentence!r}. Name the build that "
            "carries it — a floating floor is what made the old precondition "
            "unsatisfiable by the `pipx install` printed beside it, and a "
            "partner had no way to see the contradiction."
        )
    assert "agents-shipgate contract --json" in text
    assert "agents-shipgate --version" in text


def test_runbook_routes_host_boundary_partners_away_from_a_manifest():
    """#521 is explicit that the supported host-boundary route must not
    require a manifest. The dry run found `init --write` then `verify` dead-
    ending on exactly those repositories.

    The property is unchanged; the instruction it pinned was wrong. This used to
    require the literal "this repository is on Route H" on a `CHANGE_ME`
    scaffold, and a scaffold does not establish that: `tests/
    test_init_scaffold_disclosure.py`'s FastMCP reproduction publishes real
    `@server.tool` functions, scaffolds anyway because they sit under an import
    package, and `audit --host` on it reports only generic instruction trust
    roots — so following that instruction abandoned the tool surface the partner
    came to review. The runbook must still say what to do, and must no longer
    say that (#498 review).
    """
    text = _flat(_read(RUNBOOK))
    section = _flat(_section(_read(RUNBOOK), "## Routes under test"))
    assert "Do not require a partner on Route H to maintain a manifest." in section
    assert "CHANGE_ME" in section
    assert "Never invent a manifest." in text, (
        "the pilot commands must tell a partner what to do when `init` emits "
        "a CHANGE_ME scaffold, not leave them to invent a manifest."
    )
    assert "exported MCP tool list" in text, (
        "the scaffold instruction must name the input that would let `verify` "
        "read the surface, or the partner has nowhere to go."
    )
    assert "this repository is on Route H" not in text, (
        "a `CHANGE_ME` scaffold says discovery could not read a surface, not "
        "that the repository only has a coding-host boundary. Re-asserting that "
        "sends a partner with a real tool surface to an audit that never looks "
        "at it."
    )


def test_route_readiness_findings_name_the_build_they_were_measured_on():
    """These findings are true of one published build on one date. When a new
    tag ships they are unverified until re-run, so the ledger states the build
    it measured on its own labelled line and this guard compares that line with
    the newest tag that exists.

    Matching a bare version literal anywhere in the section would not do it:
    the section also names the *in-tree* build, so the release of that very
    version would satisfy the check by coincidence — the guard would go quiet
    at exactly the moment it is supposed to fire."""
    section = _section(_read(RESULTS), "### Route readiness dry run (dogfooding)")
    match = re.search(r"\*\*Published build measured: `([^`]+)`\.\*\*", section)
    assert match, (
        f"docs/{RESULTS.name} § Route readiness dry run must state the build "
        "it measured as `**Published build measured: `X.Y.Z`.**`."
    )
    measured = match.group(1)
    expected = _latest_published_tag().removeprefix("v")
    assert measured == expected, (
        f"docs/{RESULTS.name} § Route readiness dry run measured {measured}, "
        f"but the newest published build is now {expected}. Re-run the "
        "documented route against the new release, then re-date the section "
        "and the standing decision. These are build-dated claims and cannot "
        "be carried forward untested."
    )


def test_route_readiness_source_tree_row_matches_this_tree():
    """The published-tag guard above fires when a release ships. It did not
    fire when #506, #485 and #497 merged, and the page went stale inside a day
    — the ledger records that gap. This closes the half of it that can be
    checked offline: the source-tree column is a claim about *this* tree, so
    its version and contract must be this tree's. A contract bump on main now
    forces the dry run to be re-run rather than carried forward.

    The other half — a newly cut preview — needs the network and stays a
    documented limitation rather than a guard that lies about its coverage."""
    from agents_shipgate import __version__
    from agents_shipgate.schemas.contract import CONTRACT_VERSION

    section = _flat(_section(_read(RESULTS), "### Route readiness dry run (dogfooding)"))
    match = re.search(r"Source tree: `([^`]+)`", section)
    assert match, (
        f"docs/{RESULTS.name} § Route readiness dry run must name the source "
        "tree build it measured, as ``Source tree: `X.Y.Z```."
    )
    assert match.group(1) == __version__, (
        f"the ledger measured source tree {match.group(1)}, but this tree is "
        f"{__version__}. Re-run the dry run and re-date the section."
    )

    raw = _section(_read(RESULTS), "### Route readiness dry run (dogfooding)")
    row = next(
        (line for line in raw.splitlines() if line.startswith("| Runtime contract |")),
        None,
    )
    assert row is not None, "the channel matrix must carry a `Runtime contract` row"
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    assert cells[-1] == CONTRACT_VERSION, (
        f"the ledger's source-tree contract is {cells[-1]!r}, but this tree "
        f"emits {CONTRACT_VERSION!r}. The measurement predates a contract bump; "
        "re-run the dry run rather than citing it."
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
