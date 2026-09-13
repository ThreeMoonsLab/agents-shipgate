"""Human projection of host evidence shared by CLI and PR output."""

from __future__ import annotations

import re

from agents_shipgate.core.agent_control_envelope import single_line_text
from agents_shipgate.schemas.host_comparison import HostComparison


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
    if not comparison.rows:
        lines.append(
            "No static host-grant changes detected in the covered comparison. No verdict is implied."
        )
    for row in comparison.rows:
        lines.extend(
            [
                f"- {text(row.severity)} / {text(row.direction)} — {text(row.subject)}",
                f"  {text(row.before)} → {text(row.after)}",
                f"  {text(row.why)}",
            ]
        )
    if comparison.unchanged_limits:
        lines.append(
            "Not compared: unchanged in this change and not read, so no claim is made about them:"
        )
        for limit in comparison.unchanged_limits:
            lines.append(f"- {text(limit.host)} {text(limit.source)} — {text(limit.limit)}")
    return lines
