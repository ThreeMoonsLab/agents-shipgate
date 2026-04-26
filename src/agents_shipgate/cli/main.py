from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
from difflib import get_close_matches
from pathlib import Path

import typer

from agents_shipgate import __version__
from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.cli.discovery import discover_manifest_paths, render_manifest_template
from agents_shipgate.cli.fixture import fixture_app
from agents_shipgate.cli.scan import inspect_sources, run_scan
from agents_shipgate.cli.self_check import self_check
from agents_shipgate.core.baseline import write_baseline
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.findings import SEVERITY_ORDER
from agents_shipgate.core.logging import configure_logging


def _emit_agent_mode_error(error_kind: str, **fields: object) -> None:
    """When AGENTS_SHIPGATE_AGENT_MODE=1, emit a structured one-line JSON
    record on stderr after the human-readable error so coding agents can
    parse the next action without scraping prose."""
    if os.environ.get("AGENTS_SHIPGATE_AGENT_MODE", "").lower() not in {"1", "true", "yes", "on"}:
        return
    payload = {"error": error_kind, **fields}
    print(json.dumps(payload, default=str), file=sys.stderr)

app = typer.Typer(
    name="agents-shipgate",
    help="Manifest-first release readiness scanner for agent tool surfaces.",
    no_args_is_help=True,
    invoke_without_command=True,
)
baseline_app = typer.Typer(help="Manage local finding baselines.")
app.add_typer(baseline_app, name="baseline")
app.add_typer(fixture_app, name="fixture")
app.command(
    "self-check",
    help="Verify install and bundled fixtures. Run this first in a fresh environment.",
)(self_check)
logger = logging.getLogger(__name__)


@app.callback()
def _version(
    version: bool = typer.Option(False, "--version", help="Show version and exit.")
) -> None:
    if version:
        typer.echo(f"Agents Shipgate {__version__}")
        raise typer.Exit(0)


@app.command()
def scan(
    config: str = typer.Option(
        "shipgate.yaml",
        "--config",
        "-c",
        help="Path or quoted glob for shipgate.yaml.",
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        help="Scan every shipgate.yaml below this workspace.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory for reports. Overrides manifest output.directory.",
    ),
    formats: str = typer.Option(
        "markdown,json",
        "--format",
        help="Comma-separated report formats: markdown,json,sarif.",
    ),
    ci_mode: str | None = typer.Option(
        None,
        "--ci-mode",
        help="advisory or strict. Overrides manifest ci.mode.",
    ),
    fail_on: str | None = typer.Option(
        None,
        "--fail-on",
        help="Comma-separated severities that fail CI, for example critical,high.",
    ),
    baseline: Path | None = typer.Option(
        None,
        "--baseline",
        help="Path to a local baseline JSON. Strict mode fails only on new findings.",
    ),
    baseline_mode: str = typer.Option(
        "new-findings",
        "--baseline-mode",
        help="Baseline comparison mode. v0.3 supports new-findings.",
    ),
    deep_import: bool = typer.Option(
        False,
        "--deep-import",
        help="Deferred. Explicit import execution is not supported yet.",
        hidden=True,
    ),
    no_plugins: bool = typer.Option(
        False,
        "--no-plugins",
        help="Do not load third-party check plugins even when AGENTS_SHIPGATE_ENABLE_PLUGINS is set.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show debug extraction details."),
) -> None:
    """Run a static release-readiness scan."""
    try:
        configure_logging(verbose=verbose)
        parsed_formats = _parse_formats(formats)
        if ci_mode and ci_mode not in {"advisory", "strict"}:
            raise ConfigError("--ci-mode must be advisory or strict")
        parsed_fail_on = _parse_fail_on(fail_on)
        config_paths = _resolve_config_paths(config=config, workspace=workspace)
        if len(config_paths) == 1:
            report, exit_code = run_scan(
                config_path=config_paths[0],
                output_dir=out,
                formats=parsed_formats,
                ci_mode=ci_mode,
                fail_on=parsed_fail_on,
                baseline_path=baseline,
                baseline_mode=baseline_mode,
                deep_import=deep_import,
                plugins_enabled=False if no_plugins else None,
                verbose=verbose,
            )
            _print_cli_summary(report, ci_mode or "advisory", exit_code, verbose=verbose)
            raise typer.Exit(exit_code)
        exit_code = _run_multi_scan(
            config_paths=config_paths,
            out=out,
            formats=parsed_formats,
            ci_mode=ci_mode,
            fail_on=parsed_fail_on,
            baseline=baseline,
            baseline_mode=baseline_mode,
            deep_import=deep_import,
            plugins_enabled=False if no_plugins else None,
            verbose=verbose,
        )
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        _emit_agent_mode_error(
            "config_error",
            message=str(exc),
            next_action="agents-shipgate init --workspace . --write",
        )
        raise typer.Exit(2) from exc
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        _emit_agent_mode_error(
            "input_parse_error",
            message=str(exc),
            next_action="Inspect the file referenced in the error; ensure it exists, is valid, and resolves under the manifest directory.",
        )
        raise typer.Exit(3) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        _emit_agent_mode_error("other_error", message=str(exc))
        raise typer.Exit(4) from exc
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI boundary.
        if verbose:
            logger.exception("unhandled exception")
        typer.echo(f"Internal error: {exc}", err=True)
        _emit_agent_mode_error("internal_error", message=str(exc))
        raise typer.Exit(4) from exc

    raise typer.Exit(exit_code)


