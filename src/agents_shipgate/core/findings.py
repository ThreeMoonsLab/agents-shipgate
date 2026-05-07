from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict

from agents_shipgate.ci.release_decision import build_release_decision
from agents_shipgate.config.schema import AgentsShipgateManifest, SuppressionConfig
from agents_shipgate.core.check_ids import expands_to_check_id
from agents_shipgate.core.models import (
    BaselineSummary,
    CheckMetadata,
    Finding,
    LoadedPolicyPack,
    ReadinessReport,
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
FINGERPRINT_EXCLUDED_EVIDENCE_KEYS = {"default_severity"}


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
    return findings


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
