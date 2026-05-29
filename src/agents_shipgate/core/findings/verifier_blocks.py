"""Builders for the v0.22 verifier report blocks.

These populate the five additive top-level blocks introduced for the AI
coding workflow verifier (docs/engineering/ai-coding-workflow-verifier.md
§7, §8):

- ``capability_change`` (CapabilityChangeBlock) — the diff-derived
  capability delta, grouped into added / removed / broadened / narrowed
  members. A reviewer-facing projection over the already-computed
  ``action_surface_diff`` / ``tool_surface_diff``; it never gates on its
  own (Principle 1/2: legibility layer, one decision engine).
- ``protected_surface_changes`` (list[ProtectedSurfaceChange]) — touched
  trust roots (filled by the P3 classifier).
- ``effective_policy`` (EffectivePolicy) — normalized policy snapshot
  (filled by the P2 builder).
- ``human_ack`` (HumanAck) — declared human-acknowledgement state (P4).
- ``verifier_summary`` (VerifierSummary) — byte-stable composition alias
  (P5). ``verdict`` always mirrors ``release_decision.decision``.

Determinism is a hard requirement: every list is sorted by a fully
specified key (the model validators in ``schemas/capability_change.py``
re-sort on construction) and there is no wall-clock / set-iteration
leakage into output. None of these builders introduces a release gate —
``release_decision.decision`` remains the only gate.
"""

from __future__ import annotations

import hashlib

from agents_shipgate.schemas.capability_change import (
    CapabilityChangeBlock,
    CapabilityChangeMember,
    CapabilityReleaseImpact,
    HumanAck,
    HumanAckEntry,
    ProtectedSurfaceChange,
    VerifierCapabilityDeltaSummary,
    VerifierReasonCodeCount,
    VerifierSummary,
)
from agents_shipgate.schemas.report import Finding, ReadinessReport

# ---------------------------------------------------------------------------
# Shared deterministic helpers
# ---------------------------------------------------------------------------


def _member_id(direction: str, subject_kind: str, tool: str, action: str, scope: str) -> str:
    """Stable content-addressed id for a capability member.

    Hashing the identity tuple keeps the id byte-stable across runs (no
    wall-clock, no ordering dependence) and mirrors the hashed-id pattern
    used elsewhere in the report (capability_facts, misalignments).
    """
    raw = "|".join((direction, subject_kind, tool, action, scope))
    return "cap_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _active_findings_by_tool(report: ReadinessReport) -> dict[str, list[Finding]]:
    """Map ``tool_name`` -> active (non-suppressed) findings on that tool.

    Used to cross-reference each capability member with the findings that
    fired on the same tool, so a reviewer can pivot capability -> evidence
    and so ``release_impact`` can reflect a real blocking finding.
    """
    out: dict[str, list[Finding]] = {}
    for finding in report.findings:
        if finding.suppressed or not finding.tool_name:
            continue
        out.setdefault(finding.tool_name, []).append(finding)
    return out


# ---------------------------------------------------------------------------
# P1 — CapabilityChange diff-derived members
# ---------------------------------------------------------------------------
#
# The capability delta is a reviewer-facing projection over the
# already-computed ``action_surface_diff`` and ``tool_surface_diff`` (both
# base-vs-head). Each surface change is classified into one of four
# capability directions:
#
#   added     — a tool/action that did not exist on the base now exists.
#   removed   — a tool/action present on the base is gone.
#   broadened — an existing member can now do MORE: wider scope, escalated
#               effect, a new high-risk effect, or a REMOVED control
#               (fewer guardrails == more effective capability).
#   narrowed  — an existing member can now do LESS: a removed scope/effect,
#               or an ADDED control (more guardrails == less capability).
#
# Ambiguous-direction changes (an opaque tool "changed", policy drift) are
# recorded as ``broadened`` so they surface for review rather than being
# silently dropped — this block never gates, so erring toward visibility
# is safe.


