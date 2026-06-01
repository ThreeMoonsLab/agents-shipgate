from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.core.errors import InputParseError

feedback_app = typer.Typer(
    help="Export explicit, redacted verifier feedback artifacts.",
    no_args_is_help=True,
)

REVIEWER_FEEDBACK_REQUESTED = [
    "was_capability_correctly_classified",
    "was_any_capability_missed",
    "was_next_action_clear",
    "was_this_false_positive",
]


@feedback_app.command("export")
def feedback_export(
    source: Path = typer.Option(
        Path("agents-shipgate-reports/verifier.json"),
        "--from",
        help="Path to verifier.json.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Write the feedback artifact to this path.",
    ),
    redact: bool = typer.Option(
        True,
        "--redact/--no-redact",
        help="Keep the export limited to reviewer-safe projections.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the feedback artifact JSON to stdout.",
    ),
) -> None:
    """Export a small design-partner feedback artifact from verifier.json.

    The export is intentionally derived from verifier projections, not raw
    finding evidence. With ``--redact`` it is safe to attach to an issue or
    design-partner email after the user has reviewed the top-level fields.
    """
    try:
        payload = _load_verifier(source)
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise typer.Exit(3) from exc
    feedback = build_feedback_payload(payload, source=source, redacted=redact)
    rendered = json.dumps(feedback, indent=2, sort_keys=True) + "\n"

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    if json_output or out is None:
        typer.echo(rendered.rstrip())
    else:
        typer.echo(f"Wrote feedback artifact to {out}")


def build_feedback_payload(
    verifier: dict[str, Any],
    *,
    source: Path,
    redacted: bool,
) -> dict[str, Any]:
    release_decision = _dict(verifier.get("release_decision"))
    capability_review = _dict(verifier.get("capability_review"))
    first_next_action = _dict(verifier.get("first_next_action"))
    fix_task = _dict(verifier.get("fix_task"))
    trigger = _dict(verifier.get("trigger"))

    blockers = _items(release_decision.get("blockers"))
    review_items = _items(release_decision.get("review_items"))
    top_changes = _top_changes(capability_review.get("top_changes"), redacted=redacted)
    related_finding_ids = _related_finding_ids(capability_review.get("top_changes"))
    release_item_ids = {
        str(item.get("id"))
        for item in [*blockers, *review_items]
        if item.get("id") is not None
    }

    return {
        "feedback_schema_version": "0.1",
        "source_verifier": _display_path(source, redacted=redacted),
        "redacted": redacted,
        "merge_verdict": verifier.get("merge_verdict"),
        "can_merge_without_human": bool(verifier.get("can_merge_without_human")),
        "decision": verifier.get("decision") or release_decision.get("decision"),
        "mode": verifier.get("mode"),
        "trigger": {
            "should_run": trigger.get("should_run"),
            "action": trigger.get("action"),
            "matched_rule_ids": trigger.get("matched_rule_ids", []),
        },
        "first_next_action": {
            key: first_next_action.get(key)
            for key in ("actor", "kind", "command", "why")
            if key in first_next_action
        },
        "fix_task": _fix_task_projection(fix_task),
        "capability_review": {
            "trust_root_touched": bool(capability_review.get("trust_root_touched")),
            "policy_weakened": bool(capability_review.get("policy_weakened")),
            "capability_changes_added": capability_review.get("added", 0),
            "capability_changes_modified": capability_review.get("modified", 0),
            "capability_changes_removed": capability_review.get("removed", 0),
            "top_changes": top_changes,
        },
        "finding_ids": sorted(release_item_ids | related_finding_ids),
        "reviewer_feedback_requested": list(REVIEWER_FEEDBACK_REQUESTED),
        "artifacts": _artifact_projection(verifier.get("artifacts"), redacted=redacted),
    }


def _load_verifier(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputParseError(f"verifier.json not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputParseError(f"verifier.json is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise InputParseError(f"verifier.json must contain an object: {path}")
    return data


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _top_changes(value: Any, *, redacted: bool) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return changes
    keys = (
        "id",
        "title",
        "change_type",
        "change_bucket",
        "subject_kind",
        "subject",
        "impact",
        "related_finding_ids",
    )
    if not redacted:
        keys = (*keys, "rationale", "source_path", "source_start_line")
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        changes.append({key: item.get(key) for key in keys if key in item})
    return changes


def _related_finding_ids(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    output: set[str] = set()
    for change in value:
        if not isinstance(change, dict):
            continue
        ids = change.get("related_finding_ids", [])
        if not isinstance(ids, list):
            continue
        output.update(str(fid) for fid in ids)
    return output


def _artifact_projection(value: Any, *, redacted: bool) -> dict[str, Any]:
    return {
        key: _display_path(path, redacted=redacted)
        for key, path in _dict(value).items()
        if key in {"verifier_json", "report_json", "pr_comment"}
    }


def _display_path(value: Any, *, redacted: bool) -> str:
    text = str(value)
    if not redacted:
        return text
    return Path(text).name


def _fix_task_projection(value: dict[str, Any]) -> dict[str, Any] | None:
    if not value:
        return None
    return {
        key: value.get(key)
        for key in (
            "actor",
            "safe_to_attempt",
            "instructions",
            "forbidden_shortcuts",
            "verification_command",
        )
        if key in value
    }


__all__ = ["build_feedback_payload", "feedback_app"]
