"""Deterministic capability-change projection (v0.22).

Answers "what changed about agent capability?" for an AI coding agent or
reviewer reading a PR, by rolling up the existing ``action_surface_diff``
and ``tool_surface_diff`` rows into a flat ``list[CapabilityChange]``.

Sibling of ``reviewer_summary.py`` / ``agent_summary.py``: a pure,
I/O-free projection of already-computed report state. The key contract is
**one decision engine** — ``release_impact`` is a *read* of
``release_decision`` (and ``Finding.blocks_release``); this projection
never introduces a finding-independent blocker, and never mutates a
``Finding`` or an ``ActionSurfaceChange`` (their ``evidence`` /
``model_dump`` feed fingerprints, which must stay byte-stable).

Only Tier-A change types (the ten surface-diff-derived categories) are
emitted today. The five Tier-B values (``ci_gate_modified`` etc.) are
part of the closed enum but are backed by trust-root weakening checks
that do not exist yet, so the projection never produces them.
"""

from __future__ import annotations

import hashlib
from typing import Any

from agents_shipgate.core.risk_hints import CANONICAL_RISK_TAG_MAP
from agents_shipgate.schemas.report import (
    CapabilityChange,
    CapabilityReleaseImpact,
    Finding,
    ReadinessReport,
    ReleaseDecision,
)
from agents_shipgate.schemas.surfaces import (
    ActionSurfaceChange,
    ActionSurfaceDiff,
    ToolSurfaceDiff,
)

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

# ActionSurfaceChangeType (SCREAMING) -> (capability change_type, subject_kind).
# ACTION_ADDED / ACTION_REMOVED are handled from the added/removed buckets;
# everything else in the modified bucket folds to ``action_modified`` except
# APPROVAL_REMOVED, which is its own §7.1 category.
_ACTION_MODIFIED_MAP: dict[str, tuple[str, str]] = {
    "APPROVAL_REMOVED": ("approval_policy_removed", "policy"),
}
_TOOL_KIND_MAP: dict[str, tuple[str, str]] = {
    "added": ("tool_added", "tool"),
    "removed": ("tool_removed", "tool"),
    "changed": ("tool_modified", "tool"),
}
_SCOPE_KIND_MAP: dict[str, tuple[str, str]] = {
    "added": ("scope_added", "scope"),
    "removed": ("scope_removed", "scope"),
    "changed": ("scope_modified", "scope"),
}


def _canon_tags(tags: Any) -> list[str]:
    """Canonicalize + de-dupe + sort a tag iterable for byte-stable output."""
    if not isinstance(tags, (list, set, tuple)):
        return []
    return sorted(
        {
            CANONICAL_RISK_TAG_MAP.get(tag, tag)
            for tag in tags
            if isinstance(tag, str) and tag
        }
    )


def _risk_tags_from_summary(value: Any) -> list[str]:
    """Pull canonical ``risk_tags`` out of an action before/after summary dict."""
    if isinstance(value, dict):
        return _canon_tags(value.get("risk_tags") or [])
    return []


def _cap_id(change_type: str, subject_kind: str, subject: str, disc: str) -> str:
    """Deterministic, collision-resistant id for a capability change row."""
    raw = f"{change_type}|{subject_kind}|{subject}|{disc}"
    return "cap_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _decision_keys(
    release_decision: ReleaseDecision | None,
) -> tuple[set[str], set[str]]:
    """Return (blocker_keys, review_keys): fingerprints/ids the gate routed.

    Pure read of ``release_decision``. ``release_impact`` is derived only
    from these keys (plus ``Finding.blocks_release``), so the projection
    can never disagree with the gate.
    """
    blocker: set[str] = set()
    review: set[str] = set()
    if release_decision is None:
        return blocker, review
    for item in release_decision.blockers:
        blocker.update(k for k in (item.fingerprint, item.id) if k)
    for item in release_decision.review_items:
        review.update(k for k in (item.fingerprint, item.id) if k)
    for rule in release_decision.contribution_rules:
        keys = {k for k in (rule.fingerprint, rule.finding_id) if k}
        if rule.category == "blocker":
            blocker.update(keys)
        elif rule.category == "review_item":
            review.update(keys)
    return blocker, review


def _index_findings_by_action(findings: list[Finding]) -> dict[str, list[Finding]]:
    index: dict[str, list[Finding]] = {}
    for finding in findings:
        if finding.suppressed:
            continue
        evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
        action_id = evidence.get("action_id")
        if isinstance(action_id, str) and action_id:
            index.setdefault(action_id, []).append(finding)
    return index


def _index_findings_by_tool(findings: list[Finding]) -> dict[str, list[Finding]]:
    index: dict[str, list[Finding]] = {}
    for finding in findings:
        if finding.suppressed:
            continue
        if finding.tool_name:
            index.setdefault(finding.tool_name, []).append(finding)
    return index


