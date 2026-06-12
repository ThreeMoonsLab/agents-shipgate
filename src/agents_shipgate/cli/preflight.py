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
from agents_shipgate.schemas.preflight import CapabilityRequestV1, PreflightResultV1


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
    base_preflight: Path | None = typer.Option(
        None,
        "--base-preflight",
        help="Prior preflight JSON to compare policy/trust-root graph hashes against.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the PreflightResultV1 JSON contract.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show debug details."),
) -> None:
    """Run the proactive static preflight contract for coding agents."""

    try:
        configure_logging(verbose=verbose)
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


def _read_base_preflight(path: Path | None) -> PreflightResultV1 | None:
    if path is None:
        return None
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputParseError(f"Base preflight is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InputParseError("Base preflight JSON must be an object.")
    try:
        return PreflightResultV1.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid base preflight result: {exc}") from exc


__all__ = ["preflight"]
