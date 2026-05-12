from __future__ import annotations

import hashlib
import json
import shlex
from collections import Counter, defaultdict

from agents_shipgate.ci.release_decision import build_release_decision
from agents_shipgate.config.schema import AgentsShipgateManifest, SuppressionConfig
from agents_shipgate.core.check_ids import expands_to_check_id
from agents_shipgate.core.models import (
    AgentAction,
    AgentSummary,
    AgentSummaryAction,
    BaselineSummary,
    CheckMetadata,
    CodexPluginSurface,
    Finding,
    LoadedPolicyPack,
    ReadinessReport,
    ReleaseDecision,
    ReportSummary,
    Severity,
    Tool,
    ToolSurfaceDiff,
    ToolSurfaceFacts,
    ToolSurfaceSummary,
    confidence_rank,
)
from agents_shipgate.core.patches import ManualPatch
from agents_shipgate.core.risk_hints import is_high_risk_tool, risk_tags

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
FINGERPRINT_EXCLUDED_EVIDENCE_KEYS = {"default_severity", "source_provenance"}


def assign_finding_ids(findings: list[Finding]) -> list[Finding]:
    by_fingerprint: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        finding.fingerprint = finding_fingerprint(finding)
        by_fingerprint[finding.fingerprint].append(finding)
    for finding in findings:
        assert finding.fingerprint is not None
        if len(by_fingerprint[finding.fingerprint]) == 1:
            finding.id = finding.fingerprint
            continue
        finding.id = f"{finding.fingerprint}_{_collision_discriminator(finding)}"
    return findings


def apply_suppressions(
    findings: list[Finding], suppressions: list[SuppressionConfig]
) -> list[Finding]:
    for finding in findings:
        match = _matching_suppression(finding, suppressions)
        if match:
            finding.suppressed = True
            finding.suppression_reason = match.reason
    return findings


def apply_severity_overrides(
    findings: list[Finding], overrides: dict[str, Severity]
) -> list[Finding]:
    for finding in findings:
        override = _severity_override_for_check(finding.check_id, overrides)
        if override:
            # Keep this audit field out of fingerprinting so overrides can be
            # applied before or after ID assignment without changing identity.
            finding.evidence.setdefault("default_severity", finding.severity)
            finding.severity = override
    return findings


# v0.7: safe-closed default for findings whose check_id isn't in the
# loaded catalog — policy-pack rules, third-party plugins, or any check
# emitted outside the built-in set. The static catalog is silent for
# these, so we default-close: human review required, no auto-fix kind
# claimed.
_REMEDIATION_FALLBACK = {
    "autofix_safe": False,
    "requires_human_review": True,
    "suggested_patch_kind": "manual",
    "docs_url": None,
}


