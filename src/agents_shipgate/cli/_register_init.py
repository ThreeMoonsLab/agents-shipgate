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
from agents_shipgate.cli.discovery.agent_instructions.adoption_kit import (
    AdoptionKitError,
    load_adoption_kit_config,
)
from agents_shipgate.cli.discovery.agent_instructions.targets import SPECS as _AI_SPECS
from agents_shipgate.cli.discovery.gitignore_block import (
    GitignoreOutcomeStatus,
    ensure_reports_gitignore,
)
from agents_shipgate.cli.discovery.local_contract import LOCAL_CONTRACT_RELATIVE_PATH
from agents_shipgate.cli.discovery.placeholders import collect_placeholders
from agents_shipgate.cli.discovery.scope import repository_root
from agents_shipgate.core.errors import DiscoveryError
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.detect import AgentProjectCandidate
from agents_shipgate.schemas.diagnostics import NextAction


def _validate_manifest_text(text: str) -> None:
    """Run the generated manifest through the schema before write."""
    import yaml

    from agents_shipgate.schemas.manifest import AgentsShipgateManifest

    data = yaml.safe_load(text)
    AgentsShipgateManifest.model_validate(data)


_VERIFY_ALIAS_COMMAND = "agents-shipgate verify --json"
_MAKEFILE_ALIAS_BLOCK = (
    "\n# agents-shipgate:start verify alias\n"
    ".PHONY: shipgate-verify\n"
    "shipgate-verify:\n"
    f"\t{_VERIFY_ALIAS_COMMAND}\n"
    "# agents-shipgate:end\n"
)


def _apply_claude_code_extras(workspace: Path, *, write: bool) -> dict[str, object]:
    """Install hooks + conventional verify aliases for the Claude Code setup."""
    from agents_shipgate.cli.install_hooks import render_or_install_hooks
    from agents_shipgate.core.errors import ConfigError

    hooks: dict[str, object]
    try:
        result = render_or_install_hooks(
            workspace=workspace,
            target="claude-code",
            write=write,
            config=Path("shipgate.yaml"),
            base="origin/main",
            head="",
            ci_mode="advisory",
        )
        hooks = {
            "settings_path": result.settings_path,
            "settings_status": result.settings_status,
            "script_path": result.script_path,
            "script_status": result.script_status,
        }
    except ConfigError as exc:
        hooks = {"status": "error", "message": str(exc)}
    return {
        "hooks": hooks,
        "verify_alias": {
            "makefile": _upsert_makefile_alias(workspace, write=write),
            "package_json": _upsert_package_json_alias(workspace, write=write),
        },
    }


def _upsert_makefile_alias(workspace: Path, *, write: bool) -> dict[str, str]:
    for name in ("Makefile", "makefile", "GNUmakefile"):
        path = workspace / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return {"path": str(path), "status": "skipped_unreadable", "message": str(exc)}
        if "shipgate-verify:" in text or "# agents-shipgate:start verify alias" in text:
            return {"path": str(path), "status": "unchanged"}
        if not write:
            return {"path": str(path), "status": "planned"}
        suffix = "" if text.endswith("\n") else "\n"
        path.write_text(text + suffix + _MAKEFILE_ALIAS_BLOCK, encoding="utf-8")
        return {"path": str(path), "status": "appended"}
    return {"status": "skipped_missing"}


def _upsert_package_json_alias(workspace: Path, *, write: bool) -> dict[str, str]:
    path = workspace / "package.json"
    if not path.is_file():
        return {"status": "skipped_missing"}
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"path": str(path), "status": "skipped_invalid", "message": str(exc)}
    if not isinstance(data, dict):
        return {"path": str(path), "status": "skipped_invalid"}
    scripts = data.get("scripts")
    if scripts is not None and not isinstance(scripts, dict):
        return {"path": str(path), "status": "skipped_invalid"}
    if scripts and "shipgate:verify" in scripts:
        return {"path": str(path), "status": "unchanged"}
    if not write:
        return {"path": str(path), "status": "planned"}
    data.setdefault("scripts", {})["shipgate:verify"] = _VERIFY_ALIAS_COMMAND
    indent = _detect_json_indent(text)
    rendered = json.dumps(data, indent=indent, ensure_ascii=False)
    if text.endswith("\n"):
        rendered += "\n"
    path.write_text(rendered, encoding="utf-8")
    return {"path": str(path), "status": "appended"}


