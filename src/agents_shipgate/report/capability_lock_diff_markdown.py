from __future__ import annotations

from collections.abc import Iterable

from agents_shipgate.schemas.capabilities import (
    CapabilityFactV1,
    CapabilityLockChangedFact,
    CapabilityLockDiffV1,
)
from agents_shipgate.schemas.capability_semantics import CapabilitySemanticChange


def render_capability_lock_diff_markdown(
    diff: CapabilityLockDiffV1,
    *,
    heading_level: int = 2,
    max_rows: int | None = None,
) -> str:
    """Render a deterministic reviewer surface for a capability-lock diff."""

    marker = "#" * max(1, min(6, heading_level))
    lines = [f"{marker} Capability Diff", ""]
    summary = diff.summary
    lines.append(
        "Summary: "
        f"+{summary.added}, "
        f"-{summary.removed}, "
        f"{summary.changed} changed, "
        f"{summary.reidentified} reidentified, "
        f"{summary.evidence_changed} evidence-only, "
        f"{summary.unchanged} unchanged."
    )
    lines.append(
        "Base: "
        f"{_code(diff.base.path or 'inline')} "
        f"semantic={_code(diff.base.semantic_capability_set_hash)}"
    )
    lines.append(
        "Head: "
        f"{_code(diff.head.path or 'inline')} "
        f"semantic={_code(diff.head.semantic_capability_set_hash)}"
    )

    _append_fact_section(lines, marker, "Added", diff.added, max_rows=max_rows)
    _append_fact_section(lines, marker, "Removed", diff.removed, max_rows=max_rows)
    _append_changed_section(lines, marker, "Changed", diff.changed, max_rows=max_rows)
    _append_changed_section(
        lines,
        marker,
        "Reidentified",
        diff.reidentified,
        max_rows=max_rows,
    )
    _append_changed_section(
        lines,
        marker,
        "Evidence-Only",
        diff.evidence_changed,
        max_rows=max_rows,
        note="Provenance-only changes; static capability semantics did not drift.",
    )
    if _has_no_rows(diff):
        lines.extend(["", f"{marker}# No Semantic Capability Drift"])
        lines.append("No added, removed, changed, reidentified, or evidence-only rows.")
    return "\n".join(lines).rstrip() + "\n"


def _append_fact_section(
    lines: list[str],
    marker: str,
    title: str,
    facts: list[CapabilityFactV1],
    *,
    max_rows: int | None,
) -> None:
    rows = _limit(facts, max_rows=max_rows)
    if not rows:
        return
    lines.extend(
        [
            "",
            f"{marker}# {title}",
            "| Tool | Provider | Operation | Scope | Effect | Risk |",
            "|---|---|---|---|---|---|",
        ]
    )
    for fact in rows:
        lines.append(
            "| "
            f"{_cell(fact.identity.tool_name)} | "
            f"{_cell(fact.identity.provider)} | "
            f"{_cell(fact.identity.operation)} | "
            f"{_cell(_join(fact.identity.scope))} | "
            f"{_cell(fact.effect.effect)} | "
            f"{_cell(_join(fact.risk_tags))} |"
        )
    _append_omitted(lines, facts, rows)


def _append_changed_section(
    lines: list[str],
    marker: str,
    title: str,
    changes: list[CapabilityLockChangedFact],
    *,
    max_rows: int | None,
    note: str | None = None,
) -> None:
    rows = _limit(changes, max_rows=max_rows)
    if not rows:
        return
    lines.extend(["", f"{marker}# {title}"])
    if note:
        lines.append(note)
    lines.extend(
        [
            "| Tool | Provider | Operation | Scope | Direction | Hashes | Why |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for change in rows:
        lines.append(
            "| "
            f"{_cell(change.tool_name)} | "
            f"{_cell(change.after.identity.provider)} | "
            f"{_cell(change.operation)} | "
            f"{_cell(_join(change.after.identity.scope))} | "
            f"{_cell(change.semantic_direction)} | "
            f"{_cell(_join(change.changed_hashes))} | "
            f"{_cell(_semantic_rationale(change.semantic_changes))} |"
        )
    _append_omitted(lines, changes, rows)


def _semantic_rationale(changes: Iterable[CapabilitySemanticChange]) -> str:
    rationales = [change.rationale for change in changes if change.rationale]
    if rationales:
        return "; ".join(rationales[:3])
    return "Semantic hashes changed without a more specific classifier."


def _append_omitted(lines: list[str], original: list[object], rendered: list[object]) -> None:
    omitted = len(original) - len(rendered)
    if omitted > 0:
        lines.append(f"_...and {omitted} more._")


def _limit[T](items: list[T], *, max_rows: int | None) -> list[T]:
    if max_rows is None or max_rows < 0:
        return list(items)
    return list(items[:max_rows])


def _has_no_rows(diff: CapabilityLockDiffV1) -> bool:
    return not (
        diff.added
        or diff.removed
        or diff.changed
        or diff.reidentified
        or diff.evidence_changed
    )


def _join(values: Iterable[object]) -> str:
    items = [str(value) for value in values if str(value)]
    return ", ".join(items) if items else "-"


def _cell(value: object) -> str:
    text = str(value or "-").replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return text


def _code(value: object) -> str:
    text = str(value or "").replace("`", "")
    return f"`{text}`"


__all__ = ["render_capability_lock_diff_markdown"]
