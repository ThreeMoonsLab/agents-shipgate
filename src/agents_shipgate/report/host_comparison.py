"""Human projection of host evidence shared by CLI and PR output."""

from __future__ import annotations

import re
from collections.abc import Callable

from agents_shipgate.core.agent_control_envelope import single_line_text
from agents_shipgate.core.boundary_registry import (
    is_claude_plugin_manifest_path,
    is_claude_plugin_marketplace_path,
)
from agents_shipgate.core.capability_diff_rows import ReviewChange, review_changes
from agents_shipgate.core.host_grants import _source_kind
from agents_shipgate.schemas.host_comparison import HostComparison, HostComparisonCoverageItem

#: The tokens a reviewer copies to read the same comparison again (#795). The
#: canonical console script, not this process's spelling: the command is for
#: whoever reads the output, on their machine.
REPRODUCE_PROGRAM = "agents-shipgate"


def review_question(changes: list[ReviewChange]) -> str:
    """One bounded question for a reviewer, asked only about changes that exist (#795).

    Where an entry joins rows, the question names the row count too: the
    control headline beside it in `verify` and the PR comment counts rows
    (`8 repository-declared host capability change(s)`), and `diff`'s summary
    already says `from 8 rows`.
    """

    # `capability`, not `permission`: an entry may be an MCP server, a hook, a
    # workflow grant or instructions as well as a permission rule.
    rows = sum(change.rows for change in changes)
    bridge = f" (from {rows} rows)" if rows != len(changes) else ""
    if len(changes) == 1:
        return f"Review question: Does the team intend this declared capability change{bridge}?"
    return (
        f"Review question: Does the team intend these {len(changes)} declared capability "
        f"changes{bridge}?"
    )


def comparison_reference_lines(
    comparison: HostComparison, *, markdown: bool = False
) -> list[str]:
    """The compared commits, the tool version, and a command that reads the same comparison.

    Only where the comparison names a base commit and its head is a commit or a
    working tree. A provided diff and `check`'s text name no base commit, so
    they print no reference rather than one they cannot stand behind. A commit
    head is checked out first: `diff` reads the working tree.
    """

    from agents_shipgate import __version__

    base, head = comparison.base_commit, comparison.head_commit
    if not base or comparison.head_kind not in {"commit", "worktree"}:
        return []
    if comparison.head_kind == "commit" and not head:
        return []
    command = f"{REPRODUCE_PROGRAM} diff --base {base}"
    if markdown:
        command = f"`{command}`"
    if comparison.head_kind == "commit":
        return [
            f"Compared: base {base[:8]} → head {head[:8]}, agents-shipgate {__version__}.",
            f"Reproduce: check out {head}, then run {command}",
        ]
    at = f" at HEAD {head[:8]}" if head else ""
    return [
        f"Compared: base {base[:8]} → working tree{at}, agents-shipgate {__version__}.",
        f"Reproduce in that working tree: {command}",
    ]


def _text(value: object, *, markdown: bool) -> str:
    value = single_line_text(str(value))
    if markdown:
        # Inline code with a delimiter longer than any untrusted backtick run.
        width = max((len(part) for part in re.findall(r"`+", value)), default=0) + 1
        delimiter = "`" * width
        return f"{delimiter} {value} {delimiter}"
    return value


COVERAGE_HEADING = "What this run established:"

#: A blocking limit is what makes an inventory incomplete, which is the reason
#: the comparison states (`base_inventory_incomplete`, `head_inventory_incomplete`).
_SIDE_LIMIT = {
    "base": "in base, so the base inventory is incomplete",
    "head": "in head, so the head inventory is incomplete",
    "both": "in base and head, so neither inventory is complete",
}


def _published_only_with_hooks(source: str) -> bool:
    """A plugin manifest or marketplace, or a source inside one (#812).

    Its inventory artifact exists only while it declares hooks, so one side
    publishing it does not mean only that side read the file.
    """

    prefixes = [source, *(source[:index] for index, char in enumerate(source) if char == "#")]
    return any(
        is_claude_plugin_manifest_path(path) or is_claude_plugin_marketplace_path(path)
        for path in prefixes
    )


def _side_text(item: HostComparisonCoverageItem) -> str:
    if item.side == "both":
        return "compared"
    if _published_only_with_hooks(item.source):
        return f"published by {item.side} only"
    return f"read in {item.side} only"


#: What a zero-row line cannot show: the inventory redacts these values, so no
#: comparison compares them (#812).
_REDACTED_VALUES = "(redacted values such as env values and apiKeyHelper are not compared)"


