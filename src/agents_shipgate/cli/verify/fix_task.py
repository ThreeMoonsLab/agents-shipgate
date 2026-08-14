"""Deterministic ``fix_task`` projection for ``agents-shipgate verify``.

The fix_task is the single repair instruction a verify run hands to whoever
must act next. It is a pure projection of the head scan: *who* acts and
*whether it is safe* is decided by the release decision plus the per-finding
``autofix_safe`` / ``requires_human_review`` signals — never by a model. The
agent route fires only when every gating finding is mechanically fixable; any
authority gap (missing approval/idempotency evidence, a weakened policy, a
touched trust root, or degraded evidence) routes to a human so a coding agent
cannot invent its way past it.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence

from agents_shipgate.ci.release_decision import (
    _inventory_manifest_key,
    evidence_below_ie_threshold,
)
from agents_shipgate.core.agent_controls import FORBIDDEN_SHORTCUTS
from agents_shipgate.core.evidence_actions import (
    evidence_gap_target,
    is_addressable_gap,
    one_line,
)
from agents_shipgate.core.source_warnings import group_source_warnings
from agents_shipgate.invocation import retarget_command
from agents_shipgate.schemas.report import (
    EvidenceCoverageDecision,
    EvidenceGap,
    Finding,
    ReadinessReport,
)
from agents_shipgate.schemas.verifier import (
    MergeVerdict,
    VerifierCapabilityReview,
    VerifierFixTask,
    VerifierFixTaskPatch,
    VerifierRepair,
)

_MAX_INSTRUCTIONS = 5
_MAX_REPAIRS = 10
_ADOPTION_CHECK_ID = "SHIP-VERIFY-POLICY-WEAKENED"
_ADOPTION_EVIDENCE_KIND = "manifest_introduced"
_ADOPTION_TRUST_ROOT_CHECK_ID = "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
_ADOPTION_BOUNDARY_CHECK_ID = (
    "SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"
)

_FORBIDDEN_REPAIR_SPECS: tuple[tuple[str, str, str, str], ...] = (
    (
        "suppress_finding",
        "manifest_suppression",
        "checks.ignore",
        "Do not suppress Shipgate findings to make the verifier pass.",
    ),
    (
        "lower_severity",
        "severity_override",
        "checks.severity_overrides",
        "Do not lower severity or add a waiver just to pass the gate.",
    ),
    (
        "expand_baseline_or_waiver",
        "baseline_or_waiver",
        ".agents-shipgate or shipgate.yaml",
        "Do not expand baselines or waivers to hide a new PR finding.",
    ),
    (
        "weaken_release_gate",
        "trust_root_change",
        "Shipgate CI, policy, or agent instructions",
        "Do not weaken the release policy, CI gate, or agent instructions.",
    ),
    (
        "invent_authority_evidence",
        "authority_evidence",
        "approval, idempotency, audit, or human_ack evidence",
        "Do not invent or assume authority evidence that is not present in code or reviewed records.",
    ),
)


def build_fix_task(
    report: ReadinessReport | None,
    *,
    merge_verdict: MergeVerdict,
    capability_review: VerifierCapabilityReview | None,
    base_ref: str | None,
    head_ref: str,
    manifest_introduced: bool = False,
    config: str | None = None,
    worktree: bool = False,
    rerun_options: Sequence[str] | None = None,
    report_path: str = "agents-shipgate-reports/report.json",
    repair_subject_available: bool = True,
) -> VerifierFixTask | None:
    """Project the head scan onto a single repair task.

    Returns ``None`` when there is nothing to fix (mergeable, or no head
    release decision to reason about).

    ``manifest_introduced`` changes only the wording of the trust-root
    instruction and repair: a PR that adds the manifest to a base that had none
    is adopting a gate, not weakening one. "Review and merge this" appears only
    when adoption is the sole gating concern; otherwise the real blocker leads
    and adoption is named as a separate review. Routing is untouched —
    adoption remains an authority escalation and always routes to a human.
    """
    if merge_verdict == "mergeable":
        return None

    verification_command = _verification_command(
        base_ref, head_ref, config=config, worktree=worktree, options=rerun_options
    )

    # No completed head decision (scan skipped/failed → ``unknown``) but the PR
    # is not mergeable: there are no findings to route on, so fail closed to a
    # human who must investigate why the scan did not complete. Emitting a task
    # here (rather than None) keeps the contract uniform — every non-mergeable
    # verdict carries a fix_task.
    if report is None or report.release_decision is None or capability_review is None:
        return VerifierFixTask(
            actor="human",
            safe_to_attempt=False,
            instructions=[
                "Shipgate could not produce a release decision for this PR; a "
                "human must investigate why the scan did not complete and "
                "re-run before merge."
            ],
            allowed_repairs=[
                VerifierRepair(
                    id="investigate_scan_incomplete",
                    actor="human",
                    kind="investigate",
                    target="agents-shipgate-reports",
                    reason="The verifier did not produce a release decision.",
                )
            ],
            forbidden_repairs=_forbidden_repairs(),
            forbidden_shortcuts=list(FORBIDDEN_SHORTCUTS),
            verification_command=verification_command,
        )

    gating = _gating_findings(report)
    pure_adoption_review = _is_pure_adoption_review(
        report,
        gating,
        manifest_introduced=manifest_introduced,
    )

    # Degraded static evidence (below the IE threshold) is an authority gap
    # regardless of which verdict it produced. An active high/critical finding
    # elevates a degraded-evidence case from `insufficient_evidence` to
    # `review_required` (a more actionable verdict),
    # so keying the escalation on `merge_verdict == "insufficient_evidence"`
    # alone would let a mechanically-fixable high finding open a coding-agent
    # auto-fix path on weak evidence. Compute the threshold directly from the
    # same predicate the release decision uses so the two never drift.
    evidence_degraded = evidence_below_ie_threshold(
        report.release_decision.evidence_coverage,
        tool_count=len(report.tool_inventory),
    )

    # The coding-agent route is the only non-human outcome and it MUST fail
    # closed: every gating finding has to be explicitly mechanical
    # (``autofix_safe is True`` AND ``requires_human_review is False``). A
    # finding whose routing fields are ``None``/``False`` — stale, plugin, or
    # legacy — is treated as an authority gap and never silently marked
    # agent-safe.
    mechanical = bool(gating) and all(
        finding.autofix_safe is True and finding.requires_human_review is False
        for finding in gating
    ) and _all_gating_findings_have_applicable_patches(gating)
    authority_escalation = (
        capability_review.policy_weakened
        or capability_review.trust_root_touched
        # Listed in its own right, not via ``policy_weakened``: that flag now
        # reports the honest ``false`` for an adoption, and adopting a release
        # policy is an authority decision no coding agent may make.
        or manifest_introduced
        or merge_verdict in {"insufficient_evidence", "unknown"}
        or evidence_degraded
        or not repair_subject_available
    )
    if mechanical and not authority_escalation:
        return VerifierFixTask(
            actor="coding_agent",
            safe_to_attempt=True,
            instructions=_mechanical_instructions(gating),
            allowed_repairs=_mechanical_repairs(
                gating,
                verification_command=verification_command,
                report_path=report_path,
            ),
            forbidden_repairs=_forbidden_repairs(gating),
            forbidden_shortcuts=list(FORBIDDEN_SHORTCUTS),
            verification_command=verification_command,
            patches=_machine_patches(gating),
        )

    return VerifierFixTask(
        actor="human",
        safe_to_attempt=False,
        instructions=_human_instructions(
            report,
            capability_review,
            gating,
            merge_verdict=merge_verdict,
            evidence_degraded=evidence_degraded,
            manifest_introduced=manifest_introduced,
            pure_adoption_review=pure_adoption_review,
            config=config,
        ),
        allowed_repairs=_human_repairs(
            report,
            capability_review,
            gating,
            verification_command=verification_command,
            manifest_introduced=manifest_introduced,
            pure_adoption_review=pure_adoption_review,
            config=config,
        ),
        forbidden_repairs=_forbidden_repairs(gating),
        forbidden_shortcuts=list(FORBIDDEN_SHORTCUTS),
        verification_command=verification_command,
    )


def _gating_findings(report: ReadinessReport) -> list[Finding]:
    """The active findings driving blockers / review_items, in decision order."""
    decision = report.release_decision
    assert decision is not None  # guarded by build_fix_task
    by_id = {f.id: f for f in report.findings if f.id}
    by_fingerprint = {f.fingerprint: f for f in report.findings if f.fingerprint}
    out: list[Finding] = []
    seen: set[int] = set()
    for item in [*decision.blockers, *decision.review_items]:
        finding = (by_id.get(item.id) if item.id else None) or (
            by_fingerprint.get(item.fingerprint) if item.fingerprint else None
        )
        if finding is not None and id(finding) not in seen:
            out.append(finding)
            seen.add(id(finding))
    return out


def is_pure_adoption_review(
    report: ReadinessReport | None,
    *,
    manifest_introduced: bool,
) -> bool:
    """Whether adoption is the release decision's only gating concern.

    Friendly "review, then merge" copy is only truthful for this narrow
    substrate. A blocked or insufficient-evidence report, any blocker, or any
    second review item must lead with its real stop condition instead.
    """

    if report is None or report.release_decision is None:
        return False
    return _is_pure_adoption_review(
        report,
        _gating_findings(report),
        manifest_introduced=manifest_introduced,
    )


def _is_pure_adoption_review(
    report: ReadinessReport,
    gating: list[Finding],
    *,
    manifest_introduced: bool,
) -> bool:
    decision = report.release_decision
    assert decision is not None
    coverage = decision.evidence_coverage
    adoption_findings = [
        finding
        for finding in gating
        if finding.check_id == _ADOPTION_CHECK_ID
        and finding.evidence.get("kind") == _ADOPTION_EVIDENCE_KIND
    ]
    manifest_paths = {
        str(path)
        for finding in adoption_findings
        for path in (finding.evidence.get("changed_policy_files") or [])
        if path
    }
    adoption_path = next(iter(manifest_paths)) if len(manifest_paths) == 1 else None
    return bool(
        manifest_introduced
        and decision.decision == "review_required"
        and not decision.blockers
        and len(adoption_findings) == 1
        and adoption_path is not None
        # Every decision item must resolve to a finding; otherwise an unknown
        # second concern could disappear behind the friendly adoption copy.
        and len(gating) == len(decision.review_items)
        # A real adoption produces multiple layers of evidence for the same
        # human decision: the policy fail-safe, the generic trust-root touch,
        # and (for custom names) the agent-boundary manifest row. Treat those
        # as one concern only when all point to the exact introduced manifest.
        and all(
            _is_same_manifest_adoption_finding(finding, adoption_path)
            for finding in gating
        )
        and not coverage.human_review_recommended
        and not coverage.evidence_gaps
        and not evidence_below_ie_threshold(
            coverage,
            tool_count=len(report.tool_inventory),
        )
    )


def _is_same_manifest_adoption_finding(
    finding: Finding,
    manifest_path: str,
) -> bool:
    if (
        finding.check_id == _ADOPTION_CHECK_ID
        and finding.evidence.get("kind") == _ADOPTION_EVIDENCE_KIND
    ):
        return finding.evidence.get("changed_policy_files") == [manifest_path]
    if finding.check_id == _ADOPTION_TRUST_ROOT_CHECK_ID:
        return bool(
            finding.evidence.get("changed_file") == manifest_path
            and finding.evidence.get("trust_root_class") == "manifest"
        )
    if finding.check_id == _ADOPTION_BOUNDARY_CHECK_ID:
        source_path = finding.source.path if finding.source is not None else None
        return bool(
            source_path == manifest_path
            and finding.evidence.get("kind")
            in {"manifest_introduced", "protected_surface_unclassified"}
            and (
                finding.evidence.get("kind") == "manifest_introduced"
                or finding.evidence.get("trust_root_class") == "manifest"
            )
        )
    return False


def _human_instructions(
    report: ReadinessReport,
    capability_review: VerifierCapabilityReview,
    gating: list[Finding],
    *,
    merge_verdict: MergeVerdict = "human_review_required",
    evidence_degraded: bool = False,
    manifest_introduced: bool = False,
    pure_adoption_review: bool = False,
    config: str | None = None,
) -> list[str]:
    decision = report.release_decision
    assert decision is not None
    out: list[str] = [decision.reason]
    # The adoption note frames everything under it — a reader who does not know
    # the manifest is new will misread the evidence remedies as complaints
    # about their change. It also has to survive ``_MAX_INSTRUCTIONS``: a
    # cold-start repo generates enough evidence remedies to push a
    # later-appended instruction off the end.
    adoption_note = _adoption_instruction(
        capability_review,
        pure_adoption_review,
        config=config,
    )
    if adoption_note is not None:
        out.append(adoption_note)
    elif manifest_introduced:
        manifest = (
            f"the configured manifest {config!r}"
            if config
            else "the configured Shipgate manifest"
        )
        out.append(
            f"This PR also introduces {manifest}. A human must review that "
            "adoption as a separate release-policy decision; it does not clear "
            "the other gating concerns."
        )
    # Surface the concrete "make the hidden authority enumerable" remedies
    # whenever evidence is degraded — not only on the bare IE verdict. A
    # high-finding case elevated to review_required carries the same
    # evidence gap and the human needs the same remedy.
    if merge_verdict == "insufficient_evidence" or evidence_degraded:
        out.extend(_insufficient_evidence_remedies(report))
    if adoption_note is None:
        if capability_review.policy_weakened:
            out.append(
                "A human must approve the release-policy change in this PR; the "
                "coding agent that made the change cannot self-approve it."
            )
        if capability_review.trust_root_touched:
            out.append(
                "A human must review the touched release trust root (manifest, CI "
                "gate, agent instructions, or trigger catalog)"
                + (
                    " as part of the release decision."
                    if manifest_introduced
                    else " before merge."
                )
            )
    # List every gating finding's recommendation — a human-routed task owns the
    # whole decision, including findings whose routing fields were ambiguous.
    out.extend(finding.recommendation for finding in gating if finding.recommendation)
    return _dedupe_cap(out)


def _adoption_instruction(
    capability_review: VerifierCapabilityReview,
    pure_adoption_review: bool,
    *,
    config: str | None = None,
) -> str | None:
    """The one human act a first adoption needs, or ``None``.

    Keyed on the adoption itself rather than on ``trust_root_touched``, which
    would make the instruction disappear in the one case it exists for. It
    stands down when ``policy_weakened`` is set so the generic instructions —
    including the ``review_policy_weakening`` repair — remain authoritative.
    """

    if not pure_adoption_review or capability_review.policy_weakened:
        return None
    manifest = (
        f"the configured manifest {config!r}"
        if config
        else "the configured Shipgate manifest"
    )
    return (
        "This PR adopts Agents Shipgate. "
        f"Review {manifest} and merge it through a human-reviewed PR — a "
        "coding agent cannot adopt a release policy on the repository's "
        "behalf."
    )


def _is_prose_only_evidence_gap(gap: EvidenceGap) -> bool:
    """True when a gap row carries no typed repair the handoff should keep.

    ``low_confidence_tool`` rows and inventory-scaffold ``incomplete_surface``
    rows are re-derived below from the tool inventory, and a ``source_warning``
    row is usually a ``review_warning`` with nothing to open. But not always:
    the stale-``--diff-from`` base report produces a ``source_warning`` gap
    carrying ``provide_source``, a path, an expectation, and the exact
    regeneration command. Blanket-skipping the kind threw that away and left
    only the raw warning prose, so the verifier handoff named a different
    repair from the one the selected gap names (#362 review).
    """

    if gap.kind == "low_confidence_tool":
        return True
    if gap.kind == "source_warning":
        return not is_addressable_gap(gap)
    return (
        gap.kind == "incomplete_surface"
        and gap.next_action.kind == "declare_tool_inventory"
    )


def _typed_source_warning_subjects(evidence: EvidenceCoverageDecision) -> set[str]:
    """Warning texts already emitted as a typed repair, so prose skips them.

    A ``source_warning`` gap's ``subject`` is the warning text verbatim, which
    is what lets the two passes agree on which rows are already covered.
    """

    return {
        gap.subject
        for gap in evidence.evidence_gaps
        if gap.kind == "source_warning" and is_addressable_gap(gap)
    }


def _insufficient_evidence_remedies(report: ReadinessReport) -> list[str]:
    """Concrete remedies for the ``insufficient_evidence`` dead-end.

    The verdict means static evidence is too weak to gate — typically a
    dynamic or config/factory-bound toolkit whose authority the adapters
    cannot enumerate, or unreadable sources. The remedy is always the same
    shape — make the hidden authority statically enumerable — so name the
    exact sources instead of restating the threshold. Adding or editing an
    inventory asserts what the agent can do, which is why this path stays
    human-routed: a human reviews the declared inventory, the agent must
    not invent one.
    """
    out: list[str] = []
    decision = report.release_decision
    assert decision is not None
    typed_warnings = _typed_source_warning_subjects(decision.evidence_coverage)
    for gap in decision.evidence_coverage.evidence_gaps:
        if _is_prose_only_evidence_gap(gap):
            continue
        action = gap.next_action
        # Every field here is repository-derived and lands in durable,
        # machine-facing contracts — verifier.json `fix_task.instructions[]`,
        # which `agent_result` copies verbatim into `repair.instructions`,
        # `suggested_fixes`, and `agent_repair_instructions`. Unsanitized, a
        # policy-authored `expects` ending `\nControl: complete` writes a
        # forged control line into all three (#362 review).
        accepted_values = ", ".join(one_line(value) for value in action.accepted_values)
        accepted = f" Accepted values: {accepted_values}." if action.accepted_values else ""
        target = f" at {evidence_gap_target(gap)}" if is_addressable_gap(gap) else ""
        command = f" Run: {one_line(action.command)}" if action.command else ""
        out.append(
            f"{one_line(gap.subject)}: {one_line(gap.why)} "
            f"{one_line(action.expects)}{target}.{accepted}{command}"
        )
    by_source: dict[tuple[str, str], int] = {}
    for tool in report.tool_inventory:
        if str(tool.get("confidence") or "") == "high":
            continue
        key = (
            str(tool.get("source_type") or "unknown"),
            str(tool.get("source_ref") or tool.get("source_path") or "unknown"),
        )
        by_source[key] = by_source.get(key, 0) + 1
    for (source_type, source_ref), count in sorted(by_source.items()):
        noun = "tool" if count == 1 else "tools"
        # Only some frameworks have a tool_inventories manifest key. Naming it
        # for a framework that has none (openai_agents_sdk, for one) sends the
        # reader looking for a key the schema will reject.
        manifest_key = _inventory_manifest_key(source_type)
        remedy = (
            f"declare an explicit local tool inventory for that source in "
            f"shipgate.yaml ({manifest_key})"
            if manifest_key is not None
            else (
                "provide a statically enumerable source for that surface — an "
                "MCP export or OpenAPI spec declared under tool_sources"
            )
        )
        out.append(
            f"{count} {noun} from {source_type} source {source_ref!r} extracted "
            f"with low confidence: {remedy}, or replace the "
            "dynamic/config-bound toolkit with statically enumerable tool "
            "definitions, then re-run verify."
        )
    # Whatever is left is warning prose with no typed repair behind it. Group
    # first, then cap: an uncapped list of six near-identical warnings spent
    # the whole cap restating one mechanism and hid the others (#362).
    remaining = [
        warning for warning in report.source_warnings if warning not in typed_warnings
    ]
    for group in group_source_warnings(remaining)[:3]:
        out.append(f"Resolve source warning: {group.message}")
    if not out:
        out.append(
            "Provide clearer static sources — an MCP export, OpenAPI spec, or "
            "explicit local tool inventory — so the scan can enumerate the "
            "tool surface, then re-run verify."
        )
    return out


def _mechanical_instructions(gating: list[Finding]) -> list[str]:
    return _dedupe_cap([finding.recommendation for finding in gating if finding.recommendation])


def _mechanical_repairs(
    gating: list[Finding],
    *,
    verification_command: str,
    report_path: str,
) -> list[VerifierRepair]:
    repairs: list[VerifierRepair] = []
    finding_ids = sorted(
        {
            finding.id
            for finding in gating
            if finding.id is not None
        }
    )
    apply_parts = [
        "agents-shipgate",
        "apply-patches",
        "--from",
        report_path,
    ]
    for finding_id in finding_ids:
        apply_parts.extend(["--finding-id", finding_id])
    apply_parts.extend(["--confidence", "high", "--apply"])
    apply_command = retarget_command(" ".join(shlex.quote(part) for part in apply_parts))
    for finding in gating:
        for index, patch in enumerate(finding.patches or [], start=1):
            if getattr(patch, "kind", None) == "manual":
                continue
            if getattr(patch, "confidence", None) != "high":
                continue
            target = _patch_target(patch)
            repairs.append(
                VerifierRepair(
                    id=_repair_id("apply_patch", finding, index),
                    actor="coding_agent",
                    kind="apply_high_confidence_patch",
                    target=target,
                    finding_id=finding.id,
                    check_id=finding.check_id,
                    command=apply_command,
                    reason=getattr(patch, "rationale", None)
                    or finding.recommendation,
                )
            )
    if repairs:
        return _with_terminal_repair(
            repairs,
            VerifierRepair(
                id="rerun_verify",
                actor="coding_agent",
                kind="verify",
                command=verification_command,
                reason="Re-run the verifier after applying allowed mechanical repairs.",
            ),
        )
    return []


def _all_gating_findings_have_applicable_patches(
    gating: list[Finding],
) -> bool:
    """Agent routing requires one exact selectable high-confidence repair."""

    return all(
        finding.id is not None
        and any(
            getattr(patch, "kind", None) != "manual"
            and getattr(patch, "confidence", None) == "high"
            for patch in finding.patches or []
        )
        for finding in gating
    )


def _human_repairs(
    report: ReadinessReport,
    capability_review: VerifierCapabilityReview,
    gating: list[Finding],
    *,
    verification_command: str,
    manifest_introduced: bool = False,
    pure_adoption_review: bool = False,
    config: str | None = None,
) -> list[VerifierRepair]:
    decision = report.release_decision
    assert decision is not None
    repairs: list[VerifierRepair] = []
    if (
        _adoption_instruction(
            capability_review,
            pure_adoption_review,
            config=config,
        )
        is not None
    ):
        repairs.append(
            VerifierRepair(
                id="adopt_shipgate_manifest",
                actor="human",
                kind="review_trust_root_change",
                target=config or "shipgate.yaml",
                reason=(
                    "This PR introduces the Shipgate manifest; a human must "
                    "review it and merge the adoption."
                ),
            )
        )
    else:
        if manifest_introduced:
            repairs.append(
                VerifierRepair(
                    id="review_shipgate_adoption",
                    actor="human",
                    kind="review_trust_root_change",
                    target=config or "shipgate.yaml",
                    reason=(
                        "Review the proposed Shipgate adoption separately from "
                        "the other gating concerns."
                    ),
                )
            )
        if capability_review.policy_weakened:
            repairs.append(
                VerifierRepair(
                    id="review_policy_weakening",
                    actor="human",
                    kind="review_policy_change",
                    target=config or "shipgate.yaml",
                    reason=(
                        "A human must approve release-policy weakening"
                        + (
                            " as part of the release decision."
                            if manifest_introduced
                            else " before merge."
                        )
                    ),
                )
            )
        if capability_review.trust_root_touched:
            repairs.append(
                VerifierRepair(
                    id="review_trust_root",
                    actor="human",
                    kind="review_trust_root_change",
                    target="manifest, CI gate, agent instructions, or trigger catalog",
                    reason=(
                        "A human must review the touched release trust root"
                        + (
                            " as part of the release decision."
                            if manifest_introduced
                            else " before merge."
                        )
                    ),
                )
            )
    for gap in decision.evidence_coverage.evidence_gaps:
        # Same rule as the remedy text: a path-bearing source_warning row is a
        # typed repair with a target and a command, not review-only prose.
        if gap.kind == "low_confidence_tool" or (
            gap.kind == "source_warning" and not is_addressable_gap(gap)
        ):
            continue
        action = gap.next_action
        subject = one_line(gap.subject)
        target = evidence_gap_target(gap)
        repairs.append(
            VerifierRepair(
                id=f"semantic_{gap.kind}_{len(repairs) + 1}",
                actor="human",
                kind=action.kind,
                # Display fields of a durable repair row: one-lined for the
                # same reason the instructions above are.
                target=f"{subject} ({target})" if target else subject,
                command=one_line(action.command) if action.command else None,
                reason=f"{one_line(gap.why)} {one_line(action.expects)}",
            )
        )
    for finding in gating:
        repairs.append(
            VerifierRepair(
                id=_repair_id("human_review", finding, len(repairs) + 1),
                actor="human",
                kind="review_or_provide_evidence",
                target=finding.tool_name or finding.agent_id or finding.check_id,
                finding_id=finding.id,
                check_id=finding.check_id,
                reason=finding.recommendation or decision.reason,
            )
        )
    return _with_terminal_repair(
        repairs,
        VerifierRepair(
            id="rerun_verify_after_human_action",
            actor="human",
            kind="verify",
            command=verification_command,
            reason="Re-run the verifier after the human decision or evidence update.",
        ),
    )


def _with_terminal_repair(
    repairs: list[VerifierRepair],
    terminal: VerifierRepair,
) -> list[VerifierRepair]:
    if len(repairs) >= _MAX_REPAIRS:
        return [*repairs[: _MAX_REPAIRS - 1], terminal]
    return [*repairs, terminal]


def _forbidden_repairs(gating: list[Finding] | None = None) -> list[VerifierRepair]:
    first = next(iter(gating or []), None)
    return [
        VerifierRepair(
            id=repair_id,
            actor="coding_agent",
            kind=kind,
            target=target,
            finding_id=first.id if first is not None else None,
            check_id=first.check_id if first is not None else None,
            reason=reason,
        )
        for repair_id, kind, target, reason in _FORBIDDEN_REPAIR_SPECS
    ]


def _repair_id(prefix: str, finding: Finding, index: int) -> str:
    anchor = finding.id or finding.fingerprint or finding.check_id
    safe = "".join(char if char.isalnum() else "_" for char in anchor).strip("_")
    return f"{prefix}_{safe or 'finding'}_{index}"


def _patch_target(patch: object) -> str | None:
    target_file = getattr(patch, "target_file", None)
    pointer = getattr(patch, "pointer", None)
    if target_file and pointer is not None:
        return f"{target_file}#{pointer}"
    return target_file


_MAX_PATCHES = 10


def _machine_patches(gating: list[Finding]) -> list[VerifierFixTaskPatch]:
    """Project the machine-applicable suggested patches of gating findings.

    Present only when the head scan ran with ``--suggest-patches``
    (``Finding.patches`` is absent otherwise). ``manual`` patches are
    skipped — their guidance is already carried by ``instructions`` and
    they are intentionally never auto-applied.
    """
    out: list[VerifierFixTaskPatch] = []
    for finding in gating:
        for patch in finding.patches or []:
            if (
                getattr(patch, "kind", "manual") == "manual"
                or getattr(patch, "confidence", None) != "high"
            ):
                continue
            out.append(
                VerifierFixTaskPatch(
                    finding_id=finding.id or None,
                    check_id=finding.check_id,
                    patch=patch.model_dump(mode="json"),
                )
            )
            if len(out) >= _MAX_PATCHES:
                return out
    return out


def _dedupe_cap(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out[:_MAX_INSTRUCTIONS]


def _verification_command(
    base_ref: str | None,
    head_ref: str,
    *,
    config: str | None = None,
    worktree: bool = False,
    options: Sequence[str] | None = None,
) -> str:
    """The exact command that re-runs *this* verification.

    Every part of it is the run that actually happened, because an agent runs
    it verbatim:

    - ``--config`` is always emitted. A repository with a nested manifest
      (``services/api/shipgate.yaml``) re-ran against the default path, which
      is a different gate or no gate at all.
    - The base is emitted only when one was used. Substituting ``origin/main``
      invented a comparison the run never made, and fails outright in a
      repository without that remote.
    - ``--head`` is omitted for a working-tree run. Passing ``HEAD`` switches
      verify to the committed tree, which for an uncommitted first adoption is
      a tree with no manifest in it — the command exits 2.
    - ``options`` carries the rest of the evaluated request — policy packs, a
      baseline, an explicit ``--no-base``, plugin and heuristic modes. Omitting
      them produced a command that ran a *different* evaluation than the one
      whose findings it was supposed to reproduce.

    Refs and paths come from CLI / GitHub inputs and a valid git ref may
    contain shell metacharacters, so every interpolated value is quoted.
    """

    parts = ["agents-shipgate", "verify"]
    if config:
        parts.extend(["--config", shlex.quote(config)])
    if base_ref:
        parts.extend(["--base", shlex.quote(base_ref)])
    if not worktree:
        parts.extend(["--head", shlex.quote(head_ref or "HEAD")])
    parts.extend(options or ())
    parts.append("--json")
    return retarget_command(" ".join(parts))


__all__ = ["FORBIDDEN_SHORTCUTS", "build_fix_task"]
