from __future__ import annotations

import sys
from pathlib import Path

import typer

from agents_shipgate.cli.agent_result import (
    agent_result_json,
    build_codex_agent_result,
    git_diff_text,
)
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.schemas.agent_control import CodingAgentCommandAction
from agents_shipgate.schemas.codex_boundary_result import CodexBoundaryResultV2


def check(
    agent: str = typer.Option(
        "codex",
        "--agent",
        help="Agent runtime to check: codex, claude-code, or cursor.",
    ),
    diff: str | None = typer.Option(
        None,
        "--diff",
        help=(
            "Unified diff file to evaluate, or '-' to read stdin. The workspace "
            "may contain either the base tree or the already-applied head tree; "
            "mismatched content fails closed."
        ),
    ),
    format_: str = typer.Option(
        "codex-boundary-json",
        "--format",
        help="Output format. Supports codex-boundary-json.",
    ),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace root containing the Codex-local surfaces.",
    ),
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        "-c",
        help="Shipgate manifest path used for trigger context.",
    ),
    policy: Path | None = typer.Option(
        None,
        "--policy",
        help="Optional Codex boundary policy file. Defaults to workspace policy then packaged default.",
    ),
    base: str | None = typer.Option(
        None,
        "--base",
        help="Base git ref for diff resolution when --diff is omitted.",
    ),
    head: str | None = typer.Option(
        None,
        "--head",
        help="Head git ref for diff resolution when --diff is omitted.",
    ),
) -> None:
    """Run the agent-native local boundary check."""

    if agent not in {"codex", "claude-code", "cursor"}:
        typer.echo("--agent must be one of: codex, claude-code, cursor.", err=True)
        raise typer.Exit(2)
    if format_ == "agent-json":
        typer.echo(
            "--format agent-json was removed in the 0.14.0 contract cleanup. "
            "Use --format codex-boundary-json.",
            err=True,
        )
        raise typer.Exit(2)
    if format_ != "codex-boundary-json":
        typer.echo("--format must be 'codex-boundary-json'.", err=True)
        raise typer.Exit(2)
    try:
        if diff == "-":
            diff_text = sys.stdin.read()
        elif diff:
            diff_text = Path(diff).read_text(encoding="utf-8")
        else:
            diff_text = git_diff_text(workspace=workspace, base=base, head=head)
    except (OSError, RuntimeError) as exc:
        result = _diff_input_error_result(
            agent=agent,
            workspace=workspace,
            diff=diff,
            base=base,
            head=head,
            error=str(exc) or "diff input could not be resolved",
        )
        typer.echo(agent_result_json(result))
        return

    result = build_codex_agent_result(
        agent=agent,
        workspace=workspace,
        diff_text=diff_text,
        config=config,
        policy=policy,
    )
    typer.echo(agent_result_json(result))


def _diff_input_error_result(
    *,
    agent: str,
    workspace: Path,
    diff: str | None,
    base: str | None,
    head: str | None,
    error: str,
) -> CodexBoundaryResultV2:
    command = _rerun_command(agent=agent, diff=diff, base=base, head=head)
    summary = "Agents Shipgate could not resolve the diff input for local agent control."
    return CodexBoundaryResultV2(
        agent=agent,
        subject={
            "workspace": str(workspace),
            "agent": agent,
            "diff": diff,
            "base": base,
            "head": head,
        },
        decision="block",
        risk_level="medium",
        audit_id="agent_check_diff_input_error",
        policy_version="unresolved",
        summary=summary,
        changed_files=[],
        control=derive_agent_control(
            reason=summary,
            next_action=CodingAgentCommandAction(
                kind="repair",
                command=command,
                why=(
                    "Fix the diff input, make the requested git refs available, or omit "
                    "--base/--head for local uncommitted changes; then rerun shipgate check."
                ),
            ),
            allowed_next_commands=[command],
        ),
        repair={
            "actor": "coding_agent",
            "safe_to_attempt": True,
            "instructions": [
                f"Resolve diff input error: {error}",
                "Provide both --base and --head for committed refs, or omit both for local work.",
                "If --diff names a file, make sure the file exists and contains a unified diff.",
            ],
            "command": command,
            "forbidden_shortcuts": [
                "Do not claim completion without a successful shipgate check rerun.",
                "Do not infer a Shipgate decision from prose or a failed command.",
            ],
        },
        policy={
            "id": "unresolved",
            "version": "unknown",
            "source": "missing",
            "discovery": [],
        },
        diagnostics=[
            {
                "level": "error",
                "code": "diff_input_unresolved",
                "message": error,
            }
        ],
        trace=[
            {
                "step": "diff",
                "summary": "Diff resolution failed before boundary-policy evaluation.",
            }
        ],
        source_artifacts={},
    )


def _rerun_command(
    *,
    agent: str,
    diff: str | None,
    base: str | None,
    head: str | None,
) -> str:
    parts = ["shipgate", "check", "--agent", agent, "--workspace", "."]
    if diff:
        parts.extend(["--diff", diff])
    elif base and head:
        parts.extend(["--base", base, "--head", head])
    parts.extend(["--format", "codex-boundary-json"])
    return " ".join(parts)
