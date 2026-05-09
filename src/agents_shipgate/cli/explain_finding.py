"""``shipgate explain-finding`` — contextual explanation for one finding.

`explain <check-id>` returns the static catalog metadata for a check.
`explain-finding <fingerprint> --from <report.json>` returns the same
catalog metadata PLUS the specific finding's evidence PLUS a templated
prose explanation tied to that evidence — so an agent can produce a
high-quality summary for a human reviewer without re-implementing the
templating itself.

The templated explanation is deterministic per (catalog, finding); same
inputs always produce the same output.
"""

from __future__ import annotations

import json
from difflib import get_close_matches
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.cli.diagnostics import NextAction
from agents_shipgate.core.models import (
    AgentAction,
    CheckMetadata,
    Finding,
    ReadinessReport,
)

_AGENT_ACTION_GUIDANCE: dict[AgentAction, str] = {
    "auto_apply": (
        "This finding has a high-confidence machine-applicable patch "
        "and is safe to auto-apply via `agents-shipgate apply-patches "
        "--confidence high --apply`."
    ),
    "propose_patch_for_review": (
        "A non-manual patch is attached, but the full patch set is not "
        "auto-safe. Propose `apply-patches` to the user and surface any "
        "manual instructions verbatim before they confirm `--apply`."
    ),
    "escalate_to_human": (
        "No machine-applicable patch is available; this needs human "
        "judgment to resolve."
    ),
    "suppress_with_reason": (
        "This check is marked suppressible; if you accept the risk, "
        "add an entry to `checks.ignore` with a concrete reason."
    ),
    "informational": (
        "No action required — this finding is informational or already "
        "suppressed."
    ),
}


def _load_report(path: Path) -> ReadinessReport:
    """Load and validate ``report.json`` from disk.

    Returns a fully-typed :class:`ReadinessReport`. Raises ``ValueError``
    with a structured message on any failure mode (missing, malformed
    JSON, schema-invalid).
    """
    if not path.is_file():
        raise ValueError(f"report file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read report at {path}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"report is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("report JSON must be an object")
    try:
        return ReadinessReport.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"report.json failed validation: {exc}") from exc


def _evidence_summary(evidence: dict[str, Any]) -> str:
    """Render a one-sentence summary of finding evidence.

    Walks the dict in insertion order, keeping it short. Falls back to
    "(no structured evidence)" when empty.
    """
    if not evidence:
        return "(no structured evidence in this finding)"
    parts: list[str] = []
    for key, value in evidence.items():
        # Prefer compact representations for common field shapes.
        if isinstance(value, list):
            if not value:
                continue
            preview = ", ".join(str(v) for v in value[:3])
            if len(value) > 3:
                preview += f", … (+{len(value) - 3})"
            parts.append(f"{key}=[{preview}]")
        elif isinstance(value, dict):
            sub_keys = list(value)[:3]
            parts.append(f"{key}={{{', '.join(sub_keys)}}}")
        else:
            parts.append(f"{key}={value!r}")
    return "; ".join(parts) or "(structured evidence present but empty values)"


def _render_explanation(
    finding: Finding,
    metadata: CheckMetadata | None,
) -> str:
    """Render a 3–5 sentence prose explanation of this finding.

    Deterministic projection of (finding, metadata): same inputs always
    produce the same output. Designed for direct quotation in a PR
    comment or chat reply — names the affected tool, the risk, the
    recommended fix, and the action the agent intends to take.
    """
    tool = finding.tool_name or "the manifest"
    sentences: list[str] = []

    sentences.append(
        f"`{finding.check_id}` ({finding.severity}) fired on {tool}: "
        f"{finding.title.rstrip('.')}."
    )

    rationale_parts: list[str] = []
    if metadata and metadata.fires_when:
        rationale_parts.append(metadata.fires_when.rstrip("."))
    if metadata and metadata.rationale:
        rationale_parts.append(metadata.rationale.rstrip("."))
    if rationale_parts:
        sentences.append("Why it matters: " + "; ".join(rationale_parts) + ".")

    evidence_summary = _evidence_summary(finding.evidence)
    if evidence_summary and not evidence_summary.startswith("(no "):
        sentences.append(f"Evidence: {evidence_summary}.")

    recommendation = (finding.recommendation or "").rstrip(".")
    if recommendation:
        sentences.append(f"Recommended fix: {recommendation}.")

    action: AgentAction | None = finding.agent_action
    if action and action in _AGENT_ACTION_GUIDANCE:
        sentences.append(_AGENT_ACTION_GUIDANCE[action])

    if finding.suppressed:
        reason = finding.suppression_reason or "no reason recorded"
        sentences.append(
            f"This finding is currently suppressed in shipgate.yaml ({reason})."
        )

    return " ".join(sentences)


