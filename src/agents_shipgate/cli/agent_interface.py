from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.cli.current_workspace import live_workspace
from agents_shipgate.core.agent_control_envelope import (
    AgentControlRouteUnavailable,
    envelope_from_pointer,
    render_agent_control_envelope,
)
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.core.current_control import (
    CurrentControlRead,
    CurrentControlUnavailable,
    read_current_control,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.schemas.contract import COMMANDS, DEFAULT_PATHS
from agents_shipgate.schemas.current_control import (
    CURRENT_CONTROL_ARTIFACT_NAME,
    VERIFIER_ARTIFACT_KEY,
)
from agents_shipgate.schemas.diagnostics import NextAction
from agents_shipgate.schemas.verifier import VerifierArtifact

agent_app = typer.Typer(
    help="Agent-native projection commands.",
    no_args_is_help=True,
)

# Refusal reason -> (agent-mode error kind, exit code). Reasons about the
# artifact set itself map to the missing/parse family; reasons about currency
# map to "other". Anything unlisted falls through to the conservative 4.
_UNAVAILABLE_EXIT: dict[str, tuple[str, int]] = {
    "missing": ("input_parse_error", 3),
    "unreadable": ("input_parse_error", 3),
    "invalid_schema": ("input_parse_error", 3),
    "unsafe_pointer": ("input_parse_error", 3),
    "artifact_unreadable": ("input_parse_error", 3),
    "artifact_mismatch": ("input_parse_error", 3),
    "generation_changed": ("other_error", 4),
    "workspace_changed": ("other_error", 4),
    "workspace_unverified": ("other_error", 4),
    "workspace_unverifiable": ("other_error", 4),
    "receipt_mismatch": ("other_error", 4),
}


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
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help=(
            "Repository the pointer must still describe. Drift in HEAD, the "
            "tree, or the worktree overlay refuses the read."
        ),
    ),
    reports_dir: Path = typer.Option(
        Path(DEFAULT_PATHS["reports_dir"]),
        "--reports-dir",
        help="Directory holding current-control.json.",
    ),
    format_: str = typer.Option(
        "control",
        "--format",
        help=(
            "control (default): the compact shipgate.agent_control/v1 envelope "
            "— state, permissions, next action, and hashed artifact paths in "
            "one object. pointer: the raw shipgate.current_control/v1 pointer."
        ),
    ),
) -> None:
    """Read the current control identity using the generation-safe protocol.

    This is the one refresh entry point.  A zero exit means the printed answer
    was validated against every artifact it binds, still describes ``--workspace``
    as it stands right now, and did not move while it was read.  A non-zero exit
    means no control identity is current here: the caller holds no authority and
    must not fall back on a control state it cached earlier in the conversation.

    The default output is the compact envelope rather than the pointer itself.
    The pointer deliberately records no route — reproducing one there would make
    it a second decision — so a caller reading it still had to open the handoff
    to learn what to do next.  The envelope answers both questions in one read,
    by joining the pointer's currency guarantee to the route the bound verifier
    already published.  ``--format pointer`` returns the underlying artifact
    unchanged.
    """

    if format_ not in {"control", "pointer"}:
        guidance = "Re-run with --format control or --format pointer."
        typer.echo(f"Config error: --format must be control or pointer, not {format_!r}", err=True)
        emit_agent_mode_error(
            "config_error",
            message=f"--format must be control or pointer, not {format_!r}",
            exit_code=2,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="review", why=guidance, expects="A supported --format value."
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(2)

    try:
        result = read_current_control(
            reports_dir,
            live=live_workspace(workspace, reports_dir),
            # Captured inside the protocol, not reopened after it: the route
            # must come from the same generation whose identity was confirmed.
            capture=(VERIFIER_ARTIFACT_KEY,),
        )
    except CurrentControlUnavailable as exc:
        guidance = (
            "Re-run `agents-shipgate verify` and read "
            f"{reports_dir / CURRENT_CONTROL_ARTIFACT_NAME} again. Until it "
            "reads cleanly, treat completion, merge, and any cached must_stop "
            "as unavailable rather than acting on a remembered result."
        )
        # Two different failures, two different exit codes: the artifact set is
        # unreadable or inconsistent (3, the missing/parse family), or it reads
        # fine but no longer describes anything current (4). Both deny
        # authority; the split tells a caller whether to repair a directory or
        # simply re-verify.
        kind, exit_code = _UNAVAILABLE_EXIT.get(exc.reason, ("other_error", 4))
        typer.echo(f"Current control is unavailable ({exc.reason}): {exc}", err=True)
        emit_agent_mode_error(
            kind,
            message=str(exc),
            exit_code=exit_code,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="command",
                    command=COMMANDS["verify_pr"],
                    why=guidance,
                    expects=(
                        "current-control.json is present, valid, every artifact "
                        "it binds matches its recorded hash, and it still "
                        "describes this workspace."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(exit_code) from exc

    if format_ == "pointer":
        typer.echo(json.dumps(result.pointer.model_dump(mode="json"), indent=2, sort_keys=True))
        return

    try:
        bound_verifier = _bound_verifier(result)
        envelope = envelope_from_pointer(
            result.pointer,
            verifier=bound_verifier,
            # The exit code the producing run recorded, not this reader's.
            exit_code=None if bound_verifier is None else bound_verifier.head_exit_code,
            artifact_root=reports_dir.as_posix(),
        )
    except AgentControlRouteUnavailable as exc:
        guidance = (
            "Run `agents-shipgate verify` in this workspace. The pointer that "
            "is current was published by a command that reaches no release "
            "decision, so there is no route to return; inventing one would be "
            "a route that does not reproduce this subject."
        )
        typer.echo(f"Current control carries no route: {exc}", err=True)
        emit_agent_mode_error(
            "other_error",
            message=str(exc),
            exit_code=4,
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="command",
                    command=COMMANDS["verify_pr"],
                    why=guidance,
                    expects=(
                        "A verify run publishes a pointer that binds verifier.json, "
                        "which carries the exact next action."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(4) from exc

    typer.echo(render_agent_control_envelope(envelope))


def _bound_verifier(result: CurrentControlRead) -> VerifierArtifact | None:
    """Parse the verifier bytes the generation-safe read already validated.

    The bytes come from :class:`CurrentControlRead`, hashed against the pointer
    inside the same pass that confirmed the pointer had not moved. Reopening the
    file here instead would be a second, unsynchronized read: a run republishing
    between the two would let this pointer's identity be reported beside a
    different generation's decision and permissions.

    A parse failure is therefore a malformed artifact rather than a stale one,
    and resolves to "no route" rather than a crash.
    """

    data = result.artifacts.get(VERIFIER_ARTIFACT_KEY)
    if data is None:
        return None
    try:
        verifier = VerifierArtifact.model_validate_json(data)
    except ValueError:
        return None
    # The state tag alone is not identity. A verifier that closes a different
    # request than the pointer decided cannot supply this pointer's route, and
    # the difference is invisible in `control.state`.
    if (verifier.request_id, verifier.decision_id) != (result.pointer.request_id, result.pointer.decision_id):
        raise AgentControlRouteUnavailable(
            "The bound verifier reports a different request than the current "
            "control pointer decided, so no route for this generation could be "
            "recovered."
        )
    return verifier


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
