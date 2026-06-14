"""Labeling worksheet + accuracy scoring for the mined corpus.

The mined rows (:mod:`benchmark.miner.rows`) are *evidence*; a human label
is the *ground truth*. This module turns the corpus into a turnkey labeling
worksheet and, once an adjudicated labels file exists, computes the
verdict-accuracy confusion matrix — the roadmap's gating proof artifact.

Everything here is deterministic and network-free: it reads the committed
JSONL/CSV, never re-mines. The only missing input is the human labels.

Label vocabulary (one adjudicated label per PR; see ``LABELING.md``):

- ``safe_to_merge`` — the capability/authority change is benign; a gate
  should let it through.
- ``needs_human`` — a person should look (accepted-debt, evidence gap,
  authority-bearing but not clearly unsafe); a gate should not auto-pass.
- ``must_block`` — the change is unsafe to merge unreviewed (new
  high-risk authority, trust-root weakening, least-privilege removal); a
  gate must block.

The verdict scored against the label is the per-PR receipt
(``verify_verdict``), falling back to the cold-start ``head_decision`` when
the receipt is unavailable.
"""

from __future__ import annotations

import csv
from pathlib import Path

from benchmark.miner.rows import STATUS_EVALUATED, STATUS_SCAN_FAILED, MinedRow

LABELS: tuple[str, ...] = ("safe_to_merge", "needs_human", "must_block")

# Coarse gate action a verdict implies, for the confusion matrix.
_VERDICT_ACTION: dict[str, str] = {
    "mergeable": "allow",
    "passed": "allow",
    "human_review_required": "review",
    "review_required": "review",
    "insufficient_evidence": "insufficient_evidence",
    "blocked": "block",
    "unknown": "unknown",
    "": "unscored",
}
ACTIONS: tuple[str, ...] = ("allow", "review", "insufficient_evidence", "block", "unknown", "unscored")

# Worksheet columns: enough PR context to label without opening the diff,
# plus the blank columns the human fills.
WORKSHEET_COLUMNS: tuple[str, ...] = (
    "pr_url",
    "repo",
    "pr_number",
    "title",
    "status",
    "head_decision",
    "verify_verdict",
    "verify_can_merge",
    "verify_trust_root_touched",
    "cap_added",
    "cap_broadened",
    "label",  # human fills: one of LABELS
    "rationale",  # human fills: one short line
)

_DEFAULT_WORKSHEET_STATUSES = (STATUS_EVALUATED, STATUS_SCAN_FAILED)


def effective_verdict(row: MinedRow) -> str:
    """The verdict scored against the label: receipt first, cold-start fallback."""

    return row.verify_verdict or row.head_decision or ""


def build_worksheet(
    rows: list[MinedRow],
    *,
    statuses: tuple[str, ...] = _DEFAULT_WORKSHEET_STATUSES,
) -> list[dict[str, object]]:
    """One worksheet row per engine-engaged PR, with blank label columns.

    Defaults to the rows where the engine actually produced (or tried to
    produce) a verdict — ``evaluated`` and ``scan_failed``. ``trigger_skip``
    rows are the negative-control pool; sample them separately per
    ``LABELING.md`` rather than labeling all of them.
    """

    worksheet: list[dict[str, object]] = []
    for row in rows:
        if row.status not in statuses:
            continue
        worksheet.append(
            {
                "pr_url": row.pr_url,
                "repo": row.repo,
                "pr_number": row.pr_number,
                "title": row.title,
                "status": row.status,
                "head_decision": row.head_decision,
                "verify_verdict": row.verify_verdict,
                "verify_can_merge": "" if row.verify_can_merge is None else row.verify_can_merge,
                "verify_trust_root_touched": (
                    "" if row.verify_trust_root_touched is None else row.verify_trust_root_touched
                ),
                "cap_added": "" if row.cap_added is None else row.cap_added,
                "cap_broadened": "" if row.cap_broadened is None else row.cap_broadened,
                "label": "",
                "rationale": "",
            }
        )
    return worksheet


