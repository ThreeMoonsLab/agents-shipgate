"""The declaration questions an adoption still owes, as one projection.

``insufficient_evidence`` on an unconfigured repository presents itself as a
count of gaps — "36 semantic evidence gaps" — which names a symptom, gives no
finish line, and says nothing about which of the 36 could move the verdict.
The same facts, asked as a numbered questionnaire with a progress counter, are
a task a person can finish (#410 increment 2).

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

Ordering is by how much answering can move the verdict, strongest action
first. The fourth adoption walk of ``adk-samples#1745`` reached ``blocked``
after declaring 2 of 12 tools — the two that moved money and communicated
outward — so a questionnaire that leads with them reaches the same verdict in
two answers instead of twelve. Ordering is *ranking only*: it decides what to
read first, never what the verdict is.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from agents_shipgate.core.domain import (
    DECLARATION_CLAIM_SOURCES,
    SemanticIssue,
    Tool,
    ToolSemanticAssessment,
)
from agents_shipgate.core.semantic_assessment import (
    assess_tool_semantics,
    effect_evidence_rank,
)
from agents_shipgate.schemas.report import DeclarationQuestionCoverage

#: The two dimensions a per-action declaration answers.
#:
#: Deliberately not every dimension that can gap. A tool inventory and an
#: ``agent_bindings`` root are human declarations too, but neither is per
#: action and neither has a counterfactual this module can evaluate — removing
#: an inventory does not re-run extraction. Counting what cannot be counted on
#: both halves is how a progress bar starts lying, so the denominator is the
#: two dimensions whose answers live on one ``action_surface.actions`` row.
DeclarationDimension = Literal["effect", "authority"]

DECLARATION_DIMENSIONS: tuple[DeclarationDimension, ...] = ("effect", "authority")

#: Issue kinds a reviewed ``action_surface.actions`` row can close, per
#: dimension. Enumerated, never derived from ``issue.dimension``: see the
#: module docstring for the two kinds that ride on a dimension they cannot be
#: answered on.
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
            "partial_authority_evidence",
            "conflicting_authority_evidence",
        }
    ),
}

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


@dataclass(frozen=True)
class DeclarationQuestion:
    """One (action, dimension) a reviewed declaration has to answer.

    Deliberately carries no readings and no proposed answer. Those belong to
    the *row* a reviewer answers — ``EvidenceGapAction.observed_readings`` and
    ``declaration_template`` — and deriving them a second time here would be
    two sources for one row's contents. This model is the identity of a
    question and its place in the queue, nothing more.
    """

    tool_id: str
    subject: str
    dimension: DeclarationDimension
    answered: bool
    rank: int


def declaration_questions(tools: Iterable[Tool]) -> list[DeclarationQuestion]:
    """Every declaration question this scan asks, answered ones included.

    Deterministic and total over the catalog: highest-risk action first, then
    by subject, then effect before authority. A tool with no semantic
    assessment contributes nothing — it has not been resolved, so nothing is
    known about what it owes.
    """

    questions: list[DeclarationQuestion] = []
    for tool in tools:
        assessment = tool.semantic_assessment
        if assessment is None:
            continue
        # The counterfactual: what this action would owe with no declaration at
        # all. Computed once per tool, and only when a declaration could
        # possibly be doing the work.
        undeclared = (
            assess_tool_semantics(tool, None) if _has_declaration(assessment) else assessment
        )
        rank = effect_evidence_rank(assessment.conservative_effect)
        subject = _subject(tool)
        for dimension in DECLARATION_DIMENSIONS:
            question = _question(
                tool_id=tool.id,
                subject=subject,
                dimension=dimension,
                rank=rank,
                assessment=assessment,
                undeclared=undeclared,
            )
            if question is not None:
                questions.append(question)
    questions.sort(key=_ordering)
    return questions


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


def _question(
    *,
    tool_id: str,
    subject: str,
    dimension: DeclarationDimension,
    rank: int,
    assessment: ToolSemanticAssessment,
    undeclared: ToolSemanticAssessment,
) -> DeclarationQuestion | None:
    answerable = ANSWERABLE_ISSUE_KINDS[dimension]
    if _asks(_issues(assessment, dimension), answerable):
        return DeclarationQuestion(
            tool_id=tool_id,
            subject=subject,
            dimension=dimension,
            answered=False,
            rank=rank,
        )
    if _asks(_issues(undeclared, dimension), answerable):
        # The dimension is clean *and* it would not have been without the
        # declaration: a human answered this one.
        return DeclarationQuestion(
            tool_id=tool_id,
            subject=subject,
            dimension=dimension,
            answered=True,
            rank=rank,
        )
    return None


def _issues(
    assessment: ToolSemanticAssessment,
    dimension: DeclarationDimension,
) -> Sequence[SemanticIssue]:
    return assessment.effect.issues if dimension == "effect" else assessment.authority.issues


def _asks(issues: Sequence[SemanticIssue], answerable: frozenset[str]) -> bool:
    return any(issue.kind in answerable for issue in issues)


def _has_declaration(assessment: ToolSemanticAssessment) -> bool:
    """True when the manifest declares anything about this action.

    Read off the claims the resolver authored rather than by re-reading the
    manifest: the declaration reaches this module only through the assessment,
    and re-deriving which tool a declaration keyed onto is the second
    implementation of a join the resolver already made.
    """

    return any(
        claim.source in DECLARATION_CLAIM_SOURCES
        for claim in (*assessment.effect.claims, *assessment.authority.claims)
    )


def _subject(tool: Tool) -> str:
    """The gap-row display label, so both surfaces name an action identically."""

    return f"{tool.name} [{tool.provider or tool.source_id or tool.source_type}]"


def _ordering(question: DeclarationQuestion) -> tuple[int, str, int, str]:
    return (
        -question.rank,
        question.subject,
        _DIMENSION_ORDER[question.dimension],
        question.tool_id,
    )


__all__ = [
    "ANSWERABLE_ISSUE_KINDS",
    "DECLARATION_DIMENSIONS",
    "DIMENSION_BY_GAP_KIND",
    "DeclarationDimension",
    "DeclarationQuestion",
    "declaration_questions",
    "open_counts_by_dimension",
    "open_questions",
    "progress_sentence",
]
