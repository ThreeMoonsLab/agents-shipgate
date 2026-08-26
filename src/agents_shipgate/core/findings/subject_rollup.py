"""Human-facing findings, grouped by the thing they are about.

Issue #364.  The flat, severity-ordered list every human surface printed is
the right shape for ``report.json``, where a consumer joins rows itself, and
the wrong shape for a person: a scan of four money-moving tools produced
seventeen rows across five check families, and the three-row summary spent all
three on one check family repeated over sibling tools.  The reader was told the
same thing three times and never told about scopes, idempotency, owners, or
guardrails at all.

Severity is not the axis a reader acts along.  They act tool by tool — open
one, fix what is wrong with it, move on — so the subject is the group key and
severity is an attribute of the rows inside it.

Three surfaces render this: ``scan`` stdout, ``report.md``, and the PR comment.
They read one projection because rendering the same rollup three times is how
three surfaces come to disagree about how many findings a tool has.  Nothing
here reaches ``report.json``, SARIF, the release decision, fingerprints, or
baselines — ``findings[]`` stays the flat per-finding record automation
consumes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agents_shipgate.core.evidence_actions import display_literal
from agents_shipgate.core.surface_exclusions import catalog_subject, derived_id_kind
from agents_shipgate.schemas.report import Finding, ReadinessReport

from .constants import SEVERITY_ORDER

__all__ = [
    "AGENT_WIDE_SUBJECT",
    "SubjectGroup",
    "finding_line",
    "group_summary",
    "missing_controls",
    "roll_up_findings",
    "rollup_detail",
    "rollup_headline",
    "source_suffix",
    "top_findings_block",
]


def _identity(value: str) -> str:
    """The escaper a plain-console surface passes."""

    return value


def _no_annotations(_finding: Finding) -> Sequence[str]:
    """No extra lines under a row — what a compact surface passes."""

    return ()


#: What a finding that names no tool is about, when the binding graph cannot
#: name the agent.  Never a derived agent id: that is the spelling #329 took
#: out of adopter-facing subjects, and a digest names nothing the reader can
#: open.
AGENT_WIDE_SUBJECT = "agent-wide"

#: Severities that reach a human summary on their own.  Anything the release
#: decision names is included regardless of severity: a medium the decision is
#: citing is, by definition, part of the verdict the reader is reading.
_ALWAYS_SHOWN_SEVERITIES = frozenset({"critical", "high"})

#: Separator for the fallback join key. A finding that carries neither an id
#: nor a fingerprint is matched to a decision item by check id and title, and
#: NUL cannot occur in either, so no pair of values can collide by
#: concatenating differently.
_KEY_SEPARATOR = "\x00"


@dataclass(frozen=True)
class SubjectGroup:
    """Every selected finding about one tool, or about one agent as a whole."""

    subject: str
    kind: Literal["tool", "agent"]
    findings: tuple[Finding, ...]
    blocking: tuple[bool, ...]
    #: The source suffix every row in the group shares, or ``""`` when they do
    #: not all share one. Hoisted to the heading so a tool whose findings all
    #: come from one file says where once instead of once per row; a group
    #: whose rows disagree (``samples/conductor_agent``) keeps the suffix on
    #: the rows, which is where it distinguishes them.
    location: str = ""

    @property
    def blocks_release(self) -> bool:
        return any(self.blocking)

    @property
    def severity(self) -> str:
        """The most severe row in the group."""

        return min(
            (finding.severity for finding in self.findings),
            key=lambda severity: SEVERITY_ORDER[severity],
        )

    @property
    def count(self) -> int:
        return len(self.findings)

    def rows(self) -> tuple[tuple[Finding, bool], ...]:
        """Findings paired with whether the release decision blocks on each."""

        return tuple(zip(self.findings, self.blocking, strict=True))


def missing_controls(finding: Finding) -> list[str]:
    """The control paths ``finding`` says are absent, in evidence order.

    Two shapes reach here: the built-in control checks write
    ``missing: ["safeguards.audit_log", …]``, and the action-policy checks
    write ``missing: [{"path": …, "expected": …}, …]``.  Both are read, and
    anything else returns empty rather than guessing — a summary line is not
    worth inventing a fact for.
    """

    raw = finding.evidence.get("missing")
    if not isinstance(raw, list):
        return []
    paths: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            paths.append(item.strip())
        elif isinstance(item, Mapping):
            path = item.get("path")
            if isinstance(path, str) and path.strip():
                paths.append(path.strip())
    return paths


def rollup_detail(finding: Finding) -> str:
    """The one line under a subject saying what this finding wants.

    ``evidence.missing`` when the check has one, because "missing:
    safeguards.audit_log, safeguards.idempotency" is the whole decision; the
    title otherwise.  The recommendation is deliberately not used: since #364
    it is derived from the same ``missing`` list, so rendering both would
    print one fact twice in different words.
    """

    missing = missing_controls(finding)
    if missing:
        return "missing: " + ", ".join(missing)
    return finding.title


def roll_up_findings(report: ReadinessReport) -> list[SubjectGroup]:
    """Selected findings grouped by subject, most-urgent group first.

    Selection is one rule for all three surfaces: an active finding that is
    critical or high, or that the release decision names as a blocker or a
    review item.  Before #364 the three surfaces each had their own rule and
    their own limit, so one scan reported a different "top" three ways.

    A group blocks when the release decision names one of its findings as a
    blocker — not when a finding carries ``blocks_release``, which stays true
    on debt a baseline has accepted.

    Ordering is blocking groups first, then by the group's most severe row,
    then by how much is wrong with the subject, then by name — so the tie
    break is deterministic and a rerun on an unchanged repo prints an
    unchanged summary.
    """

    decision = report.release_decision
    blocking = _decision_index(decision.blockers if decision else [])
    named = _decision_index(
        [*decision.blockers, *decision.review_items] if decision else []
    )
    tool_labels = _tool_label_index(report)
    tool_ids_by_name = _tool_id_by_name(report)
    agent_labels = _agent_label_index(report)

    subjects: dict[tuple[str, str], str] = {}
    members: dict[tuple[str, str], list[tuple[Finding, bool]]] = {}
    for finding in report.findings:
        if finding.suppressed:
            continue
        if finding.severity not in _ALWAYS_SHOWN_SEVERITIES and not named.names(
            finding
        ):
            continue
        group_key, subject = _subject(
            finding, tool_labels, tool_ids_by_name, agent_labels
        )
        # The release decision is the authority on what blocks, and
        # `finding.blocks_release` is not the same claim: a policy finding
        # whose debt a baseline has accepted keeps the flag and is filed as a
        # *review item*. Reading the flag would print "BLOCKS RELEASE" two
        # lines under a decision that says otherwise. It is consulted only
        # when there is no decision to contradict.
        blocks = blocking.names(finding) if decision else finding.blocks_release
        members.setdefault(group_key, []).append((finding, blocks))
        # First writer names the group. The key is identity (a canonical tool
        # id where there is one) and the subject is a label; resolving the
        # label per finding would let two findings about one tool print two
        # spellings of it.
        subjects.setdefault(group_key, subject)

    built = [
        _build_group(group_key, subjects[group_key], rows_for_key)
        for group_key, rows_for_key in members.items()
    ]
    built.sort(key=_group_sort_key)
    return built


def _build_group(
    group_key: tuple[str, str],
    subject: str,
    members: list[tuple[Finding, bool]],
) -> SubjectGroup:
    ordered = sorted(
        members,
        key=lambda row: (
            SEVERITY_ORDER[row[0].severity],
            row[0].check_id,
            row[0].title,
        ),
    )
    kind: Literal["tool", "agent"] = "tool" if group_key[0] == "tool" else "agent"
    suffixes = {source_suffix(row[0]) for row in ordered}
    shared = suffixes.pop() if len(suffixes) == 1 else ""
    return SubjectGroup(
        subject=subject,
        kind=kind,
        findings=tuple(row[0] for row in ordered),
        blocking=tuple(row[1] for row in ordered),
        location=shared,
    )


def _group_sort_key(group: SubjectGroup) -> tuple[Any, ...]:
    return (
        not group.blocks_release,
        SEVERITY_ORDER[group.severity],
        -group.count,
        group.subject,
    )


@dataclass(frozen=True)
class _DecisionIndex:
    """Which findings a list of release-decision items names.

    Three tiers, in descending precision, and each one holds only the items
    that could not supply the tier above it.  That "only" is the whole design:
    a weaker key is a *fallback for items that have no better one*, never a
    second chance for an item that does.

    ``ids`` — ``Finding.id``, the one value that identifies a finding
    uniquely.  ``fingerprints`` — for an item with no id; a fingerprint hashes
    check id, tool id and evidence, so two findings can share one, which is
    exactly why ``assign_finding_ids`` appends a discriminator when they do.
    Consulting it for an item that *has* an id would mark the collision
    partner as blocking too.  ``unkeyed`` — ``check_id`` + ``title``, for an
    item with neither, the shape a report predating id assignment is in.
    ``samples/conductor_agent`` ships two findings with one check id and one
    title, so applying that tier more widely marks both when one is named.
    """

    ids: frozenset[str]
    fingerprints: frozenset[str]
    unkeyed: frozenset[str]

    def names(self, finding: Finding) -> bool:
        if finding.id and finding.id in self.ids:
            return True
        if finding.fingerprint and finding.fingerprint in self.fingerprints:
            return True
        if not self.unkeyed:
            return False
        return f"{finding.check_id}{_KEY_SEPARATOR}{finding.title}" in self.unkeyed


def _decision_index(items: Sequence[Any]) -> _DecisionIndex:
    ids: set[str] = set()
    fingerprints: set[str] = set()
    unkeyed: set[str] = set()
    for item in items:
        item_id = getattr(item, "id", None)
        fingerprint = getattr(item, "fingerprint", None)
        if item_id:
            ids.add(item_id)
        elif fingerprint:
            fingerprints.add(fingerprint)
        else:
            unkeyed.add(f"{item.check_id}{_KEY_SEPARATOR}{item.title}")
    return _DecisionIndex(
        ids=frozenset(ids),
        fingerprints=frozenset(fingerprints),
        unkeyed=frozenset(unkeyed),
    )


def _tool_label_index(report: ReadinessReport) -> dict[str, str]:
    """Canonical tool id to the one display label that names that tool.

    Built on :func:`catalog_subject` so a subject reads here exactly as it
    reads in the exclusion ledger and the evidence gaps — that shared spelling
    is the whole reason the function exists.

    Rows with no ``name`` are dropped rather than labelled.  ``catalog_subject``
    falls back to the tool id for those, and its fallback is safe where it is
    used (a ledger entry joined by value) and unsafe here: a group heading is
    the most adopter-facing string this module emits, and ``tool_v2_6dcebe…``
    names nothing the reader can open (#329).  A finding on such a tool falls
    through to its own ``tool_name``, and then to the agent.
    """

    labels: dict[str, str] = {}
    for row in report.tool_catalog:
        if not isinstance(row, Mapping):
            continue
        tool_id = row.get("tool_id") or row.get("id")
        if not tool_id or not str(row.get("name") or "").strip():
            continue
        labels[str(tool_id)] = catalog_subject(row)
    return labels


def _tool_id_by_name(report: ReadinessReport) -> dict[str, str]:
    """Tool name to canonical id, for the names that resolve to exactly one.

    Not every finding about a tool carries its id: ``checks.baseline_integrity``
    emits ``tool_id=None`` with a name, so a tool with one of those and one
    ordinary finding produced *two* headings for one tool — ``create_refund``
    and ``create_refund [stripe]`` — which is the second-spelling failure
    ``catalog_label_index`` exists to prevent, arrived at from the other side.

    An ambiguous name is left unresolved rather than guessed.  Two tools can
    share a name across sources; picking one would file a finding under a tool
    it is not about, and that is worse than a heading the reader has to
    reconcile.
    """

    seen: dict[str, str | None] = {}
    for row in report.tool_catalog:
        if not isinstance(row, Mapping):
            continue
        tool_id = row.get("tool_id") or row.get("id")
        name = str(row.get("name") or "").strip()
        if not tool_id or not name:
            continue
        # None marks a name that more than one tool answers to.
        seen[name] = None if name in seen else str(tool_id)
    return {name: tool_id for name, tool_id in seen.items() if tool_id}


def _agent_label_index(report: ReadinessReport) -> dict[str, str]:
    """The one agent a report is about, keyed by the id its findings carry.

    Not :func:`agents_shipgate.core.surface_exclusions.agent_label_index`,
    which indexes the *binding graph* — those nodes are keyed by ``agent_v1:``
    digests, while ``Finding.agent_id`` is the manifest-derived
    ``agent:<project>/<agent>``.  The two id spaces never intersect, so
    reaching for the graph index here reads correct and resolves nothing.

    The label is the declared ``agent.name``, refused if it happens to carry a
    derived id shape — an adopter may legally name an agent anything, and a
    digest is exactly as unreadable when they typed it as when we built it.
    """

    agent = report.agent or {}
    agent_id = agent.get("id")
    name = str(agent.get("name") or "").strip()
    if not agent_id or not name or derived_id_kind(name):
        return {}
    return {str(agent_id): name}


def _subject(
    finding: Finding,
    tool_labels: Mapping[str, str],
    tool_ids_by_name: Mapping[str, str],
    agent_labels: Mapping[str, str],
) -> tuple[tuple[str, str], str]:
    """This finding's group key and the label that names it.

    For a tool the key is identity and the label is display, and they are not
    the same string: two tools can share a name across sources, so grouping by
    name would merge them, while labelling by id would print a digest at
    someone who has never seen one (#329).  Tool labels come from the catalog
    index for the reason that index exists — an emitter that builds a label
    from its own fields produces a second spelling of a subject other surfaces
    already name.

    For an agent the *label* is the key.  An agent whose name does not resolve
    has no readable subject, and keying those on the id would print two
    indistinguishable ``agent-wide`` groups; collapsing them into one says the
    same thing without implying the reader can tell them apart.
    """

    if finding.tool_id:
        # Never chain the fallback back to the id: a derived id is refused in a
        # display subject, so an unlabelled, unnamed tool falls through to the
        # agent-wide phrasing rather than printing a digest.
        label = tool_labels.get(finding.tool_id) or finding.tool_name or ""
        if label:
            return ("tool", finding.tool_id), label
    if finding.tool_name:
        # A finding that carries only a name is still about a catalog tool
        # where the name resolves to exactly one, and keying it on the name
        # would give that tool a second heading beside its own.
        resolved = tool_ids_by_name.get(finding.tool_name)
        if resolved and resolved in tool_labels:
            return ("tool", resolved), tool_labels[resolved]
        return ("tool", f"name:{finding.tool_name}"), finding.tool_name
    name = agent_labels.get(finding.agent_id or "")
    label = f"{name} ({AGENT_WIDE_SUBJECT})" if name else AGENT_WIDE_SUBJECT
    return ("agent", label), label


#: Reading order for a severity histogram — worst first, and always this
#: order, so the same group reads the same way on every surface.
_SEVERITY_DISPLAY_ORDER: tuple[str, ...] = ("critical", "high", "medium", "low")


def group_summary(group: SubjectGroup) -> str:
    """The attributes of a subject: whether it blocks, and what it holds.

    Severity moves here from the sort key it used to be.  It still decides
    urgency *within* the list, but a reader picking what to open next needs to
    know that this tool blocks the release before they need to know that its
    worst row is critical rather than high.
    """

    counts = Counter(finding.severity for finding in group.findings)
    histogram = ", ".join(
        f"{counts[severity]} {severity}"
        for severity in _SEVERITY_DISPLAY_ORDER
        if counts[severity]
    )
    status = "BLOCKS RELEASE" if group.blocks_release else "review"
    return f"{status} ({histogram})"


def source_suffix(finding: Finding) -> str:
    """Where in the repository this row is about, or empty.

    Restores what the flat markdown row carried in its ``Evidence:`` line and
    the console never had.  It is not decoration: ``samples/conductor_agent``
    emits the same check twice with the same title, and without the pointer
    the two rows are one row printed twice.

    Rendered through :func:`display_literal` because a path names something
    the reader will open — an escape keeps a zero-width character visible and
    recoverable, where folding or deleting it would quietly name a different
    file.  This is display only; no consumer routes on it.
    """

    source = finding.source
    if source is None:
        return ""
    path = (source.path or "").strip()
    if not path:
        ref = (source.ref or "").strip()
        return f" (at {display_literal(ref)})" if ref else ""
    pointer = (source.pointer or "").strip()
    if pointer:
        return f" (at {display_literal(path)}#{display_literal(pointer)})"
    if source.start_line is not None:
        return f" (at {display_literal(path)}:{source.start_line})"
    return f" (at {display_literal(path)})"


def finding_line(
    finding: Finding,
    *,
    blocks_release: bool,
    group_location: str = "",
) -> str:
    """One row under a subject: severity, which check, what it wants, where.

    ``group_location`` is the suffix the heading already carries.  A row that
    matches it stays silent rather than repeating the same file on every line;
    a row that differs prints its own, which is the case the suffix exists for.
    """

    marker = " (blocks release)" if blocks_release else ""
    own = source_suffix(finding)
    suffix = "" if own == group_location else own
    return (
        f"{finding.severity} {finding.check_id}{marker} — "
        f"{rollup_detail(finding)}{suffix}"
    )


def rollup_headline(groups: Sequence[SubjectGroup]) -> str:
    """``14 findings across 5 subjects`` — the count both headers carry."""

    return f"{_finding_count(groups)} across {_subject_count(groups)}"


def top_findings_block(
    groups: Sequence[SubjectGroup],
    *,
    group_limit: int,
    row_limit: int,
    escape: Callable[[str], str] = _identity,
    heading: str | None = "Top findings",
    bullet: str = "- ",
    row_prefix: str = "    ",
    note_prefix: str = "      ",
    annotate: Callable[[Finding], Sequence[str]] = _no_annotations,
) -> list[str]:
    """The whole block, for a surface that differs only in how it decorates.

    One function rather than three near-copies: the projection above stops the
    surfaces disagreeing about *what* the groups are, and this stops them
    disagreeing about how many of them a reader gets to see.  What a caller
    supplies is decoration — an escaper (markdown surfaces pass theirs, the
    console passes none) and the three line prefixes, because a console indent
    and a markdown nested list are the same structure in two syntaxes.

    Both limits truncate with a count of what was cut, never silently: a
    summary that drops rows without saying so is how the flat list came to
    look complete while showing three of seventeen.

    ``annotate`` is for the one surface with room to carry more than the
    decision — ``report.md``, which keeps each row's recommendation under it.
    The console and the PR comment stay at one line per finding and point at
    the report.  ``heading=None`` suppresses the header line for a surface
    that already has one, and :func:`rollup_headline` gives it the same counts.
    """

    lines: list[str] = []
    if heading:
        suffix = f" ({rollup_headline(groups)})" if groups else ""
        lines.append(f"{heading}{suffix}:")
    if not groups:
        lines.append(f"{bullet}none")
        return lines
    shown = list(groups[:group_limit])
    for group in shown:
        lines.append(
            f"{bullet}{escape(group.subject + group.location)} — "
            f"{escape(group_summary(group))}"
        )
        rows = group.rows()
        for finding, blocks in rows[:row_limit]:
            row = finding_line(
                finding,
                blocks_release=blocks,
                group_location=group.location,
            )
            lines.append(f"{row_prefix}{escape(row)}")
            for note in annotate(finding):
                lines.append(f"{note_prefix}{escape(note)}")
        hidden = len(rows) - row_limit
        if hidden > 0:
            lines.append(
                f"{row_prefix}… and {_plural(hidden, 'more finding')} for this subject"
            )
    hidden_groups = len(groups) - len(shown)
    if hidden_groups > 0:
        lines.append(f"{bullet}… and {_plural(hidden_groups, 'more subject')}")
    return lines


def _plural(count: int, noun: str) -> str:
    """``1 subject`` / ``2 subjects``. Written out rather than ``(s)``: this
    text is read by someone deciding whether to keep reading."""

    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _subject_count(groups: Sequence[SubjectGroup]) -> str:
    return _plural(len(groups), "subject")


def _finding_count(groups: Sequence[SubjectGroup]) -> str:
    return _plural(sum(group.count for group in groups), "finding")
