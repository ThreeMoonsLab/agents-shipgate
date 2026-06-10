from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from agents_shipgate.core.artifact_models import (
    GoogleAdkArtifacts,
    OpenAIApiArtifacts,
    ValidationArtifacts,
)
from agents_shipgate.inputs.traces import TRACE_SOURCE_KEY
from agents_shipgate.schemas.capabilities import CapabilityFactV1, capability_fact_sort_key
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.report import (
    CapabilityRuntimeEvidence,
    CapabilityTraceEvidenceSummary,
    CapabilityTraceEvidenceV1,
    CapabilityTraceMatchReason,
)

_TRACE_SOURCE_FALLBACKS = {
    "openai_api": "openai_api_trace",
    "google_adk": "google_adk_trace",
    "validation_approval": "validation_approval_trace",
    "validation_agent": "validation_agent_trace",
}


def build_capability_runtime_evidence(context) -> CapabilityRuntimeEvidence:
    """Link declared local trace events to durable capability facts.

    This is audit metadata only. It never executes user code, calls tools,
    connects to services, or mutates the static capability lock envelope.
    """

    events = _trace_events(context)
    if not events:
        return CapabilityRuntimeEvidence()

    facts_by_id = {fact.id: fact for fact in context.capability_facts}
    facts_by_tool: dict[str, list[CapabilityFactV1]] = defaultdict(list)
    for fact in context.capability_facts:
        facts_by_tool[fact.identity.tool_name].append(fact)
    for facts in facts_by_tool.values():
        facts.sort(key=capability_fact_sort_key)

    matched: list[CapabilityTraceEvidenceV1] = []
    unmatched: list[CapabilityTraceEvidenceV1] = []
    for event, fallback_source_type in events:
        row = _trace_evidence_row(
            event,
            fallback_source_type=fallback_source_type,
            facts_by_id=facts_by_id,
            facts_by_tool=facts_by_tool,
        )
        if row.matched:
            matched.append(row)
        else:
            unmatched.append(row)

    matched.sort(key=_trace_sort_key)
    unmatched.sort(key=_trace_sort_key)
    provenance = _source_provenance([*matched, *unmatched])
    summary = CapabilityTraceEvidenceSummary(
        source_count=len(provenance),
        trace_count=len(matched) + len(unmatched),
        matched_trace_count=len(matched),
        unmatched_trace_count=len(unmatched),
        approval_trace_count=sum(
            1 for row in [*matched, *unmatched] if row.source_type == "validation_approval_trace"
        ),
        agent_trace_count=sum(
            1 for row in [*matched, *unmatched] if row.source_type == "validation_agent_trace"
        ),
        api_trace_count=sum(
            1
            for row in [*matched, *unmatched]
            if row.source_type in {"openai_api_trace", "google_adk_trace"}
        ),
        warning_count=_trace_warning_count(context),
    )
    return CapabilityRuntimeEvidence(
        enabled=True,
        summary=summary,
        matched=matched,
        unmatched=unmatched,
        source_provenance=provenance,
        notes=[
            "Declared local trace artifacts are audit evidence only; no live trace collection or tool execution occurred.",
            "Trace normalization retains only allowlisted scalar fields and discards prompts, messages, arguments, outputs, and payload bodies.",
        ],
    )


def capability_refs_for_tool(context, tool_name: str) -> list[str]:
    refs = [
        fact.id
        for fact in sorted(context.capability_facts, key=capability_fact_sort_key)
        if fact.identity.tool_name == tool_name
    ]
    return _unique_sorted(refs)


def capability_trace_refs_for_tool(
    context,
    tool_name: str,
    *,
    source_types: set[str] | None = None,
    observed: dict[str, Any] | None = None,
) -> list[str]:
    evidence = getattr(context, "capability_runtime_evidence", None)
    if evidence is None:
        return []
    refs: list[str] = []
    rows = [*evidence.matched, *evidence.unmatched]
    for row in rows:
        if row.tool_name != tool_name:
            continue
        if source_types is not None and row.source_type not in source_types:
            continue
        if observed and any(row.observed.get(key) != value for key, value in observed.items()):
            continue
        refs.append(row.id)
    return _unique_sorted(refs)


def _trace_events(context) -> list[tuple[dict[str, Any], str]]:
    events: list[tuple[dict[str, Any], str]] = []
    openai = context.artifact("openai_api", OpenAIApiArtifacts)
    if openai is not None:
        events.extend(
            (event, _TRACE_SOURCE_FALLBACKS["openai_api"])
            for event in openai.trace_samples
        )
    adk = context.artifact("google_adk", GoogleAdkArtifacts)
    if adk is not None:
        events.extend(
            (event, _TRACE_SOURCE_FALLBACKS["google_adk"])
            for event in adk.trace_samples
        )
    validation = context.artifact("validation", ValidationArtifacts)
    if validation is not None:
        events.extend(
            (event, _TRACE_SOURCE_FALLBACKS["validation_approval"])
            for event in validation.approval_traces
        )
        events.extend(
            (event, _TRACE_SOURCE_FALLBACKS["validation_agent"])
            for event in validation.agent_traces
        )
    return events


