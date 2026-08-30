from __future__ import annotations

from collections.abc import Iterable
from itertools import product

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.capabilities import capability_fact_from_action_fact
from agents_shipgate.core.capability_delta import (
    CapabilityDeltaRow,
    CapabilityFactContext,
    diff_capability_fact_sets,
)
from agents_shipgate.core.capability_lattice import classify_tool_permission
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import SemanticClaim, Tool
from agents_shipgate.core.lenses.tool_surface import (
    ToolSurfaceDiffReference,
    tool_annotation_hash,
)
from agents_shipgate.core.semantic_assessment import (
    acknowledged_effect_claim_ids,
    assess_tool_semantics,
)
from agents_shipgate.schemas.capabilities import CapabilityFactV1
from agents_shipgate.schemas.common import Confidence, ProvenanceKind, confidence_rank
from agents_shipgate.schemas.report import Finding
from agents_shipgate.schemas.semantic import SemanticClaimEvidence
from agents_shipgate.schemas.surfaces import ActionFact, ActionSurfaceFacts

MCP_SOURCE_TYPES = frozenset(
    {
        "mcp",
        "codex_config_mcp",
        "codex_plugin_mcp_inventory",
        "n8n_mcp_client_tool",
        "conductor_mcp_call",
    }
)
CapabilityKey = tuple[str | None, str]
EffectClaim = SemanticClaim | SemanticClaimEvidence

_INDEPENDENT_EFFECT_BASES = frozenset(
    {
        "protocol_structure",
        "typed_provider_fact",
        "structural_scope",
        "inferred_keyword",
        "inferred_regex",
    }
)
_EFFECT_BASIS_LABELS = {
    "protocol_structure": "protocol structure",
    "typed_provider_fact": "typed provider evidence",
    "structural_scope": "declared scope evidence",
    "inferred_keyword": "inferred keyword evidence",
    "inferred_regex": "inferred pattern evidence",
}
_ABSENT = object()


def run(context: ScanContext) -> list[Finding]:
    mcp_tools = [tool for tool in context.tools if _is_mcp_tool(tool)]
    if not mcp_tools:
        return []

    findings: list[Finding] = []
    fact_by_tool = _current_fact_by_tool(_mcp_capability_facts(context.capability_facts))
    tool_by_key = {_tool_key(tool): tool for tool in mcp_tools}

    findings.extend(_env_secret_findings(context, mcp_tools, fact_by_tool))
    findings.extend(_auto_approve_findings(context, mcp_tools, fact_by_tool))
    findings.extend(
        _annotation_contradiction_findings(
            context,
            mcp_tools,
            fact_by_tool,
        )
    )

    diff_available = (
        context.diff_reference is not None and context.diff_reference.action_facts is not None
    )
    if not diff_available:
        findings.extend(
            _unknown_schema_findings(
                context,
                mcp_tools,
                fact_by_tool,
                added_ids=set(),
                changed_ids=set(),
                diff_available=False,
            )
        )
        return _dedupe(findings)

    current_contexts = _mcp_capability_contexts(context.action_surface_facts)
    if not fact_by_tool:
        fact_by_tool = _current_fact_by_tool([ctx.fact for ctx in current_contexts])
    base_contexts = (
        _mcp_capability_contexts(context.diff_reference.action_facts) if diff_available else []
    )
    diff = diff_capability_fact_sets(base_contexts, current_contexts)
    added_ids = {ctx.fact.id for ctx in diff.added}
    changed_rows = [*diff.reidentified, *diff.changed]
    changed_ids = {row.after.id for row in changed_rows}
    broadened_rows = [
        row for row in changed_rows if row.semantic_direction in {"broadened", "mixed", "unknown"}
    ]

    findings.extend(
        _unknown_schema_findings(
            context,
            mcp_tools,
            fact_by_tool,
            added_ids=added_ids,
            changed_ids=changed_ids,
            diff_available=diff_available,
        )
    )
    findings.extend(
        _permission_expansion_findings(
            context,
            broadened_rows,
            tool_by_key=tool_by_key,
        )
    )
    if diff_available:
        findings.extend(
            _read_only_added_findings(
                context,
                diff.added,
                tool_by_key=tool_by_key,
            )
        )
    return _dedupe(findings)


