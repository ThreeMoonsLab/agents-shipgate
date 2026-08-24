"""One comparator for "the declared effect sits below what was observed".

#357 established that heuristic evidence must never *drive* policy: a name
match may not prove a read-only action and may not block a release on its own.
``SemanticClaim.policy_eligible`` carries that rule, and the declaration
contradiction check in :mod:`agents_shipgate.core.semantic_assessment` read it
directly — so a claim that could not drive policy could not *challenge* a
declaration either. Those are two different powers (#409). A declaration
sitting below an inferred claim is not a heuristic gating anything; it is a
human statement contradicting a recorded observation, which is exactly what a
reviewer needs to see.

This module owns that comparison, and only that comparison. Three surfaces
consume it — the resolver (which raises the evidence gap), the release
decision (which scaffolds the repair), and the action-surface lens (which
raises the reviewer-facing finding) — and they must never disagree about
whether a declaration is below its evidence, or by how much. There are already
two effect-rank tables in this codebase (``ACTION_EFFECT_RANK`` orders
capability *deltas*, and orders ``privileged_data_access`` below ``write``);
comparisons about evidence strength use :data:`EFFECT_EVIDENCE_RANK` here, and
every caller reads the answer from this module rather than re-deriving it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import cast

from agents_shipgate.core.action_semantics import ACTION_EFFECT_RANK, builtin_obligations
from agents_shipgate.core.domain import SemanticClaim
from agents_shipgate.schemas.manifest import ActionOverrideConfig
from agents_shipgate.schemas.semantic import SemanticClaimEvidence
from agents_shipgate.schemas.surfaces import ActionEffect

#: An effect claim as either the resolver's working model or the public
#: projection attached to an action fact. The two carry the same fields;
#: this comparator reads only those, so both call sites share one answer.
EffectClaim = SemanticClaim | SemanticClaimEvidence

#: Evidence strength order for one action's effect. Higher means "a stronger
#: claim about what this action does in the world", so a declaration ranked
#: below an observation is a de-escalation.
#:
#: Rank alone does not decide whether a declaration accounts for an
#: observation — see :func:`declaration_covers`. Effects are risk-ordered but
#: their *obligations* are orthogonal, and a total order over the second is
#: what let a `financial_write` declaration discharge an
#: `external_communication` observation (PR #412 review).
EFFECT_EVIDENCE_RANK: dict[ActionEffect, int] = {
    "read": 0,
    "write": 1,
    "privileged_data_access": 2,
    "identity_access": 3,
    "code_execution": 4,
    "production_operation": 5,
    "external_communication": 6,
    "financial_write": 7,
    "destructive": 8,
}
EFFECT_EVIDENCE_VALUES = frozenset(EFFECT_EVIDENCE_RANK)

#: The claim source that carries the declaration's own ``effect`` assertion. It
#: is the statement being challenged, so it can never be its own challenger.
DECLARED_EFFECT_SOURCE = "action_surface_declaration"

#: The check that reports one of these overrides. Named here, next to the
#: comparator, because the lens raises it and the PR comment reads it back: two
#: spellings of one id is how a reviewer surface quietly stops rendering rows.
EFFECT_OVERRIDE_CHECK_ID = "SHIP-ACTION-EFFECT-OVERRIDES-EVIDENCE"


@dataclass(frozen=True)
class ChallengingEvidence:
    """One inferred effect that outranks the declared one, and where it came from."""

    effect: ActionEffect
    sources: tuple[str, ...]

    def render(self) -> str:
        if not self.sources:
            return self.effect
        return f"{self.effect} ({', '.join(self.sources)})"


@dataclass(frozen=True)
class EffectOverrideAssessment:
    """What a declaration overrides, and whether the override was written down."""

    declared_effect: ActionEffect
    challenging: tuple[ChallengingEvidence, ...]
    unacknowledged: tuple[ChallengingEvidence, ...]
    stale: tuple[ActionEffect, ...]
    reason: str | None
    has_override: bool

    @property
    def challenged_effects(self) -> tuple[ActionEffect, ...]:
        return tuple(item.effect for item in self.challenging)

    @property
    def strongest_effect(self) -> ActionEffect | None:
        if not self.challenging:
            return None
        return max(self.challenged_effects, key=EFFECT_EVIDENCE_RANK.__getitem__)

    @property
    def evidence_sources(self) -> tuple[str, ...]:
        return tuple(sorted({source for item in self.challenging for source in item.sources}))

    @property
    def acknowledged(self) -> bool:
        """True only when the written override matches the observed evidence exactly.

        Exact match, not containment, in both directions. Containment would let
        one declaration pre-acknowledge every effect in the vocabulary and go
        permanently silent — the same fail-open one layer up. Requiring the sets
        to agree means new evidence re-opens the question (it is unacknowledged)
        *and* evidence that has since disappeared re-opens it too (the override
        is stale), which is the drift property an override needs to stay honest
        for as long as the manifest lives.
        """

        return self.has_override and not self.unacknowledged and not self.stale

    @property
    def weaker(self) -> tuple[ChallengingEvidence, ...]:
        """Unanswered observations that outrank the declaration on risk.

        The #409 case: a `read` declaration under an inferred
        `external_communication`. The remedy is to raise the declared effect.
        """

        return tuple(
            item
            for item in self.unacknowledged
            if EFFECT_EVIDENCE_RANK[item.effect] > EFFECT_EVIDENCE_RANK[self.declared_effect]
        )

    @property
    def uncovered(self) -> tuple[ChallengingEvidence, ...]:
        """Unanswered observations the declaration outranks but does not cover.

        A `financial_write` declaration over an inferred
        `external_communication`: higher risk, different obligations. Telling
        this reviewer to raise their effect would be wrong — it is already
        higher — so every surface has to separate the two.
        """

        weaker = set(self.weaker)
        return tuple(item for item in self.unacknowledged if item not in weaker)

    def render_evidence(self) -> str:
        """The challenged evidence, rendered once, for every surface that names it."""

        return "; ".join(item.render() for item in self.challenging)

    def render_unacknowledged(self) -> str:
        return "; ".join(item.render() for item in self.unacknowledged)


def declaration_covers(declared: ActionEffect, inferred: ActionEffect) -> bool:
    """Does declaring ``declared`` account for an observation of ``inferred``?

    Two conditions, both necessary.

    *Risk*: the declaration must not rank below the observation under **either**
    published rank table. They disagree — :data:`EFFECT_EVIDENCE_RANK` orders
    ``privileged_data_access`` above ``write`` and ``ACTION_EFFECT_RANK`` orders
    it below — and picking a winner here would either loosen an existing gate
    or leave the two surfaces contradicting each other, which is what let a
    declared ``privileged_data_access`` read as covered here while the policy
    path raised ``mixed_policy_evidence`` on the same action (PR #412 review).
    Requiring both makes the two agree without weakening either. This is the
    #409 case — ``read`` declared over an inferred ``external_communication``.

    *Obligations*: the declaration must oblige at least the controls the
    observation would. Rank is a total order; obligations are not.
    ``financial_write`` outranks ``external_communication`` and requires
    approval, audit, and idempotency — but not confirmation, which is
    precisely what communicating outward requires. Testing rank alone let a
    declaration discharge a category it does not cover: the action went
    pass-eligible with no gap and no external-communication finding, while the
    external-write risk tags sat untouched in the same report.

    Equality is covered by both conditions and is the common case. Nothing
    here decides *policy*: an uncovered observation becomes a reviewed
    question, never a control the heuristic imposed on its own (#357).
    """

    if declared == inferred:
        return True
    if EFFECT_EVIDENCE_RANK[declared] < EFFECT_EVIDENCE_RANK[inferred]:
        return False
    if ACTION_EFFECT_RANK[declared] < ACTION_EFFECT_RANK[inferred]:
        return False
    return builtin_obligations(inferred).issubset(builtin_obligations(declared))


def declared_effect_from_claims(claims: Sequence[EffectClaim]) -> ActionEffect | None:
    """Recover the declared effect from an already-resolved assessment.

    The release decision builds its repair scaffold from the tool alone; the
    manifest declaration is not in scope there, but the claim it authored is.
    """

    for claim in claims:
        if (
            claim.dimension == "effect"
            and claim.source == DECLARED_EFFECT_SOURCE
            and claim.value in EFFECT_EVIDENCE_VALUES
        ):
            return cast(ActionEffect, claim.value)
    return None


def assess_effect_override(
    claims: Iterable[EffectClaim],
    *,
    declared_effect: ActionEffect | None,
    override: ActionOverrideConfig | None,
) -> EffectOverrideAssessment | None:
    """Compare one declared effect against the observations that outrank it.

    Returns ``None`` when there is nothing to say: no declared effect, or no
    observation above it and no override claiming there is one. Policy-eligible
    contradictions are deliberately excluded — those already resolve to
    ``conflicting`` status and a ``conflicting_effect_evidence`` gap, and
    reporting them twice would say the same thing in two vocabularies.
    """

    if declared_effect is None:
        return None
    claims = list(claims)
    # Everything the reviewed surface asserts, not the `effect` field alone.
    # `risk_tags: [financial_action]` produces a policy-eligible
    # `financial_write` claim and applies the financial-write controls, so a
    # heuristic saying the same thing is not an unaccounted-for observation.
    # This is the set `_control_effects` unions for exactly that reason, and
    # matching it keeps the declaration comparator and the policy path on one
    # answer.
    covering: set[ActionEffect] = {declared_effect}
    covering.update(
        cast(ActionEffect, claim.value)
        for claim in claims
        if claim.dimension == "effect"
        and claim.policy_eligible
        and claim.value in EFFECT_EVIDENCE_VALUES
    )
    sources_by_effect: dict[ActionEffect, set[str]] = {}
    for claim in claims:
        if claim.dimension != "effect":
            continue
        if claim.policy_eligible:
            continue
        if claim.source == DECLARED_EFFECT_SOURCE:
            continue
        # A protocol default is the absence of an observation, not one. An MCP
        # tool with no annotations says nothing about its own effect, and the
        # declaration is the answer to that silence, not a contradiction of it.
        if claim.basis == "protocol_default":
            continue
        if claim.value not in EFFECT_EVIDENCE_VALUES:
            continue
        effect = cast(ActionEffect, claim.value)
        if any(declaration_covers(asserted, effect) for asserted in covering):
            continue
        sources_by_effect.setdefault(effect, set()).add(claim.source)

    challenging = tuple(
        ChallengingEvidence(effect=effect, sources=tuple(sorted(sources_by_effect[effect])))
        for effect in sorted(
            sources_by_effect,
            key=lambda value: (EFFECT_EVIDENCE_RANK[value], value),
        )
    )
    if not challenging and override is None:
        return None

    acknowledged = set(override.evidence) if override is not None else set()
    unacknowledged = tuple(item for item in challenging if item.effect not in acknowledged)
    stale = tuple(
        sorted(
            acknowledged.difference(item.effect for item in challenging),
            key=lambda value: (EFFECT_EVIDENCE_RANK[value], value),
        )
    )
    return EffectOverrideAssessment(
        declared_effect=declared_effect,
        challenging=challenging,
        unacknowledged=unacknowledged,
        stale=stale,
        reason=override.reason if override is not None else None,
        has_override=override is not None,
    )


def render_override_issue(assessment: EffectOverrideAssessment) -> str:
    """Why this declaration is still unanswered, in one sentence.

    One renderer, because the gap row a coding agent reads and the finding a
    reviewer reads must name the same declared value, the same inferred value,
    and the same hint source. Two spellings of one comparison is how the
    exclusion ledger lost a tool it had already gated (#404).
    """

    clauses: list[str] = []
    if assessment.unacknowledged:
        qualifier = " not acknowledged by override.evidence" if assessment.has_override else ""
        # Two ways a declaration fails to account for an observation, and they
        # want different words. Telling a reviewer who declared `financial_write`
        # that it is "weaker than" `external_communication` is simply false, and
        # sends them to raise an effect that is already higher.
        weaker = assessment.weaker
        uncovered = assessment.uncovered
        if weaker:
            rendered = "; ".join(item.render() for item in weaker)
            clauses.append(
                f"declared effect {assessment.declared_effect!r} is weaker than inferred "
                f"evidence{qualifier}: {rendered}"
            )
        if uncovered:
            rendered = "; ".join(item.render() for item in uncovered)
            clauses.append(
                f"declared effect {assessment.declared_effect!r} does not carry the "
                f"controls required by inferred evidence{qualifier}: {rendered}"
            )
    if assessment.stale:
        clauses.append(
            "override.evidence names effect evidence this scan did not observe: "
            + ", ".join(assessment.stale)
        )
    if not clauses:  # pragma: no cover - acknowledged assessments raise no issue
        return (
            f"declared effect {assessment.declared_effect!r} overrides "
            f"{assessment.render_evidence()}"
        )
    return "; ".join(clauses)


__all__ = [
    "DECLARED_EFFECT_SOURCE",
    "EffectClaim",
    "EFFECT_EVIDENCE_RANK",
    "EFFECT_EVIDENCE_VALUES",
    "EFFECT_OVERRIDE_CHECK_ID",
    "ChallengingEvidence",
    "EffectOverrideAssessment",
    "assess_effect_override",
    "declaration_covers",
    "declared_effect_from_claims",
    "render_override_issue",
]
