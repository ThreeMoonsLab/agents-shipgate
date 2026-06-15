"""Integrity guard for the committed mined corpus.

The benchmark claims (trigger-noise bound, IE rate, the labeling worksheet)
all rest on the committed ``benchmark/miner/results/*`` artifacts. These
tests make those artifacts load-bearing: a hand-edit, a half-regenerated run,
or a CSV/JSONL that drift apart fails CI instead of silently publishing a
wrong number. Network-free — reads only the committed files.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from benchmark.miner.labels import WORKSHEET_COLUMNS, build_worksheet
from benchmark.miner.rows import (
    CSV_COLUMNS,
    STATUS_ERROR,
    STATUS_EVALUATED,
    STATUS_INIT_SKIP,
    STATUS_SCAN_FAILED,
    STATUS_TRIGGER_SKIP,
    read_jsonl,
    summarize,
)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmark" / "miner" / "results"

KNOWN_STATUSES = {
    STATUS_EVALUATED,
    STATUS_TRIGGER_SKIP,
    STATUS_INIT_SKIP,
    STATUS_SCAN_FAILED,
    STATUS_ERROR,
}
VALID_VERDICTS = {
    "passed",
    "review_required",
    "insufficient_evidence",
    "blocked",
}


def _mined_jsonl_files() -> list[Path]:
    return sorted(RESULTS_DIR.glob("*-mined.jsonl"))


def test_at_least_one_committed_run_exists() -> None:
    assert _mined_jsonl_files(), "no committed mined corpus under benchmark/miner/results/"


@pytest.mark.parametrize("jsonl_path", _mined_jsonl_files(), ids=lambda p: p.name)
def test_committed_corpus_is_well_formed(jsonl_path: Path) -> None:
    rows = read_jsonl(jsonl_path)
    assert rows, f"{jsonl_path.name} is empty"
    seen: set[tuple[str, int]] = set()
    for row in rows:
        assert row.status in KNOWN_STATUSES, f"unknown status {row.status!r} in {jsonl_path.name}"
        assert row.pr_url, f"row with no pr_url in {jsonl_path.name}"
        key = (row.repo, row.pr_number)
        assert key not in seen, f"duplicate {key} in {jsonl_path.name}"
        seen.add(key)
        # An evaluated row, by definition, produced a release decision.
        if row.status == STATUS_EVALUATED:
            assert row.head_decision in VALID_VERDICTS, (
                f"{jsonl_path.name}: evaluated {key} has head_decision "
                f"{row.head_decision!r} (not a valid verdict)"
            )


def _csv_shape(row) -> dict[str, str]:
    """How write_csv serializes a row: None → "", everything else → str."""

    return {key: ("" if value is None else str(value)) for key, value in row.to_json().items()}


@pytest.mark.parametrize("jsonl_path", _mined_jsonl_files(), ids=lambda p: p.name)
def test_csv_and_jsonl_agree(jsonl_path: Path) -> None:
    csv_path = jsonl_path.with_suffix(".csv")
    assert csv_path.is_file(), f"missing CSV sibling for {jsonl_path.name}"
    jsonl_rows = read_jsonl(jsonl_path)
    expected = {row.pr_url: _csv_shape(row) for row in jsonl_rows}
    assert len(expected) == len(jsonl_rows), f"{jsonl_path.name}: duplicate pr_url"

    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == CSV_COLUMNS, (
            f"{csv_path.name} header drifted from the row schema"
        )
        csv_rows = list(reader)

    assert len(csv_rows) == len(jsonl_rows), (
        f"{csv_path.name} has {len(csv_rows)} rows; "
        f"{jsonl_path.name} has {len(jsonl_rows)} — regenerate both together"
    )
    # Full-content comparison: a half-regenerated pair with the same PRs but a
    # changed status / head_decision / verify_* / count must fail, not pass.
    for csv_row in csv_rows:
        url = csv_row["pr_url"]
        assert url in expected, f"{csv_path.name}: pr_url {url} absent from {jsonl_path.name}"
        normalized = {key: csv_row.get(key, "") for key in CSV_COLUMNS}
        assert normalized == expected[url], (
            f"{csv_path.stem}: row {url} differs between CSV and JSONL\n"
            f"  csv:   {normalized}\n  jsonl: {expected[url]}"
        )


@pytest.mark.parametrize("jsonl_path", _mined_jsonl_files(), ids=lambda p: p.name)
def test_committed_corpus_files_are_lf_only(jsonl_path: Path) -> None:
    assert b"\r" not in jsonl_path.read_bytes(), f"{jsonl_path.name} has CRLF (git diff --check)"
    csv_path = jsonl_path.with_suffix(".csv")
    assert b"\r" not in csv_path.read_bytes(), f"{csv_path.name} has CRLF (git diff --check)"


def test_labels_template_matches_its_corpus() -> None:
    template = RESULTS_DIR / "2026-W24-mined.labels.template.csv"
    corpus = RESULTS_DIR / "2026-W24-mined.jsonl"
    if not template.is_file():
        pytest.skip("no committed labels template")
    assert b"\r" not in template.read_bytes(), "labels template has CRLF (git diff --check)"

    # The committed template must be exactly the worksheet the corpus generates —
    # no omitted engine-engaged rows, no duplicates, no stale extras.
    expected_urls = [w["pr_url"] for w in build_worksheet(read_jsonl(corpus))]
    with template.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == WORKSHEET_COLUMNS
        template_rows = list(reader)
    template_urls = [r["pr_url"] for r in template_rows]

    assert len(template_urls) == len(set(template_urls)), "duplicate rows in labels template"
    assert sorted(template_urls) == sorted(expected_urls), (
        "labels template is out of sync with the corpus worksheet "
        "(regenerate with `python -m benchmark.miner labels`)"
    )
    for record in template_rows:
        # The committed template is blank — labels belong in an adjudicated file.
        assert record["label"] == "", "committed template must not carry labels"
        assert record["rationale"] == "", "committed template must not carry rationales"


def test_w24_headline_numbers_reproduce_from_committed_data() -> None:
    """Pin the published 2026-W24 headline to the committed corpus.

    Update these alongside the README when a new run is committed — that is
    the point: the numbers in the docs must come from the data on disk.
    """
    rows = read_jsonl(RESULTS_DIR / "2026-W24-mined.jsonl")
    summary = summarize(rows)
    assert summary["rows"] == 121
    assert summary["by_status"][STATUS_TRIGGER_SKIP] == 108
    assert summary["by_status"][STATUS_EVALUATED] == 7
    # IE on decided = 3/7 (the first measured real-world extraction-coverage gap).
    assert summary["ie_rate_on_decided"] == round(3 / 7, 3)
