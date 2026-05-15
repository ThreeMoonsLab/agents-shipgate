from __future__ import annotations

import json
from pathlib import Path

import typer

from agents_shipgate.cli._helpers import _diagnose_config_error, _resolve_config_paths
from agents_shipgate.cli.agent_mode import emit_agent_mode_error as _emit_agent_mode_error
from agents_shipgate.cli.diagnostics import (
    NextAction,
    diagnose_doctor,
    diagnose_invalid_manifest,
    top_next_actions,
)
from agents_shipgate.cli.discovery.placeholders import collect_placeholders
from agents_shipgate.cli.scan import inspect_sources
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging


def register(app: typer.Typer) -> None:
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