def _redacted_values_note(source: str) -> str:
    """The note on redacted values, only for a file that can hold them (#812 review cycle 5).

    Not for a file the inventory reads as instructions (`AGENTS.md`, a
    `CLAUDE.md` link to it, a skill or a rule): it holds no `env` value or
    `apiKeyHelper`, and a docs-only change would print the note on every such
    file it could not prove unchanged. The kind is the one the inventory gives
    the file's path when it reads it; a path redacted past recognition keeps
    the note.
    """

    return "" if _source_kind(source) == "instructions" else f" {_REDACTED_VALUES}"


def coverage_item_text(item: HostComparisonCoverageItem) -> str:
    """One item's finding, in words a reviewer reads without the schema (#812)."""

    if item.status == "blocking_limit":
        return f"{item.limit} {_SIDE_LIMIT[item.side]}"
    if item.status == "changed_without_grant_change":
        # One side only is an added or removed file: it declares no compared grant.
        change = (
            "changed, but no grant this entry compares changed"
            if item.side == "both"
            else "declares no grant this entry compares"
        )
        finding = f"{change}, so no row{_redacted_values_note(item.source)}"
    elif item.status == "unchanged_not_proven":
        finding = (
            "no grant this entry compares changed, but the file was not proven "
            f"unchanged{_redacted_values_note(item.source)}"
        )
    elif item.status == "changed_without_rows":
        finding = "changed, but no row is attributed to this path"
    elif item.rows:
        finding = f"{item.rows} row{'s' if item.rows != 1 else ''}"
    elif item.side == "both":
        # Only a file whose bytes were proven identical on both sides.
        finding = "no change in what this entry reads"
    else:
        # Only an instruction file the engine reads as guidance: it declares
        # no grant, so it gives no row on the one side that has it.
        finding = "declares no grant this entry compares, so no row"
    return f"{_side_text(item)}; {finding}"


#: How many sources compared with no change the text names before counting.
_QUIET_NAMES = 3

#: The most characters the block takes in a PR comment, whose human summary
#: is bounded as a whole. It gets at most this much of the room the comment's
#: other lines leave, never more: see :func:`with_coverage_in_room`.
MARKDOWN_COVERAGE_MAX_CHARS = 2000


def _quiet(item: HostComparisonCoverageItem) -> bool:
    """Proven byte-identical on both sides: the one kind the text collapses."""

    return item.status == "compared" and not item.rows and item.side == "both"


def coverage_lines(
    comparison: HostComparison,
    *,
    markdown: bool = False,
    bullet: str = "- ",
    max_chars: int | None = None,
) -> list[str]:
    """The "What this run established" block (#812).

    One line per item a reviewer must read, in the comparator's order — a
    blocking limit, a change no row describes, a source only one side
    published, a source not proven unchanged, a source with rows — then one
    line naming the sources proven unchanged, the first three by name and the
    rest as a count. Coverage items are a prefix of that order, so when the
    last one listed is such a source, every item the cap omitted is one too;
    otherwise the omitted count is stated as items not listed.

    With ``max_chars``, the block, heading, count and closing blank line
    included, is at most that many characters joined: it lists the longest
    prefix of those lines that fits, naming fewer sources proven unchanged
    before it drops that line, and counts the rest, as ``N items not listed``
    when it lists none. When not even the heading and that count fit, it is
    nothing.

    Nothing when coverage was not recorded (a legacy verifier, `check`, a
    comparison that read no inventory), or when a refused comparison names no
    source. Every path is repository text passed through the same one-line
    rendering as the rows. In Markdown the list is closed with a blank line,
    so a following line cannot continue its last item.
    """

    coverage = comparison.coverage
    if coverage is None:
        return []

    def fits(block: list[str]) -> bool:
        return max_chars is None or len("\n".join(block)) <= max_chars

    if not coverage.items and not coverage.omitted_items:
        if comparison.comparison_status != "comparable":
            return []
        empty = [f"{COVERAGE_HEADING} no host configuration source was compared."]
        return empty if fits(empty) else []

    item_lines = [
        f"{bullet}{_text(item.source, markdown=markdown)} "
        f"({', '.join(single_line_text(host) for host in item.hosts)}): {coverage_item_text(item)}"
        for item in coverage.items
        if not _quiet(item)
    ]
    quiet = [item for item in coverage.items if _quiet(item)]
    # Quiet items sort last: when the last item is one, every omitted item is too.
    last_quiet = bool(coverage.items) and _quiet(coverage.items[-1])
    quiet_total = len(quiet) + (coverage.omitted_items if last_quiet else 0)
    total = len(item_lines) + len(quiet) + coverage.omitted_items

    def quiet_line(count: int) -> str:
        names = ", ".join(_text(item.source, markdown=markdown) for item in quiet[:count])
        more = quiet_total - count
        return f"{bullet}compared with no change in what this entry reads: {names}" + (
            f" and {more} more" if more else ""
        )

    def block(listed: list[str], items: int) -> list[str]:
        lines = [COVERAGE_HEADING, *listed]
        unlisted = total - items
        if unlisted:
            # Items, not sources: one source can be several items (by host, side or limit).
            more = "more " if listed else ""
            lines.append(f"{bullet}{unlisted} {more}item{'s' if unlisted != 1 else ''} not listed")
        if markdown:
            lines.append("")
        return lines

    # Longest first, in order: every item line and the quiet line naming three,
    # two or one source, then ever shorter prefixes of the item lines alone.
    candidates = [
        block([*item_lines, quiet_line(count)], len(item_lines) + quiet_total)
        for count in range(min(len(quiet), _QUIET_NAMES), 0, -1)
    ] + [block(item_lines[:count], count) for count in range(len(item_lines), -1, -1)]
    return next((candidate for candidate in candidates if fits(candidate)), [])


