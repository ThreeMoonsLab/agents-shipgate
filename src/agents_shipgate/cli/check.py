from __future__ import annotations

import hashlib
import json
import os
import shlex
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
from agents_shipgate.cli.verify.git import commit_sha
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_controls import git_root_for
from agents_shipgate.core.bounded_io import (
    MAX_EXPLICIT_DIFF_BYTES,
    read_bounded_utf8_file,
    read_bounded_utf8_stdin,
)
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.schemas.agent_boundary import (
    AGENT_BOUNDARY_RESULT_SCHEMA_VERSION,
    AgentBoundaryResultV1,
)
from agents_shipgate.schemas.agent_control import (
    CodingAgentFetchBaseAction,
    HumanControlAction,
)
from agents_shipgate.schemas.codex_boundary_result import (
    CODEX_BOUNDARY_RESULT_SCHEMA_VERSION,
    CodexBoundaryResultV2,
)
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
    boundary question than the one that failed.

    Every interpolated value is shell-quoted. These are user-controlled paths
    and refs, and this string is published as an *authorized* command: a
    semicolon in ``--diff`` would otherwise become a command boundary in it.

    A half-specified ref range has two repairs — supply the missing ref, or
    drop both and check the working tree. The choice is not ours to make:
    silently dropping a range the caller asked for answers a different
    question, so the caller gets ``None`` and a review action.

    ``None`` also when the diff was read from stdin and cannot be replayed.
    """

    has_diff = diff is not None
    has_base = base is not None
    has_head = head is not None
    if diff in {"", "-"}:
        return None
    if has_diff and (has_base or has_head):
        return None
    if has_base != has_head:
        return None
    if has_base and (not base or not head):
        return None
    parts = ["agents-shipgate", "check"]
    if agent in {"codex", "claude-code", "cursor"}:
        parts.extend(["--agent", agent])
    parts.extend(["--workspace", shlex.quote(_cwd_anchored(workspace))])
    parts.extend(["--config", shlex.quote(str(config))])
    if policy is not None:
        parts.extend(["--policy", shlex.quote(str(policy))])
    if has_diff:
        assert diff is not None
        parts.extend(["--diff", shlex.quote(_cwd_anchored(Path(diff)))])
    elif has_base and has_head:
        assert base is not None and head is not None
        parts.extend(["--base", shlex.quote(base), "--head", shlex.quote(head)])
    valid_format = format_ if format_ == "codex-boundary-json" else "agent-boundary-json"
    parts.extend(["--format", valid_format])
    return " ".join(parts)


def _cwd_anchored(path: Path) -> str:
    """Return an absolute lexical spelling without resolving symlinks.

    Generated commands must replay from any working directory, while a path
    such as ``link/../input.diff`` must retain its original traversal
    semantics. ``Path.resolve``/``abspath`` would silently change that subject.
    """

    return str(path if path.is_absolute() else Path.cwd() / path)


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
    if agent is None:
        agent = detect_actor()

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

    if diff is not None and (base is not None or head is not None):
        raise _flag_error(
            "--diff cannot be combined with --base or --head.",
            command=None,
            expects="Choose one complete diff input: --diff, both refs, or the worktree.",
        )
    if diff == "":
        raise _flag_error(
            "--diff must name a file or '-' for stdin; it cannot be empty.",
            command=None,
            expects="A non-empty diff path, stdin, a complete ref range, or the worktree.",
        )
    if (base is None) != (head is None) or (
        base is not None and (not base or not head)
    ):
        raise _flag_error(
            "--base and --head must be provided together and cannot be empty.",
            command=None,
            expects="Both non-empty refs, or neither for local worktree changes.",
        )
    if any(
        value is not None
        and (
            value.startswith("-")
            or any(char in value for char in "\0\r\n")
        )
        for value in (base, head)
    ):
        raise _flag_error(
            "--base and --head must not begin with '-' or contain control delimiters.",
            command=None,
            expects="Choose exact, option-safe Git refs and rerun the request.",
        )
    if agent not in {"codex", "claude-code", "cursor"}:
        raise _flag_error(
            "--agent must be one of: codex, claude-code, cursor.",
            command=None,
            expects=(
                "Choose the intended caller identity explicitly; an unknown "
                "--agent value cannot be corrected without changing attribution."
            ),
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
        changed_files_override: list[str] | None = None
        manifest_text_snapshot: str | None = None
        manifest_snapshot_captured = False
        if diff == "-":
            diff_text = read_bounded_utf8_stdin(
                max_bytes=MAX_EXPLICIT_DIFF_BYTES,
                label="Diff input",
            )
        elif diff:
            diff_text = read_bounded_utf8_file(
                Path(diff),
                max_bytes=MAX_EXPLICIT_DIFF_BYTES,
                label="Diff input",
            )
        else:
            change_set = git_boundary_change_set(
                workspace=workspace,
                base=base,
                head=head,
                config_path=config,
            )
            diff_text = change_set.diff_text
            input_issues = list(change_set.issues)
            changed_files_override = list(change_set.changed_paths)
            manifest_text_snapshot = change_set.manifest_text_snapshot
            manifest_snapshot_captured = True
    except InputParseError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            result = _diff_input_error_result(
                agent=agent,
                workspace=workspace,
                config=config,
                policy=policy,
                format_=format_,
                diff=diff,
                base=base,
                head=head,
                error_class=type(exc.__cause__).__name__,
                error=str(exc),
            )
            if format_ == "agent-boundary-json":
                result = _neutral_diff_input_error(
                    result,
                    agent=agent,
                    base=base,
                    diff=diff,
                )
            typer.echo(agent_result_json(result))
            return
        raise _flag_error(
            str(exc),
            command=None,
            expects=(
                "Resolve the exact deterministic Git/diff or manifest-identity "
                "failure reported by this check before rerunning it."
            ),
        ) from exc
    except ConfigError as exc:
        raise _flag_error(
            str(exc),
            command=None,
            expects=(
                "Resolve the exact deterministic Git/diff or manifest-identity "
                "failure reported by this check before rerunning it."
            ),
        ) from exc
    except (OSError, RuntimeError) as exc:
        result = _diff_input_error_result(
            agent=agent,
            workspace=workspace,
            config=config,
            policy=policy,
            format_=format_,
            diff=diff,
            base=base,
            head=head,
            error_class=type(exc).__name__,
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
        "workspace": git_root_for(workspace) or workspace.resolve(),
        "requested_workspace": workspace,
        "diff_text": diff_text,
        "config": config,
        "policy": (
            policy
            if policy is None or policy.is_absolute()
            else workspace.resolve() / policy
        ),
        "input_issues": input_issues,
        "base": base,
        "head": head,
        "input_mode": "provided_diff" if diff else "git_range" if base else "worktree",
        # A standalone diff can describe either side of a change, but verify
        # accepts a checkout or a ref range. Do not authorize a worktree verify
        # for a subject the check cannot bind to repository state.
        "verification_replayable": diff is None,
        "changed_files_override": changed_files_override,
    }
    if manifest_snapshot_captured:
        kwargs["manifest_text_snapshot"] = manifest_text_snapshot
    try:
        result = builder(**kwargs)
    except ConfigError as exc:
        raise _flag_error(
            str(exc),
            command=None,
            expects=(
                "A configured manifest path that uses its exact stored "
                "filesystem spelling and contains no symlink components."
            ),
        ) from exc
    typer.echo(agent_result_json(result))


def _diff_input_error_result(
    *,
    agent: str,
    workspace: Path,
    config: Path,
    policy: Path | None,
    format_: str,
    diff: str | None,
    base: str | None,
    head: str | None,
    error_class: str,
    error: str,
) -> CodexBoundaryResultV2:
    summary = "Agents Shipgate could not resolve the diff input for local agent control."
    if diff is not None:
        route_why = (
            f"Review or restore the requested diff artifact {diff!r}; it could not "
            "be read, so rerunning the same check request cannot repair the input."
        )
        next_action: CodingAgentFetchBaseAction | HumanControlAction = HumanControlAction(
            kind="review",
            why=route_why,
        )
        repair_actor = "human"
    elif base is not None and head is not None:
        root = git_root_for(workspace)
        refs_available = (
            root is not None
            and commit_sha(root, base) is not None
            and commit_sha(root, head) is not None
        )
        if refs_available:
            route_why = (
                "Both requested refs are already available, so fetching cannot "
                f"repair the diff-collection failure: {error}. Review the "
                "reported deterministic Git input/configuration issue."
            )
            next_action = HumanControlAction(kind="review", why=route_why)
            repair_actor = "human"
        else:
            expected_refs = f"{base} and {head}"
            route_why = (
                f"Make both requested git refs available in workspace {workspace} before "
                "rerunning the check; the failed check does not fetch refs itself."
            )
            next_action = CodingAgentFetchBaseAction(
                kind="fetch_base",
                expects=expected_refs,
                why=route_why,
            )
            repair_actor = "coding_agent"
    else:
        route_why = (
            f"Review workspace {workspace} and restore a readable git worktree "
            "before rerunning the check; repeating the failed request cannot repair it."
        )
        next_action = HumanControlAction(kind="review", why=route_why)
        repair_actor = "human"

    human_route = isinstance(next_action, HumanControlAction)
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
        audit_id=_diff_input_error_audit_id(
            agent=agent,
            workspace=workspace,
            config=config,
            policy=policy,
            output_schema=(
                AGENT_BOUNDARY_RESULT_SCHEMA_VERSION
                if format_ == "agent-boundary-json"
                else CODEX_BOUNDARY_RESULT_SCHEMA_VERSION
            ),
            diff=diff,
            base=base,
            head=head,
            error_class=error_class,
        ),
        policy_version="unresolved",
        summary=summary,
        changed_files=[],
        control=derive_agent_control(
            reason=summary,
            next_action=next_action,
            human_review_required=human_route,
            human_review_why=route_why if human_route else None,
            stop_reason=route_why if human_route else None,
        ),
        repair={
            "actor": repair_actor,
            # A fetch request or human review route has no exact executable
            # repair. Keep the compatibility field false instead of claiming
            # that replaying the failed check can fix its own missing input.
            "safe_to_attempt": False,
            "instructions": [
                f"Resolve diff input error: {error}",
                route_why,
                "Rerun the original check only after the requested input is available.",
            ],
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


def _diff_input_error_audit_id(
    *,
    agent: str,
    workspace: Path,
    config: Path,
    policy: Path | None,
    output_schema: str,
    diff: str | None,
    base: str | None,
    head: str | None,
    error_class: str,
) -> str:
    """Stable, actor- and target-bound identity for a failed check input."""

    try:
        target = str(workspace.resolve())
    except OSError:
        target = str(workspace)
    input_kind = (
        "provided_diff"
        if diff is not None
        else "git_range"
        if base is not None or head is not None
        else "worktree"
    )
    requested_workspace = Path(_cwd_anchored(workspace))
    config_identity = (
        config if config.is_absolute() else requested_workspace / config
    )
    policy_identity = (
        None
        if policy is None
        else policy if policy.is_absolute() else requested_workspace / policy
    )
    diff_identity = (
        None if diff is None else _cwd_anchored(Path(diff))
    )
    payload = {
        "schema": output_schema,
        "kind": "diff_input_error",
        "actor": agent,
        "workspace": target,
        "config": os.fspath(config_identity),
        "policy": os.fspath(policy_identity) if policy_identity is not None else None,
        "input_kind": input_kind,
        "diff": diff_identity,
        "base": base,
        "head": head,
        "error_class": error_class,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"agent_boundary_error_{digest}"


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