def annotate_remediation(
    findings: list[Finding],
    check_metadata_lookup: dict[str, CheckMetadata],
) -> list[Finding]:
    """Populate the v0.7 per-finding remediation fields in place.

    Strict derivation policy:

    - When ``finding.patches`` is non-empty, the safety bools are derived
      from the actual emitted patches:
      * ``autofix_safe=True`` iff EVERY patch is non-manual AND has
        ``confidence == "high"``. Mixed-state (e.g. one safe + one
        manual, one high + one medium) → ``autofix_safe=False``.
      * ``requires_human_review`` is the inverse of ``autofix_safe``.
      * ``suggested_patch_kind`` = kind of the first non-manual patch,
        or ``"manual"`` when all are manual, or ``"none"`` when the
        list is empty.
    - When ``finding.patches`` is None (scan ran without
      ``--suggest-patches``), the safety bools and
      ``suggested_patch_kind`` come from the matching ``CheckMetadata``
      entry, with the safe-closed fallback for unknown check IDs.
    - ``docs_url`` is always sourced from CheckMetadata (or None for
      unknown check IDs). Patches don't carry per-instance doc URLs.

    Caller (`scan.run_scan`) builds the metadata lookup from the
    catalog with the scan's actual ``plugins_enabled`` setting, so this
    function never triggers plugin loading at serialization time.
    """
    for finding in findings:
        meta = check_metadata_lookup.get(finding.check_id)
        catalog_doc_url = meta.docs_url if meta is not None else None

        # Three states, treated distinctly:
        # 1. `patches is None`  → scan ran without --suggest-patches.
        #    Seed from CheckMetadata (or safe-closed fallback for
        #    unknown check IDs).
        # 2. `patches == []`    → scan ran WITH --suggest-patches but
        #    the generator emitted nothing for this finding. Treat as
        #    safe-closed with `suggested_patch_kind="none"` — falling
        #    back to the catalog would misleadingly report a patch
        #    kind that the report doesn't actually carry.
        # 3. `patches` non-empty → derive from the actual patches
        #    via the strict rule below.
        if finding.patches is None:
            if meta is not None:
                autofix_safe = meta.autofix_safe
                requires_human_review = meta.requires_human_review
                suggested_patch_kind = meta.suggested_patch_kind
            else:
                autofix_safe = bool(_REMEDIATION_FALLBACK["autofix_safe"])
                requires_human_review = bool(
                    _REMEDIATION_FALLBACK["requires_human_review"]
                )
                suggested_patch_kind = str(
                    _REMEDIATION_FALLBACK["suggested_patch_kind"]
                )
        else:
            (
                autofix_safe,
                requires_human_review,
                suggested_patch_kind,
            ) = _derive_from_patches(finding.patches)

        finding.autofix_safe = autofix_safe
        finding.requires_human_review = requires_human_review
        finding.suggested_patch_kind = suggested_patch_kind
        finding.docs_url = catalog_doc_url
        finding.agent_action = derive_agent_action(finding)
    return findings


def derive_agent_action(finding: Finding) -> AgentAction:
    """Project ``finding`` to a single ``AgentAction`` enum value.

    Deterministic projection of (``patches``, ``autofix_safe``,
    ``requires_human_review``). Order-invariant: the result depends
    on the SET of patches, not on their list ordering. The first
    non-manual patch's confidence drives the verdict, mirroring
    :func:`_derive_from_patches` (which derives ``suggested_patch_kind``
    from the first non-manual patch). Earlier this function used
    ``patches[0]`` directly, so a finding with
    ``[ManualPatch, medium SetPointerPatch]`` mapped to
    ``escalate_to_human`` while
    ``[medium SetPointerPatch, ManualPatch]`` mapped to
    ``propose_patch_for_review`` despite identical patch content
    (#57 review P2).

    The strategy proposal in ``docs/agent-adoption-strategy.md`` §7
    G10 sketched an algorithm that ordered ``requires_human_review``
    before the medium/low confidence check, but that mapped non-manual
    medium-confidence patches to ``escalate_to_human`` even though the
    value's defined semantic ("no machine-applicable patch; needs
    human judgment") excludes that case. We deviate by checking
    confidence on the first non-manual patch BEFORE falling through
    to escalate, keeping the value definitions consistent with the
    projection.

    The ``suppress_with_reason`` value is reserved for future check
    classes that explicitly mark themselves as suppressible. The
    built-in projection does not emit it.
    """
    if finding.suppressed:
        return "informational"

    patches = finding.patches

    # No patch list (no --suggest-patches) or empty patch list:
    # nothing machine-applicable. Route on the catalog flags.
    if not patches:
        if finding.requires_human_review:
            return "escalate_to_human"
        return "informational"

    # Pick the first non-manual patch (order-invariant: every patch
    # generator produces a stable order, but the agent_action verdict
    # should depend on the set, not on which manual patch happened to
    # land first). All-manual lists fall through to escalate.
    non_manual = [p for p in patches if p.kind != "manual"]
    if not non_manual:
        return "escalate_to_human"

    first = non_manual[0]
    first_confidence = getattr(first, "confidence", None)
    if first_confidence == "high" and finding.autofix_safe:
        return "auto_apply"

    # Any non-manual patch with declared confidence (high, medium, or
    # low) is machine-applicable, so the verdict is propose-for-review
    # — including high-confidence patches in mixed lists where a
    # ManualPatch sibling disqualified `autofix_safe`. The enum's
    # `escalate_to_human` definition is "no machine-applicable patch",
    # which doesn't fit this case; routing it to escalate would
    # contradict the documented semantics (#57 review P3).
    if first_confidence in {"high", "medium", "low"}:
        return "propose_patch_for_review"

    # Rare: non-manual patch carries no confidence. Conservative escalate.
    if finding.requires_human_review:
        return "escalate_to_human"
    return "informational"


