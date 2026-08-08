from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.checks.verify import TRUST_ROOT_SURFACES
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.trust_roots import inspect_lexical_path_identity

SETTINGS_RELATIVE_PATH = Path(".claude/settings.json")
HOOK_SCRIPT_RELATIVE_PATH = Path(".claude/hooks/agents-shipgate.py")
SUPPORTED_TARGET = "claude-code"
STOP_HOOK_TIMEOUT_SECONDS = 180
PRETOOLUSE_HOOK_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class InstallHooksResult:
    target: str
    workspace: str
    write: bool
    settings_path: str
    script_path: str
    settings_status: str
    script_status: str
    hooks: list[dict[str, str]]
    settings: dict[str, Any]
    script: str

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "workspace": self.workspace,
            "write": self.write,
            "settings_path": self.settings_path,
            "script_path": self.script_path,
            "settings_status": self.settings_status,
            "script_status": self.script_status,
            "hooks": self.hooks,
            "settings": self.settings,
            "script": self.script,
        }


def install_hooks(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace where project-scoped hook files should be rendered.",
    ),
    target: str = typer.Option(
        SUPPORTED_TARGET,
        "--target",
        help="Hook target to install. Supported: claude-code.",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="Write .claude/settings.json and .claude/hooks/agents-shipgate.py.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the rendered settings/script and statuses as JSON.",
    ),
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        help="Manifest path passed to the Stop verifier hook.",
    ),
    base: str = typer.Option(
        "origin/main",
        "--base",
        help="Default base ref passed to trigger/verify from the hook.",
    ),
    head: str = typer.Option(
        "",
        "--head",
        help=(
            "Optional head ref passed to verify from the Stop hook. Omit for "
            "local working-tree verification."
        ),
    ),
    ci_mode: str = typer.Option(
        "advisory",
        "--ci-mode",
        help="Verifier CI mode used by the Stop hook. Hooks are advisory; CI is authoritative.",
    ),
) -> None:
    """Install advisory local hooks for supported coding-agent runtimes."""

    try:
        result = render_or_install_hooks(
            workspace=workspace,
            target=target,
            write=write,
            config=config,
            base=base,
            head=head,
            ci_mode=ci_mode,
        )
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(2) from exc

    if json_output:
        typer.echo(json.dumps(result.to_json(), indent=2))
        return

    verb = "Wrote" if write else "Would write"
    typer.echo(f"{verb} Agents Shipgate hooks for {result.target}:")
    typer.echo(f"- {result.settings_path}: {result.settings_status}")
    typer.echo(f"- {result.script_path}: {result.script_status}")
    typer.echo(
        "Hooks are advisory local feedback. CI remains authoritative for release gating."
    )


def render_or_install_hooks(
    *,
    workspace: Path,
    target: str,
    write: bool,
    config: Path,
    base: str,
    head: str,
    ci_mode: str,
) -> InstallHooksResult:
    if target != SUPPORTED_TARGET:
        raise ConfigError(
            f"Unsupported hook target {target!r}. Supported targets: {SUPPORTED_TARGET}."
        )
    if ci_mode not in {"advisory", "strict"}:
        raise ConfigError("--ci-mode must be advisory or strict")
    for label, value in (("--base", base), ("--head", head)):
        if value and not _safe_ref_token(value):
            raise ConfigError(
                f"{label} must not begin with '-' or contain control delimiters"
            )
    if head and not base:
        raise ConfigError(
            "--head requires --base for installed hooks; omit --head for "
            "working-tree verification"
        )
    workspace = workspace.resolve()
    config_relative = _exact_hook_config(workspace, config)
    settings_path = workspace / SETTINGS_RELATIVE_PATH
    script_path = workspace / HOOK_SCRIPT_RELATIVE_PATH
    _ensure_repo_local_write(settings_path, workspace)
    _ensure_repo_local_write(script_path, workspace)

    existing_settings = _read_settings(settings_path)
    rendered_settings = _merge_claude_settings(
        existing_settings,
        config=config_relative,
        base=base,
        head=head,
        ci_mode=ci_mode,
    )
    rendered_script = _hook_script_text()
    settings_text = _settings_text(rendered_settings)
    script_text = rendered_script if rendered_script.endswith("\n") else rendered_script + "\n"

    settings_status = _status(settings_path, settings_text, write=write)
    script_status = _status(script_path, script_text, write=write)
    if write:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(settings_path, settings_text)
        _write_text_atomic(script_path, script_text)

    return InstallHooksResult(
        target=target,
        workspace=str(workspace),
        write=write,
        settings_path=str(settings_path),
        script_path=str(script_path),
        settings_status=settings_status,
        script_status=script_status,
        hooks=[
            {
                "event": "PreToolUse",
                "matcher": "Edit|Write|MultiEdit",
                "purpose": (
                    "route edits to protected trust-root surfaces to the "
                    "human for permission before they happen"
                ),
            },
            {
                "event": "PostToolUse",
                "matcher": "Edit|Write|MultiEdit",
                "purpose": "cheap trigger check after file-editing tools",
            },
            {
                "event": "Stop",
                "purpose": "full verify at relevant completion boundaries",
            },
        ],
        settings=rendered_settings,
        script=script_text,
    )