@app.command("list-checks")
def list_checks(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
    no_plugins: bool = typer.Option(
        False,
        "--no-plugins",
        help="Do not load third-party check plugins even when AGENTS_SHIPGATE_ENABLE_PLUGINS is set.",
    ),
) -> None:
    """List the built-in check catalog."""
    checks = check_catalog(plugins_enabled=False if no_plugins else None)
    if json_output:
        typer.echo(json.dumps([check.model_dump() for check in checks], indent=2))
        return
    for check in checks:
        typer.echo(
            f"{check.id}\t{check.default_severity}\t{check.category}\t{check.description}"
        )


@app.command()
def explain(
    check_id: str,
    no_plugins: bool = typer.Option(
        False,
        "--no-plugins",
        help="Do not load third-party check plugins even when AGENTS_SHIPGATE_ENABLE_PLUGINS is set.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Explain why a check exists and when it fires."""
    checks = check_catalog(plugins_enabled=False if no_plugins else None)
    check = next((item for item in checks if item.id == check_id), None)
    if not check:
        matches = get_close_matches(check_id, [item.id for item in checks], n=1)
        suggestion = matches[0] if matches else None
        suffix = f". Did you mean {suggestion}?" if suggestion else ""
        typer.echo(f"Unknown check id: {check_id}{suffix}", err=True)
        _emit_agent_mode_error(
            "unknown_check_id",
            check_id=check_id,
            suggestion=suggestion,
            next_action="agents-shipgate list-checks --json",
        )
        raise typer.Exit(2)
    if json_output:
        typer.echo(json.dumps(check.model_dump(), indent=2, sort_keys=True))
        return
    typer.echo(check.id)
    typer.echo(f"Category: {check.category}")
    typer.echo(f"Default severity: {check.default_severity}")
    typer.echo("")
    typer.echo(check.description)
    if check.rationale:
        typer.echo("")
        typer.echo(f"Rationale: {check.rationale}")
    if check.fires_when:
        typer.echo(f"Fires when: {check.fires_when}")
    if check.evidence_fields:
        typer.echo(f"Evidence fields: {', '.join(check.evidence_fields)}")
    if check.recommendation:
        typer.echo(f"Recommendation: {check.recommendation}")
    if check.docs_url:
        typer.echo(f"Docs: {check.docs_url}")


@app.command()
def init(
    workspace: Path = typer.Option(Path("."), "--workspace", help="Workspace to inspect."),
    write: bool = typer.Option(False, "--write", help="Write shipgate.yaml if it does not exist."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a structured summary (path, placeholders, next_action) on stdout.",
    ),
) -> None:
    """Draft a starter shipgate.yaml from local OpenAPI/MCP-looking files."""
    template = render_manifest_template(workspace.resolve())
    target = workspace / "shipgate.yaml"
    placeholders = _collect_placeholders(template)
    if write:
        if target.exists():
            typer.echo(f"Config already exists: {target}", err=True)
            _emit_agent_mode_error(
                "config_already_exists",
                path=str(target),
                next_action=f"Edit {target} directly or remove it before running init --write.",
            )
            raise typer.Exit(2)
        target.write_text(template, encoding="utf-8")
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "path": str(target),
                        "created": True,
                        "placeholders": placeholders,
                        "next_action": (
                            "Replace placeholders, then run: "
                            "agents-shipgate scan -c shipgate.yaml"
                        ),
                    },
                    indent=2,
                )
            )
            return
        typer.echo(f"Wrote {target}")
        if placeholders:
            typer.echo(
                f"Replace these placeholders before scanning: "
                f"{', '.join(sorted({entry['path'] for entry in placeholders}))}"
            )
        return
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "path": str(target),
                    "created": False,
                    "template": template,
                    "placeholders": placeholders,
                    "next_action": (
                        "Inspect the template, then re-run with --write to commit it."
                    ),
                },
                indent=2,
            )
        )
        return
    typer.echo(template)


