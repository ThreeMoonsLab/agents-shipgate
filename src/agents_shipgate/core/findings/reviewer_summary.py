from __future__ import annotations

from agents_shipgate.schemas.report import (
    Finding,
    PolicyAudit,
    PrivacyAudit,
    ReadinessReport,
    ReleaseDecision,
    ReviewerSummary,
    ReviewerSurfacePointer,
)

# --- v0.20: reviewer_summary projection -------------------------------------
#
# Parallels build_agent_summary. AgentSummary answers "what should an agent
# do next?"; ReviewerSummary answers "what should a reviewer look at first?".
# Both are deterministic projections of the same underlying scan state — no
# extra side effects, no I/O, no LLM calls.

# Baseline integrity findings are emitted under these three check IDs (M2).
# Kept in sync with checks/registry.py + STABILITY.md "Baseline Integrity".
_BASELINE_INTEGRITY_CHECK_IDS: frozenset[str] = frozenset(
    {
        "SHIP-BASELINE-INTEGRITY-MISMATCH",
        "SHIP-BASELINE-ENTRY-EXPIRED",
        "SHIP-BASELINE-ENTRY-STALE",
    }
)

def _tool_surface_changes(report: ReadinessReport) -> int:
    """Sum of structural-change counters in tool_surface_diff.summary.

    Returns zero when the diff is disabled (no baseline configured) or
    when every counter is zero (no changes). The counters are
    pre-computed by compute_tool_surface_diff so this is O(1).
    """
    diff = report.tool_surface_diff
    if diff is None or not getattr(diff, "enabled", False):
        return 0
    summary = diff.summary
    return (
        summary.tools_added
        + summary.tools_removed
        + summary.tools_changed
        + summary.new_scopes
        + summary.removed_scopes
        + summary.new_high_risk_effects
        + summary.removed_high_risk_effects
        + summary.controls_added
        + summary.controls_removed
        + summary.metadata_changes
        + summary.policy_drift_items
    )


def _action_surface_changes(report: ReadinessReport) -> int:
    """Sum of structural-change counters in action_surface_diff.summary.

    Returns zero when the diff is disabled or empty. ``blocking_findings``
    is intentionally NOT summed here — that count flows into the release
    decision, and is already reflected in ``AgentSummary.blocker_count``.
    Reviewer-side activity is the structural delta itself.
    """
    diff = report.action_surface_diff
    if diff is None or not getattr(diff, "enabled", False):
        return 0
    summary = diff.summary
    return (
        summary.actions_added
        + summary.actions_removed
        + summary.actions_modified
        + summary.scope_expansions
        + summary.effect_escalations
        + summary.risk_tags_added
        + summary.approvals_removed
        + summary.safeguards_removed
        + summary.input_schema_expansions
    )


def _capability_misalignment_count(report: ReadinessReport) -> int:
    """Total misalignments surfaced by the capability/intent diff (v0.9)."""
    return len(report.misalignments)


def _evidence_matrix_gap_count(report: ReadinessReport) -> int:
    """Count of evidence-matrix rows whose status is a reviewer-actionable
    gap.

    The matrix is normally a packet-only section, but ``build_evidence_matrix``
    accepts the report payload and produces the same shape. We call it
    here so the count is available in JSON reports even when the packet
    is disabled (``--no-packet`` runs).

    Only ``missing`` rows are counted as gaps. ``not_declared`` means
    "this domain is not relevant to this manifest" (e.g.,
    ``memory_isolation`` on every scan today because the manifest schema
    does not model it yet) — these are intentional non-coverage, not
    reviewer signals. ``partial``, ``covered``, and ``informational``
    are also not gaps.

    Import is deferred to avoid a top-level cycle: ``packet`` imports
        from ``schemas/report.py`` (and indirectly from ``core/findings/``
    via build helpers), so we import only when this function runs.
    """
    try:
        from agents_shipgate.packet.evidence_matrix import build_evidence_matrix
    except ImportError:  # pragma: no cover - defensive
        return 0
    payload = report.model_dump(mode="json")
    section = build_evidence_matrix(payload)
    return sum(
        1
        for row in section.rows
        if getattr(row, "evidence_present", None) == "missing"
    )