def _release_impact(
    related: list[Finding],
    *,
    severity: str,
    has_risk_tags: bool,
    decision: str | None,
    blocker_keys: set[str],
    review_keys: set[str],
) -> CapabilityReleaseImpact:
    """Map a change to its release impact — a pure read of the gate.

    Precedence: blocks_release (a related finding blocks, or the gate
    named it a blocker) > review_required (the gate routed it to review)
    > insufficient_evidence (only when the whole decision is that) >
    informational (a real change with signal but no gate impact) > none.
    """
    for finding in related:
        if finding.blocks_release:
            return "blocks_release"
        if finding.fingerprint in blocker_keys or (finding.id or "") in blocker_keys:
            return "blocks_release"
    for finding in related:
        if finding.fingerprint in review_keys or (finding.id or "") in review_keys:
            return "review_required"
    if decision == "insufficient_evidence":
        return "insufficient_evidence"
    if related or has_risk_tags or severity not in {"info", "low"}:
        return "informational"
    return "none"


def _provenance_confidence(
    related: list[Finding], *, fallback_severity: str
) -> tuple[str, str]:
    """provenance_kind + confidence for a change: from the highest-severity
    related finding, else a deterministic default keyed on row severity."""
    if related:
        top = max(related, key=lambda f: _SEVERITY_RANK.get(f.severity, 0))
        return (top.provenance_kind or "static_declaration"), top.confidence
    confidence = "high" if fallback_severity in {"critical", "high"} else "medium"
    return "static_declaration", confidence


def _action_change(
    change: ActionSurfaceChange,
    change_type: str,
    subject_kind: str,
    risk_tags: list[str],
    *,
    by_action: dict[str, list[Finding]],
    decision: str | None,
    blocker_keys: set[str],
    review_keys: set[str],
) -> CapabilityChange:
    related = by_action.get(change.action_id, [])
    subject = change.tool_name or change.action_id
    impact = _release_impact(
        related,
        severity=change.severity,
        has_risk_tags=bool(risk_tags),
        decision=decision,
        blocker_keys=blocker_keys,
        review_keys=review_keys,
    )
    provenance, confidence = _provenance_confidence(
        related, fallback_severity=change.severity
    )
    return CapabilityChange(
        id=_cap_id(change_type, subject_kind, subject, change.action_id),
        change_type=change_type,  # type: ignore[arg-type]
        subject_kind=subject_kind,  # type: ignore[arg-type]
        subject=subject,
        risk_tags=risk_tags,
        source_path=change.source_path,
        source_start_line=change.source_start_line,
        provenance_kind=provenance,
        confidence=confidence,  # type: ignore[arg-type]
        release_impact=impact,
        rationale=change.reason,
        related_finding_ids=sorted({f.id for f in related if f.id}),
    )


def _action_rows(
    diff: ActionSurfaceDiff,
    *,
    by_action: dict[str, list[Finding]],
    decision: str | None,
    blocker_keys: set[str],
    review_keys: set[str],
) -> list[CapabilityChange]:
    rows: list[CapabilityChange] = []
    common = {
        "by_action": by_action,
        "decision": decision,
        "blocker_keys": blocker_keys,
        "review_keys": review_keys,
    }
    for change in diff.added:
        rows.append(
            _action_change(
                change,
                "action_added",
                "action",
                _risk_tags_from_summary(change.after),
                **common,
            )
        )
    for change in diff.removed:
        rows.append(
            _action_change(
                change,
                "action_removed",
                "action",
                _risk_tags_from_summary(change.before),
                **common,
            )
        )
    for change in diff.modified:
        change_type, subject_kind = _ACTION_MODIFIED_MAP.get(
            change.type, ("action_modified", "action")
        )
        if change.type == "RISK_TAG_ADDED":
            risk_tags = _canon_tags(change.added)
        else:
            risk_tags = _risk_tags_from_summary(change.after) or _risk_tags_from_summary(
                change.before
            )
        if change.type == "APPROVAL_REMOVED":
            risk_tags = sorted({*risk_tags, "approval_missing"})
        rows.append(
            _action_change(change, change_type, subject_kind, risk_tags, **common)
        )
    return rows