def with_coverage_in_room(
    comparison: HostComparison, lines_for: Callable[[int], list[str]], room: int
) -> list[str]:
    """A bounded Markdown surface's lines, the coverage block given only the room left (#812).

    ``lines_for(max_chars)`` renders every line of the surface, the coverage
    block bounded to ``max_chars`` (``0`` leaves it out); ``room`` is how many
    characters those lines may take joined. The block gets what the other
    lines leave, at most :data:`MARKDOWN_COVERAGE_MAX_CHARS`, so every line the
    surface shows without it — the entries, the review question and
    reproduction, and the advisory, next action and evidence after them — it
    still shows with it (review cycle 5). When not even the heading and a count
    fit, the block is left out; whatever the other lines alone overflow is
    theirs, as without coverage.
    """

    without = lines_for(0)
    spare = room - len("\n".join(without))
    widest = coverage_lines(comparison, markdown=True, max_chars=MARKDOWN_COVERAGE_MAX_CHARS)
    if spare <= 0 or not widest:
        return without
    # The blank line that sets the block apart costs the same whatever it lists.
    separators = (
        len("\n".join(lines_for(MARKDOWN_COVERAGE_MAX_CHARS)))
        - len("\n".join(without))
        - len("\n".join(widest))
    )
    budget = min(MARKDOWN_COVERAGE_MAX_CHARS, spare - separators)
    lines = lines_for(budget) if budget > 0 else without
    return lines if len("\n".join(lines)) <= room else without


def host_comparison_lines(
    comparison: HostComparison, *, markdown: bool = False, coverage_max_chars: int | None = None
) -> list[str]:
    """The host comparison a reviewer reads, coverage block included.

    ``coverage_max_chars`` bounds the block as :func:`coverage_lines` does;
    ``None`` lists every item. A bounded surface passes the room its other
    lines leave, through :func:`with_coverage_in_room`.
    """

    def text(value):
        return _text(value, markdown=markdown)

    coverage = coverage_lines(comparison, markdown=markdown, max_chars=coverage_max_chars)
    if comparison.comparison_status != "comparable":
        return [
            "Host capability comparison unavailable: "
            + text("; ".join(comparison.incomparable_reasons)),
            *coverage,
        ]
    lines = ["Repository-declared host capability changes:"]
    changes = review_changes(comparison.rows)
    if not changes:
        lines.append(
            "No static host-grant changes detected in the covered comparison. No verdict is implied."
        )
    for change in changes:
        # The same mark `diff` prints: the engine called this change a widening.
        marker = "⚠ " if change.expands else ""
        transition = (
            text(change.change)
            if change.change is not None
            else f"{text(change.before)} → {text(change.after)}"
        )
        lines.extend(
            [
                f"- {marker}{text(change.severity)} / {text(change.direction)} — {text(change.subject)}",
                f"  {transition}",
                f"  {text(change.why)}",
            ]
        )
    if coverage and markdown and changes:
        # Ends the row list: the heading would otherwise continue its last item.
        lines.append("")
    lines.extend(coverage)
    if comparison.unchanged_limits:
        lines.append(
            "Not compared: unchanged in this change and not read, so no claim is made about them:"
        )
        for limit in comparison.unchanged_limits:
            lines.append(f"- {text(limit.host)} {text(limit.source)} — {text(limit.limit)}")
    if changes:
        if markdown and lines[-1] != "":
            # Ends the list: a following line would otherwise continue its last item.
            lines.append("")
        lines.append(review_question(changes))
        lines.extend(comparison_reference_lines(comparison, markdown=markdown))
    return lines
