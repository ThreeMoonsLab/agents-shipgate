"""Human projection of host evidence shared by CLI and PR output."""

from __future__ import annotations

import re

from agents_shipgate.core.agent_control_envelope import single_line_text
from agents_shipgate.core.capability_diff_rows import ReviewChange, review_changes
from agents_shipgate.schemas.host_comparison import HostComparison

#: The tokens a reviewer copies to read the same comparison again (#795). The
#: canonical console script, not this process's spelling: the command is for
#: whoever reads the output, on their machine.
REPRODUCE_PROGRAM = "agents-shipgate"


def review_question(changes: list[ReviewChange]) -> str:
    """One bounded question for a reviewer, asked only about changes that exist (#795)."""

    # `capability`, not `permission`: an entry may be an MCP server, a hook, a
    # workflow grant or instructions as well as a permission rule.
    if len(changes) == 1:
        return "Review question: Does the team intend this declared capability change?"
    return f"Review question: Does the team intend these {len(changes)} declared capability changes?"


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


def host_comparison_lines(comparison: HostComparison, *, markdown: bool = False) -> list[str]:
    def text(value):
        value = single_line_text(str(value))
        if markdown:
            # Inline code with a delimiter longer than any untrusted backtick run.
            width = max((len(part) for part in re.findall(r"`+", value)), default=0) + 1
            delimiter = "`" * width
            return f"{delimiter} {value} {delimiter}"
        return value

    if comparison.comparison_status != "comparable":
        return [
            "Host capability comparison unavailable: "
            + text("; ".join(comparison.incomparable_reasons))
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
    if comparison.unchanged_limits:
        lines.append(
            "Not compared: unchanged in this change and not read, so no claim is made about them:"
        )
        for limit in comparison.unchanged_limits:
            lines.append(f"- {text(limit.host)} {text(limit.source)} — {text(limit.limit)}")
    if changes:
        if markdown:
            # Ends the list: a following line would otherwise continue its last item.
            lines.append("")
        lines.append(review_question(changes))
        lines.extend(comparison_reference_lines(comparison, markdown=markdown))
    return lines