def _collect_placeholders(template: str) -> list[dict[str, str]]:
    """Find ``CHANGE_ME`` markers in the rendered template and return their
    YAML-pointer-ish locations so an agent can fix them programmatically."""
    placeholders: list[dict[str, str]] = []
    section_path: list[str] = []
    last_indent = -1
    for line in template.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        # Adjust the path stack to match indentation level.
        while section_path and last_indent >= indent:
            section_path.pop()
            last_indent -= 2
        stripped = line.strip()
        if stripped.endswith(":") and "CHANGE_ME" not in stripped:
            section_path.append(stripped[:-1])
            last_indent = indent
            continue
        if "CHANGE_ME" in line:
            key = stripped.split(":", 1)[0].lstrip("- ").strip()
            placeholders.append(
                {
                    "path": ".".join([*section_path, key] if key else section_path) or "<root>",
                    "current": "CHANGE_ME",
                }
            )
    return placeholders


@app.command()
def doctor(
    config: str = typer.Option("shipgate.yaml", "--config", "-c", help="Path or quoted glob."),
    workspace: Path | None = typer.Option(None, "--workspace", help="Inspect every manifest below workspace."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs."),
) -> None:
    """Validate manifests and enumerate declared sources without running checks."""
    try:
        configure_logging(verbose=verbose)
        paths = _resolve_config_paths(config=config, workspace=workspace)
        payloads = [inspect_sources(config_path=path, verbose=verbose) for path in paths]
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        _emit_agent_mode_error(
            "config_error",
            message=str(exc),
            next_action="agents-shipgate init --workspace . --write",
        )
        raise typer.Exit(2) from exc
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        _emit_agent_mode_error("input_parse_error", message=str(exc))
        raise typer.Exit(3) from exc
    if json_output:
        typer.echo(json.dumps(payloads, indent=2, sort_keys=True))
        return
    for payload in payloads:
        typer.echo(f"Config: {payload['config']}")
        typer.echo(f"Project: {payload['project']}")
        typer.echo(f"Agent: {payload['agent']}")
        typer.echo(f"Total tools: {payload['total_tools']}")
        for source in payload["sources"]:
            typer.echo(
                f"- {source['id']} ({source['type']}): {source['tool_count']} tools"
                + (f"; sample={source['sample_tool']}" if source["sample_tool"] else "")
            )
        if payload.get("api_surface"):
            api_surface = payload["api_surface"]
            typer.echo(
                "OpenAI API artifacts: "
                f"prompts={api_surface.get('prompt_file_count', 0)}, "
                f"tool_files={api_surface.get('tool_file_count', 0)}, "
                f"response_formats={api_surface.get('response_format_count', 0)}, "
                f"test_cases={api_surface.get('test_case_count', 0)}, "
                f"traces={api_surface.get('trace_sample_count', 0)}, "
                f"policy_files={api_surface.get('policy_rule_count', 0)}"
            )
        frameworks = payload.get("frameworks")
        if isinstance(frameworks, dict) and frameworks.get("google_adk"):
            adk_surface = frameworks["google_adk"]
            typer.echo(
                "Google ADK artifacts: "
                f"agents={adk_surface.get('agent_count', 0)}, "
                f"functions={adk_surface.get('function_tool_count', 0)}, "
                f"toolsets={adk_surface.get('toolset_count', 0)}, "
                f"dynamic_toolsets={adk_surface.get('dynamic_toolset_count', 0)}, "
                f"eval_files={adk_surface.get('eval_file_count', 0)}"
            )
        if payload.get("baseline"):
            baseline = payload["baseline"]
            typer.echo(
                "Baseline: "
                f"{baseline.get('default_path')} "
                f"({'present' if baseline.get('present') else 'not found'})"
            )
        if payload["warnings"]:
            typer.echo("Warnings:")
            for warning in payload["warnings"]:
                typer.echo(f"- {warning}")
        typer.echo("")


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
        baseline = write_baseline(report, out)
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


def _parse_formats(value: str) -> list[str]:
    formats = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [item for item in formats if item not in {"markdown", "json", "sarif"}]
    if invalid:
        raise ConfigError(f"Unsupported report format(s): {', '.join(invalid)}")
    if not formats:
        raise ConfigError("At least one report format is required")
    return formats


def _parse_fail_on(value: str | None) -> list[str] | None:
    if value is None:
        return None
    severities = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [
        severity
        for severity in severities
        if severity not in {"info", "low", "medium", "high", "critical"}
    ]
    if invalid:
        raise ConfigError(f"Unsupported fail-on severity: {', '.join(invalid)}")
    return severities


def _resolve_config_paths(*, config: str, workspace: Path | None) -> list[Path]:
    if workspace:
        paths = discover_manifest_paths(workspace)
    elif any(char in config for char in "*?[]"):
        paths = sorted(Path(path) for path in glob.glob(config, recursive=True))
    else:
        paths = [Path(config)]
    if not paths:
        raise ConfigError("No shipgate.yaml files matched")
    return paths


def _run_multi_scan(
    *,
    config_paths: list[Path],
    out: Path | None,
    formats: list[str],
    ci_mode: str | None,
    fail_on: list[str] | None,
    baseline: Path | None,
    baseline_mode: str,
    deep_import: bool,
    plugins_enabled: bool | None,
    verbose: bool,
) -> int:
    typer.echo(f"Agents Shipgate {__version__}")
    typer.echo(f"Scanning {len(config_paths)} manifests")
    typer.echo("")
    exit_code = 0
    for config_path in config_paths:
        output_dir = None
        if out is not None:
            output_dir = out / _safe_output_name(config_path)
        try:
            report, scan_exit_code = run_scan(
                config_path=config_path,
                output_dir=output_dir,
                formats=formats,
                ci_mode=ci_mode,
                fail_on=fail_on,
                baseline_path=baseline,
                baseline_mode=baseline_mode,
                deep_import=deep_import,
                plugins_enabled=plugins_enabled,
                verbose=verbose,
            )
        except ConfigError as exc:
            scan_exit_code = 2
            typer.echo(f"{config_path}: config_error - {exc}", err=True)
        except InputParseError as exc:
            scan_exit_code = 3
            typer.echo(f"{config_path}: input_parse_error - {exc}", err=True)
        except AgentsShipgateError as exc:
            scan_exit_code = 4
            typer.echo(f"{config_path}: agents_shipgate_error - {exc}", err=True)
        except Exception as exc:  # noqa: BLE001 - multi-scan boundary.
            scan_exit_code = 4
            if verbose:
                logger.exception("unhandled exception while scanning %s", config_path)
            typer.echo(f"{config_path}: internal_error - {exc}", err=True)
        else:
            typer.echo(
                f"{config_path}: {report.summary.status} "
                f"(critical={report.summary.critical_count}, high={report.summary.high_count})"
            )
        exit_code = max(exit_code, scan_exit_code)
    typer.echo("")
    typer.echo(f"Exit code: {exit_code}")
    return exit_code


def _safe_output_name(config_path: Path) -> str:
    parent = config_path.parent
    try:
        display_parent = parent.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        display_parent = parent.resolve()
    raw = display_parent.as_posix()
    if raw in {"", "."}:
        return "root"
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return safe or "root"


def _print_cli_summary(report, ci_mode: str, exit_code: int, *, verbose: bool = False) -> None:
    summary = report.summary
    typer.echo(f"Agents Shipgate {__version__}")
    typer.echo("")
    typer.echo(f"Project: {report.project.get('name')}")
    typer.echo(f"Agent: {report.agent.get('name')}")
    typer.echo(f"Target: {report.environment.get('target')}")
    typer.echo("")
    typer.echo(f"Status: {summary.status}")
    typer.echo(f"Critical: {summary.critical_count}")
    typer.echo(f"High: {summary.high_count}")
    typer.echo(f"Medium: {summary.medium_count}")
    typer.echo(f"Human review: {'recommended' if summary.human_review_recommended else 'not required'}")
    typer.echo(f"Evidence coverage: {summary.evidence_coverage}")
    if report.baseline:
        typer.echo(
            "Baseline: "
            f"matched={report.baseline.matched_count}, "
            f"new={report.baseline.new_count}, "
            f"resolved={report.baseline.resolved_count}"
        )
    if verbose:
        typer.echo(f"Tool count: {report.tool_surface.total_tools}")
        typer.echo(f"Source warnings: {len(report.source_warnings)}")
    typer.echo("")
    top = [
        finding
        for finding in report.findings
        if not finding.suppressed and finding.severity in {"critical", "high"}
    ]
    top = sorted(top, key=lambda finding: (SEVERITY_ORDER[finding.severity], finding.check_id))[:5]
    typer.echo("Top findings:")
    if top:
        for finding in top:
            target = f": {finding.tool_name}" if finding.tool_name else ""
            typer.echo(f"- {finding.check_id}{target} - {finding.title}")
    else:
        typer.echo("- none")
    typer.echo("")
    typer.echo("Reports:")
    for path in report.generated_reports.values():
        typer.echo(f"- {path}")
    if verbose and report.source_warnings:
        typer.echo("")
        typer.echo("Source warnings:")
        for warning in report.source_warnings:
            typer.echo(f"- {warning}")
    typer.echo("")
    typer.echo(f"CI mode: {ci_mode}")
    typer.echo(f"Exit code: {exit_code}")
