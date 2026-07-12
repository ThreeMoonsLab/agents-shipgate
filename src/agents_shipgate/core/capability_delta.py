from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_shipgate.core.action_semantics import ACTION_EFFECT_RANK
from agents_shipgate.schemas.capabilities import (
    CapabilityFactV1,
    capability_fact_sort_key,
)
from agents_shipgate.schemas.capability_semantics import (
    CapabilityHashName,
    CapabilitySemanticChange,
    CapabilitySemanticDirection,
    capability_semantic_change_sort_key,
)

SEMANTIC_CAPABILITY_HASH_FIELDS: tuple[CapabilityHashName, ...] = (
    "identity_hash",
    "binding_hash",
    "effect_hash",
    "authority_hash",
    "control_hash",
    "schema_hash",
    "risk_hash",
)
ALL_CAPABILITY_HASH_FIELDS: tuple[CapabilityHashName, ...] = (
    *SEMANTIC_CAPABILITY_HASH_FIELDS,
    "evidence_hash",
)

_REVERSIBILITY_RANK = {"reversible": 0, "unknown": 1, "irreversible": 2}
_CONTROL_FIELDS = (
    "approval_required",
    "confirmation_required",
    "safeguard_idempotency",
    "safeguard_audit_log",
    "safeguard_rollback",
    "safeguard_dry_run",
)
_CONTROL_METADATA_FIELDS = (
    "approval_threshold",
    "evidence_owner",
    "evidence_runbook",
    "evidence_approval_ticket",
)
_EFFECT_BOOL_FIELDS = (
    "externally_visible",
    "handles_sensitive_data",
    "financial",
    "code_execution",
    "high_risk",
)


@dataclass(frozen=True)
class CapabilityFactContext:
    fact: CapabilityFactV1
    action_id: str | None = None
    input_fields: tuple[str, ...] = ()
    required_input_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityDeltaRow:
    id: str
    before: CapabilityFactV1
    after: CapabilityFactV1
    changed_hashes: tuple[CapabilityHashName, ...]
    semantic_direction: CapabilitySemanticDirection
    semantic_changes: tuple[CapabilitySemanticChange, ...]
    before_context: CapabilityFactContext | None = None
    after_context: CapabilityFactContext | None = None


@dataclass(frozen=True)
class CapabilitySetDiff:
    added: list[CapabilityFactContext]
    removed: list[CapabilityFactContext]
    reidentified: list[CapabilityDeltaRow]
    changed: list[CapabilityDeltaRow]
    evidence_changed: list[CapabilityDeltaRow]
    unchanged_count: int


def diff_capability_fact_sets(
    base: list[CapabilityFactContext] | list[CapabilityFactV1],
    head: list[CapabilityFactContext] | list[CapabilityFactV1],
) -> CapabilitySetDiff:
    base_contexts = _contexts(base)
    head_contexts = _contexts(head)
    base_by_id = {ctx.fact.id: ctx for ctx in base_contexts}
    head_by_id = {ctx.fact.id: ctx for ctx in head_contexts}

    raw_added = _sort_contexts(
        [head_by_id[capability_id] for capability_id in head_by_id.keys() - base_by_id]
    )
    raw_removed = _sort_contexts(
        [base_by_id[capability_id] for capability_id in base_by_id.keys() - head_by_id]
    )
    reidentified, added, removed = _reidentified_changes(raw_removed, raw_added)
    changed: list[CapabilityDeltaRow] = []
    evidence_changed: list[CapabilityDeltaRow] = []
    unchanged_count = 0

    for capability_id in sorted(base_by_id.keys() & head_by_id.keys()):
        before = base_by_id[capability_id]
        after = head_by_id[capability_id]
        changed_hashes = changed_capability_hashes(before.fact, after.fact)
        if not changed_hashes:
            unchanged_count += 1
            continue
        row = build_capability_delta_row(
            before,
            after,
            row_id=capability_id,
            changed_hashes=changed_hashes,
        )
        if row.semantic_direction == "evidence_only":
            evidence_changed.append(row)
        else:
            changed.append(row)

    return CapabilitySetDiff(
        added=added,
        removed=removed,
        reidentified=sorted(reidentified, key=capability_delta_row_sort_key),
        changed=sorted(changed, key=capability_delta_row_sort_key),
        evidence_changed=sorted(evidence_changed, key=capability_delta_row_sort_key),
        unchanged_count=unchanged_count,
    )


