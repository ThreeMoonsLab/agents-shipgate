"""``shipgate audit --host`` CLI wrapper."""

from __future__ import annotations

import json
from pathlib import Path

import typer

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
    load_host_grants_baseline,
    normalized_host_grants,
    redacted_config_sha256,
    render_host_audit_markdown,
    render_host_drift_markdown,
)


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
        typer.echo(
            "Nothing to audit: pass --host for the host-capability inventory.",
            err=True,
        )
        raise typer.Exit(2)
    if save_baseline and drift:
        typer.echo(
            "--save-baseline and --drift are mutually exclusive: record the "
            "acknowledged state or compare against it, not both.",
            err=True,
        )
        raise typer.Exit(2)
    if fail_on_drift and not drift:
        typer.echo("--fail-on-drift requires --drift.", err=True)
        raise typer.Exit(2)

    inventory = host_audit_inventory(workspace)
    resolved_baseline = (
        baseline_file
        if baseline_file.is_absolute()
        else workspace.resolve() / baseline_file
    )

    if save_baseline:
        payload = build_host_grants_baseline(inventory)
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if resolved_baseline.is_file() and resolved_baseline.read_text(
            encoding="utf-8"
        ) == text:
            status = "unchanged"
        else:
            status = "updated" if resolved_baseline.is_file() else "created"
            resolved_baseline.parent.mkdir(parents=True, exist_ok=True)
            resolved_baseline.write_text(text, encoding="utf-8")
        outcome = {
            "baseline_file": str(baseline_file),
            "inventory_sha256": payload["inventory_sha256"],
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
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
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
        if fail_on_drift and payload["has_drift"]:
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
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    "load_host_grants_baseline",
    "normalized_host_grants",
    "redacted_config_sha256",
    "render_host_audit_markdown",
    "render_host_drift_markdown",
]
