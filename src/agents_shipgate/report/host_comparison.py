"""Human projection of host evidence shared by CLI and PR output."""

from __future__ import annotations

import re

from agents_shipgate.core.agent_control_envelope import single_line_text
from agents_shipgate.core.boundary_registry import (
    is_claude_plugin_manifest_path,
    is_claude_plugin_marketplace_path,
)
from agents_shipgate.core.capability_diff_rows import ReviewChange, review_changes
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


def coverage_item_text(item: HostComparisonCoverageItem) -> str:
    """One item's finding, in words a reviewer reads without the schema (#812)."""

    if item.status == "blocking_limit":
        return f"{item.limit} {_SIDE_LIMIT[item.side]}"
    if item.status == "unread_fields_changed":
        finding = "observed a change in fields this entry does not read, so no row"
    elif item.status == "changed_without_rows":
        finding = "changed, but no row is attributed to this path"
    elif item.rows:
        finding = f"{item.rows} row{'s' if item.rows != 1 else ''}"
    elif item.side == "both":
        finding = "no change in what this entry reads"
    else:
        # Only an instruction file the engine reads as guidance: it declares
        # no grant, so it gives no row on the one side that has it.
        finding = "declares no grant this entry compares, so no row"
    return f"{_side_text(item)}; {finding}"


#: How many sources compared with no change the text names before counting.
_QUIET_NAMES = 3

#: The most characters the block takes in a PR comment, whose human summary
#: is bounded as a whole: past it, items are counted rather than listed, so
#: long paths cannot push the advisory and next action out of the comment.
MARKDOWN_COVERAGE_MAX_CHARS = 2000


def _quiet(item: HostComparisonCoverageItem) -> bool:
    """Compared on both sides with no change: the one kind the text collapses."""

    return item.status == "compared" and not item.rows and item.side == "both"


def coverage_lines(
    comparison: HostComparison,
    *,
    markdown: bool = False,
    bullet: str = "- ",
    max_chars: int | None = None,
) -> list[str]:
    """The "What this run established" block (#812).

    One line per item a reviewer must read — a blocking limit, a source with
    rows, a change no row describes, a source only one side published — then
    one line naming the sources compared with no change, the first three by
    name and the rest as a count. Coverage items are a prefix of that order, so
    when the last one listed is such a source, every item the cap omitted is
    one too; otherwise the omitted count is stated as items not listed.

    With ``max_chars``, the block stops listing, in that order, once the next
    line would take it past that many characters, and counts what it did not
    list. The first line after the heading is always listed.

    Nothing when coverage was not recorded (a legacy verifier, `check`, a
    comparison that read no inventory), or when a refused comparison names no
    source. Every path is repository text passed through the same one-line
    rendering as the rows. In Markdown the list is closed with a blank line,
    so a following line cannot continue its last item.
    """

    coverage = comparison.coverage
    if coverage is None:
        return []
    if not coverage.items and not coverage.omitted_items:
        if comparison.comparison_status != "comparable":
            return []
        return [f"{COVERAGE_HEADING} no host configuration source was compared."]
    lines = [COVERAGE_HEADING]

    def add(line: str) -> bool:
        if (
            max_chars is not None
            and len(lines) > 1
            and len("\n".join([*lines, line])) > max_chars
        ):
            return False
        lines.append(line)
        return True

    unlisted = 0
    for item in coverage.items:
        if _quiet(item):
            continue
        hosts = ", ".join(single_line_text(host) for host in item.hosts)
        line = f"{bullet}{_text(item.source, markdown=markdown)} ({hosts}): {coverage_item_text(item)}"
        # Once one line is left out, so is every later one: the order holds.
        if unlisted or not add(line):
            unlisted += 1
    omitted_quiet = coverage.omitted_items if coverage.items and _quiet(coverage.items[-1]) else 0
    quiet = [item for item in coverage.items if _quiet(item)]
    if quiet:
        total = len(quiet) + omitted_quiet
        named = 0
        # Fewer names when three do not fit; none when an earlier line did not.
        counts = range(min(len(quiet), _QUIET_NAMES), 0, -1) if not unlisted else range(0)
        for count in counts:
            names = ", ".join(_text(item.source, markdown=markdown) for item in quiet[:count])
            more = total - count
            if add(
                f"{bullet}compared with no change in what this entry reads: {names}"
                + (f" and {more} more" if more else "")
            ):
                named = count
                break
        if not named:
            unlisted += total
    if not omitted_quiet:
        unlisted += coverage.omitted_items
    if unlisted:
        # Items, not sources: one source can be several items (by host, side or limit).
        lines.append(f"{bullet}{unlisted} more item{'s' if unlisted != 1 else ''} not listed")
    if markdown:
        lines.append("")
    return lines


def _coverage_budget(markdown: bool) -> int | None:
    """Only the PR comment is bounded as a whole; terminal text lists every item."""

    return MARKDOWN_COVERAGE_MAX_CHARS if markdown else None


def host_comparison_lines(comparison: HostComparison, *, markdown: bool = False) -> list[str]:
    def text(value):
        return _text(value, markdown=markdown)

    if comparison.comparison_status != "comparable":
        return [
            "Host capability comparison unavailable: "
            + text("; ".join(comparison.incomparable_reasons)),
            *coverage_lines(comparison, markdown=markdown, max_chars=_coverage_budget(markdown)),
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
    coverage = coverage_lines(comparison, markdown=markdown, max_chars=_coverage_budget(markdown))
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
