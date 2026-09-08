from __future__ import annotations

import json
import subprocess
from pathlib import Path

from test_miner import _commit_all, _init_repo

from benchmark.miner import evaluate
from benchmark.miner.reevaluate import observe_case, project_event
from benchmark.miner.rows import MinedRow


def test_observation_keeps_counts_and_routing_without_evidence_text():
    payload = {
        "reason": "PRIVATE SOURCE TEXT",
        "control": {
            "state": "human_review_required",
            "reason": "PRIVATE PROMPT",
            "next_action": {"actor": "human", "kind": "review", "why": "PRIVATE"},
            "permissions": {"merge": False},
        },
        "capability_review": {
            "top_changes": [
                {
                    "subject": "PRIVATE TOOL NAME",
                    "source_path": "PRIVATE PATH",
                    "rationale": "PRIVATE",
                }
            ]
        },
    }
    event = project_event(
        "verify", subprocess.CompletedProcess([], 0, json.dumps(payload), "PRIVATE STDERR")
    )
    assert event["control_state"] == "human_review_required"
    assert event["named_change_count"] == 1
    assert event["source_linked_change_count"] == 1
    assert event["next_action_kind"] == "review"
    assert "PRIVATE" not in json.dumps(event)


def test_observation_failure_cannot_change_the_evaluator_result(tmp_path, monkeypatch):
    row = MinedRow(
        repo="owner/repo",
        pr_number=1,
        pr_url="https://example.test/1",
        title="fixture",
        merged_at="",
        base_sha="a" * 40,
        head_sha="b" * 40,
    )
    command_result = subprocess.CompletedProcess([], 0, '{"control": "invalid"}', "")
    monkeypatch.setattr(evaluate, "_run", lambda *args, **kwargs: command_result)
    original_run = evaluate._run

    def fake_evaluate(**kwargs):
        assert kwargs["force_run"] is True
        assert kwargs["base_sha"] == row.base_sha
        assert evaluate._run(["python", "-m", "agents_shipgate", "verify"]) is command_result
        return row

    monkeypatch.setattr(evaluate, "evaluate_pr", fake_evaluate)
    result, observation = observe_case(row, tmp_path)
    assert result is row
    assert observation["events"] == [{"operation": "verify", "observation_error": "AttributeError"}]
    assert evaluate._run is original_run


def test_observer_reads_real_verifier_before_temporary_artifact_cleanup(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "mcp-tools.json").write_text('{"tools": []}\n')
    base = _commit_all(repo, "base")
    (repo / "mcp-tools.json").write_text(
        '{"tools": [{"name": "delete_files", "description": "Delete files."}]}\n'
    )
    head = _commit_all(repo, "head")
    row = MinedRow(
        repo="local/fixture",
        pr_number=1,
        pr_url="local://1",
        title="fixture",
        merged_at="",
        base_sha=base,
        head_sha=head,
    )
    result, observed = observe_case(row, repo)
    events = [item for item in observed["events"] if item["operation"] == "verify"]
    assert result.verify_verdict
    assert len(events) == 1
    assert events[0]["head_status"] == "succeeded"
    assert events[0]["control_state"]
    assert isinstance(events[0]["permissions"]["merge"], bool)
    assert isinstance(events[0]["evidence_gap_count"], int)
    assert "observation_error" not in events[0]


def test_w37_history_keeps_fixed_inputs_and_publishes_failed_bars():
    from benchmark.miner.labels import load_labels, score
    from benchmark.miner.rows import read_jsonl

    root = Path(__file__).resolve().parents[1]
    results = root / "benchmark/miner/results"
    baseline = read_jsonl(results / "2026-W27-reeval.jsonl")
    candidate = read_jsonl(results / "2026-W37-reeval.jsonl")
    comparison = json.loads((results / "2026-W37-reeval.comparison.json").read_text())
    identity = lambda rows: [(r.pr_url, r.base_sha, r.head_sha) for r in rows]  # noqa: E731
    assert identity(baseline) == identity(candidate)
    assert len(candidate) == 19
    assert len({row.repo for row in candidate}) == 5
    labels = {}
    for week in (24, 25, 26):
        labels.update(load_labels(results / f"2026-W{week}-mined.labels.csv"))
    assert comparison["baseline"] == score(baseline, labels)
    assert comparison["candidate"] == score(candidate, labels)
    assert comparison["candidate"]["metrics"]["must_block_caught"] == 0.0
    assert comparison["candidate"]["metrics"]["needs_human_caught"] == 0.333
    assert sum(bool(row.verify_verdict) for row in candidate) == 0
    assert sum(bool(row.head_decision) for row in candidate) == 3
    assert all(
        not any(event["operation"] == "verify" for event in row["events"])
        for row in comparison["observations"]
    )
    assert not any(
        "observation_error" in event
        for row in comparison["observations"]
        for event in row["events"]
    )

    import hashlib

    assert (
        comparison["baseline_sha256"]
        == hashlib.sha256((results / "2026-W27-reeval.jsonl").read_bytes()).hexdigest()
    )
    assert (
        comparison["measurement_driver_sha256"]
        == hashlib.sha256((root / "benchmark/miner/reevaluate.py").read_bytes()).hexdigest()
    )
    for name, digest in comparison["label_file_sha256"].items():
        assert hashlib.sha256((results / name).read_bytes()).hexdigest() == digest
