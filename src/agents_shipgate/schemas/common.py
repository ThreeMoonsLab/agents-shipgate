from __future__ import annotations

from typing import Literal, cast, get_args

from pydantic import BaseModel, ConfigDict

Severity = Literal["info", "low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]
BaselineStatus = Literal["new", "matched", "resolved"]
# The canonical release-verdict vocabulary — the ONE enum the whole system
# gates on. ``build_release_decision()`` is the only place that computes it;
# every other "verdict"/"decision" field (AgentSummary, ReviewerSummary,
# VerifierSummary, ReleaseConsequence) re-uses this exact alias so the
# vocabulary can never be re-spelled or drift out of lockstep. The
# agent-facing ``MergeVerdict`` (schemas/verifier.py) is a deterministic
# projection of this via ``map_merge_verdict()``.
ReleaseDecisionStatus = Literal[
    "blocked",
    "review_required",
    "insufficient_evidence",
    "passed",
]
# v0.15: per-finding provenance kind. Independent of `confidence` —
# `confidence` records how sure a rule is; `provenance_kind` records
# *what kind of rule fired* (and what artifact it inspected). Lets
# agents filter heuristic-only findings from declarative ones.
#
# - ``static_declaration`` — value came from manifest/MCP/OpenAPI schema
#   or other declared metadata.
# - ``ast_extraction`` — parsed from user Python source (framework
#   extractors: ADK, LangChain, CrewAI).
# - ``keyword_heuristic`` — matched a keyword list in core/heuristics or
#   per-check token sets.
# - ``regex_heuristic`` — matched a regex (injection, secrets).
# - ``policy_pack`` — external rule from a loaded policy pack.
ProvenanceKind = Literal[
    "static_declaration",
    "ast_extraction",
    "keyword_heuristic",
    "regex_heuristic",
    "policy_pack",
]
# v0.12: per-finding agent action enum.
#
# Deterministic projection of the existing `patches`, `autofix_safe`, and
# `requires_human_review` fields. Lets a coding agent read one canonical
# field instead of synthesizing an action from four. See
# :func:`agents_shipgate.core.findings.derive_agent_action` for the
# exact decision tree.
#
# - ``auto_apply`` — `apply-patches --confidence high` will resolve
#   cleanly. The finding has at least one non-manual patch and every
#   patch is high-confidence.
# - ``propose_patch_for_review`` — at least one non-manual patch is
#   attached and machine-applicable, but the full patch set is not
#   auto-safe. Two shapes land here: (a) every non-manual patch is
#   medium- or low-confidence, and (b) a high-confidence non-manual
#   patch sits alongside one or more ``ManualPatch`` siblings (the
#   non-manual is safe to apply, but the manual instructions still
#   need a human). In both cases the agent should ask the user before
#   running ``apply-patches`` and surface any manual instructions
#   verbatim.
# - ``escalate_to_human`` — no machine-applicable patch. Either every
#   patch is ``ManualPatch``, or ``patches`` is empty/absent and the
#   check requires human review.
# - ``suppress_with_reason`` — reserved for future check classes that
#   explicitly mark themselves as suppressible. Not emitted by the
#   built-in deterministic projection in v0.12; schema accepts the
#   value so callers can extend without a schema bump.
# - ``informational`` — no action required (suppressed finding, or
#   non-actionable advisory).
AgentAction = Literal[
    "auto_apply",
    "propose_patch_for_review",
    "escalate_to_human",
    "suppress_with_reason",
    "informational",
]


def parse_severity(value: str) -> Severity:
    if value not in get_args(Severity):
        raise ValueError(f"Unsupported severity: {value}")
    return cast(Severity, value)


def parse_confidence(value: str) -> Confidence:
    if value not in get_args(Confidence):
        raise ValueError(f"Unsupported confidence: {value}")
    return cast(Confidence, value)


def parse_provenance_kind(value: str) -> ProvenanceKind:
    if value not in get_args(ProvenanceKind):
        raise ValueError(f"Unsupported provenance kind: {value}")
    return cast(ProvenanceKind, value)


def confidence_rank(confidence: str | None) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(confidence or "", 0)


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    ref: str | None = None
    location: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    start_column: int | None = None
    pointer: str | None = None


HitlProvenanceType = Literal[
    "approval_trace",
    "override_log",
    "high_risk_exclusion",
    "promotion_criteria",
    "manifest_requirement",
]
HitlProvenanceStatus = Literal[
    "requirement_only",
    "expected_but_absent",
    "source_load_failed",
    "loaded",
    "loaded_with_warnings",
]


class HitlSourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: HitlProvenanceType
    ref: str
    location: str
    status: HitlProvenanceStatus
    detail: str


def sorted_hitl_source_provenance(
    items: list[HitlSourceProvenance],
) -> list[HitlSourceProvenance]:
    by_key = {
        (item.type, item.ref, item.location, item.status, item.detail): item
        for item in items
    }
    return [
        by_key[key]
        for key in sorted(
            by_key,
            key=lambda item: (item[0], item[1], item[2], item[3], item[4]),
        )
    ]
