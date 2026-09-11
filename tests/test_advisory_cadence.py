"""Keep preview publication separate from qualified-release cadence."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import release_cadence as cadence


@pytest.mark.parametrize("tag", [
    "preview-0.16.0+preview.20260902.g1a2b3c4",
    "preview-0.16.0b7+preview.20260902.gabcdef0",
])
def test_only_publisher_shaped_preview_tags_count(tag: str) -> None:
    assert cadence.is_advisory_tag(tag)
    assert not cadence.is_release_tag(tag)


@pytest.mark.parametrize("tag", [
    "v0.16.0", "preview-0.16.0", "preview-0.16.0+arbitrary",
    "preview-0.16.0+preview.20260230.gabcdef0",
    "preview-0.16.0+preview.20260902.gnotasha",
    "preview-garbage+preview.20260902.gabcdef0",
    "preview-0.16.0+preview.20260902.gabcdef0.extra",
])
def test_unpublished_tag_shapes_cannot_reset_advisory_cadence(tag: str) -> None:
    assert not cadence.is_advisory_tag(tag)


@pytest.mark.parametrize(("days", "status"), [(13, "current"), (14, "due"), (21, "due"), (22, "overdue")])
def test_advisory_thresholds(days: int, status: str) -> None:
    result = cadence.assess(
        [("preview-0.16.0+preview.20260902.gabcdef0", 1_800_000_000 - days * 86400)],
        now=1_800_000_000,
        interval_days=cadence.ADVISORY_INTERVAL_DAYS,
        overdue_days=cadence.ADVISORY_OVERDUE_DAYS,
        label="Advisory cadence",
    )
    assert result.status == status
    assert result.as_line().startswith("Advisory cadence:")


def test_cli_preserves_release_json_and_measures_preview_separately(monkeypatch, capsys) -> None:
    now = int(datetime.now(UTC).timestamp())
    tags = [("v0.15.0", now - 56 * 86400), ("preview-0.16.0+preview.20260902.gabcdef0", now - 86400)]
    monkeypatch.setattr(cadence, "read_release_tags", lambda repo, predicate=cadence.is_release_tag: [item for item in tags if predicate(item[0])])
    assert cadence.main(["--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["latest_release_tag"] == "v0.15.0"
    assert result["status"] == "overdue"
    assert result["days_since_release"] == 56
    assert result["advisory"]["status"] == "current"
    assert result["advisory"]["days_since_release"] == 1


def test_overdue_preview_reaches_operator_and_summary(monkeypatch, capsys, tmp_path: Path) -> None:
    now = int(datetime.now(UTC).timestamp())
    tags = [("v0.15.0", now), ("preview-0.16.0+preview.20260902.gabcdef0", now - 22 * 86400)]
    monkeypatch.setattr(cadence, "read_release_tags", lambda repo, predicate=cadence.is_release_tag: [item for item in tags if predicate(item[0])])
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cadence.main(["--github", "--fail-when-overdue"]) == 1
    output = capsys.readouterr()
    assert "Advisory cadence defect" in output.err
    assert "Release cadence defect" not in output.err
    assert "::warning title=Advisory cadence" in output.out
    assert "## Advisory cadence" in summary.read_text()
    assert "**overdue**" in summary.read_text()
