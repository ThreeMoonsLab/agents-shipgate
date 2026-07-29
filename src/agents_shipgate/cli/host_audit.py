"""``shipgate audit --host`` CLI wrapper."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error_action
from agents_shipgate.core.host_grants import (
    DEFAULT_BASELINE_FILE,
    HOST_GRANTS_INVENTORY_SCHEMA_VERSION,
    HOST_GRANTS_SCHEMA_VERSION,
    build_host_drift_payload,
    build_host_grants_baseline,
    diff_host_grants,
    host_audit_inventory,
    host_grant_expansion_signals,
    host_grants_sha256,
    inventory_is_complete,
    load_host_grants_baseline,
    normalized_host_grants,
    redacted_config_sha256,
    render_host_audit_markdown,
    render_host_drift_markdown,
)
from agents_shipgate.schemas.diagnostics import NextAction


def _io_error(message: str, *, next_action: str) -> typer.Exit:
    """Report a filesystem failure on both channels.

    ``docs/errors.json`` gives filesystem and unexpected failures
    ``other_error`` and exit 4; reporting them as ``config_error``/2 tells an
    agent to go re-read its flags when the flags were fine and the path was
    not writable.
    """

    typer.echo(message, err=True)
    emit_agent_mode_error_action(
        "other_error",
        message=message,
        exit_code=4,
        action=NextAction(kind="review", why=next_action),
    )
    return typer.Exit(4)


def _config_error(
    message: str,
    *,
    next_action: str,
    command: str | None = "agents-shipgate audit --host",
) -> typer.Exit:
    """Report flag misuse on both channels and return the exit to raise.

    Agent-facing docs promise that with ``AGENTS_SHIPGATE_AGENT_MODE=1`` a
    failing command emits a structured error line on stderr, carrying both the
    legacy ``next_action`` string and the ranked ``next_actions`` array.
    ``audit`` printed prose only, so an agent that mis-invoked it had to parse
    English or guess.
    """

    typer.echo(message, err=True)
    action = (
        NextAction(
            kind="command",
            command=command,
            why=next_action,
            expects="A host-capability audit that completes.",
        )
        if command
        else NextAction(kind="review", why=next_action)
    )
    emit_agent_mode_error_action(
        "config_error",
        message=message,
        exit_code=2,
        action=action,
    )
    return typer.Exit(2)


def audit(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace to inventory.",
    ),
    host: bool = typer.Option(
        False,
        "--host",
        help="Inventory coding-agent host grants (MCP servers, permission rules, hooks, workflow scopes).",
    ),
    scope: str = typer.Option(
        "repository",
        "--scope",
        help=(
            "Static inventory scope: repository (default, portable) or "
            "local-static (also reads supported on-disk user/managed config)."
        ),
    ),
    save_baseline: bool = typer.Option(
        False,
        "--save-baseline",
        help=(
            "Record the current host-grant inventory as the acknowledged "
            "baseline (writes the --baseline-file)."
        ),
    ),
    drift: bool = typer.Option(
        False,
        "--drift",
        help="Diff the current host grants against the saved baseline and report drift.",
    ),
    baseline_file: Path = typer.Option(
        DEFAULT_BASELINE_FILE,
        "--baseline-file",
        help="Host-grants baseline location (committed; default .agents-shipgate/host-grants.json).",
    ),
    fail_on_drift: bool = typer.Option(
        False,
        "--fail-on-drift",
        help="With --drift: exit 20 when any drift is found (for scheduled CI gates).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON instead of Markdown.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Write the JSON payload to this path in addition to normal output.",
    ),
) -> None:
    """Zero-config, read-only audits. Currently supports --host."""

    if not host:
        raise _config_error(
            "Nothing to audit: pass --host for the host-capability inventory.",
            next_action="Re-run as `agents-shipgate audit --host`.",
        )
    if scope not in {"repository", "local-static"}:
        raise _config_error(
            "--scope must be 'repository' or 'local-static'.",
            next_action="Re-run audit with --scope repository or --scope local-static.",
        )
    if save_baseline and drift:
        raise _config_error(
            "--save-baseline and --drift are mutually exclusive: record the "
            "acknowledged state or compare against it, not both.",
            next_action="Re-run audit with either --save-baseline or --drift, not both.",
        )
    if fail_on_drift and not drift:
        raise _config_error(
            "--fail-on-drift requires --drift.",
            next_action="Re-run audit with --drift, or drop --fail-on-drift.",
        )

    inventory_scope = "local_static" if scope == "local-static" else "repository"
    inventory = host_audit_inventory(workspace, scope=inventory_scope)
    resolved_baseline = (
        baseline_file
        if baseline_file.is_absolute()
        else workspace.resolve() / baseline_file
    )

    if save_baseline:
        try:
            payload = build_host_grants_baseline(inventory)
        except ValueError as exc:
            raise _config_error(
                str(exc),
                next_action=(
                    "Resolve the incomplete host inventory, then re-run "
                    "`agents-shipgate audit --host --save-baseline`."
                ),
            ) from exc
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        try:
            if resolved_baseline.is_file() and resolved_baseline.read_text(
                encoding="utf-8"
            ) == text:
                status = "unchanged"
            else:
                status = "updated" if resolved_baseline.is_file() else "created"
                resolved_baseline.parent.mkdir(parents=True, exist_ok=True)
                resolved_baseline.write_text(text, encoding="utf-8")
        except OSError as exc:
            # --baseline-file naming a directory raised IsADirectoryError
            # straight through typer: a traceback and exit 1.
            raise _io_error(
                f"Could not write the host-grants baseline {baseline_file}: {exc}",
                next_action=(
                    "Point --baseline-file at a writable file path, then re-run "
                    "the audit."
                ),
            ) from exc
        outcome = {
            "baseline_file": str(baseline_file),
            "inventory_sha256": payload["inventory_sha256"],
            "scope": payload["scope"],
            "status": status,
        }
        _write_json_out(out, outcome)
        if json_output:
            typer.echo(json.dumps(outcome, indent=2, sort_keys=True))
        else:
            typer.echo(
                f"Host-grants baseline {status}: {baseline_file} "
                f"(sha256 {payload['inventory_sha256'][:12]}…). Commit it; "
                "verify treats .agents-shipgate/ edits as trust-root changes."
            )
        return

    if drift:
        try:
            baseline = load_host_grants_baseline(resolved_baseline)
        except ValueError as exc:
            # The loader converts every read OSError into ValueError, so the
            # I/O cause has to be recovered from __cause__ — otherwise a
            # directory passed as --baseline-file is reported as flag misuse
            # and the recovery drops the drift request entirely.
            # A *missing* baseline is the documented "record one first" case
            # and keeps its config_error/2 contract; only a path that exists
            # and cannot be read is a filesystem failure.
            if isinstance(exc.__cause__, OSError) and not isinstance(
                exc.__cause__, FileNotFoundError
            ):
                raise _io_error(
                    f"Could not read the host-grants baseline {baseline_file}: "
                    f"{exc.__cause__}",
                    next_action=(
                        "Point --baseline-file at a readable baseline file, "
                        "then re-run the audit."
                    ),
                ) from exc
            # A baseline that exists but does not load — malformed, unknown
            # schema, integrity-failed — must never be recovered by writing
            # over it. Recommending --save-baseline there replaced the failed
            # artifact with the *current* grants, silently acknowledging them
            # and destroying the evidence a human needed to look at. Only a
            # genuinely absent baseline can be recorded without losing
            # anything, and that command carries the scope it was asked for.
            missing = isinstance(exc.__cause__, FileNotFoundError)
            raise _config_error(
                str(exc),
                next_action=(
                    "Record a baseline with `agents-shipgate audit --host "
                    "--save-baseline`."
                    if missing
                    else "Inspect the existing baseline file and repair or "
                    "replace it deliberately; do not overwrite it with the "
                    "current grants, which would acknowledge them unreviewed."
                ),
                command=(
                    "agents-shipgate audit --host --workspace "
                    f"{shlex.quote(str(workspace))} --scope {shlex.quote(scope)} "
                    f"--save-baseline --baseline-file "
                    f"{shlex.quote(str(baseline_file))}"
                    if missing
                    else None
                ),
            ) from exc
        payload = build_host_drift_payload(
            baseline=baseline,
            inventory=inventory,
            baseline_file=str(baseline_file),
        )
        if json_output:
            _write_json_out(out, payload)
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            _write_json_out(out, payload)
            typer.echo(render_host_drift_markdown(payload), nl=False)
        if fail_on_drift and (
            payload["comparison_status"] != "comparable" or payload["has_drift"]
        ):
            raise typer.Exit(20)
        return

    if json_output:
        _write_json_out(out, inventory)
        typer.echo(json.dumps(inventory, indent=2, sort_keys=True))
        return
    _write_json_out(out, inventory)
    typer.echo(render_host_audit_markdown(inventory), nl=False)


def _write_json_out(out: Path | None, payload: dict) -> None:
    if out is None:
        return
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        # An unwritable --out reached the user as a Rich traceback and exit 1,
        # which is neither the documented exit code nor something an agent can
        # route on.
        raise _io_error(
            f"Could not write --out {out}: {exc}",
            next_action=(
                "Point --out at a writable file path, then re-run the audit."
            ),
        ) from exc


__all__ = [
    "DEFAULT_BASELINE_FILE",
    "HOST_GRANTS_INVENTORY_SCHEMA_VERSION",
    "HOST_GRANTS_SCHEMA_VERSION",
    "audit",
    "build_host_drift_payload",
    "build_host_grants_baseline",
    "diff_host_grants",
    "host_audit_inventory",
    "host_grant_expansion_signals",
    "host_grants_sha256",
    "inventory_is_complete",
    "load_host_grants_baseline",
    "normalized_host_grants",
    "redacted_config_sha256",
    "render_host_audit_markdown",
    "render_host_drift_markdown",
]
