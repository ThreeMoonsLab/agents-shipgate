from __future__ import annotations

import glob
import json
import logging
import re
import sys
from difflib import get_close_matches
from pathlib import Path

import typer

from agents_shipgate import __version__
from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.cli.agent_mode import emit_agent_mode_error as _emit_agent_mode_error
from agents_shipgate.cli.apply_patches import apply_patches as _apply_patches_command
from agents_shipgate.cli.detect import detect as _detect_command
from agents_shipgate.cli.diagnostics import (
    NextAction,
    diagnose_doctor,
    diagnose_invalid_manifest,
    diagnose_missing_manifest,
    top_next_actions,
)
from agents_shipgate.cli.discovery import (
    detect_workspace,
    discover_manifest_paths,
    render_auto_manifest,
    render_manifest_template,
    write_ci_workflow,
)
from agents_shipgate.cli.discovery.placeholders import collect_placeholders
from agents_shipgate.cli.evidence_packet import evidence_packet as _evidence_packet_command
from agents_shipgate.cli.fixture import fixture_app
from agents_shipgate.cli.scan import inspect_sources, run_scan
from agents_shipgate.cli.scenario import scenario_app
from agents_shipgate.cli.self_check import self_check
from agents_shipgate.contract import build_contract_payload
from agents_shipgate.core.baseline import write_baseline
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.findings import SEVERITY_ORDER
from agents_shipgate.core.logging import configure_logging

