"""Tests for the labeling worksheet + accuracy scoring (benchmark/miner/labels).

Fully network-free and engine-free: synthetic MinedRows + label dicts. The
point of these tests is the confusion-matrix math and the worksheet/label IO
contract, so the accuracy harness is trustworthy the moment real labels land.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from benchmark.miner.labels import (
    LABELS,
    WORKSHEET_COLUMNS,
    build_worksheet,
    effective_verdict,
    load_labels,
    render_matrix_markdown,
    score,
    write_worksheet,
)
from benchmark.miner.rows import (
    STATUS_EVALUATED,
    STATUS_SCAN_FAILED,
    STATUS_TRIGGER_SKIP,
    MinedRow,
)


def _row(pr_url: str, *, status: str = STATUS_EVALUATED, verify="", head="", **kw) -> MinedRow:
    return MinedRow(
        repo="o/r",
        pr_number=int(pr_url.rsplit("/", 1)[-1]),
        pr_url=pr_url,
        title="t",
        merged_at="",
        base_sha="b",
        head_sha="h",
        status=status,
        verify_verdict=verify,
        head_decision=head,
        **kw,
    )


def test_effective_verdict_prefers_receipt_then_cold_start() -> None:
    assert effective_verdict(_row("u/1", verify="blocked", head="passed")) == "blocked"
    assert effective_verdict(_row("u/2", verify="", head="insufficient_evidence")) == (
        "insufficient_evidence"
    )
    assert effective_verdict(_row("u/3", verify="", head="")) == ""


def test_worksheet_includes_engaged_rows_only(tmp_path: Path) -> None:
    rows = [
        _row("u/1", status=STATUS_EVALUATED, verify="blocked"),
        _row("u/2", status=STATUS_SCAN_FAILED),
        _row("u/3", status=STATUS_TRIGGER_SKIP),
    ]
    worksheet = build_worksheet(rows)
    urls = {w["pr_url"] for w in worksheet}
    assert urls == {"u/1", "u/2"}  # trigger_skip excluded by default
    assert worksheet[0]["label"] == "" and worksheet[0]["rationale"] == ""

    out = tmp_path / "ws.csv"
    write_worksheet(worksheet, out)
    with out.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == WORKSHEET_COLUMNS
    assert b"\r" not in out.read_bytes()  # LF-only for git diff --check


def test_load_labels_skips_blank_and_rejects_unknown(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text(
        "pr_url,label,rationale\n"
        "u/1,must_block,money tool added\n"
        "u/2,,unlabeled yet\n"
        "u/3,safe_to_merge,docs\n",
        encoding="utf-8",
    )
    labels = load_labels(path)
    assert labels == {"u/1": "must_block", "u/3": "safe_to_merge"}

    bad = tmp_path / "bad.csv"
    bad.write_text("pr_url,label,rationale\nu/1,unsafe,typo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown label"):
        load_labels(bad)


def test_score_confusion_matrix_and_metrics() -> None:
    rows = [
        _row("u/1", verify="blocked"),  # must_block, correctly blocked
        _row("u/2", verify="insufficient_evidence"),  # must_block, only IE (missed block)
        _row("u/3", verify="human_review_required"),  # needs_human, caught
        _row("u/4", verify="mergeable"),  # safe, correctly allowed
        _row("u/5", verify="blocked"),  # safe, false alarm
        _row("u/6", verify="insufficient_evidence"),  # safe, coverage gap
    ]
    labels = {
        "u/1": "must_block",
        "u/2": "must_block",
        "u/3": "needs_human",
        "u/4": "safe_to_merge",
        "u/5": "safe_to_merge",
        "u/6": "safe_to_merge",
    }
    scored = score(rows, labels)

    assert scored["labeled_rows"] == 6
    assert scored["matrix"]["must_block"]["block"] == 1
    assert scored["matrix"]["must_block"]["insufficient_evidence"] == 1
    assert scored["matrix"]["safe_to_merge"]["allow"] == 1
    m = scored["metrics"]
    assert m["blocked_recall"] == 0.5  # 1 of 2 must_block hard-blocked
    assert m["must_block_caught"] == 1.0  # both did not auto-pass
    assert m["needs_human_caught"] == 1.0
    assert m["benign_escalation_rate"] == round(1 / 3, 3)  # 1 of 3 safe escalated
    assert m["ie_rate_on_safe"] == round(1 / 3, 3)
    assert scored["unmatched_labels"] == []


def test_score_flags_unmatched_labels() -> None:
    rows = [_row("u/1", verify="blocked")]
    scored = score(rows, {"u/1": "must_block", "u/missing": "safe_to_merge"})
    assert scored["unmatched_labels"] == ["u/missing"]
    assert scored["labeled_rows"] == 1


def test_render_matrix_markdown_has_a_row_per_label() -> None:
    scored = score([_row("u/1", verify="blocked")], {"u/1": "must_block"})
    md = render_matrix_markdown(scored)
    for label in LABELS:
        assert f"`{label}`" in md
    assert md.count("\n") >= len(LABELS) + 1  # header + separator + label rows