def build_capability_delta_row(
    before: CapabilityFactContext,
    after: CapabilityFactContext,
    *,
    row_id: str,
    changed_hashes: tuple[CapabilityHashName, ...] | None = None,
) -> CapabilityDeltaRow:
    changed_hashes = changed_hashes or changed_capability_hashes(
        before.fact, after.fact
    )
    semantic_direction, semantic_changes = classify_capability_delta(
        before,
        after,
        changed_hashes=changed_hashes,
    )
    return CapabilityDeltaRow(
        id=row_id,
        before=before.fact,
        after=after.fact,
        changed_hashes=changed_hashes,
        semantic_direction=semantic_direction,
        semantic_changes=semantic_changes,
        before_context=before,
        after_context=after,
    )


def classify_capability_delta(
    before: CapabilityFactContext | CapabilityFactV1,
    after: CapabilityFactContext | CapabilityFactV1,
    *,
    changed_hashes: tuple[CapabilityHashName, ...] | None = None,
) -> tuple[CapabilitySemanticDirection, tuple[CapabilitySemanticChange, ...]]:
    before_ctx = _context(before)
    after_ctx = _context(after)
    changed_hashes = changed_hashes or changed_capability_hashes(
        before_ctx.fact, after_ctx.fact
    )
    if changed_hashes == ("evidence_hash",):
        return (
            "evidence_only",
            (
                CapabilitySemanticChange(
                    kind="evidence_changed",
                    field="evidence",
                    before=_compact_source(before_ctx.fact),
                    after=_compact_source(after_ctx.fact),
                    rationale="Capability source evidence changed without semantic drift.",
                ),
            ),
        )

    changes: list[tuple[CapabilitySemanticDirection, CapabilitySemanticChange]] = []
    _compare_sets(
        changes,
        "scope_changed",
        "identity.scope",
        before_ctx.fact.identity.scope,
        after_ctx.fact.identity.scope,
        added_direction="broadened",
        removed_direction="narrowed",
        added_rationale="Capability scope expanded.",
        removed_rationale="Capability scope narrowed.",
    )
    _compare_sets(
        changes,
        "resource_changed",
        "identity.resource",
        before_ctx.fact.identity.resource,
        after_ctx.fact.identity.resource,
        added_direction="broadened",
        removed_direction="narrowed",
        added_rationale="Capability resource reach expanded.",
        removed_rationale="Capability resource reach narrowed.",
    )
    _compare_sets(
        changes,
        "authority_scope_changed",
        "authority.scopes",
        before_ctx.fact.authority.scopes,
        after_ctx.fact.authority.scopes,
        added_direction="broadened",
        removed_direction="narrowed",
        added_rationale="Authority scope expanded.",
        removed_rationale="Authority scope narrowed.",
    )
    _compare_sets(
        changes,
        "broad_scope_changed",
        "authority.broad_scopes",
        before_ctx.fact.authority.broad_scopes,
        after_ctx.fact.authority.broad_scopes,
        added_direction="broadened",
        removed_direction="narrowed",
        added_rationale="Broad authority scope added.",
        removed_rationale="Broad authority scope removed.",
    )
    _compare_rank(
        changes,
        "effect_changed",
        "effect.effect",
        before_ctx.fact.effect.effect,
        after_ctx.fact.effect.effect,
        ACTION_EFFECT_RANK,
        up_rationale="Capability effect escalated.",
        down_rationale="Capability effect reduced.",
    )
    for field in _EFFECT_BOOL_FIELDS:
        _compare_bool(
            changes,
            "effect_flag_changed",
            f"effect.{field}",
            getattr(before_ctx.fact.effect, field),
            getattr(after_ctx.fact.effect, field),
            false_to_true="broadened",
            true_to_false="narrowed",
            false_to_true_rationale=f"Capability effect flag {field} was enabled.",
            true_to_false_rationale=f"Capability effect flag {field} was disabled.",
        )
    _compare_rank(
        changes,
        "reversibility_changed",
        "effect.reversibility",
        before_ctx.fact.effect.reversibility,
        after_ctx.fact.effect.reversibility,
        _REVERSIBILITY_RANK,
        up_rationale="Capability reversibility became less certain.",
        down_rationale="Capability reversibility improved.",
    )
    _compare_bool(
        changes,
        "idempotency_changed",
        "effect.idempotency_known",
        before_ctx.fact.effect.idempotency_known,
        after_ctx.fact.effect.idempotency_known,
        false_to_true="narrowed",
        true_to_false="broadened",
        false_to_true_rationale="Capability idempotency evidence was added.",
        true_to_false_rationale="Capability idempotency evidence was removed.",
    )
    _compare_sets(
        changes,
        "risk_changed",
        "risk_tags",
        before_ctx.fact.risk_tags,
        after_ctx.fact.risk_tags,
        added_direction="broadened",
        removed_direction="narrowed",
        added_rationale="Capability risk tag added.",
        removed_rationale="Capability risk tag removed.",
    )
    for field in ("auth_type", "credential_mode", "source"):
        _compare_unknown(
            changes,
            "authority_changed",
            f"authority.{field}",
            getattr(before_ctx.fact.authority, field),
            getattr(after_ctx.fact.authority, field),
            "Capability authority metadata changed.",
        )
    for field in _CONTROL_FIELDS:
        _compare_bool(
            changes,
            "control_changed",
            f"controls.{field}",
            getattr(before_ctx.fact.controls, field),
            getattr(after_ctx.fact.controls, field),
            false_to_true="narrowed",
            true_to_false="broadened",
            false_to_true_rationale=f"Capability control {field} was added.",
            true_to_false_rationale=f"Capability control {field} was removed.",
        )
    for field in _CONTROL_METADATA_FIELDS:
        _compare_unknown(
            changes,
            "control_metadata_changed",
            f"controls.{field}",
            getattr(before_ctx.fact.controls, field),
            getattr(after_ctx.fact.controls, field),
            "Capability control evidence metadata changed.",
        )
    _compare_schema(changes, before_ctx, after_ctx)

    known_fields = {change.field for _, change in changes}
    for name in changed_hashes:
        if name == "evidence_hash":
            continue
        if _hash_name_explained(name, known_fields):
            continue
        changes.append(
            (
                "unknown",
                CapabilitySemanticChange(
                    kind="hash_changed",
                    field=name,
                    before=getattr(before_ctx.fact.hashes, name),
                    after=getattr(after_ctx.fact.hashes, name),
                    rationale=f"Capability {name} changed without a proven direction.",
                ),
            )
        )

    semantic_changes = tuple(
        change
        for _, change in sorted(
            changes, key=lambda item: capability_semantic_change_sort_key(item[1])
        )
    )
    return _semantic_direction(direction for direction, _ in changes), semantic_changes