def _severity_override_counts(
    policy_audit: PolicyAudit | None,
) -> tuple[int, int]:
    """Return (total_applied, tier_crossed_subset).

    ``total_applied`` is len(severity_overrides_applied).
    ``tier_crossed_subset`` counts the entries flagged ``tier_crossed=True``.
    """
    if policy_audit is None:
        return 0, 0
    rows = policy_audit.severity_overrides_applied
    return len(rows), sum(1 for row in rows if row.tier_crossed)


def _privacy_redaction_count(privacy_audit: PrivacyAudit | None) -> int:
    """Total redactions across the public output surfaces.

    Reads the pre-computed ``redacted_occurrence_count`` field rather
    than re-summing per-path counts — they agree by construction (see
    privacy.py) but the pre-computed total is the canonical surface and
    keeps the projection cheap.
    """
    if privacy_audit is None or not privacy_audit.enabled:
        return 0
    return privacy_audit.redacted_occurrence_count


def _baseline_integrity_issue_count(findings: list[Finding]) -> int:
    """Count of active findings emitted by the three baseline-integrity
    checks (SHIP-BASELINE-{INTEGRITY-MISMATCH,ENTRY-EXPIRED,ENTRY-STALE}).

    Suppressed findings are excluded; matched (accepted-debt) findings
    are included because tampering and expiry remain reviewer signals
    regardless of baseline status.
    """
    return sum(
        1
        for f in findings
        if not f.suppressed and f.check_id in _BASELINE_INTEGRITY_CHECK_IDS
    )


def _reviewer_headline(
    *,
    verdict: str,
    lens_total: int,
    audit_total: int,
    pointer: ReviewerSurfacePointer | None,
) -> str:
    """Deterministic, ≤200-char one-sentence summary.

    Sort order in the headline mirrors the priority order in
    ``_pick_first_recommended_surface`` so the headline and pointer
    cannot disagree about which surface matters most.
    """
    if verdict == "blocked":
        head = "Release blocked"
    elif verdict == "insufficient_evidence":
        head = "Evidence coverage below threshold"
    elif verdict == "review_required":
        head = "Review required"
    else:  # passed
        head = "Release ready"

    if lens_total == 0 and audit_total == 0:
        body = "no reviewer signals (lenses + audits all clean)."
    else:
        body_parts: list[str] = []
        if lens_total > 0:
            noun = "change" if lens_total == 1 else "changes"
            body_parts.append(f"{lens_total} lens {noun}")
        if audit_total > 0:
            noun = "event" if audit_total == 1 else "events"
            body_parts.append(f"{audit_total} audit {noun}")
        body = "; ".join(body_parts) + "."

    headline = f"{head}: {body}"
    if pointer is not None:
        suffix = f" Start at {pointer.name}."
        if len(headline) + len(suffix) <= 200:
            headline += suffix
    return headline


