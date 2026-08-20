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
from agents_shipgate.cli.diagnostics import diagnose_detect
from agents_shipgate.cli.discovery import detect_workspace, select_agent_name
from agents_shipgate.cli.setup_control import (
    SETUP_INCOMPLETE,
    setup_control_envelope,
    setup_input_id,
)
from agents_shipgate.cli.workspace_guard import require_workspace
from agents_shipgate.core.errors import DiscoveryError
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.agent_control import AgentActionKind
from agents_shipgate.schemas.detect import DetectResult
from agents_shipgate.schemas.diagnostics import Diagnostic, NextAction

# A monorepo can hold hundreds of agent projects; the human summary lists
# enough to recognize the shape and points at --json for the rest.
_MAX_ECHOED_SCOPE_CANDIDATES = 10


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
    require_workspace(workspace)
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
    advance, advance_kind, advance_decision = _detect_advance(
        result, has_manifest=has_manifest, workspace=workspace_resolved
    )
    routing = setup_control_envelope(
        operation="detect",
        input_id=setup_input_id(
            operation="detect",
            workspace=workspace_resolved,
            manifest_path=(workspace_resolved / "shipgate.yaml" if has_manifest else None),
            routing_facts=(result.model_dump(mode="json"), advance_decision),
        ),
        reason=_detect_reason(result, has_manifest=has_manifest),
        diagnostics=diagnostics,
        advance=advance,
        advance_kind=advance_kind,
        advance_decision=advance_decision,
        # `detect` classifies; it never fails a gate. This JSON path always
        # exits 0, and reporting the fact beats leaving a reader to infer it.
        exit_code=0,
    )
    # One selected route reaches every field a caller can route on. Deriving the
    # legacy string independently is what let an executable command sit beside a
    # control state that authorized nothing.
    result = result.model_copy(update={"next_action": routing.legacy_next_action})
    if json_output:
        payload = result.model_dump(mode="json")
        payload["diagnostics"] = [d.model_dump(mode="json") for d in diagnostics]
        payload["next_actions"] = routing.json_actions()
        payload["control"] = routing.envelope.model_dump(mode="json")
        typer.echo(json.dumps(payload, indent=2))
        return

    if (
        not result.is_agent_project
        and not result.suggested_sources
        and not result.codex_plugin_candidates
    ):
        if result.agent_scope_truncated:
            # "No signals matched" is a claim about the whole workspace, and
            # a capped parse read part of one. On a repository large enough
            # to truncate before reaching any agent, the flat negative is
            # simply false — and it is the reading that stops an adopter
            # (#395).
            typer.echo(
                "Discovery was capped before it could classify this workspace."
            )
            typer.echo(
                "No agent framework signals matched in the part of the tree that "
                f"was read, and it holds {result.workspace_signals.project_root_count} "
                "project roots. Re-run with --max-python-files <n>, or point "
                "--workspace at the project you are changing."
            )
        else:
            typer.echo("Workspace does not appear to be an agent project.")
            typer.echo(
                "No agent framework signals matched the strong-signal threshold."
            )
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
    _echo_agent_scope(result)
    if result.agent_name_candidates:
        # The same call `init` makes, not "candidate zero" — printing the
        # top-ranked entry regardless of selectability would tell a human
        # that `t` is the agent name while `init` writes CHANGE_ME.
        selected = select_agent_name(result.agent_name_candidates)
        if selected is not None:
            typer.echo(
                f"Agent name candidate: {selected.value} (source: {selected.source})"
            )
        else:
            top = result.agent_name_candidates[0]
            typer.echo(
                "Agent name candidate: none usable — init will write CHANGE_ME "
                f"(highest ranked was {top.value!r})"
            )
            for reason in top.rationale:
                typer.echo(f"    · {reason}")
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
        return (
            "This workspace already has a shipgate.yaml. detect does not read it, "
            "so whether setup is complete is doctor's answer to give."
        )
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
    saying ``init`` there, which the command would refuse.

    That route names **doctor**, not the gate, and reports ``setup_incomplete``.
    ``detect`` classifies a workspace; it never opens the manifest, so it cannot
    know whether the manifest still owes a human a declaration. Asserting
    ``setup_complete`` from the mere presence of a file contradicted ``init`` and
    ``doctor`` on the same manifest — they returned ``human_review_required``
    for an unresolved ``agent_bindings`` declaration while this said "go verify",
    which is a route around the human stop. Handing off to the command that does
    read the manifest keeps one answer per obligation.

    An adopted manifest is checked first, and that ordering is the point: a
    person who ran ``init --allow-unresolved-scope`` chose the root as the
    boundary, and asking again on the next ``detect`` would make that decision
    impossible to keep.

    An unresolved *scope* is the same shape as an unresolved manifest, one step
    earlier. When a workspace defines agents in several projects (#363/#370),
    ``DetectResult.next_action`` is prose rather than a command precisely because
    naming one candidate would make the arbitrary pick ``init --write`` refuses
    to make. Typing that prose as a ``command`` action would publish an
    unrunnable string, and typing it as an agent route would ask the agent to
    make the choice; it is a human route.

    ``None`` when the workspace is not adoptable at all; the negative-control
    diagnostics own that route and end in a human stop.
    """

    if has_manifest:
        # An adopted root settles the scope question, including when discovery
        # still sees several candidate projects. `init --allow-unresolved-scope`
        # exists precisely so a person can accept the root as the boundary, and
        # re-asking on the next `detect` made that decision unrepeatable — the
        # flow could never hand the accepted manifest on. A manifest on disk is
        # evidence a choice was made; re-litigating it is not detect's to do.
        return (
            NextAction(
                kind="command",
                command=render_command(
                    ["doctor", "--config", str(workspace / "shipgate.yaml"), "--json"]
                ),
                why=(
                    "This workspace already has a manifest. Ask doctor whether it "
                    "is complete before treating setup as done."
                ),
                expects=(
                    "A doctor payload whose control state names the outstanding "
                    "setup obligation, or the release gate when there is none."
                ),
            ),
            "configure",
            SETUP_INCOMPLETE,
        )
    if result.agent_scope != "single":
        return (
            NextAction(
                kind="review",
                # `DetectResult.next_action` already states the situation and
                # names the field holding the candidates; restating it here
                # would be a second wording of one fact.
                why=result.next_action,
                expects=(
                    "One project directory chosen from agent_project_candidates, "
                    "then init --workspace <that path> --write."
                ),
            ),
            "discover",
            SETUP_INCOMPLETE,
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


def _echo_agent_scope(result: DetectResult) -> None:
    """Say when the workspace holds more than one manifest's worth of agents.

    Silent in the ordinary single-project case: the manifest scope is then
    the workspace the caller already named, and repeating it is noise.

    Never silent about a truncated walk. The candidate list below is what a
    human is told to choose from, and a list assembled from the part of the
    tree that got read first is a lower bound, not an enumeration — printing
    it unqualified told an adopter their own project was not an agent
    project (#395).
    """
    if result.agent_scope == "single":
        return
    candidates = result.agent_project_candidates
    typer.echo("")
    if result.agent_scope == "ambiguous":
        typer.echo(
            f"Agent scope: ambiguous — {len(candidates)} separate projects define agents:"
        )
    else:
        typer.echo(
            "Agent scope: unknown — discovery was capped before it could tell "
            "whether one manifest describes this workspace."
            + (" Projects found before the cap:" if candidates else "")
        )
    for candidate in candidates[:_MAX_ECHOED_SCOPE_CANDIDATES]:
        # A config-driven ``LlmAgent(name=CONFIG.agent_name)`` leaves no name
        # literal to parse; name the marker that made it a project instead.
        detail = ", ".join(candidate.agent_names) or (candidate.marker or "project root")
        typer.echo(f"- {candidate.path} ({detail})")
    remaining = len(candidates) - _MAX_ECHOED_SCOPE_CANDIDATES
    if remaining > 0:
        typer.echo(f"- ... ({remaining} more; see agent_project_candidates in --json)")
    if result.agent_scope == "ambiguous":
        typer.echo(
            "One shipgate.yaml describes one agent surface, so init --write "
            "refuses here until you name the project directory to initialize."
        )
    if result.agent_scope_truncated:
        roots = result.workspace_signals.project_root_count
        typer.echo(
            (
                "This list is not exhaustive: the Python parse stopped at the "
                f"cap in a workspace holding {roots} project roots, so a "
                "project in the unread remainder is missing from it."
                if candidates
                else (
                    "The Python parse stopped at the cap in a workspace "
                    f"holding {roots} project roots."
                )
            )
            + " Re-run with --max-python-files <n> before concluding your "
            "project is absent."
        )
    typer.echo("")


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
