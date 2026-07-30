from __future__ import annotations

import json
import logging
import os
import shlex
import stat
import sys
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.core.bounded_io import (
    MAX_EXPLICIT_DIFF_BYTES,
    MAX_EXPLICIT_JSON_BYTES,
    MAX_EXPLICIT_PATH_LIST_BYTES,
    read_bounded_utf8_file,
    read_bounded_utf8_stdin,
)
from agents_shipgate.core.codex_boundary import parse_unified_diff
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging
from agents_shipgate.core.preflight import build_preflight_result
from agents_shipgate.core.trust_roots import inspect_lexical_path_identity
from agents_shipgate.schemas.diagnostics import NextAction
from agents_shipgate.schemas.preflight import (
    CapabilityRequestV1,
    PreflightPlanV1,
    PreflightResultV1,
    PreflightResultV2,
    PreflightResultV3,
)

logger = logging.getLogger(__name__)


def _rerun_command(**flags: object) -> str | None:
    """The failed invocation, spelled out so rerunning it means the same thing.

    Every error used to recommend a bare ``agents-shipgate preflight --json``,
    which discards the workspace, config, plan, diff and capability request. An
    agent that followed it evaluated the *current* repository with an empty
    plan and got ``control.state=complete`` — a clean answer to a question
    nobody asked. When the request cannot be reproduced from stdin ("-"), no
    command is offered at all: a review action is honest, a wrong command is
    not.
    """

    cwd_relative_paths = {
        "workspace",
        "changed_files",
        "diff",
        "capability_request",
        "plan",
        "base_preflight",
    }
    stdin_paths = {"diff", "capability_request", "plan", "base_preflight"}
    parts = ["agents-shipgate", "preflight"]
    for name, value in flags.items():
        if value is None or value is False:
            continue
        if value is True:
            parts.append(f"--{name.replace('_', '-')}")
            continue
        text = str(value)
        if text == "-" and name in stdin_paths:
            return None
        if name in cwd_relative_paths:
            path = Path(text)
            text = str(path if path.is_absolute() else Path.cwd() / path)
        parts.extend([f"--{name.replace('_', '-')}", shlex.quote(text)])
    return " ".join(parts)


def _agent_mode_exit(
    error_kind: str,
    exc: BaseException,
    *,
    exit_code: int,
    next_action: str,
    command: str | None,
    repair_path: Path | None = None,
) -> typer.Exit:
    """Emit ranked recovery actions and return the exit to raise.

    ``error_kind`` must be one of the ids published in ``docs/errors.json``;
    an id an agent cannot look up is no better than prose.

    A malformed input cannot be repaired by immediately replaying the failed
    command. When the failing file is known, editing it is rank one and an
    exact rerun may follow as rank two. Stdin and request-shape conflicts have
    no editable file or valid replay, so they remain review-only.
    """

    actions: list[NextAction] = []
    if repair_path is not None:
        actions.append(
            NextAction(
                kind="edit",
                path=str(repair_path),
                why=next_action,
                expects="The named preflight input is valid and readable.",
            )
        )
    elif command is None:
        actions.append(NextAction(kind="review", why=next_action))
    if command is not None and repair_path is not None:
        actions.append(
            NextAction(
                kind="command",
                command=command,
                why="Re-run the exact preflight request after repairing its input.",
                expects="A preflight run of the same request that completes.",
            )
        )
    if not actions:
        actions.append(NextAction(kind="review", why=next_action))

    emit_agent_mode_error(
        error_kind,
        message=str(exc),
        exit_code=exit_code,
        next_action=actions[0].to_legacy_string(),
        next_actions=[action.model_dump(mode="json") for action in actions],
    )
    return typer.Exit(exit_code)


