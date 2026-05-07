"""``shipgate scenario suggest`` — derive adversarial scenarios from a static report.

Read-only with respect to user code. Reads an existing ``report.json``
and writes a deterministic YAML file that pairs each critical/high
missing-control finding with at least one concrete sandbox scenario.

Exit codes (per STABILITY.md):
- 0  — scenarios emitted successfully
- 2  — input report.json is malformed or unreadable
- 4  — internal error
- 20 — strict-mode coverage gate failure (uncovered critical/high findings)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from agents_shipgate.core.models import ReadinessReport
from agents_shipgate.report.scenario_export import (
    SEVERITY_RANK,
    coverage_gaps,
    derive_yaml_scenarios,
    dump_json,
    dump_yaml,
)

logger = logging.getLogger(__name__)

scenario_app = typer.Typer(
    help="Derive adversarial scenario suggestions from static findings.",
    no_args_is_help=True,
)


def _emit_input_error(kind: str, message: str, **fields: Any) -> None:
    """Emit a structured one-line JSON error on stderr when
    AGENTS_SHIPGATE_AGENT_MODE=1 is set, matching the convention used by
    other commands. Silent otherwise."""
    if os.environ.get("AGENTS_SHIPGATE_AGENT_MODE", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    payload: dict[str, Any] = {"error": kind, "message": message, **fields}
    print(json.dumps(payload, default=str), file=sys.stderr)


def _validate_min_severity(value: str) -> str:
    if value not in SEVERITY_RANK:
        raise typer.BadParameter(
            f"--min-severity must be one of {sorted(SEVERITY_RANK)}, got {value!r}"
        )
    return value


@scenario_app.command("suggest")
def suggest(
    from_path: Path = typer.Option(
        ...,
        "--from",
        help="Path to a scan report.json containing findings and misalignments.",
    ),
    out: Path = typer.Option(
        Path("agents-shipgate-reports/suggested-scenarios.yaml"),
        "--out",
        help="Where to write the YAML output. Ignored when --json is set.",
    ),
    min_severity: str = typer.Option(
        "high",
        "--min-severity",
        help="Drop findings below this severity. One of critical|high|medium|low|info.",
        callback=_validate_min_severity,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON envelope to stdout instead of writing YAML to disk.",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help=(
            "Fail with exit code 20 if any critical/high finding the predicate "
            "says should map to a scenario is uncovered. Coverage gaps are "
            "appended to the envelope as `coverage_gaps`."
        ),
    ),
) -> None:
    """Turn static findings into deterministic adversarial scenario suggestions."""
    try:
        raw = from_path.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Could not read --from path {from_path}: {exc}", err=True)
        _emit_input_error(
            "input_parse_error",
            message=str(exc),
            next_action="Run `agents-shipgate scan --json --out agents-shipgate-reports/report.json` first.",
        )
        raise typer.Exit(2) from exc

    try:
        report = ReadinessReport.model_validate_json(raw)
    except ValidationError as exc:
        typer.echo(f"Malformed report at {from_path}: {exc}", err=True)
        _emit_input_error(
            "input_parse_error",
            message=str(exc),
            next_action="Re-run `agents-shipgate scan` to regenerate report.json.",
        )
        raise typer.Exit(2) from exc

    scenarios = derive_yaml_scenarios(report, min_severity=min_severity)
    gaps: list[str] | None = None
    if strict:
        gaps = coverage_gaps(report, scenarios, min_severity=min_severity)

    if json_output:
        typer.echo(dump_json(scenarios, gaps=gaps))
    else:
        rendered = dump_yaml(scenarios, gaps=gaps)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            typer.echo(f"Could not write --out path {out}: {exc}", err=True)
            _emit_input_error("internal_error", message=str(exc))
            raise typer.Exit(4) from exc
        typer.echo(
            f"Wrote {len(scenarios)} scenario(s) to {out}"
            + (f"; {len(gaps)} coverage gap(s)" if gaps else "")
        )

    if strict and gaps:
        typer.echo(
            f"Strict-mode coverage failure: {len(gaps)} critical/high finding(s) uncovered.",
            err=True,
        )
        for ref in gaps:
            typer.echo(f"  - {ref}", err=True)
        _emit_input_error(
            "strict_gate_failure",
            message=f"{len(gaps)} uncovered finding(s)",
            uncovered=gaps,
        )
        raise typer.Exit(20)