def build_agent_summary(
    *,
    findings: list[Finding],
    release_decision: ReleaseDecision | None,
    json_report_path: str | None = None,
) -> AgentSummary:
    """Construct the top-level ``agent_summary`` block.

    Deterministic projection of ``release_decision`` plus the
    per-finding ``agent_action`` values. Surfaces the same numbers a
    coding agent would otherwise compute by traversing arrays — same
    inputs, same output, no agent-side aggregation needed.

    ``json_report_path`` is the actual on-disk path of the emitted JSON
    report (from ``ReadinessReport.generated_reports['json']``). It is
    threaded in so ``first_recommended_action.command`` can name the
    real path the user just wrote — not the default. When the scan ran
    without JSON output (no path available), the action falls back to
    ``kind: "info"`` with a parameterised hint instead of a command,
    so we never emit an apply-patches invocation pointing at a file
    that doesn't exist or — worse — at a stale default-path report
    from a previous run.
    """
    if release_decision is None:
        verdict: str = "passed"
        blocker_count = 0
        review_item_count = 0
        reason = "No release decision computed."
        evidence_recommended = False
    else:
        verdict = release_decision.decision
        blocker_count = len(release_decision.blockers)
        review_item_count = len(release_decision.review_items)
        reason = (release_decision.reason or "").strip()
        # `evidence_coverage.human_review_recommended` is the
        # release-decision signal that says "this is review_required
        # because the scan saw only low-confidence/static evidence,
        # not because any specific finding needs fixing." In that
        # case we want to surface the evidence-coverage reason
        # (rather than the unhelpful "0 review items flagged" text)
        # and route the agent toward gathering better evidence
        # (#57 review P2: evidence-only review_required).
        evidence_recommended = bool(
            release_decision.evidence_coverage
            and release_decision.evidence_coverage.human_review_recommended
        )

    active_findings = [f for f in findings if not f.suppressed]
    auto_appliable = sum(
        1 for f in active_findings if f.agent_action == "auto_apply"
    )
    # `needs_human_review` covers every active finding the user has to
    # weigh in on before release: full escalations (no machine path)
    # PLUS proposed patches that ship at medium/low confidence and
    # require an explicit `--apply` after the user reviews the diff.
    # Earlier this counted only `escalate_to_human`, which silently
    # under-counted propose_patch_for_review findings — release_decision
    # already routes both into review_items, so the agent_summary
    # number must agree (#57 review P1).
    needs_review = sum(
        1
        for f in active_findings
        if f.agent_action in {"escalate_to_human", "propose_patch_for_review"}
    )

    # Headline: short, one-sentence statement that names the verdict
    # and the action-driven counts. The two populations differ:
    # `review_item_count` mirrors `release_decision.review_items`
    # (severity-driven; can include medium-severity auto_apply
    # findings), while `needs_human_review` counts only findings whose
    # `agent_action` requires human input. The headline uses
    # `needs_human_review` for the "require human review" wording so a
    # review_required verdict with only auto-applicable findings reads
    # honestly as "auto-applicable; none require human input" instead
    # of falsely claiming N findings need review.
    # `release_decision.reason` is severity-driven and can contradict
    # an action-driven headline (e.g. when only-auto-applicable
    # findings are flagged for release review, the reason often reads
    # "1 finding requires human review" — the opposite of what
    # agent_summary needs to say). We therefore skip the reason append
    # in branches where the headline already explains the agent-level
    # situation in agent-driven terms; we keep the append in branches
    # where the reason adds non-overlapping context (like blocker
    # counts).
    append_reason = True
    if verdict == "blocked":
        headline = (
            f"{blocker_count} active finding(s) block release"
            + (
                f"; {review_item_count} review item(s) accepted as debt."
                if review_item_count
                else "."
            )
        )
    elif verdict == "review_required":
        if needs_review > 0:
            head = f"{needs_review} finding(s) require human review"
            if auto_appliable > 0:
                head += f"; {auto_appliable} also auto-applicable"
            headline = head + "."
        elif auto_appliable > 0:
            headline = (
                f"{auto_appliable} auto-applicable finding(s) flagged for "
                "release review; none require human input beyond apply-patches."
            )
            # Suppress the severity-driven reason here. release_decision
            # likely says something like "N finding(s) require human
            # review" — appending it would directly contradict the
            # action-driven headline (#57 review P1).
            append_reason = False
        elif evidence_recommended:
            # Evidence-coverage-driven review: no actionable findings,
            # but the scan saw only low-confidence/static evidence and
            # the release_decision wants a human to weigh in. Surface
            # the reason directly — it carries the only useful
            # explanation. Falling back to "0 review items flagged"
            # would lose the most important context (#57 review P2).
            headline = (
                reason
                if reason
                else "Human review recommended: low-confidence evidence."
            )
            append_reason = False  # already in headline
        else:
            # Even rarer fallback: review_required without any of the
            # above signals. Surface review_item_count so the
            # headline isn't a self-contradiction.
            headline = (
                f"{review_item_count} review item(s) flagged for release review."
            )
            append_reason = False
        if blocker_count:
            headline += f" ({blocker_count} blocker(s) detected.)"
    else:
        headline = (
            "Release ready"
            + (
                f" ({review_item_count} review item(s) accepted as debt)."
                if review_item_count
                else "."
            )
        )
    if append_reason and reason and len(headline) + len(reason) + 4 < 240:
        headline = f"{headline} {reason}" if reason.endswith(".") else f"{headline} {reason}."

    first_action = _build_first_recommended_action(
        verdict=verdict,
        auto_appliable=auto_appliable,
        needs_review=needs_review,
        review_item_count=review_item_count,
        active_findings=active_findings,
        json_report_path=json_report_path,
        evidence_recommended=evidence_recommended,
        evidence_reason=reason if evidence_recommended else "",
    )

    return AgentSummary(
        verdict=verdict,  # type: ignore[arg-type]
        headline=headline,
        blocker_count=blocker_count,
        review_item_count=review_item_count,
        auto_appliable_patches=auto_appliable,
        needs_human_review=needs_review,
        first_recommended_action=first_action,
    )


