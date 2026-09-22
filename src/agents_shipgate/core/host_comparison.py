"""Compare the host inventories selected by a caller, without creating policy."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agents_shipgate.core.boundary_registry import (
    is_claude_plugin_manifest_path,
    is_claude_plugin_marketplace_path,
)
from agents_shipgate.core.capability_diff_rows import (
    CapabilityDiffRow,
    capability_diff_rows,
    review_changes,
    review_question,
)
from agents_shipgate.core.host_grants import (
    _CLAUDE_PROJECT_SETTINGS_SOURCES,
    _PATH_REDACTION_MARKER,
    build_host_comparison_payload,
    build_host_drift_payload,
    build_host_grants_baseline,
    hook_loading_basis,
    host_grants_sha256,
    inventory_is_complete,
    normalized_host_grants,
)
from agents_shipgate.schemas.host_comparison import (
    COVERAGE_LIMIT_ORDER,
    MAX_COVERAGE_ITEMS,
    HostComparison,
    HostComparisonCoverage,
    HostComparisonCoverageItem,
    HostComparisonReview,
    HostComparisonReviewChange,
    HostComparisonReviewSummary,
)

#: Coverage's identity question (#812): every path it asks about, in one call,
#: to ``True`` (bytes proven identical), ``False`` (shown to differ) or
#: ``None`` (neither shown). A path left out of the answer reads ``None``.
IdentityAnswers = Callable[[Sequence[str]], Mapping[str, "bool | None"]]

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


#: Coverage order (#812): what refused the comparison, then changes no row
#: describes, then sources only one side published, then sources not proven
#: unchanged, then sources both sides read that gave rows, then sources compared
#: with no change. What the entries above the block cannot show comes before
#: what they already show, a file's rows, so the cap never counts an `env` edit
#: away behind files the entries already name (review cycle 5). The cap keeps a
#: prefix of this order.
#:
#: Within the blocking limits the kind decides next, most actionable first
#: (:data:`COVERAGE_LIMIT_ORDER`), because a refused comparison publishes
#: nothing else and the source name alone decided the cap: on a corpus
#: repository twenty-one routine `unsupported` items sorted ahead of the one
#: `unreadable` source and pushed it past ten (#812 follow-up). The source name
#: is the last word everywhere, so the order is total and the same two
#: inventories always publish the same list.
def _coverage_rank(item: dict[str, Any]) -> tuple[int, int, str, str, str]:
    limit = item.get("limit")
    if item["status"] == "blocking_limit":
        rank = 0
    elif item["status"] in {"changed_without_grant_change", "changed_without_rows"}:
        rank = 1
    elif item["side"] != "both":
        rank = 2
    elif item["status"] == "unchanged_not_proven":
        rank = 3
    elif item["rows"]:
        rank = 4
    else:
        rank = 5
    # These are raw facts, not model instances yet, so the key stays total for
    # a kind the tuple does not list: it sorts after every registered one
    # rather than ahead of them. That is not a safety net — `limit` is a closed
    # literal, so such an item is refused by the model right after this sort
    # rather than printed last. What keeps a newly registered kind in its place
    # is `test_every_published_limit_kind_has_a_place_in_the_order`.
    kind = (
        COVERAGE_LIMIT_ORDER.index(limit)
        if limit in COVERAGE_LIMIT_ORDER
        else len(COVERAGE_LIMIT_ORDER)
    )
    return (rank, kind, item["source"], item["side"], str(limit))


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


def _no_grant_change_shown(
    host: str, path: str, changes: list[dict[str, Any]], *, hook_basis_changed: bool
) -> bool:
    """Whether the data shows that a changed file with no row moved no grant this entry compares (#812).

    ``changes`` are the file's artifact changes. None means its bytes differ
    while its published artifact did not change, as for an edited ``env``
    value or ``apiKeyHelper``, whose values the artifact digest redacts. True
    only when the published data shows it:

    - The file's artifact digests the whole file. A plugin manifest or a
      marketplace publishes only its hooks, and only while it declares them,
      and the grants it selects are published under the hook files; a path
      with `#` is a projection of a file.
    - At most one artifact changed, and every side that has it parsed it. When
      both sides have it, nothing but that digest differs: a retargeted link
      (`resolved_through`), a parse status or an instruction structure is
      something this entry reads.
    - It is not a Claude Code project settings file while a hook's loading
      basis changed. Those settings decide which plugin hooks load, and that
      change is published on the hook file's grant, not on the settings file.

    It never says which fields changed: the digest also moves when rules are
    reordered or repeated, which changes no grant, and the change may be in
    fields a grant reads.
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
        or len(changes) > 1
    ):
        return False
    if not changes:
        return True
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
    identities: IdentityAnswers | None,
) -> HostComparisonCoverage:
    """What a comparable comparison established about each source it read (#812).

    Read off the payload the rows were projected from and the two inventories.
    A file's rows are the grant changes it publishes, including those of a
    source inside it. The side is which inventories published the file, as
    an artifact or as the file of a grant. A changed file that gives no row is
    ``changed_without_grant_change`` only where :func:`_no_grant_change_shown`
    shows it, and otherwise ``changed_without_rows``. A source named as an
    unchanged limit is already published there and is not repeated.

    A file both sides read that gives no row and whose artifact did not change
    takes its status from ``identities``, asked once for all such files. The
    artifact digest redacts values such as ``env`` values and
    ``apiKeyHelper``, so an equal digest is not an unchanged file:

    - ``True`` (its bytes are proven identical) is ``compared``.
    - ``False`` (Git shows its content differs) is a change with no row.
    - ``None`` (neither is shown, such as a difference only a checkout
      line-ending conversion makes, or a Git failure) is
      ``unchanged_not_proven``, never a change and never no change.

    The question is not asked, and the file is ``unchanged_not_proven``, when
    there is no ``identities`` (a provided diff), the published path is
    redacted and so names no file, the read followed a link
    (``resolved_through``), or no artifact publishes the source on both
    sides. The answers are private to this function; only the status is
    published.
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
    #: for: no file on such a host is said to have changed no compared grant.
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
    published: list[dict[tuple[str, str], list[dict[str, Any]]]] = []
    for inventory in (before, after):
        by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for artifact in inventory.get("artifacts", []):
            by_key.setdefault((str(artifact["host"]), str(artifact["path"])), []).append(artifact)
        published.append(by_key)

    def askable(key: tuple[str, str]) -> bool:
        """Whether the file's published path names one file both sides read directly."""

        path = key[1]
        read = [by_key.get(key, []) for by_key in published]
        return bool(
            identities is not None
            and all(read)
            and not any(artifact.get("resolved_through") for side in read for artifact in side)
            and not _PATH_REDACTION_MARKER.search(path)
            and not _REDACTION_DIGEST.search(path)
        )

    listed = [
        key for key in sorted(base | head | set(rows)) if not (key in named and not rows.get(key))
    ]

    def side_of(key: tuple[str, str]) -> str:
        return "both" if key in base and key in head else "base" if key in base else "head"

    # Only a zero-row file both sides read with an unchanged artifact asks;
    # one side only is an added or removed file. One question for all of
    # them, and one answer per file however many hosts read it.
    asked = {
        key
        for key in listed
        if not rows.get(key) and key not in changed and side_of(key) == "both"
    }
    paths = sorted({key[1] for key in asked if askable(key)})
    answers = identities(paths) if identities is not None and paths else {}

    facts: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    for key in listed:
        host, source = key
        side = side_of(key)
        count = rows.get(key, 0)
        same: bool | None = True
        if key in asked:
            same = answers.get(source) if askable(key) else None
        if count or (key not in changed and same):
            status = "compared"
        elif same is None:
            status = "unchanged_not_proven"
        elif host not in unattributed and _no_grant_change_shown(
            host, source, changed.get(key, []), hook_basis_changed=hook_basis_changed
        ):
            status = "changed_without_grant_change"
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
    identities: IdentityAnswers | None = None,
    coverage: bool = True,
) -> HostComparison:
    """Compare two inventories, refusing unless every limit is proven unchanged.

    ``unchanged`` answers whether one repository-relative source is identical
    on both sides; anything short of proof is ``False``. Without it, an
    incomplete inventory refuses the comparison as it always has.

    ``identities`` is coverage's own question, asked once with every path it
    needs (#812): for each, ``True`` when its bytes are proven identical,
    ``False`` when they are shown to differ, and ``None`` when neither is
    shown. It is separate from ``unchanged`` because "not proven identical"
    is not "changed": a line-ending conversion at checkout is neither.
    Without it, coverage calls no zero-row file unchanged or changed. A caller
    that publishes no coverage (`check`, a provided diff) passes
    ``coverage=False``: none is recorded and nothing is asked for it.
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
    established = (
        None
        if not coverage
        else _blocking_coverage(before, after)
        if reasons
        else _compared_coverage(before, after, payload, limits, identities)
    )
    rows = (
        []
        if reasons
        else capability_diff_rows(payload, redact_permission_arguments=redact_permission_arguments)
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
        rows=rows,
        unchanged_limits=limits,
        coverage=established,
        # How these rows are presented, decided here where the rows are built
        # and published with them, so the text and every JSON reader state the
        # same facts about the same comparison (#795).
        review=(
            None
            if reasons
            else host_comparison_review(
                rows, base_commit=base_commit, head_kind=head_kind, head_commit=head_commit
            )
        ),
    )


#: The tokens a reviewer copies to read the same comparison again (#795). The
#: canonical console script, not this process's spelling: the command is for
#: whoever reads the output, on their machine.
REPRODUCE_PROGRAM = "agents-shipgate"


def reproduce_command(
    *, base_commit: str | None, head_kind: str, head_commit: str | None
) -> str | None:
    """The command that reads this comparison again, or ``None`` where there is none.

    Only where the comparison names a base commit and its head is a commit or a
    working tree. A provided diff and `check` name no base commit, so they
    offer no command rather than one they cannot stand behind. For a commit
    head the command is run after checking that commit out; `diff` reads the
    working tree.
    """

    if not base_commit or head_kind not in {"commit", "worktree"}:
        return None
    if head_kind == "commit" and not head_commit:
        return None
    return f"{REPRODUCE_PROGRAM} diff --base {base_commit}"


def host_comparison_review(
    rows: Sequence[CapabilityDiffRow],
    *,
    base_commit: str | None,
    head_kind: str,
    head_commit: str | None,
) -> HostComparisonReview:
    """What the text says about these rows, as data (#795).

    One projection of :func:`review_changes`, so the block cannot count or
    classify a change differently from the sentence printed beside it. The
    question and the reproduction command are published only where the text
    prints them: the question after at least one change, and the command
    wherever the comparison names commits it can be run against, change or no
    change. A zero-row result is exactly where a reviewer asks how the answer
    was reached, so it is the last place to withhold the command (#812
    follow-up).
    """

    changes = review_changes(rows)
    return HostComparisonReview(
        changes=[
            HostComparisonReviewChange(
                row_indexes=list(change.row_indexes),
                severity=change.severity,
                direction=change.direction,
                subject=change.subject,
                before=change.before,
                after=change.after,
                change=change.change,
                why=change.why,
                expands=change.expands,
            )
            for change in changes
        ],
        summary=HostComparisonReviewSummary(
            rows=len(rows),
            changes=len(changes),
            widenings=sum(1 for change in changes if change.expands),
        ),
        question=review_question(changes) if changes else None,
        reproduce_command=reproduce_command(
            base_commit=base_commit, head_kind=head_kind, head_commit=head_commit
        ),
    )
