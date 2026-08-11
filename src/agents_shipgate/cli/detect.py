"""``shipgate detect`` — classify a workspace as an agent project.

Read-only. Walks the workspace, scores per-framework signals, and emits a
:class:`agents_shipgate.schemas.detect.DetectResult` payload. Useful
for AI coding agents deciding whether to run ``init`` next; also exposed as
a library function so ``init`` Pass B can reuse the detection results.

Negative case (``is_agent_project=false``) is informational, not an error
— exit code 0 with payload.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.cli.diagnostics import (
    diagnose_detect,
    top_next_actions,
)
from agents_shipgate.cli.discovery import detect_workspace
from agents_shipgate.cli.setup_control import (
    SETUP_COMPLETE,
    SETUP_INCOMPLETE,
    setup_control_envelope,
    setup_input_id,
)
from agents_shipgate.core.errors import DiscoveryError
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.agent_control import AgentActionKind
from agents_shipgate.schemas.detect import DetectResult
from agents_shipgate.schemas.diagnostics import Diagnostic, NextAction


def detect(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace to inspect.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON. Default: human-readable summary.",
    ),
    max_python_files: int = typer.Option(
        1000,
        "--max-python-files",
        help="Cap on .py files to AST-parse. Defends against large monorepos.",
        hidden=True,
    ),
) -> None:
    """Classify a workspace: which agent framework(s), if any."""
    workspace_resolved = workspace.resolve()
    try:
        result = detect_workspace(
            workspace_resolved,
            max_python_files=max_python_files,
        )
    except DiscoveryError as exc:
        message = f"Workspace discovery could not establish bounded coverage: {exc}"
        action = NextAction(
            kind="review",
            why=message,
            expects=(
                "Reduce the repository inventory or inspect the Git failure, "
                "then rerun detect."
            ),
        )
        typer.echo(message, err=True)
        emit_agent_mode_error(
            "other_error",
            message=message,
            next_action=action.to_legacy_string(),
            next_actions=[action.model_dump(mode="json")],
        )
        raise typer.Exit(4) from exc
    has_manifest = (workspace_resolved / "shipgate.yaml").is_file()
    diagnostics: list[Diagnostic] = diagnose_detect(
        result, has_manifest=has_manifest, workspace=workspace_resolved
    )
    flattened = top_next_actions(diagnostics)
    if diagnostics:
        # Override the legacy single-string field with the rank-1 projection
        # so callers that read `next_action` get a routable answer when a
        # diagnostic fires (otherwise keep the existing classification text).
        result = result.model_copy(
            update={"next_action": flattened[0].to_legacy_string()}
        )
    if json_output:
        payload = result.model_dump(mode="json")
        payload["diagnostics"] = [d.model_dump(mode="json") for d in diagnostics]
        payload["next_actions"] = [a.model_dump(mode="json") for a in flattened]
        advance, advance_kind, advance_decision = _detect_advance(
            result, has_manifest=has_manifest, workspace=workspace_resolved
        )
        payload["control"] = setup_control_envelope(
            operation="detect",
            input_id=setup_input_id(
                operation="detect",
                workspace=workspace_resolved,
                manifest_path=(
                    workspace_resolved / "shipgate.yaml" if has_manifest else None
                ),
            ),
            reason=_detect_reason(result, has_manifest=has_manifest),
            diagnostics=diagnostics,
            advance=advance,
            advance_kind=advance_kind,
            advance_decision=advance_decision,
            # `detect` classifies; it never fails a gate. This JSON path always
            # exits 0, and reporting the fact beats leaving a reader to infer it.
            exit_code=0,
        ).model_dump(mode="json")
        typer.echo(json.dumps(payload, indent=2))
        return

    if (
        not result.is_agent_project
        and not result.suggested_sources
        and not result.codex_plugin_candidates
    ):
        typer.echo("Workspace does not appear to be an agent project.")
        typer.echo("No agent framework signals matched the strong-signal threshold.")
        _echo_excluded_sources(result.excluded_sources)
        return

    typer.echo(
        "Detected agent project."
        if result.is_agent_project
        else "Detected Shipgate-compatible artifact workspace."
    )
    typer.echo("")
    if result.frameworks:
        typer.echo("Frameworks:")
        for framework in result.frameworks:
            typer.echo(
                f"- {framework.type} (score={framework.score}, "
                f"confidence={framework.confidence})"
            )
            for line in framework.evidence[:5]:
                typer.echo(f"    · {line}")
            if len(framework.evidence) > 5:
                typer.echo(f"    · ... ({len(framework.evidence) - 5} more)")
        typer.echo("")
    if result.agent_name_candidates:
        primary = result.agent_name_candidates[0]
        typer.echo(f"Agent name candidate: {primary.value} (source: {primary.source})")
    if result.project_name_candidates:
        primary = result.project_name_candidates[0]
        typer.echo(f"Project name candidate: {primary.value} (source: {primary.source})")
    if result.suggested_sources:
        typer.echo("")
        typer.echo("Suggested tool sources:")
        for source in result.suggested_sources:
            typer.echo(f"- {source['type']}: {source['path']}")
    _echo_excluded_sources(result.excluded_sources)
    if result.codex_plugin_candidates:
        typer.echo("")
        typer.echo("Codex plugin candidates:")
        for candidate in result.codex_plugin_candidates:
            typer.echo(f"- {candidate.mode}: {candidate.path}")
    typer.echo("")
    typer.echo(f"Next: {result.next_action}")


def _detect_reason(result: DetectResult, *, has_manifest: bool) -> str:
    """One sentence stating what this classification found."""

    if has_manifest:
        return "This workspace already has a shipgate.yaml; classification is informational."
    if result.is_agent_project:
        frameworks = ", ".join(framework.type for framework in result.frameworks)
        return f"Detected an agent project ({frameworks}) with no shipgate.yaml yet."
    if result.suggested_sources or result.codex_plugin_candidates:
        return (
            "Detected Shipgate-compatible tool artifacts with no Python "
            "framework and no shipgate.yaml yet."
        )
    return "No agent framework, tool artifact, or prompt surface matched."


def _detect_advance(
    result: DetectResult,
    *,
    has_manifest: bool,
    workspace: Path,
) -> tuple[NextAction | None, AgentActionKind, str]:
    """The stage this classification hands off to when nothing is wrong.

    ``DetectResult.next_action`` already names ``init`` in the adoptable case,
    and this reuses it rather than composing a second answer. The one case it
    does *not* cover is a workspace that is already configured: detect keeps
    saying ``init`` there, which the command would refuse, so the control route
    names the gate instead. That is not a new decision — the manifest's presence
    is a fact detect already computed for its own diagnostics.

    ``None`` when the workspace is not adoptable at all; the negative-control
    diagnostics own that route and end in a human stop.
    """

    if has_manifest:
        return (
            NextAction(
                kind="command",
                command=render_command(["verify", "--workspace", str(workspace), "--json"]),
                why=(
                    "This workspace is already configured, so the outstanding "
                    "step is the release gate, not more setup."
                ),
                expects="A verifier run that publishes a control identity for this workspace.",
            ),
            "verify",
            SETUP_COMPLETE,
        )
    adoptable = bool(
        result.is_agent_project
        or result.suggested_sources
        or result.codex_plugin_candidates
    )
    if not adoptable:
        return (None, "discover", SETUP_INCOMPLETE)
    return (
        NextAction(
            kind="command",
            command=result.next_action,
            why="Draft a manifest for the detected agent surface.",
            expects="shipgate.yaml is created at the workspace root.",
        ),
        "initialize",
        SETUP_INCOMPLETE,
    )


def _echo_excluded_sources(excluded: list[dict[str, str]]) -> None:
    """List glob-matched files the input adapters reject.

    Answers "why wasn't my mcp.json picked up?" without making the file a
    tool source — writing it would fail ``scan`` input parsing (exit 3).
    """
    if not excluded:
        return
    typer.echo("")
    typer.echo("Excluded sources (scan cannot parse these as tool sources):")
    for source in excluded:
        typer.echo(f"- {source['type']}: {source['path']} — {source['reason']}")