def _build_first_recommended_action(
    *,
    verdict: str,
    auto_appliable: int,
    needs_review: int,
    review_item_count: int,
    active_findings: list[Finding],
    json_report_path: str | None,
    evidence_recommended: bool = False,
    evidence_reason: str = "",
) -> AgentSummaryAction | None:
    """Deterministic next-step picker for ``agent_summary``.

    Order (highest impact first):
    1. Auto-applicable patches available → propose ``apply-patches``,
       but only as a ``command`` action when we know the actual JSON
       report path (so the command never points at the wrong file).
       Otherwise emit ``kind: "info"`` with a parameterised hint.
    2. Verdict is blocked → surface the top blocker for review.
    3. Verdict is review_required → walk the top review item.
    4. Verdict is passed → no action (None).
    """
    if auto_appliable > 0:
        why = (
            f"{auto_appliable} finding(s) carry high-confidence patches "
            "safe to apply without human review."
        )
        if json_report_path:
            # shlex.quote so paths with spaces (e.g. macOS
            # "/Users/.../My Project/agents-shipgate-reports/report.json")
            # round-trip through shlex.split unchanged. Without the
            # quote, the advertised command splits at the spaces and
            # apply-patches receives garbage --from arguments
            # (#57 review P2).
            quoted_path = shlex.quote(json_report_path)
            return AgentSummaryAction(
                kind="command",
                command=(
                    f"agents-shipgate apply-patches --from "
                    f"{quoted_path} --confidence high --apply"
                ),
                why=why,
            )
        # No JSON output on this scan: emit an info action that names
        # the canonical pattern so the agent runs apply-patches against
        # *their* report, not the default path. The user-facing reports
        # path is stable enough (`agents-shipgate-reports/report.json`
        # is the default) that we mention it in the why-text, but as
        # documentation, not a literal command the agent might dispatch.
        return AgentSummaryAction(
            kind="info",
            command=None,
            why=(
                f"{why} Re-run the scan with --format json (default path "
                "is agents-shipgate-reports/report.json), then: "
                "agents-shipgate apply-patches --from <report.json> "
                "--confidence high --apply."
            ),
        )

    if verdict == "blocked":
        top = _top_active_finding(active_findings)
        if top is None:
            return None
        return AgentSummaryAction(
            kind="info",
            command=None,
            why=(
                f"Surface {top.check_id} on {top.tool_name or 'agent'} to "
                "the user; release is blocked and no auto-applicable patch "
                "is available."
            ),
        )

    if verdict == "review_required":
        # Evidence-coverage-driven review: no specific finding to walk;
        # the release_decision is asking for human attention because
        # the scan saw only low-confidence/static evidence. Return an
        # info action that names the situation so first_recommended_action
        # is non-null and useful in this case (#57 review P2).
        if (
            evidence_recommended
            and needs_review == 0
            and auto_appliable == 0
        ):
            base = (
                evidence_reason
                or "Static-only scan with low-confidence evidence; "
                "human review recommended."
            )
            return AgentSummaryAction(
                kind="info",
                command=None,
                why=(
                    f"{base} Surface this to the user and discuss whether "
                    "to gather better evidence (e.g. add MCP/OpenAPI "
                    "inputs, eval traces) or accept the static-only "
                    "review posture; no machine-applicable fix is "
                    "available."
                ),
            )

        top = _top_active_finding(active_findings)
        if top is None:
            return None
        # Prefer the action-driven count when there are findings that
        # need human input. Fall back to the severity-driven
        # review_item_count when needs_review is 0 — otherwise the
        # text would read "Walk the 0 review item(s)" even though the
        # release decision has flagged something for review.
        visible = needs_review if needs_review > 0 else review_item_count
        return AgentSummaryAction(
            kind="info",
            command=None,
            why=(
                f"Walk the {visible} review item(s) starting with "
                f"{top.check_id}; release is allowed but the human "
                "reviewer should weigh in."
            ),
        )

    return None