def _annotation_contradiction_findings(
    context: ScanContext,
    tools: list[Tool],
    fact_by_tool: dict[CapabilityKey, CapabilityFactV1],
) -> list[Finding]:
    findings: list[Finding] = []
    for tool in tools:
        claims = _independent_effect_claims(
            (tool.semantic_assessment or assess_tool_semantics(tool)).effect.claims
        )
        contradictions: list[dict[str, object]] = []
        used_claims: list[EffectClaim] = []

        if tool.annotations.get("readOnlyHint") is True:
            read_only_conflicts = [claim for claim in claims if claim.value != "read"]
            if read_only_conflicts:
                contradictions.append(
                    {
                        "annotation": "readOnlyHint",
                        "published_value": True,
                        "contradicting_effects": sorted(
                            {claim.value for claim in read_only_conflicts}
                        ),
                    }
                )
                used_claims.extend(read_only_conflicts)

        # MCP defaults destructiveHint to true. Only an explicit false value
        # narrows what a client is told, and only destructive evidence
        # contradicts that assertion; ordinary write evidence does not.
        if tool.annotations.get("destructiveHint") is False:
            destructive_conflicts = [claim for claim in claims if claim.value == "destructive"]
            if destructive_conflicts:
                contradictions.append(
                    {
                        "annotation": "destructiveHint",
                        "published_value": False,
                        "contradicting_effects": ["destructive"],
                    }
                )
                used_claims.extend(destructive_conflicts)

        if not contradictions:
            continue

        used_claims = _dedupe_claims(used_claims)
        published_annotations = {
            str(item["annotation"]): item["published_value"] for item in contradictions
        }
        annotation_changes = _unchanged_evidence_annotation_changes(
            tool,
            claims,
            published_annotations,
            context.diff_reference,
        )
        bases = sorted({claim.basis for claim in used_claims})
        consequence = (
            "MCP clients that honor these annotations may show too little "
            "confirmation before a side-effecting call."
        )
        finding = tool_finding(
            tool=tool,
            check_id="SHIP-MCP-ANNOTATION-CONTRADICTION",
            title="MCP narrowing annotation contradicts side-effect evidence",
            severity="high",
            category="mcp_permissions",
            evidence={
                "form": "delta" if annotation_changes else "static",
                "published_annotations": published_annotations,
                "contradictions": contradictions,
                "independent_evidence": [_claim_evidence(claim) for claim in used_claims],
                "annotation_changes": annotation_changes,
                "independent_evidence_unchanged": bool(annotation_changes),
                "client_consequence": consequence,
            },
            confidence=_strongest_confidence(used_claims),
            recommendation=(
                f"Review {', '.join(f'{name}: {str(value).lower()}' for name, value in published_annotations.items())} "
                f"against {_basis_phrase(bases)}; {consequence}"
            ),
            context=context,
            provenance_kind=_finding_provenance(used_claims),
            capability_refs=_capability_ref(tool, fact_by_tool),
        )
        findings.append(finding)
    return findings


def _independent_effect_claims(claims: Iterable[EffectClaim]) -> list[EffectClaim]:
    claim_list = list(claims)
    overridden_claim_ids = acknowledged_effect_claim_ids(claim_list)
    return [
        claim
        for claim in claim_list
        if claim.value != "read"
        and claim.basis in _INDEPENDENT_EFFECT_BASES
        and claim.source != "mcp_annotation"
        and claim.claim_id not in overridden_claim_ids
    ]


def _dedupe_claims(claims: Iterable[EffectClaim]) -> list[EffectClaim]:
    by_key = {
        (
            claim.value,
            claim.confidence,
            claim.basis,
            claim.source,
            repr(sorted(claim.evidence.items())),
        ): claim
        for claim in claims
    }
    return [by_key[key] for key in sorted(by_key)]