def changed_capability_hashes(
    before: CapabilityFactV1,
    after: CapabilityFactV1,
) -> tuple[CapabilityHashName, ...]:
    return tuple(
        name
        for name in ALL_CAPABILITY_HASH_FIELDS
        if getattr(before.hashes, name) != getattr(after.hashes, name)
    )


def capability_delta_row_sort_key(row: CapabilityDeltaRow) -> tuple[str, str, str]:
    return (row.after.identity.tool_name, row.after.identity.operation, row.id)


def _contexts(
    facts: list[CapabilityFactContext] | list[CapabilityFactV1],
) -> list[CapabilityFactContext]:
    return [_context(fact) for fact in facts]


def _context(value: CapabilityFactContext | CapabilityFactV1) -> CapabilityFactContext:
    if isinstance(value, CapabilityFactContext):
        return value
    return CapabilityFactContext(fact=value)


def _reidentified_changes(
    removed: list[CapabilityFactContext],
    added: list[CapabilityFactContext],
) -> tuple[
    list[CapabilityDeltaRow],
    list[CapabilityFactContext],
    list[CapabilityFactContext],
]:
    added_by_key: dict[tuple[str, str, str, str, str, str], list[CapabilityFactContext]] = {}
    for ctx in added:
        added_by_key.setdefault(_lineage_key(ctx.fact), []).append(ctx)
    for contexts in added_by_key.values():
        contexts.sort(key=lambda ctx: capability_fact_sort_key(ctx.fact))

    reidentified: list[CapabilityDeltaRow] = []
    remaining_removed: list[CapabilityFactContext] = []
    consumed_added_ids: set[str] = set()

    for before in removed:
        candidates = added_by_key.get(_lineage_key(before.fact), [])
        after = next(
            (ctx for ctx in candidates if ctx.fact.id not in consumed_added_ids),
            None,
        )
        if after is None:
            remaining_removed.append(before)
            continue
        consumed_added_ids.add(after.fact.id)
        reidentified.append(
            build_capability_delta_row(
                before,
                after,
                row_id=f"{before.fact.id}->{after.fact.id}",
            )
        )

    remaining_added = [ctx for ctx in added if ctx.fact.id not in consumed_added_ids]
    return (
        sorted(reidentified, key=capability_delta_row_sort_key),
        _sort_contexts(remaining_added),
        _sort_contexts(remaining_removed),
    )