def _exact_hook_config(workspace: Path, config: Path) -> str:
    """Return one stable repository-relative manifest identity for the hook."""

    raw = config if config.is_absolute() else workspace / config
    candidate = Path(os.path.abspath(os.path.normpath(os.fspath(raw))))
    try:
        relative = candidate.relative_to(workspace)
    except ValueError as exc:
        raise ConfigError("--config must stay inside --workspace") from exc
    issue = inspect_lexical_path_identity(workspace, relative)
    if issue is not None:
        raise ConfigError(
            "--config must use one exact non-symlink filesystem identity"
        )
    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        return relative.as_posix()
    except OSError as exc:
        raise ConfigError("--config could not be inspected safely") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ConfigError(
            "--config must identify one singly-linked regular file"
        )
    return relative.as_posix()


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise ConfigError(f"{path} exists but is not a file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read Claude Code settings JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def _safe_ref_token(value: str) -> bool:
    return bool(value) and not value.startswith("-") and not any(
        char in value for char in "\0\r\n"
    )


def _merge_claude_settings(
    settings: dict[str, Any],
    *,
    config: str,
    base: str,
    head: str,
    ci_mode: str,
) -> dict[str, Any]:
    merged = json.loads(json.dumps(settings))
    hooks = merged.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ConfigError(".claude/settings.json field 'hooks' must be an object")
    _replace_event_hook(
        hooks,
        "PreToolUse",
        _matcher_group(
            matcher="Edit|Write|MultiEdit",
            mode="pretooluse",
            timeout=PRETOOLUSE_HOOK_TIMEOUT_SECONDS,
            config=config,
            base=base,
            head=head,
            ci_mode=ci_mode,
        ),
    )
    _replace_event_hook(
        hooks,
        "PostToolUse",
        _matcher_group(
            matcher="Edit|Write|MultiEdit",
            mode="trigger",
            timeout=15,
            config=config,
            base=base,
            head=head,
            ci_mode=ci_mode,
        ),
    )
    _replace_event_hook(
        hooks,
        "Stop",
        _matcher_group(
            matcher=None,
            mode="verify",
            timeout=STOP_HOOK_TIMEOUT_SECONDS,
            config=config,
            base=base,
            head=head,
            ci_mode=ci_mode,
        ),
    )
    return merged


def _replace_event_hook(
    hooks: dict[str, Any],
    event: str,
    desired_group: dict[str, Any],
) -> None:
    groups = hooks.get(event, [])
    if not isinstance(groups, list):
        raise ConfigError(f".claude/settings.json hooks.{event} must be an array")
    cleaned: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            raise ConfigError(f".claude/settings.json hooks.{event} entries must be objects")
        handlers = group.get("hooks", [])
        if not isinstance(handlers, list):
            raise ConfigError(f".claude/settings.json hooks.{event}[].hooks must be an array")
        remaining = [
            handler
            for handler in handlers
            if not _is_shipgate_handler(handler, event)
        ]
        if remaining:
            kept = dict(group)
            kept["hooks"] = remaining
            cleaned.append(kept)
    cleaned.append(desired_group)
    hooks[event] = cleaned


def _matcher_group(
    *,
    matcher: str | None,
    mode: str,
    timeout: int,
    config: str,
    base: str,
    head: str,
    ci_mode: str,
) -> dict[str, Any]:
    args = [
        "${CLAUDE_PROJECT_DIR}/.claude/hooks/agents-shipgate.py",
        mode,
        "--config",
        config,
        "--base",
        base,
    ]
    if head:
        args.extend(["--head", head])
    args.extend(["--ci-mode", ci_mode])
    group: dict[str, Any] = {
        "hooks": [
            {
                "type": "command",
                "command": "python3",
                "args": args,
                "timeout": timeout,
            }
        ],
    }
    if matcher is not None:
        group["matcher"] = matcher
    return group


def _is_shipgate_handler(handler: object, event: str) -> bool:
    if not isinstance(handler, dict):
        return False
    args = handler.get("args")
    if not isinstance(args, list) or len(args) < 2:
        return False
    script = str(args[0])
    mode = str(args[1])
    expected_mode = {
        "PreToolUse": "pretooluse",
        "PostToolUse": "trigger",
    }.get(event, "verify")
    return (
        script.endswith(".claude/hooks/agents-shipgate.py")
        and mode == expected_mode
    )


def _settings_text(settings: dict[str, Any]) -> str:
    return json.dumps(settings, indent=2) + "\n"


def _status(path: Path, content: str, *, write: bool) -> str:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return "unchanged"
    return "written" if write else "would_write"


def _ensure_repo_local_write(path: Path, workspace: Path) -> None:
    try:
        path.resolve().relative_to(workspace)
    except ValueError as exc:
        raise ConfigError(f"Hook path resolves outside workspace: {path}") from exc
    symlink = _first_symlink_in_chain(path, workspace)
    if symlink is not None:
        raise ConfigError(f"{symlink} is a symlink; refusing to write hook files")


def _first_symlink_in_chain(path: Path, workspace: Path) -> Path | None:
    workspace_real = workspace.resolve()
    try:
        parts = path.relative_to(workspace_real).parts
    except ValueError:
        return path
    cur = workspace_real
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            return cur
        if not cur.exists():
            return None
    return None


def _write_text_atomic(path: Path, content: str) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _hook_script_text() -> str:
    # The protected-surface list is rendered at install time from the
    # same TRUST_ROOT_SURFACES the verify check classifies against, so
    # the in-session boundary and the PR-time gate can never drift.
    surfaces = json.dumps(
        [[kind, pattern] for kind, pattern in TRUST_ROOT_SURFACES],
        indent=4,
    )
    return _HOOK_SCRIPT_TEMPLATE.replace("__PROTECTED_SURFACES_JSON__", surfaces)


_HOOK_SCRIPT_TEMPLATE = r'''#!/usr/bin/env python3
"""Advisory Claude Code hook runner for Agents Shipgate.

Generated by `agents-shipgate install-hooks --target claude-code`.
It is local-only, deterministic, and advisory. CI remains authoritative.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any


VERIFY_TIMEOUT_SECONDS = 170
UNTRACKED_DIFF_CONTENT_LIMIT_BYTES = 131072
GIT_PATH_OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024
GIT_DIFF_OUTPUT_LIMIT_BYTES = 32 * 1024 * 1024
_MAX_REMEMBERED_SURFACES = 256
_MAX_REMEMBERED_SESSIONS = 8
_SAFE_DIFF_CONFIG = [
    "-c", "core.fsmonitor=false",
    "-c", "core.autocrlf=false",
    "-c", "core.safecrlf=false",
    "-c", "core.eol=lf",
    "-c", "core.bigFileThreshold=32m",
    "-c", "core.fileMode=false",
    "-c", "core.precomposeUnicode=false",
    "-c", "submodule.recurse=false",
    "-c", "core.quotePath=false",
]
_DETERMINISTIC_DIFF_OPTIONS = [
    "--no-ext-diff",
    "--no-textconv",
    "--ignore-submodules=dirty",
    "--no-color",
    "--diff-algorithm=myers",
    "--no-indent-heuristic",
    "--unified=3",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--find-renames=50%",
    "--submodule=short",
    "--full-index",
]
# Host permission modes that answer a hook's permission request without asking
# a human. An edit that lands under one of these is not evidence that anyone
# saw a prompt, so it must never seed the approval memory. Every other mode
# (including acceptEdits, where an explicit hook "ask" still prompts) does
# surface the request. An absent mode is treated as unknown and records
# nothing.
_UNPROMPTED_PERMISSION_MODES = frozenset({"bypasspermissions", "dontask"})

# Rendered at install time from agents_shipgate.checks.verify
# TRUST_ROOT_SURFACES — the same classification the PR-time verifier
# uses. Each entry is [trust_root_class, glob_pattern].
PROTECTED_SURFACES = __PROTECTED_SURFACES_JSON__


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("trigger", "verify", "pretooluse"))
    parser.add_argument("--config", default="shipgate.yaml")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="")
    parser.add_argument("--ci-mode", default="advisory")
    args = parser.parse_args()

    payload = _read_payload()
    root = _project_root(payload)
    if args.mode == "pretooluse":
        return _pretooluse(payload, root, args)
    if args.mode == "trigger":
        return _trigger(payload, root, args)
    return _verify(payload, root, args)


def _pretooluse(
    payload: dict[str, Any],
    root: Path,
    args: argparse.Namespace,
) -> int:
    """Surface the authority boundary BEFORE a protected file is edited.

    When the agent is about to Edit/Write a trust-root surface (the
    manifest, policies, Shipgate CI, agent instructions, MCP/host
    config), route the call to the human for permission instead of
    letting the edit land and tripping the PR gate later. Default
    decision is "ask" (the human approves in-session — the same
    authority semantics as merge_verdict=human_review_required); set
    AGENTS_SHIPGATE_PRETOOLUSE_DECISION=deny for hard blocking or
    =allow to disable without uninstalling.
    """
    decision = os.environ.get(
        "AGENTS_SHIPGATE_PRETOOLUSE_DECISION", "ask"
    ).strip().lower()
    if decision not in {"ask", "deny"}:
        return 0
    # ``deny`` is a hard block chosen by the operator, not a prompt. A prior
    # in-session approval may quiet a repeated *prompt*; it must never quiet a
    # deny.
    session_id = str(payload.get("session_id") or "")
    already_approved = (
        _approved_surfaces(root, session_id) if decision == "ask" else frozenset()
    )
    contained = _contained_repo_paths(payload, root)
    matched: list[tuple[str, str, str]] = []
    for path in _changed_paths(payload, root):
        hit = _protected_surface_for(path, configured_manifest=args.config)
        alias_kind = _unsafe_alias_kind(root, path)
        if hit is None and alias_kind is not None:
            hit = ("path_identity", alias_kind)
        if hit is None:
            continue
        # Only a path proven to be inside this repository can be matched
        # against the memory: an out-of-workspace absolute path degrades to its
        # basename, and `/elsewhere/shipgate.yaml` must not authorize the
        # repository's own manifest.
        if path in already_approved and path in contained:
            continue
        matched.append((path, hit[0], hit[1]))
    if not matched:
        return 0
    preview = "; ".join(
        f"{path} (protected {kind} surface, pattern {pattern})"
        for path, kind, pattern in matched[:3]
    )
    reason = (
        "Agents Shipgate protects this surface: "
        f"{preview}. Changes here alter what agents are allowed to do and "
        "route to human review at PR time (suppression-immune "
        "SHIP-VERIFY-* checks). If the change is intended, have the human "
        "approve this edit; never weaken the manifest, policies, CI gate, "
        "or agent instructions just to make a verifier verdict pass."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason[:9500],
                }
            }
        )
    )
    return 0


def _protected_surface_for(
    path: str,
    *,
    configured_manifest: str | None = None,
) -> tuple[str, str] | None:
    normalized = path.replace("\\", "/")
    if configured_manifest:
        configured = configured_manifest.replace("\\", "/").removeprefix("./")
        if normalized == configured or normalized.casefold() == configured.casefold():
            return "manifest", configured
    for kind, pattern in PROTECTED_SURFACES:
        if _glob_match(pattern, path) or _glob_match(
            pattern.casefold(), path.casefold()
        ):
            return kind, pattern
    return None


def _glob_match(pattern: str, path: str) -> bool:
    """Mirror of agents_shipgate.core.globbing.glob_match (keep in sync)."""
    import re as _re

    pattern = pattern.replace("\\", "/")
    path = path.replace("\\", "/")
    if not any(token in pattern for token in ("*", "?", "[")):
        return path == pattern

    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            parts.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("/**", i):
            parts.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            close = pattern.find("]", i + 1)
            if close == -1:
                parts.append(_re.escape(pattern[i]))
                i += 1
            else:
                parts.append(pattern[i : close + 1])
                i = close + 1
        else:
            parts.append(_re.escape(pattern[i]))
            i += 1
    return _re.fullmatch("".join(parts), path) is not None


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _project_root(payload: dict[str, Any]) -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    return Path(str(raw)).resolve()


def _cli() -> list[str]:
    raw = os.environ.get("AGENTS_SHIPGATE_CLI", "agents-shipgate").strip()
    return shlex.split(raw) if raw else ["agents-shipgate"]


def _trigger(payload: dict[str, Any], root: Path, args: argparse.Namespace) -> int:
    paths = _changed_paths(payload, root)
    if not paths:
        return 0
    _record_in_session_approvals(payload, root, args)
    diff_text = _git_diff_for_paths(root, paths)
    if diff_text is None:
        return _emit_context(
            "PostToolUse",
            "Agents Shipgate could not inspect the edited source text through "
            "the repository's static Git diff boundary. Before finishing, run "
            f"`{_manual_verify_command(args, root=root, worktree=True)}` manually.",
        )
    # For edit-time nudges, evaluate path relevance without the opted-in
    # manifest force-run rule. CI still runs every PR for opted-in repos.
    result = _run_trigger_for_paths(
        root,
        paths,
        diff_text=diff_text,
        manifest_present=False,
    )
    protected = any(
        _protected_surface_for(path, configured_manifest=args.config) is not None
        for path in paths
    )
    if result is None and not protected:
        return 0
    if not protected and not result.get("should_run"):
        return 0

    path_preview = ", ".join(paths[:3])
    rationale = (
        (result or {}).get("rationale")
        or "A configured protected surface changed."
    )
    if (root / args.config).is_file():
        command = _manual_verify_command(args, root=root, worktree=True)
    else:
        command = "AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate verify --preview --json"
    return _emit_context(
        "PostToolUse",
        (
            "Agents Shipgate trigger matched after editing "
            f"{path_preview}: {rationale} Before finishing, run `{command}` "
            "and read `agents-shipgate-reports/report.json.release_decision.decision`. "
            "Do not bypass the verifier by suppressing findings, lowering severity, "
            "expanding baselines/waivers, removing Shipgate CI, or weakening agent instructions."
        ),
    )


def _record_in_session_approvals(
    payload: dict[str, Any],
    root: Path,
    args: argparse.Namespace,
) -> None:
    """Note protected files whose edit the human just allowed.

    PostToolUse only fires once the tool call went through, so when the host
    surfaced this hook's permission request, an edit that landed is an edit a
    human allowed. Three conditions must all hold, and each failure means we
    have no evidence of a human decision:

    * the boundary was actually prompting — not disabled with ``allow`` (no
      request was made) and not ``deny`` (nothing was ever approvable);
    * the host was in a mode that asks rather than auto-answering;
    * the path is provably inside this repository, so a same-basename file
      elsewhere cannot authorize a protected repository path.
    """

    if (
        os.environ.get("AGENTS_SHIPGATE_PRETOOLUSE_DECISION", "ask").strip().lower()
        != "ask"
    ):
        return
    mode = str(payload.get("permission_mode") or payload.get("permissionMode") or "")
    mode = mode.strip().lower()
    if not mode or mode in _UNPROMPTED_PERMISSION_MODES:
        return
    protected = [
        path
        for path in _contained_repo_paths(payload, root)
        if _protected_surface_for(path, configured_manifest=args.config) is not None
    ]
    if protected:
        _remember_approved_surfaces(root, str(payload.get("session_id") or ""), protected)


def _contained_repo_paths(payload: dict[str, Any], root: Path) -> frozenset[str]:
    """Changed paths proven to resolve inside ``root``.

    ``_repo_path`` deliberately falls back to a bare filename for paths outside
    the workspace so the boundary still warns about them. That fallback must
    never feed the approval memory, where a basename collision would carry an
    approval from one file to a different one.
    """

    out: set[str] = set()
    for raw in _raw_changed_paths(payload):
        candidate = Path(raw)
        try:
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (root / candidate).resolve()
            )
            out.add(resolved.relative_to(root).as_posix())
        except (ValueError, OSError):
            continue
    return frozenset(out)


def _verify(payload: dict[str, Any], root: Path, args: argparse.Namespace) -> int:
    config_path = root / args.config
    stop_hook_active = bool(payload.get("stop_hook_active"))
    snapshot = _change_snapshot(root, args)
    if snapshot["kind"] == "unavailable":
        if snapshot.get("reason") == "base_ref_unavailable":
            return _emit_context(
                "Stop",
                "Agents Shipgate could not determine the committed change set "
                "because the configured base ref is unavailable locally. Fetch "
                "the base ref, then rerun the hook or run "
                f"`{_manual_verify_command(args, root=root)}` manually.",
            )
        return _emit_context(
            "Stop",
            "Agents Shipgate could not collect a bounded, static worktree "
            "snapshot. Commit the intended changes, then run the ref-bound "
            "verifier manually; the hook will not execute repository-configured "
            "filters or trust incomplete Git output.",
        )
    if not config_path.is_file():
        if not snapshot["paths"]:
            return 0
        trigger = _run_trigger_for_paths(
            root,
            snapshot["paths"],
            diff_text=snapshot["diff_text"],
            manifest_present=False,
        )
        protected = any(
            _protected_surface_for(path, configured_manifest=args.config) is not None
            for path in snapshot["paths"]
        )
        if protected or (trigger and trigger.get("should_run")):
            # Advisory: nothing is configured yet, so nobody has decided this
            # repo is gated — advise, never force the turn to continue.
            return _emit_context(
                "Stop",
                "Agents Shipgate trigger matched, but the configured manifest "
                f"{args.config!r} does not exist. "
                "Run `agents-shipgate verify --preview --json` and initialize "
                "the manifest if this workspace contains an agent.",
            )
        return 0

    if not snapshot["paths"]:
        return 0

    trigger = _run_trigger_for_paths(
        root,
        snapshot["paths"],
        diff_text=snapshot["diff_text"],
        manifest_present=False,
    )
    protected = any(
        _protected_surface_for(path, configured_manifest=args.config) is not None
        for path in snapshot["paths"]
    )
    if trigger is None and not protected:
        return _emit_context(
            "Stop",
            "Agents Shipgate hook could not evaluate the local trigger. Hooks are "
            "advisory; before finishing an agent-related diff, run "
            f"`{_manual_verify_command(args, root=root, worktree=snapshot['kind'] == 'worktree')}` manually.",
        )
    if not protected and not trigger.get("should_run"):
        return 0

    signature = str(snapshot["signature"])
    if _last_verified_signature(root) == signature:
        return 0

    base = os.environ.get("AGENTS_SHIPGATE_VERIFY_BASE", args.base).strip()
    head = os.environ.get("AGENTS_SHIPGATE_VERIFY_HEAD", args.head).strip()
    ci_mode = os.environ.get("AGENTS_SHIPGATE_VERIFY_CI_MODE", args.ci_mode)
    if (base and not _safe_ref_token(base)) or (head and not _safe_ref_token(head)):
        return _emit_context(
            "Stop",
            "Agents Shipgate refused an option-like or control-delimited Git "
            "ref. Set explicit, option-safe base/head refs before verification.",
        )
    base_note = ""
    if base and not _ref_exists(root, base):
        base_note = (
            f" Base ref {base!r} is not available locally, so the hook ran "
            "working-tree verification without base diff enrichment."
        )
        base = ""

    command = [
        *_cli(),
        "verify",
        "--workspace",
        str(root),
        "--config",
        args.config,
    ]
    if base:
        command.extend(["--base", base])
    if head and snapshot["kind"] != "worktree":
        command.extend(["--head", head])
    command.extend(["--ci-mode", ci_mode, "--format", "json"])
    env = {**os.environ, "AGENTS_SHIPGATE_AGENT_MODE": "1"}
    completed = _run(command, cwd=root, timeout=VERIFY_TIMEOUT_SECONDS, env=env)
    if completed is None:
        return _emit_context(
            "Stop",
            "Agents Shipgate verify could not start from the local hook. Hooks "
            "are advisory; check the hook Python/agents-shipgate PATH or set "
            "AGENTS_SHIPGATE_CLI, then run the verifier manually before "
            "finishing an agent-related diff.",
        )
    if completed.returncode not in {0, 20}:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()[:2]
        suffix = f" Details: {' '.join(detail)}" if detail else ""
        return _emit_context(
            "Stop",
            f"Agents Shipgate verify exited {completed.returncode}.{suffix} "
            "The local hook is advisory and did not block completion; run the "
            "verifier manually and resolve the structured next_action before "
            "finishing an agent-related diff.",
        )

    try:
        verifier = json.loads(completed.stdout or "")
    except json.JSONDecodeError:
        verifier = None
    if not isinstance(verifier, dict):
        # Never cache and never stay silent on unreadable verifier output: a
        # malfunctioning gate must not read as a passing one.
        return _emit_context(
            "Stop",
            "Agents Shipgate verify produced output the hook could not parse. "
            "Do not treat this as a passing verdict; run "
            f"`{_manual_verify_command(args, root=root, worktree=snapshot['kind'] == 'worktree')}` manually and read "
            "`agents-shipgate-reports/report.json`.",
        )

    return _route_verify_result(
        verifier,
        root=root,
        args=args,
        signature=signature,
        base_note=base_note,
        stop_hook_active=stop_hook_active,
        worktree=snapshot["kind"] == "worktree",
    )


def _route_verify_result(
    verifier: dict[str, Any],
    *,
    root: Path,
    args: argparse.Namespace,
    signature: str,
    base_note: str,
    stop_hook_active: bool,
    worktree: bool,
) -> int:
    decision = ((verifier.get("release_decision") or {}).get("decision") or "unknown")
    blockers = len((verifier.get("release_decision") or {}).get("blockers") or [])
    review_items = len((verifier.get("release_decision") or {}).get("review_items") or [])
    control = verifier.get("control") if isinstance(verifier.get("control"), dict) else {}
    state = control.get("state")
    summary = f"decision={decision}, blockers={blockers}, review_items={review_items}"

    # The hook mirrors the operational control contract: ``control.state`` is
    # authoritative and ``decision`` is diagnostic.  A Claude Code Stop-hook
    # "block" forces the agent to KEEP WORKING, so it is only ever correct
    # when an exact coding-agent action remains.  Both human routes are the
    # opposite situation — the turn must be allowed to end so a person can
    # take over — but they differ in what stays authorized meanwhile:
    # ``review_publishable`` keeps commit/push/update-PR, and
    # ``human_review_required`` (``must_stop=true``) keeps nothing.
    if state == "complete":
        _write_verified_signature(root, signature)
        if base_note:
            return _emit_context("Stop", "Agents Shipgate verify passed." + base_note)
        return 0
    if state == "agent_action_required":
        _write_verified_signature(root, signature)
        if stop_hook_active:
            return 0
        next_action = (
            control.get("next_action") if isinstance(control.get("next_action"), dict) else {}
        )
        action_kind = next_action.get("kind")
        command = next_action.get("command")
        allowed_commands = control.get("allowed_next_commands")
        allowed_commands = (
            allowed_commands
            if isinstance(allowed_commands, list)
            and all(isinstance(item, str) for item in allowed_commands)
            else []
        )
        why = next_action.get("why") or "One coding-agent action remains."
        if action_kind == "fetch_base" and not command:
            expects = next_action.get("expects") or "the requested Git ref"
            return _emit_stop_block(
                f"Agents Shipgate verify ran before completion: {summary}.{base_note} "
                f"Make {expects!r} available locally, then rerun the verifier. "
                f"{why} No executable command was authorized by the control result."
            )
        if not isinstance(command, str) or command not in allowed_commands:
            return _emit_context(
                "Stop",
                "Agents Shipgate returned agent_action_required without one exact "
                "authorized command. Do not invent or replay a fallback command; "
                "inspect control.next_action and allowed_next_commands, then route "
                "the malformed handoff to human review.",
            )
        return _emit_stop_block(
            f"Agents Shipgate verify ran before completion: {summary}.{base_note} "
            f"One exact coding-agent action remains before finishing: run `{command}`. "
            f"{why} "
            "Do not bypass the verifier by suppressing findings, lowering severity, "
            "expanding baselines/waivers, removing Shipgate CI, or weakening agent instructions."
        )
    if state == "review_publishable":
        # Human review gates the merge, not the pull request. Ending the turn
        # is still correct — the agent has no Shipgate work left — but say
        # plainly that committing, pushing, and updating the PR remain
        # authorized, because the whole point of this state is that a human
        # cannot review what was never published.
        _write_verified_signature(root, signature)
        human_review = control.get("human_review")
        why = (
            (human_review.get("why") if isinstance(human_review, dict) else None)
            or control.get("reason")
            or ""
        )
        commands = control.get("allowed_next_commands")
        publish = (
            commands[0]
            if isinstance(commands, list) and commands and isinstance(commands[0], str)
            else None
        )
        return _emit_context(
            "Stop",
            f"Agents Shipgate verify ran before completion: {summary}.{base_note} "
            "A human must review this change before it can merge"
            f"{': ' + why if why else ''}. "
            "Publishing the change for that review is still authorized: you may "
            "commit, push, and update the pull request. You may not merge it or "
            "report the task complete."
            + (f" After publishing, rerun `{publish}` to refresh the evidence." if publish else "")
            + " Details: `agents-shipgate-reports/report.json`.",
        )
    if state == "human_review_required":
        _write_verified_signature(root, signature)
        stop_reason = control.get("stop_reason") or control.get("reason") or ""
        return _emit_context(
            "Stop",
            f"Agents Shipgate verify ran before completion: {summary}.{base_note} "
            "A human must review this change before it can merge"
            f"{': ' + stop_reason if stop_reason else ''}. "
            "The coding agent's local work can end here; PR review is unchanged. "
            "Details: `agents-shipgate-reports/report.json`.",
        )
    # Unknown control state: warn loudly, never cache, never block.
    return _emit_context(
        "Stop",
        f"Agents Shipgate verify returned an unrecognized control state {state!r} "
        f"({summary}). Do not treat this as a passing verdict; read "
        "`agents-shipgate-reports/report.json` and treat unknown states as "
        "requiring human review.",
    )


def _manual_verify_command(
    args: argparse.Namespace,
    *,
    root: Path | None = None,
    worktree: bool = False,
) -> str:
    parts = [
        "AGENTS_SHIPGATE_AGENT_MODE=1",
        "agents-shipgate",
        "verify",
        "--workspace",
        ".",
        "--config",
        args.config,
    ]
    if args.base and (root is None or _ref_exists(root, args.base)):
        parts.extend(["--base", args.base])
    if args.head and not worktree:
        parts.extend(["--head", args.head])
    parts.extend(["--ci-mode", args.ci_mode, "--format", "json"])
    return " ".join(shlex.quote(str(part)) for part in parts)


def _run_trigger_for_paths(
    root: Path,
    paths: list[str],
    *,
    diff_text: str,
    manifest_present: bool,
) -> dict[str, Any] | None:
    if any(not _safe_changed_path_transport(path) for path in paths):
        return None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        for path in paths:
            handle.write(path + "\n")
        changed_path = Path(handle.name)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(diff_text)
        diff_path = Path(handle.name)
    try:
        command = [
            *_cli(),
            "trigger",
            "--workspace",
            str(root),
            "--changed-files",
            str(changed_path),
            "--diff",
            str(diff_path),
            "--manifest-present" if manifest_present else "--no-manifest-present",
            "--json",
        ]
        completed = _run(command, cwd=root, timeout=15)
        if completed is None or completed.returncode != 0:
            return None
        return json.loads(completed.stdout or "{}")
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        changed_path.unlink(missing_ok=True)
        diff_path.unlink(missing_ok=True)


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    return _run(
        ["git", "--no-replace-objects", "-C", str(root), *args],
        cwd=root,
        timeout=20,
        env=_git_environment(),
    )


def _git_environment() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    env.update(
        {
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def _run_git_bounded(
    root: Path,
    args: list[str],
    *,
    limit: int = 1024 * 1024,
) -> bytes | None:
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            ["git", "--no-replace-objects", "-C", str(root), *args],
            cwd=root,
            env=_git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None

    output = bytearray()
    exceeded = False
    read_failed = False

    def _drain() -> None:
        nonlocal exceeded, read_failed
        assert process is not None and process.stdout is not None
        try:
            while chunk := process.stdout.read(64 * 1024):
                remaining = limit + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(output) > limit:
                    exceeded = True
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
        except OSError:
            read_failed = True

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join()
        return None
    reader.join()
    if returncode != 0 or exceeded or read_failed:
        return None
    return bytes(output)


def _ref_exists(root: Path, ref: str) -> bool:
    if not _safe_ref_token(ref):
        return False
    completed = _run_git(
        root,
        [
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{ref}^{{commit}}",
        ],
    )
    return completed is not None and completed.returncode == 0


def _is_git_repository(root: Path) -> bool:
    completed = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    return (
        completed is not None
        and completed.returncode == 0
        and completed.stdout.strip() == "true"
    )


def _commit_for_ref(root: Path, ref: str) -> str | None:
    if not _safe_ref_token(ref):
        return None
    completed = _run_git(
        root,
        [
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{ref}^{{commit}}",
        ],
    )
    if completed is None or completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _change_snapshot(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    if _has_executable_worktree_filter(root):
        return {
            "kind": "unavailable",
            "paths": [],
            "diff_text": "",
            "signature": "",
        }
    paths = _worktree_changed_paths(root, configured_manifest=args.config)
    if paths is None:
        return {
            "kind": "unavailable",
            "paths": [],
            "diff_text": "",
            "signature": "",
        }
    if any(not _safe_changed_path_transport(path) for path in paths):
        return {
            "kind": "unavailable",
            "paths": [],
            "diff_text": "",
            "signature": "",
        }
    if paths:
        diff_text = _worktree_diff(root, paths)
        if diff_text is None:
            return {
                "kind": "unavailable",
                "paths": [],
                "diff_text": "",
                "signature": "",
            }
        return _snapshot(root, "worktree", paths, diff_text, args)

    base = os.environ.get("AGENTS_SHIPGATE_VERIFY_BASE", args.base).strip()
    configured_head = os.environ.get(
        "AGENTS_SHIPGATE_VERIFY_HEAD", args.head
    ).strip()
    if configured_head and not base:
        return {
            "kind": "unavailable",
            "reason": "head_without_base",
            "paths": [],
            "diff_text": "",
            "signature": "",
        }
    head = configured_head or "HEAD"
    if base:
        if not _ref_exists(root, base) or not _ref_exists(root, head):
            return {
                "kind": "unavailable",
                "reason": "base_ref_unavailable",
                "paths": [],
                "diff_text": "",
                "signature": "",
            }
        diff = _diff_context(root, f"{base}...{head}")
        if diff is not None:
            commit_paths, diff_text = diff
            if commit_paths:
                if any(
                    not _safe_changed_path_transport(path)
                    for path in commit_paths
                ):
                    return {
                        "kind": "unavailable",
                        "paths": [],
                        "diff_text": "",
                        "signature": "",
                    }
                return _snapshot(root, "commit", commit_paths, diff_text, args)
        else:
            return {
                "kind": "unavailable",
                "reason": "commit_diff_unavailable",
                "paths": [],
                "diff_text": "",
                "signature": "",
            }

    return {"kind": "none", "paths": [], "diff_text": "", "signature": ""}


def _snapshot(
    root: Path,
    kind: str,
    paths: list[str],
    diff_text: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    paths = sorted({path for path in paths if path})
    effective_base = os.environ.get(
        "AGENTS_SHIPGATE_VERIFY_BASE", args.base
    ).strip()
    effective_head = os.environ.get(
        "AGENTS_SHIPGATE_VERIFY_HEAD", args.head
    ).strip()
    effective_ci_mode = os.environ.get(
        "AGENTS_SHIPGATE_VERIFY_CI_MODE", args.ci_mode
    )
    payload = {
        "kind": kind,
        "config": args.config,
        "base": effective_base,
        "base_commit": _commit_for_ref(root, effective_base)
        if effective_base
        else None,
        "head": effective_head,
        "head_commit": _commit_for_ref(root, effective_head or "HEAD"),
        "ci_mode": effective_ci_mode,
        "cli": os.environ.get("AGENTS_SHIPGATE_CLI", "agents-shipgate"),
        "paths": paths,
        "diff_sha256": hashlib.sha256(diff_text.encode("utf-8")).hexdigest(),
    }
    signature = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "kind": kind,
        "paths": paths,
        "diff_text": diff_text,
        "signature": signature,
    }


def _worktree_changed_paths(
    root: Path,
    *,
    configured_manifest: str,
) -> list[str] | None:
    hidden_sensitive = _has_index_hidden_path(root)
    if hidden_sensitive is not False:
        # A hidden sensitive path is not observable through ordinary diff
        # plumbing. An unavailable inventory is equally unsafe: neither may be
        # mistaken for a clean worktree.
        return None
    paths: list[str] = []
    tracked = (
        _run_git_bounded(
            root,
            [
                *_SAFE_DIFF_CONFIG,
                "diff",
                *_DETERMINISTIC_DIFF_OPTIONS,
                "HEAD",
                "--name-only",
                "-z",
            ],
            limit=GIT_PATH_OUTPUT_LIMIT_BYTES,
        )
        if _ref_exists(root, "HEAD")
        else b""
    )
    if tracked is None:
        return None
    untracked = _run_git_bounded(
        root,
        [
            *_SAFE_DIFF_CONFIG,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        limit=GIT_PATH_OUTPUT_LIMIT_BYTES,
    )
    if untracked is None:
        return None
    try:
        tracked_text = tracked.decode("utf-8", errors="strict")
        untracked_text = untracked.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    paths.extend(path for path in tracked_text.split("\0") if path)
    paths.extend(path for path in untracked_text.split("\0") if path)
    return _bind_config_to_worktree_paths(
        root,
        paths=sorted({path for path in paths if path}),
        configured_manifest=configured_manifest,
    )


def _bind_config_to_worktree_paths(
    root: Path,
    *,
    paths: list[str],
    configured_manifest: str,
) -> list[str] | None:
    normalized = configured_manifest.replace("\\", "/").removeprefix("./")
    candidate_path = Path(normalized)
    if (
        not normalized
        or candidate_path.is_absolute()
        or ".." in candidate_path.parts
        or not _safe_changed_path_transport(normalized)
    ):
        return None
    raw, metadata = _read_untracked_file(root, normalized)
    candidate = root / candidate_path
    if raw is None or metadata is None:
        return paths if not candidate.exists() else None
    if metadata.st_size > UNTRACKED_DIFF_CONTENT_LIMIT_BYTES:
        return None
    head_probe = _run_git(
        root,
        ["cat-file", "-e", f"HEAD:{normalized}"],
    )
    if head_probe is None:
        return None
    head_raw: bytes | None
    if head_probe.returncode == 0:
        head_raw = _run_git_bounded(
            root,
            ["show", f"HEAD:{normalized}"],
            limit=UNTRACKED_DIFF_CONTENT_LIMIT_BYTES,
        )
        if head_raw is None:
            return None
    elif head_probe.returncode in {1, 128}:
        head_raw = None
    else:
        return None
    if head_raw == raw:
        return paths
    return sorted({*paths, normalized})


def _has_index_hidden_path(root: Path) -> bool | None:
    if not _ref_exists(root, "HEAD"):
        return False
    raw = _run_git_bounded(
        root,
        [*_SAFE_DIFF_CONFIG, "ls-files", "--cached", "-v", "-z"],
        limit=GIT_PATH_OUTPUT_LIMIT_BYTES,
    )
    if raw is None:
        return None
    try:
        records = raw.decode("utf-8", errors="strict").split("\0")
    except UnicodeDecodeError:
        return None
    for record in records:
        if not record:
            continue
        if len(record) < 3 or record[1] != " ":
            return None
        marker = record[0]
        hidden = marker == "S" or marker.islower()
        if hidden:
            return True
    return False


def _safe_changed_path_transport(path: str) -> bool:
    return bool(path) and not any(char in path for char in "\0\r\n")


def _literal_pathspec(path: str) -> str:
    return f":(top,literal){path}"


def _worktree_diff(root: Path, paths: list[str]) -> str | None:
    if _has_executable_worktree_filter(root):
        return None
    raw = (
        _run_git_bounded(
            root,
            [
                *_SAFE_DIFF_CONFIG,
                "diff",
                *_DETERMINISTIC_DIFF_OPTIONS,
                "HEAD",
                "--",
                *[_literal_pathspec(path) for path in paths],
            ],
            limit=GIT_DIFF_OUTPUT_LIMIT_BYTES,
        )
        if _ref_exists(root, "HEAD")
        else b""
    )
    if raw is None:
        return None
    try:
        body = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if _diff_hides_source_text(body, paths):
        return None
    untracked = _untracked_content_for_paths(root, paths)
    if untracked is None:
        return None
    return _join_text(body, untracked)


def _git_diff_for_paths(root: Path, paths: list[str]) -> str | None:
    if not _is_git_repository(root):
        return _untracked_content_for_paths(
            root,
            paths,
            assume_all_untracked=True,
        )
    if _has_executable_worktree_filter(root):
        return None
    raw = _run_git_bounded(
        root,
        [
            *_SAFE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            "HEAD",
            "--",
            *[_literal_pathspec(path) for path in paths],
        ],
        limit=GIT_DIFF_OUTPUT_LIMIT_BYTES,
    )
    if raw is None:
        return None
    try:
        body = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if _diff_hides_source_text(body, paths):
        return None
    untracked = _untracked_content_for_paths(root, paths)
    if untracked is None:
        return None
    return _join_text(body, untracked)


def _join_text(left: str, right: str) -> str:
    if left and right:
        return f"{left}\n{right}"
    return left or right


def _untracked_content_for_paths(
    root: Path,
    paths: list[str],
    *,
    assume_all_untracked: bool = False,
) -> str | None:
    tracked_paths: set[str]
    if assume_all_untracked:
        tracked_paths = set()
    else:
        tracked_raw = _run_git_bounded(
            root,
            [
                *_SAFE_DIFF_CONFIG,
                "ls-files",
                "-z",
                "--",
                *[_literal_pathspec(path) for path in paths],
            ],
            limit=GIT_PATH_OUTPUT_LIMIT_BYTES,
        )
        if tracked_raw is None:
            return None
        try:
            tracked_paths = {
                item
                for item in tracked_raw.decode("utf-8", errors="strict").split("\0")
                if item
            }
        except UnicodeDecodeError:
            return None

    chunks: list[str] = []
    aggregate_bytes = 0
    for path in paths:
        if path in tracked_paths:
            continue
        raw, metadata = _read_untracked_file(root, path)
        if raw is None or metadata is None:
            return None
        if metadata.st_size > UNTRACKED_DIFF_CONTENT_LIMIT_BYTES:
            # A metadata-only marker is cache-unsafe: same-size content can be
            # rewritten while preserving mtime. Make the snapshot unavailable
            # until the file is committed (or reduced below the bounded read).
            return None
        digest = hashlib.sha256(raw).hexdigest()
        if b"\0" in raw:
            chunk = (
                f"# untracked binary {path} size={metadata.st_size} "
                f"sha256={digest}"
            )
        else:
            text = raw.decode("utf-8", errors="replace")
            chunk = f"# untracked {path} sha256={digest}\n{text}"
        aggregate_bytes += len(chunk.encode("utf-8")) + 1
        if aggregate_bytes > GIT_DIFF_OUTPUT_LIMIT_BYTES:
            return None
        chunks.append(chunk)
    return "\n".join(chunks)


def _read_untracked_file(
    root: Path,
    path: str,
) -> tuple[bytes | None, os.stat_result | None]:
    if not _safe_changed_path_transport(path):
        return None, None
    candidate = Path(os.path.abspath(os.path.normpath(os.fspath(root / path))))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None, None
    current = root
    try:
        for component in relative.parts:
            current = current / component
            if current.is_symlink():
                return None, None
        before = candidate.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            return None, None
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(candidate, flags)
        try:
            opened = os.fstat(descriptor)
            if _file_metadata(opened) != _file_metadata(before):
                return None, None
            if opened.st_size > UNTRACKED_DIFF_CONTENT_LIMIT_BYTES:
                raw = b""
            else:
                raw = os.read(descriptor, UNTRACKED_DIFF_CONTENT_LIMIT_BYTES + 1)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current_metadata = candidate.lstat()
    except OSError:
        return None, None
    if (
        _file_metadata(opened) != _file_metadata(after)
        or _file_metadata(opened) != _file_metadata(current_metadata)
        or len(raw) > UNTRACKED_DIFF_CONTENT_LIMIT_BYTES
    ):
        return None, None
    return raw, opened


def _file_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _diff_context(root: Path, revspec: str) -> tuple[list[str], str] | None:
    if not _safe_ref_token(revspec):
        return None
    names = _run_git_bounded(
        root,
        [
            *_SAFE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            "--name-only",
            "-z",
            revspec,
        ],
        limit=GIT_PATH_OUTPUT_LIMIT_BYTES,
    )
    body = _run_git_bounded(
        root,
        [
            *_SAFE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            revspec,
        ],
        limit=GIT_DIFF_OUTPUT_LIMIT_BYTES,
    )
    if names is None or body is None:
        return None
    try:
        names_text = names.decode("utf-8", errors="strict")
        body_text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    paths = [path for path in names_text.split("\0") if path]
    if _diff_hides_source_text(body_text, paths):
        return None
    return paths, body_text


def _diff_hides_source_text(diff_text: str, paths: list[str]) -> bool:
    if "Binary files " not in diff_text and "GIT binary patch" not in diff_text:
        return False
    source_suffixes = {
        ".json", ".jsonl", ".md", ".py", ".toml", ".yaml", ".yml",
    }
    return any(
        Path(path).suffix.casefold() in source_suffixes
        or _protected_surface_for(path) is not None
        for path in paths
    )


def _safe_ref_token(value: str) -> bool:
    return bool(value) and not value.startswith("-") and not any(
        char in value for char in "\0\r\n"
    )


def _has_executable_worktree_filter(root: Path) -> bool:
    completed = _run_git(
        root,
        [
            "config",
            "--includes",
            "--get-regexp",
            r"^filter\..*\.(clean|process|smudge)$",
        ],
    )
    if completed is None:
        return True
    if completed.returncode not in {0, 1}:
        return True
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            _key, separator, value = line.partition(" ")
            if separator and value.strip():
                return True
    diff_config = _run_git(
        root,
        [
            "config",
            "--includes",
            "--get-regexp",
            r"^diff\.",
        ],
    )
    if diff_config is None or diff_config.returncode not in {0, 1}:
        return True
    if diff_config.returncode == 0 and diff_config.stdout.strip():
        return True
    info = _run_git(root, ["rev-parse", "--git-path", "info/attributes"])
    if info is None or info.returncode != 0 or not info.stdout.strip():
        return True
    info_path = Path(info.stdout.strip())
    if not info_path.is_absolute():
        info_path = root / info_path
    try:
        if info_path.is_symlink() or (info_path.is_file() and info_path.stat().st_size):
            return True
    except OSError:
        return True
    attributed = _run_git_bounded(
        root,
        [
            *_SAFE_DIFF_CONFIG,
            "ls-files",
            "-z",
            "--",
            ":(top)**",
            ":(exclude,attr:!filter)",
            ":(exclude,attr:-filter)",
        ],
    )
    return attributed is None or bool(attributed)


def _state_path(root: Path) -> Path | None:
    completed = _run_git(root, ["rev-parse", "--git-path", "agents-shipgate-hooks-state.json"])
    if completed is None or completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else (root / path)


def _read_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(root: Path, data: dict[str, Any]) -> None:
    path = _state_path(root)
    if path is None:
        return
    # Atomic replace: parallel PostToolUse hooks can run concurrently, and a
    # torn advisory cache is worse than a stale one.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temp.write_text(payload, encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        return


def _last_verified_signature(root: Path) -> str | None:
    signature = _read_state(root).get("last_verified_signature")
    return signature if isinstance(signature, str) else None


def _write_verified_signature(root: Path, signature: str) -> None:
    # Merge rather than overwrite: the same file carries the session's
    # already-approved protected surfaces.
    state = _read_state(root)
    state["last_verified_signature"] = signature
    _write_state(root, state)


def _approval_memory_enabled() -> bool:
    raw = os.environ.get("AGENTS_SHIPGATE_APPROVAL_MEMORY", "on").strip().lower()
    return raw not in {"0", "off", "false", "no"}


def _approved_surfaces(root: Path, session_id: str) -> set[str]:
    """Protected paths the human already allowed in THIS host session.

    Advisory only: it suppresses a repeated in-session permission prompt for a
    file the human already allowed. It changes no verdict — `shipgate check`
    and PR-time verify evaluate the edit exactly as before, and
    ``SHIP-VERIFY-*`` still reports the trust-root touch.
    """

    if not session_id or not _approval_memory_enabled():
        return set()
    approved = _read_state(root).get("approved_surfaces")
    if not isinstance(approved, dict):
        return set()
    entries = approved.get(session_id)
    if not isinstance(entries, list):
        return set()
    return {item for item in entries if isinstance(item, str)}


def _remember_approved_surfaces(root: Path, session_id: str, paths: list[str]) -> None:
    if not session_id or not paths or not _approval_memory_enabled():
        return
    state = _read_state(root)
    approved = state.get("approved_surfaces")
    if not isinstance(approved, dict):
        approved = {}
    # Preserve concurrent sessions rather than clobbering them; bound both the
    # per-session list and the number of retained sessions.
    updated: dict[str, list[str]] = {}
    for key, value in approved.items():
        if isinstance(key, str) and isinstance(value, list):
            updated[key] = [item for item in value if isinstance(item, str)]
    existing = set(updated.get(session_id, []))
    updated[session_id] = sorted(existing | set(paths))[:_MAX_REMEMBERED_SURFACES]
    if len(updated) > _MAX_REMEMBERED_SESSIONS:
        keep = [session_id] + [key for key in updated if key != session_id]
        updated = {key: updated[key] for key in keep[:_MAX_REMEMBERED_SESSIONS]}
    state["approved_surfaces"] = updated
    _write_state(root, state)


def _raw_changed_paths(payload: dict[str, Any]) -> list[str]:
    """Path strings exactly as the host reported them, before normalization."""

    out: list[str] = []
    for node in (payload.get("tool_input"), payload.get("tool_response")):
        if isinstance(node, dict):
            for key in ("file_path", "filePath", "path"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    out.append(value)
            edits = node.get("edits")
            if isinstance(edits, list):
                for edit in edits:
                    if isinstance(edit, dict):
                        value = edit.get("file_path") or edit.get("filePath")
                        if isinstance(value, str) and value.strip():
                            out.append(value)
    return out


def _changed_paths(payload: dict[str, Any], root: Path) -> list[str]:
    paths = [_repo_path(value, root) for value in _raw_changed_paths(payload)]
    return sorted({path for path in paths if path})


def _unsafe_alias_kind(root: Path, path: str) -> str | None:
    """Return a conservative prompt reason for aliased/non-regular writes."""

    if any(char in path for char in "\0\r\n"):
        return "control-delimited-path"
    candidate = Path(os.path.abspath(os.path.normpath(os.fspath(root / path))))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    current = root
    try:
        for part in relative.parts:
            requested = current / part
            exact_entry = False
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.name == part:
                        exact_entry = True
                        break
            if not exact_entry:
                try:
                    requested.lstat()
                except FileNotFoundError:
                    return None
                return "aliased-path"
            current = requested
            metadata = requested.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return "symbolic-link-path"
        metadata = candidate.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return "uninspectable-path"
    if not stat.S_ISREG(metadata.st_mode):
        return "non-regular-path"
    if metadata.st_nlink != 1:
        return "hardlinked-path"
    return None


def _repo_path(value: str, root: Path) -> str:
    path = Path(value)
    lexical = Path(
        os.path.abspath(
            os.path.normpath(os.fspath(path if path.is_absolute() else root / path))
        )
    )
    try:
        return lexical.relative_to(root).as_posix()
    except ValueError:
        return lexical.name


def _emit_context(event: str, message: str) -> int:
    if event == "Stop":
        print(json.dumps({"systemMessage": message[:9500]}))
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": message[:9500],
                }
            }
        )
    )
    return 0


def _emit_stop_block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason[:9500]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


__all__ = [
    "HOOK_SCRIPT_RELATIVE_PATH",
    "SETTINGS_RELATIVE_PATH",
    "install_hooks",
    "render_or_install_hooks",
]