class _MemberFactory:
    """Builds CapabilityChangeMembers with consistent cross-references."""

    def __init__(self, report: ReadinessReport) -> None:
        self._by_tool = _active_findings_by_tool(report)

    def make(
        self,
        direction: str,
        subject_kind: str,
        tool: str | None,
        *,
        action: str | None = None,
        scope: str | None = None,
        before_scope: str | None = None,
        after_scope: str | None = None,
        risk_tags: list[str] | None = None,
        confidence: str = "high",
        rationale: str = "",
    ) -> CapabilityChangeMember:
        tool = tool or ""
        related = self._by_tool.get(tool, [])
        related_ids = sorted(
            {f.id or f.fingerprint for f in related if (f.id or f.fingerprint)}
        )
        impact = _release_impact(direction, related)
        return CapabilityChangeMember(
            id=_member_id(direction, subject_kind, tool, action or "", scope or ""),
            direction=direction,  # type: ignore[arg-type]
            subject_kind=subject_kind,  # type: ignore[arg-type]
            tool=tool,
            action=action,
            scope=scope,
            before_scope=before_scope,
            after_scope=after_scope,
            risk_tags=sorted(risk_tags or []),
            release_impact=impact,
            provenance_kind="static_declaration",
            confidence=confidence,  # type: ignore[arg-type]
            rationale=rationale,
            related_finding_ids=related_ids,
        )


def _release_impact(direction: str, related: list[Finding]) -> CapabilityReleaseImpact:
    """Descriptive (never gating) release impact for a capability member.

    A member tied to an actual blocking finding is labelled
    ``blocks_release`` so the readability layer agrees with the gate;
    additive/broadening directions default to ``review_required`` (more
    capability warrants a look); removals/narrowing are informational.
    """
    if any(f.blocks_release for f in related):
        return "blocks_release"
    if direction in ("added", "broadened"):
        return "review_required"
    return "informational"


def _dedup_members(
    members: list[CapabilityChangeMember],
) -> list[CapabilityChangeMember]:
    """Dedup by member id, merging related finding ids. Order-independent.

    The block model's validator re-sorts the result, so we only need to
    collapse duplicates here (e.g. the same scope reported on the same
    tool by two facts).
    """
    by_id: dict[str, CapabilityChangeMember] = {}
    for member in members:
        existing = by_id.get(member.id)
        if existing is None:
            by_id[member.id] = member
            continue
        existing.related_finding_ids = sorted(
            set(existing.related_finding_ids) | set(member.related_finding_ids)
        )
    return list(by_id.values())


def build_capability_change(report: ReadinessReport) -> CapabilityChangeBlock:
    """Project the capability delta from the report's surface diffs.

    Returns a deterministic disabled/empty block when neither surface
    diff is enabled (e.g. a non-diff ``scan`` with no base): there is no
    delta to show, so the block mirrors the surfaces it projects from.
    """
    asd = report.action_surface_diff
    tsd = report.tool_surface_diff
    action_enabled = bool(getattr(asd, "enabled", False))
    tool_enabled = bool(getattr(tsd, "enabled", False))
    if not (action_enabled or tool_enabled):
        return CapabilityChangeBlock(enabled=False)

    factory = _MemberFactory(report)
    added: list[CapabilityChangeMember] = []
    removed: list[CapabilityChangeMember] = []
    broadened: list[CapabilityChangeMember] = []
    narrowed: list[CapabilityChangeMember] = []

    # --- action surface ------------------------------------------------
    if action_enabled:
        for ch in asd.added:
            added.append(
                factory.make(
                    "added",
                    "action",
                    ch.tool_name,
                    action=ch.action_id,
                    rationale=ch.reason or "action added",
                )
            )
        for ch in asd.removed:
            removed.append(
                factory.make(
                    "removed",
                    "action",
                    ch.tool_name,
                    action=ch.action_id,
                    rationale=ch.reason or "action removed",
                )
            )
        for ch in asd.modified:
            # Every ActionSurfaceChangeType modification on the modified
            # list (SCOPE_EXPANDED, EFFECT_ESCALATED, RISK_TAG_ADDED,
            # APPROVAL_REMOVED, SAFEGUARD_REMOVED, INPUT_SCHEMA_EXPANDED)
            # widens effective capability.
            broadened.append(
                factory.make(
                    "broadened",
                    "action",
                    ch.tool_name,
                    action=ch.action_id,
                    risk_tags=list(ch.added),
                    rationale=ch.reason or ch.type,
                )
            )

    # --- tool surface --------------------------------------------------
    if tool_enabled:
        for t in tsd.tools:
            if t.kind == "added":
                added.append(
                    factory.make("added", "tool", t.name, rationale="tool added")
                )
            elif t.kind == "removed":
                removed.append(
                    factory.make("removed", "tool", t.name, rationale="tool removed")
                )
            else:  # "changed" — direction unknown; surface for review.
                broadened.append(
                    factory.make(
                        "broadened",
                        "tool",
                        t.name,
                        confidence="medium",
                        rationale="tool definition changed",
                    )
                )
        for s in tsd.scopes:
            tool_names = sorted(s.tool_names) or [""]
            for tool in tool_names:
                if s.kind == "removed":
                    narrowed.append(
                        factory.make(
                            "narrowed",
                            "scope",
                            tool,
                            scope=s.scope,
                            rationale="scope removed",
                        )
                    )
                else:  # "added" / "changed" — more reach.
                    broadened.append(
                        factory.make(
                            "broadened",
                            "scope",
                            tool,
                            scope=s.scope,
                            confidence="high" if s.kind == "added" else "medium",
                            rationale=f"scope {s.kind}",
                        )
                    )
        for c in tsd.controls:
            # A control PRESENT after the change == more safety == narrower
            # capability; a removed control == fewer guardrails == broader.
            if c.kind == "added":
                narrowed.append(
                    factory.make(
                        "narrowed",
                        "policy",
                        c.tool,
                        action=c.control,
                        rationale=f"control {c.control} added",
                    )
                )
            elif c.kind == "removed":
                broadened.append(
                    factory.make(
                        "broadened",
                        "policy",
                        c.tool,
                        action=c.control,
                        rationale=f"control {c.control} removed",
                    )
                )
            else:
                broadened.append(
                    factory.make(
                        "broadened",
                        "policy",
                        c.tool,
                        action=c.control,
                        confidence="medium",
                        rationale=f"control {c.control} changed",
                    )
                )
        for e in tsd.high_risk_effects:
            if e.kind == "removed":
                narrowed.append(
                    factory.make(
                        "narrowed",
                        "action",
                        e.tool,
                        action=e.tag,
                        rationale=f"high-risk effect {e.tag} removed",
                    )
                )
            else:
                broadened.append(
                    factory.make(
                        "broadened",
                        "action",
                        e.tool,
                        action=e.tag,
                        risk_tags=[e.tag],
                        rationale=f"high-risk effect {e.tag} {e.kind}",
                    )
                )
        for d in tsd.policy_drift:
            # Policy drift on a tool requirement — direction is opaque from
            # the change-detection hashes, so surface it as broadened.
            broadened.append(
                factory.make(
                    "broadened",
                    "policy",
                    "",
                    scope=f"{d.policy_kind}:{d.key}",
                    confidence="medium",
                    rationale="policy drift",
                )
            )

    return CapabilityChangeBlock(
        enabled=True,
        added=_dedup_members(added),
        removed=_dedup_members(removed),
        broadened=_dedup_members(broadened),
        narrowed=_dedup_members(narrowed),
    )


