"""Compare the host inventories selected by a caller, without creating policy."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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
    PluginScopeFacts,
    _issue_source_label,
    build_host_comparison_payload,
    build_host_drift_payload,
    hook_loading_basis,
    host_comparison_baseline,
    host_grants_sha256,
    inventory_is_complete,
    normalized_host_grants,
    public_host_path,
    without_host_sources,
)
from agents_shipgate.core.unread_inputs import (
    ChangedInputs,
    UnreadDiscovery,
    discover_unread_inputs,
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
#: change it points to, and a link the reader does not read through (#700) is
#: `unreadable` with no target read on either side. A source reached through
#: a link the reader does read through qualifies only when ``unchanged`` proves
#: the link and the file it lands on both unchanged (#822).
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


#: Coverage order (#812): what refused the comparison, then a changed input no
#: reader of this entry read (#821), then changes no row
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
#: `unreadable` source and pushed it past ten (#812 follow-up). Then the source
#: name, and after it every remaining field an item's identity is keyed by in
#: :func:`_group_coverage` — side, limit and status — so no two items can tie
#: and the same two inventories always publish the same list.
def _coverage_rank(item: dict[str, Any]) -> tuple[int, int, str, str, str, str]:
    limit = item.get("limit")
    if item["status"] == "blocking_limit":
        rank = 0
    elif item["status"] == "changed_not_read":
        # A changed input no reader read (#821) is the one change nothing
        # else in the output shows at all: not a row, not a limit, not a file
        # this entry compared. A refused comparison publishes only its limits
        # beside it, which stay first.
        rank = 1
    elif item["status"] in {"changed_without_grant_change", "changed_without_rows"}:
        rank = 2
    elif item["side"] != "both":
        rank = 3
    elif item["status"] == "unchanged_not_proven":
        rank = 4
    elif item["rows"]:
        rank = 5
    else:
        rank = 6
    # These are raw facts, not model instances yet, so the key stays total for
    # a kind the tuple does not list: it sorts after every registered one
    # rather than ahead of them. That is not a safety net — `limit` is a closed
    # literal, so an item carrying such a kind is refused by the model right
    # after this sort when the cap keeps it, and past the cap it is only
    # counted into `omitted_items` and never reaches the model. Either way it
    # is never printed last. What keeps a newly registered kind in its place is
    # `test_every_published_limit_kind_has_a_place_in_the_order`.
    kind = (
        COVERAGE_LIMIT_ORDER.index(limit)
        if limit in COVERAGE_LIMIT_ORDER
        else len(COVERAGE_LIMIT_ORDER)
    )
    # `status` is last because the rank does not separate every status: the two
    # statuses for a changed source with no row share rank 2, and
    # `_group_coverage`'s key keeps them apart, so two hosts reading one source
    # on one side can hold both at once. Without it the key ties there and only
    # `sorted`'s stability decides, which is weaker than the order this docstring
    # claims. Last, so no existing pair changes places.
    #
    # A `changed_not_read` item keys on its candidate rule where another keys
    # on its limit (#821), so that field keeps the key total for both.
    return (
        rank,
        kind,
        item["source"],
        item["side"],
        str(limit if limit is not None else item.get("candidate")),
        item["status"],
    )


def _group_coverage(
    facts: dict[tuple[str, str, str, str | None], dict[str, Any]],
    unread: UnreadDiscovery | None = None,
) -> HostComparisonCoverage:
    """One item per source, status, side and limit, its hosts merged and capped.

    ``unread`` adds the changed inputs no reader of this entry read (#821),
    one item per source, side and candidate rule, and records whether they
    were looked for; ``None`` records nothing about them.
    """

    facts = dict(facts)
    for fact in unread.facts if unread is not None else []:
        item = facts.setdefault(
            (fact["source"], fact["status"], fact["side"], fact["candidate"]),
            {**fact, "hosts": set()},
        )
        item["hosts"] |= fact["hosts"]
    items = sorted(facts.values(), key=_coverage_rank)
    return HostComparisonCoverage(
        items=[
            HostComparisonCoverageItem(**{**item, "hosts": sorted(item["hosts"])})
            for item in items[:MAX_COVERAGE_ITEMS]
        ],
        omitted_items=max(0, len(items) - MAX_COVERAGE_ITEMS),
        read_sources_only=not any(item["status"] == "changed_not_read" for item in items),
        unread_candidates=(
            None if unread is None else "examined" if unread.examined else "not_examined"
        ),
        unread_candidates_not_examined=unread.not_examined if unread is not None else 0,
    )


def _read_by_entry(before: dict[str, Any], after: dict[str, Any]) -> Callable[[str], bool]:
    """Whether either inventory published a file, so it is already an item of its own (#821).

    As an artifact, the file of a grant, or the source of a blocking issue:
    exactly the facts coverage names a source from. A non-blocking issue is
    not an item, so a file only one of those names is still named as unread.
    """

    names: set[str] = set()
    for inventory in (before, after):
        names.update(str(artifact["path"]) for artifact in inventory.get("artifacts", []))
        names.update(str(grant["source"]) for grant in inventory.get("grants", []))
        names.update(
            str(issue["source"]) for issue in inventory.get("issues", []) if issue.get("blocking")
        )
    names |= {name.split("#", 1)[0] for name in names}

    def read(path: str) -> bool:
        return public_host_path(path) in names or _issue_source_label(path) in names

    return read


def _unread(
    before: dict[str, Any], after: dict[str, Any], changed_inputs: ChangedInputs | None
) -> UnreadDiscovery | None:
    if changed_inputs is None:
        return None
    return discover_unread_inputs(changed_inputs, read_by_entry=_read_by_entry(before, after))


def _blocking_facts(
    before: dict[str, Any],
    after: dict[str, Any],
    scope_of: Mapping[tuple[str, str], str] | None = None,
) -> dict[tuple[str, str, str, str | None], dict[str, Any]]:
    """The blocking issues behind a refused or partial comparison, as coverage facts (#812).

    Only what the inventories already published: every blocking issue, keyed
    by its published source and kind. With ``scope_of`` — ``{(side, issue
    id): the directory a partial comparison left uncompared}`` (#808) — only
    those issues, each naming its directory as ``scope``: any other blocking
    issue of a partial comparison is an unchanged limit, named there instead.
    """

    Found = dict[tuple[str, str, str], tuple[str, str | None]]

    def blocking(side: str, inventory: dict[str, Any]) -> Found:
        found: Found = {}
        for issue in inventory.get("issues", []):
            if not issue.get("blocking"):
                continue
            scope = None
            if scope_of is not None:
                scope = scope_of.get((side, str(issue["issue_id"])))
                if scope is None:
                    continue
            key = (str(issue["source"]), str(issue["kind"]), str(issue["host"]))
            found[key] = (str(issue["message"]), scope)
        return found

    base, head = blocking("base", before), blocking("head", after)
    facts: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    for key in sorted(set(base) | set(head)):
        source, kind, host = key
        side = "both" if key in base and key in head else "base" if key in base else "head"
        # The head's message first, as a refused comparison has always named
        # it, and with it the directory the head's limit is bounded by.
        message, scope = head.get(key) or base[key]
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
                "detail": message,
                **({"scope": scope} if scope is not None else {}),
            },
        )
        item["hosts"].add(host)
    return facts


def _blocking_coverage(
    before: dict[str, Any], after: dict[str, Any], unread: UnreadDiscovery | None = None
) -> HostComparisonCoverage:
    """The sources behind a refused comparison, each with its limit and side (#812).

    Only what the inventories already published: every blocking issue, keyed
    by its published source and kind. The refusal itself is decided elsewhere
    and is not changed by naming them. A changed input this entry does not
    read (#821) is named beside them: it is not a source either side compared.
    """

    return _group_coverage(_blocking_facts(before, after), unread)


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
    unread: UnreadDiscovery | None = None,
) -> HostComparisonCoverage:
    """What a comparable comparison established about each source it read (#812).

    :func:`_compared_facts`, grouped and capped with any unread inputs.
    """

    return _group_coverage(_compared_facts(before, after, payload, limits, identities), unread)


def _compared_facts(
    before: dict[str, Any],
    after: dict[str, Any],
    payload: dict[str, Any],
    limits: list[dict[str, str]],
    identities: IdentityAnswers | None,
    *,
    hooks_withheld: bool = False,
) -> dict[tuple[str, str, str, str | None], dict[str, Any]]:
    """What a comparison established about each source it compared, as coverage facts (#812).

    Read off the payload the rows were projected from and the two inventories.
    A file's rows are the grant changes it publishes, including those of a
    source inside it. The side is which inventories published the file, as
    an artifact or as the file of a grant. A changed file that gives no row is
    ``changed_without_grant_change`` only where :func:`_no_grant_change_shown`
    shows it, and otherwise ``changed_without_rows``. A source named as an
    unchanged limit is already published there and is not repeated.

    ``hooks_withheld`` is a partial comparison's (#808): its inventories leave
    out the withheld directories, and with them any hook grant whose loading
    basis the project settings decide. Whether a settings change moved that
    basis is then not shown, so it is taken as moved, and a changed project
    settings file with no row is ``changed_without_rows``, never
    ``changed_without_grant_change``.

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
    # A withheld hook grant is compared nowhere, so its basis is not shown unmoved.
    hook_basis_changed = hooks_withheld
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
    return facts


#: Both sides' plugin reference graphs, base first (#808).
PluginScopes = tuple[PluginScopeFacts, PluginScopeFacts]


def _within(path: str, directory: str) -> bool:
    """Whether ``path`` is ``directory`` or lies under it, compared case-insensitively.

    Folded because the plugin reader matches a reference to a file
    case-insensitively (#714): a selection may reach a file spelled otherwise.
    """

    folded, root = path.casefold(), directory.casefold()
    return folded == root or folded.startswith(f"{root}/")


def _bounded(root: str | None) -> bool:
    """Whether a plugin directory can bound what a limit hides (#808).

    It must name a directory below the repository root, and publish as itself:
    a redacted or shortened path would no longer be a prefix of the published
    paths under it, and a ``#`` would be read as a member separator.
    """

    return bool(root) and "#" not in str(root) and _issue_source_label(str(root)) == root


@dataclass(frozen=True)
class _Retained:
    """The part of a comparison that no withheld plugin directory can affect (#808)."""

    #: The outermost directories left uncompared, in order.
    scopes: tuple[str, ...]
    #: Both inventories without anything under them.
    before: dict[str, Any]
    after: dict[str, Any]
    #: The unchanged limits of what remains.
    limits: list[dict[str, str]]
    #: ``{(side, issue id): the directory it left uncompared}``.
    scope_of: dict[tuple[str, str], str]


def _independent_of_plugin_scopes(
    before: dict[str, Any],
    after: dict[str, Any],
    scopes: PluginScopes,
    unchanged: Callable[[str], bool] | None,
) -> _Retained | None:
    """The comparison outside the plugin directories its limits are bounded by, or ``None`` (#808).

    Independence is read off the reference graph the inventories were built
    from, never off directory names alone. A plugin-reference limit can hide
    only what that plugin's references select, and a reference is followed
    only inside its plugin directory (#714), so what it hides is published
    under that directory. In this entry's repository scope every other grant
    is read from its own file alone. Two edges leave a plugin directory, and
    either one refuses retention, because a row outside the directory would
    then depend on what is inside it:

    - project settings (``.claude/settings.json``, ``.claude/settings.local.json``)
      decide the loading basis of every plugin's hooks, so a withheld
      directory holding them withholds nothing independently;
    - a marketplace entry outside a withheld directory can declare inline
      hooks for the plugin inside it, and those grants are published under
      the marketplace.

    ``None`` — refuse as before — whenever independence is not established:
    a blocking limit that is not a plugin reference and not an unchanged
    limit of the rest, a reference no directory bounds (one naming a path
    outside its plugin), a plugin at the repository root, a directory that
    does not publish as itself, either edge above, a host coverage status
    this reader does not derive from its issues, or nothing read outside the
    directories at all, which would retain nothing.
    """

    if any(
        item.get("status") not in {"complete", "partial"}
        for inventory in (before, after)
        for item in inventory.get("host_coverage", [])
    ):
        # What remains has its coverage recomputed from its artifacts and
        # blocking issues, which can only say `complete` or `partial`; any
        # other status would be lost, so nothing is retained past one.
        return None
    roots_by_issue: dict[tuple[str, str], frozenset[str | None]] = {}
    for side, inventory, facts in (("base", before, scopes[0]), ("head", after, scopes[1])):
        for issue in inventory.get("issues", []):
            if not issue.get("blocking"):
                continue
            roots = facts.issue_roots.get(str(issue["issue_id"]))
            if roots is None:
                continue  # Not a plugin reference: what remains must answer for it.
            if not roots or not all(_bounded(root) for root in roots):
                return None
            roots_by_issue[(side, str(issue["issue_id"]))] = roots
    if not roots_by_issue:
        return None
    outer: list[str] = []
    for root in sorted(
        {str(root) for roots in roots_by_issue.values() for root in roots},
        key=lambda root: (root.count("/"), root),
    ):
        if not any(_within(root, kept) for kept in outer):
            outer.append(root)

    def withheld(path: str) -> bool:
        return any(_within(path, root) for root in outer)

    if any(withheld(settings) for settings in _CLAUDE_PROJECT_SETTINGS_SOURCES):
        return None
    for facts in scopes:
        for source, root in facts.inline_roots.items():
            # The whole source: its member only extends the marketplace's path.
            if withheld(root) and not withheld(source):
                return None
    ids = {
        side: {issue for key_side, issue in roots_by_issue if key_side == side}
        for side in ("base", "head")
    }
    rest_before = without_host_sources(before, issue_ids=ids["base"], withheld=withheld)
    rest_after = without_host_sources(after, issue_ids=ids["head"], withheld=withheld)
    if not any(
        inventory.get("artifacts") or inventory.get("grants")
        for inventory in (rest_before, rest_after)
    ):
        return None
    limits: list[dict[str, str]] | None = []
    if not (inventory_is_complete(rest_before) and inventory_is_complete(rest_after)):
        # Whatever remains incomplete must be a limit both sides share on a
        # source the change did not touch, proven exactly as #721 proves one.
        limits = (
            unchanged_limits(rest_before, rest_after, unchanged) if unchanged is not None else None
        )
    if limits is None:
        return None
    scope_of = {
        key: next(root for root in outer if any(_within(str(each), root) for each in roots))
        for key, roots in roots_by_issue.items()
    }
    return _Retained(
        scopes=tuple(sorted(outer)),
        before=rest_before,
        after=rest_after,
        limits=limits,
        scope_of=scope_of,
    )


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
    changed_inputs: ChangedInputs | None = None,
    plugin_scopes: PluginScopes | None = None,
) -> HostComparison:
    """Compare two inventories, refusing unless every limit is proven unchanged.

    ``unchanged`` answers whether one repository-relative source is identical
    on both sides, and for a source reached through an in-tree link, that the
    link and the file it lands on both are (#822); anything short of proof is
    ``False``. Without it, an incomplete inventory refuses the comparison as it
    always has.

    ``identities`` is coverage's own question, asked once with every path it
    needs (#812): for each, ``True`` when its bytes are proven identical,
    ``False`` when they are shown to differ, and ``None`` when neither is
    shown. It is separate from ``unchanged`` because "not proven identical"
    is not "changed": a line-ending conversion at checkout is neither.
    Without it, coverage calls no zero-row file unchanged or changed. A caller
    that publishes no coverage (`check`, a provided diff) passes
    ``coverage=False``: none is recorded and nothing is asked for it.

    ``changed_inputs`` is the comparison's own changed-file set and a bounded
    way to look at it (#821). With it, coverage also names each changed input
    a documented candidate rule recognises and no reader of this entry read,
    and records whether the set could be listed. Without it, coverage records
    nothing about such inputs. It never touches the rows, the reasons, the
    limits or the digests.

    ``plugin_scopes`` are both readers' plugin reference graphs (#808). With
    them, and with coverage recorded, a comparison refused only by
    plugin-reference limits that each plugin directory bounds, and that no
    compared source depends on, is ``partial`` instead: those directories are
    left uncompared on both sides and named, as ``scope``, on the blocking
    limits that caused it, and the rest is compared as a comparable
    comparison would be, so any other blocking limit must be one
    ``unchanged`` proves, and is then named in ``unchanged_limits``.
    ``incomparable_reasons`` stays what the refusal would have said. Without
    coverage the scope could be named nowhere, so such a comparison refuses
    as before; that is `check`'s.
    """

    reasons: list[str] = []
    limits: list[dict[str, str]] = []
    retained: _Retained | None = None
    if not (inventory_is_complete(before) and inventory_is_complete(after)):
        shared = unchanged_limits(before, after, unchanged) if unchanged is not None else None
        if shared is None:
            if not inventory_is_complete(before):
                reasons.append("base_inventory_incomplete")
            if not inventory_is_complete(after):
                reasons.append("head_inventory_incomplete")
            if coverage and plugin_scopes is not None:
                retained = _independent_of_plugin_scopes(before, after, plugin_scopes, unchanged)
        else:
            limits = shared
    baseline_file = base_commit or "compared input"
    if retained is not None:
        # Both sides without the withheld directories, whose remaining limits
        # were proven unchanged exactly as a comparable comparison's are.
        limits = retained.limits
        payload: dict = build_host_comparison_payload(
            before=retained.before, after=retained.after, baseline_file=baseline_file
        )
    elif reasons:
        payload = {}
    elif limits:
        payload = build_host_comparison_payload(before=before, after=after, baseline_file=baseline_file)
    else:
        payload = build_host_drift_payload(
            baseline=host_comparison_baseline(before),
            inventory=after,
            baseline_file=baseline_file,
        )
    if payload.get("incomparable_reasons"):
        reasons.extend(payload["incomparable_reasons"])
        retained = None
    # Refused: nothing was compared, so no row, limit or review is published.
    refused = bool(reasons) and retained is None
    if refused:
        limits = []
    # What the run established, from the facts above and nothing else (#812).
    # It stays on the comparison: it is not an input to the inventory digests,
    # a saved baseline or the rows.
    unread = _unread(before, after, changed_inputs) if coverage else None
    if not coverage:
        established = None
    elif refused:
        established = _blocking_coverage(before, after, unread)
    elif retained is not None:
        # The limits that left a directory uncompared, each naming it, then
        # what the rest established, read off the inventories it compared.
        established = _group_coverage(
            {
                **_blocking_facts(before, after, retained.scope_of),
                **_compared_facts(
                    retained.before,
                    retained.after,
                    payload,
                    limits,
                    identities,
                    hooks_withheld=True,
                ),
            },
            unread,
        )
    else:
        established = _compared_coverage(before, after, payload, limits, identities, unread)
    rows = (
        []
        if refused
        else capability_diff_rows(payload, redact_permission_arguments=redact_permission_arguments)
    )
    return HostComparison(
        comparison_status=(
            "incomparable" if refused else "partial" if retained is not None else "comparable"
        ),
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
            if refused
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