def write_worksheet(worksheet: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(WORKSHEET_COLUMNS), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(worksheet)


REQUIRED_LABEL_COLUMNS: tuple[str, ...] = ("pr_url", "label")


def load_labels(path: Path) -> dict[str, str]:
    """Read the adjudicated labels file (``pr_url,label,rationale``).

    Returns ``{pr_url: label}``. Rows with a blank ``label`` are skipped
    (worksheet rows the human hasn't filled yet). Because this file feeds the
    headline accuracy table, every other malformation is a hard error rather
    than a silent partial read:

    - missing a required header (e.g. ``url`` instead of ``pr_url``) — would
      otherwise read zero labels and publish an all-null benchmark;
    - a filled ``label`` with no ``pr_url`` — the label cannot be attributed;
    - a duplicate ``pr_url`` — ambiguous ground truth;
    - an unknown ``label`` value — a typo must not be miscounted.
    """

    labels: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [c for c in REQUIRED_LABEL_COLUMNS if c not in fieldnames]
        if missing:
            raise ValueError(
                f"{path}: labels file missing required column(s): "
                f"{', '.join(missing)} (found: {', '.join(fieldnames) or 'none'})"
            )
        for line_no, record in enumerate(reader, start=2):
            label = (record.get("label") or "").strip()
            pr_url = (record.get("pr_url") or "").strip()
            if not label:
                continue  # unfilled worksheet row — not yet labeled.
            if label not in LABELS:
                raise ValueError(
                    f"{path}:{line_no}: unknown label {label!r}; "
                    f"expected one of {', '.join(LABELS)}"
                )
            if not pr_url:
                raise ValueError(
                    f"{path}:{line_no}: row labeled {label!r} has no pr_url"
                )
            if pr_url in labels:
                raise ValueError(f"{path}:{line_no}: duplicate pr_url {pr_url!r}")
            labels[pr_url] = label
    return labels


def score(rows: list[MinedRow], labels: dict[str, str]) -> dict[str, object]:
    """Confusion matrix + headline accuracy metrics over labeled rows.

    Only rows whose ``pr_url`` has a label are scored. The matrix is
    ``label → gate action → count``; the metrics are the roadmap's headline
    accuracy numbers.
    """

    by_url = {row.pr_url: row for row in rows}
    matrix: dict[str, dict[str, int]] = {
        label: {action: 0 for action in ACTIONS} for label in LABELS
    }
    totals: dict[str, int] = {label: 0 for label in LABELS}
    unmatched: list[str] = []

    for pr_url, label in labels.items():
        row = by_url.get(pr_url)
        if row is None:
            unmatched.append(pr_url)
            continue
        action = _VERDICT_ACTION.get(effective_verdict(row), "unknown")
        matrix[label][action] += 1
        totals[label] += 1

    def rate(numer: int, denom: int) -> float | None:
        return round(numer / denom, 3) if denom else None

    must_block = matrix["must_block"]
    needs_human = matrix["needs_human"]
    safe = matrix["safe_to_merge"]
    not_allowed = lambda m: m["block"] + m["review"] + m["insufficient_evidence"]  # noqa: E731

    metrics = {
        # Of must_block PRs, the share the gate hard-blocked.
        "blocked_recall": rate(must_block["block"], totals["must_block"]),
        # Of must_block PRs, the share that did NOT auto-pass (block/review/IE).
        "must_block_caught": rate(not_allowed(must_block), totals["must_block"]),
        # Of needs_human PRs, the share that did NOT auto-pass.
        "needs_human_caught": rate(not_allowed(needs_human), totals["needs_human"]),
        # Of safe PRs, the share the gate escalated (block or review) — false alarms.
        "benign_escalation_rate": rate(safe["block"] + safe["review"], totals["safe_to_merge"]),
        # Of safe PRs, the share that returned insufficient_evidence (coverage gap).
        "ie_rate_on_safe": rate(safe["insufficient_evidence"], totals["safe_to_merge"]),
    }
    return {
        "labeled_rows": sum(totals.values()),
        "totals_by_label": totals,
        "matrix": matrix,
        "metrics": metrics,
        "unmatched_labels": sorted(unmatched),
    }


def render_matrix_markdown(scored: dict[str, object]) -> str:
    """A compact Markdown table of the confusion matrix for the run README."""

    matrix: dict[str, dict[str, int]] = scored["matrix"]  # type: ignore[assignment]
    header = "| label \\ verdict | " + " | ".join(ACTIONS) + " |"
    sep = "|" + "---|" * (len(ACTIONS) + 1)
    lines = [header, sep]
    for label in LABELS:
        cells = " | ".join(str(matrix[label][action]) for action in ACTIONS)
        lines.append(f"| `{label}` | {cells} |")
    return "\n".join(lines)