def _detect_json_indent(text: str) -> int:
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped and len(line) > len(stripped):
            return len(line) - len(stripped)
    return 2


# A monorepo can hold hundreds of agent projects. The refusal lists enough
# of them to route on and points at the JSON payload for the rest.
_MAX_LISTED_SCOPE_CANDIDATES = 10


def _describe_candidate(candidate: AgentProjectCandidate) -> str:
    """One candidate as a line a human can choose from.

    Not every project names its agent in a string literal — a config-driven
    ``LlmAgent(name=CONFIG.agent_name)`` has none to parse — so the marker
    that made the directory a project stands in for it rather than leaving
    an empty pair of brackets.
    """

    detail = ", ".join(candidate.agent_names) or (candidate.marker or "project root")
    return f"{candidate.path} ({detail})"


def _requested_setup_flags(
    *,
    ci: bool,
    claude_code: bool,
    agent_instructions: str | None,
    agent_instructions_kit: Path | None,
) -> list[str]:
    """The setup this invocation asked for, as flags a rerun must repeat.

    A recovery command that drops ``--ci`` or an agent-instruction
    selection completes with less than the caller requested and reports
    success for it. Mirrors ``_rerun_options`` in the verifier, for the
    same reason.
    """

    flags: list[str] = []
    if ci:
        flags.append("--ci")
    if claude_code:
        flags.append("--claude-code")
    if agent_instructions is not None:
        flags.append(f"--agent-instructions={agent_instructions}")
    if agent_instructions_kit is not None:
        flags.extend(["--agent-instructions-kit", str(agent_instructions_kit)])
    return flags


def _unresolved_scope_message(
    workspace: Path,
    candidates: list[AgentProjectCandidate],
    *,
    scope: str,
) -> str:
    if scope == "unknown":
        lines = [
            f"Refusing to write shipgate.yaml: discovery stopped at the "
            f"Python-file cap while {workspace} holds several project roots, "
            "so whether one manifest describes it was not established.",
        ]
        if candidates:
            lines.append("Projects found before the cap:")
            for candidate in candidates[:_MAX_LISTED_SCOPE_CANDIDATES]:
                lines.append(f"  - {_describe_candidate(candidate)}")
    else:
        lines = [
            f"Refusing to write shipgate.yaml: {workspace} holds "
            f"{len(candidates)} self-contained projects that define agents, "
            "and one manifest describes one agent surface.",
            "Candidate project directories:",
        ]
        for candidate in candidates[:_MAX_LISTED_SCOPE_CANDIDATES]:
            lines.append(f"  - {_describe_candidate(candidate)}")
    remaining = len(candidates) - _MAX_LISTED_SCOPE_CANDIDATES
    if remaining > 0:
        lines.append(
            f"  - ... ({remaining} more; see auto_detected.agent_project_candidates "
            "in --json)"
        )
    lines.append(
        "Re-run init with --workspace pointed at the project you are changing, "
        "or pass --allow-unresolved-scope to write one manifest for this "
        "workspace as a whole."
    )
    if scope == "unknown":
        lines.append(
            "`agents-shipgate detect --max-python-files <n> --json` reports the "
            "full picture when the repository is larger than the default cap."
        )
    return "\n".join(lines)


