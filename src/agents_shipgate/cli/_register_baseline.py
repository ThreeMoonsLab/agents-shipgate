from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import typer

from agents_shipgate.checks.baseline_integrity import has_hash_mismatch
from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.cli.scan.path_helpers import _resolve_audit_log_path
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.baseline import (
    baseline_status_payload,
    baseline_status_violations,
    load_baseline,
    verify_baseline,
    write_baseline,
)
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
        owner: str | None = typer.Option(
            None,
            "--owner",
            help=(
                "Human approver recorded on newly-accepted entries "
                "(exception metadata; never inferred)."
            ),
        ),
        reason: str | None = typer.Option(
            None,
            "--reason",
            help="Why this debt was accepted; recorded on newly-accepted entries.",
        ),
        expires: str | None = typer.Option(
            None,
            "--expires",
            help="Review-by date (YYYY-MM-DD) recorded on newly-accepted entries.",
        ),
        apply_to_existing: bool = typer.Option(
            False,
            "--apply-to-existing",
            help=(
                "Also fill --owner/--reason/--expires into existing entries "
                "that lack them. Never overwrites a previously-set value and "
                "preserves each entry's original recorded_at."
            ),
        ),
        verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs."),
    ) -> None:
        """Save active unsuppressed findings as the current accepted baseline."""
        # Blank approval metadata is rejected, not normalized away silently:
        # declared human authority cannot be blank (human_ack contract), and
        # a blank owner must never satisfy `status --require-owner`.
        for flag_name, value in (("--owner", owner), ("--reason", reason)):
            if value is not None and not value.strip():
                typer.echo(
                    f"{flag_name} cannot be blank — declared human authority "
                    "requires a non-empty value.",
                    err=True,
                )
                raise typer.Exit(2)
        owner = owner.strip() if owner is not None else None
        reason = reason.strip() if reason is not None else None
        expires_date: date | None = None
        if expires is not None:
            try:
                expires_date = date.fromisoformat(expires)
            except ValueError:
                typer.echo(
                    f"--expires must be an ISO date (YYYY-MM-DD), got {expires!r}.",
                    err=True,
                )
                raise typer.Exit(2) from None
        if apply_to_existing and not (owner or reason or expires_date):
            typer.echo(
                "--apply-to-existing needs at least one of --owner/--reason/--expires.",
                err=True,
            )
            raise typer.Exit(2)
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
            baseline = write_baseline(
                report,
                out,
                audit_log_path=audit_log,
                owner=owner,
                reason=reason,
                expires=expires_date,
                apply_to_existing=apply_to_existing,
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
            raise typer.Exit(BASELINE_INTEGRITY_EXIT_CODE)

    @baseline_app.command("status")
    def baseline_status(
        baseline: Path = typer.Option(
            Path(".agents-shipgate/baseline.json"),
            "--baseline",
            help="Baseline JSON path to report on.",
        ),
        as_of: str | None = typer.Option(
            None,
            "--as-of",
            help="Compute ages/expiry as of this ISO date (default: today UTC).",
        ),
        require_owner: bool = typer.Option(
            False,
            "--require-owner",
            help="Governance gate: every entry must name an owner.",
        ),
        require_expiry: bool = typer.Option(
            False,
            "--require-expiry",
            help="Governance gate: every entry must carry an unexpired review-by date.",
        ),
        max_age_days: int | None = typer.Option(
            None,
            "--max-age-days",
            help="Governance gate: no entry may be older than this many days.",
        ),
        json_output: bool = typer.Option(
            False, "--json", help="Emit JSON instead of text."
        ),
    ) -> None:
        """Accepted-debt aging report; gate flags exit 20 on violations.

        Advisory with no gate flags. Entries without provenance fail every
        active gate (unknown history is ungoverned debt, not exempt debt).
        """
        as_of_date: date
        if as_of is not None:
            try:
                as_of_date = date.fromisoformat(as_of)
            except ValueError:
                typer.echo(
                    f"--as-of must be an ISO date (YYYY-MM-DD), got {as_of!r}.",
                    err=True,
                )
                raise typer.Exit(2) from None
        else:
            as_of_date = datetime.now(UTC).date()
        try:
            baseline_file = load_baseline(baseline)
        except InputParseError as exc:
            typer.echo(f"Input parsing error: {exc}", err=True)
            raise typer.Exit(3) from exc
        payload = baseline_status_payload(baseline_file, as_of=as_of_date)
        violations = baseline_status_violations(
            payload,
            require_owner=require_owner,
            require_expiry=require_expiry,
            max_age_days=max_age_days,
        )
        payload["violations"] = violations
        gating = require_owner or require_expiry or max_age_days is not None
        if json_output:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            summary = payload["summary"]
            typer.echo(f"Accepted debt as of {payload['as_of']}: {summary['total']} entries")
            typer.echo(
                f"  owned: {summary['owned']} · unowned: {summary['unowned']} · "
                f"no expiry: {summary['no_expiry']} · expired: {summary['expired']} · "
                f"expiring within {payload['expiring_within_days']}d: "
                f"{summary['expiring_within_days']}"
            )
            if summary["oldest_age_days"] is not None:
                typer.echo(f"  oldest entry: {summary['oldest_age_days']} days")
            if summary["no_provenance"]:
                typer.echo(
                    f"  no provenance: {summary['no_provenance']} "
                    "(legacy entries; re-save to stamp)"
                )
            for entry in payload["entries"]:
                marks = []
                if entry["expired"]:
                    marks.append("EXPIRED")
                if entry["unowned"]:
                    marks.append("unowned")
                if entry["no_expiry"]:
                    marks.append("no-expiry")
                suffix = f"  [{', '.join(marks)}]" if marks else ""
                owner_text = entry["owner"] or "—"
                age_text = (
                    f"{entry['age_days']}d" if entry["age_days"] is not None else "?"
                )
                typer.echo(
                    f"  {entry['fingerprint'][:16]}  {entry['check_id']}  "
                    f"owner={owner_text}  age={age_text}{suffix}"
                )
            if violations:
                typer.echo(f"Governance violations: {len(violations)}")
                for violation in violations:
                    typer.echo(
                        f"  {violation['kind']}: {violation['fingerprint'][:16]}"
                    )
                typer.echo(
                    "Re-acknowledge after review: agents-shipgate baseline save "
                    "--owner <name> --reason <why> --expires <YYYY-MM-DD> "
                    "--apply-to-existing"
                )
        if gating and violations:
            raise typer.Exit(20)

    app.add_typer(baseline_app, name="baseline", hidden=True)


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