def _claim_evidence(claim: EffectClaim) -> dict[str, object]:
    return {
        "effect": claim.value,
        "confidence": claim.confidence,
        "basis": claim.basis,
        "source": claim.source,
        "source_pointer": claim.source_pointer,
        "details": claim.evidence,
    }


def _unchanged_evidence_annotation_changes(
    tool: Tool,
    current_claims: list[EffectClaim],
    contradicted_annotations: dict[str, object],
    reference: ToolSurfaceDiffReference | None,
) -> list[dict[str, object]]:
    if reference is None or reference.action_facts is None or reference.facts is None:
        return []
    base_action = next(
        (action for action in reference.action_facts.actions if action.tool_id == tool.id),
        None,
    )
    if base_action is None:
        matches = [
            action
            for action in reference.action_facts.actions
            if action.source_id == tool.source_id and action.tool_name == tool.name
        ]
        if len(matches) != 1:
            return []
        base_action = matches[0]
    if base_action.semantic_assessment is None:
        return []

    base_claims = _independent_effect_claims(base_action.semantic_assessment.effect.claims)
    if _claim_signature(base_claims) != _claim_signature(current_claims):
        return []

    base_tool = next(
        (fact for fact in reference.facts.tools if fact.tool_id == tool.id),
        None,
    )
    if base_tool is None:
        matches = [
            fact
            for fact in reference.facts.tools
            if fact.source_id == tool.source_id and fact.name == tool.name
        ]
        if len(matches) != 1:
            return []
        base_tool = matches[0]
    if base_tool.hashes.annotations is None:
        return []

    # Tool-surface facts already bind the complete base annotation map by
    # hash. Rebuild only the tiny set of possible prior values for the two
    # explicit narrowing hints and accept a delta only when one candidate
    # reproduces that hash exactly. This proves the hint flip without adding
    # raw annotations to the report schema or guessing from a changed hash.
    dimensions: list[tuple[str, object, tuple[object, ...]]] = []
    if "readOnlyHint" in contradicted_annotations:
        dimensions.append(("readOnlyHint", True, (True, False, _ABSENT)))
    if "destructiveHint" in contradicted_annotations:
        dimensions.append(("destructiveHint", False, (False, True, _ABSENT)))
    for before_values in product(*(options for _, _, options in dimensions)):
        candidate = dict(tool.annotations)
        changes: list[dict[str, object]] = []
        for (name, after, _), before in zip(dimensions, before_values, strict=True):
            if before is _ABSENT:
                candidate.pop(name, None)
                rendered_before: object = "absent"
            else:
                candidate[name] = before
                rendered_before = before
            if before != after:
                changes.append(
                    {
                        "annotation": name,
                        "before": rendered_before,
                        "after": after,
                    }
                )
        if changes and tool_annotation_hash(candidate) == base_tool.hashes.annotations:
            return changes
    return []


def _claim_signature(claims: Iterable[EffectClaim]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        sorted(
            (
                claim.value,
                claim.confidence,
                claim.basis,
                claim.source,
                repr(sorted(claim.evidence.items())),
            )
            for claim in claims
        )
    )


def _strongest_confidence(claims: Iterable[EffectClaim]) -> Confidence:
    return max(
        (claim.confidence for claim in claims),
        key=confidence_rank,
        default="low",
    )


def _finding_provenance(claims: Iterable[EffectClaim]) -> ProvenanceKind:
    ordered = sorted(
        claims,
        key=lambda claim: (
            claim.provenance_kind in {"keyword_heuristic", "regex_heuristic"},
            -confidence_rank(claim.confidence),
            claim.provenance_kind,
        ),
    )
    return ordered[0].provenance_kind if ordered else "static_declaration"


