"""Base-vs-head action declaration review projection.

``action_surface_facts`` describes resolved capabilities.  It deliberately
does not preserve enough manifest-row identity to tell an added declaration
from a changed answer, so the PR review surface carries a separate parsed-row
snapshot and joins that snapshot back to semantic evidence by canonical tool
id only.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Literal

from agents_shipgate.core.domain import Tool
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.semantic_assessment import (
    declaration_covers,
    declaration_effects,
    effect_readings,
)
from agents_shipgate.core.tool_identity import ToolSelectorIndex
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import (
    AcknowledgedEffectOverride,
    DeclarationReviewDecision,
    DeclarationReviewRow,
    DeclarationReviewSummary,
    EvidenceGap,
    EvidenceReading,
)
from agents_shipgate.schemas.surfaces import (
    ActionDeclarationFact,
    ActionDeclarationFacts,
    ActionDeclarationSelectorFact,
    ActionSurfaceFacts,
)

_EFFECT_GAP_KINDS = {
    "incomplete_surface",
    "missing_effect_evidence",
    "inferred_effect_only",
    "conflicting_effect_evidence",
    "declaration_below_inferred_evidence",
    "declaration_drift",
    "invalid_semantic_annotation",
}

# A public report must never preserve a digest derived from secret override
# prose.  Once privacy redaction rewrites either override field, equality with
# a base report is unknowable: two different secrets and one unchanged secret
# all project to the same marker.  This public, non-secret sentinel keeps that
# uncertainty explicit so the comparison can fail closed without publishing a
# dictionary-attackable hash of the raw value.
_INDETERMINATE_OVERRIDE_IDENTITY = "action_declaration_override_indeterminate"


def build_action_declaration_facts(
    manifest: AgentsShipgateManifest,
    tools: list[Tool],
    *,
    manifest_path: str = "shipgate.yaml",
) -> ActionDeclarationFacts:
    """Snapshot parsed manifest rows and resolve each to one canonical tool.

    Row identity is the normalized selector, not list position or YAML bytes.
    Reordering rows, comments, and formatting are therefore silent. ``basis``
    is carried for reviewer context but deliberately excluded from
    ``declaration_hash``: it is a digest of evidence, not a reviewed semantic
    declaration, so a pin-only refresh is not a declaration change.
    """

    return _build_action_declaration_facts(
        manifest,
        tools,
        manifest_path=manifest_path,
        allow_public_collisions=False,
        indeterminate_override_positions=frozenset(),
    )


def build_public_action_declaration_facts(
    manifest: AgentsShipgateManifest,
    tools: list[Tool],
    *,
    manifest_path: str = "shipgate.yaml",
    indeterminate_override_positions: frozenset[int] = frozenset(),
) -> ActionDeclarationFacts:
    """Build the post-redaction declaration snapshot without revalidation.

    Privacy redaction is intentionally many-to-one. Distinct valid raw
    selectors can therefore become the same public selector. The raw manifest
    has already been validated before this projection; public collisions are
    retained as distinct ordinal row ids and every colliding row is marked
    ambiguous so no one can borrow the other's semantic evidence.

    ``indeterminate_override_positions`` identifies rows whose evidence or
    reason changed under redaction. Their public identity is the fixed
    sentinel above, never a digest of the secret-bearing raw value.
    """

    return _build_action_declaration_facts(
        manifest,
        tools,
        manifest_path=manifest_path,
        allow_public_collisions=True,
        indeterminate_override_positions=indeterminate_override_positions,
    )


def _build_action_declaration_facts(
    manifest: AgentsShipgateManifest,
    tools: list[Tool],
    *,
    manifest_path: str,
    allow_public_collisions: bool,
    indeterminate_override_positions: frozenset[int],
) -> ActionDeclarationFacts:
    index = ToolSelectorIndex.build(tools)
    rows: list[ActionDeclarationFact] = []
    seen: dict[str, ActionDeclarationSelectorFact] = {}
    row_id_counts: Counter[str] = Counter()
    colliding_row_ids: set[str] = set()
    row_bases: list[str] = []
    for position, declaration in enumerate(manifest.action_surface.actions):
        selector = ActionDeclarationSelectorFact(
            tool=declaration.tool,
            tool_id=declaration.tool_id,
            source_type=declaration.source_type,
            source_id=declaration.source_id,
            provider=declaration.provider,
            operation=declaration.operation,
        )
        base_row_id = _content_id(
            "action_declaration", selector.model_dump(mode="json")
        )
        row_id_counts[base_row_id] += 1
        ordinal = row_id_counts[base_row_id]
        if base_row_id in seen and not allow_public_collisions:
            raise ConfigError(
                "Multiple action_surface.actions rows produced the same declaration "
                f"row id {base_row_id!r}: "
                f"{seen[base_row_id].model_dump(mode='json')!r} and "
                f"{selector.model_dump(mode='json')!r}. Refusing to overwrite a "
                "declaration-row identity."
            )
        if ordinal > 1:
            colliding_row_ids.add(base_row_id)
        row_id = base_row_id if ordinal == 1 else f"{base_row_id}#{ordinal}"
        seen.setdefault(base_row_id, selector)

        resolution = index.resolve(declaration)
        if resolution.resolved:
            tool = resolution.matches[0]
            subject_id = tool.id
            subject = _subject(tool)
            resolution_kind: Literal["resolved", "unresolved", "ambiguous"] = "resolved"
        else:
            subject_id = None
            subject = declaration.tool
            resolution_kind = (
                "ambiguous"
                if resolution.kind == "ambiguous_tool_selector"
                else "unresolved"
            )

        # #410 §D is the effect proposal surface, not every policy/control on
        # an action row. Authority, scopes, approval, safeguards, and evidence
        # have their own review surfaces; including them here could label an
        # approval-only change ✓ merely because the unchanged effect had
        # evidence. ``basis`` is also excluded: it is a derived evidence pin,
        # not a human semantic answer. A newly added row is still detected by
        # ``row_id`` absence even when this projection is otherwise empty.
        # Hash the exact semantic proposal, not its YAML spelling.  Unmapped
        # tags are not effect answers, synonymous/reordered tags are the same
        # answer, and an override changes only when its reviewed evidence or
        # reason changes.  A newly added row is still detected by row-id
        # absence even when this proposal is empty.
        row_effects = declaration_effects(
            declaration.effect,
            declaration.risk_tags,
        )
        override_identity = None
        if declaration.override is not None:
            override_identity = (
                _INDETERMINATE_OVERRIDE_IDENTITY
                if position in indeterminate_override_positions
                else _override_identity(
                    declaration.override.evidence,
                    declaration.override.reason,
                )
            )
        normalized = {
            "effects": list(row_effects),
            "override_identity": override_identity,
        }
        rows.append(
            ActionDeclarationFact(
                row_id=row_id,
                selector=selector,
                subject=subject,
                subject_id=subject_id,
                resolution=resolution_kind,
                declared_effect=declaration.effect,
                declared_risk_tags=list(declaration.risk_tags),
                has_override=declaration.override is not None,
                override_identity=override_identity,
                basis=declaration.basis,
                declaration_hash=_content_id("action_declaration_value", normalized),
                manifest_path=(
                    f"{manifest_path}#action_surface.actions[{position}]"
                ),
            )
        )
        row_bases.append(base_row_id)

    # Distinct selectors may be aliases for the same canonical tool (for
    # example a name-only row beside a tool-id row).  The semantic assessment
    # is aggregate per tool, so neither row may be classified from it as
    # though the other did not exist.  Mark every such raw row ambiguous;
    # changed rows then fail closed before an effect or override join.
    subjects = Counter(
        row.subject_id
        for row in rows
        if row.resolution == "resolved" and row.subject_id is not None
    )
    duplicate_subjects = {
        subject_id for subject_id, count in subjects.items() if count > 1
    }
    if duplicate_subjects or colliding_row_ids:
        rows = [
            row.model_copy(update={"resolution": "ambiguous"})
            if row.subject_id in duplicate_subjects
            or row_bases[index] in colliding_row_ids
            else row
            for index, row in enumerate(rows)
        ]
    return ActionDeclarationFacts(rows=sorted(rows, key=lambda row: row.row_id))


def build_declaration_review(
    *,
    head: ActionDeclarationFacts,
    base: ActionDeclarationFacts | None,
    action_surface_facts: ActionSurfaceFacts,
    evidence_gaps: list[EvidenceGap],
    acknowledged_overrides: list[AcknowledgedEffectOverride],
    base_kind: Literal["report", "absent_manifest"] = "report",
    unavailable_note: str | None = None,
) -> DeclarationReviewDecision:
    """Classify only added/modified head declaration rows.

    The three buckets are exhaustive by construction. Anything that cannot be
    proved evidence-consistent is ``unverified`` unless the exact canonical
    subject has an acknowledged override row. This fail-closed bucket includes
    no observations, heuristic-only observations, semantic conflicts/drift,
    unresolved selectors, and declarations that do not cover every observed
    reading.
    """

    if base is None:
        return DeclarationReviewDecision(
            notes=[
                unavailable_note
                or "No trustworthy base declaration snapshot was available; declaration review disabled."
            ]
        )

    head_by_id = _unique_rows(head, side="head")
    base_by_id = _unique_rows(base, side="base")
    if head_by_id is None or base_by_id is None:
        return DeclarationReviewDecision(
            notes=[
                "A declaration snapshot contained duplicate row ids; declaration review disabled."
            ]
        )

    changed: list[tuple[ActionDeclarationFact, Literal["added", "modified"]]] = []
    for row_id in sorted(head_by_id):
        row = head_by_id[row_id]
        before = base_by_id.get(row_id)
        if before is None:
            changed.append((row, "added"))
        elif (
            before.declaration_hash != row.declaration_hash
            or _override_identity_is_indeterminate(before.override_identity)
            or _override_identity_is_indeterminate(row.override_identity)
        ):
            changed.append((row, "modified"))

    actions = {row.tool_id: row for row in action_surface_facts.actions}
    gaps_by_subject: dict[str, set[str]] = {}
    for gap in evidence_gaps:
        if gap.subject_kind != "action" or not gap.subject_id:
            continue
        gaps_by_subject.setdefault(gap.subject_id, set()).add(str(gap.kind))
    overrides_by_subject: dict[str, list[AcknowledgedEffectOverride]] = {}
    for override in acknowledged_overrides:
        if override.subject_id:
            overrides_by_subject.setdefault(override.subject_id, []).append(override)

    rows = [
        _classify_changed_row(
            row,
            change_type=change_type,
            action=actions.get(row.subject_id or ""),
            effect_gap_kinds=(
                gaps_by_subject.get(row.subject_id or "", set()) & _EFFECT_GAP_KINDS
            ),
            overrides=overrides_by_subject.get(row.subject_id or "", []),
        )
        for row, change_type in changed
    ]
    counts = Counter(row.bucket for row in rows)
    return DeclarationReviewDecision(
        enabled=True,
        base_kind=base_kind,
        changed_count=len(rows),
        summary=DeclarationReviewSummary(
            evidence_consistent=counts["evidence_consistent"],
            unverified=counts["unverified"],
            acknowledged_override=counts["acknowledged_override"],
        ),
        rows=rows,
    )


def _classify_changed_row(
    row: ActionDeclarationFact,
    *,
    change_type: Literal["added", "modified"],
    action: Any | None,
    effect_gap_kinds: set[str],
    overrides: list[AcknowledgedEffectOverride],
) -> DeclarationReviewRow:
    if row.resolution != "resolved" or row.subject_id is None:
        return _review_row(
            row,
            change_type,
            "unverified",
            reason=f"The declaration selector is {row.resolution}; no canonical evidence join exists.",
        )
    if _override_identity_is_indeterminate(row.override_identity):
        return _review_row(
            row,
            change_type,
            "unverified",
            reason=(
                "Override equality is indeterminate after privacy redaction; "
                "review the current evidence and reason directly."
            ),
        )
    # An override counts only on the canonical substrate of this changed row.
    # A same-named action from another provider can never donate its review.
    canonical_overrides = [
        override
        for override in overrides
        if row.has_override
        and row.override_identity is not None
        and override.subject_id == row.subject_id
        and override.subject == row.subject
        and override.declared_effect == row.declared_effect
        and _override_identity(override.evidence, override.reason)
        == row.override_identity
    ]
    if canonical_overrides:
        return _review_row(
            row,
            change_type,
            "acknowledged_override",
            reason="A reviewer acknowledged a declaration below inferred evidence.",
            overrides=canonical_overrides,
        )
    if action is None or action.semantic_assessment is None:
        return _review_row(
            row,
            change_type,
            "unverified",
            reason="No normalized semantic assessment exists for this declaration.",
        )

    readings = effect_readings(action.semantic_assessment.effect)
    # Read the proposal from this row only.  Aggregate action claims can carry
    # reviewed declarations from another selector that resolves to the same
    # tool, so they are evidence about the action but never the declaration
    # surface being reviewed here.
    covering_effects = set(
        declaration_effects(row.declared_effect, row.declared_risk_tags)
    )
    if not covering_effects:
        return _review_row(
            row,
            change_type,
            "unverified",
            reason="The changed row declares no effect-bearing proposal.",
        )
    observed = [reading for reading in readings if reading.observed]
    public_readings = [
        EvidenceReading(
            effect=reading.effect,
            sources=list(reading.sources),
            observed=reading.observed,
            policy_eligible=reading.policy_eligible,
        )
        for reading in readings
    ]
    if effect_gap_kinds or action.semantic_assessment.effect.issues:
        kinds = sorted(
            effect_gap_kinds
            or {str(issue.kind) for issue in action.semantic_assessment.effect.issues}
        )
        return _review_row(
            row,
            change_type,
            "unverified",
            readings=public_readings,
            reason=f"Effect evidence remains unresolved ({', '.join(kinds)}).",
        )
    if not observed:
        return _review_row(
            row,
            change_type,
            "unverified",
            readings=public_readings,
            reason="The scan observed no action-effect reading for this declaration.",
        )
    if not all(reading.policy_eligible for reading in observed):
        return _review_row(
            row,
            change_type,
            "unverified",
            readings=public_readings,
            reason=(
                "At least one observed effect reading is not policy-eligible; "
                "mixed-strength evidence cannot machine-verify this declaration."
            ),
        )
    uncovered = [
        reading.effect
        for reading in observed
        if not any(
            declaration_covers(declared, reading.effect)
            for declared in covering_effects
        )
    ]
    if uncovered:
        return _review_row(
            row,
            change_type,
            "unverified",
            readings=public_readings,
            reason=(
                "The declared effect does not cover observed reading(s): "
                + ", ".join(sorted(set(uncovered)))
                + "."
            ),
        )
    return _review_row(
        row,
        change_type,
        "evidence_consistent",
        readings=public_readings,
        reason="The declared effect covers every observed effect reading.",
    )


def _review_row(
    row: ActionDeclarationFact,
    change_type: Literal["added", "modified"],
    bucket: Literal["evidence_consistent", "unverified", "acknowledged_override"],
    *,
    reason: str,
    readings: list[EvidenceReading] | None = None,
    overrides: list[AcknowledgedEffectOverride] | None = None,
) -> DeclarationReviewRow:
    return DeclarationReviewRow(
        row_id=row.row_id,
        change_type=change_type,
        bucket=bucket,
        subject=row.subject,
        subject_id=row.subject_id,
        declared_effect=row.declared_effect,
        declared_risk_tags=row.declared_risk_tags,
        observed_readings=readings or [],
        reason=reason,
        manifest_path=row.manifest_path,
        acknowledged_overrides=overrides or [],
    )


def _unique_rows(
    facts: ActionDeclarationFacts,
    *,
    side: str,
) -> dict[str, ActionDeclarationFact] | None:
    rows: dict[str, ActionDeclarationFact] = {}
    for row in facts.rows:
        if row.row_id in rows:
            return None
        rows[row.row_id] = row
    return rows


def _subject(tool: Tool) -> str:
    return f"{tool.name} [{tool.provider or tool.source_id or tool.source_type}]"


def _content_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _override_identity(evidence: str, reason: str) -> str:
    return _content_id(
        "action_declaration_override",
        {"evidence": evidence, "reason": reason},
    )


def _override_identity_is_indeterminate(identity: str | None) -> bool:
    return identity == _INDETERMINATE_OVERRIDE_IDENTITY


__all__ = [
    "build_action_declaration_facts",
    "build_public_action_declaration_facts",
    "build_declaration_review",
]
