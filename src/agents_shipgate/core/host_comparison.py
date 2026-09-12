"""Compare the host inventories selected by a caller, without creating policy."""

from __future__ import annotations

from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    build_host_drift_payload,
    build_host_grants_baseline,
    host_grant_expansion_signals,
    host_grants_sha256,
    inventory_is_complete,
    normalized_host_grants,
)
from agents_shipgate.schemas.host_comparison import HostComparison


def compare_host_inventories(
    before: dict,
    after: dict,
    *,
    head_kind: str,
    base_commit=None,
    head_commit=None,
    redact_permission_arguments: bool = False,
) -> HostComparison:
    reasons = []
    if not inventory_is_complete(before):
        reasons.append("base_inventory_incomplete")
    if not inventory_is_complete(after):
        reasons.append("head_inventory_incomplete")
    payload = (
        {}
        if reasons
        else build_host_drift_payload(
            baseline=build_host_grants_baseline(before),
            inventory=after,
            baseline_file=base_commit or "compared input",
        )
    )
    reasons.extend(payload.get("incomparable_reasons") or [])
    if redact_permission_arguments:
        from copy import deepcopy

        from agents_shipgate.core.host_boundary import _safe_rule

        payload = deepcopy(payload)
        # Redact by the producer's typed grant kind, not a display-string regex.
        # Grant identities/expansion signals retain the original comparison.
        for change in payload.get("changes", []):
            for side in ("baseline", "current"):
                grant = change.get(side)
                if isinstance(grant, dict) and grant.get("kind") == "permission_rule":
                    grant["rule"] = _safe_rule(grant["rule"])
        payload["expansion_signals"] = host_grant_expansion_signals(payload.get("changes", []))
    return HostComparison(
        comparison_status="incomparable" if reasons else "comparable",
        incomparable_reasons=reasons,
        base_commit=base_commit,
        head_commit=head_commit,
        head_kind=head_kind,
        base_inventory_sha256=host_grants_sha256(normalized_host_grants(before)),
        head_inventory_sha256=host_grants_sha256(normalized_host_grants(after)),
        paths=sorted(
            {item["path"] for inventory in (before, after) for item in inventory["artifacts"]}
        ),
        rows=[] if reasons else capability_diff_rows(payload),
    )
