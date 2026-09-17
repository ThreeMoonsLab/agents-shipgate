"""Compare the host inventories selected by a caller, without creating policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    build_host_comparison_payload,
    build_host_drift_payload,
    build_host_grants_baseline,
    host_grants_sha256,
    inventory_is_complete,
    normalized_host_grants,
)
from agents_shipgate.schemas.host_comparison import (
    MAX_COVERAGE_ITEMS,
    HostComparison,
    HostComparisonCoverage,
    HostComparisonCoverageItem,
)

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


#: Coverage order (#812): what refused the comparison, then sources with rows,
#: then changes no row describes, then sources only one side read, then
#: sources compared with no change. The cap keeps a prefix of this order.
def _coverage_rank(item: dict[str, Any]) -> tuple[int, str, str, str]:
    if item["status"] == "blocking_limit":
        rank = 0
    elif item["rows"]:
        rank = 1
    elif item["status"] == "unread_fields_changed":
        rank = 2
    elif item["side"] != "both":
        rank = 3
    else:
        rank = 4
    return (rank, item["source"], item["side"], str(item.get("limit")))


def _group_coverage(
    facts: dict[tuple[str, str, str, str | None], dict[str, Any]],
) -> HostComparisonCoverage:
    """One item per source, status, side and limit, its hosts merged and capped."""

    items = sorted(facts.values(), key=_coverage_rank)
    return HostComparisonCoverage(
        items=[
            HostComparisonCoverageItem(**{**item, "hosts": sorted(item["hosts"])})
            for item in items[:MAX_COVERAGE_ITEMS]
        ],
        omitted_items=max(0, len(items) - MAX_COVERAGE_ITEMS),
    )


def _blocking_coverage(before: dict[str, Any], after: dict[str, Any]) -> HostComparisonCoverage:
    """The sources behind a refused comparison, each with its limit and side (#812).

    Only what the inventories already published: every blocking issue, keyed
    by its published source and kind. The refusal itself is decided elsewhere
    and is not changed by naming them.
    """

    def blocking(inventory: dict[str, Any]) -> dict[tuple[str, str, str], str]:
        return {
            (str(issue["source"]), str(issue["kind"]), str(issue["host"])): str(issue["message"])
            for issue in inventory.get("issues", [])
            if issue.get("blocking")
        }

    base, head = blocking(before), blocking(after)
    facts: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    for key in sorted(set(base) | set(head)):
        source, kind, host = key
        side = "both" if key in base and key in head else "base" if key in base else "head"
        item = facts.setdefault(
            (source, "blocking_limit", side, kind),
            {
                "source": source,
                "hosts": set(),
                "side": side,
                "status": "blocking_limit",
                "rows": 0,
                "limit": kind,
                # The first host's message, in host order: hosts reading one
                # source through one reader publish the same one.
                "detail": head.get(key) or base.get(key),
            },
        )
        item["hosts"].add(host)
    return _group_coverage(facts)


def _compared_coverage(
    before: dict[str, Any],
    after: dict[str, Any],
    payload: dict[str, Any],
    limits: list[dict[str, str]],
) -> HostComparisonCoverage:
    """What a comparable comparison established about each source it read (#812).

    Read off the payload the rows were projected from and the two inventories:
    a source's rows are the grant changes whose published source it is; an
    artifact change with no such grant change is a change in fields this entry
    does not read; the side is which inventories observed it. A source named
    as an unchanged limit is already published there and is not repeated.
    """

    def observed(inventory: dict[str, Any]) -> set[tuple[str, str]]:
        return {
            (str(artifact["host"]), str(artifact["path"]))
            for artifact in inventory.get("artifacts", [])
        } | {
            (str(grant["host"]), str(grant["source"]))
            for grant in inventory.get("grants", [])
        }

    rows: dict[tuple[str, str], int] = {}
    for change in payload.get("changes") or []:
        grant = change.get("current") or change.get("baseline")
        if grant:
            key = (str(grant["host"]), str(grant["source"]))
            rows[key] = rows.get(key, 0) + 1
    unread = {
        (str(artifact["host"]), str(artifact["path"]))
        for change in payload.get("artifact_changes") or []
        for artifact in (change.get("baseline"), change.get("current"))
        if artifact
    }
    base, head = observed(before), observed(after)
    named = {(limit["host"], limit["source"]) for limit in limits}
    facts: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    for key in sorted(base | head | set(rows)):
        if key in named and not rows.get(key):
            continue
        host, source = key
        side = "both" if key in base and key in head else "base" if key in base else "head"
        count = rows.get(key, 0)
        status = "unread_fields_changed" if not count and key in unread else "compared"
        # A source with rows and one without never merge, so a count is never
        # spread over a host that contributed none.
        item = facts.setdefault(
            (source, status if not count else "rows", side, None),
            {"source": source, "hosts": set(), "side": side, "status": status, "rows": 0},
        )
        item["hosts"].add(host)
        item["rows"] += count
    return _group_coverage(facts)


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
    # What the run established, from the facts above and nothing else (#812).
    # It stays on the comparison: it is not an input to the inventory digests,
    # a saved baseline or the rows.
    coverage = (
        _blocking_coverage(before, after)
        if reasons
        else _compared_coverage(before, after, payload, limits)
    )
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
        rows=[] if reasons else capability_diff_rows(
            payload, redact_permission_arguments=redact_permission_arguments
        ),
        unchanged_limits=limits,
        coverage=coverage,
    )
