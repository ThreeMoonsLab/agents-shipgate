"""Repeat a fixed public-history run and retain privacy-safe operational counts.

This is measurement apparatus, not an alternative gate. The existing evaluator
and its cold-start/fallback behavior run unchanged. Only observed command
results are counted; a boundary check or a scan never substitutes for verify.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from benchmark.miner import evaluate
from benchmark.miner.labels import load_labels, score
from benchmark.miner.rows import read_jsonl, write_csv, write_jsonl


def project_event(operation: str, result: subprocess.CompletedProcess) -> dict:
    """Allowlist counts/enums only: never commit stderr, source text or prompts."""
    payload = evaluate._parse_json(result.stdout) or {}
    control = payload.get("control") or {}
    next_action = control.get("next_action") or {}
    review = payload.get("capability_review") or {}
    changes = review.get("top_changes") or []
    return {
        "operation": operation,
        "exit_code": result.returncode,
        "json_answer": bool(payload),
        "error": payload.get("error"),
        "manifest_status": payload.get("manifest_status"),
        "control_state": control.get("state") or control.get("control_state"),
        "next_action_kind": next_action.get("kind"),
        "next_actor": next_action.get("actor"),
        "permissions": control.get("permissions"),
        "execution": payload.get("execution"),
        "head_status": payload.get("head_status"),
        "base_status": payload.get("base_status"),
        "named_change_count": sum(bool(item.get("subject")) for item in changes),
        "source_linked_change_count": sum(bool(item.get("source_path")) for item in changes),
    }


def observe_case(row, repo_path: Path) -> tuple[object, dict]:
    events = []
    original = evaluate._run

    def observed_run(cmd, **kwargs):
        result = original(cmd, **kwargs)
        if "agents_shipgate" not in cmd:
            return result
        operation = cmd[cmd.index("agents_shipgate") + 1]
        try:
            event = project_event(operation, result)
            if operation == "verify" and "--out" in cmd:
                record_report_counts(event, Path(cmd[cmd.index("--out") + 1]))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            # A broken observation is not an engine failure and must not
            # change the evaluator's result. Absence is explicit, never zero.
            event = {"operation": operation, "observation_error": type(exc).__name__}
        events.append(event)
        return result

    with patch.object(evaluate, "_run", observed_run):
        result = evaluate.evaluate_pr(
            repo_path=repo_path,
            base_sha=row.base_sha,
            head_sha=row.head_sha,
            repo=row.repo,
            pr_number=row.pr_number,
            pr_url=row.pr_url,
            title=row.title,
            merged_at=row.merged_at,
            force_run=True,
        )
    return result, {"pr_url": row.pr_url, "events": events}


def record_report_counts(event: dict, reports: Path) -> None:
    report_path = reports / "report.json"
    request_path = reports / "human-review-request.json"
    event["review_question_count"] = 0
    event["evidence_gap_count"] = None
    event["evidence_gap_kinds"] = {}
    if report_path.is_file():
        report = json.loads(report_path.read_text())
        gaps = ((report.get("release_decision") or {}).get("evidence_coverage") or {}).get(
            "evidence_gaps"
        ) or []
        event["evidence_gap_count"] = len(gaps)
        event["evidence_gap_kinds"] = dict(sorted(Counter(gap["kind"] for gap in gaps).items()))
    if request_path.is_file():
        request = json.loads(request_path.read_text())
        event["review_question_count"] = len(request.get("questions") or [])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--repos", type=Path, required=True)
    parser.add_argument("--out-prefix", type=Path, required=True)
    args = parser.parse_args()
    baseline = read_jsonl(args.baseline)
    labels = {}
    label_paths = [args.baseline.parent / f"2026-W{week}-mined.labels.csv" for week in (24, 25, 26)]
    for path in label_paths:
        labels.update(load_labels(path))
    if len({row.pr_url for row in baseline}) != len(baseline) or any(
        row.pr_url not in labels for row in baseline
    ):
        raise ValueError("Every fixed row must have a unique URL and its original label")
    root = Path(__file__).resolve().parents[2]
    git = lambda *cmd: subprocess.check_output(["git", "-C", str(root), *cmd], text=True).strip()  # noqa: E731
    source_commit = git("rev-parse", "HEAD")
    source_tree = git("rev-parse", "HEAD^{tree}")
    git("diff", "--exit-code", "HEAD", "--", "src", "scripts", "pyproject.toml", "constraints")
    results, observations = [], []
    for row in baseline:
        result, observed = observe_case(row, args.repos / row.repo.replace("/", "__"))
        results.append(result)
        observations.append(observed)
        write_csv(results, args.out_prefix.with_suffix(".csv"))
        write_jsonl(results, args.out_prefix.with_suffix(".jsonl"))
        print(
            f"{len(results)}/{len(baseline)} {row.repo}#{row.pr_number}: {result.status} {result.verify_verdict or result.head_decision or 'no answer'}",
            flush=True,
        )
    comparison = {
        "source_commit": source_commit,
        "source_tree": source_tree,
        "measurement_driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
        "label_file_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in label_paths
        },
        "force_run": True,
        "baseline": score(baseline, labels),
        "candidate": score(results, labels),
        "observations": observations,
    }
    args.out_prefix.with_suffix(".comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