def _top_active_finding(findings: list[Finding]) -> Finding | None:
    """Pick the highest-severity active finding (ties broken by check_id)."""
    if not findings:
        return None
    return min(
        findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 99), f.check_id)
    )


def _derive_from_patches(patches: list) -> tuple[bool, bool, str]:
    """Strict derivation: ``autofix_safe`` is True only when EVERY
    emitted patch is non-manual AND high-confidence. Mixed states fall
    to safe-closed."""
    if not patches:
        return (False, True, "none")

    has_manual = any(isinstance(p, ManualPatch) for p in patches)
    non_manual = [p for p in patches if not isinstance(p, ManualPatch)]
    all_high_confidence_non_manual = (
        not has_manual
        and bool(non_manual)
        and all(getattr(p, "confidence", None) == "high" for p in non_manual)
    )

    # Per the plan §2 derivation rule: kind of the FIRST non-manual
    # patch takes priority (even when ManualPatches are also present).
    # All-manual → "manual". Empty list → "none" (handled above).
    if non_manual:
        suggested_patch_kind = non_manual[0].kind
    else:
        suggested_patch_kind = "manual"

    autofix_safe = all_high_confidence_non_manual
    requires_human_review = not autofix_safe
    return (autofix_safe, requires_human_review, suggested_patch_kind)


