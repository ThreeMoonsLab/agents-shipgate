"""Bounded PR presentation of an existing review request; no decision ingestion."""

from __future__ import annotations

import re
from urllib.parse import quote

from agents_shipgate.report.markdown import _safe_markdown_text
from agents_shipgate.schemas.human_review_request import HumanReviewRequestV1

_MAX_REVIEW_ROWS = 3
_MAX_DETAIL_CHARS = 1100
_GITHUB_REPOSITORY = re.compile(r"github\.com/[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")


def _text(value: str, limit: int) -> str:
    single_line = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    if len(single_line) > limit:
        single_line = single_line[:limit - 1] + "…"
    return _safe_markdown_text(single_line)


def _source(request: HumanReviewRequestV1, path: str) -> str:
    label = _text(path, 120)
    # Only GitHub.com repository locators have this known blob URL shape.
    # Local/custom hosts retain the exact file path rather than a guessed URL.
    if _GITHUB_REPOSITORY.fullmatch(request.repository_id):
        target = f"https://{request.repository_id}/blob/{request.source_head_commit_sha}/{quote(path, safe='/')}"
        return f"[{label}]({target})"
    return label


def human_review_lines(request: HumanReviewRequestV1) -> list[str]:
    """Show the question before long findings, with exact omitted-row counts.

    #555 still owns the independent signer boundary. The current Action may
    publish a question, not claim that an authenticated decision was recorded.
    The fixed actor/consequence/fallback lines precede bounded source prose so
    truncation cannot turn a partial question into an approval instruction.
    """

    rows: list[str] = []
    abbreviated = False
    for item, question in zip(request.review_items, request.questions, strict=True):
        if len(rows) >= _MAX_REVIEW_ROWS:
            break
        sources = ", ".join(_source(request, path) for path in item.paths[:2])
        if len(item.paths) > 2:
            sources += f"; {len(item.paths) - 2} more source paths in the full request"
        if not sources:
            sources = "source location unavailable; open the full request"
        row = f"- **Medium documentation concern:** {_text(question.question, 350)} Evidence: {sources}."
        if sum(len(value) + 1 for value in [*rows, row]) > _MAX_DETAIL_CHARS:
            break
        rows.append(row)
        abbreviated |= len(question.question) > 350 or any(len(path) > 120 for path in item.paths[:2])
    total = len(request.review_items)
    hidden = total - len(rows)
    scope = f"Showing {len(rows)} of {total} review questions for commit `{request.source_head_commit_sha[:12]}`."
    if hidden:
        scope += f" {hidden} omitted; do not decide a partial list."
    if abbreviated:
        scope += " Some text is abbreviated; read the full request before deciding."
    return [
        "", "### Review question",
        "An eligible authenticated human must decide; the author and bots cannot supply this decision.",
        "**Accept** records agreement with the concern; **Reject** requests a fix; **Dispute** records disagreement for triage. None grants merge/completion or suppresses a finding.",
        "**No authenticated decision is recorded by this Action.** Discuss the question in this PR's Conversation; a comment or ordinary approval does not clear current control.",
        "Coverage: complete static evidence for this documentation-quality class; runtime behavior is not proven.",
        scope,
        *rows,
        "Full scope and identity: `human-review-request.json` in the report directory or uploaded report artifact.",
    ]