def _safe_repair_file(
    workspace: Path,
    path: Path | None,
    *,
    relative_to_workspace: bool,
) -> Path | None:
    """Return one exact, singly-linked editable file inside the workspace."""

    if path is None or str(path) == "-":
        return None
    root = workspace.resolve()
    anchor = root if relative_to_workspace else Path.cwd()
    candidate = path if path.is_absolute() else anchor / path
    if ".." in candidate.parts:
        return None
    lexical = Path(os.path.normpath(os.fspath(candidate)))
    try:
        relative = lexical.relative_to(root)
    except ValueError:
        return None
    if inspect_lexical_path_identity(root, relative) is not None:
        return None
    try:
        metadata = lexical.lstat()
    except OSError:
        return None
    # A hard link inside the workspace can name an inode whose other name is
    # outside it. Granting an edit action would then authorize an external
    # mutation despite the lexical containment check.
    return (
        lexical
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
        else None
    )


def _safe_manifest_repair_path(workspace: Path, config: Path) -> Path | None:
    """Return an editable manifest only when it is one exact in-workspace file."""

    return _safe_repair_file(
        workspace,
        config,
        relative_to_workspace=True,
    )


def _repair_path_for_parse_error(
    exc: BaseException,
    *,
    workspace: Path,
    config: Path,
    changed_files: Path | None,
    diff: Path | None,
    capability_request: Path | None,
    plan: Path | None,
    base_preflight: Path | None,
    host_baseline: Path | None,
) -> Path | None:
    """Identify the malformed file without changing CLI path semantics."""

    message = str(exc).lower()
    if "preflight plan" in message:
        return _safe_repair_file(workspace, plan, relative_to_workspace=False)
    if "capability request" in message:
        return _safe_repair_file(
            workspace,
            capability_request,
            relative_to_workspace=False,
        )
    if "base preflight" in message:
        return _safe_repair_file(
            workspace,
            base_preflight,
            relative_to_workspace=False,
        )
    if "host-grant" in message or "host grant" in message:
        # A host-grants baseline is human-owned trust evidence. Editing or
        # replacing it acknowledges the current host authority, so parse and
        # integrity failures must remain review-only even when the file itself
        # would otherwise be a mechanically safe edit target.
        return None
    if "changed-files" in message or "changed files" in message:
        return _safe_repair_file(
            workspace,
            changed_files,
            relative_to_workspace=False,
        )
    if "unified diff" in message or "diff input" in message:
        return _safe_repair_file(workspace, diff, relative_to_workspace=False)
    if any(
        marker in message
        for marker in (
            "manifest",
            "invalid yaml",
            "config file must contain",
            "check_severity_overrides was removed",
        )
    ):
        return _safe_manifest_repair_path(workspace, config)
    if isinstance(exc, OSError) and exc.filename:
        return _safe_repair_file(
            workspace,
            Path(exc.filename),
            relative_to_workspace=False,
        )
    return None


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

    # A plan combined with the per-flag inputs is a request-shape conflict:
    # replaying it verbatim can never satisfy its own ``expects``. Offer a
    # review action instead of a command that reproduces the mistake.
    conflicting_request = plan is not None and any(
        value is not None
        for value in (changed_files, diff, capability_request, base_preflight)
    )
    rerun = None if conflicting_request else _rerun_command(
        workspace=workspace,
        config=config,
        changed_files=changed_files,
        diff=diff,
        capability_request=capability_request,
        plan=plan,
        base_preflight=base_preflight,
        host_baseline=host_baseline,
        json=json_output,
        verbose=verbose,
    )

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
                "the same preflight request."
            ),
            command=rerun,
            repair_path=(
                None
                if conflicting_request or rerun is None
                else _repair_path_for_parse_error(
                    exc,
                    workspace=workspace,
                    config=config,
                    changed_files=changed_files,
                    diff=diff,
                    capability_request=capability_request,
                    plan=plan,
                    base_preflight=base_preflight,
                    host_baseline=host_baseline,
                )
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
                "the error, then re-run the same preflight request."
            ),
            command=rerun,
            repair_path=(
                None
                if conflicting_request or rerun is None
                else _repair_path_for_parse_error(
                    exc,
                    workspace=workspace,
                    config=config,
                    changed_files=changed_files,
                    diff=diff,
                    capability_request=capability_request,
                    plan=plan,
                    base_preflight=base_preflight,
                    host_baseline=host_baseline,
                )
            ),
        ) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        raise _agent_mode_exit(
            "other_error",
            exc,
            exit_code=4,
            next_action="Resolve the error above, then re-run the same preflight request.",
            command=rerun,
        ) from exc
    except OSError as exc:
        typer.echo(f"Input error: {exc}", err=True)
        raise _agent_mode_exit(
            "input_parse_error",
            exc,
            exit_code=3,
            next_action=(
                "Make the input file readable at the path given, then re-run "
                "the same preflight request."
            ),
            command=rerun,
            repair_path=(
                None
                if rerun is None
                else _repair_path_for_parse_error(
                    exc,
                    workspace=workspace,
                    config=config,
                    changed_files=changed_files,
                    diff=diff,
                    capability_request=capability_request,
                    plan=plan,
                    base_preflight=base_preflight,
                    host_baseline=host_baseline,
                )
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
            command=None,
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
    # LF is the format delimiter. Spaces and Unicode separators are legal Git
    # filename content and must not be normalized into another repository path.
    lines = read_bounded_utf8_file(
        path,
        max_bytes=MAX_EXPLICIT_PATH_LIST_BYTES,
        label="Changed-files input",
    ).split("\n")
    return [
        line[:-1] if line.endswith("\r") else line
        for line in lines
        if line not in {"", "\r"}
    ]


def _read_diff(path: Path) -> str:
    if str(path) == "-":
        return read_bounded_utf8_stdin(
            max_bytes=MAX_EXPLICIT_DIFF_BYTES,
            label="Diff input",
        )
    return read_bounded_utf8_file(
        path,
        max_bytes=MAX_EXPLICIT_DIFF_BYTES,
        label="Diff input",
    )


def _read_capability_request(path: Path | None) -> CapabilityRequestV1 | None:
    if path is None:
        return None
    source_label = _json_source_label(path, label="Capability request")
    payload = _read_json_file_or_stdin(path, label="Capability request")
    if not isinstance(payload, dict):
        raise InputParseError(f"{source_label} JSON must be an object.")
    try:
        return CapabilityRequestV1.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid {source_label}: {exc}") from exc


def _read_plan(path: Path) -> PreflightPlanV1:
    source_label = _json_source_label(path, label="Preflight plan")
    if str(path) == "-":
        raw = (
            ""
            if sys.stdin.isatty()
            else read_bounded_utf8_stdin(
                max_bytes=MAX_EXPLICIT_JSON_BYTES,
                label="Preflight plan",
            )
        )
        payload: Any = {} if not raw.strip() else _loads_json(raw, label="Preflight plan")
    else:
        payload = _read_json_file_or_stdin(path, label="Preflight plan")
    if not isinstance(payload, dict):
        raise InputParseError(f"{source_label} JSON must be an object.")
    try:
        return PreflightPlanV1.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid {source_label}: {exc}") from exc


def _read_base_preflight(
    path: Path | None,
) -> PreflightResultV1 | PreflightResultV2 | PreflightResultV3 | None:
    if path is None:
        return None
    source_label = _json_source_label(path, label="Base preflight")
    payload = _read_json_file_or_stdin(path, label="Base preflight")
    if not isinstance(payload, dict):
        raise InputParseError(f"{source_label} JSON must be an object.")
    try:
        version = payload.get("preflight_schema_version")
        if version == "0.3":
            return PreflightResultV3.model_validate(payload)
        if version == "0.2":
            return PreflightResultV2.model_validate(payload)
        return PreflightResultV1.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid {source_label}: {exc}") from exc


def _read_json_file_or_stdin(path: Path, *, label: str) -> Any:
    raw = (
        read_bounded_utf8_stdin(
            max_bytes=MAX_EXPLICIT_JSON_BYTES,
            label=label,
        )
        if str(path) == "-"
        else read_bounded_utf8_file(
            path,
            max_bytes=MAX_EXPLICIT_JSON_BYTES,
            label=label,
        )
    )
    source_label = _json_source_label(path, label=label)
    return _loads_json(raw, label=source_label)


def _json_source_label(path: Path, *, label: str) -> str:
    return label if str(path) == "-" else f"{label} file {path}"


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