# ---------------------------------------------------------------------------
# P3 — protected_surface_changes rollup
# ---------------------------------------------------------------------------
#
# A reviewer-facing rollup of *which protected trust-root paths* this PR
# affected, derived entirely from the active SHIP-VERIFY-* findings so it
# can never disagree with the gate. Each path-bearing verify finding
# contributes one or more rows keyed by (path, kind); rows are deduped and
# the related findings merged, so a single file touched by both the Tier A
# "touched" check and a Tier B "weakened" check appears once with both
# finding ids attached.
#
# Pure-semantic weakenings (e.g. a fail-on severity dropped, a CI mode
# downgrade) have no specific changed file, so they intentionally produce
# no path row — they remain in findings[] and surface via verifier_summary.
# This keeps the block honest: a row means "a protected file was touched",
# not "something somewhere weakened".

# How to extract path rows from each verify finding's evidence. ``key`` is
# the evidence field holding the changed path(s); ``single`` marks a
# scalar path vs a list; ``kind`` is the protected-surface bucket. The
# trust-root-touched finding is special-cased (it carries its own
# per-file class + glob) and handled separately below.
_VERIFY_PATH_ROWS: dict[str, tuple[str, str, bool]] = {
    # check_id: (evidence_key, surface_kind, is_single)
    "SHIP-VERIFY-CI-GATE-REMOVED": ("removed_workflow_files", "ci_gate", False),
    "SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED": (
        "changed_instruction_files",
        "agent_instructions",
        False,
    ),
    "SHIP-VERIFY-TRIGGER-CATALOG-DRIFT": (
        "changed_trigger_files",
        "trigger_catalog",
        False,
    ),
    # Fail-safe POLICY-WEAKENED finding (no base snapshot) lists the
    # changed policy files; the semantic POLICY-WEAKENED kinds carry no
    # path and are skipped (no ``changed_policy_files`` key).
    "SHIP-VERIFY-POLICY-WEAKENED": ("changed_policy_files", "policy", False),
}

_TRUST_ROOT_TOUCHED = "SHIP-VERIFY-TRUST-ROOT-TOUCHED"


