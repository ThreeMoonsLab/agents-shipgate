from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

from agents_shipgate.core.baseline import load_baseline
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.host_grants import (
    DEFAULT_BASELINE_FILE,
    build_host_drift_payload,
    host_audit_inventory,
    load_host_grants_baseline,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest, PolicyPackConfig
from agents_shipgate.schemas.org_governance import (
    ExceptionRecordV1,
    OrgGovernanceStatusV1,
    OrgGovernanceSummaryV1,
    PolicyPackPinRecordV1,
)

_METADATA_GATED_EXCEPTION_KINDS = frozenset(
    {"baseline", "suppression", "severity_override", "override_acknowledgement", "human_ack"}
)


def build_org_governance_status(
    *,
    manifest: AgentsShipgateManifest,
    config_path: Path,
    workspace: Path,
    as_of: date,
    baseline_path: Path | None = None,
    host_baseline_path: Path | None = None,
) -> OrgGovernanceStatusV1:
    """Build the deterministic org-governance projection.

    This is intentionally not a release decision. It evaluates local exception
    hygiene, policy-pack pinning, and host-grant drift for scheduled/org gates.
    """

    workspace = workspace.resolve()
    config_path = config_path.resolve()
    base_dir = config_path.parent
    org = manifest.organization
    exceptions = build_exception_records(
        manifest=manifest,
        baseline_path=(
            baseline_path
            if baseline_path is not None
            else workspace / ".agents-shipgate" / "baseline.json"
        ),
        host_baseline_path=(
            host_baseline_path if host_baseline_path is not None else workspace / DEFAULT_BASELINE_FILE
        ),
        as_of=as_of,
    )
    policy_packs = policy_pack_pin_records(
        manifest.checks.policy_packs,
        base_dir=base_dir,
        require_pin_for_sourced=org is not None,
    )
    host_drift = host_grant_drift_payload(
        workspace=workspace,
        baseline_path=(
            host_baseline_path if host_baseline_path is not None else workspace / DEFAULT_BASELINE_FILE
        ),
    )

    violations: list[dict[str, str]] = []
    for record in exceptions:
        for violation in record.violations:
            violations.append(
                {
                    "kind": violation,
                    "source": record.source_ref,
                    "record_kind": record.kind,
                    "subject": record.subject or record.check_id or record.fingerprint or "",
                }
            )
    for pack in policy_packs:
        for violation in pack.violations:
            violations.append(
                {
                    "kind": violation,
                    "source": pack.path,
                    "record_kind": "policy_pack",
                    "subject": pack.id or pack.source or pack.path,
                }
            )
    if host_drift is not None and host_drift.get("has_drift"):
        violations.append(
            {
                "kind": "host_grant_drift",
                "source": str(host_drift.get("baseline_file") or DEFAULT_BASELINE_FILE),
                "record_kind": "host_grants",
                "subject": "host_grants",
            }
        )

    registry = None
    if org is not None and org.audit.registry:
        registry_path = Path(org.audit.registry)
        resolved = registry_path if registry_path.is_absolute() else workspace / registry_path
        registry = {
            "path": org.audit.registry,
            "exists": resolved.is_file(),
        }

    summary = OrgGovernanceSummaryV1(
        exception_count=len(exceptions),
        exception_violation_count=sum(len(record.violations) for record in exceptions),
        policy_pack_count=len(policy_packs),
        policy_pack_violation_count=sum(len(pack.violations) for pack in policy_packs),
        host_grant_drift=bool(host_drift and host_drift.get("has_drift")),
        registry_configured=registry is not None,
    )

    return OrgGovernanceStatusV1(
        organization=(
            org.model_dump(mode="json", exclude_none=True) if org is not None else None
        ),
        config=_display_path(config_path, workspace),
        as_of=as_of.isoformat(),
        summary=summary,
        exceptions=exceptions,
        policy_packs=policy_packs,
        host_grant_drift=host_drift,
        registry=registry,
        violations=violations,
    )


def build_exception_records(
    *,
    manifest: AgentsShipgateManifest,
    baseline_path: Path,
    host_baseline_path: Path,
    as_of: date,
) -> list[ExceptionRecordV1]:
    policy = (
        manifest.organization.exception_policy
        if manifest.organization is not None
        else None
    )
    records: list[ExceptionRecordV1] = []

    if baseline_path.is_file():
        try:
            baseline = load_baseline(baseline_path)
        except InputParseError:
            baseline = None
        if baseline is not None:
            for finding in sorted(baseline.findings, key=lambda item: item.fingerprint):
                provenance = finding.provenance
                records.append(
                    _record_with_violations(
                        ExceptionRecordV1(
                            kind="baseline",
                            source_ref=_display_path(baseline_path, baseline_path.parent.parent),
                            owner=_clean(getattr(provenance, "owner", None)),
                            reason=_clean(getattr(provenance, "reason", None)),
                            expires=(
                                provenance.expires.isoformat()
                                if provenance is not None and provenance.expires is not None
                                else None
                            ),
                            recorded_at=(
                                provenance.recorded_at if provenance is not None else None
                            ),
                            subject=finding.tool_name,
                            check_id=finding.check_id,
                            fingerprint=finding.fingerprint,
                        ),
                        as_of=as_of,
                        policy=policy,
                    )
                )

    for item in manifest.checks.ignore:
        records.append(
            _record_with_violations(
                ExceptionRecordV1(
                    kind="suppression",
                    source_ref="shipgate.yaml#/checks/ignore",
                    owner=_clean(getattr(item, "owner", None)),
                    reason=_clean(item.reason),
                    expires=(
                        item.expires.isoformat()
                        if getattr(item, "expires", None) is not None
                        else None
                    ),
                    subject=item.tool,
                    check_id=item.check_id,
                ),
                as_of=as_of,
                policy=policy,
            )
        )

    for check_id, item in sorted(manifest.checks.severity_overrides.items()):
        records.append(
            _record_with_violations(
                ExceptionRecordV1(
                    kind="severity_override",
                    source_ref=f"shipgate.yaml#/checks/severity_overrides/{check_id}",
                    owner=_clean(getattr(item, "owner", None)),
                    reason=_clean(item.reason),
                    expires=item.expires.isoformat() if item.expires is not None else None,
                    subject=item.severity,
                    check_id=check_id,
                ),
                as_of=as_of,
                policy=policy,
            )
        )

    for item in sorted(manifest.checks.acknowledge_overrides, key=lambda ack: ack.check_id):
        records.append(
            _record_with_violations(
                ExceptionRecordV1(
                    kind="override_acknowledgement",
                    source_ref="shipgate.yaml#/checks/acknowledge_overrides",
                    owner=_clean(getattr(item, "owner", None)),
                    reason=_clean(item.reason),
                    expires=item.expires.isoformat() if item.expires is not None else None,
                    check_id=item.check_id,
                ),
                as_of=as_of,
                policy=policy,
            )
        )

    for item in sorted(manifest.human_ack, key=lambda ack: (ack.affected_surface, ack.owner)):
        records.append(
            _record_with_violations(
                ExceptionRecordV1(
                    kind="human_ack",
                    source_ref="shipgate.yaml#/human_ack",
                    owner=_clean(item.owner),
                    reason=_clean(item.reason),
                    expires=item.expires,
                    subject=item.affected_surface,
                ),
                as_of=as_of,
                policy=policy,
            )
        )

    if host_baseline_path.is_file():
        records.append(
            ExceptionRecordV1(
                kind="host_grant_baseline",
                source_ref=_display_path(host_baseline_path, host_baseline_path.parent.parent),
                subject="host_grants",
            )
        )

    return sorted(records, key=lambda item: (item.kind, item.source_ref, item.subject or "", item.check_id or "", item.fingerprint or ""))


def policy_pack_pin_records(
    configs: list[PolicyPackConfig],
    *,
    base_dir: Path,
    require_pin_for_sourced: bool,
) -> list[PolicyPackPinRecordV1]:
    records: list[PolicyPackPinRecordV1] = []
    for config in configs:
        path = Path(config.path)
        resolved = path if path.is_absolute() else base_dir / path
        actual: str | None = None
        status = "unpinned"
        violations: list[str] = []
        try:
            actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except FileNotFoundError:
            status = "missing"
            violations.append("policy_pack_missing")
        except OSError:
            status = "unreadable"
            violations.append("policy_pack_unreadable")
        else:
            pinned = (config.sha256 or "").strip().lower()
            if pinned:
                if actual == pinned:
                    status = "verified"
                else:
                    status = "mismatch"
                    violations.append("policy_pack_pin_mismatch")
            elif require_pin_for_sourced and config.source:
                violations.append("policy_pack_unpinned")
        records.append(
            PolicyPackPinRecordV1(
                id=config.id,
                path=config.path,
                source=config.source,
                sha256=config.sha256,
                actual_sha256=actual,
                status=status,  # type: ignore[arg-type]
                violations=violations,
            )
        )
    return sorted(records, key=lambda item: (item.path, item.id or ""))


def host_grant_drift_payload(
    *,
    workspace: Path,
    baseline_path: Path,
) -> dict[str, Any] | None:
    if not baseline_path.is_file():
        return None
    try:
        baseline = load_host_grants_baseline(baseline_path)
    except ValueError as exc:
        return {
            "host_grants_schema_version": "unknown",
            "baseline_file": _display_path(baseline_path, workspace),
            "has_drift": True,
            "load_error": str(exc),
            "expansion_signals": [],
        }
    return build_host_drift_payload(
        baseline=baseline,
        inventory=host_audit_inventory(workspace),
        baseline_file=_display_path(baseline_path, workspace),
    )


def render_org_status_markdown(status: OrgGovernanceStatusV1) -> str:
    lines = ["# Shipgate Organization Status", ""]
    org = status.organization or {}
    if org:
        lines.append(f"Organization: `{org.get('id', 'unknown')}`")
        if org.get("repo"):
            lines.append(f"Repository: `{org['repo']}`")
        lines.append("")
    if not status.violations:
        lines.append("No organization governance violations.")
    else:
        lines.append(f"Governance violations: {len(status.violations)}")
        lines.append("")
        for item in status.violations:
            lines.append(
                f"- `{item['kind']}` in `{item['source']}`"
                + (f" ({item['subject']})" if item.get("subject") else "")
            )
    lines.append("")
    lines.append(
        f"Exceptions: {status.summary.exception_count} "
        f"({status.summary.exception_violation_count} violation(s))"
    )
    lines.append(
        f"Policy packs: {status.summary.policy_pack_count} "
        f"({status.summary.policy_pack_violation_count} violation(s))"
    )
    if status.summary.host_grant_drift:
        lines.append("Host grants: drift detected")
    elif status.host_grant_drift is not None:
        lines.append("Host grants: no drift")
    return "\n".join(lines) + "\n"


def _record_with_violations(
    record: ExceptionRecordV1,
    *,
    as_of: date,
    policy: Any,
) -> ExceptionRecordV1:
    if policy is None or record.kind not in _METADATA_GATED_EXCEPTION_KINDS:
        return record
    violations: list[str] = []
    if getattr(policy, "require_owner", False) and not _clean(record.owner):
        violations.append("missing_owner")
    if getattr(policy, "require_reason", False) and not _clean(record.reason):
        violations.append("missing_reason")
    if getattr(policy, "require_expiry", False) and not record.expires:
        violations.append("missing_expiry")
    if record.expires:
        try:
            expires = date.fromisoformat(record.expires)
        except ValueError:
            violations.append("invalid_expiry")
        else:
            if expires < as_of:
                violations.append("expired")
    max_age = getattr(policy, "max_age_days", None)
    if max_age is not None and record.recorded_at:
        try:
            recorded = date.fromisoformat(record.recorded_at[:10])
        except ValueError:
            violations.append("invalid_recorded_at")
        else:
            if (as_of - recorded).days > max_age:
                violations.append("age_exceeded")
    elif max_age is not None and record.kind == "baseline":
        violations.append("age_unknown")
    return record.model_copy(update={"violations": sorted(set(violations))})


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


__all__ = [
    "build_exception_records",
    "build_org_governance_status",
    "host_grant_drift_payload",
    "policy_pack_pin_records",
    "render_org_status_markdown",
]
