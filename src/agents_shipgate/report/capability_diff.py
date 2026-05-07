"""Build deterministic capability/intent diff facts for report output."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass

from agents_shipgate.core.finding_refs import finding_tool_names
from agents_shipgate.core.models import (
    CapabilityFact,
    CapabilityIncludedReason,
    DeclaredIntention,
    DeclaredIntentionKind,
    FailPolicy,
    Finding,
    Misalignment,
    MisalignmentKind,
    ReadinessReport,
    ReleaseConsequence,
    SuggestedScenario,
    SuggestedScenarioType,
    Tool,
)
from agents_shipgate.core.risk_hints import is_high_risk_tool, risk_tags

RISK_TAG_PRIORITY = (
    "financial_action",
    "destructive",
    "external_write",
    "customer_communication",
    "sensitive_data_access",
    "infrastructure_change",
    "code_execution",
    "write",
    "read_only",
)

# P0.1 intentionally covers the common release-review vocabulary first.
# Additional aliases for infrastructure/code-exec/data-access intents can
# be added without changing the report contract.
INTENT_TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "billing": ("financial_action",),
    "cancel": ("destructive",),
    "delete": ("destructive",),
    "email": ("external_write", "customer_communication"),
    "external": ("external_write", "customer_communication"),
    "message": ("external_write", "customer_communication"),
    "messages": ("external_write", "customer_communication"),
    "payment": ("financial_action",),
    "payments": ("financial_action",),
    "refund": ("financial_action",),
    "refunds": ("financial_action",),
    "reimbursement": ("financial_action",),
    "reimbursements": ("financial_action",),
    "remove": ("destructive",),
    "send": ("external_write", "customer_communication"),
    "sms": ("external_write", "customer_communication"),
}
NEGATION_TOKENS = {"without", "no", "not", "never", "non"}

RELEASE_RELEVANT_CATEGORIES = {
    "adk",
    "api",
    "auth",
    "crewai",
    "documentation",
    "evidence",
    "inventory",
    "langchain",
    "manifest",
    "policy",
    "schema",
    "scope",
    "security",
    "side_effects",
}
RELEASE_RELEVANT_SEVERITIES = {"critical", "high", "medium"}
INTENTION_KIND_ORDER = {
    "declared_purpose": 0,
    "prohibited_action": 1,
    "instruction_preview": 2,
}
SEVERITY_SORT = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

MISSING_CONTROL_CHECKS = {
    "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE",
    "SHIP-ADK-MCP-TOOLSET-UNFILTERED",
    "SHIP-API-RETRY-WITHOUT-IDEMPOTENCY",
    "SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
    "SHIP-INVENTORY-NOT-ENUMERABLE",
    "SHIP-INVENTORY-WILDCARD-TOOLS",
    "SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
}


@dataclass(frozen=True)
class DiffSpec:
    kind: MisalignmentKind
    policy_requirement: str
    release_implication: str
    scenario_type: SuggestedScenarioType | None


@dataclass(frozen=True)
class MisalignmentRecord:
    misalignment: Misalignment
    scenario_type: SuggestedScenarioType | None


CHECK_DIFF_MAP: dict[str, DiffSpec] = {
    "SHIP-INVENTORY-NOT-ENUMERABLE": DiffSpec(
        kind="control_missing",
        policy_requirement="Tool surface must be statically enumerable before release review.",
        release_implication="Release review cannot verify the agent's actual tool surface.",
        scenario_type="wildcard_inventory",
    ),
    "SHIP-INVENTORY-WILDCARD-TOOLS": DiffSpec(
        kind="control_missing",
        policy_requirement="Tool exposure must use an explicit reviewed allowlist.",
        release_implication="Release review cannot bound which tools may be available at runtime.",
        scenario_type="wildcard_inventory",
    ),
    "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE": DiffSpec(
        kind="control_missing",
        policy_requirement="Framework toolsets must be statically enumerable before release review.",
        release_implication="Release review cannot verify the Google ADK toolset surface.",
        scenario_type="wildcard_inventory",
    ),
    "SHIP-ADK-MCP-TOOLSET-UNFILTERED": DiffSpec(
        kind="control_missing",
        policy_requirement="Framework MCP toolsets must use static tool filters or explicit inventory.",
        release_implication="Release review cannot bound the Google ADK MCP toolset surface.",
        scenario_type="wildcard_inventory",
    ),
    "SHIP-ADK-FUNCTION-TOOL-METADATA-MISSING": DiffSpec(
        kind="control_missing",
        policy_requirement="Framework function tools must expose static metadata for review.",
        release_implication="Release reviewers cannot verify the ADK function tool boundary.",
        scenario_type="schema_boundary",
    ),
    "SHIP-ADK-LONGRUNNING-CONTRACT-MISSING": DiffSpec(
        kind="control_missing",
        policy_requirement="Long-running framework tools need operation status and progress contracts.",
        release_implication="Release review cannot verify continuation and retry behavior.",
        scenario_type="schema_boundary",
    ),
    "SHIP-ADK-GUARDRAIL-EVIDENCE-MISSING": DiffSpec(
        kind="control_missing",
        policy_requirement="High-risk framework tools must include static guardrail evidence.",
        release_implication="Release lacks deterministic evidence for high-risk framework controls.",
        scenario_type="test_case_coverage",
    ),
    "SHIP-ADK-EVAL-COVERAGE-MISSING": DiffSpec(
        kind="control_missing",
        policy_requirement="Production-like framework releases should declare eval coverage.",
        release_implication="Release lacks validation evidence for production-like ADK behavior.",
        scenario_type="test_case_coverage",
    ),
    "SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE": DiffSpec(
        kind="control_missing",
        policy_requirement="Framework tool surfaces must be statically enumerable before release review.",
        release_implication="Release review cannot verify the LangChain tool surface.",
        scenario_type="wildcard_inventory",
    ),
    "SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE": DiffSpec(
        kind="control_missing",
        policy_requirement="Framework tool surfaces must be statically enumerable before release review.",
        release_implication="Release review cannot verify the CrewAI tool surface.",
        scenario_type="wildcard_inventory",
    ),
    "SHIP-SCHEMA-BROAD-FREE-TEXT": DiffSpec(
        kind="control_missing",
        policy_requirement="Action-like tool inputs must constrain high-blast-radius fields.",
        release_implication="Release reviewers cannot bound the operation payload safely.",
        scenario_type="schema_boundary",
    ),
    "SHIP-SCHEMA-MISSING-BOUNDS": DiffSpec(
        kind="control_missing",
        policy_requirement="Risky numeric parameters must declare a maximum or equivalent limit.",
        release_implication="Release reviewers cannot verify blast-radius limits.",
        scenario_type="schema_boundary",
    ),
    "SHIP-SCHEMA-FREEFORM-OUTPUT": DiffSpec(
        kind="control_missing",
        policy_requirement="Model-consumed tool outputs should be structured.",
        release_implication="Downstream model behavior may depend on unstructured tool output.",
        scenario_type="schema_boundary",
    ),
    "SHIP-AUTH-MISSING-SCOPE": DiffSpec(
        kind="scope_drift",
        policy_requirement="Scope-requiring tools must declare operation-specific auth scopes.",
        release_implication="Release reviewers cannot assess least privilege.",
        scenario_type="least_privilege_scope",
    ),
    "SHIP-AUTH-MANIFEST-BROAD-SCOPE": DiffSpec(
        kind="scope_drift",
        policy_requirement="Manifest permissions should declare narrow release scopes.",
        release_implication="Release may grant broader access than the reviewed capability needs.",
        scenario_type="least_privilege_scope",
    ),
    "SHIP-AUTH-TOOL-BROAD-SCOPE": DiffSpec(
        kind="scope_drift",
        policy_requirement="Tool auth metadata should use narrow operation-specific scopes.",
        release_implication="The tool may require broader credentials than review can justify.",
        scenario_type="least_privilege_scope",
    ),
    "SHIP-AUTH-SCOPE-COVERAGE-MISSING": DiffSpec(
        kind="scope_drift",
        policy_requirement="Manifest permissions must cover tool-required scopes.",
        release_implication="The manifest does not describe the actual permissions needed.",
        scenario_type="least_privilege_scope",
    ),
    "SHIP-MANIFEST-UNUSED-SCOPE": DiffSpec(
        kind="scope_drift",
        policy_requirement="Manifest permissions should only include scopes used by loaded tools.",
        release_implication="Release may include stale or unnecessary permission grants.",
        scenario_type="least_privilege_scope",
    ),
    "SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE": DiffSpec(
        kind="intent_mismatch",
        policy_requirement="Declared purpose must match the attached tool surface.",
        release_implication="The release scope is broader than the declared agent intent.",
        scenario_type="prompt_scope_alignment",
    ),
    "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT": DiffSpec(
        kind="prohibited_action_present",
        policy_requirement="Prohibited actions must not be contradicted by enabled capabilities.",
        release_implication="The tool surface appears to enable behavior the manifest prohibits.",
        scenario_type="prohibited_action",
    ),
    "SHIP-POLICY-APPROVAL-MISSING": DiffSpec(
        kind="policy_gap",
        policy_requirement="High-risk tools must have a declared approval policy.",
        release_implication="Release is blocked until approval is declared or the tool is removed.",
        scenario_type="approval",
    ),
    "SHIP-POLICY-CONFIRMATION-MISSING": DiffSpec(
        kind="policy_gap",
        policy_requirement="Destructive, external, or customer actions require confirmation.",
        release_implication="Release review must verify explicit user confirmation before shipping.",
        scenario_type="confirmation",
    ),
    "SHIP-SIDEFX-IDEMPOTENCY-MISSING": DiffSpec(
        kind="control_missing",
        policy_requirement="Risky write tools need idempotency evidence before retryable release.",
        release_implication="Retries could duplicate financial, destructive, or external effects.",
        scenario_type="idempotency_retry",
    ),
    "SHIP-API-FUNCTION-SCHEMA-STRICTNESS": DiffSpec(
        kind="control_missing",
        policy_requirement="API function schemas must be strict enough for reliable tool calls.",
        release_implication="The model may send ambiguous or overbroad tool arguments.",
        scenario_type="schema_boundary",
    ),
    "SHIP-API-STRUCTURED-OUTPUT-READINESS": DiffSpec(
        kind="control_missing",
        policy_requirement="API outputs need structured success, failure, and review states.",
        release_implication="Downstream release behavior may depend on under-specified output.",
        scenario_type="schema_boundary",
    ),
    "SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH": DiffSpec(
        kind="intent_mismatch",
        policy_requirement="Prompt scope must align with enabled high-risk tools.",
        release_implication="The agent instructions and actual tool surface disagree.",
        scenario_type="prompt_scope_alignment",
    ),
    "SHIP-API-TEST-CASES-MISSING": DiffSpec(
        kind="control_missing",
        policy_requirement="High-risk API tool-call flows should include declared test cases.",
        release_implication="Release lacks validation evidence for high-risk tool paths.",
        scenario_type="test_case_coverage",
    ),
    "SHIP-API-TRACE-APPROVAL-MISSING": DiffSpec(
        kind="policy_gap",
        policy_requirement="Trace evidence for approval-controlled tools must show approval.",
        release_implication="Observed sample evidence contradicts the approval expectation.",
        scenario_type="approval",
    ),
    "SHIP-API-TRACE-CONFIRMATION-MISSING": DiffSpec(
        kind="policy_gap",
        policy_requirement="Trace evidence for confirmation-controlled tools must show confirmation.",
        release_implication="Observed sample evidence contradicts the confirmation expectation.",
        scenario_type="confirmation",
    ),
    "SHIP-EVIDENCE-APPROVAL-TRACE-MISSING": DiffSpec(
        kind="policy_gap",
        policy_requirement="Local HITL evidence must show approval before approval-controlled tool calls.",
        release_implication="Reviewers do not have local approval trace evidence for the approval-controlled action.",
        scenario_type="approval",
    ),
    "SHIP-EVIDENCE-OVERRIDE-REASON-MISSING": DiffSpec(
        kind="control_missing",
        policy_requirement="Local HITL override evidence must record non-empty reasons.",
        release_implication="Reviewers do not have local reason evidence for override, bypass, or auto-approval events.",
        scenario_type=None,
    ),
    "SHIP-EVIDENCE-HIGH-RISK-EXCLUSION-MISSING": DiffSpec(
        kind="control_missing",
        policy_requirement="High-risk tools with approval policy must have local auto-approval exclusion evidence.",
        release_implication="Reviewers do not have local evidence that the high-risk tool is excluded from auto-approval.",
        scenario_type=None,
    ),
    "SHIP-EVIDENCE-HITL-PROMOTION-CRITERIA-MISSING": DiffSpec(
        kind="control_missing",
        policy_requirement="Limited auto-approval review posture requires local promotion criteria evidence.",
        release_implication="Reviewers do not have the local criteria evidence needed to evaluate that review posture.",
        scenario_type=None,
    ),
}


def apply_capability_diff(report: ReadinessReport, tools: list[Tool]) -> None:
    capability_facts, declared_intentions, records = _build_diff_records(report, tools)
    misalignments = [record.misalignment for record in records]
    report.capability_facts = capability_facts
    report.declared_intentions = declared_intentions
    report.misalignments = misalignments
    report.suggested_scenarios = _suggested_scenarios(records)
    report.release_consequence = _release_consequence(report, misalignments)


def _build_diff_records(
    report: ReadinessReport,
    tools: list[Tool],
) -> tuple[list[CapabilityFact], list[DeclaredIntention], list[MisalignmentRecord]]:
    active_findings = _active_relevant_findings(report.findings)
    tool_lookup = {tool.name: tool for tool in tools}
    findings_by_tool = _findings_by_tool(active_findings, tool_lookup.keys())

    capability_facts = [
        _capability_fact(tool, findings_by_tool.get(tool.name, []))
        for tool in tools
        if _include_tool(tool, findings_by_tool.get(tool.name, []))
    ]
    capability_facts.sort(key=lambda fact: (fact.tool_name, fact.id))
    capability_by_tool = {fact.tool_name: fact for fact in capability_facts}

    declared_intentions = _declared_intentions(report)
    records = _misalignment_records(
        active_findings,
        tool_lookup,
        capability_by_tool,
        declared_intentions,
    )
    records.sort(
        key=lambda record: (
            SEVERITY_SORT[record.misalignment.severity],
            record.misalignment.kind,
            record.misalignment.tool_name or "",
            record.misalignment.id,
        )
    )
    return capability_facts, declared_intentions, records


def _active_relevant_findings(findings: list[Finding]) -> list[Finding]:
    return [
        finding
        for finding in findings
        if not finding.suppressed
        and finding.severity in RELEASE_RELEVANT_SEVERITIES
        and finding.category in RELEASE_RELEVANT_CATEGORIES
    ]


def _findings_by_tool(
    findings: list[Finding],
    known_tool_names: set[str] | dict[str, Tool] | list[str],
) -> dict[str, list[Finding]]:
    known = set(known_tool_names)
    by_tool: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        for tool_name in finding_tool_names(finding, known):
            by_tool[tool_name].append(finding)
    return dict(by_tool)


def _include_tool(tool: Tool, related_findings: list[Finding]) -> bool:
    return bool(
        related_findings
        or tool.annotations.get("wildcard_tools") is True
        or is_high_risk_tool(tool)
    )


def _capability_fact(tool: Tool, related_findings: list[Finding]) -> CapabilityFact:
    tags = risk_tags(tool, min_confidence="medium")
    capability = "wildcard_tool_surface" if tool.annotations.get("wildcard_tools") is True else _capability(tags)
    related_refs = sorted(_finding_ref(finding) for finding in related_findings)
    return CapabilityFact(
        id=_hash_id(
            "cap",
            tool.name,
            tool.source_type,
            tool.source_ref,
            capability,
            sorted(tags),
        ),
        tool_name=tool.name,
        source_type=tool.source_type,
        source_ref=tool.source_ref,
        capability=capability,
        risk_tags=tags,
        auth_scopes=tool.auth.scopes,
        owner=tool.owner,
        included_reason=_included_reason(tool, related_findings),
        control_status=_control_status(tool, related_findings),
        related_findings=related_refs,
    )


def _capability(tags: list[str]) -> str:
    tag_set = set(tags)
    for tag in RISK_TAG_PRIORITY:
        if tag in tag_set:
            return tag
    return "unknown"


def _included_reason(tool: Tool, related_findings: list[Finding]) -> CapabilityIncludedReason:
    severities = {finding.severity for finding in related_findings}
    if "critical" in severities:
        return "referenced_by_critical_finding"
    if "high" in severities:
        return "referenced_by_high_finding"
    if "medium" in severities:
        return "referenced_by_medium_finding"
    if tool.annotations.get("wildcard_tools") is True:
        return "wildcard_exposure"
    return "high_risk_tag"


def _control_status(tool: Tool, related_findings: list[Finding]) -> str:
    if tool.annotations.get("wildcard_tools") is True:
        return "unknown"
    if any(_is_missing_control(finding) for finding in related_findings):
        return "missing"
    if related_findings:
        return "partial"
    if tool.extraction_confidence == "high":
        return "present"
    return "unknown"


def _is_missing_control(finding: Finding) -> bool:
    return finding.check_id in MISSING_CONTROL_CHECKS or finding.check_id.endswith("-MISSING")


def _declared_intentions(report: ReadinessReport) -> list[DeclaredIntention]:
    intentions: list[DeclaredIntention] = []
    for text in _string_list(report.agent.get("declared_purpose")):
        intentions.append(_intention("declared_purpose", text, "agent.declared_purpose"))
    for text in _string_list(report.agent.get("prohibited_actions")):
        intentions.append(_intention("prohibited_action", text, "agent.prohibited_actions"))

    instructions = report.agent.get("instructions")
    if isinstance(instructions, dict):
        preview = instructions.get("value_preview")
        source = instructions.get("source") or "agent.instructions"
        if isinstance(preview, str) and preview.strip():
            intentions.append(_intention("instruction_preview", preview.strip(), str(source)))

    intentions.sort(key=lambda item: (INTENTION_KIND_ORDER[item.kind], item.id))
    return intentions


def _intention(kind: DeclaredIntentionKind, text: str, source: str) -> DeclaredIntention:
    tags = _intent_tags(text)
    return DeclaredIntention(
        id=_hash_id("int", kind, source, text, sorted(tags)),
        kind=kind,
        text=text,
        source=source,
        intent_tags=tags,
    )


def _intent_tags(text: str) -> list[str]:
    tags: set[str] = set()
    tokens = _ordered_tokens(text)
    for index, token in enumerate(tokens):
        # Adjacent-token negation covers common manifest phrasing such as
        # "without approval", "do not refund", and "never delete".
        if index > 0 and tokens[index - 1] in NEGATION_TOKENS:
            continue
        tags.update(INTENT_TAG_ALIASES.get(token, ()))
    return sorted(tags, key=lambda tag: RISK_TAG_PRIORITY.index(tag) if tag in RISK_TAG_PRIORITY else 99)


def _misalignment_records(
    findings: list[Finding],
    tool_lookup: dict[str, Tool],
    capability_by_tool: dict[str, CapabilityFact],
    intentions: list[DeclaredIntention],
) -> list[MisalignmentRecord]:
    records: list[MisalignmentRecord] = []
    known_tools = set(tool_lookup)
    for finding in findings:
        spec = _diff_spec(finding)
        tool_names = finding_tool_names(finding, known_tools) or [None]
        for tool_name in tool_names:
            capability = capability_by_tool.get(tool_name or "")
            capability_refs = [capability.id] if capability else []
            intention_refs = _intention_refs(finding, capability, intentions)
            finding_refs = [_finding_ref(finding)]
            misalignment = Misalignment(
                id=_hash_id(
                    "mis",
                    spec.kind,
                    tool_name,
                    sorted(capability_refs),
                    sorted(intention_refs),
                    sorted(finding_refs),
                ),
                kind=spec.kind,
                severity=finding.severity,
                tool_name=tool_name,
                capability_refs=sorted(capability_refs),
                intention_refs=sorted(intention_refs),
                finding_refs=sorted(finding_refs),
                policy_requirement=spec.policy_requirement,
                gap=f"{finding.title}.",
                release_implication=spec.release_implication,
            )
            records.append(MisalignmentRecord(misalignment, spec.scenario_type))
    return records


def _diff_spec(finding: Finding) -> DiffSpec:
    if finding.check_id in CHECK_DIFF_MAP:
        return CHECK_DIFF_MAP[finding.check_id]
    if finding.category == "policy":
        return DiffSpec(
            kind="policy_gap",
            policy_requirement="Policy-controlled tools must declare matching controls.",
            release_implication="Release review must resolve the missing policy evidence.",
            scenario_type="approval",
        )
    if finding.category == "auth":
        return DiffSpec(
            kind="scope_drift",
            policy_requirement="Auth scopes must match the reviewed release surface.",
            release_implication="Least-privilege review is incomplete.",
            scenario_type="least_privilege_scope",
        )
    if finding.category == "scope":
        return DiffSpec(
            kind="intent_mismatch",
            policy_requirement="Declared intent must align with tool capabilities.",
            release_implication="The reviewed purpose does not fully cover the tool surface.",
            scenario_type="prompt_scope_alignment",
        )
    if finding.category in {"schema", "security"}:
        return DiffSpec(
            kind="control_missing",
            policy_requirement="Tool schemas and metadata must constrain unsafe inputs or outputs.",
            release_implication="The release has unresolved tool-boundary risk.",
            scenario_type="schema_boundary",
        )
    if finding.category == "manifest":
        return DiffSpec(
            kind="control_missing",
            policy_requirement="Manifest metadata must match the active release surface.",
            release_implication="Release review metadata is incomplete or stale.",
            scenario_type="test_case_coverage",
        )
    if finding.category in {"adk", "langchain", "crewai"}:
        return DiffSpec(
            kind="control_missing",
            policy_requirement="Framework tool surfaces and controls must be statically reviewable.",
            release_implication="Release review cannot fully verify framework-specific tool behavior.",
            scenario_type="wildcard_inventory",
        )
    if finding.category == "documentation":
        return DiffSpec(
            kind="control_missing",
            policy_requirement="Tool documentation must include enough static metadata for review.",
            release_implication="Release reviewers lack the static description needed to assess behavior.",
            scenario_type="schema_boundary",
        )
    if finding.category == "evidence":
        return DiffSpec(
            kind="control_missing",
            policy_requirement="Local validation evidence must match the declared review posture.",
            release_implication="Release reviewers lack local evidence needed to evaluate the declared review posture.",
            scenario_type=None,
        )
    return DiffSpec(
        kind="undetected_gap",
        policy_requirement="Static review requires deterministic evidence for release gaps.",
        release_implication="Human review is required to interpret this finding.",
        scenario_type=None,
    )


def _intention_refs(
    finding: Finding,
    capability: CapabilityFact | None,
    intentions: list[DeclaredIntention],
) -> list[str]:
    tags = set(capability.risk_tags if capability else _evidence_tags(finding))
    refs: set[str] = {
        intention.id
        for intention in intentions
        if tags and tags.intersection(intention.intent_tags)
    }
    prohibited_action = finding.evidence.get("prohibited_action")
    if isinstance(prohibited_action, str):
        refs.update(
            intention.id
            for intention in intentions
            if intention.kind == "prohibited_action" and intention.text == prohibited_action
        )
    if finding.check_id == "SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH":
        refs.update(
            intention.id
            for intention in intentions
            if intention.kind == "instruction_preview"
        )
    return sorted(refs)


def _suggested_scenarios(records: list[MisalignmentRecord]) -> list[SuggestedScenario]:
    grouped: dict[SuggestedScenarioType, list[Misalignment]] = defaultdict(list)
    for record in records:
        if record.scenario_type is not None:
            grouped[record.scenario_type].append(record.misalignment)

    scenarios = [
        _scenario(scenario_type, misalignments)
        for scenario_type, misalignments in grouped.items()
    ]
    order = {record.misalignment.id: index for index, record in enumerate(records)}
    scenarios.sort(
        key=lambda scenario: (
            min(order[item] for item in scenario.source_misalignments),
            scenario.scenario_type,
            scenario.id,
        )
    )
    return scenarios


def _scenario(
    scenario_type: SuggestedScenarioType,
    misalignments: list[Misalignment],
) -> SuggestedScenario:
    source_misalignments = sorted(misalignment.id for misalignment in misalignments)
    source_findings = sorted(
        {
            finding_ref
            for misalignment in misalignments
            for finding_ref in misalignment.finding_refs
        }
    )
    scope = _scenario_scope(misalignments)
    title, expected_control = _scenario_text(scenario_type)
    return SuggestedScenario(
        id=_hash_id("scn", scenario_type, sorted(source_misalignments)),
        scenario_type=scenario_type,
        title=title,
        given=f"Exercise the release path for {scope}.",
        expected_control=expected_control,
        source_misalignments=source_misalignments,
        source_findings=source_findings,
    )


def _scenario_text(scenario_type: SuggestedScenarioType) -> tuple[str, str]:
    return {
        "approval": (
            "Approval gate for high-risk action",
            "The run records human approval before the tool call and denies calls without approval.",
        ),
        "confirmation": (
            "Confirmation gate for external or destructive action",
            "The run records explicit confirmation before the side effect occurs.",
        ),
        "idempotency_retry": (
            "Retry behavior for risky write",
            "Retries use idempotency evidence or the side effect is not retried.",
        ),
        "least_privilege_scope": (
            "Least-privilege scope review",
            "Manifest and tool scopes match the narrow permissions needed for the release.",
        ),
        "prohibited_action": (
            "Prohibited-action guard",
            "The prohibited action is blocked, removed, or covered by the stated control.",
        ),
        "wildcard_inventory": (
            "Explicit tool inventory review",
            "The release exposes a static allowlist instead of wildcard or unbounded tools.",
        ),
        "schema_boundary": (
            "Tool schema boundary check",
            "The tool accepts bounded structured inputs and returns structured outputs where needed.",
        ),
        "prompt_scope_alignment": (
            "Prompt and tool-surface alignment",
            "The agent instructions match the enabled write and high-risk capabilities.",
        ),
        "test_case_coverage": (
            "High-risk tool validation case",
            "A declared test or review scenario covers the high-risk tool path.",
        ),
    }[scenario_type]


def _scenario_scope(misalignments: list[Misalignment]) -> str:
    tools = sorted({item.tool_name for item in misalignments if item.tool_name})
    if not tools:
        return "the agent-level release gap"
    if len(tools) <= 3:
        return ", ".join(tools)
    return f"{', '.join(tools[:3])}, and {len(tools) - 3} more tool(s)"


def _release_consequence(
    report: ReadinessReport,
    misalignments: list[Misalignment],
) -> ReleaseConsequence | None:
    decision = report.release_decision
    if decision is None:
        return None
    blocker_refs = {
        ref for item in decision.blockers if (ref := _decision_ref(item)) is not None
    }
    review_refs = {
        ref for item in decision.review_items if (ref := _decision_ref(item)) is not None
    }
    blocker_finding_refs = {
        ref
        for item in misalignments
        for ref in item.finding_refs
        if ref in blocker_refs
    }
    review_finding_refs = {
        ref
        for item in misalignments
        for ref in item.finding_refs
        if ref in review_refs and ref not in blocker_refs
    }
    return ReleaseConsequence(
        decision=decision.decision,
        summary=_release_summary(
            decision.decision,
            len(blocker_finding_refs),
            len(review_finding_refs),
        ),
        blocker_misalignment_count=len(blocker_finding_refs),
        review_misalignment_count=len(review_finding_refs),
        fail_policy=FailPolicy.model_validate(decision.fail_policy.model_dump()),
    )


def _release_summary(decision: str, blocker_count: int, review_count: int) -> str:
    if decision == "blocked":
        return (
            f"{blocker_count} release-relevant finding(s) map to active "
            "release blockers; resolve required controls or remove the capability."
        )
    if decision == "review_required":
        return (
            f"{review_count} release-relevant finding(s) require release review "
            "before shipping."
        )
    return "No capability/intent misalignments require release action from static evidence."


def _decision_ref(item: object) -> str | None:
    item_id = getattr(item, "id", None)
    fingerprint = getattr(item, "fingerprint", None)
    # v0.8 items currently set id == fingerprint; the fingerprint fallback
    # keeps older test fixtures and hand-built reports defensive.
    return item_id or fingerprint


def _finding_ref(finding: Finding) -> str:
    return finding.id or finding.fingerprint or finding.check_id


def _evidence_tags(finding: Finding) -> list[str]:
    tags = finding.evidence.get("risk_tags")
    if not isinstance(tags, list):
        return []
    return [tag for tag in tags if isinstance(tag, str)]


def _ordered_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _hash_id(prefix: str, *parts: object) -> str:
    normalized = [_normalize_hash_part(part) for part in parts]
    digest = hashlib.sha256("|".join(normalized).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _normalize_hash_part(part: object) -> str:
    if part is None:
        return ""
    if isinstance(part, (list, tuple, set)):
        return ",".join(sorted(str(item) for item in part))
    return str(part)