def _lineage_key(fact: CapabilityFactV1) -> tuple[str, str, str, str, str, str]:
    identity = fact.identity
    return (
        identity.agent_id,
        identity.tool_id,
        identity.provider,
        identity.operation,
        identity.tool_name,
        identity.subject_kind,
    )


def _sort_contexts(contexts: list[CapabilityFactContext]) -> list[CapabilityFactContext]:
    return sorted(contexts, key=lambda ctx: capability_fact_sort_key(ctx.fact))


def _compare_sets(
    changes: list[tuple[CapabilitySemanticDirection, CapabilitySemanticChange]],
    kind: str,
    field: str,
    before: tuple[str, ...],
    after: tuple[str, ...],
    *,
    added_direction: CapabilitySemanticDirection,
    removed_direction: CapabilitySemanticDirection,
    added_rationale: str,
    removed_rationale: str,
) -> None:
    before_set = set(before)
    after_set = set(after)
    added = tuple(sorted(after_set - before_set))
    removed = tuple(sorted(before_set - after_set))
    if added:
        changes.append(
            (
                added_direction,
                CapabilitySemanticChange(
                    kind=kind,
                    field=field,
                    before=tuple(sorted(before_set)),
                    after=tuple(sorted(after_set)),
                    rationale=added_rationale,
                ),
            )
        )
    if removed:
        changes.append(
            (
                removed_direction,
                CapabilitySemanticChange(
                    kind=kind,
                    field=field,
                    before=tuple(sorted(before_set)),
                    after=tuple(sorted(after_set)),
                    rationale=removed_rationale,
                ),
            )
        )


def _compare_rank(
    changes: list[tuple[CapabilitySemanticDirection, CapabilitySemanticChange]],
    kind: str,
    field: str,
    before: str,
    after: str,
    ranks: dict[str, int],
    *,
    up_rationale: str,
    down_rationale: str,
) -> None:
    if before == after:
        return
    before_rank = ranks.get(before)
    after_rank = ranks.get(after)
    if before_rank is None or after_rank is None:
        direction: CapabilitySemanticDirection = "unknown"
        rationale = f"{field} changed without a known rank."
    elif after_rank > before_rank:
        direction = "broadened"
        rationale = up_rationale
    elif after_rank < before_rank:
        direction = "narrowed"
        rationale = down_rationale
    else:
        direction = "unknown"
        rationale = f"{field} changed without a directional rank change."
    changes.append(
        (
            direction,
            CapabilitySemanticChange(
                kind=kind,
                field=field,
                before=before,
                after=after,
                rationale=rationale,
            ),
        )
    )


def _compare_bool(
    changes: list[tuple[CapabilitySemanticDirection, CapabilitySemanticChange]],
    kind: str,
    field: str,
    before: bool | None,
    after: bool | None,
    *,
    false_to_true: CapabilitySemanticDirection,
    true_to_false: CapabilitySemanticDirection,
    false_to_true_rationale: str,
    true_to_false_rationale: str,
) -> None:
    if before == after:
        return
    if before is True and after is not True:
        direction = true_to_false
        rationale = true_to_false_rationale
    elif before is not True and after is True:
        direction = false_to_true
        rationale = false_to_true_rationale
    else:
        direction = "unknown"
        rationale = f"{field} changed without a proven boolean direction."
    changes.append(
        (
            direction,
            CapabilitySemanticChange(
                kind=kind,
                field=field,
                before=before,
                after=after,
                rationale=rationale,
            ),
        )
    )