def _basis_phrase(bases: list[str]) -> str:
    labels = [_EFFECT_BASIS_LABELS.get(basis, basis.replace("_", " ")) for basis in bases]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def _env_secret_findings(
    context: ScanContext,
    tools: list[Tool],
    fact_by_tool: dict[CapabilityKey, CapabilityFactV1],
) -> list[Finding]:
    findings: list[Finding] = []
    seen_servers: set[tuple[str | None, tuple[str, ...]]] = set()
    for tool in tools:
        secret_names = tuple(
            str(item) for item in tool.annotations.get("mcp_env_secret_names") or []
        )
        if not secret_names:
            continue
        key = (tool.source_id, secret_names)
        if key in seen_servers:
            continue
        seen_servers.add(key)
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-MCP-ENV-SECRET-PASSTHROUGH",
                title="MCP server passes through secret environment variables",
                severity="high",
                category="mcp_permissions",
                evidence={
                    "source_id": tool.source_id,
                    "env_secret_names": list(secret_names),
                },
                confidence="high",
                recommendation=(
                    "Have a human review the MCP server secret boundary or replace "
                    "secret pass-through with a narrower credential mechanism."
                ),
                context=context,
                provenance_kind="static_declaration",
                capability_refs=_capability_ref(tool, fact_by_tool),
            )
        )
    return findings


def _auto_approve_findings(
    context: ScanContext,
    tools: list[Tool],
    fact_by_tool: dict[CapabilityKey, CapabilityFactV1],
) -> list[Finding]:
    findings: list[Finding] = []
    for tool in tools:
        approval_mode = str(tool.annotations.get("mcp_approval_mode") or "").lower()
        if approval_mode != "approve":
            continue
        profile = classify_tool_permission(tool)
        if not any(
            item in {"write", "destructive", "external", "financial", "production"}
            for item in profile.classes
        ) or not _has_structural_side_effect(tool):
            continue
        finding = tool_finding(
            tool=tool,
            check_id="SHIP-MCP-AUTO-APPROVE-SIDE-EFFECT",
            title="MCP side-effecting tool is auto-approved",
            severity="critical",
            category="mcp_permissions",
            evidence={
                "permission_classes": list(profile.classes),
                "risk_score": profile.risk_score,
                "approval_mode": approval_mode,
                "reasons": list(profile.reasons),
            },
            confidence="high",
            recommendation="Do not auto-approve write, destructive, external, financial, or production MCP tools.",
            context=context,
            provenance_kind="static_declaration",
            capability_refs=_capability_ref(tool, fact_by_tool),
        )
        finding.blocks_release = True
        findings.append(finding)
    return findings


def _unknown_schema_findings(
    context: ScanContext,
    tools: list[Tool],
    fact_by_tool: dict[CapabilityKey, CapabilityFactV1],
    *,
    added_ids: set[str],
    changed_ids: set[str],
    diff_available: bool,
) -> list[Finding]:
    findings: list[Finding] = []
    for tool in tools:
        if tool.annotations.get("mcp_unknown_schema") is not True:
            continue
        profile = classify_tool_permission(tool)
        if str(tool.annotations.get("mcp_approval_mode") or "").lower() == "approve" and any(
            item in {"write", "destructive", "external", "financial", "production"}
            for item in profile.classes
        ):
            continue
        fact = fact_by_tool.get(_tool_key(tool))
        current = fact.id if fact else None
        if diff_available and current not in added_ids | changed_ids:
            continue
        if not diff_available and tool.source_type != "codex_config_mcp":
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-MCP-UNKNOWN-TOOL-SCHEMA",
                title="MCP tool side effect cannot be proven from static schema",
                severity="high",
                category="mcp_permissions",
                evidence={
                    "source_id": tool.source_id,
                    "wildcard": bool(tool.annotations.get("wildcard_tools")),
                    "approval_mode": tool.annotations.get("mcp_approval_mode"),
                },
                confidence="high",
                recommendation=(
                    "Provide an explicit MCP tool inventory/schema or keep the "
                    "server behind human review."
                ),
                context=context,
                provenance_kind="static_declaration",
                capability_refs=_capability_ref(tool, fact_by_tool),
            )
        )
    return findings


