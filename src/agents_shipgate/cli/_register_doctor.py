from __future__ import annotations

import json
from pathlib import Path

import typer

from agents_shipgate.cli._helpers import (
    _diagnose_config_error,
    _echo_next_action_hint,
    _resolve_config_paths,
)
from agents_shipgate.cli.agent_mode import emit_agent_mode_error as _emit_agent_mode_error
from agents_shipgate.cli.diagnostics import diagnose_doctor, top_next_actions
from agents_shipgate.cli.discovery.placeholders import collect_placeholders
from agents_shipgate.cli.scan.inspect import MANIFEST_SNAPSHOT_KEY, inspect_sources
from agents_shipgate.cli.setup_control import (
    SETUP_COMPLETE,
    setup_control_envelope,
    setup_input_id,
)
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.diagnostics import NextAction


def _doctor_reason(payload: dict[str, object], manifest_path: Path) -> str:
    total = payload.get("total_tools", 0)
    sources = payload.get("sources") or []
    return (
        f"{manifest_path} loaded with {len(sources)} tool source(s) "
        f"and {total} enumerable tool(s)."
    )


def _doctor_advance(manifest_path: Path, *, workspace: Path) -> NextAction:
    """The stage a clean manifest hands off to.

    ``doctor`` validates configuration; it never decides a release, so even a
    manifest with nothing wrong leaves an outstanding step. Naming the gate is
    what keeps the setup envelope from looking like a finish line — and
    ``verify``, not ``scan``, because ``scan`` is the engine rather than the
    command an adopter runs.

    ``--workspace`` is carried explicitly. Without it the command was runnable
    only from a directory that happened to be the workspace: run from anywhere
    else, ``verify`` resolved the repository from the *caller's* cwd and exited 2
    with "Workspace is not inside a git checkout". The invocation policy makes
    emitted commands runnable where they were produced (#322), and a command
    that silently depends on the caller's cwd is the same failure by another
    route. Doctor already knows the workspace — the explicit ``--workspace`` when
    one was supplied, the manifest's own directory otherwise.

    ``--config`` is resolved to an absolute path for the same reason, and it is
    not the same reason twice. ``verify`` resolves a relative ``--config``
    against the workspace it was given, so passing the caller's own relative
    spelling through composed the two: ``doctor --config repo/shipgate.yaml``
    from the parent directory emitted ``--workspace <abs>/repo --config
    repo/shipgate.yaml``, and verify then looked for ``<abs>/repo/repo/…``.
    Absolute is unambiguous under every workspace, including the glob matches
    where the two are not even siblings.
    """

    try:
        config = manifest_path.resolve()
    except OSError:  # pragma: no cover - unreadable cwd
        config = manifest_path
    return NextAction(
        kind="command",
        command=render_command(
            [
                "verify",
                "--workspace",
                str(workspace),
                "--config",
                str(config),
                "--json",
            ]
        ),
        why=(
            "The manifest validates and its declared sources resolve. The "
            "outstanding step is the release gate, which doctor does not run."
        ),
        expects="A verifier run that publishes a control identity for this workspace.",
    )


def register(app: typer.Typer) -> None:
    @app.command(hidden=True)
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
            _echo_next_action_hint(flattened)
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
                    # A specific discovered manifest failed to load. Route
                    # through the same ConfigError classifier as scan so
                    # unknown third-party adapter source types produce the
                    # install/enable diagnostic instead of the generic
                    # INVALID-MANIFEST "edit shipgate.yaml" recovery.
                    typer.echo(f"Config error: {exc}", err=True)
                    diagnostics = _diagnose_config_error(
                        config=str(path), workspace=None, exc=exc
                    )
                    flattened = top_next_actions(diagnostics)
                    _echo_next_action_hint(flattened)
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
            # The bytes `inspect_sources` itself read, handed back out of band.
            # Reopening the file here was a *third* read — after the manifest
            # load and after the source resolution — so a concurrent edit could
            # publish a route selected from one manifest under an identity
            # hashing another.
            manifest_bytes = payload.pop(MANIFEST_SNAPSHOT_KEY, b"")
            manifest_text = manifest_bytes.decode("utf-8", errors="replace")
            placeholders = collect_placeholders(manifest_text)
            diagnostics = diagnose_doctor(
                payload,
                manifest_path=path,
                manifest_text=manifest_text,
                placeholders=placeholders,
            )
            manifest_workspace = (workspace or path.parent).resolve()
            routing = setup_control_envelope(
                operation="doctor",
                input_id=setup_input_id(
                    operation="doctor",
                    workspace=manifest_workspace,
                    manifest_path=path,
                    manifest_bytes=manifest_bytes,
                    # `doctor` routes on the *inspection* of the declared
                    # sources, not only on the manifest text: deleting a
                    # declared source file moved the route from a verify
                    # handoff to an edit while the manifest bytes were
                    # unchanged, and the identity did not move with it.
                    # Every fact `diagnose_doctor` reads, so the identity moves
                    # whenever the route can. Naming them individually rather
                    # than hashing the whole payload keeps run-to-run noise
                    # (timestamps, absolute paths) out of the identity while
                    # still covering the inputs that select a route.
                    routing_facts=(
                        payload.get("unresolved_sources"),
                        payload.get("total_tools"),
                        payload.get("warnings"),
                        payload.get("codex_plugin_surface"),
                        payload.get("frameworks"),
                        payload.get("manifest_summary"),
                        placeholders,
                    ),
                ),
                reason=_doctor_reason(payload, path),
                diagnostics=diagnostics,
                placeholders=placeholders,
                manifest_display_path=str(path),
                advance=_doctor_advance(path, workspace=manifest_workspace),
                advance_kind="verify",
                advance_decision=SETUP_COMPLETE,
                # `doctor` exits 3 for a human on unresolved sources but 0 for a
                # JSON consumer, and the control envelope is read by the latter.
                # Publishing the human exit code here would contradict the
                # process status the caller actually observed.
                exit_code=0,
            )
            enriched = dict(payload)
            enriched["diagnostics"] = [d.model_dump(mode="json") for d in diagnostics]
            # The complete structured locations the human-review route refers to.
            # That route names the first few and says "and N more in
            # placeholders[]"; doctor did not publish the array, so the rest were
            # unrecoverable from its output.
            enriched["placeholders"] = placeholders
            # Both routing fields come from the one selected route, so a caller
            # reading the legacy string and a caller reading `control` are never
            # sent to different work.
            enriched["next_actions"] = routing.json_actions()
            enriched["next_action"] = routing.legacy_next_action
            enriched["control"] = routing.envelope.model_dump(mode="json")
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
            if isinstance(frameworks, dict) and frameworks.get("conductor"):
                conductor_surface = frameworks["conductor"]
                typer.echo(
                    "Conductor OSS artifacts: "
                    f"workflows={conductor_surface.get('workflow_count', 0)}, "
                    f"mcp_calls={conductor_surface.get('mcp_call_task_count', 0)}, "
                    f"llm_tasks={conductor_surface.get('llm_task_count', 0)}, "
                    f"dynamic_surfaces={conductor_surface.get('dynamic_tool_surface_count', 0)}, "
                    f"unsupported={conductor_surface.get('unsupported_capability_count', 0)}"
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
