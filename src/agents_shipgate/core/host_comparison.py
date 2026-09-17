"""Compare the host inventories selected by a caller, without creating policy."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from agents_shipgate.core.boundary_registry import (
    is_claude_plugin_manifest_path,
    is_claude_plugin_marketplace_path,
)
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    _CLAUDE_PROJECT_SETTINGS_SOURCES,
    build_host_comparison_payload,
    build_host_drift_payload,
    build_host_grants_baseline,
    hook_loading_basis,
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
#: then changes no row describes, then sources only one side published, then
#: sources compared with no change. The cap keeps a prefix of this order.
def _coverage_rank(item: dict[str, Any]) -> tuple[int, str, str, str]:
    if item["status"] == "blocking_limit":
        rank = 0
    elif item["rows"]:
        rank = 1
    elif item["status"] in {"unread_fields_changed", "changed_without_rows"}:
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


#: The digest `public_host_path` stamps after a redacted component. A source
#: inside a file (`<file>#profiles.dev`) is redacted as one string, so its
#: digest differs from the file's own; matching a source to its file ignores
#: it (#812).
_REDACTION_DIGEST = re.compile(r"~[0-9a-f]{12}(?=/|#|$)")


def _file_of(source: str, files: set[str]) -> str:
    """The published file a grant source belongs to (#812).

    A source is its own file, or lies inside one: a Codex profile publishes
    `<file>#profiles.<name>` and a marketplace entry's inline hooks
    `<file>#plugins.<name>`. Only a published artifact path can own a source,
    so a `#` in a directory name is never taken for a separator. A source no
    published file owns, or that two files could own, stays its own.
    """

    if source in files or "#" not in source:
        return source
    digestless: dict[str, list[str]] = {}
    for path in sorted(files):
        digestless.setdefault(_REDACTION_DIGEST.sub("", path), []).append(path)
    for index in sorted((i for i, char in enumerate(source) if char == "#"), reverse=True):
        prefix = source[:index]
        if prefix in files:
            return prefix
        owners = digestless.get(_REDACTION_DIGEST.sub("", prefix), [])
        if len(owners) == 1:
            return owners[0]
        if owners:
            break
    return source


def _only_unread_fields_changed(
    host: str, path: str, changes: list[dict[str, Any]], *, hook_basis_changed: bool
) -> bool:
    """Whether a changed file that gives no row changed only in fields no grant reads (#812).

    True only when the published data shows it:

    - The file's artifact digests the whole file. A plugin manifest or a
      marketplace publishes only its hooks, and only while it declares them,
      and the grants it selects are published under the hook files; a path
      with `#` is a projection of a file.
    - One artifact changed, and every side that has it parsed it. When both
      sides have it, nothing but that digest differs: a retargeted link
      (`resolved_through`), a parse status or an instruction structure is
      something this entry reads.
    - It is not a Claude Code project settings file while a hook's loading
      basis changed. Those settings decide which plugin hooks load, and that
      change is published on the hook file's grant, not on the settings file.
    """

    if (
        "#" in path
        or is_claude_plugin_manifest_path(path)
        or is_claude_plugin_marketplace_path(path)
        or (
            hook_basis_changed
            and host == "claude-code"
            and path in _CLAUDE_PROJECT_SETTINGS_SOURCES
        )
        or len(changes) != 1
    ):
        return False
    before, after = changes[0].get("baseline"), changes[0].get("current")
    present = [artifact for artifact in (before, after) if artifact is not None]
    if not present or any(artifact.get("parse_status") != "parsed" for artifact in present):
        return False
    if before is None or after is None:
        return True
    return {key: value for key, value in before.items() if key != "redacted_sha256"} == {
        key: value for key, value in after.items() if key != "redacted_sha256"
    }


def _compared_coverage(
    before: dict[str, Any],
    after: dict[str, Any],
    payload: dict[str, Any],
    limits: list[dict[str, str]],
) -> HostComparisonCoverage:
    """What a comparable comparison established about each source it read (#812).

    Read off the payload the rows were projected from and the two inventories.
    A file's rows are the grant changes it publishes, including those of a
    source inside it. The side is which inventories published the file, as
    an artifact or as the file of a grant. A changed artifact that gives no
    row is ``unread_fields_changed`` only where
    :func:`_only_unread_fields_changed` shows it, and otherwise
    ``changed_without_rows``. A source named as an unchanged limit is already
    published there and is not repeated.
    """

    files: dict[str, set[str]] = {}
    for inventory in (before, after):
        for artifact in inventory.get("artifacts", []):
            files.setdefault(str(artifact["host"]), set()).add(str(artifact["path"]))

    def file_key(host: str, source: str) -> tuple[str, str]:
        return (host, _file_of(source, files.get(host, set())))

    def observed(inventory: dict[str, Any]) -> set[tuple[str, str]]:
        return {
            (str(artifact["host"]), str(artifact["path"]))
            for artifact in inventory.get("artifacts", [])
        } | {
            file_key(str(grant["host"]), str(grant["source"]))
            for grant in inventory.get("grants", [])
        }

    rows: dict[tuple[str, str], int] = {}
    hook_basis_changed = False
    #: Hosts with a row from inside a file no published file could be named
    #: for: no file on such a host is said to have changed only unread fields.
    unattributed: set[str] = set()
    for change in payload.get("changes") or []:
        grant = change.get("current") or change.get("baseline")
        if grant:
            key = file_key(str(grant["host"]), str(grant["source"]))
            rows[key] = rows.get(key, 0) + 1
            if "#" in key[1] and key[1] not in files.get(key[0], set()):
                unattributed.add(key[0])
        if (
            change.get("baseline") is not None
            and change.get("current") is not None
            and change["current"].get("kind") == "hook"
            and hook_loading_basis(change["baseline"]) != hook_loading_basis(change["current"])
        ):
            hook_basis_changed = True
    changed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for change in payload.get("artifact_changes") or []:
        artifact = change.get("current") or change.get("baseline")
        if artifact:
            key = (str(artifact["host"]), str(artifact["path"]))
            changed.setdefault(key, []).append(change)
    base, head = observed(before), observed(after)
    named = {(limit["host"], limit["source"]) for limit in limits}
    facts: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    for key in sorted(base | head | set(rows)):
        if key in named and not rows.get(key):
            continue
        host, source = key
        side = "both" if key in base and key in head else "base" if key in base else "head"
        count = rows.get(key, 0)
        if count or key not in changed:
            status = "compared"
        elif host not in unattributed and _only_unread_fields_changed(
            host, source, changed[key], hook_basis_changed=hook_basis_changed
        ):
            status = "unread_fields_changed"
        else:
            status = "changed_without_rows"
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
