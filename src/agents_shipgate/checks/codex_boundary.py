from __future__ import annotations

from pathlib import Path

from agents_shipgate.core.codex_boundary import (
    DEFAULT_POLICY_PATH,
    evaluate_codex_boundary_result,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.common import SourceReference, parse_confidence, parse_severity
from agents_shipgate.schemas.report import Finding


def run(context: ScanContext) -> list[Finding]:
    verification = context.verification
    if verification is None:
        return []
    diff_text = verification.diff_text or _synthetic_diff(verification.changed_files)
    result = evaluate_codex_boundary_result(
        workspace=Path(context.config_path).resolve().parent,
        diff_text=diff_text,
        policy_path=DEFAULT_POLICY_PATH,
        trigger=verification.trigger_result,
    )
    findings: list[Finding] = []
    for violated in result.violated_rules:
        findings.append(
            Finding(
                check_id=violated.check_id,
                title=violated.title,
                severity=parse_severity(_severity_for(violated.risk_level)),
                category="codex_boundary",
                agent_id=context.agent.id,
                evidence=violated.evidence,
                confidence=parse_confidence("high"),
                provenance_kind="static_declaration",
                source=SourceReference(type="changed_file", path=violated.path),
                recommendation=violated.recommendation,
                blocks_release=violated.action == "block",
            )
        )
    return findings


def _severity_for(risk_level: str) -> str:
    return {
        "none": "info",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "critical": "critical",
    }.get(risk_level, "medium")


def _synthetic_diff(changed_files: list[str]) -> str:
    return "\n".join(f"diff --git a/{path} b/{path}" for path in changed_files)