def _unresolved_scope_actions(
    workspace: Path,
    candidates: list[AgentProjectCandidate],
    *,
    scope: str,
    setup_flags: list[str],
) -> list[NextAction]:
    """Rank the decision above the commands that carry it out.

    Rank 1 is deliberately not a command: promoting one candidate would
    make the same arbitrary pick this refusal exists to prevent. The
    per-candidate commands follow, in path order, so a caller that knows
    which project it is changing can match on the path rather than trust
    an ordering. Each repeats the setup flags this invocation asked for —
    a recovery that silently drops ``--ci`` or an agent-instruction
    selection completes with less than the caller requested.
    """

    why = (
        f"{workspace} defines agents in {len(candidates)} separate projects; "
        "pick the one this change belongs to. Shipgate will not choose for "
        "you — the manifest declares one agent's name, purpose, and tool "
        "surface."
        if scope != "unknown"
        else (
            f"Discovery of {workspace} was capped before it could tell whether "
            "one manifest describes it. Name the project you are changing, or "
            "re-run detection with a higher cap."
        )
    )
    actions = [
        NextAction(
            kind="review",
            why=why,
            expects=(
                "One project directory chosen from "
                "auto_detected.agent_project_candidates."
            ),
        )
    ]
    # The workspace root is never offered as a command: it is the scope this
    # run just refused, so running it again returns here. `.` stays in the
    # reported candidate list because agent files that belong to no
    # sub-project are real evidence of why the answer is unresolved, and
    # `--allow-unresolved-scope` is the route that accepts them.
    routable = [candidate for candidate in candidates if candidate.path != "."]
    for candidate in routable[:_MAX_LISTED_SCOPE_CANDIDATES]:
        target = workspace / candidate.path
        defines = ", ".join(candidate.agent_names)
        actions.append(
            NextAction(
                kind="command",
                command=render_command(
                    [
                        "init",
                        "--workspace",
                        str(target),
                        "--write",
                        *setup_flags,
                        "--json",
                    ]
                ),
                why=(
                    f"Initialize only {candidate.path}"
                    + (f", which defines {defines}." if defines else ".")
                ),
                expects=f"shipgate.yaml is created in {candidate.path}.",
            )
        )
    return actions


def _claude_code_outcome_lines(outcome: dict[str, object]) -> list[str]:
    lines: list[str] = []
    hooks = outcome.get("hooks")
    if isinstance(hooks, dict):
        if hooks.get("status") == "error":
            lines.append(f"Claude Code hooks: error — {hooks.get('message')}")
        else:
            lines.append(
                "Claude Code hooks: "
                f"{hooks.get('settings_path')} ({hooks.get('settings_status')}), "
                f"{hooks.get('script_path')} ({hooks.get('script_status')})"
            )
    alias = outcome.get("verify_alias")
    if isinstance(alias, dict):
        for key in ("makefile", "package_json"):
            entry = alias.get(key)
            if not isinstance(entry, dict):
                continue
            status = entry.get("status")
            if status == "skipped_missing":
                continue
            label = entry.get("path", key)
            lines.append(f"Verify alias ({key}): {label} ({status})")
    return lines