def explain_finding_payload(
    *,
    fingerprint: str,
    report_path: Path,
    plugins_enabled: bool | None = None,
) -> dict[str, Any]:
    """Build the deterministic payload for ``explain-finding --json``.

    Pure function: takes a fingerprint and a report path, returns a
    serialisable dict. Raises ``ValueError`` on missing fingerprint
    or unparseable report. Importable from tests.
    """
    report = _load_report(report_path)
    target = next(
        (f for f in report.findings if f.fingerprint == fingerprint),
        None,
    )
    if target is None:
        # Suggest a close match by fingerprint prefix.
        all_fps = [f.fingerprint or "" for f in report.findings]
        close = get_close_matches(fingerprint, all_fps, n=1)
        suggestion = close[0] if close else None
        raise FingerprintNotFound(fingerprint, suggestion=suggestion)

    catalog = check_catalog(plugins_enabled=plugins_enabled)
    catalog_lookup = {c.id: c for c in catalog}
    metadata = catalog_lookup.get(target.check_id)

    return {
        "fingerprint": target.fingerprint,
        "id": target.id,
        "check_id": target.check_id,
        "title": target.title,
        "severity": target.severity,
        "category": target.category,
        "tool_name": target.tool_name,
        "tool_id": target.tool_id,
        "evidence": target.evidence,
        "recommendation": target.recommendation,
        "agent_action": target.agent_action,
        "autofix_safe": target.autofix_safe,
        "requires_human_review": target.requires_human_review,
        "suggested_patch_kind": target.suggested_patch_kind,
        "docs_url": target.docs_url,
        "suppressed": target.suppressed,
        "suppression_reason": target.suppression_reason,
        "baseline_status": target.baseline_status,
        "metadata": (
            metadata.model_dump(mode="json") if metadata is not None else None
        ),
        "explanation": _render_explanation(target, metadata),
        "source_report": str(report_path),
    }


class FingerprintNotFound(LookupError):
    """Raised when ``explain-finding`` cannot match the requested
    fingerprint to a finding in the report."""

    def __init__(self, fingerprint: str, *, suggestion: str | None) -> None:
        self.fingerprint = fingerprint
        self.suggestion = suggestion
        suffix = f" Did you mean {suggestion}?" if suggestion else ""
        super().__init__(f"Unknown fingerprint: {fingerprint}.{suffix}")


def explain_finding(
    fingerprint: str = typer.Argument(
        ...,
        help=(
            "Finding fingerprint (e.g. `fp_f092940f62fbb012`). Read it "
            "from `findings[].fingerprint` in `report.json`."
        ),
    ),
    source: Path = typer.Option(
        Path("agents-shipgate-reports/report.json"),
        "--from",
        help=(
            "Path to the scan's `report.json`. Default mirrors the "
            "canonical reports directory."
        ),
    ),
    no_plugins: bool = typer.Option(
        False,
        "--no-plugins",
        help=(
            "Do not load third-party check plugins even when "
            "AGENTS_SHIPGATE_ENABLE_PLUGINS is set."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON instead of text.",
    ),
) -> None:
    """Explain a specific finding from a `report.json`, with evidence.

    Returns the catalog metadata, the specific finding's evidence, and
    a 3–5 sentence prose explanation suitable for direct quotation in a
    PR comment or chat reply. Companion to `explain <check-id>`, which
    returns only the static catalog metadata for a check ID.
    """
    try:
        payload = explain_finding_payload(
            fingerprint=fingerprint,
            report_path=source,
            plugins_enabled=False if no_plugins else None,
        )
    except FingerprintNotFound as exc:
        suffix = f" Did you mean {exc.suggestion}?" if exc.suggestion else ""
        typer.echo(
            f"Unknown fingerprint: {exc.fingerprint}.{suffix}", err=True
        )
        emit_agent_mode_error(
            "unknown_fingerprint",
            fingerprint=exc.fingerprint,
            suggestion=exc.suggestion,
            source_report=str(source),
            next_action=(
                f"Read findings[].fingerprint in {source} to find the "
                "right id."
            ),
            next_actions=[
                NextAction(
                    kind="review",
                    path=str(source),
                    why=(
                        "Walk findings[] to copy the exact fingerprint "
                        "string. Fingerprints are stable across scans "
                        "for the same (check_id, tool_name, evidence) "
                        "tuple."
                    ),
                    expects=(
                        "A `findings[].fingerprint` value of the form "
                        "`fp_<16-hex-chars>`."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(2) from exc
    except ValueError as exc:
        typer.echo(f"explain-finding: {exc}", err=True)
        emit_agent_mode_error(
            "input_parse_error",
            message=str(exc),
            source_report=str(source),
            next_action="agents-shipgate scan -c shipgate.yaml --format json",
            next_actions=[
                NextAction(
                    kind="command",
                    command=(
                        "agents-shipgate scan -c shipgate.yaml --format json"
                    ),
                    why=(
                        f"Could not load {source}. Generate a fresh "
                        "report.json with the canonical 4-call flow."
                    ),
                    expects=(
                        "agents-shipgate-reports/report.json on disk, "
                        "validatable against the current report schema."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(3) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(payload["fingerprint"])
    typer.echo(f"Check: {payload['check_id']}")
    typer.echo(f"Severity: {payload['severity']}")
    if payload["tool_name"]:
        typer.echo(f"Tool: {payload['tool_name']}")
    typer.echo(f"Title: {payload['title']}")
    typer.echo("")
    typer.echo(payload["explanation"])
    if payload["docs_url"]:
        typer.echo("")
        typer.echo(f"Docs: {payload['docs_url']}")
