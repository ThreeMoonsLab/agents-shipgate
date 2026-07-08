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


_TEMPLATE_SUFFIX = ".labels.template.csv"


def _label_templates() -> list[Path]:
    return sorted(RESULTS_DIR.glob(f"*{_TEMPLATE_SUFFIX}"))


@pytest.mark.parametrize("template", _label_templates(), ids=lambda p: p.name)
def test_labels_template_matches_its_corpus(template: Path) -> None:
    # Each committed template must stay an exact, blank worksheet of its OWN
    # run — so a stale or accidentally-labeled template for any run fails, not
    # just the W24 one.
    run = template.name[: -len(_TEMPLATE_SUFFIX)]
    corpus = RESULTS_DIR / f"{run}.jsonl"
    assert corpus.is_file(), f"{template.name} has no corpus sibling {corpus.name}"
    assert b"\r" not in template.read_bytes(), f"{template.name} has CRLF (git diff --check)"

    # The committed template must be exactly the worksheet the corpus generates —
    # no omitted engine-engaged rows, no duplicates, no stale extras.
    expected_urls = [w["pr_url"] for w in build_worksheet(read_jsonl(corpus))]
    with template.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == WORKSHEET_COLUMNS
        template_rows = list(reader)
    template_urls = [r["pr_url"] for r in template_rows]

    assert len(template_urls) == len(set(template_urls)), f"duplicate rows in {template.name}"
    assert sorted(template_urls) == sorted(expected_urls), (
        f"{template.name} is out of sync with the corpus worksheet "
        "(regenerate with `python -m benchmark.miner labels`)"
    )
    for record in template_rows:
        # The committed template is blank — labels belong in an adjudicated file.
        assert record["label"] == "", f"{template.name} must not carry labels"
        assert record["rationale"] == "", f"{template.name} must not carry rationales"


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


def test_w25_headline_numbers_reproduce_from_committed_data() -> None:
    """Pin the published 2026-W25 widen-run headline (the noise-bound claim)."""
    rows = read_jsonl(RESULTS_DIR / "2026-W25-mined.jsonl")
    summary = summarize(rows)
    assert summary["rows"] == 120
    assert summary["by_status"][STATUS_TRIGGER_SKIP] == 118
    assert summary["by_status"][STATUS_EVALUATED] == 2


def test_w26_headline_numbers_reproduce_from_committed_data() -> None:
    """Pin the 2026-W26 deepen run (stripe/agent-toolkit + block/goose +
    pydantic/pydantic-ai). The headline value is the first run with
    `tools_scanned` captured (the #223 fix, validated here on real data).

    Verdict nuance: the decided rows' cold-start `head_decision` is
    `review_required`, but the accuracy scorer uses the per-PR `verify` receipt
    (`labels.effective_verdict` = `verify_verdict or head_decision`), which is
    `insufficient_evidence` for all 6 — so W26 adds NO scored `review_required`
    cases; the effective verdict mix stays IE-dominated. This guard asserts both
    so the README/findings can't drift back to the misleading "non-IE" framing.
    """
    from benchmark.miner.labels import effective_verdict

    rows = read_jsonl(RESULTS_DIR / "2026-W26-mined.jsonl")
    summary = summarize(rows)
    assert summary["rows"] == 120
    assert summary["by_status"][STATUS_TRIGGER_SKIP] == 110
    assert summary["by_status"][STATUS_EVALUATED] == 6
    assert summary["by_status"][STATUS_SCAN_FAILED] == 4
    evaluated = [r for r in rows if r.status == STATUS_EVALUATED]
    # `summarize.ie_rate_on_decided` reads head_decision; all 6 are
    # review_required at cold-start, so the HEAD IE rate is 0.0.
    assert summary["ie_rate_on_decided"] == 0.0
    assert all(r.head_decision == "review_required" for r in evaluated)
    # But the SCORED (effective) verdict is insufficient_evidence for all 6 —
    # W26 contributes IE cases to the accuracy corpus, not review_required ones.
    assert all(effective_verdict(r) == "insufficient_evidence" for r in evaluated)
    # The #223 tools_scanned fix populated the ratio denominator on real data.
    assert evaluated and all(r.tools_scanned is not None for r in evaluated)


def _load_labels(name: str) -> dict[str, str]:
    import csv

    with (RESULTS_DIR / name).open(encoding="utf-8") as handle:
        return {row["pr_url"].strip(): row["label"].strip() for row in csv.DictReader(handle)}


def test_w24_labeled_scores_reproduce_the_published_accuracy_numbers() -> None:
    """Pin the W24 labeled-corpus headline: the first real-history `must_block`
    rows, and the finding that the mining-era gate abstained rather than blocked.

    These are the numbers the top-level README banner and the W24–W25 section
    cite. If a labels edit or a corpus change moves them, the docs must move too.
    """
    from benchmark.miner.labels import score

    rows = read_jsonl(RESULTS_DIR / "2026-W24-mined.jsonl")
    labels = _load_labels("2026-W24-mined.labels.csv")
    # 13 decided rows labeled (6 stripe reused from W26 + 7 new).
    assert len(labels) == 13
    dist = {}
    for label in labels.values():
        dist[label] = dist.get(label, 0) + 1
    # The two real-history must_block rows surfaced by growing the corpus.
    assert dist.get("must_block") == 2
    scored = score(rows, labels)
    metrics = scored["metrics"]
    # The gate did not auto-pass the unsafe PRs, but abstained instead of
    # blocking — blocked_recall 0/2 on real history.
    assert metrics["blocked_recall"] == 0.0
    assert metrics["must_block_caught"] == 1.0
    assert metrics["benign_escalation_rate"] == 0.0
    # Both must_block rows scored as insufficient_evidence (the abstention).
    assert scored["matrix"]["must_block"]["insufficient_evidence"] == 2
    assert scored["matrix"]["must_block"]["block"] == 0


def test_w25_labeled_scores_reproduce() -> None:
    from benchmark.miner.labels import score

    rows = read_jsonl(RESULTS_DIR / "2026-W25-mined.jsonl")
    labels = _load_labels("2026-W25-mined.labels.csv")
    assert len(labels) == 2
    metrics = score(rows, labels)["metrics"]
    assert metrics["needs_human_caught"] == 1.0
    assert metrics["benign_escalation_rate"] == 0.0


def test_cross_run_trigger_skip_rate_is_high_on_real_history() -> None:
    """The headline cross-run claim: ~93% of real merged PRs trigger-skip.

    Aggregated over every committed run, so it tightens as more runs land and
    fails if a future corpus edit quietly changes the validated noise bound.
    """
    skip = decided = total = 0
    for jsonl_path in _mined_jsonl_files():
        for row in read_jsonl(jsonl_path):
            total += 1
            if row.status == STATUS_TRIGGER_SKIP:
                skip += 1
            if row.head_decision:
                decided += 1
    assert total >= 361
    assert skip / total >= 0.9, f"trigger-skip rate fell to {skip}/{total}"
    # Decided capability-changing PRs are rare on real history — documented,
    # not aspirational. Guard the order of magnitude, not an exact count.
    assert decided / total <= 0.1