def _trace_evidence_row(
    event: dict[str, Any],
    *,
    fallback_source_type: str,
    facts_by_id: dict[str, CapabilityFactV1],
    facts_by_tool: dict[str, list[CapabilityFactV1]],
) -> CapabilityTraceEvidenceV1:
    observed = _observed_event(event)
    source_meta = _source_meta(event, fallback_source_type)
    source = _source_reference(source_meta)
    capability_id = _string_or_none(observed.get("capability_id"))
    tool_name = _string_or_none(observed.get("tool_name"))
    matched_fact: CapabilityFactV1 | None = None
    match_reason: CapabilityTraceMatchReason

    if capability_id:
        matched_fact = facts_by_id.get(capability_id)
        match_reason = "capability_id" if matched_fact else "invalid_capability_id"
    elif not tool_name:
        match_reason = "missing_tool_name"
    else:
        candidate_facts = facts_by_tool.get(tool_name, [])
        if len(candidate_facts) == 1:
            matched_fact = candidate_facts[0]
            match_reason = "tool_name"
        elif candidate_facts:
            match_reason = "ambiguous_tool"
        else:
            match_reason = "unknown_tool"

    matched_capability_id = matched_fact.id if matched_fact else None
    identity = _event_identity(observed, source_meta)
    return CapabilityTraceEvidenceV1(
        id=f"ctrace_{_stable_hash(identity)[:16]}",
        source_type=source_meta["source_type"],
        source=source,
        tool_name=tool_name,
        provider=_string_or_none(observed.get("provider")),
        operation=_string_or_none(observed.get("operation")),
        capability_id=capability_id,
        matched_capability_id=matched_capability_id,
        matched=matched_fact is not None,
        match_reason=match_reason,
        observed=observed,
        event_hash=_stable_hash(observed),
        source_hash=_stable_hash(source_meta),
    )


def _observed_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in event.items()
        if key != TRACE_SOURCE_KEY and _is_public_scalar(value)
    }


def _source_meta(event: dict[str, Any], fallback_source_type: str) -> dict[str, Any]:
    raw = event.get(TRACE_SOURCE_KEY)
    raw = raw if isinstance(raw, dict) else {}
    return {
        "source_type": _string_or_none(raw.get("source_type")) or fallback_source_type,
        "source_ref": _string_or_none(raw.get("source_ref")),
        "source_path": _string_or_none(raw.get("source_path")),
        "source_line": raw.get("source_line") if isinstance(raw.get("source_line"), int) else None,
        "source_pointer": _string_or_none(raw.get("source_pointer")),
        "source_index": raw.get("source_index") if isinstance(raw.get("source_index"), int) else 0,
    }


def _source_reference(source_meta: dict[str, Any]) -> SourceReference:
    return SourceReference(
        type=source_meta["source_type"],
        ref=source_meta.get("source_ref"),
        path=source_meta.get("source_path"),
        start_line=source_meta.get("source_line"),
        pointer=source_meta.get("source_pointer"),
    )


def _event_identity(
    observed: dict[str, Any],
    source_meta: dict[str, Any],
) -> dict[str, Any]:
    source_identity = {
        "source_type": source_meta.get("source_type"),
        "source_ref": source_meta.get("source_ref"),
        "source_path": source_meta.get("source_path"),
        "source_index": source_meta.get("source_index"),
    }
    return {"observed": observed, "source": source_identity}


def _source_provenance(
    rows: list[CapabilityTraceEvidenceV1],
) -> list[SourceReference]:
    by_key: dict[str, SourceReference] = {}
    for row in rows:
        if row.source is None:
            continue
        key = json.dumps(row.source.model_dump(mode="json"), sort_keys=True)
        by_key[key] = row.source
    return [by_key[key] for key in sorted(by_key)]


def _trace_warning_count(context) -> int:
    warnings: list[str] = []
    for source_type, artifact_type in (
        ("openai_api", OpenAIApiArtifacts),
        ("google_adk", GoogleAdkArtifacts),
        ("validation", ValidationArtifacts),
    ):
        artifact = context.artifact(source_type, artifact_type)
        if artifact is not None:
            warnings.extend(getattr(artifact, "warnings", []))
    return sum(1 for warning in warnings if "trace" in warning.lower())


def _trace_sort_key(row: CapabilityTraceEvidenceV1) -> tuple[str, str, str, str]:
    return (
        row.source_type,
        row.tool_name or "",
        row.matched_capability_id or row.capability_id or "",
        row.id,
    )


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _is_public_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _unique_sorted(values: list[str]) -> list[str]:
    return sorted(set(values))
