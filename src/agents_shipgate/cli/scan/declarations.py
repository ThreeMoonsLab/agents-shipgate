"""The declaration questionnaire, assembled from what the engine derived.

Several evidence gaps can only be closed by a reviewed human declaration —
what a tool's effect is, what authority it runs with, which object is the root
agent. The decision engine already generates the exact manifest snippet each
one wants (``EvidenceGapAction.declaration_template``), but until now those
snippets were only reachable inside ``report.json`` at
``release_decision.evidence_coverage.evidence_gaps[].next_action.declaration_template``,
which made a one-time, three-line task look like schema archaeology.

This module assembles them into one reviewable YAML document next to
``report.json``, the same way ``suggested-inventory.json`` is written for
low-confidence sources. Deciding remains entirely the decision engine's job.

**Self-sufficiency is the point** (#388). The file a user is told to edit was
the one file that did not say what a legal answer looks like: ``effect:
<REVIEW_REQUIRED>`` with the nine accepted values sitting in ``report.json``,
and an ``agent_bindings.root`` block with two blanks whose answer the scan had
already observed. Every sentinel now carries the vocabulary or the shape it
takes, as a comment, and where the scan observed candidates they are listed for
a human to confirm.

**A numbered questionnaire, not a blank form** (#410 increment 2). A pile of
blanks has no finish line and no order: the fourth ``adk-samples#1745`` walk
reached a verdict after answering 2 of 12 actions — the two that moved money
and communicated outward — so the blocks are numbered, counted, and ordered by
how much answering them can move the verdict. Both numbers come from
``semantic_coverage.declaration_questions``, so the file and the report cannot
disagree about how much work is left.

That order leads with the actions nothing has *bounded* — no evidence, a
protocol default standing in for its absence, or only a heuristic reading the
scan may not act on — rather than with the ones it read as risky (#419); see
``core.declaration_questions``. The header says so, and a block with no
reading at all to print says so too, because a blank that printed nothing let
its silence read as "nothing to see here".

**Where evidence supports one conservative answer, it is filled in.** The scan
already read ``request_refund_approval`` as a financial write; asking a human
to retype that is the cost that stalls adoption, and the readings behind it are
printed above the value so the answer can be checked in place rather than
taken on trust. A pre-filled value is a *proposal*, and the distinction is
mechanical, not editorial:

* nothing consumes this file — only a reviewed edit to ``shipgate.yaml``, the
  trust root, makes any of it operative;
* the proposed value comes from the closed ``ActionEffect`` vocabulary, never
  from source content, so no repository can put a word of its choosing here;
* it is never weaker than any reading (see ``propose_effect_declaration``), so
  confirming one without thinking over-declares rather than under-declares —
  and it is offered only where something was actually observed, never from a
  protocol default standing in for the absence of evidence.

Everything a human owns and the scan did not observe still reads
``<REVIEW_REQUIRED>``, and a block still carrying one closes nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

import yaml

from agents_shipgate.ci.release_decision import REVIEW_REQUIRED_SENTINEL
from agents_shipgate.core.declaration_questions import (
    DIMENSION_BY_GAP_KIND,
    progress_sentence,
)
from agents_shipgate.core.evidence_actions import (
    display_literal,
    evidence_gap_action_text,
    one_line,
    yaml_scalar,
)
from agents_shipgate.schemas.bindings import AgentBindingNode
from agents_shipgate.schemas.report import (
    DeclarationQuestionCoverage,
    DeclarationQuestionRow,
    EvidenceGap,
    EvidenceReading,
    ReadinessReport,
)

# Which template field an action's ``accepted_values`` is the vocabulary FOR.
#
# ``accepted_values`` is overloaded on the wire: for most gap kinds it lists
# the manifest *keys* a repair must set (``["agent", "complete:true", …]``),
# and for these two it lists the legal *values* of one field. Only the second
# reading may be printed above a blank — rendering key names as "accepted
# values" would tell a reviewer that `agent` is a legal `effect`.
#
# The values themselves are never copied here. The annotation is rendered from
# the gap's own ``accepted_values``, so the two artifacts cannot disagree about
# the vocabulary; only this routing could ever drift, and a test pins it.
_VOCABULARY_FIELD_BY_ACTION_KIND: dict[str, str] = {
    "declare_action_effect": "effect",
    "declare_action_authority": "authority.mode",
}

# Fields whose answer is not drawn from a closed set. A vocabulary cannot be
# printed for these, so the comment says what shape the answer takes and which
# other answers make the field required — the co-requirement rules the header
# states in general, restated where the reviewer's cursor actually is.
#
# Keyed by the tail of the template path, matched longest-suffix-first, so one
# entry covers ``google_adk.tool_inventories[].path`` and its three sibling
# framework blocks.
_FIELD_HINTS: dict[str, str] = {
    "authority.auth_type": (
        "how the credential behind this authenticates, in your own words "
        "(api_key, oauth2, service_account, workload_identity, …). "
        "Required for every mode except `none`; delete this line for `none`."
    ),
    "authority.reason": (
        "why this authority is the right one. Required for `unscoped` and "
        "`ambient`; optional otherwise."
    ),
    "authority.scopes": (
        "the exact permission strings this source's credential is granted, "
        "one per line. Required and non-empty for `mode: scoped`; must be "
        "empty (delete this block) for every other mode."
    ),
    "scopes": (
        "the exact permission strings this action is granted, one per line. "
        "Required and non-empty for `mode: scoped`; must be empty (delete "
        "this block) for every other mode."
    ),
    "override.evidence": (
        "what you checked to conclude the inferred effect does not apply — the "
        "function body, the deployment, the upstream contract. Named so the "
        "next reviewer can re-check it, not so this row goes quiet."
    ),
    "override.reason": (
        "why that evidence does not establish the stronger effect for this "
        "action. Accounting for the observation instead — raising `effect:`, "
        "or keeping it and declaring the category under `risk_tags:` above — "
        "needs no override at all. The row's own instruction says which of the "
        "two applies here."
    ),
    "risk_tags": (
        "filled in from what this scan observed above the declared effect. "
        "Keeping these declares the categories as reviewed, which both accounts "
        "for the observation and makes each category's built-in controls apply "
        "to this action — the obligation the row is missing. Delete any you "
        "reject, and record that judgement in `override:` instead."
    ),
    "root.object": (
        "the agent's declared name — what `Agent(name=…)` was given, not the "
        "Python variable it was assigned to."
    ),
    "root.source_id": "the shipgate.yaml#tool_sources[].id that defines it.",
    "declarations.complete": (
        "`true`, and only once you have checked BOTH lists below — the tools "
        "and the handoffs. This one word closes the world over each of them: "
        "add anything reachable that is missing (a dynamically wired tool, a "
        "sub-agent nothing static names) and delete anything listed that this "
        "agent cannot reach. Both lists were read off what was observed; the "
        "claim that they are complete is yours."
    ),
    "declarations.reason": (
        "how you checked both lists — the files and constructs you read for "
        "the tools AND for the handoffs, so the next reviewer can re-check "
        "them."
    ),
    "declarations.handoffs": (
        "every agent this one can hand off to, by name. Covered by "
        "`complete:` above, so an agent missing here is asserted unreachable."
    ),
    "tool_inventories.path": (
        "repo-relative path where you saved the reviewed inventory (for "
        "example `inventories/tools.json`). Start from the skeleton written "
        "next to report.json."
    ),
}

# Enough candidates to choose from without turning the block into a listing.
_MAX_RENDERED_CANDIDATES = 10


def scaffold_for_report(report: ReadinessReport) -> str | None:
    """Render the questionnaire for a report, or ``None`` when nothing is owed."""

    decision = report.release_decision
    if decision is None or decision.evidence_coverage is None:
        return None
    return build_declaration_scaffold(
        decision.evidence_coverage.evidence_gaps,
        agents=report.binding_surface_facts.agents,
        questions=decision.evidence_coverage.semantic_coverage.declaration_questions,
    )


def build_declaration_scaffold(
    gaps: Sequence[EvidenceGap],
    *,
    agents: Sequence[AgentBindingNode] = (),
    questions: DeclarationQuestionCoverage | None = None,
) -> str | None:
    """Render the paste-ready questionnaire, or ``None`` when nothing is owed.

    Deterministic. Templates aimed at the same manifest target are merged once,
    and the merged blocks are ordered by the question order the decision engine
    published — highest-risk action first — falling back to gap emission order
    when no question coverage is supplied.

    ``agents`` is the binding graph's observed agent nodes. They are rendered
    as commented candidates under an ``agent_bindings.root`` block — the value
    the scan computed, offered for confirmation rather than asserted (#388).

    ``questions`` is ``semantic_coverage.declaration_questions``. It supplies
    the numbering, the progress counter, and the order; the blocks themselves
    are built from the gaps either way, so a report that carries no coverage
    (one written before v0.37) still renders every block it owes.
    """

    sections = _sections(gaps)
    ordering = _question_numbers(questions)
    for entry in sections:
        numbered_keys = sorted(
            (numbered[0], numbered[1], key)
            for key in entry["question_keys"]
            if (numbered := ordering.get(key)) is not None
        )
        entry["numbers"] = tuple(number for number, _, _ in numbered_keys)
        # In question order, not template order: the banner is read alongside
        # the numbers, so "Questions 2-3 · authority, effect" would name the
        # dimensions in the opposite order from the numbers beside them.
        # Deduplicated: a merged block can answer two questions of one
        # dimension, and "effect, effect" reads as a rendering fault.
        entry["dimensions"] = tuple(
            dict.fromkeys(dimension for _, _, (_, _, dimension) in numbered_keys)
        )
        # The banner names the *question's* subject when the block answers
        # questions about exactly one. A source-wide authority block is built
        # from one of its source's actions, so labelling it with that action
        # would name one of twelve and read as a per-action row.
        subjects = {subject for _, subject, _ in numbered_keys}
        if len(subjects) == 1:
            entry["subject"] = subjects.pop()
    # Numbered blocks lead, in question order. A block nothing asked about —
    # a tool inventory, an ``agent_bindings`` root — is a declaration too, but
    # not a per-action question, so it keeps its emission order below them
    # rather than being given a number the counter does not know about.
    numbered = sorted(
        (entry for entry in sections if entry["numbers"]),
        key=lambda entry: entry["numbers"],
    )
    unnumbered = [entry for entry in sections if not entry["numbers"]]
    claimed = {number for entry in numbered for number in entry["numbers"]}
    unfillable = _unfillable_questions(gaps, ordering, claimed)
    if not sections and not unfillable:
        return None

    total_open = len(ordering) if ordering else len(claimed)
    lines = _header(questions)
    # Blocks and comment-only entries interleaved by question number. Emitting
    # every block and *then* every unanswerable note printed a file numbered
    # 2, 3–4, 5–6, 1 — which is worse than not numbering it at all.
    for numbers, entry, subject, gap in sorted(
        [(entry["numbers"], entry, "", None) for entry in numbered]
        + [((number,), None, subject, gap) for number, subject, gap in unfillable],
        key=lambda item: item[0],
    ):
        if entry is not None:
            _emit_block(entry, agents=agents, total_open=total_open, out=lines)
        else:
            _emit_unfillable(
                numbers[0],
                subject,
                gap,
                total_open=total_open,
                out=lines,
            )
    if unnumbered:
        if numbered or unfillable:
            lines.extend(
                [
                    "",
                    "# " + "─" * 68,
                    "# Also required, and not a per-action question: these are",
                    "# declarations about a source or an agent rather than about one",
                    "# action, so they are not counted above.",
                ]
            )
        for entry in unnumbered:
            _emit_block(entry, agents=agents, total_open=total_open, out=lines)
    return "\n".join(lines) + "\n"


def _sections(gaps: Sequence[EvidenceGap]) -> list[dict[str, Any]]:
    """Merge the gaps' templates into one block per manifest target.

    Two gaps on one tool (an undeclared effect and an undeclared authority)
    want ONE ``action_surface.actions`` row, so emitting them as two blocks
    would hand the human something invalid to paste.
    """

    sections: list[dict[str, Any]] = []
    by_target: dict[tuple[str, str, str], dict[str, Any]] = {}
    for gap in gaps:
        action = gap.next_action
        template = getattr(action, "declaration_template", None)
        if not isinstance(template, dict) or not template:
            continue
        path = str(getattr(action, "path", "") or "shipgate.yaml")
        # Key on the rendered selector, not the display name: two canonical
        # tools can share a name, and folding those into one row would produce
        # a declaration that resolves neither of them.
        target = (
            path,
            str(gap.subject or ""),
            str(template.get("tool_id") or template.get("tool") or ""),
        )
        vocabulary = _vocabulary_for(action)
        dimension = DIMENSION_BY_GAP_KIND.get(str(gap.kind))
        # The questions this block answers, keyed exactly as the decision
        # engine numbers them. Carried as a set rather than as a subject plus a
        # dimension list, because merging two blocks can bring questions about
        # two different subjects together and a single subject on the entry
        # would silently drop one.
        question_key = _gap_question_key(gap, dimension) if dimension else None
        existing = by_target.get(target)
        if existing is None:
            entry: dict[str, Any] = {
                "path": path,
                "kinds": [str(gap.kind)],
                "template": dict(template),
                "vocabulary": dict(vocabulary),
                "subject": str(gap.subject or ""),
                "question_keys": [question_key] if question_key else [],
                "dimensions": (),
                "readings": list(getattr(action, "observed_readings", ()) or ()),
                "numbers": (),
            }
            by_target[target] = entry
            sections.append(entry)
            continue
        _absorb(existing, gap, template, vocabulary, question_key, action)
    return _drop_duplicate_blocks(sections)


def _absorb(
    entry: dict[str, Any],
    gap: EvidenceGap,
    template: dict[str, Any],
    vocabulary: dict[str, list[str]],
    question_key: tuple[str | None, str] | None,
    action: Any,
) -> None:
    """Fold one more gap into the block already claiming this manifest target."""

    if str(gap.kind) not in entry["kinds"]:
        entry["kinds"].append(str(gap.kind))
    if question_key and question_key not in entry["question_keys"]:
        entry["question_keys"].append(question_key)
    if not entry["readings"]:
        entry["readings"] = list(getattr(action, "observed_readings", ()) or ())
    for key, value in template.items():
        entry["template"].setdefault(key, value)
    for field, values in vocabulary.items():
        entry["vocabulary"].setdefault(field, values)


#: How a block or a gap row is matched to the question it answers.
#:
#: The id and its namespace, never the display label: two catalog tools can
#: render one subject, and a ``tool_sources`` id lives in a different namespace
#: from a tool id entirely.
QuestionKey = tuple[str, str | None, str]


def _question_key(row: DeclarationQuestionRow) -> QuestionKey:
    return (row.subject_kind, row.subject_id, row.dimension)


def _gap_question_key(gap: EvidenceGap, dimension: str) -> QuestionKey:
    return (gap.subject_kind, gap.subject_id, dimension)


def _question_numbers(
    questions: DeclarationQuestionCoverage | None,
) -> dict[QuestionKey, tuple[int, str]]:
    """``key -> (1-based question number, subject)``, in answer order.

    The subject travels with the number because the banner is announcing a
    *question*, and one question can be raised on many actions: a source-wide
    authority question is asked once and answered once, so the block that
    answers it has to say whose question it is rather than name whichever of
    the source's actions happened to build the block.
    """

    if questions is None:
        return {}
    return {
        _question_key(row): (index, row.subject)
        for index, row in enumerate(questions.open_questions, start=1)
    }


def _unfillable_questions(
    gaps: Sequence[EvidenceGap],
    ordering: dict[QuestionKey, tuple[int, str]],
    claimed: set[int],
) -> list[tuple[int, str, EvidenceGap]]:
    """Open questions no block answers, with the gap that explains them.

    A conflict between two sources is a real open question — it is counted, and
    it is what the reviewer has to resolve next — but its repair is to correct
    the source, not to fill in a blank, so no template is offered for it. Left
    out, the numbering would skip and the file would silently disagree with its
    own header about how many questions there are.
    """

    entries: list[tuple[int, str, EvidenceGap]] = []
    seen: set[int] = set()
    for gap in gaps:
        dimension = DIMENSION_BY_GAP_KIND.get(str(gap.kind))
        if dimension is None:
            continue
        numbered = ordering.get(_gap_question_key(gap, dimension))
        if numbered is None:
            continue
        number, subject = numbered
        if number in claimed or number in seen:
            continue
        seen.add(number)
        entries.append((number, subject, gap))
    return sorted(entries, key=lambda entry: entry[0])


def _header(questions: DeclarationQuestionCoverage | None) -> list[str]:
    lines = [
        "# Declaration questionnaire generated by agents-shipgate.",
        "#",
    ]
    if questions is not None and questions.total:
        lines.extend(_wrapped_comment(progress_sentence(questions), ""))
        lines.append("#")
        lines.extend(
            _wrapped_comment(
                "Ordered by how much answering can move the verdict. First, "
                "the actions nothing has pinned down: no effect evidence at "
                "all, or only a reading this scan is not allowed to act on. An "
                "action nothing has bounded is unmeasured, not safe, and its "
                "answer can still turn out to be anything. Then the ones the "
                "scan did establish, strongest first: money, outward "
                "communication, destruction.",
                "",
            )
        )
        lines.append("#")
    lines.extend(
        [
            "# Each block below is one blank this repository still owes before it",
            f"# can reach a `passed` verdict. Replace every {REVIEW_REQUIRED_SENTINEL}",
            "# with a reviewed value, merge the block into shipgate.yaml at the path",
            "# named above it, then re-run verification. One block answers both of",
            "# an action's questions where it has two — they are one manifest row —",
            "# and a `tool_sources[].authority` block answers for every action that",
            "# source contributes, since they run with one credential. An",
            "# `action_surface.actions` row still overrides it for one action.",
            "#",
            "# Where a value is already filled in, the scan observed the evidence",
            "# printed above it and proposes the most conservative reading of that",
            "# evidence — it is never weaker than anything observed. Keep it to",
            "# confirm it, or replace it with a value you can defend. Nothing here",
            "# is operative until you merge it into shipgate.yaml yourself.",
            "#",
            "# Everything else is a human declaration on purpose. Agents Shipgate",
            "# will not guess a tool's authority or which object is the root agent,",
            f"# and a block still containing {REVIEW_REQUIRED_SENTINEL} closes nothing.",
            "#",
            "# Every blank carries the values it accepts, or the shape its answer",
            "# takes, on the comment line above it. Where a field is only required",
            "# for some answers the comment says so — delete the lines your answer",
            "# does not take.",
        ]
    )
    return lines


#: Column the question banner rules out to, so the questionnaire scans as a
#: list of sections rather than a wall of comments. Matches the width
#: ``_wrapped_comment`` wraps prose at.
_BANNER_WIDTH = 78

#: Subject text a banner keeps even when the label leaves it no room. A tool
#: name is repository-controlled and unbounded; without a cap one action ruled
#: a line off the screen and the rest scanned as a ragged column.
_MIN_BANNER_SUBJECT = 24


def _numbers_phrase(numbers: Sequence[int]) -> str:
    """``Question 3`` / ``Questions 1-2`` / ``Questions 1 and 3``.

    A range is only written when the numbers really are contiguous. Ordering
    now keeps one action's questions adjacent, but a banner that renders any
    set as a span is a renderer that can lie the moment ordering changes — and
    what it would claim is another action's question.
    """

    if len(numbers) == 1:
        return f"Question {numbers[0]}"
    if numbers[-1] - numbers[0] == len(numbers) - 1:
        return f"Questions {numbers[0]}–{numbers[-1]}"
    listed = [str(number) for number in numbers]
    return f"Questions {', '.join(listed[:-1])} and {listed[-1]}"


def _question_banner(
    numbers: Sequence[int],
    total: int,
    subject: str,
    dimensions: Sequence[str] = (),
) -> str:
    """``Question 3 of 5 · effect · send_email [mcp]`` — a range where merged.

    The dimensions are named because one block can answer two questions, and
    the counter beside them counts questions rather than blocks: without them,
    "Questions 2–3" would look like a numbering error.

    The subject is elided to fit. That is safe here and nowhere near a machine
    route: this is a heading, and the block directly beneath it carries the
    exact ``tool`` and ``tool_id`` a reader has to act on.
    """

    parts = [f"{_numbers_phrase(numbers)} of {total}"]
    if dimensions:
        parts.append(", ".join(dimensions))
    prefix = f"── {' · '.join(parts)} · "
    # Two characters held back for the space and at least one rule character,
    # so the common line never ends in trailing whitespace.
    room = max(_BANNER_WIDTH - 4 - len(prefix), _MIN_BANNER_SUBJECT)
    rendered = display_literal(subject)
    if len(rendered) > room:
        rendered = f"{rendered[: room - 1].rstrip()}…"
    text = f"{prefix}{rendered}"
    if len(text) + 2 > _BANNER_WIDTH - 2:
        # The label alone fills the line. Rule it off with nothing rather than
        # with a space that every diff tool flags.
        return text
    return f"{text} ".ljust(_BANNER_WIDTH - 2, "─")


def _emit_block(
    entry: dict[str, Any],
    *,
    agents: Sequence[AgentBindingNode],
    total_open: int,
    out: list[str],
) -> None:
    # Each block is its own YAML document. Concatenated mappings would
    # repeat top-level keys (two `tool:` roots), which is not a file a
    # reader or a parser can make sense of.
    out.append("")
    if entry["numbers"]:
        out.append(
            "# "
            + _question_banner(
                entry["numbers"],
                total_open,
                entry["subject"],
                entry["dimensions"],
            )
        )
    out.extend(
        _reading_lines(
            entry["readings"],
            entry["template"],
            asks_effect=any(
                DIMENSION_BY_GAP_KIND.get(kind) == "effect" for kind in entry["kinds"]
            ),
            ordered=bool(entry["numbers"]),
        )
    )
    out.append("---")
    out.append(f"# closes: {', '.join(entry['kinds'])}")
    out.append(f"# merge into: {entry['path']}")
    _emit_mapping(
        entry["template"],
        path="",
        depth=0,
        vocabulary=entry["vocabulary"],
        agents=agents,
        proposed=_proposed_fields(entry["template"]),
        out=out,
    )


def _emit_unfillable(
    number: int,
    subject: str,
    gap: EvidenceGap,
    *,
    total_open: int,
    out: list[str],
) -> None:
    """A counted question with no blank to fill — say what closes it instead."""

    out.append("")
    dimension = DIMENSION_BY_GAP_KIND.get(str(gap.kind))
    out.append(
        "# "
        + _question_banner(
            (number,),
            total_open,
            subject or gap.subject,
            (dimension,) if dimension else (),
        )
    )
    # The readings too. This row has no blank to fill precisely *because* its
    # sources disagree, so what they each say is the thing the reviewer has to
    # go and reconcile.
    # Never the "nothing was read" note: this row has no blank to fill, and
    # its silence is a conflict between sources rather than an absence.
    out.extend(
        _reading_lines(
            getattr(gap.next_action, "observed_readings", ()) or (),
            {},
            asks_effect=False,
        )
    )
    out.extend(_wrapped_comment(one_line(gap.why), ""))
    out.append("#")
    out.extend(
        _wrapped_comment(
            "No block is offered for this one: "
            + one_line(evidence_gap_action_text(gap, include_command=False)),
            "",
        )
    )


def _proposed_fields(template: dict[str, Any]) -> frozenset[str]:
    """Template fields carrying a proposal rather than a blank.

    Enumerated, not inferred from "is not the sentinel": a selector field is
    filled in too, and calling the tool's own name a proposal would invite a
    reviewer to change it.
    """

    proposed: set[str] = set()
    if template.get("effect") not in (None, REVIEW_REQUIRED_SENTINEL):
        proposed.add("effect")
    if isinstance(template.get("risk_tags"), list) and template["risk_tags"]:
        proposed.add("risk_tags")
    return frozenset(proposed)


def _reading_lines(
    readings: Sequence[EvidenceReading],
    template: dict[str, Any],
    *,
    asks_effect: bool = False,
    ordered: bool = False,
) -> list[str]:
    """What the scan read this action's effect as, above the value it proposes.

    Observations and defaults are stated separately. A protocol default is what
    the protocol assumes when a server publishes nothing about a tool, so
    presenting it beside a keyword match as though both were evidence about
    this action would misrepresent the weaker one — and it is exactly the
    reading nothing is ever proposed from.

    An effect question with *no* readings at all says so (#419). The header
    explains why the top of the file is the unbounded half; a block that
    printed nothing left a reader to read the silence as "nothing to see
    here", which is the reading this whole ordering exists to correct.

    ``ordered`` gates the half of that note which claims a *position*. A report
    written before ``declaration_questions`` existed carries no coverage to
    number the blocks from, so they keep gap emission order and a blank can
    follow a bounded question — where "it is asked before" would be a sentence
    the file itself disproves two lines up.
    """

    if not readings:
        if not asks_effect:
            return []
        note = (
            "This scan read nothing about this action's effect — an absence of "
            "evidence, not evidence that it is safe. Your answer is the only "
            "thing that bounds it."
        )
        if ordered:
            note += " It is asked before the ones the scan could read for itself."
        return _wrapped_comment(note, "")
    observed = [reading for reading in readings if reading.observed]
    defaults = [reading for reading in readings if not reading.observed]
    lines: list[str] = []
    if observed:
        lines.append("# What this scan read this action's effect as:")
        lines.extend(_reading_rows(observed))
    if defaults:
        lines.append(
            "# Assumed in the absence of evidence, and never proposed from:"
        )
        lines.extend(_reading_rows(defaults))
    proposal = template.get("effect")
    if "effect" in _proposed_fields(template):
        tags = template.get("risk_tags")
        detail = (
            f" with risk_tags: [{', '.join(str(tag) for tag in tags)}]"
            if isinstance(tags, list) and tags
            else ""
        )
        lines.extend(
            _wrapped_comment(
                f"Proposed below: {proposal}{detail} — at or above every reading "
                "here, so confirming it can only over-declare. Replace it if you "
                "can defend a different reading.",
                "",
            )
        )
    return lines


def _reading_rows(readings: Sequence[EvidenceReading]) -> list[str]:
    """One comment row per reading: the effect, then who says so.

    ``display_literal`` on the sources because a claim source can embed a
    repository-controlled name (``risk_hint:<hint source>``), and this is a
    YAML comment directly above a value a reader pastes — a forged line break
    here would render a filled-in field nobody wrote (#268).
    """

    return [
        f"#   {display_literal(reading.effect)} — "
        f"{', '.join(display_literal(source) for source in reading.sources)}"
        for reading in readings
    ]


def _vocabulary_for(action: Any) -> dict[str, list[str]]:
    """The ``field -> accepted values`` this action publishes, if any."""

    field = _VOCABULARY_FIELD_BY_ACTION_KIND.get(str(getattr(action, "kind", "")))
    values = list(getattr(action, "accepted_values", ()) or ())
    return {field: values} if field and values else {}


def _drop_duplicate_blocks(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse blocks that would render identically at the same path.

    One mechanism restated per subject produces one gap row per subject — six
    unresolved ADK tool symbols are six ``source_warning`` rows — and each
    carries the same repair. Keyed on the subject alone those are six identical
    ``tool_inventories`` blocks to paste, which reads as six separate things to
    do. Byte-identical content at one path is one instruction.
    """

    kept: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in sections:
        key = (entry["path"], json.dumps(entry["template"], sort_keys=True, default=str))
        first = seen.get(key)
        if first is None:
            seen[key] = entry
            kept.append(entry)
            continue
        for kind in entry["kinds"]:
            if kind not in first["kinds"]:
                first["kinds"].append(kind)
        for field, values in entry["vocabulary"].items():
            first["vocabulary"].setdefault(field, values)
        # The questions too. A dropped block still answered whatever it was
        # asked, and losing its keys here would leave that question numbered by
        # the counter and answered by no block in the file.
        for question_key in entry["question_keys"]:
            if question_key not in first["question_keys"]:
                first["question_keys"].append(question_key)
        if not first["readings"]:
            first["readings"] = entry["readings"]
    return kept


def _hint_for(path: str) -> str | None:
    """The guidance registered for this template path, longest suffix first."""

    parts = path.split(".")
    for start in range(len(parts)):
        hint = _FIELD_HINTS.get(".".join(parts[start:]))
        if hint is not None:
            return hint
    return None


def _annotate(
    path: str,
    depth: int,
    vocabulary: dict[str, list[str]],
    agents: Sequence[AgentBindingNode],
    proposed: frozenset[str],
    out: list[str],
) -> None:
    """Write the comment lines that belong above the value at ``path``."""

    pad = "  " * depth
    for line in _candidate_lines(path, agents):
        out.append(f"{pad}# {line}")
    if path in proposed:
        # Said at the cursor as well as in the header. A reader who scrolls
        # straight to a filled-in field would otherwise have no way to tell it
        # apart from a value they wrote on an earlier pass.
        out.extend(
            _wrapped_comment(
                "proposed from the evidence above — keep it to confirm, or "
                "replace it.",
                pad,
            )
        )
    hint = _hint_for(path)
    if hint is not None:
        out.extend(_wrapped_comment(hint, pad))
    values = vocabulary.get(path)
    if values:
        out.extend(_wrapped_comment("accepted: " + " | ".join(values), pad))


def _candidate_lines(
    path: str, agents: Sequence[AgentBindingNode]
) -> list[str]:
    """Observed root-agent candidates, for a human to confirm.

    Instance 1 of #388: the scan resolves the agent objects and then hands the
    reader two blanks. Naming what it saw removes the guessing game without
    filling anything in — inferring ``agent_bindings.root`` from AST evidence
    is the self-declaration surface #268 closed, and this does not do that.
    """

    if not path.endswith("agent_bindings.root") or not agents:
        return []
    lines = [
        "shipgate observed these agent objects — confirm one and fill it in:"
    ]
    # By name rather than by the graph's agent_id order: the reader is choosing
    # between names, and an id-hash ordering looks arbitrary to them.
    ordered = sorted(agents, key=lambda agent: (agent.name, agent.source_id or ""))
    for agent in ordered[:_MAX_RENDERED_CANDIDATES]:
        # ``display_literal`` because these are repository-controlled identity
        # strings, and the caller writes them into a YAML `#` comment. An
        # `Agent(name="...\nroot:\n  object: X")` would otherwise close the
        # comment and render a *filled-in* root block for a reader to paste —
        # a self-declaration the scaffold exists to refuse (#268). Injective,
        # so the escape still names exactly one object.
        name = display_literal(agent.name)
        source = (
            f", source_id: {display_literal(agent.source_id)}"
            if agent.source_id
            else ""
        )
        where = (
            f"  ({display_literal(agent.source_ref)})" if agent.source_ref else ""
        )
        lines.append(f"    object: {name}{source}{where}")
    remaining = len(agents) - _MAX_RENDERED_CANDIDATES
    if remaining > 0:
        lines.append(
            f"    (+{remaining} more — see report.json "
            "binding_surface_facts.agents)"
        )
    return lines


def _wrapped_comment(text: str, pad: str) -> list[str]:
    """``text`` as comment lines that stay inside a readable column."""

    limit = 78
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(pad) + 2 + len(candidate) > limit:
            lines.append(f"{pad}# {current}")
            current = word
            continue
        current = candidate
    if current:
        lines.append(f"{pad}# {current}")
    return lines


def _scalar(value: Any) -> str:
    """``value`` as the one-line YAML scalar ``yaml.safe_dump`` would emit.

    One line is the load-bearing part. ``safe_dump`` renders a string holding a
    newline as a *multi-line* single-quoted scalar whose continuation lines it
    indents relative to the key it is dumping — knowledge this emitter does not
    have when it formats a leaf on its own, so pasting the result at depth 2
    produced continuation lines indented less than their key. Tool names come
    from repository JSON, so that is reachable input, not a hypothetical.

    ``yaml_scalar`` is the escape hatch for exactly those: a JSON string is a
    valid YAML double-quoted scalar, total over anything a loader can produce,
    and always one line. Ordinary values never reach it, so the common
    rendering stays byte-identical to ``safe_dump``.
    """

    text = yaml.safe_dump(
        value, default_flow_style=True, sort_keys=False, width=1 << 30
    ).strip()
    if text.endswith("..."):
        text = text[:-3].strip()
    # Only a string can carry the newline that forces the multi-line form.
    return yaml_scalar(value) if isinstance(value, str) and "\n" in text else text


def _emit_mapping(
    node: dict[str, Any],
    *,
    path: str,
    depth: int,
    vocabulary: dict[str, list[str]],
    agents: Sequence[AgentBindingNode],
    proposed: frozenset[str] = frozenset(),
    out: list[str],
    first_prefix: str | None = None,
) -> None:
    """Render a mapping in the block layout ``yaml.safe_dump`` produces.

    Hand-rolled because comments have to be interleaved and PyYAML has no way
    to carry them. The layout is not a second opinion about YAML style: a test
    asserts that stripping the comment lines back out yields exactly what
    ``yaml.safe_dump`` writes for the same template, so the two cannot drift.

    ``first_prefix`` is the ``- `` of an enclosing sequence item, which YAML
    puts on the same line as the item's first key.
    """

    pad = "  " * depth
    for index, (key, value) in enumerate(node.items()):
        child = f"{path}.{key}" if path else str(key)
        on_dash = index == 0 and first_prefix is not None
        lead = first_prefix if on_dash else pad
        # The dash line's own annotation belongs above the dash, at the
        # sequence's indentation rather than the item's.
        _annotate(
            child,
            depth - 1 if on_dash else depth,
            vocabulary,
            agents,
            proposed,
            out,
        )
        if isinstance(value, dict) and value:
            out.append(f"{lead}{key}:")
            _emit_mapping(
                value,
                path=child,
                depth=depth + 1,
                vocabulary=vocabulary,
                agents=agents,
                proposed=proposed,
                out=out,
            )
        elif isinstance(value, (list, tuple)):
            if not value:
                out.append(f"{lead}{key}: []")
                continue
            out.append(f"{lead}{key}:")
            _emit_sequence(
                value,
                path=child,
                depth=depth,
                vocabulary=vocabulary,
                agents=agents,
                proposed=proposed,
                out=out,
            )
        else:
            # An empty mapping renders through the same scalar path (`{}`).
            out.append(f"{lead}{key}: {_scalar(value)}")


def _emit_sequence(
    items: Iterable[Any],
    *,
    path: str,
    depth: int,
    vocabulary: dict[str, list[str]],
    agents: Sequence[AgentBindingNode],
    proposed: frozenset[str] = frozenset(),
    out: list[str],
) -> None:
    """Render a sequence: dashes at the key's indent, content one deeper.

    Scalar items carry no annotation of their own — the guidance for a list
    (``scopes``) is about the list, and was already written above its key.
    """

    pad = "  " * depth
    for item in items:
        if isinstance(item, dict) and item:
            _emit_mapping(
                item,
                path=path,
                depth=depth + 1,
                vocabulary=vocabulary,
                agents=agents,
                proposed=proposed,
                out=out,
                first_prefix=f"{pad}- ",
            )
            continue
        out.append(f"{pad}- {_scalar(item)}")


__all__ = ["build_declaration_scaffold", "scaffold_for_report"]