def build_protected_surface_changes(
    report: ReadinessReport,
) -> list[ProtectedSurfaceChange]:
    """Roll up the path-bearing verify findings into protected-surface rows.

    Deterministic: rows are deduped by (kind, path), related finding ids
    are merged and sorted, and the list is sorted by (kind, path). Derived
    from ``report.findings`` so it cannot contradict the release gate.
    """
    # (kind, path) -> {"glob": str|None, "fids": set[str]}
    rows: dict[tuple[str, str], dict] = {}

    def _add(kind: str, path: str, *, glob: str | None, fid: str | None) -> None:
        path = (path or "").replace("\\", "/").strip()
        if not path:
            return
        slot = rows.setdefault((kind, path), {"glob": None, "fids": set()})
        if glob and not slot["glob"]:
            slot["glob"] = glob
        if fid:
            slot["fids"].add(fid)

    for finding in report.findings:
        if finding.suppressed or finding.category != "verify":
            continue
        fid = finding.id or finding.fingerprint
        evidence = finding.evidence or {}
        if finding.check_id == _TRUST_ROOT_TOUCHED:
            _add(
                str(evidence.get("trust_root_class", "unknown")),
                str(evidence.get("changed_file", "")),
                glob=evidence.get("matched_glob"),
                fid=fid,
            )
            continue
        spec = _VERIFY_PATH_ROWS.get(finding.check_id)
        if spec is None:
            continue
        key, kind, is_single = spec
        value = evidence.get(key)
        if value is None:
            continue
        paths = [value] if is_single else list(value)
        for path in paths:
            _add(kind, str(path), glob=None, fid=fid)

    return [
        ProtectedSurfaceChange(
            path=path,
            kind=kind,
            glob=slot["glob"],
            related_finding_ids=sorted(slot["fids"]),
        )
        for (kind, path), slot in sorted(rows.items())
    ]


# ---------------------------------------------------------------------------
# P4 — human acknowledgement (§5.4 / §7.2)
# ---------------------------------------------------------------------------
#
# Which trust-root weakening checks require *declared human authority* to
# pass, and the canonical surface key each maps to. A coding agent cannot
# synthesize this authority (Principle 4): the only way to satisfy a
# requirement is a matching ``human_ack`` declaration in shipgate.yaml,
# which is itself a trust-root edit. Findings not in this map (e.g. the
# medium agent-instructions / trigger-drift reviews) are surfaced for
# review but do not *require* a formal acknowledgement.
_ACK_REQUIRING_CHECKS: dict[str, str] = {
    "SHIP-VERIFY-POLICY-WEAKENED": "policy",
    "SHIP-VERIFY-CI-GATE-REMOVED": "ci_gate",
    "SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED": "baseline_or_waiver",
}


def build_human_ack(report: ReadinessReport, manifest=None) -> HumanAck:
    """Compute declared human-acknowledgement state for the report.

    A surface is *required* when an active (non-suppressed) trust-root
    weakening finding in ``_ACK_REQUIRING_CHECKS`` fired for it. A required
    surface is *satisfied* only when the manifest declares a matching
    ``human_ack`` entry (matched by ``affected_surface`` == the surface key
    OR a concrete changed-file path in the finding's evidence). Acks are
    never inferred — absent a declaration, the surface stays outstanding.

    Determinism: ``required`` is derived from findings, ``satisfied`` is a
    pure set operation, and every list is sorted by the model validator.
    Expiry is recorded verbatim on the ack entry but NOT evaluated against
    wall-clock here (that belongs to the clock-bearing verify
    orchestration; the report builder must stay reproducible).
    """
    required_surfaces: set[str] = set()
    # Concrete paths each required surface touched, for path-level ack match.
    surface_paths: dict[str, set[str]] = {}
    for finding in report.findings:
        if finding.suppressed or finding.category != "verify":
            continue
        surface = _ACK_REQUIRING_CHECKS.get(finding.check_id)
        if surface is None:
            continue
        required_surfaces.add(surface)
        evidence = finding.evidence or {}
        for key in (
            "changed_policy_files",
            "removed_workflow_files",
            "added_check_ids",
            "added_scopes",
            "added_fingerprints",
        ):
            value = evidence.get(key)
            if isinstance(value, list):
                surface_paths.setdefault(surface, set()).update(
                    str(v) for v in value
                )

    declarations = list(manifest.human_ack) if manifest is not None else []
    acks: list[HumanAckEntry] = [
        HumanAckEntry(
            owner=d.owner,
            reason=d.reason,
            affected_surface=d.affected_surface,
            expires=d.expires,
            source="shipgate.yaml#/human_ack",
        )
        for d in declarations
    ]
    declared_surfaces = {d.affected_surface for d in declarations}

    def _is_satisfied(surface: str) -> bool:
        if surface in declared_surfaces:
            return True
        # Path-level acknowledgement: a declaration naming a concrete file
        # the weakening touched also satisfies the surface.
        return bool(surface_paths.get(surface, set()) & declared_surfaces)

    outstanding = sorted(s for s in required_surfaces if not _is_satisfied(s))
    return HumanAck(
        required=bool(required_surfaces),
        satisfied=not outstanding,
        acks=acks,
        outstanding=outstanding,
    )


