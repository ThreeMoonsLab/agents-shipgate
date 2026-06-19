from __future__ import annotations

import json
from pathlib import Path

import typer

from agents_shipgate.checks.baseline_integrity import has_hash_mismatch
from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.cli.scan.path_helpers import _resolve_audit_log_path
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.baseline import verify_baseline, write_baseline
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging

# Exit code 6: baseline integrity failure. Reserved by M2; documented
# in `.well-known/agents-shipgate.json` and STABILITY.md.
BASELINE_INTEGRITY_EXIT_CODE = 6


def register(app: typer.Typer) -> None:
    baseline_app = typer.Typer(help="Manage local finding baselines.")

    @baseline_app.command("save")
    def baseline_save(
        config: Path = typer.Option(
            Path("shipgate.yaml"),
            "--config",
            "-c",
            help="Manifest path used to create the baseline.",
        ),
        out: Path = typer.Option(
            Path(".agents-shipgate/baseline.json"),
            "--out",
            help="Baseline JSON path to write.",
        ),
        verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs."),
    ) -> None:
        """Save active unsuppressed findings as the current accepted baseline."""
        try:
            configure_logging(verbose=verbose)
            report, _ = run_scan(
                config_path=config,
                formats=["json"],
                ci_mode="advisory",
                verbose=verbose,
            )
            manifest = load_manifest(config)
            audit_log = _resolve_audit_log_path(manifest, out)
            baseline = write_baseline(report, out, audit_log_path=audit_log)
        except ConfigError as exc:
            typer.echo(f"Config error: {exc}", err=True)
            raise typer.Exit(2) from exc
        except InputParseError as exc:
            typer.echo(f"Input parsing error: {exc}", err=True)
            emit_agent_mode_error(
                "input_parse_error",
                message=str(exc),
                exit_code=3,
                command="agents-shipgate baseline verify",
                next_action="check the --baseline and --audit-log paths",
                next_actions=[
                    {
                        "kind": "review",
                        "path": str(baseline),
                        "why": "Baseline file could not be loaded or parsed.",
                        "expects": "baseline JSON",
                    }
                ],
            )
            raise typer.Exit(3) from exc
        except AgentsShipgateError as exc:
            typer.echo(f"Agents Shipgate error: {exc}", err=True)
            emit_agent_mode_error(
                "other_error",
                message=str(exc),
                exit_code=4,
                command="agents-shipgate baseline verify",
                next_action="rerun baseline verify with --verbose",
                next_actions=[
                    {
                        "kind": "command",
                        "command": "agents-shipgate baseline verify --verbose --json",
                        "why": "Surface the baseline verification failure.",
                        "expects": "debug logging",
                    }
                ],
            )
            raise typer.Exit(4) from exc
        typer.echo(f"Wrote {out}")
        typer.echo(f"Findings saved: {len(baseline.findings)}")
        typer.echo(f"Audit log: {audit_log}")

    @baseline_app.command("verify")
    def baseline_verify(
        baseline: Path = typer.Option(
            Path(".agents-shipgate/baseline.json"),
            "--baseline",
            help="Baseline JSON path to verify.",
        ),
        audit_log: Path | None = typer.Option(
            None,
            "--audit-log",
            help=(
                "Audit log path. Defaults to "
                "<baseline-dir>/baseline-audit.log, matching `baseline save`."
            ),
        ),
        strict: bool = typer.Option(
            False,
            "--strict",
            help=(
                "Exit with code 6 if SHIP-BASELINE-INTEGRITY-MISMATCH is "
                "detected. Without --strict the command still reports issues "
                "but exits 0 unless an underlying input error occurs."
            ),
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Emit issues as JSON on stdout instead of human text.",
        ),
        verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs."),
    ) -> None:
        """Verify baseline file integrity against the audit log.

        Detects hand-edits (hash mismatch), entries without audit
        provenance, legacy v0.2-0.4 entries lacking provenance,
        expired review windows, and deprecated check IDs. The
        scan-aware "resolved-but-not-pruned" check is not part of
        `verify` (it requires a scan) — use `agents-shipgate scan
        --baseline X` for the complete picture.

        Exit codes:
          0 - clean, or non-mismatch issues only (without --strict).
          3 - baseline file missing or unparseable.
          6 - integrity mismatch detected and --strict was set.
        """
        try:
            configure_logging(verbose=verbose)
            issues = verify_baseline(baseline, audit_log)
        except InputParseError as exc:
            typer.echo(f"Input parsing error: {exc}", err=True)
            raise typer.Exit(3) from exc
        except AgentsShipgateError as exc:
            typer.echo(f"Agents Shipgate error: {exc}", err=True)
            raise typer.Exit(4) from exc
        if json_output:
            payload = {
                "baseline_path": str(baseline),
                "audit_log_path": (
                    str(audit_log)
                    if audit_log is not None
                    else str(baseline.parent / "baseline-audit.log")
                ),
                "issue_count": len(issues),
                "issues": [
                    {
                        "kind": issue.kind,
                        "default_severity": issue.default_severity,
                        "title": issue.title,
                        "fingerprint": issue.fingerprint,
                        "check_id": issue.check_id,
                        "tool_name": issue.tool_name,
                        "evidence": _coerce_evidence(issue.evidence),
                    }
                    for issue in issues
                ],
            }
            typer.echo(json.dumps(payload, indent=2, default=str))
        else:
            if not issues:
                typer.echo(f"Baseline OK: {baseline}")
            else:
                typer.echo(f"Baseline {baseline}: {len(issues)} issue(s)")
                for issue in issues:
                    typer.echo(
                        f"  [{issue.default_severity}] {issue.kind}: "
                        f"{issue.title}"
                    )
        if strict and has_hash_mismatch(issues):
            emit_agent_mode_error(
                "baseline_integrity_failure",
                message="Baseline integrity mismatch detected.",
                exit_code=BASELINE_INTEGRITY_EXIT_CODE,
                command="agents-shipgate baseline verify",
                next_action="route baseline changes to human review",
                next_actions=[
                    {
                        "kind": "review",
                        "path": str(baseline),
                        "why": "Strict baseline verification found a hash mismatch.",
                        "expects": "human review of baseline provenance",
                    }
                ],
            )
            raise typer.Exit(BASELINE_INTEGRITY_EXIT_CODE)

    app.add_typer(baseline_app, name="baseline")


def _coerce_evidence(evidence: dict[str, object]) -> dict[str, object]:
    """Make evidence JSON-serializable.

    Most values are already strings/ints/lists, but ``date`` objects
    from ``BaselineProvenance.expires`` need explicit isoformat-ing.
    Keeping this here rather than in core/ avoids leaking CLI concerns
    into the data layer.
    """
    return {key: _coerce_value(value) for key, value in evidence.items()}


def _coerce_value(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[attr-defined]
    return value
