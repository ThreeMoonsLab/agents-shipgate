"""One semantic text projection for declaration review surfaces."""

from __future__ import annotations

from collections.abc import Callable

from agents_shipgate.core.evidence_actions import display_literal
from agents_shipgate.schemas.report import (
    AcknowledgedEffectOverride,
    DeclarationReviewDecision,
    DeclarationReviewRow,
)


def declaration_review_lines(
    review: DeclarationReviewDecision,
    *,
    detail_limit: int | None = None,
    block_char_limit: int | None = None,
    row_char_limit: int | None = None,
    render_for_budget: Callable[[str], str] | None = None,
) -> list[str]:
    """Render counts plus only declarations needing a person's attention.

    Evidence-consistent row names are deliberately absent: their count is the
    useful signal and listing them would bury the exceptions.  Packet §1 may
    additionally request static (unchanged) overrides so the older #409
    promise that every acknowledged exception is visible remains true.
    """

    if detail_limit is not None and detail_limit < 0:
        raise ValueError("declaration review detail_limit must be non-negative")
    if block_char_limit is not None and block_char_limit < 1:
        raise ValueError("declaration review block_char_limit must be positive")
    if row_char_limit is not None and row_char_limit < 1:
        raise ValueError("declaration review row_char_limit must be positive")
    budget_renderer = render_for_budget or _identity

    lines: list[str] = []
    if review.enabled and review.changed_count:
        summary = review.summary
        summary_line = (
            "Declaration changes: "
            f"{review.changed_count} — "
            f"{summary.evidence_consistent} evidence-consistent, "
            f"{summary.unverified} unverified, "
            f"{summary.acknowledged_override} acknowledged override."
        )
        lines.append(summary_line)
        attention_rows = [
            row
            for row in review.rows
            if row.bucket in {"unverified", "acknowledged_override"}
        ]
        detail_lines = [
            (
                _unverified_line(row)
                if row.bucket == "unverified"
                else _changed_override_line(row)
            )
            for row in attention_rows
        ]
        visible = _bounded_detail_lines(
            summary_line=summary_line,
            detail_lines=detail_lines,
            detail_limit=detail_limit,
            block_char_limit=block_char_limit,
            row_char_limit=row_char_limit,
            render_for_budget=budget_renderer,
        )
        lines.extend(visible)
        omitted = len(detail_lines) - len(visible)
        if omitted:
            omission = _omission_line(omitted)
            lines.append(omission)
        if block_char_limit is not None and _rendered_block_length(
            lines, budget_renderer
        ) > block_char_limit:
            raise ValueError(
                "declaration review block_char_limit cannot fit its summary "
                "and omission line"
            )
    return lines


def _bounded_detail_lines(
    *,
    summary_line: str,
    detail_lines: list[str],
    detail_limit: int | None,
    block_char_limit: int | None,
    row_char_limit: int | None,
    render_for_budget: Callable[[str], str],
) -> list[str]:
    """Select complete detail rows within deterministic PR-style budgets.

    A row is either present in full or counted as omitted.  Oversized rows do
    not consume the row-count budget, so a hostile override cannot hide every
    short row after it.  Reserving the largest possible omission line before
    selection guarantees the final block can always state exactly what it did
    not render.
    """

    if not detail_lines:
        return []
    visible: list[str] = []
    for line in detail_lines:
        if detail_limit is not None and len(visible) >= detail_limit:
            continue
        if row_char_limit is not None and len(render_for_budget(line)) > row_char_limit:
            continue
        candidate = [summary_line, *visible, line]
        omitted = len(detail_lines) - len(visible) - 1
        if omitted:
            candidate.append(_omission_line(omitted))
        if block_char_limit is not None and _rendered_block_length(
            candidate, render_for_budget
        ) > block_char_limit:
            continue
        visible.append(line)
    return visible


def _rendered_block_length(
    lines: list[str], render_for_budget: Callable[[str], str]
) -> int:
    return len("\n".join(render_for_budget(line) for line in lines))


def _omission_line(count: int) -> str:
    return f"{count} additional rows; see report.json."


def changed_override_keys(
    review: DeclarationReviewDecision,
) -> set[tuple[object, ...]]:
    """Overrides already represented by the changed-row renderer."""

    return {
        _override_key(override)
        for row in review.rows
        if row.bucket == "acknowledged_override"
        for override in row.acknowledged_overrides
    }


def override_is_represented(
    review: DeclarationReviewDecision,
    override: AcknowledgedEffectOverride,
) -> bool:
    return _override_key(override) in changed_override_keys(review)


def _unverified_line(row: DeclarationReviewRow) -> str:
    effect = (
        repr(_visible(row.declared_effect))
        if row.declared_effect is not None
        else f"risk tags {[_visible(tag) for tag in row.declared_risk_tags]!r}"
        if row.declared_risk_tags
        else "no effect-bearing proposal"
    )
    return (
        f"Unverified declaration: {_visible(row.subject)} declares {effect}; "
        f"{_visible(row.reason)} Review {_visible(row.manifest_path)}."
    )


def _changed_override_line(row: DeclarationReviewRow) -> str:
    if not row.acknowledged_overrides:
        return (
            f"Acknowledged override declaration: {_visible(row.subject)}; "
            f"{_visible(row.reason)} Review {_visible(row.manifest_path)}."
        )
    details = " ".join(_override_detail(override) for override in row.acknowledged_overrides)
    return f"Acknowledged override declaration: {details}"


def _override_detail(override: AcknowledgedEffectOverride) -> str:
    sources = ", ".join(_visible(source) for source in override.inferred_sources)
    sources = sources or "static evidence"
    agrees = (
        " Source evidence agrees ("
        + ", ".join(_visible(source) for source in override.corroborating_sources)
        + ")."
        if override.corroborating_sources
        else ""
    )
    return (
        f"{_visible(override.subject)} declares "
        f"{_visible(override.declared_effect)!r}; "
        f"{sources} infers {_visible(override.inferred_effect)!r}.{agrees} "
        f"Evidence: {_visible(override.evidence)} — "
        f"Reason: {_visible(override.reason)}"
    )


def _visible(value: object) -> str:
    """One-line, injective display for every repository-derived value."""

    return display_literal(str(value))


def _identity(value: str) -> str:
    return value


def _override_key(override: AcknowledgedEffectOverride) -> tuple[object, ...]:
    return (
        override.subject_id,
        override.declared_effect,
        override.inferred_effect,
        tuple(override.inferred_sources),
        tuple(override.corroborating_sources),
        override.evidence,
        override.reason,
    )


__all__ = ["declaration_review_lines", "override_is_represented"]
