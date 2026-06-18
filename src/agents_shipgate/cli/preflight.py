from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from agents_shipgate.core.codex_boundary import parse_unified_diff
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging
from agents_shipgate.core.preflight import build_preflight_result
from agents_shipgate.schemas.preflight import (
    CapabilityRequestV1,
    PreflightPlanV1,
    PreflightResultV1,
    PreflightResultV2,
)


def preflight(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace root to inspect.",
    ),
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        "-c",
        help="Shipgate manifest path, relative to --workspace unless absolute.",
    ),
    changed_files: Path | None = typer.Option(
        None,
        "--changed-files",
        help="Newline-delimited paths the agent plans to edit or has changed.",
    ),
    diff: Path | None = typer.Option(
        None,
        "--diff",
        help="Unified diff file to classify. Use '-' to read stdin.",
    ),
    capability_request: Path | None = typer.Option(
        None,
        "--capability-request",
        help="JSON file describing a proposed high-risk action before implementation.",
    ),
    plan: Path | None = typer.Option(
        None,
        "--plan",
        help="PreflightPlanV1 JSON file. Use '-' to read stdin.",
    ),
    base_preflight: Path | None = typer.Option(
        None,
        "--base-preflight",
        help="Prior preflight JSON to compare policy/trust-root graph hashes against.",
    ),
    host_baseline: Path | None = typer.Option(
        None,
        "--host-baseline",
        help="Host-grants baseline to compare against. Defaults to .agents-shipgate/host-grants.json when present.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the PreflightResultV2 JSON contract.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show debug details."),
) -> None:
    """Run the proactive static preflight contract for coding agents."""

    try:
        configure_logging(verbose=verbose)
        if plan is not None:
            _reject_plan_flag_mix(
                changed_files=changed_files,
                diff=diff,
                capability_request=capability_request,
                base_preflight=base_preflight,
            )
            request_plan = _read_plan(plan)
            result = build_preflight_result(
                workspace=workspace,
                config=config,
                plan=request_plan,
                host_baseline=host_baseline,
            )
        else:
            changed = _read_changed_files(changed_files)
            if diff is not None:
                changed = sorted(set(changed) | set(_changed_files_from_diff(diff)))
            request = _read_capability_request(capability_request)
            base = _read_base_preflight(base_preflight)
            result = build_preflight_result(
                workspace=workspace,
                config=config,
                changed_files=changed,
                capability_request=request,
                base_preflight=base,
                host_baseline=host_baseline,
            )
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise typer.Exit(3) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        raise typer.Exit(4) from exc
    except OSError as exc:
        typer.echo(f"Input error: {exc}", err=True)
        raise typer.Exit(3) from exc

    payload = result.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(
        "Agents Shipgate preflight: "
        f"{'human review required' if result.requires_human_review else 'continue'}"
    )
    typer.echo(f"Protected surface touches: {len(result.protected_surface_touches)}")
    missing = [item for item in result.required_evidence if not item.satisfied]
    typer.echo(f"Missing required evidence: {len(missing)}")
    typer.echo(f"Signals: {len(result.signals)}")
    typer.echo(f"Requires verify: {str(result.requires_verify).lower()}")
    typer.echo(f"Next action: {result.first_next_action.why}")

def _read_changed_files(path: Path | None) -> list[str]:
    if path is None:
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _changed_files_from_diff(path: Path) -> list[str]:
    text = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")
    return sorted({item.path for item in parse_unified_diff(text) if item.path})


def _read_capability_request(path: Path | None) -> CapabilityRequestV1 | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputParseError(f"Capability request is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InputParseError("Capability request JSON must be an object.")
    try:
        return CapabilityRequestV1.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid capability request: {exc}") from exc


def _read_plan(path: Path) -> PreflightPlanV1:
    payload = _read_json_file_or_stdin(path, label="Preflight plan")
    if not isinstance(payload, dict):
        raise InputParseError("Preflight plan JSON must be an object.")
    try:
        return PreflightPlanV1.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid preflight plan: {exc}") from exc


def _read_base_preflight(path: Path | None) -> PreflightResultV1 | PreflightResultV2 | None:
    if path is None:
        return None
    payload = _read_json_file_or_stdin(path, label="Base preflight")
    if not isinstance(payload, dict):
        raise InputParseError("Base preflight JSON must be an object.")
    try:
        if payload.get("preflight_schema_version") == "0.2":
            return PreflightResultV2.model_validate(payload)
        return PreflightResultV1.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid base preflight result: {exc}") from exc


def _read_json_file_or_stdin(path: Path, *, label: str) -> Any:
    raw = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputParseError(f"{label} is not valid JSON: {exc}") from exc


def _reject_plan_flag_mix(
    *,
    changed_files: Path | None,
    diff: Path | None,
    capability_request: Path | None,
    base_preflight: Path | None,
) -> None:
    mixed = [
        name
        for name, value in (
            ("--changed-files", changed_files),
            ("--diff", diff),
            ("--capability-request", capability_request),
            ("--base-preflight", base_preflight),
        )
        if value is not None
    ]
    if mixed:
        raise ConfigError(
            "--plan cannot be combined with "
            + ", ".join(mixed)
            + "; put those inputs in the plan object or run legacy mode."
        )


__all__ = ["preflight"]
