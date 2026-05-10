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

_MIN_SUPPORTED_SCHEMA = "0.12"


def _version_tuple(value: str) -> tuple[int, ...]:
    """Parse a `MAJOR.MINOR` schema version into a comparable tuple.

    Raises ``ValueError`` for malformed strings so the CLI maps the
    failure to ``input_parse_error`` (exit 3) rather than a 500-style
    crash."""
    try:
        return tuple(int(part) for part in value.split("."))
    except (AttributeError, ValueError) as exc:
        raise ValueError(
            f"invalid report_schema_version: {value!r}"
        ) from exc


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


def _load_report(path: Path) -> tuple[ReadinessReport, dict[str, Any]]:
    """Load, version-gate, and validate ``report.json`` from disk.

    Returns ``(report, raw_payload)``: the typed
    :class:`ReadinessReport` plus the raw dict (for pass-through fields
    that the Pydantic model may strip). Raises ``ValueError`` with a
    structured message on any failure mode (missing, malformed JSON,
    schema-invalid, or pre-v0.12 schema version).

    Pydantic's ``ReadinessReport`` is intentionally looser than
    ``docs/report-schema.v0.12.json`` (e.g. ``Finding.agent_action``
    is ``Optional`` so test fixtures can construct minimal findings).
    Without the explicit version gate, ``explain-finding`` would
    silently accept a v0.11 report and return ``"agent_action": null``
    in the payload, contradicting the v0.12 contract that the
    explanation is action-aware (#58 review P2.2).
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

    version = payload.get("report_schema_version")
    if not isinstance(version, str):
        raise ValueError(
            "input must be an agents-shipgate report.json with a "
            "string `report_schema_version`."
        )
    if _version_tuple(version) < _version_tuple(_MIN_SUPPORTED_SCHEMA):
        raise ValueError(
            f"explain-finding requires report_schema_version "
            f">= {_MIN_SUPPORTED_SCHEMA} (got {version!r}). The "
            "v0.12 schema added the per-finding `agent_action` enum "
            "that this command depends on. Re-scan with the current "
            "CLI: `agents-shipgate scan -c shipgate.yaml --format json`."
        )

    try:
        return ReadinessReport.model_validate(payload), payload
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
    serialisable dict. Raises ``ValueError`` on missing report or
    pre-v0.12 schema; raises :class:`FingerprintNotFound` when the
    fingerprint doesn't match any finding in the report.

    Payload shape: every canonical ``Finding`` field (via
    :meth:`pydantic.BaseModel.model_dump`) plus three derived fields:

    - ``metadata`` — full :class:`CheckMetadata` for ``check_id``
      (None for unknown ids, e.g. third-party plugins).
    - ``explanation`` — deterministic 3–5 sentence prose summary.
    - ``source_report`` — absolute path to the report file the
      explanation was sourced from.

    Earlier this function returned only a hand-picked subset of
    Finding fields, dropping ``source``, ``patches``, ``confidence``,
    and ``agent_id``. The action-aware sentence in the prose
    explicitly tells agents to surface manual instructions, but with
    ``patches`` missing they had to re-fetch the report — so the
    payload now mirrors the full Finding by default (#58 review P2.1).
    """
    report, _raw_payload = _load_report(report_path)
    target = next(
        (f for f in report.findings if f.fingerprint == fingerprint),
        None,
    )
    if target is None:
        all_fps = [f.fingerprint or "" for f in report.findings]
        close = get_close_matches(fingerprint, all_fps, n=1)
        suggestion = close[0] if close else None
        raise FingerprintNotFound(fingerprint, suggestion=suggestion)

    catalog = check_catalog(plugins_enabled=plugins_enabled)
    catalog_lookup = {c.id: c for c in catalog}
    metadata = catalog_lookup.get(target.check_id)

    # Mirror the canonical Finding shape via model_dump so future
    # additive fields (e.g. v0.13 source-provenance enrichments)
    # flow through automatically. Overlay the three derived fields.
    payload = target.model_dump(mode="json")
    payload["metadata"] = (
        metadata.model_dump(mode="json") if metadata is not None else None
    )
    payload["explanation"] = _render_explanation(target, metadata)
    # `source_report` is documented as an absolute path. Resolve here
    # so a relative `--from` value (e.g. the documented default
    # `agents-shipgate-reports/report.json`) round-trips correctly
    # for downstream tooling that expects a stable, machine-routable
    # path (#58 review P3).
    payload["source_report"] = str(report_path.resolve())
    return payload


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