def register(app: typer.Typer) -> None:
    @app.command(hidden=True)
    def init(
        workspace: Path = typer.Option(Path("."), "--workspace", help="Workspace to inspect."),
        write: bool = typer.Option(
            False,
            "--write",
            help=(
                "Write shipgate.yaml if it does not exist. Also ensures "
                ".gitignore ignores agents-shipgate-reports/ via a managed "
                "block (idempotent; respects an existing line or explicit "
                "!negation; never blocks init)."
            ),
        ),
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
        allow_unresolved_scope: bool = typer.Option(
            False,
            "--allow-unresolved-scope",
            help=(
                "Write one manifest for a workspace whose manifest scope is "
                "unresolved — agents in several self-contained projects, or "
                "discovery capped before it could tell. Without this, --write "
                "refuses and lists the candidate project directories instead "
                "of adopting the first agent name it parsed."
            ),
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
        claude_code: bool = typer.Option(
            False,
            "--claude-code",
            help=(
                "One-shot Claude Code setup. Implies "
                "--agent-instructions=claude-md,claude-code-skill (unless "
                "--agent-instructions is passed explicitly), installs the "
                "Claude Code hooks (PostToolUse trigger + Stop verifier via "
                "install-hooks), and adds an `agents-shipgate verify --json` "
                "alias to Makefile / package.json scripts when those files "
                "exist. Dry-run without --write."
            ),
        ),
        agent_instructions: str | None = typer.Option(
            None,
            "--agent-instructions",
            help=(
                "Render or write agent-instruction snippets for the target repo. "
                "Pass --agent-instructions=default for the recommended downstream "
                "kit (AGENTS.md, Cursor rule, Claude command, and local contract), "
                "--agent-instructions=all for every supported target, "
                "--agent-instructions=agents-md,codex-skill for an explicit "
                "subset, or --agent-instructions=none to opt out. "
                "Without --write, snippets are printed to stdout (or returned in "
                "--json). With --write, snippets are written to AGENTS.md, "
                ".agents/skills/agents-shipgate/, "
                ".claude/skills/agents-shipgate/, CLAUDE.md, "
                ".claude/commands/shipgate.md, .cursor/rules/agents-shipgate.mdc, "
                f"{LOCAL_CONTRACT_RELATIVE_PATH}, and the PR template via managed "
                "`<!-- agents-shipgate:start -->` markers (idempotent where host "
                "files are shared, full-file/skill-bundle safe-update checks "
                "elsewhere). Strict CI and baselines remain opt-in human "
                "decisions; generated CI stays advisory by default."
            ),
        ),
        agent_instructions_kit: Path | None = typer.Option(
            None,
            "--agent-instructions-kit",
            help=(
                "Optional repo-local adoption-kit YAML config for file-tree "
                "agent-instruction targets. Relative paths resolve under "
                "--workspace. When omitted, init auto-discovers "
                ".agents-shipgate/adoption-kit.yaml."
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
                        "Pass --agent-instructions=default, --agent-instructions=all, "
                        "--agent-instructions=none, or a comma-separated subset."
                    ),
                    next_actions=[
                        NextAction(
                            kind="command",
                            command="agents-shipgate init --agent-instructions=default",
                            why=str(exc),
                            expects=(
                                "Snippets render for the recommended downstream "
                                "agent kit (AGENTS.md, Cursor rule, Claude "
                                "command, and local contract)."
                            ),
                        ).model_dump(mode="json")
                    ],
                )
                raise typer.Exit(2) from exc

        if claude_code and requested_targets is None:
            requested_targets = parse_selector("claude-md,claude-code-skill")

        excluded_sources: list[dict[str, str]] = []
        # Manifest scope, decided before anything is written. `--minimal`
        # never adopts a detected agent name or tool surface, so its output
        # cannot be silently mis-scoped and the refusal does not apply.
        scope_candidates: list[AgentProjectCandidate] = []
        detected_scope = "single"
        if minimal:
            template = render_manifest_template(workspace_resolved)
            placeholders = collect_placeholders(template)
            auto_detected: dict[str, object] = {}
            next_action_create = NextAction(
                kind="command",
                command="agents-shipgate scan -c shipgate.yaml",
                why=(
                    "Replace every value listed in placeholders[] in shipgate.yaml, "
                    "then scan the declared tool surface."
                ),
                expects="A readiness report under agents-shipgate-reports/.",
            )
            next_action_dry = "Inspect the template, then re-run with --write to commit it."
        else:
            try:
                detect_result = detect_workspace(workspace_resolved)
            except DiscoveryError as exc:
                message = (
                    "Workspace discovery could not establish bounded coverage: "
                    f"{exc}"
                )
                action = NextAction(
                    kind="review",
                    why=message,
                    expects=(
                        "Reduce the repository inventory or inspect the Git "
                        "failure, then rerun init."
                    ),
                )
                typer.echo(message, err=True)
                _emit_agent_mode_error(
                    "other_error",
                    message=message,
                    next_action=action.to_legacy_string(),
                    next_actions=[action.model_dump(mode="json")],
                )
                raise typer.Exit(4) from exc
            template = render_auto_manifest(workspace_resolved, detect_result)
            # Validation gate: refuse to emit a manifest the schema would reject.
            try:
                _validate_manifest_text(template)
            except Exception as exc:  # noqa: BLE001 - validation surface
                typer.echo(f"Generated manifest failed validation: {exc}", err=True)
                minimal_action = NextAction(
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
                )
                _emit_agent_mode_error(
                    "internal_error",
                    message=f"Generated manifest failed validation: {exc}",
                    next_action=minimal_action.to_legacy_string(),
                    next_actions=[minimal_action.model_dump(mode="json")],
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
                # Which directory this manifest is entitled to describe. On
                # "ambiguous", `chosen_agent_name` above is one of several
                # unrelated agents, so --write refuses (#363).
                "agent_scope": detect_result.agent_scope,
                "agent_project_candidates": [
                    candidate.model_dump(mode="json")
                    for candidate in detect_result.agent_project_candidates
                ],
            }
            scope_candidates = list(detect_result.agent_project_candidates)
            detected_scope = detect_result.agent_scope
            excluded_sources = detect_result.excluded_sources
            if excluded_sources:
                # Glob-matched files the input adapters reject — dropped from
                # tool_sources so the manifest scans, surfaced here so the
                # decision is visible to JSON consumers.
                auto_detected["excluded_sources"] = excluded_sources
            next_action_create = NextAction(
                kind="command",
                command="agents-shipgate scan -c shipgate.yaml --suggest-patches",
                why=(
                    "Review the auto-detected manifest, then scan the declared "
                    "tool surface with patch suggestions."
                ),
                expects="A readiness report under agents-shipgate-reports/.",
            )
            next_action_dry = "Inspect the template, then re-run with --write to commit it."

        kit_config = None
        if agent_instructions_kit is not None or requested_targets is not None:
            try:
                kit_config = load_adoption_kit_config(
                    workspace_resolved,
                    agent_instructions_kit,
                )
            except AdoptionKitError as exc:
                path = str(exc.path or agent_instructions_kit or workspace_resolved)
                typer.echo(str(exc), err=True)
                _emit_agent_mode_error(
                    "config_error",
                    path=path,
                    message=str(exc),
                    next_action=f"Edit {path}",
                    next_actions=[
                        NextAction(
                            kind="edit",
                            path=path,
                            why=str(exc),
                            expects=(
                                "Adoption-kit config uses schema_version: 1 "
                                "and each overrides_dir resolves under the workspace."
                            ),
                        ).model_dump(mode="json")
                    ],
                )
                raise typer.Exit(2) from exc

        # Manifest action — orthogonal to --ci. Track outcome instead of
        # exiting immediately so --ci can still run when the manifest exists.
        manifest_status = "not_attempted"
        manifest_exit = 0
        manifest_message: str | None = None
        manifest_skip_pending = False
        # A refused run writes nothing at all — not the workflow, not the
        # agent-instruction snippets, not the reports .gitignore block. The
        # scope it would have used is exactly what is in question, so leaving
        # managed edits behind in a directory Shipgate declined to adopt
        # would put unrelated modifications in the pull request (#363).
        scope_refused = False
        if write:
            if target.exists():
                manifest_status = "skipped_existing"
                manifest_exit = 2
                manifest_message = f"Config already exists: {target}"
                # Defer the agent-mode error emit. When --agent-instructions is
                # set the user's primary intent is refreshing snippets, and an
                # already-existing manifest is informational, not a failure.
                manifest_skip_pending = True
            elif detected_scope != "single" and not allow_unresolved_scope:
                manifest_status = "refused_unresolved_scope"
                manifest_exit = 2
                manifest_message = _unresolved_scope_message(
                    workspace_resolved, scope_candidates, scope=detected_scope
                )
                scope_refused = True
            else:
                target.write_text(template, encoding="utf-8")
                manifest_status = "written"
                manifest_message = f"Wrote {target}"

        # Workflow action — independent of manifest action.
        workflow_outcome: dict[str, object] | None = None
        workflow_requested = ci and not scope_refused
        if workflow_requested:
            # GitHub loads workflows from the repository root only, so a
            # scoped adoption still wires CI there — with a config path
            # relative to that root (#363).
            result = write_ci_workflow(
                workspace_resolved,
                repository_root=repository_root(workspace_resolved),
            )
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
        if requested_targets is not None and not scope_refused:
            ai_result = apply_agent_instructions(
                workspace_resolved,
                requested_targets,
                write=write,
                kit_config=kit_config,
            )
            agent_instructions_outcome = ai_result.to_json()
            agent_instructions_exit = ai_result.exit_code
            agent_instructions_targets = list(ai_result.targets)
            local_contract_target = next(
                (t for t in agent_instructions_targets if t.name == "local-contract"),
                None,
            )
        else:
            local_contract_target = None

        # Gitignore action — runs unconditionally on --write so the reports
        # directory created by the first `scan` is never silently committed.
        # Idempotent: subsequent runs see the managed block and report
        # `unchanged`. Never blocks `init` (exit_contribution is 0) — the
        # outcome is advisory, surfaced in --json and as a one-line message.
        # Also runs when the manifest already exists so repos that adopted
        # Shipgate before this CLI version was released get the line on their
        # next `init --write`.
        gitignore_outcome = (
            ensure_reports_gitignore(workspace_resolved, write=write)
            if write and not scope_refused
            else None
        )

        # Claude Code extras — hooks plus a conventional verify alias.
        # Best-effort and advisory: failures are reported in the outcome,
        # never as an init exit code (the instructions/manifest actions
        # above carry the contract).
        claude_code_outcome: dict[str, object] | None = None
        if claude_code and not scope_refused:
            claude_code_outcome = _apply_claude_code_extras(workspace_resolved, write=write)

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
        scope_actions: list[NextAction] = []
        if scope_refused:
            scope_actions = _unresolved_scope_actions(
                workspace_resolved,
                scope_candidates,
                scope=detected_scope,
                setup_flags=_requested_setup_flags(
                    ci=ci,
                    claude_code=claude_code,
                    agent_instructions=agent_instructions,
                    agent_instructions_kit=agent_instructions_kit,
                ),
            )
            _emit_agent_mode_error(
                "config_error",
                path=str(target),
                message=manifest_message,
                exit_code=manifest_exit,
                next_action=scope_actions[0].to_legacy_string(),
                next_actions=[action.model_dump(mode="json") for action in scope_actions],
                agent_scope=detected_scope,
                agent_project_candidates=[
                    candidate.model_dump(mode="json") for candidate in scope_candidates
                ],
            )
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
            elif scope_refused:
                # The manifest this run would have written describes a scope
                # nobody chose, so the routable answer is the choice — not the
                # scan command that assumes it was already made.
                payload["next_action"] = scope_actions[0].to_legacy_string()
                payload["next_actions"] = [
                    action.model_dump(mode="json") for action in scope_actions
                ]
            else:
                # Projected from the action rather than written twice, so the
                # recovery command is spelled for this invocation and carries
                # its structured argv (#322).
                payload["next_action"] = next_action_create.to_legacy_string()
                payload["next_actions"] = [next_action_create.model_dump(mode="json")]
            if auto_detected:
                payload["auto_detected"] = auto_detected
            if workflow_outcome is not None:
                payload["workflow"] = workflow_outcome
            if agent_instructions_outcome is not None:
                payload["agent_instructions"] = agent_instructions_outcome
            if local_contract_target is not None:
                payload["local_contract"] = local_contract_target.to_json()
            if gitignore_outcome is not None:
                payload["gitignore"] = gitignore_outcome.to_json()
            if claude_code_outcome is not None:
                payload["claude_code"] = claude_code_outcome
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
                    if excluded_sources:
                        typer.echo(
                            f"Excluded {len(excluded_sources)} detected file(s) "
                            "scan cannot parse as tool sources; see the comments "
                            "in shipgate.yaml."
                        )
                    if placeholders:
                        typer.echo(
                            f"Replace these placeholders before scanning: "
                            f"{', '.join(sorted({entry['path'] for entry in placeholders}))}"
                        )
                elif manifest_status in ("skipped_existing", "refused_unresolved_scope"):
                    typer.echo(manifest_message, err=True)
            if workflow_outcome is not None:
                stream = (
                    sys.stderr if workflow_outcome["status"].startswith("skipped") else sys.stdout
                )
                print(workflow_outcome["message"], file=stream)
            if write and agent_instructions_targets:
                for outcome in agent_instructions_targets:
                    stream = sys.stderr if outcome.status.startswith("skipped") else sys.stdout
                    if outcome.message:
                        print(outcome.message, file=stream)
            if gitignore_outcome is not None:
                # Quiet the no-op cases (already_present + unchanged) to keep
                # the success path scannable. Everything else — created,
                # updated, migrated, skipped_*, error — gets a line so an
                # adopter sees what changed (or why nothing did).
                noisy_status = {
                    GitignoreOutcomeStatus.ALREADY_PRESENT,
                    GitignoreOutcomeStatus.UNCHANGED,
                }
                if gitignore_outcome.status not in noisy_status:
                    stream = (
                        sys.stderr
                        if gitignore_outcome.status.value.startswith("skipped")
                        or gitignore_outcome.status is GitignoreOutcomeStatus.ERROR
                        else sys.stdout
                    )
                    print(gitignore_outcome.message, file=stream)
            if claude_code_outcome is not None:
                for line in _claude_code_outcome_lines(claude_code_outcome):
                    typer.echo(line)

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
