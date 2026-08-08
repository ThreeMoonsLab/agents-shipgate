from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.core.current_control import (
    CurrentControlUnavailable,
    read_current_control,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.schemas.contract import COMMANDS, DEFAULT_PATHS
from agents_shipgate.schemas.current_control import CURRENT_CONTROL_ARTIFACT_NAME
from agents_shipgate.schemas.diagnostics import NextAction

agent_app = typer.Typer(
    help="Agent-native projection commands.",
    no_args_is_help=True,
)


@agent_app.command("handoff")
def handoff(
    source: Path = typer.Option(
        Path("agents-shipgate-reports/verifier.json"),
        "--from",
        help="Path to verifier.json.",
    ),
    report: Path | None = typer.Option(
        None,
        "--report",
        help=("Optional report.json path. Defaults to the sibling report.json when present."),
    ),
    verify_run: Path | None = typer.Option(
        None,
        "--verify-run",
        help=(
            "Optional verify-run.json path. Defaults to the sibling verify-run.json when present."
        ),
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Optional output path for agent-handoff.json.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the handoff JSON to stdout.",
    ),
) -> None:
    """Render the shipgate.agent_handoff/v6 artifact from verifier outputs."""

    try:
        verifier_payload = _load_required_json(source, "verifier.json")
        report_payload = _load_optional_json(
            explicit=report,
            fallback=source.parent / "report.json",
            label="report.json",
        )
        verify_run_payload = _load_optional_json(
            explicit=verify_run,
            fallback=source.parent / "verify-run.json",
            label="verify-run.json",
        )
        payload = build_agent_handoff(
            verifier=verifier_payload,
            report=report_payload,
            verify_run=verify_run_payload,
        )
    except (InputParseError, ValueError) as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise typer.Exit(3) from exc
    except Exception as exc:  # noqa: BLE001 - CLI boundary.
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        raise typer.Exit(4) from exc

    rendered = json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    if json_output or out is None:
        typer.echo(rendered.rstrip())
    else:
        typer.echo(f"Wrote agent handoff to {out}")


@agent_app.command("control")
def control(
    reports_dir: Path = typer.Option(
        Path(DEFAULT_PATHS["reports_dir"]),
        "--reports-dir",
        help="Directory holding current-control.json.",
    ),
) -> None:
    """Read the current control identity using the generation-safe protocol.

    This is the one refresh entry point.  A zero exit means the printed pointer
    was validated against every artifact it binds and did not move while it was
    read.  A non-zero exit means no control identity is current here: the caller
    holds no authority and must not fall back on a control state it cached
    earlier in the conversation.
    """

    try:
        result = read_current_control(reports_dir)
    except CurrentControlUnavailable as exc:
        guidance = (
            "Re-run `agents-shipgate verify` and read "
            f"{reports_dir / CURRENT_CONTROL_ARTIFACT_NAME} again. Until it "
            "reads cleanly, treat completion, merge, and any cached must_stop "
            "as unavailable rather than acting on a remembered result."
        )
        typer.echo(f"Current control is unavailable ({exc.reason}): {exc}", err=True)
        emit_agent_mode_error(
            "input_parse_error" if exc.reason != "generation_changed" else "other_error",
            message=str(exc),
            exit_code=3 if exc.reason != "generation_changed" else 4,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="command",
                    command=COMMANDS["verify_pr"],
                    why=guidance,
                    expects=(
                        "current-control.json is present, valid, and every "
                        "artifact it binds matches its recorded hash."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(3 if exc.reason != "generation_changed" else 4) from exc

    typer.echo(
        json.dumps(result.pointer.model_dump(mode="json"), indent=2, sort_keys=True)
    )


def _load_required_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputParseError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputParseError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise InputParseError(f"{label} must contain an object: {path}")
    return payload


def _load_optional_json(
    *,
    explicit: Path | None,
    fallback: Path,
    label: str,
) -> dict[str, Any] | None:
    path = explicit or fallback
    if explicit is None and not path.is_file():
        return None
    return _load_required_json(path, label)


__all__ = ["agent_app", "control", "handoff"]
