"""Build a GitHub Check Run payload from Shipgate verify artifacts.

Invoked by ``action.yml`` when ``check_run: true``: reads
``verifier.json`` / ``report.sarif`` / ``pr-comment.md`` from
``$OUTPUT_DIR`` and writes ``check-run-payload.json`` next to them. The
actual Checks API call happens in a ``github-script`` step (same token
path as the PR comment step); this script stays pure and testable.

Mapping contract:

- ``merge_verdict`` + ``check_run_policy`` → check conclusion. The default
  ``advisory`` policy preserves the historical mapping
  (``mergeable`` → ``success``, ``blocked`` → ``failure``, everything else
  → ``neutral``). ``blocked-fails`` maps ``blocked`` and ``unknown`` to
  ``failure`` while leaving human-routed verdicts neutral. ``require-mergeable``
  is the strict branch-protection policy: only
  ``can_merge_without_human=true`` succeeds. The conclusion mirrors — never
  replaces — the one decision engine:
  ``report.json.release_decision.decision`` stays the gate.
- PR projection items → up to ``MAX_ANNOTATIONS`` line-level annotations
  (Checks API caps one request at 50). SARIF remains the full finding export;
  Check Runs intentionally annotate only merge-relevant PR items.
- ``pr-comment.md`` → check summary (truncated to the API limit).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agents_shipgate.report.pr_projection import (
    item_to_check_run_annotation,
    select_pr_items,
)

MAX_ANNOTATIONS = 50
# Checks API hard limit is 65535 chars for output.summary; keep headroom.
MAX_SUMMARY_CHARS = 60000
PAYLOAD_FILENAME = "check-run-payload.json"
DEFAULT_CHECK_NAME = "Agents Shipgate"
DEFAULT_CHECK_RUN_POLICY = "advisory"
CHECK_RUN_POLICIES = frozenset({"advisory", "blocked-fails", "require-mergeable"})

_CONCLUSIONS = {
    "mergeable": "success",
    "blocked": "failure",
}

_SARIF_LEVELS = {
    "error": "failure",
    "warning": "warning",
}


def conclusion_for(
    merge_verdict: object,
    *,
    policy: str = DEFAULT_CHECK_RUN_POLICY,
    can_merge_without_human: object = None,
) -> str:
    """Map a merge verdict onto a Checks API conclusion.

    The default policy is advisory for backward compatibility. Strict branch
    protection should use ``require-mergeable``.
    """
    normalized_policy = normalize_check_run_policy(policy)
    if normalized_policy == "require-mergeable":
        return "success" if _truthy(can_merge_without_human) else "failure"
    if not isinstance(merge_verdict, str):
        return "neutral"
    if normalized_policy == "blocked-fails":
        if merge_verdict == "mergeable":
            return "success"
        if merge_verdict in {"blocked", "unknown"}:
            return "failure"
        return "neutral"
    return _CONCLUSIONS.get(merge_verdict, "neutral")


def normalize_check_run_policy(policy: object) -> str:
    normalized = str(policy or DEFAULT_CHECK_RUN_POLICY).strip().lower()
    if normalized in CHECK_RUN_POLICIES:
        return normalized
    return DEFAULT_CHECK_RUN_POLICY


def title_for(merge_verdict: object, *, blocker_count: int = 0) -> str:
    verdict = merge_verdict if isinstance(merge_verdict, str) and merge_verdict else "unknown"
    if verdict == "blocked" and blocker_count:
        noun = "blocker" if blocker_count == 1 else "blockers"
        return f"merge_verdict: blocked ({blocker_count} {noun})"
    return f"merge_verdict: {verdict}"


def annotations_from_pr_projection(
    report: dict[str, Any] | None,
    verifier: dict[str, Any] | None,
    *,
    limit: int = MAX_ANNOTATIONS,
) -> list[dict[str, Any]]:
    annotations: list[dict[str, Any]] = []
    for item in select_pr_items(report, verifier, limit=max(0, limit)):
        if not item.source_path:
            continue
        annotations.append(item_to_check_run_annotation(item))
    return annotations


def annotations_from_sarif(sarif: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Project SARIF results onto Checks API annotations.

    Compatibility fallback only. Normal check runs use
    :func:`annotations_from_pr_projection` so job annotations and Check Run
    annotations show the same merge-relevant items.
    """
    if not isinstance(sarif, dict):
        return []
    annotations: list[dict[str, Any]] = []
    for run in sarif.get("runs") or []:
        for result in run.get("results") or []:
            if len(annotations) >= MAX_ANNOTATIONS:
                return annotations
            location = _first_location(result)
            if location is None:
                continue
            path, start_line = location
            message = ((result.get("message") or {}).get("text") or "").strip()
            if not message:
                continue
            annotations.append(
                {
                    "path": path,
                    "start_line": start_line,
                    "end_line": start_line,
                    "annotation_level": _SARIF_LEVELS.get(result.get("level"), "notice"),
                    "message": message[:1000],
                    "title": str(result.get("ruleId") or "agents-shipgate"),
                }
            )
    return annotations