def summarize_findings(findings: list[Finding], tools: list[Tool]) -> ReportSummary:
    active = [finding for finding in findings if not finding.suppressed]
    counts = Counter(finding.severity for finding in active)
    suppressed_count = len(findings) - len(active)
    if counts["critical"] > 0:
        status = "release_blockers_detected"
    elif active:
        status = "warnings_detected"
    elif any(tool.extraction_confidence != "high" for tool in tools):
        status = "human_review_recommended"
    else:
        status = "no_release_blockers_detected"
    return ReportSummary(
        status=status,
        critical_count=counts["critical"],
        high_count=counts["high"],
        medium_count=counts["medium"],
        low_count=counts["low"],
        info_count=counts["info"],
        suppressed_count=suppressed_count,
        human_review_recommended=counts["critical"] > 0 or counts["high"] > 0 or status == "human_review_recommended",
        evidence_coverage="mixed" if _has_mixed_evidence(tools) else "static",
    )


def summarize_tool_surface(tools: list[Tool]) -> ToolSurfaceSummary:
    sources = Counter(tool.source_type for tool in tools)
    return ToolSurfaceSummary(
        total_tools=len(tools),
        high_risk_tools=sum(1 for tool in tools if is_high_risk_tool(tool)),
        sources=dict(sorted(sources.items())),
        wildcard_tools=sum(1 for tool in tools if tool.annotations.get("wildcard_tools") is True),
        missing_descriptions=sum(1 for tool in tools if not (tool.description or "").strip()),
    )


def recommended_actions(findings: list[Finding]) -> list[str]:
    active = sorted(
        [finding for finding in findings if not finding.suppressed],
        key=lambda finding: (SEVERITY_ORDER[finding.severity], finding.check_id),
    )
    actions: list[str] = []
    seen: set[str] = set()
    for finding in active:
        if finding.recommendation in seen:
            continue
        actions.append(finding.recommendation)
        seen.add(finding.recommendation)
        if len(actions) >= 8:
            break
    return actions


def tool_inventory(tools: list[Tool]) -> list[dict[str, object]]:
    return [
        {
            "name": tool.name,
            "source_type": tool.source_type,
            "source_ref": tool.source_ref,
            "risk_tags": risk_tags(tool, min_confidence="medium"),
            "risk_tag_confidence": _risk_tag_confidence(tool, min_confidence="medium"),
            "auth_scopes": tool.auth.scopes,
            "owner": tool.owner,
            "confidence": tool.extraction_confidence,
        }
        for tool in sorted(tools, key=lambda item: item.name)
    ]


def build_report(
    *,
    run_id: str,
    manifest: AgentsShipgateManifest,
    agent: dict[str, object],
    environment: dict[str, object],
    tools: list[Tool],
    findings: list[Finding],
    generated_reports: dict[str, str],
    ci_mode: str,
    fail_on: list[Severity] | None = None,
    new_findings_only: bool = False,
    loaded_policy_packs: list[LoadedPolicyPack] | None = None,
    loaded_plugins: list[dict[str, object]] | None = None,
    source_warnings: list[str] | None = None,
    api_surface: dict[str, object] | None = None,
    anthropic_surface: dict[str, object] | None = None,
    frameworks: dict[str, object] | None = None,
    codex_plugin_surface: CodexPluginSurface | None = None,
    baseline: BaselineSummary | None = None,
    manifest_dir: str | None = None,
    tool_surface_facts: ToolSurfaceFacts | None = None,
    tool_surface_diff: ToolSurfaceDiff | None = None,
) -> ReadinessReport:
    report = ReadinessReport(
        run_id=run_id,
        manifest_dir=manifest_dir,
        project=manifest.project.model_dump(exclude_none=True),
        agent=agent,
        environment=environment,
        summary=summarize_findings(findings, tools),
        tool_surface=summarize_tool_surface(tools),
        tool_surface_facts=tool_surface_facts or ToolSurfaceFacts(),
        tool_surface_diff=tool_surface_diff or ToolSurfaceDiff(),
        api_surface=api_surface,
        anthropic_surface=anthropic_surface,
        frameworks=frameworks or {},
        codex_plugin_surface=codex_plugin_surface,
        baseline=baseline,
        findings=findings,
        recommended_actions=recommended_actions(findings),
        generated_reports=generated_reports,
        loaded_policy_packs=loaded_policy_packs or [],
        loaded_plugins=loaded_plugins or [],
        tool_inventory=tool_inventory(tools),
        source_warnings=source_warnings or [],
    )
    report.release_decision = build_release_decision(
        report=report,
        tools=tools,
        ci_mode=ci_mode,
        fail_on=fail_on,
        new_findings_only=new_findings_only,
    )
    # v0.12: agent_summary is the deterministic projection of
    # release_decision + per-finding agent_action. Built last so it
    # picks up everything else. The JSON report path is threaded in
    # so first_recommended_action.command names the real on-disk
    # path the user just wrote (not the default — see #57 review P1.1).
    report.agent_summary = build_agent_summary(
        findings=findings,
        release_decision=report.release_decision,
        json_report_path=generated_reports.get("json"),
    )
    return report


