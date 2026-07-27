from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.checks.verify import TRUST_ROOT_SURFACES
from agents_shipgate.core.errors import ConfigError

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
    workspace = workspace.resolve()
    settings_path = workspace / SETTINGS_RELATIVE_PATH
    script_path = workspace / HOOK_SCRIPT_RELATIVE_PATH
    _ensure_repo_local_write(settings_path, workspace)
    _ensure_repo_local_write(script_path, workspace)

    existing_settings = _read_settings(settings_path)
    rendered_settings = _merge_claude_settings(
        existing_settings,
        config=config.as_posix(),
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
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


VERIFY_TIMEOUT_SECONDS = 170
UNTRACKED_DIFF_CONTENT_LIMIT_BYTES = 131072

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
        return _pretooluse(payload, root)
    if args.mode == "trigger":
        return _trigger(payload, root, args)
    return _verify(payload, root, args)


def _pretooluse(payload: dict[str, Any], root: Path) -> int:
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
    matched: list[tuple[str, str, str]] = []
    for path in _changed_paths(payload, root):
        hit = _protected_surface_for(path)
        if hit is not None:
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


def _protected_surface_for(path: str) -> tuple[str, str] | None:
    for kind, pattern in PROTECTED_SURFACES:
        if _glob_match(pattern, path):
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
    diff_text = _git_diff_for_paths(root, paths)
    # For edit-time nudges, evaluate path relevance without the opted-in
    # manifest force-run rule. CI still runs every PR for opted-in repos.
    result = _run_trigger_for_paths(
        root,
        paths,
        diff_text=diff_text,
        manifest_present=False,
    )
    if result is None or not result.get("should_run"):
        return 0

    path_preview = ", ".join(paths[:3])
    rationale = result.get("rationale") or "Shipgate trigger matched."
    if (root / args.config).is_file():
        command = _manual_verify_command(args, root=root)
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


def _verify(payload: dict[str, Any], root: Path, args: argparse.Namespace) -> int:
    config_path = root / args.config
    stop_hook_active = bool(payload.get("stop_hook_active"))
    snapshot = _change_snapshot(root, args)
    if not config_path.is_file():
        if not snapshot["paths"]:
            return 0
        trigger = _run_trigger_for_paths(
            root,
            snapshot["paths"],
            diff_text=snapshot["diff_text"],
            manifest_present=False,
        )
        if trigger and trigger.get("should_run"):
            # Advisory: nothing is configured yet, so nobody has decided this
            # repo is gated — advise, never force the turn to continue.
            return _emit_context(
                "Stop",
                "Agents Shipgate trigger matched, but no shipgate.yaml exists. "
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
    if trigger is None:
        return _emit_context(
            "Stop",
            "Agents Shipgate hook could not evaluate the local trigger. Hooks are "
            "advisory; before finishing an agent-related diff, run "
            f"`{_manual_verify_command(args, root=root)}` manually.",
        )
    if not trigger.get("should_run"):
        return 0

    signature = str(snapshot["signature"])
    if _last_verified_signature(root) == signature:
        return 0

    base = os.environ.get("AGENTS_SHIPGATE_VERIFY_BASE", args.base).strip()
    head = os.environ.get("AGENTS_SHIPGATE_VERIFY_HEAD", args.head).strip()
    ci_mode = os.environ.get("AGENTS_SHIPGATE_VERIFY_CI_MODE", args.ci_mode)
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
    if head:
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
            f"`{_manual_verify_command(args, root=root)}` manually and read "
            "`agents-shipgate-reports/report.json`.",
        )

    decision = ((verifier.get("release_decision") or {}).get("decision") or "unknown")
    blockers = len((verifier.get("release_decision") or {}).get("blockers") or [])
    review_items = len((verifier.get("release_decision") or {}).get("review_items") or [])
    control = verifier.get("control") if isinstance(verifier.get("control"), dict) else {}
    state = control.get("state")
    summary = f"decision={decision}, blockers={blockers}, review_items={review_items}"

    # The hook mirrors the operational control contract: ``control.state`` is
    # authoritative and ``decision`` is diagnostic.  A Claude Code Stop-hook
    # "block" forces the agent to KEEP WORKING, so it is only ever correct
    # when an exact coding-agent action remains.  ``human_review_required``
    # (``must_stop=true``) is the opposite situation — the turn must be
    # allowed to end so a human can take over.
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
        command = next_action.get("command") or _manual_verify_command(args, root=root)
        why = next_action.get("why") or "One coding-agent action remains."
        return _emit_stop_block(
            f"Agents Shipgate verify ran before completion: {summary}.{base_note} "
            f"One exact coding-agent action remains before finishing: run `{command}`. "
            f"{why} "
            "Do not bypass the verifier by suppressing findings, lowering severity, "
            "expanding baselines/waivers, removing Shipgate CI, or weakening agent instructions."
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


def _manual_verify_command(args: argparse.Namespace, *, root: Path | None = None) -> str:
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
    if args.head:
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
    return _run(["git", "-C", str(root), *args], cwd=root, timeout=20)


def _ref_exists(root: Path, ref: str) -> bool:
    completed = _run_git(root, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return completed is not None and completed.returncode == 0


def _change_snapshot(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    paths = _worktree_changed_paths(root)
    if paths:
        diff_text = _worktree_diff(root, paths)
        return _snapshot("worktree", paths, diff_text, args)

    head = args.head or "HEAD"
    if args.base and _ref_exists(root, args.base) and _ref_exists(root, head):
        diff = _diff_context(root, f"{args.base}...{head}")
        if diff is not None:
            commit_paths, diff_text = diff
            if commit_paths:
                return _snapshot("commit", commit_paths, diff_text, args)

    return {"kind": "none", "paths": [], "diff_text": "", "signature": ""}


def _snapshot(
    kind: str,
    paths: list[str],
    diff_text: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    paths = sorted({path for path in paths if path})
    payload = {
        "kind": kind,
        "base": args.base,
        "head": args.head,
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


def _worktree_changed_paths(root: Path) -> list[str]:
    paths: list[str] = []
    tracked = _run_git(root, ["diff", "HEAD", "--name-only"])
    if tracked is not None and tracked.returncode == 0:
        paths.extend(line.strip() for line in tracked.stdout.splitlines() if line.strip())
    untracked = _run_git(root, ["ls-files", "--others", "--exclude-standard"])
    if untracked is not None and untracked.returncode == 0:
        paths.extend(line.strip() for line in untracked.stdout.splitlines() if line.strip())
    return sorted({path for path in paths if path})


def _worktree_diff(root: Path, paths: list[str]) -> str:
    completed = _run_git(root, ["diff", "HEAD", "--", *paths])
    body = completed.stdout if completed is not None and completed.returncode == 0 else ""
    untracked = _untracked_content_for_paths(root, paths)
    return _join_text(body, untracked)


def _git_diff_for_paths(root: Path, paths: list[str]) -> str:
    completed = _run_git(root, ["diff", "HEAD", "--", *paths])
    body = completed.stdout if completed is not None and completed.returncode == 0 else ""
    return _join_text(body, _untracked_content_for_paths(root, paths))


def _join_text(left: str, right: str) -> str:
    if left and right:
        return f"{left}\n{right}"
    return left or right


def _untracked_content_for_paths(root: Path, paths: list[str]) -> str:
    chunks: list[str] = []
    for path in paths:
        tracked = _run_git(root, ["ls-files", "--error-unmatch", path])
        if tracked is not None and tracked.returncode == 0:
            continue
        candidate = root / path
        if not candidate.is_file():
            continue
        try:
            stat = candidate.stat()
            if stat.st_size > UNTRACKED_DIFF_CONTENT_LIMIT_BYTES:
                chunks.append(
                    f"# untracked {path} size={stat.st_size} mtime_ns={stat.st_mtime_ns}"
                )
                continue
            raw = candidate.read_bytes()
        except OSError:
            continue
        if b"\0" in raw:
            chunks.append(f"# untracked binary {path} size={stat.st_size}")
            continue
        text = raw.decode("utf-8", errors="replace")
        chunks.append(f"# untracked {path}\n{text}")
    return "\n".join(chunks)


def _diff_context(root: Path, revspec: str) -> tuple[list[str], str] | None:
    names = _run_git(root, ["diff", "--name-only", revspec])
    body = _run_git(root, ["diff", revspec])
    if names is None or body is None or names.returncode != 0 or body.returncode != 0:
        return None
    paths = [line.strip() for line in names.stdout.splitlines() if line.strip()]
    return paths, body.stdout


def _state_path(root: Path) -> Path | None:
    completed = _run_git(root, ["rev-parse", "--git-path", "agents-shipgate-hooks-state.json"])
    if completed is None or completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else (root / path)


def _last_verified_signature(root: Path) -> str | None:
    path = _state_path(root)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    signature = data.get("last_verified_signature")
    return signature if isinstance(signature, str) else None


def _write_verified_signature(root: Path, signature: str) -> None:
    path = _state_path(root)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_verified_signature": signature}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return


def _changed_paths(payload: dict[str, Any], root: Path) -> list[str]:
    out: list[str] = []
    tool_input = payload.get("tool_input")
    tool_response = payload.get("tool_response")
    for node in (tool_input, tool_response):
        if isinstance(node, dict):
            for key in ("file_path", "filePath", "path"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    out.append(_repo_path(value, root))
            edits = node.get("edits")
            if isinstance(edits, list):
                for edit in edits:
                    if isinstance(edit, dict):
                        value = edit.get("file_path") or edit.get("filePath")
                        if isinstance(value, str) and value.strip():
                            out.append(_repo_path(value, root))
    return sorted({path for path in out if path})


def _repo_path(value: str, root: Path) -> str:
    path = Path(value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            return path.name
    return path.as_posix()


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
