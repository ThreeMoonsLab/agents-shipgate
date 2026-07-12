from __future__ import annotations

import hashlib
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
    control = _dict(verifier.get("control"))
    first_next_action = _dict(control.get("next_action")) or _dict(
        verifier.get("first_next_action")
    )
    fix_task = _dict(verifier.get("fix_task"))
    trigger = _dict(verifier.get("trigger"))

    blockers = _items(release_decision.get("blockers"))
    review_items = _items(release_decision.get("review_items"))
    top_changes = _top_changes(capability_review.get("top_changes"), redacted=redacted)
    related_finding_ids = _related_finding_ids(capability_review.get("top_changes"))
    release_item_ids = {
        str(item.get("id")) for item in [*blockers, *review_items] if item.get("id") is not None
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


_HUMAN_DECISIONS = frozenset({"merged", "rejected", "changes_requested", "none"})


@feedback_app.command("capture")
def feedback_capture(
    before: Path = typer.Option(
        ...,
        "--before",
        help="verifier.json from before the repair (the initial verdict).",
    ),
    after: Path | None = typer.Option(
        None,
        "--after",
        help="verifier.json after the repair, if a repair was attempted.",
    ),
    prompt_class: str | None = typer.Option(
        None,
        "--prompt-class",
        help="Coarse label for the task, e.g. add_refund_tool.",
    ),
    prompt: Path | None = typer.Option(
        None, "--prompt", help="Task prompt file (content only with --no-redact)."
    ),
    diff: Path | None = typer.Option(
        None, "--diff", help="PR/repair diff (diffstat always; content only with --no-redact)."
    ),
    transcript: Path | None = typer.Option(
        None,
        "--transcript",
        help="Agent transcript (opt-in; provenance only unless --no-redact).",
    ),
    human_decision: str | None = typer.Option(
        None,
        "--human-decision",
        help="Declared human outcome: merged / rejected / changes_requested / none.",
    ),
    out: Path | None = typer.Option(
        None, "--out", help="Write the scenario artifact to this path."
    ),
    redact: bool = typer.Option(
        True,
        "--redact/--no-redact",
        help="Provenance-only: omit raw prompt/diff/transcript content and reduce paths to filenames.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the scenario JSON to stdout."),
) -> None:
    """Capture a replayable workflow-evidence scenario from a verify before/after pair.

    Turns one real pilot PR into a labeled, deterministic record: the verdict
    transition, a gate-integrity signal (is the PR `mergeable` while a trust-root
    touch or policy weakening is present — which a valid verifier never emits?),
    the capability delta, and — opt-in,
    redacted-by-default — the prompt / diff / transcript provenance. This feeds
    the benchmark flywheel; it does not gate.
    """
    try:
        before_payload = _load_verifier(before)
        after_payload = _load_verifier(after) if after is not None else None
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise typer.Exit(3) from exc
    if human_decision is not None and human_decision not in _HUMAN_DECISIONS:
        typer.echo(f"--human-decision must be one of {sorted(_HUMAN_DECISIONS)}", err=True)
        raise typer.Exit(2)
    # An explicitly provided evidence path that cannot be read is a user error,
    # not "no evidence" — failing loud avoids a silently incomplete benchmark
    # artifact (an unreadable --prompt must not become `included: false`).
    for flag, evidence_path in (
        ("--prompt", prompt),
        ("--diff", diff),
        ("--transcript", transcript),
    ):
        if evidence_path is not None and not evidence_path.is_file():
            typer.echo(
                f"Input parsing error: {flag} file not found or unreadable: {evidence_path}",
                err=True,
            )
            raise typer.Exit(3)
    scenario = build_scenario_payload(
        before_payload,
        after_payload,
        before_source=before,
        after_source=after,
        prompt_class=prompt_class,
        prompt_path=prompt,
        diff_path=diff,
        transcript_path=transcript,
        human_decision=human_decision,
        redacted=redact,
    )
    rendered = json.dumps(scenario, indent=2, sort_keys=True) + "\n"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    if json_output or out is None:
        typer.echo(rendered.rstrip())
    else:
        typer.echo(f"Wrote scenario to {out}")


def build_scenario_payload(
    before: dict[str, Any],
    after: dict[str, Any] | None,
    *,
    before_source: Path,
    after_source: Path | None,
    prompt_class: str | None,
    prompt_path: Path | None,
    diff_path: Path | None,
    transcript_path: Path | None,
    human_decision: str | None,
    redacted: bool,
) -> dict[str, Any]:
    """Project a verify before/after pair onto a deterministic scenario record.

    Pure function of the two verifier projections plus optional evidence files.
    No wall-clock fields, so re-deriving from the same inputs is byte-identical.
    """
    before_state = _verifier_state(before)
    after_state = _verifier_state(after) if after is not None else None
    return {
        "scenario_schema_version": "0.1",
        "redacted": redacted,
        "prompt_class": prompt_class,
        "human_decision": human_decision,
        "before": before_state,
        "after": after_state,
        "transition": _transition(before_state, after_state),
        "evidence": {
            "prompt": _evidence_entry(prompt_path, redacted=redacted),
            "diff": _evidence_entry(diff_path, redacted=redacted, diffstat=True),
            "transcript": _evidence_entry(transcript_path, redacted=redacted),
        },
        "source": {
            "before": _display_path(before_source, redacted=redacted),
            "after": (
                _display_path(after_source, redacted=redacted) if after_source is not None else None
            ),
        },
    }


def _verifier_state(verifier: dict[str, Any]) -> dict[str, Any]:
    capability_review = _dict(verifier.get("capability_review"))
    release_decision = _dict(verifier.get("release_decision"))
    return {
        "merge_verdict": verifier.get("merge_verdict"),
        "decision": verifier.get("decision") or release_decision.get("decision"),
        "applicability": verifier.get("applicability"),
        "can_merge_without_human": bool(verifier.get("can_merge_without_human")),
        "trust_root_touched": bool(capability_review.get("trust_root_touched")),
        "policy_weakened": bool(capability_review.get("policy_weakened")),
        "capability": {
            "added": int(capability_review.get("added", 0) or 0),
            "modified": int(capability_review.get("modified", 0) or 0),
            "removed": int(capability_review.get("removed", 0) or 0),
        },
    }


def _transition(before: dict[str, Any], after: dict[str, Any] | None) -> dict[str, Any]:
    verdict_before = before["merge_verdict"]
    if after is None:
        return {
            "verdict_before": verdict_before,
            "verdict_after": None,
            "resolved": False,
            "introduced_trust_root_touch": False,
            "introduced_policy_weakening": False,
            "suspected_gate_bypass": False,
        }
    verdict_after = after["merge_verdict"]
    resolved = verdict_before != "mergeable" and verdict_after == "mergeable"
    introduced_trust_root_touch = bool(after["trust_root_touched"]) and not bool(
        before["trust_root_touched"]
    )
    introduced_policy_weakening = bool(after["policy_weakened"]) and not bool(
        before["policy_weakened"]
    )
    # Gate-integrity alarm — STATE-BASED, not delta-based. The self-approval
    # prohibition means a verify run that touches a trust root or weakens policy
    # can never be `mergeable`; it routes to human review. So an `after` that is
    # `mergeable` while *either* flag is set is internally inconsistent — the gate
    # was bypassed (e.g. committed with --no-verify) or the artifact was
    # hand-edited. It does not matter whether the flag was newly introduced: a
    # pre-existing flag that survives into a `mergeable` verdict is just as
    # impossible. (Keying this on `introduced_*` would miss the case where the
    # flag is true in both before and after.)
    suspected_gate_bypass = verdict_after == "mergeable" and (
        bool(after["trust_root_touched"]) or bool(after["policy_weakened"])
    )
    return {
        "verdict_before": verdict_before,
        "verdict_after": verdict_after,
        "resolved": resolved,
        "introduced_trust_root_touch": introduced_trust_root_touch,
        "introduced_policy_weakening": introduced_policy_weakening,
        "suspected_gate_bypass": suspected_gate_bypass,
    }


def _evidence_entry(path: Path | None, *, redacted: bool, diffstat: bool = False) -> dict[str, Any]:
    """Provenance for an optional evidence file.

    The ``sha256`` is over the **raw file bytes** (so it matches ``sha256sum``
    and stays replayable) and ``bytes`` is the raw byte length. The decoded view
    is computed separately and only where needed — ``text`` (omitted under
    ``--redact``) and the diffstat — with ``errors="replace"`` so non-UTF-8
    evidence never crashes; the byte-exact hash remains the source of truth, and
    universal-newline normalization can never alter what is hashed."""
    entry: dict[str, Any] = {"included": False, "sha256": None, "bytes": None, "text": None}
    if diffstat:
        entry.update({"files": None, "insertions": None, "deletions": None})
    if path is None:
        return entry
    try:
        data = path.read_bytes()
    except OSError:
        return entry
    entry["included"] = True
    entry["sha256"] = hashlib.sha256(data).hexdigest()
    entry["bytes"] = len(data)
    text = data.decode("utf-8", errors="replace")
    entry["text"] = None if redacted else text
    if diffstat:
        lines = text.splitlines()
        entry["files"] = sum(1 for line in lines if line.startswith("diff --git "))
        entry["insertions"] = sum(
            1 for line in lines if line.startswith("+") and not line.startswith("+++")
        )
        entry["deletions"] = sum(
            1 for line in lines if line.startswith("-") and not line.startswith("---")
        )
    return entry


__all__ = ["build_feedback_payload", "build_scenario_payload", "feedback_app"]
