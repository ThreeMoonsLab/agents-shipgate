from __future__ import annotations

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    OpenAIApiArtifacts,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.risk_hints import (
    risk_tags,
)


def run(context: ScanContext):
    findings = []
    policy_tools = set(context.manifest.policies.idempotency_tools())
    api_artifacts = context.artifact("openai_api", OpenAIApiArtifacts)
    if api_artifacts:
        policy_tools.update(api_artifacts.idempotency_tools())
    anthropic_artifacts = context.artifact("anthropic_api", AnthropicArtifacts)
    if anthropic_artifacts:
        policy_tools.update(anthropic_artifacts.idempotency_tools())
    for tool in context.tools:
        if not _needs_idempotency(tool):
            continue
        if tool.name in policy_tools or tool.annotations.get("idempotentHint") is True:
            continue
        if any(parameter.name == "idempotency_key" for parameter in tool.parameters):
            continue
        retry_known = bool(tool.annotations.get("retryPolicy"))
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-SIDEFX-IDEMPOTENCY-MISSING",
                title=f"{tool.name} lacks idempotency evidence",
                severity="critical" if retry_known else "high",
                category="side_effects",
                evidence={
                    "risk_tags": risk_tags(tool, min_confidence="medium"),
                    "retry_policy_known": retry_known,
                },
                confidence="high" if retry_known else "medium",
                recommendation=f"Add an idempotency key, idempotent annotation, or declared idempotency policy for {tool.name}.",
                context=context,
                provenance_kind="static_declaration",
                policy_evidence_pointer="/policies/require_idempotency_for_tools",
            )
        )
    return findings


def _needs_idempotency(tool) -> bool:
    assessment = getattr(tool, "semantic_assessment", None)
    if assessment is None:
        # Compatibility for direct check fixtures.  Live scans always attach
        # semantic assessments before check execution.
        return "financial_action" in risk_tags(tool, min_confidence="medium")
    return any(
        claim.value == "financial_write"
        and claim.confidence == "high"
        and claim.provenance_kind not in {"keyword_heuristic", "regex_heuristic"}
        for claim in assessment.effect.claims
    ) or (
        assessment.effect.status in {"declared", "structural"}
        and assessment.conservative_effect == "financial_write"
    )