def _permission_expansion_findings(
    context: ScanContext,
    rows: list[CapabilityDeltaRow],
    *,
    tool_by_key: dict[CapabilityKey, Tool],
) -> list[Finding]:
    findings: list[Finding] = []
    for row in rows:
        tool = tool_by_key.get(_fact_key(row.after))
        if tool is None:
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-MCP-PERMISSION-EXPANDED",
                title="MCP capability permissions expanded",
                severity="high",
                category="mcp_permissions",
                evidence={
                    "capability_id": row.after.id,
                    "semantic_direction": row.semantic_direction,
                    "semantic_changes": [
                        change.model_dump(mode="json") for change in row.semantic_changes
                    ],
                },
                confidence="high",
                recommendation=(
                    "Review the expanded MCP capability; narrow tools/scopes or "
                    "add explicit approval evidence."
                ),
                context=context,
                provenance_kind="static_declaration",
                capability_refs=[row.after.id],
            )
        )
    return findings


def _read_only_added_findings(
    context: ScanContext,
    added: list[CapabilityFactContext],
    *,
    tool_by_key: dict[CapabilityKey, Tool],
) -> list[Finding]:
    findings: list[Finding] = []
    for ctx in added:
        tool = tool_by_key.get(_fact_key(ctx.fact))
        if tool is None or tool.annotations.get("mcp_local_documentation") is not True:
            continue
        profile = classify_tool_permission(tool)
        if not profile.is_read_only:
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-MCP-READONLY-SERVER-ADDED",
                title="Read-only local documentation MCP server added",
                severity="low",
                category="mcp_permissions",
                evidence={
                    "capability_id": ctx.fact.id,
                    "permission_classes": list(profile.classes),
                    "risk_score": profile.risk_score,
                },
                confidence="high",
                recommendation=(
                    "Keep the MCP server read-only and local; review if it gains "
                    "write, network, or secret access."
                ),
                context=context,
                provenance_kind="static_declaration",
                capability_refs=[ctx.fact.id],
            )
        )
    return findings


def _mcp_capability_contexts(facts: ActionSurfaceFacts | None) -> list[CapabilityFactContext]:
    if facts is None:
        return []
    contexts: list[CapabilityFactContext] = []
    for action in facts.actions:
        if action.source_type not in MCP_SOURCE_TYPES:
            continue
        contexts.append(_context_from_action(action))
    return contexts


def _context_from_action(action: ActionFact) -> CapabilityFactContext:
    return CapabilityFactContext(
        fact=capability_fact_from_action_fact(action),
        action_id=action.action_id,
        input_fields=tuple(action.input_fields),
        required_input_fields=tuple(action.required_input_fields),
    )


def _mcp_capability_facts(facts: list[CapabilityFactV1]) -> list[CapabilityFactV1]:
    return [fact for fact in facts if fact.evidence.source_type in MCP_SOURCE_TYPES]


def _current_fact_by_tool(
    facts: list[CapabilityFactV1],
) -> dict[CapabilityKey, CapabilityFactV1]:
    return {_fact_key(fact): fact for fact in facts}


def _capability_ref(tool: Tool, fact_by_tool: dict[CapabilityKey, CapabilityFactV1]) -> list[str]:
    fact = fact_by_tool.get(_tool_key(tool))
    return [fact.id] if fact else []


def _tool_key(tool: Tool) -> CapabilityKey:
    return (tool.source_id, tool.name)


def _fact_key(fact: CapabilityFactV1) -> CapabilityKey:
    return (fact.evidence.source_id, fact.identity.tool_name)


def _is_mcp_tool(tool: Tool) -> bool:
    if tool.source_type in MCP_SOURCE_TYPES:
        return True
    return tool.annotations.get("mcp_server") is True


def _has_structural_side_effect(tool: Tool) -> bool:
    assessment = tool.semantic_assessment
    if assessment is None:
        return False
    return any(
        claim.value != "read"
        and claim.confidence == "high"
        and claim.provenance_kind not in {"keyword_heuristic", "regex_heuristic"}
        for claim in assessment.effect.claims
    )


def _dedupe(findings: list[Finding]) -> list[Finding]:
    by_key = {
        (
            finding.check_id,
            finding.tool_name,
            repr(sorted(finding.evidence.items())),
        ): finding
        for finding in findings
    }
    return [by_key[key] for key in sorted(by_key)]
