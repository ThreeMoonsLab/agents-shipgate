from __future__ import annotations

import shlex
import sys
from pathlib import Path

import typer

from agents_shipgate.cli.agent_mode import (
    detect_actor,
    emit_agent_mode_error_action,
)
from agents_shipgate.cli.agent_result import (
    agent_result_json,
    build_agent_boundary_result,
    build_codex_agent_result,
    git_boundary_change_set,
)
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.schemas.agent_boundary import AgentBoundaryResultV1
from agents_shipgate.schemas.agent_control import CodingAgentCommandAction
from agents_shipgate.schemas.codex_boundary_result import CodexBoundaryResultV2
from agents_shipgate.schemas.diagnostics import NextAction


def _corrected_request(
    *,
    agent: str | None,
    workspace: Path,
    config: Path,
    policy: Path | None,
    diff: str | None,
    base: str | None,
    head: str | None,
    format_: str,
) -> str | None:
    """The same check request with only the invalid field corrected.

    A fixed ``agents-shipgate check --format agent-boundary-json`` discards
    every other argument, so following the rank-1 action switched actor,
    workspace, config, policy, and diff/range context — answering a different
    boundary question than the one that failed. ``None`` when the request read
    its diff from stdin and therefore cannot be replayed.
    """

    if diff == "-":
        return None
    parts = ["agents-shipgate", "check"]
    if agent in {"codex", "claude-code", "cursor"}:
        parts.extend(["--agent", agent])
    parts.extend(["--workspace", shlex.quote(str(workspace))])
    parts.extend(["--config", shlex.quote(str(config))])
    if policy is not None:
        parts.extend(["--policy", shlex.quote(str(policy))])
    if diff:
        parts.extend(["--diff", shlex.quote(diff)])
    elif base and head:
        parts.extend(["--base", shlex.quote(base), "--head", shlex.quote(head)])
    valid_format = format_ if format_ == "codex-boundary-json" else "agent-boundary-json"
    parts.extend(["--format", valid_format])
    return " ".join(parts)


def _flag_error(message: str, *, command: str | None, expects: str) -> typer.Exit:
    """Report flag misuse on both channels and return the exit to raise."""

    typer.echo(message, err=True)
    action = (
        NextAction(kind="command", command=command, why=message, expects=expects)
        if command
        else NextAction(kind="review", why=message, expects=expects)
    )
    emit_agent_mode_error_action(
        "config_error",
        message=message,
        exit_code=2,
        action=action,
    )
    return typer.Exit(2)


def check(
    agent: str | None = typer.Option(
        None,
        "--agent",
        help=(
            "Agent runtime to check: codex, claude-code, or cursor. Detected "
            "from the environment when omitted; codex when undetectable."
        ),
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
        "agent-boundary-json",
        "--format",
        help="Output format. Supports agent-boundary-json and deprecated codex-boundary-json.",
    ),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace root containing coding-agent boundary surfaces.",
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
        help="Optional unified agent boundary policy. Defaults to workspace policy then packaged policy families.",
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

    # The actor lands in the result and in the audit id, so an undetected
    # harness mislabels every row it writes. An explicit flag always wins.
    agent = agent or detect_actor()

    def corrected(*, valid_agent: str | None) -> str | None:
        return _corrected_request(
            agent=valid_agent,
            workspace=workspace,
            config=config,
            policy=policy,
            diff=diff,
            base=base,
            head=head,
            format_=format_,
        )

    if agent not in {"codex", "claude-code", "cursor"}:
        raise _flag_error(
            "--agent must be one of: codex, claude-code, cursor.",
            command=corrected(valid_agent=detect_actor()),
            expects="An --agent value the boundary evaluator recognizes.",
        )
    if format_ == "agent-json":
        raise _flag_error(
            "--format agent-json was removed in the 0.14.0 contract cleanup. "
            "Use --format agent-boundary-json.",
            command=corrected(valid_agent=agent),
            expects="The current agent-boundary result contract.",
        )
    if format_ not in {"agent-boundary-json", "codex-boundary-json"}:
        raise _flag_error(
            "--format must be 'agent-boundary-json' or 'codex-boundary-json'.",
            command=corrected(valid_agent=agent),
            expects="The current agent-boundary result contract.",
        )
    try:
        input_issues = []
        if diff == "-":
            diff_text = sys.stdin.read()
        elif diff:
            diff_text = Path(diff).read_text(encoding="utf-8")
        else:
            change_set = git_boundary_change_set(
                workspace=workspace,
                base=base,
                head=head,
                config_path=config,
            )
            diff_text = change_set.diff_text
            input_issues = list(change_set.issues)
    except (OSError, RuntimeError) as exc:
        result = _diff_input_error_result(
            agent=agent,
            workspace=workspace,
            diff=diff,
            base=base,
            head=head,
            error=str(exc) or "diff input could not be resolved",
        )
        if format_ == "agent-boundary-json":
            result = _neutral_diff_input_error(result, agent=agent, base=base, diff=diff)
        typer.echo(agent_result_json(result))
        return

    builder = (
        build_agent_boundary_result
        if format_ == "agent-boundary-json"
        else build_codex_agent_result
    )
    kwargs = {
        "agent": agent,
        "workspace": workspace,
        "diff_text": diff_text,
        "config": config,
        "policy": policy,
        "input_issues": input_issues,
    }
    if format_ == "agent-boundary-json":
        kwargs["input_mode"] = "provided_diff" if diff else "git_range" if base else "worktree"
    result = builder(**kwargs)
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
    parts.extend(["--format", "agent-boundary-json"])
    return " ".join(parts)


def _neutral_diff_input_error(
    legacy: CodexBoundaryResultV2,
    *,
    agent: str,
    base: str | None,
    diff: str | None,
) -> AgentBoundaryResultV1:
    return AgentBoundaryResultV1(
        **legacy.model_dump(mode="python", exclude={"schema_version"}),
        actor=agent,  # type: ignore[arg-type]
        input_mode="provided_diff" if diff else "git_range" if base else "worktree",
        input_coverage="unknown",
        host_coverage=[],
        affected_hosts=[],
        policies=[legacy.policy],
        policy_set_sha256="0" * 64,
        issues=["diff_input_unresolved"],
        violations=list(legacy.violated_rules),
        excluded_scopes=["runtime_tool_behavior"],
    )
