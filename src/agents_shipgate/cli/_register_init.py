from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error as _emit_agent_mode_error
from agents_shipgate.cli.discovery import (
    detect_workspace,
    render_auto_manifest,
    render_manifest_template,
    write_ci_workflow,
)
from agents_shipgate.cli.discovery.agent_instructions import (
    InvalidSelector,
    apply_agent_instructions,
    parse_selector,
)
from agents_shipgate.cli.discovery.agent_instructions.targets import SPECS as _AI_SPECS
from agents_shipgate.cli.discovery.placeholders import collect_placeholders
from agents_shipgate.schemas.diagnostics import NextAction


def _validate_manifest_text(text: str) -> None:
    """Run the generated manifest through the schema before write."""
    import yaml

    from agents_shipgate.schemas.manifest import AgentsShipgateManifest

    data = yaml.safe_load(text)
    AgentsShipgateManifest.model_validate(data)


def register(app: typer.Typer) -> None:
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
        agent_instructions: str | None = typer.Option(
            None,
            "--agent-instructions",
            help=(
                "Render or write agent-instruction snippets for the target repo. "
                "Pass --agent-instructions=all for every target, "
                "--agent-instructions=agents-md,cursor for a subset, or "
                "--agent-instructions=none to opt out. "
                "Without --write, snippets are printed to stdout (or returned in "
                "--json). With --write, snippets are written to AGENTS.md, "
                ".agents/skills/agents-shipgate/, CLAUDE.md, "
                ".cursor/rules/agents-shipgate.mdc, and the PR template "
                "via managed `<!-- agents-shipgate:start -->` markers (idempotent "
                "where host files are shared, full-file/skill-bundle safe-update "
                "checks elsewhere). Strict CI and baselines remain opt-in human "
                "decisions; this flag only emits advisory guidance."
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

        # Parse --agent-instructions selector early so invalid input fails before
        # any filesystem mutation. ``None`` = flag absent.
        requested_targets: list[str] | None
        if agent_instructions is None:
            requested_targets = None
        else:
            try:
                requested_targets = parse_selector(agent_instructions)
            except InvalidSelector as exc:
                typer.echo(str(exc), err=True)
                _emit_agent_mode_error(
                    "config_error",
                    message=str(exc),
                    next_action=(
                        "Pass --agent-instructions=all, --agent-instructions=none, "
                        "or a comma-separated subset."
                    ),
                    next_actions=[
                        NextAction(
                            kind="command",
                            command="agents-shipgate init --agent-instructions=all",
                            why=str(exc),
                            expects=(
                                "Snippets render for every supported target "
                                "(AGENTS.md, Codex skill, CLAUDE.md, Cursor rule, "
                                "PR template)."
                            ),
                        ).model_dump(mode="json")
                    ],
                )
                raise typer.Exit(2) from exc

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
        manifest_skip_pending = False
        if write:
            if target.exists():
                manifest_status = "skipped_existing"
                manifest_exit = 2
                manifest_message = f"Config already exists: {target}"
                # Defer the agent-mode error emit. When --agent-instructions is
                # set the user's primary intent is refreshing snippets, and an
                # already-existing manifest is informational, not a failure.
                manifest_skip_pending = True
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

        # Agent-instructions action — independent of manifest and workflow actions.
        agent_instructions_outcome: dict[str, object] | None = None
        agent_instructions_exit = 0
        agent_instructions_targets: list[object] = []
        if requested_targets is not None:
            ai_result = apply_agent_instructions(
                workspace_resolved, requested_targets, write=write
            )
            agent_instructions_outcome = ai_result.to_json()
            agent_instructions_exit = ai_result.exit_code
            agent_instructions_targets = list(ai_result.targets)

        # Idempotency reconciliation: when --agent-instructions selects at least
        # one real target AND the manifest already exists, treat the manifest
        # action as already-done so `init --write --agent-instructions=<target>`
        # is safe to rerun (the advertised refresh command). The manifest_status
        # field still reports "skipped_existing" so callers can detect.
        #
        # `=none` parses to an empty list — no instruction action runs, so this
        # accommodation does NOT apply and manifest skip remains exit 2 (matches
        # plain `init --write`).
        if requested_targets and manifest_status == "skipped_existing":
            manifest_exit = 0
            manifest_skip_pending = False
        if manifest_skip_pending:
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
            if agent_instructions_outcome is not None:
                payload["agent_instructions"] = agent_instructions_outcome
            typer.echo(json.dumps(payload, indent=2))
        else:
            if not write:
                if requested_targets is not None:
                    # Manifest + each requested target, separated by section headers
                    # so the output is unambiguous.
                    typer.echo("--- shipgate.yaml ---")
                    typer.echo(template)
                    for outcome in agent_instructions_targets:
                        relative = _AI_SPECS[outcome.name].relative_path
                        typer.echo("")
                        typer.echo(f"--- {relative} ---")
                        typer.echo(outcome.rendered or "")
                else:
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
            if write and agent_instructions_targets:
                for outcome in agent_instructions_targets:
                    stream = sys.stderr if outcome.status.startswith("skipped") else sys.stdout
                    if outcome.message:
                        print(outcome.message, file=stream)

        # Surface a structured next_action JSON line for the rank-1 skipped target
        # so coding-agent callers can route to a fix without scraping stdout. Gated
        # on AGENTS_SHIPGATE_AGENT_MODE=1 by `_emit_agent_mode_error` itself.
        if agent_instructions_exit:
            first_skip = next(
                (t for t in agent_instructions_targets if t.status.startswith("skipped")),
                None,
            )
            if first_skip is not None:
                _emit_agent_mode_error(
                    "config_already_exists",
                    path=first_skip.path,
                    message=first_skip.message,
                    next_action=f"Edit {first_skip.path}",
                    next_actions=[
                        NextAction(
                            kind="edit",
                            path=first_skip.path,
                            why=first_skip.message
                            or f"{first_skip.path} is in a state we will not overwrite.",
                            expects=(
                                "After resolving, re-run "
                                f"`agents-shipgate init --write --agent-instructions={first_skip.name}`."
                            ),
                        ).model_dump(mode="json")
                    ],
                )

        final_exit = max(manifest_exit, agent_instructions_exit)
        if final_exit:
            raise typer.Exit(final_exit)