def _matching_suppression(
    finding: Finding, suppressions: list[SuppressionConfig]
) -> SuppressionConfig | None:
    for suppression in suppressions:
        if not expands_to_check_id(suppression.check_id, finding.check_id):
            continue
        if not suppression.tool:
            return suppression
        possible_tools = {
            finding.tool_name,
            finding.tool_id,
            finding.tool_id.replace("tool:", "") if finding.tool_id else None,
        }
        if suppression.tool in possible_tools:
            return suppression
    return None


def _severity_override_for_check(
    check_id: str, overrides: dict[str, Severity]
) -> Severity | None:
    if override := overrides.get(check_id):
        return override
    for configured_check_id, override in overrides.items():
        if expands_to_check_id(configured_check_id, check_id):
            return override
    return None


def finding_fingerprint(finding: Finding) -> str:
    identity = {
        "check_id": finding.check_id,
        "tool_name": finding.tool_name,
        "evidence": _canonicalize_for_fingerprint(finding.evidence),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"fp_{digest}"


def _canonicalize_for_fingerprint(value):
    if isinstance(value, dict):
        return {
            key: _canonicalize_for_fingerprint(value[key])
            for key in sorted(value)
            if key not in FINGERPRINT_EXCLUDED_EVIDENCE_KEYS
        }
    if isinstance(value, list):
        items = [_canonicalize_for_fingerprint(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        )
    if isinstance(value, tuple | set):
        return _canonicalize_for_fingerprint(list(value))
    return value


def _collision_discriminator(finding: Finding) -> str:
    identity = {
        "agent_id": finding.agent_id,
        "category": finding.category,
        "check_id": finding.check_id,
        "confidence": finding.confidence,
        "recommendation": finding.recommendation,
        "source": finding.source.model_dump(mode="json") if finding.source else None,
        "title": finding.title,
        "tool_id": finding.tool_id,
        "tool_name": finding.tool_name,
    }
    digest = hashlib.sha256(
        json.dumps(
            _canonicalize_for_fingerprint(identity),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:8]
    return digest


def _risk_tag_confidence(tool: Tool, min_confidence: str) -> dict[str, str]:
    threshold = confidence_rank(min_confidence)
    by_tag: dict[str, str] = {}
    for hint in tool.risk_hints:
        if confidence_rank(hint.confidence) < threshold:
            continue
        current = by_tag.get(hint.tag)
        if current is None or confidence_rank(hint.confidence) > confidence_rank(current):
            by_tag[hint.tag] = hint.confidence
    return dict(sorted(by_tag.items()))


def _has_mixed_evidence(tools: list[Tool]) -> bool:
    return any(
        tool.source_type == "sdk_function" or tool.extraction_confidence != "high"
        for tool in tools
    )
