"""Tests for the merged-PR history miner (benchmark/miner).

Network-free: every test builds a local git repo and evaluates base/head
SHAs directly via :func:`benchmark.miner.evaluate.evaluate_pr`. The
gh-dependent enumeration path is intentionally untested here (it is a
thin subprocess wrapper run by maintainers, not CI).
"""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path

from benchmark.miner.evaluate import evaluate_pr
from benchmark.miner.rows import (
    CSV_COLUMNS,
    STATUS_EVALUATED,
    STATUS_TRIGGER_SKIP,
    MinedRow,
    summarize,
    write_csv,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "clone"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    return repo


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def test_capability_pr_is_evaluated_end_to_end(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    # The organic trigger catalog keys on canonical names (**/*mcp*.json);
    # a bare root tools.json does NOT trigger — that's the catalog's noise
    # bound, and real mining uses --force-run to sample such repos anyway.
    (repo / "mcp-tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (repo / "README.md").write_text("agent service\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "mcp-tools.json").write_text(
        '{"tools": [{"name": "delete_files", "description": "Delete files."}]}\n',
        encoding="utf-8",
    )
    head = _commit_all(repo, "add risky tool")

    row = evaluate_pr(
        repo_path=repo,
        base_sha=base,
        head_sha=head,
        repo="local/fixture",
        pr_number=1,
        pr_url="local://1",
        title="add risky tool",
    )

    assert row.trigger_run is True, row.to_json()
    assert row.status == STATUS_EVALUATED, row.to_json()
    assert row.check_decision in {"allow", "warn", "block", "require_review"}
    assert row.init_status == "written"
    assert row.head_decision in {
        "passed",
        "review_required",
        "insufficient_evidence",
        "blocked",
    }
    assert row.files_changed == 1
    # v0.2 receipt fields: the real verify ran base-vs-head with the
    # injected manifest on both sides and produced a per-PR verdict.
    assert row.verify_verdict in {
        "mergeable",
        "human_review_required",
        "insufficient_evidence",
        "blocked",
        "unknown",
    }, row.to_json()
    assert row.verify_decision != "", row.to_json()
    assert isinstance(row.verify_can_merge, bool)
    # The worktrees must be cleaned up even on success.
    assert _git(repo, "worktree", "list").count("\n") == 0


def test_pr_that_adds_real_manifest_keeps_the_trust_root_diff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "mcp-tools.json").write_text(
        '{"tools": [{"name": "delete_files", "description": "Delete files."}]}\n',
        encoding="utf-8",
    )
    base = _commit_all(repo, "base: tool source, no manifest")
    # The PR ADDS a real shipgate.yaml — a trust-root surface. From the miner's
    # POV the manifest is "preexisting" (head already has it), but the base
    # genuinely lacks it: the receipt must not erase that trust-root diff by
    # mirroring head's manifest onto base.
    (repo / "shipgate.yaml").write_text(
        'version: "0.1"\n'
        "project: {name: adds-manifest}\n"
        "agent: {name: svc-agent, declared_purpose: [serve]}\n"
        "environment: {target: production_like}\n"
        "tool_sources: [{id: mcp, type: mcp, path: mcp-tools.json}]\n",
        encoding="utf-8",
    )
    head = _commit_all(repo, "add shipgate.yaml")

    row = evaluate_pr(repo_path=repo, base_sha=base, head_sha=head)

    assert row.init_status == "preexisting", row.to_json()
    assert row.verify_trust_root_touched is True, row.to_json()
    assert row.verify_can_merge is False, row.to_json()


def test_docs_only_pr_is_a_trigger_skip(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (repo / "README.md").write_text("v1\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "README.md").write_text("v2 docs only\n", encoding="utf-8")
    head = _commit_all(repo, "docs")

    row = evaluate_pr(repo_path=repo, base_sha=base, head_sha=head)

    assert row.trigger_run is False
    assert row.status == STATUS_TRIGGER_SKIP
    assert row.head_decision == ""


def test_bad_sha_yields_error_row_not_exception(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _commit_all(repo, "only")

    row = evaluate_pr(repo_path=repo, base_sha="deadbeef", head_sha="deadbeef")

    assert row.status == "error"
    assert "unresolvable_base_sha" in row.notes


def test_csv_roundtrip_matches_schema(tmp_path: Path) -> None:
    rows = [MinedRow(repo="r", pr_number=1, pr_url="u", title="t", merged_at="", base_sha="b", head_sha="h")]
    out = tmp_path / "rows.csv"
    write_csv(rows, out)
    with out.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == CSV_COLUMNS
        parsed = list(reader)
    assert parsed[0]["repo"] == "r"
    assert parsed[0]["head_blockers"] == ""  # None serializes to blank, not "None".
    # LF-only: CRLF rows fail `git diff --check` on every committed line.
    assert b"\r" not in out.read_bytes()


def test_evaluate_resolves_revision_expressions_to_shas(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("v1\n", encoding="utf-8")
    _commit_all(repo, "base")
    (repo / "README.md").write_text("v2\n", encoding="utf-8")
    head = _commit_all(repo, "docs")

    row = evaluate_pr(repo_path=repo, base_sha="HEAD^1", head_sha="HEAD")

    # The committed row contract says these columns are commit ids.
    assert row.head_sha == head
    assert row.base_sha != "HEAD^1"
    assert len(row.base_sha) == 40


def test_unresolved_candidate_becomes_error_row() -> None:
    from benchmark.miner.__main__ import unresolved_candidate_row
    from benchmark.miner.candidates import Candidate

    candidate = Candidate(
        repo="o/r", pr_number=9, pr_url="u", title="t",
        merged_at="2026-06-12", merge_sha="a" * 40,
    )
    row = unresolved_candidate_row(candidate)
    assert row.status == "error"
    assert row.notes == "merge_commit_not_in_clone"
    assert row.pr_number == 9


def test_summarize_reports_ie_rate() -> None:
    evaluated_ie = MinedRow(
        repo="r", pr_number=1, pr_url="", title="", merged_at="", base_sha="", head_sha="",
        status=STATUS_EVALUATED, head_decision="insufficient_evidence",
    )
    evaluated_ok = MinedRow(
        repo="r", pr_number=2, pr_url="", title="", merged_at="", base_sha="", head_sha="",
        status=STATUS_EVALUATED, head_decision="passed",
    )
    skip = MinedRow(
        repo="r", pr_number=3, pr_url="", title="", merged_at="", base_sha="", head_sha="",
        status=STATUS_TRIGGER_SKIP,
    )
    summary = summarize([evaluated_ie, evaluated_ok, skip])
    assert summary["rows"] == 3
    assert summary["ie_rate_on_decided"] == 0.5