def _pick_first_recommended_surface(
    *,
    release_decision: ReleaseDecision | None,
    action_surface_changes: int,
    baseline_integrity_issues: int,
    severity_overrides_tier_crossed: int,
    severity_overrides_applied: int,
    capability_misalignments: int,
    tool_surface_changes: int,
    privacy_redactions: int,
    evidence_matrix_gaps: int,
) -> ReviewerSurfacePointer | None:
    """Deterministic priority order. Returns None only when every
    counter is zero AND the release decision is ``passed``.

    Priority encodes "which surface gives the highest-leverage reviewer
    signal first":

      1. Release decision when blocked or insufficient_evidence — the
         verdict tells the reviewer what to gate on.
      2. Action-surface changes — first-class PR/release delta of what
         the agent can do externally.
      3. Baseline integrity issues — tampering or expiry on accepted
         debt is high-attention.
      4. Tier-crossed severity overrides — explicit downgrade across
         a release-critical boundary.
      5. Capability/intent misalignments — declared purpose vs.
         observed surface.
      6. Tool surface changes — registry-level PR diff.
      7. Privacy redactions — output sanitation events.
      8. Evidence matrix gaps — coverage holes for review.
      9. Non-tier-crossed severity overrides — same-tier downgrades
         and upgrades. Lower priority than the tier-crossed case
         because they don't cross a release-critical boundary, but
         still a reviewer signal that warrants a glance. Without
         this fallthrough, ``severity_overrides_applied > 0`` would
         produce a non-zero headline but a ``null`` pointer —
         contradicting the contract that ``null`` means a fully
         clean scan.
     10. review_required verdict with no specific lens/audit signal —
         source warnings (e.g., duplicate tool names) or evidence
         gaps can produce ``decision=review_required`` with all
         reviewer counters at zero. Without this fallthrough, such
         scans emit a non-null verdict but a ``null`` pointer —
         contradicting the contract that ``null`` means
         ``passed + all-zero``.

    Each branch picks the most informative ``path`` and a single
    sentence ``why`` suitable for a PR comment lead.
    """
    verdict = (release_decision.decision if release_decision else "passed")

    if verdict == "blocked":
        return ReviewerSurfacePointer(
            kind="release_decision",
            name="release_decision",
            path="report.release_decision",
            why=(
                "Release is blocked; read release_decision.blockers[] for "
                "the gating findings."
            ),
        )
    if verdict == "insufficient_evidence":
        return ReviewerSurfacePointer(
            kind="release_decision",
            name="release_decision",
            path="report.release_decision",
            why=(
                "Evidence coverage is below threshold; read "
                "release_decision.reason and evidence_coverage."
            ),
        )

    if action_surface_changes > 0:
        return ReviewerSurfacePointer(
            kind="lens",
            name="action_surface_diff",
            path="report.action_surface_diff",
            why=(
                "Action-surface diff has structural changes "
                "(scope, effect, approval, or safeguard); read this lens "
                "first to see what the agent can now do."
            ),
        )

    if baseline_integrity_issues > 0:
        return ReviewerSurfacePointer(
            kind="audit",
            name="baseline_integrity",
            path="report.findings[]",
            why=(
                "Baseline integrity findings were emitted; filter "
                "findings[] by SHIP-BASELINE-* check_id and inspect the "
                "baseline-audit.log alongside the baseline file."
            ),
        )

    if severity_overrides_tier_crossed > 0:
        return ReviewerSurfacePointer(
            kind="audit",
            name="policy_audit",
            path="report.policy_audit.severity_overrides_applied",
            why=(
                "One or more severity overrides crossed a tier boundary "
                "with explicit acknowledgement; review the entries to "
                "confirm the downgrade is still appropriate."
            ),
        )

    if capability_misalignments > 0:
        return ReviewerSurfacePointer(
            kind="lens",
            name="capability_intent_diff",
            path="report.misalignments",
            why=(
                "The capability/intent diff surfaced misalignments "
                "between declared agent purpose and observed tool "
                "surface; read misalignments[] for the specifics."
            ),
        )

    if tool_surface_changes > 0:
        return ReviewerSurfacePointer(
            kind="lens",
            name="tool_surface_diff",
            path="report.tool_surface_diff",
            why=(
                "Tool-surface diff has structural changes (tools, "
                "scopes, controls, or policies); read this lens to see "
                "the registry-level delta."
            ),
        )

    if privacy_redactions > 0:
        return ReviewerSurfacePointer(
            kind="audit",
            name="privacy_audit",
            path="report.privacy_audit.redacted_paths",
            why=(
                "Sensitive values were redacted from the public outputs; "
                "confirm the redactions match expectations and that no "
                "secret should have been there in the first place."
            ),
        )

    if evidence_matrix_gaps > 0:
        return ReviewerSurfacePointer(
            kind="evidence_matrix",
            name="evidence_matrix",
            path="packet.evidence_matrix.rows",
            why=(
                "Evidence-matrix rows report coverage gaps "
                "(missing/not_declared); open the Release Evidence "
                "Packet to see which domains lack reviewer-visible "
                "evidence."
            ),
        )

    # Low-priority fallthrough: same-tier severity overrides and
    # upgrades. Without this branch, a manifest with a single
    # medium → low override would produce a non-zero
    # ``severity_overrides_applied`` counter AND a non-zero
    # ``audit_total`` in the headline, but a ``null`` pointer —
    # contradicting the contract that ``null`` means a fully clean
    # scan. The tier-crossed case above handles the higher-attention
    # subset; this branch covers the rest.
    if severity_overrides_applied > 0:
        return ReviewerSurfacePointer(
            kind="audit",
            name="policy_audit",
            path="report.policy_audit.severity_overrides_applied",
            why=(
                "Severity overrides are applied (same-tier or upgrade); "
                "review the policy_audit entries to confirm the overrides "
                "match reviewer intent."
            ),
        )

    # Final fallthrough: review_required verdict with no specific
    # lens/audit signals. Source warnings (e.g., duplicate tool names,
    # unresolvable imports) or an evidence gap can force
    # decision=review_required even when findings=0 and all reviewer
    # counters are zero. Without this branch, such scans would emit
    # first_recommended_surface=null despite a non-passed verdict,
    # contradicting the contract that null means passed + all-zero.
    if verdict == "review_required":
        return ReviewerSurfacePointer(
            kind="release_decision",
            name="release_decision",
            path="report.release_decision",
            why=(
                "Review is required (source warnings or evidence gap "
                "without specific lens/audit signals); read "
                "release_decision.reason for details."
            ),
        )

    return None


