from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from agents_shipgate.schemas.report import Finding

from .constants import FINGERPRINT_EXCLUDED_EVIDENCE_KEYS


def assign_finding_ids(findings: list[Finding]) -> list[Finding]:
    by_fingerprint: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        finding.fingerprint = finding_fingerprint(finding)
        by_fingerprint[finding.fingerprint].append(finding)
    used_ids: dict[str, int] = defaultdict(int)
    for finding in findings:
        assert finding.fingerprint is not None
        if len(by_fingerprint[finding.fingerprint]) == 1:
            candidate = finding.fingerprint
        else:
            candidate = f"{finding.fingerprint}_{_collision_discriminator(finding)}"
        used_ids[candidate] += 1
        finding.id = (
            candidate
            if used_ids[candidate] == 1
            else f"{candidate}_{used_ids[candidate]}"
        )
    return findings


def dedupe_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str, str, str, str]] = set()
    deduped: list[Finding] = []
    for finding in findings:
        evidence_key = json.dumps(
            _canonicalize_for_fingerprint(finding.evidence),
            sort_keys=True,
            default=str,
        )
        source_key = json.dumps(
            finding.source.model_dump(mode="json") if finding.source else None,
            sort_keys=True,
            default=str,
        )
        key = (
            finding.check_id,
            # Title is intentionally part of local de-dupe identity. Some
            # checks share structured evidence across distinct user-visible
            # targets, and the interpolated title is the only stable context
            # that keeps those findings separate before IDs are assigned.
            finding.title,
            finding.tool_id or "",
            finding.tool_name or "",
            evidence_key,
            source_key,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def finding_fingerprint(finding: Finding) -> str:
    identity = {
        "fingerprint_version": "2",
        "check_id": finding.check_id,
        "tool_id": finding.tool_id,
        "evidence": _canonicalize_for_fingerprint(finding.evidence),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"fp_{digest}"


def legacy_policy_routing_fingerprint(finding: Finding) -> str | None:
    """Return the v0.27 policy-pack fingerprint for baseline compatibility."""
    routing = finding.policy_routing
    if finding.provenance_kind != "policy_pack" or routing is None:
        return None
    evidence = dict(finding.evidence)
    evidence.update(
        {
            "policy_owner": routing.owner,
            "policy_reviewers": list(routing.reviewers),
            "policy_approval_required": routing.approval.required,
            "policy_approval_teams": list(routing.approval.teams),
            "policy_approval_min_approvals": routing.approval.min_approvals,
            "policy_approval_enforced": routing.approval.enforced,
        }
    )
    return _finding_fingerprint_v1(finding.model_copy(update={"evidence": evidence}))


def legacy_name_fingerprint(finding: Finding) -> str:
    """Return the pre-identity v1 fingerprint for guarded baseline migration."""

    return _finding_fingerprint_v1(finding)


def _finding_fingerprint_v1(finding: Finding) -> str:
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
