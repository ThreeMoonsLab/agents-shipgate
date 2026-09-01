#!/usr/bin/env python3
"""Standalone verifier for an Agents Shipgate capability-delta attestation.

Reads `capability-delta-attestation.json` — an in-toto Statement whose
predicate carries the frozen `shipgate.capability_payload/v1` delta — checks
every rule the format promises, and prints what the agent can do after the
change. **Stdlib only, one file, no `agents-shipgate` install.** That is the
point: the format is only an interchange format if consuming it requires
nothing of ours at runtime.

Usage::

    python3 tools/verify-capability-delta.py capability-delta-attestation.json
    python3 tools/verify-capability-delta.py att.json --expect-tree <sha> --require-receipt-binding
    curl -sSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/tools/verify-capability-delta.py \\
        | python3 - att.json --json

Exit codes: ``0`` valid, ``1`` invalid (every failed rule is listed), ``2``
usage or unreadable input.

What it checks
--------------
Everything in `docs/capability-payload.md` § *Validating a payload: two stages*
that applies to the delta view, plus the envelope rules in
`docs/capability-delta-attestation.md`. Each rule has an id, and the ids are
published on that page so a reader can tell what a passing run established:

Envelope — ``E1``..``E9``; payload — ``P1``..``P11``. See ``CHECKS`` below for
the one-line statement of each; the page and this table are pinned together by
``tests/test_capability_delta_attestation.py``.

What it does **not** check
--------------------------
Two things, both deliberate and both stated rather than implied:

* **Authenticity.** A ``v1`` statement is unsigned. Everything here is
  *self-consistency*: it proves the file is internally coherent and describes
  the tree it names, not that Agents Shipgate produced it. Wrap the bytes in a
  DSSE envelope, or trust the transport that handed you the file.
* **The full JSON Schema (stage one).** JSON Schema validation needs a
  validator this script will not depend on. Pass ``--schema
  docs/capability-delta-attestation-schema.v1.json`` and, if the ``jsonschema``
  package is importable, stage one runs too; otherwise the run says it was
  skipped. The structural checks this script needs for its own rules always
  run, so a malformed document fails here regardless.

``base``/``head`` state digests are taken on trust, as the payload spec says:
they describe two full state payloads this delta does not carry. The
``analysis_coverage_digest`` of each side *is* recomputed, because the delta
carries the coverage it is taken over.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "1.0.0"

IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://threemoonslab.com/agents-shipgate/capability-delta/v1"
PREDICATE_SCHEMA_VERSION = "shipgate.capability_delta_attestation/v1"
PAYLOAD_SCHEMA_VERSION = "shipgate.capability_payload/v1"

SUBJECT_KEY_PREFIX = "capsubj_"
SUBJECT_KEY_DIGEST_CHARS = 16

#: One line per rule, in the order they are applied. Published so a passing run
#: means something specific, and pinned against the docs page by the test suite.
CHECKS: tuple[tuple[str, str], ...] = (
    ("E1", "_type is the in-toto Statement type"),
    ("E2", "predicateType is the capability-delta predicate type"),
    ("E3", "exactly one subject, with a name and a gitTree digest"),
    ("E4", "the predicate and payload schema versions are the published ones"),
    ("E5", "the carried payload is the delta view"),
    ("E6", "base.ref and head.ref are git object ids"),
    ("E7", "the attested subject's gitTree is delta.head.ref"),
    ("E8", "two refs naming one tree carry an empty delta"),
    ("E9", "verification.status matches the identities it carries"),
    ("P1", "every subject.key is the published derivation of its own identity"),
    ("P2", "subject.key and capability_id are unique across the payload"),
    ("P3", "transition follows from the presence pair, and presence bounds changes"),
    ("P4", "summary is the rollup of the subject rows"),
    ("P5", "changed_dimensions are exactly the per-dimension digests that differ"),
    ("P6", "semantic_direction and semantic_changes are what the two records show"),
    ("P7", "coverage status, directional lists and naming follow from the two sides"),
    ("P8", "each side's analysis_coverage_digest describes the coverage carried"),
    ("P9", "the two state refs reconcile with the membership rows"),
    ("P10", "an empty delta names two states whose capability digests agree"),
    ("P11", "rows and lists are in the published sort order"),
)

# --------------------------------------------------------------------------
# Canonicalization. Stated in full in docs/capability-payload.md § Canonical
# bytes; this is that specification, implemented.
# --------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def subject_key(agent: str, provider: str, tool_id: str) -> str:
    body = digest({"agent": agent, "provider": provider, "tool_id": tool_id})
    return f"{SUBJECT_KEY_PREFIX}{body[:SUBJECT_KEY_DIGEST_CHARS]}"


def is_git_object_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) in (40, 64)
        and all(char in "0123456789abcdef" for char in value)
    )


def is_content_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(char in "0123456789abcdef" for char in value[7:])
    )


# --------------------------------------------------------------------------
# The published semantic model. These tables are the payload spec's, restated
# here because an independent implementation is the whole point; they are
# pinned against the package by tests/test_capability_delta_attestation.py.
# --------------------------------------------------------------------------

PERMISSION_CLASS_RANK = {
    "read": 0,
    "write": 1,
    "external": 2,
    "financial": 3,
    "production": 3,
    "destructive": 4,
    "unknown": 5,
}
ACTION_EFFECT_RANK = {
    "read": 0,
    "privileged_data_access": 1,
    "write": 2,
    "external_communication": 3,
    "financial_write": 4,
    "production_operation": 4,
    "identity_access": 4,
    "code_execution": 4,
    "destructive": 5,
}
REVERSIBILITY_RANK = {"reversible": 0, "unknown": 1, "irreversible": 2}

SET_DIMENSIONS = (
    ("scope_changed", "scope", "scope", "Capability scope"),
    ("resource_changed", "resource", "resource", "Capability resource reach"),
    (
        "authority_scope_changed",
        "authority.scopes",
        "authority.scopes",
        "Authority scope",
    ),
    (
        "broad_scope_changed",
        "broad_scope",
        "authority.broad_scopes",
        "Broad authority scope",
    ),
    ("risk_tags_changed", "risk_tags", "risk_tags", "Risk tags"),
)
WIDENING_FLAGS = (
    ("effect.externally_visible", "Externally visible"),
    ("effect.handles_sensitive_data", "Handles sensitive data"),
    ("effect.financial", "Financial"),
    ("effect.code_execution", "Code execution"),
    ("effect.high_risk", "High risk"),
)
CONTROL_FLAGS = (
    ("controls.approval_required", "Approval requirement"),
    ("controls.confirmation_required", "Confirmation requirement"),
    ("controls.safeguard_idempotency", "Idempotency safeguard"),
    ("controls.safeguard_audit_log", "Audit-log safeguard"),
    ("controls.safeguard_rollback", "Rollback safeguard"),
    ("controls.safeguard_dry_run", "Dry-run safeguard"),
)
OPAQUE_DIMENSIONS = (
    ("authority_identity_changed", "authority.auth_type", "Authority type"),
    ("authority_identity_changed", "authority.credential_mode", "Credential mode"),
    ("authority_identity_changed", "authority.source", "Authority source"),
    ("control_metadata_changed", "controls.approval_threshold", "Approval threshold"),
    ("control_metadata_changed", "controls.evidence_owner", "Evidence owner"),
    ("control_metadata_changed", "controls.evidence_runbook", "Evidence runbook"),
    ("control_metadata_changed", "controls.evidence_approval_ticket", "Approval ticket"),
    ("operation_changed", "operation", "Operation"),
    ("operation_changed", "subject_kind", "Subject kind"),
)
DIGEST_DIMENSIONS = (
    "authority_hash",
    "binding_hash",
    "control_hash",
    "effect_hash",
    "evidence_hash",
    "identity_hash",
    "risk_hash",
    "schema_hash",
)


def dotted(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def direction_of(widened: bool, narrowed: bool) -> str:
    if widened and narrowed:
        return "mixed"
    if widened:
        return "broadened"
    if narrowed:
        return "narrowed"
    return "unknown"


def set_verb(direction: str) -> str:
    return {
        "broadened": "expanded",
        "narrowed": "narrowed",
        "mixed": "both expanded and narrowed",
    }.get(direction, "changed")


def rank_verb(direction: str) -> str:
    return {"broadened": "escalated", "narrowed": "reduced"}.get(direction, "changed")


def semantic_projection(record: dict[str, Any]) -> dict[str, Any]:
    """One published record with its provenance removed.

    The one definition of "the semantic content of a record": the ``evidence``
    block goes, and so does ``digests.evidence_hash``. ``evidence_only`` means
    exactly that two records are equal under this projection, and nothing else
    may be called that.
    """

    trimmed = {key: value for key, value in record.items() if key != "evidence"}
    digests = trimmed.get("digests")
    if isinstance(digests, dict):
        trimmed["digests"] = {
            name: value for name, value in digests.items() if name != "evidence_hash"
        }
    return trimmed


def permission_direction(before: dict[str, Any], after: dict[str, Any]) -> str:
    if before.get("status") != after.get("status"):
        return "unknown"
    before_classes = set(before.get("classes") or ())
    after_classes = set(after.get("classes") or ())
    before_unknown = bool(before.get("side_effect_unknown"))
    after_unknown = bool(after.get("side_effect_unknown"))
    return direction_of(
        bool(after_classes - before_classes) or (after_unknown and not before_unknown),
        bool(before_classes - after_classes) or (before_unknown and not after_unknown),
    )


def semantic_shift(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    """Derive `(direction, changes)` from the two published records."""

    if semantic_projection(before) == semantic_projection(after):
        return "evidence_only", []

    changes: list[dict[str, Any]] = []
    widened = False
    narrowed = False

    def record_change(
        kind: str,
        field: str,
        direction: str,
        before_value: Any,
        after_value: Any,
        rationale: str,
    ) -> None:
        nonlocal widened, narrowed
        widened = widened or direction in {"broadened", "mixed"}
        narrowed = narrowed or direction in {"narrowed", "mixed"}
        changes.append(
            {
                "kind": kind,
                "field": field,
                "direction": direction,
                "before": before_value,
                "after": after_value,
                "rationale": rationale,
            }
        )

    for kind, field, source, label in SET_DIMENSIONS:
        old_values = list(dotted(before, source) or [])
        new_values = list(dotted(after, source) or [])
        if old_values == new_values:
            continue
        direction = direction_of(
            bool(set(new_values) - set(old_values)),
            bool(set(old_values) - set(new_values)),
        )
        record_change(
            kind, field, direction, old_values, new_values, f"{label} {set_verb(direction)}."
        )

    old_effect = dotted(before, "effect.effect")
    new_effect = dotted(after, "effect.effect")
    if old_effect != new_effect:
        old_rank = ACTION_EFFECT_RANK[old_effect]
        new_rank = ACTION_EFFECT_RANK[new_effect]
        direction = direction_of(new_rank > old_rank, new_rank < old_rank)
        record_change(
            "effect_changed",
            "effect.effect",
            direction,
            old_effect,
            new_effect,
            f"Capability effect {rank_verb(direction)}.",
        )

    old_rev = dotted(before, "effect.reversibility")
    new_rev = dotted(after, "effect.reversibility")
    if old_rev != new_rev:
        old_rank = REVERSIBILITY_RANK[old_rev]
        new_rank = REVERSIBILITY_RANK[new_rev]
        direction = direction_of(new_rank > old_rank, new_rank < old_rank)
        record_change(
            "reversibility_changed",
            "effect.reversibility",
            direction,
            old_rev,
            new_rev,
            f"Reversibility {rank_verb(direction)}.",
        )

    for field, label in WIDENING_FLAGS:
        old_value = dotted(before, field)
        new_value = dotted(after, field)
        if old_value == new_value:
            continue
        record_change(
            "effect_flag_changed",
            field,
            direction_of(bool(new_value), bool(old_value)),
            old_value,
            new_value,
            f"{label} {'set' if new_value else 'cleared'}.",
        )

    old_idem = dotted(before, "effect.idempotency_known")
    new_idem = dotted(after, "effect.idempotency_known")
    if old_idem != new_idem:
        record_change(
            "idempotency_evidence_changed",
            "effect.idempotency_known",
            direction_of(not new_idem, bool(new_idem)),
            old_idem,
            new_idem,
            f"Idempotency evidence {'lost' if not new_idem else 'gained'}.",
        )

    for field, label in CONTROL_FLAGS:
        old_value = dotted(before, field)
        new_value = dotted(after, field)
        if old_value == new_value:
            continue
        record_change(
            "control_changed",
            field,
            direction_of(old_value is True, new_value is True),
            old_value,
            new_value,
            f"{label} {'proven' if new_value is True else 'no longer proven'}.",
        )

    if before.get("permission") != after.get("permission"):
        before_permission = before.get("permission") or {}
        after_permission = after.get("permission") or {}
        record_change(
            "permission_changed",
            "permission",
            permission_direction(before_permission, after_permission),
            list(before_permission.get("classes") or ()),
            list(after_permission.get("classes") or ()),
            "Published permission profile changed.",
        )

    for kind, field, label in OPAQUE_DIMENSIONS:
        old_value = dotted(before, field)
        new_value = dotted(after, field)
        if old_value == new_value:
            continue
        record_change(kind, field, "unknown", old_value, new_value, f"{label} changed.")

    changes.sort(key=lambda change: (change["kind"], change["field"]))
    return direction_of(widened, narrowed), changes


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


class Problems:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []

    def add(self, rule: str, message: str) -> None:
        self.rows.append((rule, message))

    def __bool__(self) -> bool:
        return bool(self.rows)


def _require_mapping(value: Any, label: str, problems: Problems, rule: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        problems.add(rule, f"{label} must be an object, got {type(value).__name__}")
        return {}
    return value


def _require_list(value: Any, label: str, problems: Problems, rule: str) -> list[Any]:
    if not isinstance(value, list):
        problems.add(rule, f"{label} must be an array, got {type(value).__name__}")
        return []
    return value


def subject_sort_key(subject: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(subject.get("agent", "")),
        str(subject.get("provider", "")),
        str(subject.get("name", "")),
        str(subject.get("key", "")),
    )


def record_sort_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("subject_kind", "")),
        str(record.get("operation", "")),
        str(record.get("capability_id", "")),
    )


def entry_sort_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    current = entry.get("after") or entry.get("before") or {}
    return record_sort_key(current if isinstance(current, dict) else {})


def check_subject_ref(
    ref: Any, label: str, problems: Problems, *, rule: str = "P1"
) -> str | None:
    subject = _require_mapping(ref, label, problems, rule)
    if not subject:
        return None
    key = subject.get("key")
    expected = subject_key(
        str(subject.get("agent", "")),
        str(subject.get("provider", "")),
        str(subject.get("tool_id", "")),
    )
    if key != expected:
        problems.add(
            rule,
            f"{label}.key {key!r} is not the derivation of its own "
            f"agent/provider/tool_id (expected {expected!r})",
        )
        return None
    return str(key)


def check_coverage_side(
    coverage: Any, label: str, problems: Problems
) -> tuple[str, list[dict[str, Any]]]:
    side = _require_mapping(coverage, label, problems, "P7")
    status = side.get("status")
    if status not in {"not_requested", "unavailable", "complete"}:
        problems.add("P7", f"{label}.status {status!r} is not a published status")
        status = "unavailable"
    named = _require_list(
        side.get("subjects_outside_analysis"),
        f"{label}.subjects_outside_analysis",
        problems,
        "P7",
    )
    if status != "complete" and named:
        problems.add(
            "P7",
            f"{label}.status is {status!r} and still names {len(named)} subject(s): "
            "naming them requires having looked",
        )
    seen: set[str] = set()
    for index, entry in enumerate(named):
        key = check_subject_ref(entry, f"{label}.subjects_outside_analysis[{index}]", problems)
        if key is None:
            continue
        if key in seen:
            problems.add("P7", f"{label} names subject {key} twice")
        seen.add(key)
    ordered = [subject_sort_key(entry) for entry in named if isinstance(entry, dict)]
    if ordered != sorted(ordered):
        problems.add("P11", f"{label}.subjects_outside_analysis is not in sorted order")
    return str(status), [entry for entry in named if isinstance(entry, dict)]


def coverage_delta_status(base: str, head: str) -> str:
    if base == "complete" and head == "complete":
        return "complete"
    if "unavailable" in (base, head):
        return "unavailable"
    return "not_requested"


def verify(document: Any) -> Problems:
    """Every rule in ``CHECKS``, applied to one parsed attestation."""

    problems = Problems()
    statement = _require_mapping(document, "the attestation", problems, "E1")
    if not statement:
        return problems

    if statement.get("_type") != IN_TOTO_STATEMENT_TYPE:
        problems.add("E1", f"_type must be {IN_TOTO_STATEMENT_TYPE!r}")
    if statement.get("predicateType") != PREDICATE_TYPE:
        problems.add("E2", f"predicateType must be {PREDICATE_TYPE!r}")

    subjects = _require_list(statement.get("subject"), "subject", problems, "E3")
    subject_tree: str | None = None
    if len(subjects) != 1:
        problems.add(
            "E3",
            f"a capability-delta attestation names exactly one subject, got {len(subjects)}",
        )
    else:
        attested = _require_mapping(subjects[0], "subject[0]", problems, "E3")
        if not str(attested.get("name") or "").strip():
            problems.add("E3", "subject[0].name must not be blank")
        digests = _require_mapping(attested.get("digest"), "subject[0].digest", problems, "E3")
        subject_tree = digests.get("gitTree")
        if not is_git_object_id(subject_tree):
            problems.add("E3", f"subject[0].digest.gitTree {subject_tree!r} is not a git object id")
            subject_tree = None
        commit = digests.get("gitCommit")
        if commit is not None and not is_git_object_id(commit):
            problems.add("E3", f"subject[0].digest.gitCommit {commit!r} is not a git object id")

    predicate = _require_mapping(statement.get("predicate"), "predicate", problems, "E4")
    if predicate.get("predicate_schema_version") != PREDICATE_SCHEMA_VERSION:
        problems.add("E4", f"predicate_schema_version must be {PREDICATE_SCHEMA_VERSION!r}")
    if predicate.get("capability_payload_schema_version") != PAYLOAD_SCHEMA_VERSION:
        problems.add("E4", f"capability_payload_schema_version must be {PAYLOAD_SCHEMA_VERSION!r}")

    verification = _require_mapping(
        predicate.get("verification"), "predicate.verification", problems, "E9"
    )
    status = verification.get("status")
    identities = (verification.get("input_set_id"), verification.get("subject_id"))
    if status == "bound":
        for name, value in zip(("input_set_id", "subject_id"), identities, strict=True):
            if not is_content_id(value):
                problems.add(
                    "E9",
                    f"verification.status is 'bound' but {name} is {value!r}: a "
                    "partial chain is one a consumer cannot follow",
                )
    elif status == "unbound":
        if any(value is not None for value in identities):
            problems.add("E9", "verification.status is 'unbound' but names identities")
    else:
        problems.add("E9", f"verification.status {status!r} is not 'bound' or 'unbound'")

    delta = _require_mapping(predicate.get("delta"), "predicate.delta", problems, "E5")
    if delta.get("view") != "delta":
        problems.add("E5", f"predicate.delta.view must be 'delta', got {delta.get('view')!r}")
    if delta.get("capability_payload_schema_version") != PAYLOAD_SCHEMA_VERSION:
        problems.add("E4", "predicate.delta declares a different payload schema version")

    base_ref_block = _require_mapping(delta.get("base"), "delta.base", problems, "E6")
    head_ref_block = _require_mapping(delta.get("head"), "delta.head", problems, "E6")
    base_ref = base_ref_block.get("ref")
    head_ref = head_ref_block.get("ref")
    for side, ref in (("base", base_ref), ("head", head_ref)):
        if not is_git_object_id(ref):
            problems.add("E6", f"delta.{side}.ref {ref!r} is not a git object id")

    if subject_tree is not None and head_ref != subject_tree:
        problems.add(
            "E7",
            "the attested subject is not the state the delta describes: subject "
            f"gitTree {subject_tree} vs delta.head.ref {head_ref}",
        )

    rows = _require_list(delta.get("subjects"), "delta.subjects", problems, "P2")
    if base_ref == head_ref and rows:
        problems.add(
            "E8",
            f"delta.base.ref and delta.head.ref name one tree, so the delta must "
            f"be empty; it carries {len(rows)} changed subject(s)",
        )

    _verify_rows(rows, problems)
    _verify_summary(delta, rows, problems)
    _verify_coverage(delta, base_ref_block, head_ref_block, problems)
    _verify_refs(base_ref_block, head_ref_block, rows, problems)
    return problems


def _verify_rows(rows: list[Any], problems: Problems) -> None:
    seen_subjects: set[str] = set()
    seen_capabilities: set[str] = set()
    ordered: list[tuple[str, str, str, str]] = []
    for index, row_value in enumerate(rows):
        row = _require_mapping(row_value, f"delta.subjects[{index}]", problems, "P2")
        if not row:
            continue
        subject = row.get("subject")
        key = check_subject_ref(subject, f"delta.subjects[{index}].subject", problems)
        if key is not None:
            if key in seen_subjects:
                problems.add("P2", f"subject {key} appears in more than one row")
            seen_subjects.add(key)
        if isinstance(subject, dict):
            ordered.append(subject_sort_key(subject))
        label = key or f"delta.subjects[{index}]"

        present_in_base = row.get("present_in_base")
        present_in_head = row.get("present_in_head")
        if not isinstance(present_in_base, bool) or not isinstance(present_in_head, bool):
            problems.add("P3", f"{label} presence flags must be booleans")
            continue
        if not (present_in_base or present_in_head):
            problems.add("P3", f"{label} is present on neither side: a row exists because a subject does")
            continue
        expected = (
            "added"
            if not present_in_base
            else "removed"
            if not present_in_head
            else "modified"
        )
        if row.get("transition") != expected:
            problems.add(
                "P3",
                f"{label} declares transition {row.get('transition')!r}, but its "
                f"presence pair makes it {expected!r}",
            )

        changes = _require_list(row.get("changes"), f"{label}.changes", problems, "P3")
        if not changes:
            problems.add("P3", f"{label} carries no changes: a delta row exists because something moved")
        change_order: list[tuple[str, str, str]] = []
        for change_index, change_value in enumerate(changes):
            entry = _require_mapping(
                change_value, f"{label}.changes[{change_index}]", problems, "P5"
            )
            if not entry:
                continue
            change_order.append(entry_sort_key(entry))
            _verify_change_entry(
                entry,
                f"{label}.changes[{change_index}]",
                present_in_base=present_in_base,
                present_in_head=present_in_head,
                seen_capabilities=seen_capabilities,
                problems=problems,
            )
        if change_order != sorted(change_order):
            problems.add("P11", f"{label}.changes is not in the published sort order")
    if ordered != sorted(ordered):
        problems.add("P11", "delta.subjects is not in the published sort order")


def _verify_change_entry(
    entry: dict[str, Any],
    label: str,
    *,
    present_in_base: bool,
    present_in_head: bool,
    seen_capabilities: set[str],
    problems: Problems,
) -> None:
    transition = entry.get("transition")
    if transition not in {"added", "removed", "changed", "reidentified"}:
        problems.add("P5", f"{label}.transition {transition!r} is not a published transition")
        return
    before = entry.get("before")
    after = entry.get("after")
    for side, value in (("before", before), ("after", after)):
        if value is not None and not isinstance(value, dict):
            problems.add("P5", f"{label}.{side} must be an object or null")
            return
    for capability_id in dict.fromkeys(
        side.get("capability_id")
        for side in (before, after)
        if isinstance(side, dict)
    ):
        if not isinstance(capability_id, str):
            problems.add("P2", f"{label} carries a record with no capability_id")
            continue
        if capability_id in seen_capabilities:
            problems.add("P2", f"capability_id {capability_id} appears more than once")
        seen_capabilities.add(capability_id)

    # Presence bounds what a row's changes may say.
    if not present_in_base and transition != "added":
        problems.add(
            "P3",
            f"{label} is a {transition!r} change on a subject base never had; only "
            "'added' is possible",
        )
    if not present_in_head and transition != "removed":
        problems.add(
            "P3",
            f"{label} is a {transition!r} change on a subject head does not have; "
            "only 'removed' is possible",
        )

    declared_dimensions = _require_list(
        entry.get("changed_dimensions"), f"{label}.changed_dimensions", problems, "P5"
    )
    declared_changes = _require_list(
        entry.get("semantic_changes"), f"{label}.semantic_changes", problems, "P6"
    )
    direction = entry.get("semantic_direction")

    if transition in {"added", "removed"}:
        # Both sides, not only the one the transition forbids. An `added` entry
        # with neither record still satisfied every remaining rule — no changed
        # dimensions, direction equal to the transition, no explanations — and
        # would have published a membership change naming no capability at all.
        expected_present = "after" if transition == "added" else "before"
        for side, value in (("before", before), ("after", after)):
            present = value is not None
            if present != (side == expected_present):
                problems.add(
                    "P5",
                    f"{label}.transition {transition!r} "
                    f"{'requires' if side == expected_present else 'forbids'} "
                    f"a {side!r} record",
                )
        if declared_dimensions:
            problems.add("P5", f"{label} is a membership change and cannot name dimensions")
        if direction != transition:
            problems.add(
                "P6",
                f"{label} is a membership change and must carry semantic_direction "
                f"{transition!r}, not {direction!r}",
            )
        if declared_changes:
            problems.add("P6", f"{label} has no second record and cannot carry semantic changes")
        return

    if not isinstance(before, dict) or not isinstance(after, dict):
        problems.add("P5", f"{label}.transition {transition!r} needs both records")
        return
    derived_dimensions = [
        name
        for name in DIGEST_DIMENSIONS
        if (before.get("digests") or {}).get(name) != (after.get("digests") or {}).get(name)
    ]
    if not derived_dimensions:
        problems.add("P5", f"{label} carries two identical records; that is not a delta row")
    if list(declared_dimensions) != derived_dimensions:
        problems.add(
            "P5",
            f"{label}.changed_dimensions must be exactly the digests that differ: "
            f"declared {list(declared_dimensions)}, records give {derived_dimensions}",
        )
    same_capability = before.get("capability_id") == after.get("capability_id")
    same_identity = (before.get("digests") or {}).get("identity_hash") == (
        after.get("digests") or {}
    ).get("identity_hash")
    if transition == "changed" and not (same_capability and same_identity):
        problems.add(
            "P5",
            f"{label} is 'changed' — one capability moving — so both sides need the "
            "same capability_id and identity_hash",
        )
    if transition == "reidentified" and (same_capability or same_identity):
        problems.add(
            "P5",
            f"{label} is 'reidentified' — an identity moving — so the two sides need "
            "different capability_id and identity_hash",
        )
    if direction in {"added", "removed"}:
        problems.add("P6", f"{label} carries both sides and cannot claim direction {direction!r}")
        return
    try:
        derived_direction, derived_changes = semantic_shift(before, after)
    except (KeyError, TypeError) as exc:  # a value outside the published vocabulary
        problems.add("P6", f"{label} carries a record this format cannot compare: {exc}")
        return
    if direction != derived_direction:
        problems.add(
            "P6",
            f"{label}.semantic_direction {direction!r} is not what the two published "
            f"records show ({derived_direction!r})",
        )
    if list(declared_changes) != derived_changes:
        problems.add(
            "P6",
            f"{label}.semantic_changes must be exactly the published dimensions that "
            f"moved: declared {[c.get('field') for c in declared_changes if isinstance(c, dict)]}, "
            f"records give {[c['field'] for c in derived_changes]}",
        )


def _verify_summary(delta: dict[str, Any], rows: list[Any], problems: Problems) -> None:
    summary = _require_mapping(delta.get("summary"), "delta.summary", problems, "P4")
    counted = {"added": 0, "removed": 0, "modified": 0}
    changes = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        transition = row.get("transition")
        if transition in counted:
            counted[transition] += 1
        row_changes = row.get("changes")
        changes += len(row_changes) if isinstance(row_changes, list) else 0
    expected = {
        "subjects": len([row for row in rows if isinstance(row, dict)]),
        "added_subjects": counted["added"],
        "removed_subjects": counted["removed"],
        "modified_subjects": counted["modified"],
        "capability_changes": changes,
    }
    for name, value in expected.items():
        if summary.get(name) != value:
            problems.add(
                "P4",
                f"delta.summary.{name} is {summary.get(name)!r}; the rows give {value}",
            )


def _verify_coverage(
    delta: dict[str, Any],
    base_ref_block: dict[str, Any],
    head_ref_block: dict[str, Any],
    problems: Problems,
) -> None:
    coverage = _require_mapping(delta.get("analysis_coverage"), "analysis_coverage", problems, "P7")
    base_status, base_named = check_coverage_side(
        coverage.get("base"), "analysis_coverage.base", problems
    )
    head_status, head_named = check_coverage_side(
        coverage.get("head"), "analysis_coverage.head", problems
    )
    expected_status = coverage_delta_status(base_status, head_status)
    if coverage.get("status") != expected_status:
        problems.add(
            "P7",
            f"analysis_coverage.status {coverage.get('status')!r} does not follow "
            f"from base {base_status!r} and head {head_status!r} "
            f"(expected {expected_status!r})",
        )
    if expected_status == "complete":
        base_keys = {entry.get("key") for entry in base_named}
        head_keys = {entry.get("key") for entry in head_named}
        expected_newly = sorted(
            (entry for entry in head_named if entry.get("key") not in base_keys),
            key=subject_sort_key,
        )
        expected_no_longer = sorted(
            (entry for entry in base_named if entry.get("key") not in head_keys),
            key=subject_sort_key,
        )
    else:
        expected_newly = []
        expected_no_longer = []
    for name, expected in (
        ("newly_outside_analysis", expected_newly),
        ("no_longer_outside_analysis", expected_no_longer),
    ):
        declared = _require_list(coverage.get(name), f"analysis_coverage.{name}", problems, "P7")
        if declared != expected:
            problems.add(
                "P7",
                f"analysis_coverage.{name} must follow from the two sides: declared "
                f"{[entry.get('name') for entry in declared if isinstance(entry, dict)]}, "
                f"the sides give {[entry.get('name') for entry in expected]}",
            )
    for side, block in (("base", base_ref_block), ("head", head_ref_block)):
        carried = coverage.get(side)
        if not isinstance(carried, dict):
            continue
        if block.get("analysis_coverage_digest") != digest(carried):
            problems.add(
                "P8",
                f"delta.{side}.analysis_coverage_digest does not describe the {side} "
                "coverage this delta carries",
            )


def _verify_refs(
    base_ref_block: dict[str, Any],
    head_ref_block: dict[str, Any],
    rows: list[Any],
    problems: Problems,
) -> None:
    added = removed = 0
    arrivals = departures = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("transition") == "added":
            added += 1
        elif row.get("transition") == "removed":
            removed += 1
        for entry in row.get("changes") or ():
            if not isinstance(entry, dict):
                continue
            if entry.get("transition") == "added":
                arrivals += 1
            elif entry.get("transition") == "removed":
                departures += 1
    for name, expected in (
        ("subject_count", added - removed),
        ("capability_count", arrivals - departures),
    ):
        base_value = base_ref_block.get(name)
        head_value = head_ref_block.get(name)
        if not isinstance(base_value, int) or not isinstance(head_value, int):
            problems.add("P9", f"delta.base/head.{name} must be integers")
            continue
        if head_value - base_value != expected:
            problems.add(
                "P9",
                f"head.{name} - base.{name} is {head_value - base_value}; the rows "
                f"give {expected}",
            )
    if not rows:
        for name in ("capability_set_digest", "evidence_set_digest"):
            if base_ref_block.get(name) != head_ref_block.get(name):
                problems.add(
                    "P10",
                    f"an empty delta claims two states are the same state, but "
                    f"{name} differs between them",
                )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _subject_label(subject: dict[str, Any]) -> str:
    name = subject.get("name") or "(unnamed)"
    provider = subject.get("provider") or "(no provider)"
    return f"{name} [{provider}]"


def render(document: dict[str, Any]) -> str:
    predicate = document.get("predicate") or {}
    delta = predicate.get("delta") or {}
    subject = (document.get("subject") or [{}])[0]
    digests = subject.get("digest") or {}
    verification = predicate.get("verification") or {}
    summary = delta.get("summary") or {}
    coverage = delta.get("analysis_coverage") or {}

    lines = [
        f"Capability delta attestation — {predicate.get('predicate_schema_version')}",
        f"  subject      {subject.get('name')}",
        f"  base tree    {(delta.get('base') or {}).get('ref')}",
        f"  head tree    {digests.get('gitTree')}",
        f"  head commit  {digests.get('gitCommit') or '(not published)'}",
        f"  receipt      {verification.get('status')}"
        + (
            f" · input_set_id {verification.get('input_set_id')}"
            if verification.get("status") == "bound"
            else ""
        ),
        "",
        "Analysed capability",
        f"  {summary.get('subjects', 0)} subject(s) changed "
        f"(+{summary.get('added_subjects', 0)} added, "
        f"~{summary.get('modified_subjects', 0)} modified, "
        f"-{summary.get('removed_subjects', 0)} removed) "
        f"over {summary.get('capability_changes', 0)} capability change(s)",
    ]
    marks = {"added": "+", "removed": "-", "modified": "~"}
    for row in delta.get("subjects") or []:
        mark = marks.get(row.get("transition"), "?")
        lines.append(f"  {mark} {_subject_label(row.get('subject') or {})} — {row.get('transition')}")
        for entry in row.get("changes") or []:
            record = entry.get("after") or entry.get("before") or {}
            operation = record.get("operation") or record.get("capability_id")
            lines.append(
                f"      {entry.get('transition')}: {operation} "
                f"({entry.get('semantic_direction')})"
            )
            for change in entry.get("semantic_changes") or []:
                lines.append(f"        · {change.get('rationale')} [{change.get('direction')}]")
    if not (delta.get("subjects") or []):
        lines.append("  (no analysed capability moved)")

    lines.extend(["", "Outside the analysed surface"])
    lines.append(
        f"  status {coverage.get('status')} "
        f"(base {(coverage.get('base') or {}).get('status')}, "
        f"head {(coverage.get('head') or {}).get('status')})"
    )
    if coverage.get("status") != "complete":
        lines.append(
            "  NOTE: this is not a claim that nothing was left out — the "
            "comparison was not established."
        )
    newly = coverage.get("newly_outside_analysis") or []
    lines.append(
        "  newly outside analysis: "
        + (", ".join(_subject_label(entry) for entry in newly) if newly else "none")
    )
    no_longer = coverage.get("no_longer_outside_analysis") or []
    lines.append(
        "  no longer outside analysis: "
        + (", ".join(_subject_label(entry) for entry in no_longer) if no_longer else "none")
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _run_stage_one(document: Any, schema_path: Path) -> tuple[str, list[str]]:
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError:
        return ("skipped", [])
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ("error", [f"could not read {schema_path}: {exc}"])
    validator = jsonschema.Draft202012Validator(schema)
    return (
        "ran",
        [error.message for error in sorted(validator.iter_errors(document), key=str)],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify-capability-delta",
        description=(
            "Validate an Agents Shipgate capability-delta attestation and print "
            "the delta it carries. Stdlib only."
        ),
    )
    parser.add_argument("path", help="Path to capability-delta-attestation.json, or - for stdin.")
    parser.add_argument(
        "--expect-tree",
        help="Reject the attestation unless it attests this git tree object id.",
    )
    parser.add_argument(
        "--expect-commit",
        help="Reject the attestation unless it names this git commit object id.",
    )
    parser.add_argument(
        "--require-receipt-binding",
        action="store_true",
        help="Reject an attestation not chained to a verification receipt.",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        help=(
            "Also run JSON Schema validation (stage one) against this file. "
            "Requires the jsonschema package; reported as skipped without it."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable result.")
    parser.add_argument("--version", action="version", version=SCRIPT_VERSION)
    args = parser.parse_args(argv)

    try:
        raw = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"verify-capability-delta: cannot read attestation: {exc}", file=sys.stderr)
        return 2

    problems = verify(document)

    stage_one = "not_requested"
    if args.schema is not None:
        stage_one, messages = _run_stage_one(document, args.schema)
        for message in messages:
            problems.add("stage-one", message)

    subject = (document.get("subject") or [{}])[0] if isinstance(document, dict) else {}
    digests = subject.get("digest") or {} if isinstance(subject, dict) else {}
    if args.expect_tree and digests.get("gitTree") != args.expect_tree:
        problems.add(
            "expect-tree",
            f"attested tree {digests.get('gitTree')!r} is not the expected "
            f"{args.expect_tree!r}",
        )
    if args.expect_commit and digests.get("gitCommit") != args.expect_commit:
        problems.add(
            "expect-commit",
            f"attested commit {digests.get('gitCommit')!r} is not the expected "
            f"{args.expect_commit!r}",
        )
    if args.require_receipt_binding:
        predicate = document.get("predicate") or {} if isinstance(document, dict) else {}
        verification = predicate.get("verification") or {}
        if verification.get("status") != "bound":
            problems.add(
                "require-receipt-binding",
                "this attestation is not chained to a verification receipt",
            )

    if args.json:
        print(
            json.dumps(
                {
                    "script_version": SCRIPT_VERSION,
                    "valid": not problems,
                    "stage_one": stage_one,
                    "checks": [rule for rule, _ in CHECKS],
                    "problems": [
                        {"rule": rule, "message": message} for rule, message in problems.rows
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1 if problems else 0

    if problems:
        print("INVALID — this attestation does not verify:", file=sys.stderr)
        for rule, message in problems.rows:
            print(f"  [{rule}] {message}", file=sys.stderr)
        return 1

    print(render(document))
    print()
    print(
        f"VALID — {len(CHECKS)} rules checked"
        + (
            ""
            if stage_one == "not_requested"
            else f"; JSON Schema stage one {stage_one}"
        )
        + ". Unsigned: this proves self-consistency, not authorship."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