def build_reviewer_summary(
    *,
    findings: list[Finding],
    report: ReadinessReport,
) -> ReviewerSummary:
    """Construct the top-level ``reviewer_summary`` block.

    Deterministic projection of the reviewer lens surfaces and audit
    envelopes on ``report``. Parallels ``build_agent_summary`` but for
    the audit/lens dimensions: a reviewer who wants headline activity
    counts and a recommended starting surface reads this block instead
    of opening every lens and audit envelope.

    ``findings`` is passed separately (not derived from
    ``report.findings``) so we can run on the same active-findings list
    callers have already filtered/annotated — same pattern as
    ``build_agent_summary``.
    """
    tool_surface_changes = _tool_surface_changes(report)
    action_surface_changes = _action_surface_changes(report)
    capability_misalignments = _capability_misalignment_count(report)
    evidence_matrix_gaps = _evidence_matrix_gap_count(report)
    severity_overrides_applied, severity_overrides_tier_crossed = (
        _severity_override_counts(report.policy_audit)
    )
    privacy_redactions = _privacy_redaction_count(report.privacy_audit)
    baseline_integrity_issues = _baseline_integrity_issue_count(findings)

    lens_total = (
        tool_surface_changes
        + action_surface_changes
        + capability_misalignments
        + evidence_matrix_gaps
    )
    audit_total = (
        severity_overrides_applied
        + privacy_redactions
        + baseline_integrity_issues
    )

    verdict = (
        report.release_decision.decision if report.release_decision else "passed"
    )

    pointer = _pick_first_recommended_surface(
        release_decision=report.release_decision,
        action_surface_changes=action_surface_changes,
        baseline_integrity_issues=baseline_integrity_issues,
        severity_overrides_tier_crossed=severity_overrides_tier_crossed,
        severity_overrides_applied=severity_overrides_applied,
        capability_misalignments=capability_misalignments,
        tool_surface_changes=tool_surface_changes,
        privacy_redactions=privacy_redactions,
        evidence_matrix_gaps=evidence_matrix_gaps,
    )
    headline = _reviewer_headline(
        verdict=verdict,
        lens_total=lens_total,
        audit_total=audit_total,
        pointer=pointer,
    )

    return ReviewerSummary(
        verdict=verdict,
        headline=headline,
        tool_surface_changes=tool_surface_changes,
        capability_misalignments=capability_misalignments,
        action_surface_changes=action_surface_changes,
        evidence_matrix_gaps=evidence_matrix_gaps,
        severity_overrides_applied=severity_overrides_applied,
        severity_overrides_tier_crossed=severity_overrides_tier_crossed,
        privacy_redactions=privacy_redactions,
        baseline_integrity_issues=baseline_integrity_issues,
        first_recommended_surface=pointer,
    )
