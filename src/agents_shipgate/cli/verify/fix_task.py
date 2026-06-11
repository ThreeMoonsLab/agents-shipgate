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

from agents_shipgate.schemas.report import Finding, ReadinessReport
from agents_shipgate.schemas.verifier import (
    MergeVerdict,
    VerifierCapabilityReview,
    VerifierFixTask,
    VerifierFixTaskPatch,
    VerifierRepair,
)

# Reward-hacking moves that are never acceptable, for either actor. Kept in
# sync with the PR-comment guardrail language (cli/verify/pr_comment.py).
FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "Do not suppress the finding (checks.ignore in shipgate.yaml).",
    "Do not lower severity or add a waiver just to pass the gate.",
    "Do not invent or assume approval, idempotency, or audit evidence you "
    "cannot prove from the code.",
    "Do not weaken the release policy, CI gate, or agent instructions that "
    "evaluate this change.",
)

_MAX_INSTRUCTIONS = 5
_MAX_REPAIRS = 10

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
) -> VerifierFixTask | None:
    """Project the head scan onto a single repair task.

    Returns ``None`` when there is nothing to fix (mergeable, or no head
    release decision to reason about).
    """
    if merge_verdict == "mergeable":
        return None

    verification_command = _verification_command(base_ref, head_ref)

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

    # The coding-agent route is the only non-human outcome and it MUST fail
    # closed: every gating finding has to be explicitly mechanical
    # (``autofix_safe is True`` AND ``requires_human_review is False``). A
    # finding whose routing fields are ``None``/``False`` — stale, plugin, or
    # legacy — is treated as an authority gap and never silently marked
    # agent-safe.
    mechanical = bool(gating) and all(
        finding.autofix_safe is True and finding.requires_human_review is False
        for finding in gating
    )
    authority_escalation = (
        capability_review.policy_weakened
        or capability_review.trust_root_touched
        or merge_verdict in {"insufficient_evidence", "unknown"}
    )
    if mechanical and not authority_escalation:
        return VerifierFixTask(
            actor="coding_agent",
            safe_to_attempt=True,
            instructions=_mechanical_instructions(gating),
            allowed_repairs=_mechanical_repairs(
                gating,
                verification_command=verification_command,
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
            report, capability_review, gating, merge_verdict=merge_verdict
        ),
        allowed_repairs=_human_repairs(
            report,
            capability_review,
            gating,
            verification_command=verification_command,
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


def _human_instructions(
    report: ReadinessReport,
    capability_review: VerifierCapabilityReview,
    gating: list[Finding],
    *,
    merge_verdict: MergeVerdict = "human_review_required",
) -> list[str]:
    decision = report.release_decision
    assert decision is not None
    out: list[str] = [decision.reason]
    if merge_verdict == "insufficient_evidence":
        out.extend(_insufficient_evidence_remedies(report))
    if capability_review.policy_weakened:
        out.append(
            "A human must approve the release-policy change in this PR; the "
            "coding agent that made the change cannot self-approve it."
        )
    if capability_review.trust_root_touched:
        out.append(
            "A human must review the touched release trust root (manifest, CI "
            "gate, agent instructions, or trigger catalog) before merge."
        )
    # List every gating finding's recommendation — a human-routed task owns the
    # whole decision, including findings whose routing fields were ambiguous.
    out.extend(finding.recommendation for finding in gating if finding.recommendation)
    return _dedupe_cap(out)


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
        out.append(
            f"{count} {noun} from {source_type} source {source_ref!r} extracted "
            "with low confidence: declare an explicit local tool inventory for "
            "that source in shipgate.yaml (tool_inventories), or replace the "
            "dynamic/config-bound toolkit with statically enumerable tool "
            "definitions, then re-run verify."
        )
    for warning in report.source_warnings[:3]:
        out.append(f"Resolve source warning: {warning}")
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
) -> list[VerifierRepair]:
    repairs: list[VerifierRepair] = []
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
                    command=(
                        "agents-shipgate apply-patches --from "
                        "agents-shipgate-reports/report.json "
                        "--confidence high --apply"
                    ),
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


def _human_repairs(
    report: ReadinessReport,
    capability_review: VerifierCapabilityReview,
    gating: list[Finding],
    *,
    verification_command: str,
) -> list[VerifierRepair]:
    decision = report.release_decision
    assert decision is not None
    repairs: list[VerifierRepair] = []
    if capability_review.policy_weakened:
        repairs.append(
            VerifierRepair(
                id="review_policy_weakening",
                actor="human",
                kind="review_policy_change",
                target="shipgate.yaml",
                reason="A human must approve release-policy weakening before merge.",
            )
        )
    if capability_review.trust_root_touched:
        repairs.append(
            VerifierRepair(
                id="review_trust_root",
                actor="human",
                kind="review_trust_root_change",
                target="manifest, CI gate, agent instructions, or trigger catalog",
                reason="A human must review the touched release trust root before merge.",
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
            if getattr(patch, "kind", "manual") == "manual":
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


def _verification_command(base_ref: str | None, head_ref: str) -> str:
    # Refs come from CLI / GitHub branch inputs and a valid git ref may contain
    # shell metacharacters (e.g. ``;``); quote them so the emitted command is
    # safe to run when an agent or human copies it verbatim.
    base = shlex.quote(base_ref or "origin/main")
    head = shlex.quote(head_ref or "HEAD")
    return f"agents-shipgate verify --base {base} --head {head} --json"


__all__ = ["FORBIDDEN_SHORTCUTS", "build_fix_task"]
