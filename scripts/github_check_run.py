"""Build a GitHub Check Run payload from Shipgate verify artifacts.

Invoked by ``action.yml`` when ``check_run: true``: reads
``verifier.json`` / ``report.sarif`` / ``pr-comment.md`` from
``$OUTPUT_DIR`` and writes ``check-run-payload.json`` next to them. The
actual Checks API call happens in a ``github-script`` step (same token
path as the PR comment step); this script stays pure and testable.

Mapping contract:

- ``merge_verdict`` → check conclusion: ``mergeable`` → ``success``,
  ``blocked`` → ``failure``, everything else (``human_review_required``,
  ``insufficient_evidence``, ``unknown``, missing) → ``neutral``. The
  conclusion mirrors — never replaces — the one decision engine:
  ``report.json.release_decision.decision`` stays the gate.
- SARIF results → up to ``MAX_ANNOTATIONS`` line-level annotations
  (Checks API caps one request at 50). ``error`` → ``failure``,
  ``warning`` → ``warning``, anything else → ``notice``.
- ``pr-comment.md`` → check summary (truncated to the API limit).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MAX_ANNOTATIONS = 50
# Checks API hard limit is 65535 chars for output.summary; keep headroom.
MAX_SUMMARY_CHARS = 60000
PAYLOAD_FILENAME = "check-run-payload.json"
DEFAULT_CHECK_NAME = "Agents Shipgate"

_CONCLUSIONS = {
    "mergeable": "success",
    "blocked": "failure",
}

_SARIF_LEVELS = {
    "error": "failure",
    "warning": "warning",
}


def conclusion_for(merge_verdict: object) -> str:
    """Map a merge verdict onto a Checks API conclusion.

    Unknown / missing verdicts are ``neutral``: the check must never
    claim success for a verdict it does not understand, and must not
    fail a PR on a verdict that routes to humans rather than blocking.
    """
    if not isinstance(merge_verdict, str):
        return "neutral"
    return _CONCLUSIONS.get(merge_verdict, "neutral")


def title_for(merge_verdict: object, *, blocker_count: int = 0) -> str:
    verdict = merge_verdict if isinstance(merge_verdict, str) and merge_verdict else "unknown"
    if verdict == "blocked" and blocker_count:
        noun = "blocker" if blocker_count == 1 else "blockers"
        return f"merge_verdict: blocked ({blocker_count} {noun})"
    return f"merge_verdict: {verdict}"


def annotations_from_sarif(sarif: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Project SARIF results onto Checks API annotations.

    Deterministic: results are taken in SARIF order, capped at
    ``MAX_ANNOTATIONS``. Only results with a resolvable file location
    become annotations; the rest stay visible in the summary/report.
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
                    "annotation_level": _SARIF_LEVELS.get(
                        result.get("level"), "notice"
                    ),
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
    sarif: dict[str, Any] | None,
    summary_markdown: str,
    name: str = DEFAULT_CHECK_NAME,
) -> dict[str, Any]:
    verifier = verifier or {}
    merge_verdict = verifier.get("merge_verdict")
    release_decision = verifier.get("release_decision") or {}
    blockers = release_decision.get("blockers") or []
    annotations = annotations_from_sarif(sarif)
    total_results = _sarif_result_count(sarif)
    summary = summary_markdown.strip() or "No Shipgate summary was produced."
    if total_results > len(annotations):
        summary += (
            f"\n\n_{len(annotations)} of {total_results} findings shown as "
            "line annotations; see the uploaded report artifact for the "
            "full list._"
        )
    return {
        "name": name,
        "conclusion": conclusion_for(merge_verdict),
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


def main() -> int:
    output_dir = Path(os.environ.get("OUTPUT_DIR") or "agents-shipgate-reports")
    name = os.environ.get("CHECK_RUN_NAME") or DEFAULT_CHECK_NAME
    verifier = _load_json(output_dir / "verifier.json")
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
        sarif=sarif,
        summary_markdown=summary_markdown,
        name=name,
    )
    out_path = output_dir / PAYLOAD_FILENAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