def _compare_unknown(
    changes: list[tuple[CapabilitySemanticDirection, CapabilitySemanticChange]],
    kind: str,
    field: str,
    before: Any,
    after: Any,
    rationale: str,
) -> None:
    if before == after:
        return
    changes.append(
        (
            "unknown",
            CapabilitySemanticChange(
                kind=kind,
                field=field,
                before=before,
                after=after,
                rationale=rationale,
            ),
        )
    )


def _compare_schema(
    changes: list[tuple[CapabilitySemanticDirection, CapabilitySemanticChange]],
    before: CapabilityFactContext,
    after: CapabilityFactContext,
) -> None:
    if not (
        before.input_fields
        or after.input_fields
        or before.required_input_fields
        or after.required_input_fields
    ):
        return
    before_fields = set(before.input_fields)
    after_fields = set(after.input_fields)
    added_fields = tuple(sorted(after_fields - before_fields))
    removed_fields = tuple(sorted(before_fields - after_fields))
    if added_fields:
        changes.append(
            (
                "broadened",
                CapabilitySemanticChange(
                    kind="schema_input_added",
                    field="schema.input_fields",
                    before=tuple(sorted(before_fields)),
                    after=tuple(sorted(after_fields)),
                    rationale="Capability input schema accepts additional fields.",
                ),
            )
        )
    if removed_fields:
        changes.append(
            (
                "narrowed",
                CapabilitySemanticChange(
                    kind="schema_input_removed",
                    field="schema.input_fields",
                    before=tuple(sorted(before_fields)),
                    after=tuple(sorted(after_fields)),
                    rationale="Capability input schema accepts fewer fields.",
                ),
            )
        )

    before_required = set(before.required_input_fields)
    after_required = set(after.required_input_fields)
    added_required = tuple(sorted(after_required - before_required))
    removed_required = tuple(sorted(before_required - after_required))
    if added_required:
        changes.append(
            (
                "narrowed",
                CapabilitySemanticChange(
                    kind="schema_required_input_added",
                    field="schema.required_input_fields",
                    before=tuple(sorted(before_required)),
                    after=tuple(sorted(after_required)),
                    rationale="Capability input schema requires additional fields.",
                ),
            )
        )
    if removed_required:
        changes.append(
            (
                "broadened",
                CapabilitySemanticChange(
                    kind="schema_required_input_removed",
                    field="schema.required_input_fields",
                    before=tuple(sorted(before_required)),
                    after=tuple(sorted(after_required)),
                    rationale="Capability input schema requires fewer fields.",
                ),
            )
        )


def _semantic_direction(
    directions: Any,
) -> CapabilitySemanticDirection:
    values = set(directions)
    values.discard("evidence_only")
    if not values:
        return "unknown"
    if values == {"broadened"}:
        return "broadened"
    if values == {"narrowed"}:
        return "narrowed"
    if values == {"unknown"}:
        return "unknown"
    return "mixed"


def _hash_name_explained(name: CapabilityHashName, fields: set[str]) -> bool:
    prefixes = {
        "identity_hash": ("identity.",),
        "binding_hash": ("semantic_assessment.binding.",),
        "effect_hash": ("effect.",),
        "authority_hash": ("authority.",),
        "control_hash": ("controls.",),
        "schema_hash": ("schema.",),
        "risk_hash": ("risk_tags",),
    }
    return any(field.startswith(prefix) for prefix in prefixes.get(name, ()) for field in fields)


def _compact_source(fact: CapabilityFactV1) -> dict[str, Any]:
    return {
        "source_type": fact.evidence.source_type,
        "source_id": fact.evidence.source_id,
        "source_ref": fact.evidence.source_ref,
        "source_path": fact.evidence.source_path,
        "source_pointer": fact.evidence.source_pointer,
    }


__all__ = [
    "ALL_CAPABILITY_HASH_FIELDS",
    "SEMANTIC_CAPABILITY_HASH_FIELDS",
    "CapabilityDeltaRow",
    "CapabilityFactContext",
    "CapabilitySetDiff",
    "build_capability_delta_row",
    "capability_delta_row_sort_key",
    "changed_capability_hashes",
    "classify_capability_delta",
    "diff_capability_fact_sets",
]