# ---------------------------------------------------------------------------
# P5 — verifier_summary composition (§7.3 / §8)
# ---------------------------------------------------------------------------
#
# A byte-stable, one-fetch projection that COMPOSES the already-built
# blocks — it derives no independent verdict (Principle 2: one decision
# engine). ``verdict`` mirrors ``release_decision.decision`` exactly; the
# counts and flags are read straight from findings[] and the sibling
# verifier blocks, so the §7.4 cross-block invariants hold by construction.

# Severity rank for ranking top_reason_codes (higher == more significant).
_SUMMARY_SEVERITY_RANK: dict[str, int] = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}
# Maximum reason codes surfaced in the ranked highlight list. The complete
# per-code map is always in ``by_reason_code`` regardless of this cap
# (Decision: top 5, ranked severity desc -> count desc -> code asc).
_TOP_REASON_CODES_LIMIT = 5


def _active_findings(report: ReadinessReport) -> list[Finding]:
    return [f for f in report.findings if not f.suppressed]


def _top_reason_codes(
    active: list[Finding],
) -> list[VerifierReasonCodeCount]:
    """Rank fired reason codes: severity desc, count desc, code asc; top 5.

    Severity per code is its MAX active severity (the code's worst
    instance), so a code that ever fired at critical outranks a
    high-count-but-low-severity code. Deterministic and byte-stable.
    """
    counts: dict[str, int] = {}
    max_rank: dict[str, int] = {}
    for finding in active:
        code = finding.check_id
        counts[code] = counts.get(code, 0) + 1
        rank = _SUMMARY_SEVERITY_RANK.get(finding.severity, -1)
        if rank > max_rank.get(code, -1):
            max_rank[code] = rank
    ranked = sorted(
        counts.items(),
        key=lambda kv: (-max_rank.get(kv[0], -1), -kv[1], kv[0]),
    )
    return [
        VerifierReasonCodeCount(reason_code=code, count=count)
        for code, count in ranked[:_TOP_REASON_CODES_LIMIT]
    ]


def build_verifier_summary(report: ReadinessReport) -> VerifierSummary:
    """Compose the verifier summary from the report's other blocks.

    Composition only (Principle 2): ``verdict`` mirrors
    ``release_decision.decision`` and every count/flag is read from
    findings[] or a sibling verifier block, so this block can never
    introduce a finding-independent blocker. ``by_reason_code`` is the
    complete per-code map; ``top_reason_codes`` is the ranked top-5
    highlight (severity desc, count desc, code asc).
    """
    verdict = (
        report.release_decision.decision if report.release_decision else "passed"
    )
    active = _active_findings(report)

    by_severity: dict[str, int] = {}
    by_reason_code: dict[str, int] = {}
    for finding in active:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        by_reason_code[finding.check_id] = (
            by_reason_code.get(finding.check_id, 0) + 1
        )

    cap = report.capability_change
    if cap is not None:
        delta = VerifierCapabilityDeltaSummary(
            added=len(cap.added),
            removed=len(cap.removed),
            broadened=len(cap.broadened),
            narrowed=len(cap.narrowed),
        )
    else:
        delta = VerifierCapabilityDeltaSummary()

    human_ack = report.human_ack
    human_ack_required = bool(human_ack.required) if human_ack else False
    human_ack_satisfied = bool(human_ack.satisfied) if human_ack else True

    policy_weakened = any(
        f.check_id == "SHIP-VERIFY-POLICY-WEAKENED" for f in active
    )

    return VerifierSummary(
        verdict=verdict,
        # Sorted dicts for byte-stable JSON regardless of finding order.
        by_severity=dict(sorted(by_severity.items())),
        by_reason_code=dict(sorted(by_reason_code.items())),
        capability_delta_summary=delta,
        protected_surface_touched=bool(report.protected_surface_changes),
        policy_weakened=policy_weakened,
        human_ack_required=human_ack_required,
        human_ack_satisfied=human_ack_satisfied,
        top_reason_codes=_top_reason_codes(active),
    )
