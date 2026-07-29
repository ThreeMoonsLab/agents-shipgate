from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from agents_shipgate.cli.agent_mode import emit_agent_mode_error_action
from agents_shipgate.core.codex_boundary import parse_unified_diff
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging
from agents_shipgate.core.preflight import build_preflight_result
from agents_shipgate.schemas.diagnostics import NextAction
from agents_shipgate.schemas.preflight import (
    CapabilityRequestV1,
    PreflightPlanV1,
    PreflightResultV1,
    PreflightResultV2,
    PreflightResultV3,
)

logger = logging.getLogger(__name__)


def _agent_mode_exit(
    error_kind: str,
    exc: BaseException,
    *,
    exit_code: int,
    next_action: str,
    command: str = "agents-shipgate preflight --json",
) -> typer.Exit:
    """Emit the structured agent-mode error line and return the exit to raise.

    ``error_kind`` must be one of the ids published in ``docs/errors.json``;
    an id an agent cannot look up is no better than prose.
    """

    emit_agent_mode_error_action(
        error_kind,
        message=str(exc),
        exit_code=exit_code,
        action=NextAction(
            kind="command",
            command=command,
            why=next_action,
            expects="A preflight run that completes and returns its plan.",
        ),
    )
    return typer.Exit(exit_code)


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
        help="Emit the PreflightResultV3 JSON contract.",
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
            diff_text = None
            if diff is not None:
                diff_text = _read_diff(diff)
                changed = sorted(
                    set(changed)
                    | {item.path for item in parse_unified_diff(diff_text) if item.path}
                )
            request = _read_capability_request(capability_request)
            base = _read_base_preflight(base_preflight)
            result = build_preflight_result(
                workspace=workspace,
                config=config,
                changed_files=changed,
                diff_text=diff_text,
                capability_request=request,
                base_preflight=base,
                host_baseline=host_baseline,
            )
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise _agent_mode_exit(
            "config_error",
            exc,
            exit_code=2,
            next_action=(
                "Fix the manifest or flag value named in the error, then re-run "
                "`agents-shipgate preflight`."
            ),
        ) from exc
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise _agent_mode_exit(
            "input_parse_error",
            exc,
            exit_code=3,
            next_action=(
                "Correct the diff, changed-files list, or request JSON named in "
                "the error, then re-run `agents-shipgate preflight`."
            ),
        ) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        raise _agent_mode_exit(
            "other_error",
            exc,
            exit_code=4,
            next_action="Resolve the error above, then re-run `agents-shipgate preflight`.",
        ) from exc
    except OSError as exc:
        typer.echo(f"Input error: {exc}", err=True)
        raise _agent_mode_exit(
            "input_parse_error",
            exc,
            exit_code=3,
            next_action=(
                "Make the input file readable at the path given, then re-run "
                "`agents-shipgate preflight`."
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - agents must never see a bare traceback.
        # Every other command reports unexpected failures on the agent channel;
        # preflight let them escape as a traceback, which an agent cannot route
        # on. Prose still goes to stderr and the exit code is unchanged — and
        # --verbose still gets the traceback, since swallowing it is exactly
        # what that flag exists to prevent.
        if verbose:
            logger.exception("unhandled exception")
        typer.echo(f"Agents Shipgate internal error: {exc}", err=True)
        raise _agent_mode_exit(
            "internal_error",
            exc,
            exit_code=4,
            next_action=(
                "Report this failure with the command line above; preflight "
                "could not complete."
            ),
        ) from exc

    payload = result.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"Agents Shipgate preflight: {result.control.state.replace('_', ' ')}")
    typer.echo(f"Protected surface touches: {len(result.protected_surface_touches)}")
    missing = [item for item in result.required_evidence if not item.satisfied]
    typer.echo(f"Missing required evidence: {len(missing)}")
    typer.echo(f"Signals: {len(result.signals)}")
    typer.echo(f"Requires verify: {str(result.requires_verify).lower()}")
    typer.echo(f"Next action: {result.first_next_action.why}")


def _read_changed_files(path: Path | None) -> list[str]:
    if path is None:
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_diff(path: Path) -> str:
    return sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")


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
    if str(path) == "-":
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        payload: Any = {} if not raw.strip() else _loads_json(raw, label="Preflight plan")
    else:
        payload = _read_json_file_or_stdin(path, label="Preflight plan")
    if not isinstance(payload, dict):
        raise InputParseError("Preflight plan JSON must be an object.")
    try:
        return PreflightPlanV1.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid preflight plan: {exc}") from exc


def _read_base_preflight(
    path: Path | None,
) -> PreflightResultV1 | PreflightResultV2 | PreflightResultV3 | None:
    if path is None:
        return None
    payload = _read_json_file_or_stdin(path, label="Base preflight")
    if not isinstance(payload, dict):
        raise InputParseError("Base preflight JSON must be an object.")
    try:
        version = payload.get("preflight_schema_version")
        if version == "0.3":
            return PreflightResultV3.model_validate(payload)
        if version == "0.2":
            return PreflightResultV2.model_validate(payload)
        return PreflightResultV1.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid base preflight result: {exc}") from exc


def _read_json_file_or_stdin(path: Path, *, label: str) -> Any:
    raw = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")
    return _loads_json(raw, label=label)


def _loads_json(raw: str, *, label: str) -> Any:
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