def _first_location(result: dict[str, Any]) -> tuple[str, int] | None:
    for location in result.get("locations") or []:
        physical = location.get("physicalLocation") or {}
        artifact = physical.get("artifactLocation") or {}
        uri = artifact.get("uri")
        if not isinstance(uri, str) or not uri:
            continue
        region = physical.get("region") or {}
        start_line = region.get("startLine")
        if not isinstance(start_line, int) or start_line < 1:
            start_line = 1
        return uri, start_line
    return None


def build_check_run_payload(
    *,
    verifier: dict[str, Any] | None,
    report: dict[str, Any] | None = None,
    sarif: dict[str, Any] | None = None,
    summary_markdown: str,
    name: str = DEFAULT_CHECK_NAME,
    check_run_policy: str = DEFAULT_CHECK_RUN_POLICY,
) -> dict[str, Any]:
    verifier = verifier or {}
    merge_verdict = verifier.get("merge_verdict")
    release_decision = verifier.get("release_decision") or {}
    blockers = release_decision.get("blockers") or []
    annotations = annotations_from_pr_projection(report, verifier)
    if not annotations and report is None:
        annotations = annotations_from_sarif(sarif)
    total_results = _sarif_result_count(sarif)
    summary = summary_markdown.strip() or "No Shipgate summary was produced."
    if total_results > len(annotations):
        summary += (
            f"\n\n_{len(annotations)} of {total_results} findings shown as "
            "merge-relevant line annotations; see the uploaded report "
            "artifact for the full list._"
        )
    return {
        "name": name,
        "conclusion": conclusion_for(
            merge_verdict,
            policy=check_run_policy,
            can_merge_without_human=verifier.get("can_merge_without_human"),
        ),
        "output": {
            "title": title_for(merge_verdict, blocker_count=len(blockers)),
            "summary": summary[:MAX_SUMMARY_CHARS],
            "annotations": annotations,
        },
    }


def _sarif_result_count(sarif: dict[str, Any] | None) -> int:
    if not isinstance(sarif, dict):
        return 0
    return sum(len(run.get("results") or []) for run in sarif.get("runs") or [])


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def main() -> int:
    output_dir = Path(os.environ.get("OUTPUT_DIR") or "agents-shipgate-reports")
    name = os.environ.get("CHECK_RUN_NAME") or DEFAULT_CHECK_NAME
    check_run_policy = normalize_check_run_policy(os.environ.get("CHECK_RUN_POLICY"))
    verifier = _load_json(output_dir / "verifier.json")
    report = _load_json(output_dir / "report.json")
    sarif = _load_json(output_dir / "report.sarif")
    comment_path = output_dir / "pr-comment.md"
    summary_markdown = ""
    if comment_path.is_file():
        try:
            summary_markdown = comment_path.read_text(encoding="utf-8")
        except OSError:
            summary_markdown = ""
    payload = build_check_run_payload(
        verifier=verifier,
        report=report,
        sarif=sarif,
        summary_markdown=summary_markdown,
        name=name,
        check_run_policy=check_run_policy,
    )
    out_path = output_dir / PAYLOAD_FILENAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
