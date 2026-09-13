"""Compare the host inventories selected by a caller, without creating policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    build_host_comparison_payload,
    build_host_drift_payload,
    build_host_grants_baseline,
    host_grant_expansion_signals,
    host_grants_sha256,
    inventory_is_complete,
    normalized_host_grants,
)
from agents_shipgate.schemas.host_comparison import HostComparison

#: Blocking issue kinds an unchanged source may carry without refusing the
#: comparison (#721). `unreadable` is deliberately absent: an unchanged symlink
#: whose in-tree target changed would read as an unchanged limit and hide the
#: change it points to, and when a link is truly unchanged is #700's to decide.
UNCHANGED_LIMIT_ISSUE_KINDS = frozenset({"unsupported", "parse_failed"})


def unchanged_limits(
    before: dict[str, Any], after: dict[str, Any], unchanged: Callable[[str], bool]
) -> list[dict[str, str]] | None:
    """The limits both inventories share and the change did not touch, or ``None``.

    ``None`` whenever any partial or experimental coverage is not explained by
    such a limit: an issue only one side has, an issue of another kind, a
    source that changed or whose identity cannot be proven, or a host whose
    coverage differs between the two sides.
    """

    def blocking(inventory: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
        return {
            (str(issue["kind"]), str(issue["host"]), str(issue["source"])): issue
            for issue in inventory.get("issues", [])
            if issue.get("blocking")
        }

    base_issues, head_issues = blocking(before), blocking(after)
    if set(base_issues) != set(head_issues):
        return None
    limits: list[dict[str, str]] = []
    for key in sorted(base_issues):
        kind, host, source = key
        if kind not in UNCHANGED_LIMIT_ISSUE_KINDS or not unchanged(source):
            return None
        limits.append(
            {"host": host, "limit": kind, "source": source, "detail": str(head_issues[key]["message"])}
        )

    base_coverage = {item["host"]: item for item in before.get("host_coverage", [])}
    head_coverage = {item["host"]: item for item in after.get("host_coverage", [])}
    if set(base_coverage) != set(head_coverage):
        return None
    for host in sorted(base_coverage):
        status = base_coverage[host].get("status")
        if status != head_coverage[host].get("status"):
            return None
        if status == "complete":
            continue
        if status == "partial":
            # Partial coverage comes only from a blocking issue on that host,
            # and every one of those qualified above.
            if not any(limit["host"] == host for limit in limits):
                return None
            continue
        if status == "experimental":
            sources = sorted(set(base_coverage[host].get("sources_observed", [])))
            if not sources or sources != sorted(set(head_coverage[host].get("sources_observed", []))):
                return None
            if not all(unchanged(source) for source in sources):
                return None
            limits.extend(
                {
                    "host": host,
                    "limit": "experimental_coverage",
                    "source": source,
                    "detail": f"{host} coverage is experimental; this unchanged source was not compared",
                }
                for source in sources
            )
            continue
        return None
    return limits


def compare_host_inventories(
    before: dict,
    after: dict,
    *,
    head_kind: str,
    base_commit=None,
    head_commit=None,
    redact_permission_arguments: bool = False,
    unchanged: Callable[[str], bool] | None = None,
) -> HostComparison:
    """Compare two inventories, refusing unless every limit is proven unchanged.

    ``unchanged`` answers whether one repository-relative source is identical
    on both sides. Without it, an incomplete inventory refuses the comparison
    as it always has.
    """

    reasons: list[str] = []
    limits: list[dict[str, str]] = []
    if not (inventory_is_complete(before) and inventory_is_complete(after)):
        shared = unchanged_limits(before, after, unchanged) if unchanged is not None else None
        if shared is None:
            if not inventory_is_complete(before):
                reasons.append("base_inventory_incomplete")
            if not inventory_is_complete(after):
                reasons.append("head_inventory_incomplete")
        else:
            limits = shared
    baseline_file = base_commit or "compared input"
    if reasons:
        payload: dict = {}
    elif limits:
        payload = build_host_comparison_payload(before=before, after=after, baseline_file=baseline_file)
    else:
        payload = build_host_drift_payload(
            baseline=build_host_grants_baseline(before),
            inventory=after,
            baseline_file=baseline_file,
        )
    reasons.extend(payload.get("incomparable_reasons") or [])
    if reasons:
        limits = []
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
        unchanged_limits=limits,
    )