app = typer.Typer(
    name="agents-shipgate",
    help="Manifest-first release readiness scanner for agent tool surfaces.",
    no_args_is_help=True,
    invoke_without_command=True,
)
baseline_app = typer.Typer(help="Manage local finding baselines.")
app.add_typer(baseline_app, name="baseline")
app.add_typer(fixture_app, name="fixture")
app.add_typer(scenario_app, name="scenario")
app.command(
    "self-check",
    help="Verify install and bundled fixtures. Run this first in a fresh environment.",
)(self_check)
app.command(
    "detect",
    help="Classify a workspace: which agent framework(s), if any. Read-only.",
)(_detect_command)
app.command(
    "apply-patches",
    help=(
        "Apply patches from a scan JSON report. Dry-run by default; pass "
        "--apply to mutate. Containment-checked against the report's "
        "manifest_dir."
    ),
)(_apply_patches_command)
app.command(
    "evidence-packet",
    help=(
        "Re-render a Release Evidence Packet from an existing packet.json "
        "into md, html, and/or pdf."
    ),
)(_evidence_packet_command)
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
    diff_from: Path | None = typer.Option(
        None,
        "--diff-from",
        help="Prior report.json or v0.3 baseline JSON used for tool-surface diff.",
    ),
    baseline_mode: str = typer.Option(
        "new-findings",
        "--baseline-mode",
        help="Baseline comparison mode. Supported value: new-findings.",
    ),
    policy_packs: list[Path] | None = typer.Option(
        None,
        "--policy-pack",
        help="Additional declarative YAML policy pack path. May be supplied multiple times.",
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
    suggest_patches: bool = typer.Option(
        False,
        "--suggest-patches",
        help=(
            "Attach machine-applicable patches (or ManualPatch fallback) to "
            "every active finding. Use `agents-shipgate apply-patches` to "
            "apply them; the report stays read-only."
        ),
    ),
    packet: bool | None = typer.Option(
        None,
        "--packet/--no-packet",
        help=(
            "Emit the Release Evidence Packet alongside report.{md,json}. "
            "Defaults to manifest output.packet.enabled (true unless the "
            "manifest disables it). Use --no-packet to override."
        ),
    ),
    packet_format: str | None = typer.Option(
        None,
        "--packet-format",
        help=(
            "Comma-separated packet formats: md,json,html,pdf. "
            "Default from manifest output.packet.formats (md,json,html). "
            "PDF requires the [pdf] extras."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show debug extraction details."),
) -> None:
    """Run a static release-readiness scan."""
    # Parse CLI options first, in their own try block. ConfigError raised
    # here is about flag values, not the manifest — emitting a manifest
    # diagnostic ("edit shipgate.yaml") would route the agent to the
    # wrong fix.
    try:
        configure_logging(verbose=verbose)
        parsed_formats = _parse_formats(formats)
        parsed_packet_formats = _parse_packet_formats(packet_format)
        if ci_mode and ci_mode not in {"advisory", "strict"}:
            raise ConfigError("--ci-mode must be advisory or strict")
        parsed_fail_on = _parse_fail_on(fail_on)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        guidance = (
            "Fix the invalid CLI flag value referenced in the error and "
            "re-run scan."
        )
        _emit_agent_mode_error(
            "config_error",
            message=str(exc),
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="review",
                    why=guidance,
                    expects=(
                        "Re-run with a flag value the option parser accepts."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(2) from exc

    try:
        config_paths = _resolve_config_paths(config=config, workspace=workspace)
        if len(config_paths) == 1:
            report, exit_code = run_scan(
                config_path=config_paths[0],
                output_dir=out,
                formats=parsed_formats,
                ci_mode=ci_mode,
                fail_on=parsed_fail_on,
                baseline_path=baseline,
                diff_from_path=diff_from,
                baseline_mode=baseline_mode,
                deep_import=deep_import,
                policy_pack_paths=policy_packs,
                plugins_enabled=False if no_plugins else None,
                verbose=verbose,
                suggest_patches=suggest_patches,
                packet_enabled=packet,
                packet_formats=parsed_packet_formats,
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
            diff_from=diff_from,
            baseline_mode=baseline_mode,
            deep_import=deep_import,
            policy_packs=policy_packs or [],
            plugins_enabled=False if no_plugins else None,
            verbose=verbose,
            suggest_patches=suggest_patches,
            packet_enabled=packet,
            packet_formats=parsed_packet_formats,
        )
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        diagnostics = _diagnose_config_error(
            config=config, workspace=workspace, exc=exc
        )
        flattened = top_next_actions(diagnostics)
        _emit_agent_mode_error(
            "config_error",
            message=str(exc),
            next_action=flattened[0].to_legacy_string(),
            next_actions=[a.model_dump(mode="json") for a in flattened],
        )
        raise typer.Exit(2) from exc
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        guidance = (
            "Inspect the file referenced in the error; ensure it exists, "
            "is valid, and resolves under the manifest directory."
        )
        _emit_agent_mode_error(
            "input_parse_error",
            message=str(exc),
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="review",
                    why=guidance,
                    expects=(
                        "Referenced file is present, parseable, and inside "
                        "the manifest directory."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(3) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        guidance = (
            "Re-run with --verbose for a stack trace, then file an issue if "
            "the error is not actionable."
        )
        _emit_agent_mode_error(
            "other_error",
            message=str(exc),
            next_action=guidance,
            next_actions=[
                NextAction(kind="review", why=guidance).model_dump(mode="json")
            ],
        )
        raise typer.Exit(4) from exc
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI boundary.
        if verbose:
            logger.exception("unhandled exception")
        typer.echo(f"Internal error: {exc}", err=True)
        guidance = (
            "Re-run with --verbose for a stack trace; this is a bug — please "
            "file an issue."
        )
        _emit_agent_mode_error(
            "internal_error",
            message=str(exc),
            next_action=guidance,
            next_actions=[
                NextAction(kind="review", why=guidance).model_dump(mode="json")
            ],
        )
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
def contract(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Show the installed CLI contract for agent consumers."""
    payload = build_contract_payload()
    if json_output:
        typer.echo(json.dumps(payload.model_dump(mode="json"), indent=2))
        return

    typer.echo(f"Contract version: {payload.contract_version}")
    typer.echo(f"CLI version: {payload.cli_version}")
    typer.echo(f"Report schema version: {payload.report_schema_version}")
    typer.echo(f"Packet schema version: {payload.packet_schema_version}")
    typer.echo(f"Gating signal: {payload.gating_signal}")
    typer.echo("Manual review signals:")
    for signal in payload.manual_review_signals:
        typer.echo(f"  {signal}")


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
            next_actions=[
                NextAction(
                    kind="command",
                    command="agents-shipgate list-checks --json",
                    why=(
                        "Enumerate the full check catalog so the agent can "
                        "match by id."
                    ),
                    expects=(
                        "JSON array of CheckMetadata objects with stable ids."
                    ),
                ).model_dump(mode="json")
            ],
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
    minimal: bool = typer.Option(
        False,
        "--minimal",
        help="Use the legacy CHANGE_ME-heavy template instead of auto-detection.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="(No-op alias.) Auto-detection is the default in v0.6+.",
        hidden=True,
    ),
    ci: bool = typer.Option(
        False,
        "--ci",
        help=(
            "Also generate .github/workflows/agents-shipgate.yml. Refuses to "
            "overwrite. Skips with a message if another workflow already "
            "calls ThreeMoonsLab/agents-shipgate."
        ),
    ),
) -> None:
    """Draft a starter shipgate.yaml from a workspace.

    Default (v0.6+): walk the workspace, detect agent framework(s), and
    emit a near-complete manifest. Use --minimal to fall back to the
    pre-v0.6 CHANGE_ME-heavy template.
    """
    workspace_resolved = workspace.resolve()
    target = workspace / "shipgate.yaml"

    if minimal:
        template = render_manifest_template(workspace_resolved)
        placeholders = collect_placeholders(template)
        auto_detected: dict[str, object] = {}
        next_action_create = (
            "Replace placeholders, then run: agents-shipgate scan -c shipgate.yaml"
        )
        next_action_dry = "Inspect the template, then re-run with --write to commit it."
    else:
        detect_result = detect_workspace(workspace_resolved)
        template = render_auto_manifest(workspace_resolved, detect_result)
        # Validation gate: refuse to emit a manifest the schema would reject.
        try:
            _validate_manifest_text(template)
        except Exception as exc:  # noqa: BLE001 - validation surface
            typer.echo(f"Generated manifest failed validation: {exc}", err=True)
            _emit_agent_mode_error(
                "internal_error",
                message=f"Generated manifest failed validation: {exc}",
                next_action="agents-shipgate init --minimal",
                next_actions=[
                    NextAction(
                        kind="command",
                        command="agents-shipgate init --minimal",
                        why=(
                            "Auto-detected manifest failed schema validation. "
                            "Fall back to the legacy CHANGE_ME-heavy template."
                        ),
                        expects=(
                            "shipgate.yaml renders with placeholder fields "
                            "you fill in manually."
                        ),
                    ).model_dump(mode="json")
                ],
            )
            raise typer.Exit(4) from exc
        placeholders = collect_placeholders(template)
        # Mirror the template's selection logic so JSON output never claims
        # a name that the YAML left as CHANGE_ME. Per v0.6 reviewer
        # feedback: workspace_dir is a candidate but NOT chosen for
        # agent.name; only Agent_name_literal/ADK_name_field do.
        chosen_agent_name: str | None = None
        for candidate in detect_result.agent_name_candidates:
            if candidate.source in {"Agent_name_literal", "ADK_name_field"}:
                chosen_agent_name = candidate.value
                break
        auto_detected = {
            "is_agent_project": detect_result.is_agent_project,
            "frameworks": [
                {
                    "type": fw.type,
                    "score": fw.score,
                    "confidence": fw.confidence,
                }
                for fw in detect_result.frameworks
            ],
            # The actual value the manifest will carry (None when the
            # template falls back to CHANGE_ME).
            "agent_name": chosen_agent_name,
            # Full candidate list with sources, so agents can pick a
            # different one if they want to override.
            "agent_name_candidates": [
                {"value": c.value, "source": c.source}
                for c in detect_result.agent_name_candidates
            ],
        }
        next_action_create = (
            "Review and run: agents-shipgate scan -c shipgate.yaml --suggest-patches"
        )
        next_action_dry = (
            "Inspect the template, then re-run with --write to commit it."
        )

    # Manifest action — orthogonal to --ci. Track outcome instead of
    # exiting immediately so --ci can still run when the manifest exists.
    manifest_status = "not_attempted"
    manifest_exit = 0
    manifest_message: str | None = None
    if write:
        if target.exists():
            manifest_status = "skipped_existing"
            manifest_exit = 2
            manifest_message = f"Config already exists: {target}"
            _emit_agent_mode_error(
                "config_already_exists",
                path=str(target),
                next_action=f"Edit {target}",
                next_actions=[
                    NextAction(
                        kind="edit",
                        path=str(target),
                        why=(
                            f"{target} already exists. Edit it directly or "
                            "remove it before re-running init --write."
                        ),
                        expects=(
                            "Manifest reflects the desired tool sources, "
                            "agent declared_purpose, and policies."
                        ),
                    ).model_dump(mode="json")
                ],
            )
        else:
            target.write_text(template, encoding="utf-8")
            manifest_status = "written"
            manifest_message = f"Wrote {target}"

    # Workflow action — independent of manifest action.
    workflow_outcome: dict[str, object] | None = None
    if ci:
        result = write_ci_workflow(workspace_resolved)
        workflow_outcome = {
            "status": result.status,
            "path": result.path,
            "message": result.message,
        }
        if result.cross_reference_path is not None:
            workflow_outcome["cross_reference_path"] = result.cross_reference_path

    # Output
    if json_output:
        payload: dict[str, object] = {
            "path": str(target),
            "created": manifest_status == "written",
            "manifest_status": manifest_status,
            "placeholders": placeholders,
        }
        if manifest_message:
            payload["manifest_message"] = manifest_message
        if not write:
            payload["template"] = template
            payload["next_action"] = next_action_dry
        else:
            payload["next_action"] = next_action_create
        if auto_detected:
            payload["auto_detected"] = auto_detected
        if workflow_outcome is not None:
            payload["workflow"] = workflow_outcome
        typer.echo(json.dumps(payload, indent=2))
    else:
        if not write:
            typer.echo(template)
        else:
            if manifest_status == "written":
                typer.echo(manifest_message)
                if placeholders:
                    typer.echo(
                        f"Replace these placeholders before scanning: "
                        f"{', '.join(sorted({entry['path'] for entry in placeholders}))}"
                    )
            elif manifest_status == "skipped_existing":
                typer.echo(manifest_message, err=True)
        if workflow_outcome is not None:
            stream = (
                sys.stderr
                if workflow_outcome["status"].startswith("skipped")
                else sys.stdout
            )
            print(workflow_outcome["message"], file=stream)

    if manifest_exit:
        raise typer.Exit(manifest_exit)


def _validate_manifest_text(text: str) -> None:
    """Run the generated manifest through the schema before write."""
    import yaml

    from agents_shipgate.config.schema import AgentsShipgateManifest

    data = yaml.safe_load(text)
    AgentsShipgateManifest.model_validate(data)


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
    except ConfigError as exc:
        # Discovery itself failed — no candidate manifest exists.
        typer.echo(f"Config error: {exc}", err=True)
        diagnostics = _diagnose_config_error(
            config=config, workspace=workspace, exc=exc
        )
        flattened = top_next_actions(diagnostics)
        _emit_agent_mode_error(
            "config_error",
            message=str(exc),
            next_action=flattened[0].to_legacy_string(),
            next_actions=[a.model_dump(mode="json") for a in flattened],
        )
        raise typer.Exit(2) from exc
    payloads: list[dict[str, object]] = []
    try:
        for path in paths:
            try:
                payloads.append(inspect_sources(config_path=path, verbose=verbose))
            except ConfigError as exc:
                # A specific discovered manifest failed to load. If the
                # file exists, route the agent to edit it directly
                # (INVALID-MANIFEST) — `init` refuses to overwrite, so
                # MISSING-MANIFEST's detect/init hint would loop. If
                # the file is genuinely absent (only possible in the
                # bare ``-c missing.yaml`` path, since discovery and
                # globbing only yield existing files), fall through to
                # the missing-manifest dispatch.
                typer.echo(f"Config error: {exc}", err=True)
                if path.is_file():
                    diagnostics = diagnose_invalid_manifest(
                        path, message=str(exc)
                    )
                else:
                    diagnostics = _diagnose_config_error(
                        config=str(path), workspace=None, exc=exc
                    )
                flattened = top_next_actions(diagnostics)
                _emit_agent_mode_error(
                    "config_error",
                    message=str(exc),
                    next_action=flattened[0].to_legacy_string(),
                    next_actions=[
                        a.model_dump(mode="json") for a in flattened
                    ],
                )
                raise typer.Exit(2) from exc
    except typer.Exit:
        raise
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        guidance = (
            "Inspect the file referenced in the error; ensure it exists, "
            "is valid, and resolves under the manifest directory."
        )
        _emit_agent_mode_error(
            "input_parse_error",
            message=str(exc),
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="review",
                    why=guidance,
                    expects=(
                        "Referenced file is present, parseable, and inside "
                        "the manifest directory."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(3) from exc
    enriched_payloads: list[dict[str, object]] = []
    for path, payload in zip(paths, payloads, strict=True):
        try:
            manifest_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            manifest_text = ""
        placeholders = collect_placeholders(manifest_text)
        diagnostics = diagnose_doctor(
            payload,
            manifest_path=path,
            manifest_text=manifest_text,
            placeholders=placeholders,
        )
        flattened = top_next_actions(diagnostics)
        enriched = dict(payload)
        enriched["diagnostics"] = [d.model_dump(mode="json") for d in diagnostics]
        enriched["next_actions"] = [a.model_dump(mode="json") for a in flattened]
        enriched["next_action"] = (
            flattened[0].to_legacy_string() if flattened else ""
        )
        enriched_payloads.append(enriched)
    payloads = enriched_payloads
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
        if isinstance(frameworks, dict) and frameworks.get("langchain"):
            langchain_surface = frameworks["langchain"]
            typer.echo(
                "LangChain artifacts: "
                f"functions={langchain_surface.get('function_tool_count', 0)}, "
                f"structured_tools={langchain_surface.get('structured_tool_count', 0)}, "
                f"tool_nodes={langchain_surface.get('tool_node_count', 0)}, "
                f"dynamic_surfaces={langchain_surface.get('dynamic_tool_surface_count', 0)}"
            )
        if isinstance(frameworks, dict) and frameworks.get("crewai"):
            crewai_surface = frameworks["crewai"]
            typer.echo(
                "CrewAI artifacts: "
                f"agents={crewai_surface.get('agent_count', 0)}, "
                f"functions={crewai_surface.get('function_tool_count', 0)}, "
                f"class_tools={crewai_surface.get('class_tool_count', 0)}, "
                f"prebuilt_tools={crewai_surface.get('prebuilt_tool_count', 0)}, "
                f"dynamic_surfaces={crewai_surface.get('dynamic_tool_surface_count', 0)}"
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
        if payload.get("unresolved_sources"):
            typer.echo("Unresolved required sources:")
            config_name = Path(str(payload["config"])).name
            for entry in payload["unresolved_sources"]:
                line = entry.get("line")
                location = (
                    f"{config_name}:{line}" if line is not None else config_name
                )
                typer.echo(
                    f"- {entry['id']} -> {entry['declared_path']!r} "
                    f"(declared at {location})"
                )
        diagnostics = payload.get("diagnostics") or []
        if diagnostics:
            typer.echo("Diagnostics:")
            for diag in diagnostics:
                typer.echo(
                    f"- [{diag['severity']}] {diag['id']}: {diag['title']}"
                )
                if diag["next_actions"]:
                    action = diag["next_actions"][0]
                    kind = action["kind"]
                    if kind == "command":
                        typer.echo(f"    next: {action['command']}")
                    elif kind == "edit":
                        typer.echo(f"    edit: {action['path']}")
                    elif kind == "stop":
                        typer.echo(f"    stop: {action['why']}")
                    else:
                        typer.echo(f"    review: {action['why']}")
        typer.echo("")
    # Restore pre-PR loud-failure for humans on the missing-required-source
    # case. JSON consumers (agents) get exit 0 + unresolved_sources earlier in
    # this function and route on the structured diagnostic instead.
    if any(payload.get("unresolved_sources") for payload in payloads):
        raise typer.Exit(3)


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


def _parse_packet_formats(value: str | None) -> list[str] | None:
    if value is None:
        return None
    parts = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [item for item in parts if item not in {"md", "json", "html", "pdf"}]
    if invalid:
        raise ConfigError(
            f"Unsupported packet format(s): {', '.join(invalid)}; "
            "expected a subset of md,json,html,pdf"
        )
    if not parts:
        raise ConfigError(
            "--packet-format must contain at least one of md,json,html,pdf"
        )
    return parts


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


def _missing_manifest_workspace(
    *, config: str, workspace: Path | None
) -> Path:
    """Pick the workspace path used by the missing-manifest diagnostic.

    Routes recovery to the directory the user pointed scan/doctor at
    (``-c <path>`` or ``--workspace <dir>``), not whichever directory
    they happen to be invoking the CLI from. For glob inputs, walks the
    path components and uses the longest non-glob prefix — so an
    invocation like ``scan -c /tmp/repo/*/shipgate.yaml`` from another
    cwd still routes the agent to ``/tmp/repo``.
    """
    if workspace is not None:
        return workspace.resolve()
    if any(char in config for char in "*?[]"):
        return _glob_non_glob_prefix(config)
    config_path = Path(config)
    parent = config_path.parent
    if not str(parent) or str(parent) == ".":
        return Path.cwd()
    # `Path.resolve()` works on non-existent paths — and the manifest
    # parent often exists even when the manifest itself is missing.
    return parent.resolve()


def _glob_non_glob_prefix(config: str) -> Path:
    """Return the longest leading path component sequence with no glob
    metacharacters, falling back to ``cwd`` for purely-relative globs.
    """
    parts = Path(config).parts
    safe: list[str] = []
    for part in parts:
        if any(char in part for char in "*?[]"):
            break
        safe.append(part)
    if not safe:
        return Path.cwd()
    candidate = Path(*safe)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _candidate_manifest_paths(
    *, config: str, workspace: Path | None
) -> list[Path]:
    """Enumerate the manifest paths the user pointed scan/doctor at.

    Mirrors ``_resolve_config_paths`` but does not raise — it's called
    from inside the ``ConfigError`` handler, where re-raising would
    obscure the original failure. Returns an empty list when nothing
    resolves; the dispatcher then falls back to ``MISSING-MANIFEST``.
    """
    try:
        if workspace is not None:
            return list(discover_manifest_paths(workspace))
        if any(char in config for char in "*?[]"):
            return sorted(Path(p) for p in glob.glob(config, recursive=True))
        return [Path(config)]
    except Exception:  # noqa: BLE001 — diagnostic dispatch must not fail
        return []


def _diagnose_config_error(
    *, config: str, workspace: Path | None, exc: ConfigError
) -> list:
    """Pick the right diagnostic for a ``ConfigError``.

    ``ConfigError`` covers two distinct failure shapes:
    - the manifest file does not exist (``MISSING-MANIFEST``)
    - one or more candidate manifest files exist but the loader rejected
      them — invalid YAML, schema validation failure, unsupported
      version (``INVALID-MANIFEST``)

    Disambiguate by walking every candidate path the CLI invocation
    points at (direct ``-c <file>``, ``--workspace`` discovery, or a
    glob pattern). If any candidate is a real file, the loader is
    choking on it — emit ``INVALID-MANIFEST`` for that file.
    """
    for candidate in _candidate_manifest_paths(
        config=config, workspace=workspace
    ):
        if candidate.is_file():
            return diagnose_invalid_manifest(candidate, message=str(exc))
    return diagnose_missing_manifest(
        _missing_manifest_workspace(config=config, workspace=workspace)
    )


def _run_multi_scan(
    *,
    config_paths: list[Path],
    out: Path | None,
    formats: list[str],
    ci_mode: str | None,
    fail_on: list[str] | None,
    baseline: Path | None,
    diff_from: Path | None,
    baseline_mode: str,
    deep_import: bool,
    policy_packs: list[Path],
    plugins_enabled: bool | None,
    verbose: bool,
    suggest_patches: bool = False,
    packet_enabled: bool | None = None,
    packet_formats: list[str] | None = None,
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
                diff_from_path=diff_from,
                baseline_mode=baseline_mode,
                deep_import=deep_import,
                policy_pack_paths=policy_packs,
                plugins_enabled=plugins_enabled,
                verbose=verbose,
                suggest_patches=suggest_patches,
                packet_enabled=packet_enabled,
                packet_formats=packet_formats,
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
            # v0.8: lead with release_decision.decision (baseline-aware,
            # the recommended release-gate signal). Fall back to the
            # legacy summary.status only if the report somehow lacks
            # release_decision (older baselines loaded for diff, etc.).
            decision = report.release_decision
            if decision is not None:
                typer.echo(
                    f"{config_path}: {decision.decision} "
                    f"(blockers={len(decision.blockers)}, "
                    f"review_items={len(decision.review_items)}, "
                    f"critical={report.summary.critical_count}, "
                    f"high={report.summary.high_count})"
                )
            else:
                typer.echo(
                    f"{config_path}: {report.summary.status} "
                    f"(critical={report.summary.critical_count}, "
                    f"high={report.summary.high_count})"
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
    decision = report.release_decision
    typer.echo(f"Agents Shipgate {__version__}")
    typer.echo("")
    typer.echo(f"Project: {report.project.get('name')}")
    typer.echo(f"Agent: {report.agent.get('name')}")
    typer.echo(f"Target: {report.environment.get('target')}")
    typer.echo("")
    if decision is not None:
        typer.echo(f"Decision: {decision.decision}")
        typer.echo(f"Reason: {decision.reason}")
        typer.echo(f"Blockers: {len(decision.blockers)}")
        typer.echo(f"Review items: {len(decision.review_items)}")
        ev = decision.evidence_coverage
        ev_extras: list[str] = []
        if ev.low_confidence_tool_count:
            ev_extras.append(f"{ev.low_confidence_tool_count} low-confidence tool(s)")
        if ev.source_warning_count:
            ev_extras.append(f"{ev.source_warning_count} source warning(s)")
        if ev.human_review_recommended:
            ev_extras.append("human review recommended")
        suffix = f" ({'; '.join(ev_extras)})" if ev_extras else ""
        typer.echo(f"Evidence coverage: {ev.level}{suffix}")
        bd = decision.baseline_delta
        if bd.enabled:
            typer.echo(
                "Baseline delta: "
                f"matched={bd.matched_count}, new={bd.new_count}, "
                f"resolved={bd.resolved_count}"
            )
        else:
            typer.echo("Baseline delta: not enabled")
        fp = decision.fail_policy
        fail_on_text = ", ".join(fp.fail_on) if fp.fail_on else "none"
        typer.echo(
            f"Fail policy: ci_mode={fp.ci_mode}, fail_on=[{fail_on_text}], "
            f"new_findings_only={str(fp.new_findings_only).lower()}, "
            f"would_fail_ci={str(fp.would_fail_ci).lower()}"
        )
    else:
        typer.echo("Decision: (not recorded)")
    typer.echo("")
    typer.echo(
        f"Counts: critical={summary.critical_count}, high={summary.high_count}, "
        f"medium={summary.medium_count}, low={summary.low_count}, "
        f"suppressed={summary.suppressed_count}"
    )
    diff = report.tool_surface_diff
    if diff.enabled:
        if _tool_surface_diff_has_changes(diff.summary):
            typer.echo(
                "Tool-surface diff: "
                f"+{diff.summary.tools_added} tools, "
                f"-{diff.summary.tools_removed} tools, "
                f"{diff.summary.tools_changed} changed, "
                f"{diff.summary.new_high_risk_effects} new high-risk effect(s), "
                f"{diff.summary.controls_removed} removed control(s)"
            )
        else:
            typer.echo("Tool-surface diff: no changes")
    elif diff.notes:
        typer.echo(f"Tool-surface diff: disabled ({diff.notes[0]})")
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


def _tool_surface_diff_has_changes(summary) -> bool:
    return any(
        (
            summary.tools_added,
            summary.tools_removed,
            summary.tools_changed,
            summary.new_scopes,
            summary.removed_scopes,
            summary.new_high_risk_effects,
            summary.removed_high_risk_effects,
            summary.controls_added,
            summary.controls_removed,
            summary.metadata_changes,
            summary.policy_drift_items,
            summary.new_findings,
            summary.resolved_findings,
            summary.unchanged_findings,
            summary.accepted_debt,
        )
    )