def _tool_rows(
    diff: ToolSurfaceDiff,
    *,
    by_tool: dict[str, list[Finding]],
    decision: str | None,
    blocker_keys: set[str],
    review_keys: set[str],
) -> list[CapabilityChange]:
    rows: list[CapabilityChange] = []
    added_tags_by_tool: dict[str, set[str]] = {}
    for effect in diff.high_risk_effects:
        if effect.kind == "added":
            added_tags_by_tool.setdefault(effect.tool, set()).add(effect.tag)

    for change in diff.tools:
        change_type, subject_kind = _TOOL_KIND_MAP[change.kind]
        related = by_tool.get(change.name, [])
        risk_tags = _canon_tags(added_tags_by_tool.get(change.name, set()))
        impact = _release_impact(
            related,
            severity="info",
            has_risk_tags=bool(risk_tags),
            decision=decision,
            blocker_keys=blocker_keys,
            review_keys=review_keys,
        )
        provenance, confidence = _provenance_confidence(
            related, fallback_severity="high" if risk_tags else "info"
        )
        rows.append(
            CapabilityChange(
                id=_cap_id(change_type, subject_kind, change.name, change.kind),
                change_type=change_type,  # type: ignore[arg-type]
                subject_kind=subject_kind,  # type: ignore[arg-type]
                subject=change.name,
                risk_tags=risk_tags,
                source_path=change.source_path,
                source_start_line=change.source_start_line,
                provenance_kind=provenance,
                confidence=confidence,  # type: ignore[arg-type]
                release_impact=impact,
                rationale=f"Tool `{change.name}` {change.kind} in the agent tool surface.",
                related_finding_ids=sorted({f.id for f in related if f.id}),
            )
        )

    for change in diff.scopes:
        change_type, subject_kind = _SCOPE_KIND_MAP[change.kind]
        risk_tags = ["broad_scope"] if change.broad else []
        related = [
            finding
            for tool_name in change.tool_names
            for finding in by_tool.get(tool_name, [])
        ]
        impact = _release_impact(
            related,
            severity="info",
            has_risk_tags=bool(risk_tags),
            decision=decision,
            blocker_keys=blocker_keys,
            review_keys=review_keys,
        )
        provenance, confidence = _provenance_confidence(
            related, fallback_severity="info"
        )
        broad = " (broad)" if change.broad else ""
        rows.append(
            CapabilityChange(
                id=_cap_id(change_type, subject_kind, change.scope, change.kind),
                change_type=change_type,  # type: ignore[arg-type]
                subject_kind=subject_kind,  # type: ignore[arg-type]
                subject=change.scope,
                risk_tags=risk_tags,
                provenance_kind=provenance,
                confidence=confidence,  # type: ignore[arg-type]
                release_impact=impact,
                rationale=f"Scope `{change.scope}` {change.kind}{broad}.",
                related_finding_ids=sorted({f.id for f in related if f.id}),
            )
        )
    return rows


def build_capability_changes(
    *,
    report: ReadinessReport,
    findings: list[Finding],
) -> list[CapabilityChange]:
    """Project ``report``'s surface diffs into a flat capability-change list.

    Returns ``[]`` when neither surface diff is enabled (no base to diff
    against, e.g. a plain ``scan`` with no baseline) — capability
    *changes* require a base/head comparison. ``findings`` is passed
    separately (already filtered/annotated by the caller) to match the
    ``build_reviewer_summary`` pattern.
    """
    decision = (
        report.release_decision.decision if report.release_decision else None
    )
    blocker_keys, review_keys = _decision_keys(report.release_decision)
    by_action = _index_findings_by_action(findings)
    by_tool = _index_findings_by_tool(findings)

    rows: list[CapabilityChange] = []
    action_diff = report.action_surface_diff
    if action_diff is not None and action_diff.enabled:
        rows.extend(
            _action_rows(
                action_diff,
                by_action=by_action,
                decision=decision,
                blocker_keys=blocker_keys,
                review_keys=review_keys,
            )
        )
    tool_diff = report.tool_surface_diff
    if tool_diff is not None and tool_diff.enabled:
        rows.extend(
            _tool_rows(
                tool_diff,
                by_tool=by_tool,
                decision=decision,
                blocker_keys=blocker_keys,
                review_keys=review_keys,
            )
        )

    # Byte-stable order for the same input report.
    rows.sort(key=lambda c: (c.subject, c.change_type, c.id))
    return rows


# Priority order for "top capability changes" (PR comment / verifier rollup),
# per docs/engineering/ai-coding-workflow-verifier.md: blocked impacts first,
# then policy/trust-root, money movement, production mutation/deletion,
# external communication, sensitive data, then everything else.
_IMPACT_RANK = {
    "blocks_release": 0,
    "insufficient_evidence": 1,
    "review_required": 2,
    "informational": 3,
    "none": 4,
}
_RISK_RANK = [
    "policy_weakened",
    "trust_root_touched",
    "financial_write",
    "production_ops",
    "destructive",
    "irreversible",
    "external_communication",
    "privileged_data",
    "secret_access",
]


def _risk_priority(change: CapabilityChange) -> int:
    for rank, tag in enumerate(_RISK_RANK):
        if tag in change.risk_tags:
            return rank
    return len(_RISK_RANK)


def top_capability_changes(
    changes: list[CapabilityChange], *, limit: int = 5
) -> list[CapabilityChange]:
    """Return the ``limit`` highest-priority changes for a summary surface.

    Deterministic: sorts by release impact, then risk severity, then the
    stable (subject, change_type) order. Does not mutate ``changes``.
    """
    ranked = sorted(
        changes,
        key=lambda c: (
            _IMPACT_RANK.get(c.release_impact, 99),
            _risk_priority(c),
            c.subject,
            c.change_type,
        ),
    )
    return ranked[:limit]


__all__ = ["build_capability_changes", "top_capability_changes"]
