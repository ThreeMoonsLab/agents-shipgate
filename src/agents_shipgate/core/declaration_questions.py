"""The declaration questions an adoption still owes, as one projection.

``insufficient_evidence`` on an unconfigured repository presents itself as a
count of gaps — "36 semantic evidence gaps" — which names a symptom, gives no
finish line, and says nothing about which of the 36 could move the verdict.
The same facts, asked as a numbered questionnaire with a progress counter, are
a task a person can finish (#410 increment 2).

A question is **one blank a reviewer fills**, identified by the manifest block
that fills it (:class:`DeclarationTarget`). Two rows closed by the same edit
are one question, however many actions raised them: a source of 117 actions
with no authority evidence owes one ``tool_sources[].authority`` block, and
counting that as 117 questions describes one edit as a backlog (#410
increment 3). The unit of the counter has to be the unit of the work.

Three rules decide what counts as a question, and each of them is the RFC's
first principle applied literally — *never ask a human what the scanner can
prove*:

* A dimension is a question only when the scan **cannot close it alone**. An
  action whose effect is established structurally — an OpenAPI ``GET``, an MCP
  ``readOnlyHint`` on a source the manifest trusts — was never asked about, and
  never appears in the denominator.
* A question is **answered** when a reviewed declaration is what closed it. The
  test is counterfactual and exact: re-resolve the same action with its
  declaration removed, and if the dimension gaps without it, the declaration is
  the answer. Anything else would either count declarations nobody needed or
  flatter the number with dimensions the scanner proved by itself.
* A question is **open** when the dimension still carries an issue only a
  declaration can close. The kinds are enumerated below rather than inferred
  from the dimension, because a dimension carries issues that a declaration
  *cannot* answer: ``incomplete_surface`` rides on the effect dimension and is
  repaired by a tool inventory, and ``invalid_semantic_annotation`` is a defect
  in the source, not a blank in the manifest.

Ordering is by how much answering can move the verdict. The fourth adoption
walk of ``adk-samples#1745`` reached ``blocked`` after declaring 2 of 12 tools
— the two that moved money and communicated outward — so a questionnaire that
leads with them reaches the same verdict in two answers instead of twelve.

The quantity that ranks a question is the **ceiling** of what its answer can
establish, not the floor the scan already inferred (#419). Those are not the
same number, and ranking by the second inverted the promise: a proposal is
offered only where something was observed, so ranking by the observation put
every question that arrives with a proposed answer above every question that
arrives blank — the cheapest questions first and the most valuable ones last.
An action nothing was observed about is not a low-risk action; it is an
unmeasured one, its answer can still turn out to be anything in the
vocabulary, and it is exactly where a human answer carries new information.
So an unmeasured action outranks every measured one, and among the measured
the strongest reading leads.

Ordering is *ranking only*: it decides what to read first, never what the
verdict is.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from agents_shipgate.core.domain import (
    DECLARED_EFFECT_SOURCE,
    REVIEWED_DECLARATION_CLAIM_SOURCES,
    SemanticIssue,
    Tool,
    ToolSemanticAssessment,
)
from agents_shipgate.core.risk_hints import name_shape_band
from agents_shipgate.core.semantic_assessment import (
    UNMEASURED_EFFECT_RANK,
    assess_tool_semantics,
    effect_evidence_rank,
    effect_is_measured,
    effect_readings,
)
from agents_shipgate.schemas.report import DeclarationQuestionCoverage

#: The two dimensions a reviewed declaration answers.
#:
#: Deliberately not every dimension that can gap. A tool inventory and an
#: ``agent_bindings`` root are human declarations too, but neither is per
#: action and neither has a counterfactual this module can evaluate — removing
#: an inventory does not re-run extraction. Counting what cannot be counted on
#: both halves is how a progress bar starts lying, so the denominator is the
#: two dimensions whose answers live on an ``action_surface.actions`` row —
#: or, for an authority every action of a source shares, on that source's
#: ``tool_sources[].authority`` block. Both are the same claim, and both are
#: measurable on both halves: the counterfactual drops every reviewed
#: declaration, whichever site it was written at.
DeclarationDimension = Literal["effect", "authority"]

DECLARATION_DIMENSIONS: tuple[DeclarationDimension, ...] = ("effect", "authority")

#: Issue kinds a reviewed ``action_surface.actions`` row can close, per
#: dimension. Enumerated, never derived from ``issue.dimension``: see the
#: module docstring for the two kinds that ride on a dimension they cannot be
#: answered on.
#:
#: ``partial_authority_evidence`` is deliberately **absent**. The resolver
#: preserves it whenever the source's own authority evidence is ambiguous or
#: incomplete, *whatever the manifest declares* — "reviewed authority cannot
#: replace ambiguous or incomplete source authority alternatives" is a
#: deliberate safety property, not an oversight. Counting it would advertise a
#: finish line no answer reaches: an MCP tool published with scopes but no auth
#: type asks one authority question, and writing the exact scoped declaration
#: the scaffold requests leaves the counter at 0 of 1 answered forever.
#:
#: ``test_every_answerable_kind_has_an_answer_that_closes_it`` is the guard:
#: each kind here must have a generated declaration that makes its question
#: answered on re-resolution, in every configuration that raises it.
ANSWERABLE_ISSUE_KINDS: dict[DeclarationDimension, frozenset[str]] = {
    "effect": frozenset(
        {
            "missing_effect_evidence",
            "inferred_effect_only",
            "conflicting_effect_evidence",
            "declaration_below_inferred_evidence",
        }
    ),
    "authority": frozenset(
        {
            "missing_authority_evidence",
            "conflicting_authority_evidence",
        }
    ),
}

#: Kinds raised about *either* surface, where the resolver's own attribution
#: decides whether a declaration can close it.
#:
#: ``conflicting_effect_evidence`` has two branches. One says the declaration
#: is weaker than policy-eligible source evidence and blames
#: ``action_surface_declaration`` — raising the declared effect closes it. The
#: other says the *source* asserts read-only and a side effect at once and
#: blames ``tool_source``; no declaration touches that, because the resolver
#: reads the source's self-contradiction before it reads the manifest. Treating
#: the kind as uniformly answerable is the same defect as counting
#: ``partial_authority_evidence``, one branch deeper.
DECLARATION_ATTRIBUTED_KINDS: frozenset[str] = frozenset({"conflicting_effect_evidence"})

#: The issue source the resolver stamps when the manifest row is what is at
#: fault. One spelling, shared with the resolver's claim sources.
_DECLARATION_ISSUE_SOURCE = DECLARED_EFFECT_SOURCE


def is_declaration_answerable(kind: str, source: str | None) -> bool:
    """Can a reviewed ``action_surface.actions`` row close this exact issue?

    The one predicate. Two surfaces ask it and they must not diverge: the
    questionnaire decides whether to *count* the issue as a question, and the
    evidence-gap builder decides what repair to *publish* for it. Counting a
    row the published repair cannot close, or publishing a declaration for a
    row the counter knows is unanswerable, are the same defect seen from two
    ends.

    ``source`` is the resolver's own attribution of what is at fault, so this
    never re-derives the judgement — it reads the one the resolver already
    recorded. ``None`` is treated as source-owned: it is the conservative
    reading, and it never promises a declaration that may not work.
    """

    if kind not in DECLARATION_ATTRIBUTED_KINDS:
        return True
    return source == _DECLARATION_ISSUE_SOURCE

#: The inverse of :data:`ANSWERABLE_ISSUE_KINDS` — which dimension each gap
#: kind belongs to. Inverted once, here, because both the questionnaire and the
#: gap ordering need the same routing and a second spelling of it is how they
#: start disagreeing about what a question is.
DIMENSION_BY_GAP_KIND: dict[str, DeclarationDimension] = {
    kind: dimension
    for dimension, kinds in ANSWERABLE_ISSUE_KINDS.items()
    for kind in kinds
}

#: Reading order within one action. Effect first: it is the question that names
#: the risk, and the authority answer is a fact about a deployment that the
#: person reading a repository often cannot supply at all.
_DIMENSION_ORDER: dict[DeclarationDimension, int] = {"effect": 0, "authority": 1}


#: Authority issue kinds a ``tool_sources[].authority`` block can answer.
#:
#: Exactly one: the action has no authority evidence at all, so the answer is
#: the deployment fact the whole source shares — which credential its actions
#: run with — and asking it once per action asks the same infrastructure
#: question N times (#410 increment 3).
#:
#: A *conflict* is deliberately absent. It is raised about one action whose own
#: published evidence disagrees with the reviewed block, so the decision it
#: asks for is about that action: correct the block, or declare the exception
#: on its row. Folding those into one source-wide question would count one
#: answer for N independent judgements.
SOURCE_ANSWERABLE_AUTHORITY_KINDS: frozenset[str] = frozenset({"missing_authority_evidence"})


@dataclass(frozen=True)
class DeclarationTarget:
    """The manifest block one declaration question is answered in.

    A question is one blank a reviewer fills, so the block is its identity: two
    rows closed by the same edit are one question, however many actions they
    were raised on. That is the whole of the per-source authority payoff —
    117 actions with no authority evidence are one question, because one block
    answers all of them.

    ``id`` is the identity and ``subject`` is the label. Never the other way
    round: two catalog tools can render one display subject, so keying on the
    label would merge two actions' questions into one and answer neither.
    ``path`` is the machine-readable route, and it is derived here so the
    questionnaire and the evidence-gap row cannot name two different blocks.
    """

    kind: Literal["action", "tool_source"]
    id: str
    subject: str
    path: str


def action_declaration_target(tool: Tool) -> DeclarationTarget:
    """The ``action_surface.actions`` row that answers for this action."""

    return DeclarationTarget(
        kind="action",
        id=tool.id,
        subject=_subject(tool),
        path=f"shipgate.yaml#action_surface.actions[tool={tool.name!r}]",
    )


def declaration_answer_target(tool: Tool, kind: str) -> DeclarationTarget:
    """Where the answer to one gap ``kind`` on ``tool`` is written.

    The one derivation. The evidence-gap builder reads it to publish a repair
    and the questionnaire reads it to identify a question, and if those two
    ever disagreed the file would number a block the counter does not know
    about.
    """

    source_id = _source_answering(tool, kind)
    if source_id is None:
        return action_declaration_target(tool)
    return DeclarationTarget(
        kind="tool_source",
        id=source_id,
        # Labelled by what it is. A source id and a tool name are both
        # repository-chosen strings, and a reader looking at a numbered
        # question has to be able to tell which kind of thing it names.
        subject=f"{source_id} [tool_source]",
        path=f"shipgate.yaml#tool_sources[id={source_id!r}].authority",
    )


def _source_answering(tool: Tool, kind: str) -> str | None:
    """The ``tool_sources`` id that answers this kind here, or ``None``.

    ``None`` covers three different situations that all mean "the answer goes
    on the action row": the kind is not one a source block can answer, this
    action already carries its own reviewed authority, or nothing in
    ``tool_sources`` configures the surface it came from. The resolver decides
    the last two — see ``AuthoritySemanticAssessment.answerable_source_id`` —
    so no caller has to guess a source id from ``tool.source_id``.
    """

    assessment = tool.semantic_assessment
    if kind not in SOURCE_ANSWERABLE_AUTHORITY_KINDS or assessment is None:
        return None
    return assessment.authority.answerable_source_id


@dataclass(frozen=True)
class DeclarationQuestion:
    """One blank a reviewed declaration has to fill.

    Deliberately carries no readings and no proposed answer. Those belong to
    the *row* a reviewer answers — ``EvidenceGapAction.observed_readings`` and
    ``declaration_template`` — and deriving them a second time here would be
    two sources for one row's contents. This model is the identity of a
    question and its place in the queue, nothing more.

    ``subject_id`` is a tool id when ``subject_kind`` is ``action`` and a
    ``tool_sources[].id`` when it is ``tool_source``. The kind is carried
    rather than inferred, because both are repository-chosen strings and a
    consumer joining one id space against the other would silently match
    nothing — or, once, the wrong thing.
    """

    subject: str
    subject_id: str
    subject_kind: Literal["action", "tool_source"]
    answer_path: str
    dimension: DeclarationDimension
    answered: bool
    rank: int
    shape: int


class _PendingQuestion:
    """One question under construction, folding in every action that asks it."""

    def __init__(self, target: DeclarationTarget, dimension: DeclarationDimension) -> None:
        self.target = target
        self.dimension = dimension
        self.answered = True
        # A floor no real reach ties with: a measured action ranks at least
        # ``write`` and an unmeasured one ranks at the ceiling, so the first
        # ``absorb`` always replaces this.
        self.rank = 0
        self.shape = 0

    def absorb(self, *, answered: bool, rank: int, shape: int) -> None:
        # Open wins. A block that answers eleven of its twelve actions and
        # leaves the twelfth gapping is not an answered question: the reviewer
        # still has an edit to make, and a counter that said otherwise would
        # report a finish line the scan does not agree has been reached.
        self.answered = self.answered and answered
        # Ranked by the strongest action it covers, so a source carrying one
        # action nothing was read about is asked before a source the scan read
        # end to end. The band travels with the rank rather than being
        # maximised on its own: it describes the same action the rank came
        # from, and taking the two from different actions would order a
        # question by a name nothing else about it refers to.
        self.rank, self.shape = max((self.rank, self.shape), (rank, shape))

    def build(self) -> DeclarationQuestion:
        return DeclarationQuestion(
            subject=self.target.subject,
            subject_id=self.target.id,
            subject_kind=self.target.kind,
            answer_path=self.target.path,
            dimension=self.dimension,
            answered=self.answered,
            rank=self.rank,
            shape=self.shape,
        )


def declaration_questions(tools: Iterable[Tool]) -> list[DeclarationQuestion]:
    """Every declaration question this scan asks, answered ones included.

    Deterministic and total over the catalog: the actions nothing was read
    about first, then the ones that were read, strongest first — see
    :func:`_reach` — then by subject, then effect before authority. A tool
    with no semantic assessment contributes nothing: it has not been resolved,
    so nothing is known about what it owes.

    Actions asking the same question are folded together. That is not a
    display convenience: the unit of the counter is the unit of the work, and
    counting one edit as N questions is what made ``insufficient_evidence``
    read as an unbounded backlog on a repository that owed one authority block.
    """

    pending: dict[tuple[str, str, DeclarationDimension], _PendingQuestion] = {}
    for tool in tools:
        assessment = tool.semantic_assessment
        if assessment is None:
            continue
        # The counterfactual: what this action would owe with no declaration at
        # all. Computed once per tool, and only when a declaration could
        # possibly be doing the work. Resolved with no ``tool_source`` either,
        # so it means "no reviewed declaration anywhere" and a source-wide
        # answer scores exactly like a per-action one.
        undeclared = (
            assess_tool_semantics(tool, None) if _has_declaration(assessment) else assessment
        )
        rank, shape = _reach(tool, assessment)
        for dimension in DECLARATION_DIMENSIONS:
            asked = _asked(dimension, assessment, undeclared)
            if asked is None:
                continue
            answered, kinds = asked
            target = _target_for(tool, dimension, kinds)
            key = (target.kind, target.id, dimension)
            slot = pending.get(key)
            if slot is None:
                slot = _PendingQuestion(target, dimension)
                pending[key] = slot
            slot.absorb(answered=answered, rank=rank, shape=shape)
    questions = [slot.build() for slot in pending.values()]
    questions.sort(key=_ordering)
    return questions


def _reach(tool: Tool, assessment: ToolSemanticAssessment) -> tuple[int, int]:
    """``(rank, name band)`` — how far an answer about this action can reach.

    The ceiling, not the floor (#419). Where the scan measured a side effect it
    ranks the action by what it read, and the questionnaire's own proposal
    machinery is offered on exactly the same condition, so those questions
    arrive with a draft answer and cost a reader a glance. Where nothing was
    measured the scan holds no bound at all: the answer can still be
    ``destructive``, so the question sorts above every measured one.

    The band is the tiebreaker among those, and it is ``0`` — inert — for every
    measured action, so a name can never reorder an action the scan actually
    read. See :func:`name_shape_band` for why an unmeasured action may be
    ordered by something no verdict is allowed to touch.
    """

    if effect_is_measured(effect_readings(assessment.effect)):
        return effect_evidence_rank(assessment.conservative_effect), 0
    return UNMEASURED_EFFECT_RANK, name_shape_band(tool)


def _target_for(
    tool: Tool,
    dimension: DeclarationDimension,
    kinds: frozenset[str],
) -> DeclarationTarget:
    """The block that answers this dimension, given what it is being asked.

    Falls back to the action row unless *every* asking kind agrees the source
    block answers it. One kind that a source block cannot close makes the whole
    question the action's, because the reviewer's edit has to land where all of
    it can be answered.
    """

    targets = {declaration_answer_target(tool, kind) for kind in kinds}
    if len(targets) == 1:
        return targets.pop()
    return action_declaration_target(tool)


def open_questions(
    questions: Sequence[DeclarationQuestion],
) -> list[DeclarationQuestion]:
    """The questions still owed, in the order they should be answered."""

    return [question for question in questions if not question.answered]


def open_counts_by_dimension(
    questions: Sequence[DeclarationQuestion],
) -> dict[str, int]:
    """Open questions per dimension, omitting dimensions with none.

    In reading order (``effect`` before ``authority``), not alphabetical: this
    is rendered as a sentence beside a questionnaire that asks them in that
    order, and "1 authority, 1 effect" reads as a different order from the one
    the file uses.
    """

    counts: dict[str, int] = {}
    for question in open_questions(questions):
        counts[question.dimension] = counts.get(question.dimension, 0) + 1
    return {
        dimension: counts[dimension]
        for dimension in sorted(counts, key=lambda item: _DIMENSION_ORDER[item])
    }


def progress_sentence(coverage: DeclarationQuestionCoverage) -> str:
    """The progress counter as one complete, labelled line — ``""`` when unasked.

    Labelled here rather than at each call site: the CLI echoes it verbatim and
    the generated questionnaire prints it as a comment, and a prefix chosen
    twice is a prefix that ends up saying two things.

    One rendering, so the CLI line, the generated questionnaire's header, and
    anything else that reports progress cannot describe the same state two
    ways. Reads the published counts rather than recomputing them: the number
    a user sees and the number ``report.json`` carries are then the same number
    by construction.

    ``open_by_dimension`` is rendered in the order it arrives — reading order,
    which is the order the questionnaire asks them in. Re-sorting it here would
    put "1 authority" ahead of "1 effect" and describe an order the file does
    not use.
    """

    if not coverage.total:
        return ""
    noun = "question" if coverage.total == 1 else "questions"
    sentence = (
        f"Declaration {noun}: {coverage.answered} of {coverage.total} answered"
    )
    if not coverage.open:
        return f"{sentence}."
    breakdown = ", ".join(
        f"{count} {dimension}" for dimension, count in coverage.open_by_dimension.items()
    )
    return f"{sentence}; {coverage.open} open ({breakdown})."


def _asked(
    dimension: DeclarationDimension,
    assessment: ToolSemanticAssessment,
    undeclared: ToolSemanticAssessment,
) -> tuple[bool, frozenset[str]] | None:
    """``(answered, asking kinds)`` for one action's dimension, or ``None``.

    ``None`` means the dimension was never a question: the scan closed it by
    itself, and nothing was ever asked of a human. The kinds come back with the
    answer because they decide *where* the question is answered, and the two
    halves have to be read off the same assessment — the open kinds off what
    stands now, the answered kinds off the counterfactual.
    """

    answerable = ANSWERABLE_ISSUE_KINDS[dimension]
    open_kinds = _asking(_issues(assessment, dimension), answerable)
    if open_kinds:
        return False, open_kinds
    would_ask = _asking(_issues(undeclared, dimension), answerable)
    if would_ask:
        # The dimension is clean *and* it would not have been without the
        # declaration: a human answered this one.
        return True, would_ask
    return None


def _issues(
    assessment: ToolSemanticAssessment,
    dimension: DeclarationDimension,
) -> Sequence[SemanticIssue]:
    return assessment.effect.issues if dimension == "effect" else assessment.authority.issues


def _asking(issues: Sequence[SemanticIssue], answerable: frozenset[str]) -> frozenset[str]:
    """The kinds among ``issues`` that a reviewed declaration can actually close."""

    return frozenset(
        issue.kind
        for issue in issues
        if issue.kind in answerable and is_declaration_answerable(issue.kind, issue.source)
    )


def _has_declaration(assessment: ToolSemanticAssessment) -> bool:
    """True when the manifest declares anything about this action.

    Read off the claims the resolver authored rather than by re-reading the
    manifest: the declaration reaches this module only through the assessment,
    and re-deriving which tool a declaration keyed onto is the second
    implementation of a join the resolver already made.
    """

    return any(
        claim.source in REVIEWED_DECLARATION_CLAIM_SOURCES
        for claim in (*assessment.effect.claims, *assessment.authority.claims)
    )


def _subject(tool: Tool) -> str:
    """The gap-row display label, so both surfaces name an action identically."""

    return f"{tool.name} [{tool.provider or tool.source_id or tool.source_type}]"


def _ordering(question: DeclarationQuestion) -> tuple[int, int, str, str, str, int]:
    """Reach, then name band, then subject, then **subject id**, then dimension.

    Reach is the ceiling of what an answer can establish — see
    :func:`_reach` — so the questions the scan could not read at all lead,
    and the band orders those among themselves. The band is ``0`` for every
    measured action, so that second component only ever separates questions the
    first one has already tied.

    Subject id before dimension is what keeps one subject's questions
    contiguous. Two canonical tools can render the same display subject, and
    ordering by dimension first interleaved them — tool A effect, tool B
    effect, tool A authority — so the block merged for tool A owned questions 1
    and 3 and the scaffold announced it as "Questions 1-3", claiming the one in
    between that belongs to the other tool. Within a subject the dimension
    order still holds, which is the only thing it was ever there for.

    ``subject_kind`` joins the key because a source id and a tool id are
    independent namespaces: without it, ordering would depend on which one
    happened to be compared.
    """

    return (
        -question.rank,
        -question.shape,
        question.subject,
        question.subject_kind,
        question.subject_id,
        _DIMENSION_ORDER[question.dimension],
    )


__all__ = [
    "ANSWERABLE_ISSUE_KINDS",
    "DECLARATION_ATTRIBUTED_KINDS",
    "DECLARATION_DIMENSIONS",
    "DIMENSION_BY_GAP_KIND",
    "SOURCE_ANSWERABLE_AUTHORITY_KINDS",
    "DeclarationDimension",
    "DeclarationQuestion",
    "DeclarationTarget",
    "action_declaration_target",
    "declaration_answer_target",
    "declaration_questions",
    "is_declaration_answerable",
    "open_counts_by_dimension",
    "open_questions",
    "progress_sentence",
]
