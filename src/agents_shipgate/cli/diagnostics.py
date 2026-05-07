"""Ranked next-action diagnostics for first-run failure modes.

This module turns already-computed signals (``DetectResult``,
``inspect_sources`` payloads, manifest text) into ranked, structured
recovery hints a coding-agent caller can route on without reading the
human-facing docs.

The functions here are pure — they accept already-parsed inputs and
return Pydantic models. They never hit the filesystem, the network, or
the typer CLI.

Diagnostics are *advisory*: they do not influence exit codes. Exit codes
remain owned by ``ConfigError`` (2), ``InputParseError`` (3), and the
``scan`` policy (20). A diagnostic with ``severity="block"`` describes a
blocking *condition*; the caller decides what to do.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.cli.discovery.signals import DetectResult


def _quote_path(value: str | Path) -> str:
    """POSIX-shell-quote a path for inclusion in a `command` field.

    `next_actions[].command` is a single shell string per the v1 contract,
    so paths with spaces or shell metacharacters must be quoted before
    interpolation. ``shlex.quote`` returns the input verbatim when no
    quoting is needed, which keeps the existing rank-1 commands stable
    in the common case of simple paths.
    """
    return shlex.quote(str(value))

# --- Public models ----------------------------------------------------------


NextActionKind = Literal["command", "edit", "review", "stop"]
DiagnosticSeverity = Literal["block", "warn", "info"]


class NextAction(BaseModel):
    """One ranked recovery step.

    Ordered list position is the rank — there is no separate ``rank`` field.

    - ``kind="command"`` → ``command`` is a runnable shell string.
    - ``kind="edit"`` → ``path`` points at a file (optionally
      ``shipgate.yaml:<line>``).
    - ``kind="review"`` → no command, just a sentence in ``why``.
    - ``kind="stop"`` → negative-control; ``command`` is None.
    """

    model_config = ConfigDict(extra="forbid")

    kind: NextActionKind
    command: str | None = None
    path: str | None = None
    why: str
    expects: str | None = None

    @model_validator(mode="after")
    def _check_kind_fields(self) -> NextAction:
        if self.kind == "command" and not self.command:
            raise ValueError("kind='command' requires a non-empty command")
        if self.kind == "edit" and not self.path:
            raise ValueError("kind='edit' requires a non-empty path")
        if self.kind == "stop" and self.command is not None:
            raise ValueError("kind='stop' must not carry a command")
        return self

    def to_legacy_string(self) -> str:
        """Project to the back-compat single-string ``next_action`` field."""
        if self.kind == "command":
            assert self.command is not None
            return self.command
        if self.kind == "edit":
            return f"Edit {self.path}"
        if self.kind == "review":
            return f"Review: {self.why}"
        return f"Stop: {self.why}"


class Diagnostic(BaseModel):
    """A first-run failure mode with at least one ranked recovery step."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    severity: DiagnosticSeverity
    next_actions: list[NextAction] = Field(min_length=1)


# --- Catalog of diagnostic IDs ---------------------------------------------
# Stable identifiers; surfaced in JSON and cross-linked from
# docs/diagnostics.md. See tests/test_diagnostics.py for stability checks.

DIAG_MISSING_MANIFEST = "SHIP-DIAG-MISSING-MANIFEST"
DIAG_INVALID_MANIFEST = "SHIP-DIAG-INVALID-MANIFEST"
DIAG_NO_AGENT_SURFACE = "SHIP-DIAG-NO-AGENT-SURFACE"
DIAG_NON_AGENT_LIBRARY = "SHIP-DIAG-NON-AGENT-LIBRARY"
DIAG_PURE_PROMPT_EXPERIMENT = "SHIP-DIAG-PURE-PROMPT-EXPERIMENT"
DIAG_MCP_OPENAPI_ARTIFACT_ONLY = "SHIP-DIAG-MCP-OPENAPI-ARTIFACT-ONLY"
DIAG_ZERO_TOOLS = "SHIP-DIAG-ZERO-TOOLS"
DIAG_DYNAMIC_TOOLSETS_ONLY = "SHIP-DIAG-DYNAMIC-TOOLSETS-ONLY"
DIAG_MISSING_SOURCE_FILE = "SHIP-DIAG-MISSING-SOURCE-FILE"
DIAG_CHANGE_ME_PLACEHOLDERS = "SHIP-DIAG-CHANGE-ME-PLACEHOLDERS"
DIAG_NO_PRODUCTION_PERMISSIONS = "SHIP-DIAG-NO-PRODUCTION-PERMISSIONS"

ALL_DIAGNOSTIC_IDS: tuple[str, ...] = (
    DIAG_MISSING_MANIFEST,
    DIAG_INVALID_MANIFEST,
    DIAG_NO_AGENT_SURFACE,
    DIAG_NON_AGENT_LIBRARY,
    DIAG_PURE_PROMPT_EXPERIMENT,
    DIAG_MCP_OPENAPI_ARTIFACT_ONLY,
    DIAG_ZERO_TOOLS,
    DIAG_DYNAMIC_TOOLSETS_ONLY,
    DIAG_MISSING_SOURCE_FILE,
    DIAG_CHANGE_ME_PLACEHOLDERS,
    DIAG_NO_PRODUCTION_PERMISSIONS,
)


# --- Public resolvers -------------------------------------------------------


def diagnose_missing_manifest(workspace: Path) -> list[Diagnostic]:
    """``shipgate.yaml`` is absent. The agent should detect, then init."""
    workspace_q = _quote_path(workspace)
    return [
        Diagnostic(
            id=DIAG_MISSING_MANIFEST,
            title="No shipgate.yaml in this workspace",
            severity="block",
            next_actions=[
                NextAction(
                    kind="command",
                    command=f"agents-shipgate detect --workspace {workspace_q} --json",
                    why=(
                        "Confirm this is an agent project before writing a "
                        "manifest. detect is read-only."
                    ),
                    expects=(
                        "JSON with is_agent_project, suggested_sources, and "
                        "diagnostics."
                    ),
                ),
                NextAction(
                    kind="command",
                    command=f"agents-shipgate init --workspace {workspace_q} --write",
                    why=(
                        "Draft a starter manifest from auto-detected "
                        "frameworks and tool sources."
                    ),
                    expects="shipgate.yaml is created at the workspace root.",
                ),
            ],
        )
    ]


def diagnose_invalid_manifest(
    manifest_path: Path, *, message: str
) -> list[Diagnostic]:
    """``shipgate.yaml`` exists on disk but the loader rejected it.

    Distinct from ``SHIP-DIAG-MISSING-MANIFEST``: the manifest is
    present, so the right rank-1 action is to *edit* it, not to run
    ``detect`` / ``init`` again. ``message`` is the underlying loader
    error (invalid YAML, schema validation failure, unsupported version,
    etc.) so the agent can route to the specific fix.
    """
    return [
        Diagnostic(
            id=DIAG_INVALID_MANIFEST,
            title="Manifest exists but failed to load",
            severity="block",
            next_actions=[
                NextAction(
                    kind="edit",
                    path=str(manifest_path),
                    why=(
                        f"Loader rejected {manifest_path}: {message}. Fix "
                        "the manifest in place — do not re-run init, which "
                        "would refuse to overwrite an existing file."
                    ),
                    expects=(
                        "agents-shipgate doctor -c <path> --json runs without "
                        "raising ConfigError."
                    ),
                ),
                NextAction(
                    kind="command",
                    command=(
                        f"agents-shipgate doctor -c {_quote_path(manifest_path)} "
                        "--json"
                    ),
                    why=(
                        "Re-run doctor after editing to verify the fix and "
                        "surface any further diagnostics."
                    ),
                    expects=(
                        "JSON payload with diagnostics[] reflecting current "
                        "manifest state."
                    ),
                ),
            ],
        )
    ]


def diagnose_detect(
    result: DetectResult, *, has_manifest: bool, workspace: Path
) -> list[Diagnostic]:
    """Diagnostics for ``detect --json``.

    Negative-control precedence (most specific wins):
        PURE_PROMPT_EXPERIMENT > NON_AGENT_LIBRARY > NO_AGENT_SURFACE
    """
    diagnostics: list[Diagnostic] = []
    signals = result.workspace_signals
    is_agent = result.is_agent_project
    has_suggested = bool(result.suggested_sources)

    # If a manifest already exists, none of the *workspace-classification*
    # diagnostics here are interesting — the agent is past detect. Only
    # surface the artifact-only nudge when relevant.
    if not has_manifest:
        if not is_agent and not has_suggested:
            # Negative-control precedence
            if (
                signals.has_prompts_dir
                and not signals.has_tools_dir
                and signals.python_file_count == 0
            ):
                diagnostics.append(
                    Diagnostic(
                        id=DIAG_PURE_PROMPT_EXPERIMENT,
                        title="Workspace looks like a pure prompt experiment",
                        severity="info",
                        next_actions=[
                            NextAction(
                                kind="stop",
                                why=(
                                    "Only prompts/ is present — no framework "
                                    "imports, no tool sources. Not a Shipgate "
                                    "target until tools or a framework appear."
                                ),
                            )
                        ],
                    )
                )
            elif (
                signals.python_file_count > 0
                and signals.has_pyproject_or_requirements
                and not signals.has_prompts_dir
                and not signals.has_tools_dir
            ):
                diagnostics.append(
                    Diagnostic(
                        id=DIAG_NON_AGENT_LIBRARY,
                        title="Workspace looks like a non-agent Python library",
                        severity="info",
                        next_actions=[
                            NextAction(
                                kind="stop",
                                why=(
                                    "Python project with no agent framework, "
                                    "prompts, or tool surface — not a "
                                    "Shipgate target."
                                ),
                            )
                        ],
                    )
                )
            else:
                diagnostics.append(
                    Diagnostic(
                        id=DIAG_NO_AGENT_SURFACE,
                        title="No agent or tool surface detected",
                        severity="info",
                        next_actions=[
                            NextAction(
                                kind="stop",
                                why=(
                                    "Workspace has no framework imports, no "
                                    "tool artifacts, and no prompt directory."
                                ),
                            )
                        ],
                    )
                )

        if not is_agent and has_suggested:
            diagnostics.append(
                Diagnostic(
                    id=DIAG_MCP_OPENAPI_ARTIFACT_ONLY,
                    title="MCP/OpenAPI artifacts present without Python framework",
                    severity="info",
                    next_actions=[
                        NextAction(
                            kind="command",
                            command=(
                                f"agents-shipgate init --workspace "
                                f"{_quote_path(workspace)} --write"
                            ),
                            why=(
                                "Artifact-only repos are valid Shipgate "
                                "targets; init picks up suggested_sources."
                            ),
                            expects=(
                                "shipgate.yaml is created with tool_sources "
                                "prefilled."
                            ),
                        )
                    ],
                )
            )

    return diagnostics


def diagnose_doctor(
    payload: dict[str, Any],
    *,
    manifest_path: Path,
    manifest_text: str,
    placeholders: list[dict[str, Any]] | None = None,
) -> list[Diagnostic]:
    """Diagnostics for ``doctor --json``.

    ``payload`` is the dict returned by
    :func:`agents_shipgate.cli.scan.inspect_sources`, including the new
    ``unresolved_sources`` and ``manifest_summary`` fields.

    ``placeholders`` is the output of
    :func:`agents_shipgate.cli.discovery.placeholders.collect_placeholders`
    against ``manifest_text``. Caller passes it in so this resolver stays
    pure and the placeholder helper is exercised once per command.
    """
    diagnostics: list[Diagnostic] = []
    # Use the manifest path the caller actually invoked, so edit actions
    # remain unambiguous in workspace runs ("subdir/shipgate.yaml:14")
    # and absolute-path invocations.
    manifest_rel = str(manifest_path)

    # SHIP-DIAG-MISSING-SOURCE-FILE — required tool_sources path doesn't resolve.
    unresolved = payload.get("unresolved_sources") or []
    if unresolved:
        actions: list[NextAction] = []
        for entry in unresolved:
            line = entry.get("line")
            target = (
                f"{manifest_rel}:{line}" if line is not None else manifest_rel
            )
            reason = entry.get("reason", "missing")
            if reason == "outside_manifest_dir":
                why = (
                    f"tool_sources entry '{entry.get('id')}' points at "
                    f"{entry.get('declared_path')!r} which resolves outside "
                    "the manifest directory; the loader would refuse to "
                    "load it."
                )
            else:
                why = (
                    f"tool_sources entry '{entry.get('id')}' points at "
                    f"{entry.get('declared_path')!r} which does not "
                    "resolve to an existing file under the manifest "
                    "directory."
                )
            actions.append(
                NextAction(
                    kind="edit",
                    path=target,
                    why=why,
                    expects="The path resolves to an existing file.",
                )
            )
        diagnostics.append(
            Diagnostic(
                id=DIAG_MISSING_SOURCE_FILE,
                title="One or more tool_sources paths do not resolve",
                severity="block",
                next_actions=actions,
            )
        )

    # SHIP-DIAG-ZERO-TOOLS — manifest exists but inspect_sources returned 0.
    if payload.get("total_tools", 0) == 0:
        diagnostics.append(
            Diagnostic(
                id=DIAG_ZERO_TOOLS,
                title="Manifest declares no enumerable tools",
                severity="block",
                next_actions=[
                    NextAction(
                        kind="command",
                        command=(
                            f"agents-shipgate doctor -c {_quote_path(manifest_path)} "
                            "--verbose --json"
                        ),
                        why=(
                            "Re-run with --verbose to see source-load warnings "
                            "and dynamic-toolset hints."
                        ),
                        expects=(
                            "warnings[] entries explain why each tool_source "
                            "produced 0 tools."
                        ),
                    ),
                    NextAction(
                        kind="edit",
                        path=str(manifest_path),
                        why=(
                            "Add an explicit MCP export, OpenAPI spec, or "
                            "local tool inventory as a new tool_sources entry."
                        ),
                        expects=(
                            "doctor reports total_tools >= 1 on the next run."
                        ),
                    ),
                ],
            )
        )

    # SHIP-DIAG-DYNAMIC-TOOLSETS-ONLY — low tools + dynamic count >= 1.
    if _has_dynamic_toolsets_only(payload):
        diagnostics.append(
            Diagnostic(
                id=DIAG_DYNAMIC_TOOLSETS_ONLY,
                title="Tool surface is dominated by dynamic toolsets",
                severity="warn",
                next_actions=[
                    NextAction(
                        kind="edit",
                        path=str(manifest_path),
                        why=(
                            "Static extractors cannot enumerate dynamic "
                            "ADK/LangChain/CrewAI toolsets. Declare an "
                            "explicit MCP/OpenAPI source or a local tool "
                            "inventory artifact."
                        ),
                        expects=(
                            "tool_sources gains a non-dynamic entry; doctor "
                            "reports a higher total_tools."
                        ),
                    )
                ],
            )
        )

    # SHIP-DIAG-CHANGE-ME-PLACEHOLDERS — manifest text still has CHANGE_ME.
    if placeholders:
        actions = []
        for entry in placeholders[:5]:
            line = entry.get("line")
            target = (
                f"{manifest_rel}:{line}" if line is not None else manifest_rel
            )
            actions.append(
                NextAction(
                    kind="edit",
                    path=target,
                    why=(
                        f"Replace CHANGE_ME at field "
                        f"{entry.get('path', '<root>')!r}."
                    ),
                    expects="The placeholder is replaced with a real value.",
                )
            )
        diagnostics.append(
            Diagnostic(
                id=DIAG_CHANGE_ME_PLACEHOLDERS,
                title="Manifest still contains CHANGE_ME placeholders",
                severity="warn",
                next_actions=actions,
            )
        )

    # SHIP-DIAG-NO-PRODUCTION-PERMISSIONS — production target with empty perms.
    summary = payload.get("manifest_summary") or {}
    if (
        summary.get("environment_target") == "production"
        and not summary.get("has_permissions")
        and not summary.get("has_policies")
        and (summary.get("scope_count") or 0) == 0
    ):
        diagnostics.append(
            Diagnostic(
                id=DIAG_NO_PRODUCTION_PERMISSIONS,
                title="Production target declares no permissions or policies",
                severity="warn",
                next_actions=[
                    NextAction(
                        kind="edit",
                        path=str(manifest_path),
                        why=(
                            "environment.target is 'production' but the "
                            "manifest declares no permissions, scopes, or "
                            "policies — production gates will trigger on "
                            "scan."
                        ),
                        expects=(
                            "permissions / policies blocks declare at least "
                            "one scope or rule."
                        ),
                    )
                ],
            )
        )

    return diagnostics


def top_next_actions(
    diagnostics: list[Diagnostic], *, limit: int = 3
) -> list[NextAction]:
    """Flatten ranked rank-1 actions across diagnostics.

    Severity order: block > warn > info. Within each severity bucket,
    the input order is preserved so callers can shape the catalog
    output deterministically.
    """
    severity_rank = {"block": 0, "warn": 1, "info": 2}
    ordered = sorted(
        enumerate(diagnostics),
        key=lambda item: (severity_rank[item[1].severity], item[0]),
    )
    actions: list[NextAction] = []
    for _, diag in ordered:
        actions.append(diag.next_actions[0])
        if len(actions) >= limit:
            break
    return actions


# --- Internals --------------------------------------------------------------


def _has_dynamic_toolsets_only(payload: dict[str, Any]) -> bool:
    total_tools = payload.get("total_tools", 0) or 0
    if total_tools >= 3:
        return False
    frameworks = payload.get("frameworks") or {}
    if not isinstance(frameworks, dict):
        return False
    dynamic_total = 0
    adk = frameworks.get("google_adk") or {}
    if isinstance(adk, dict):
        dynamic_total += int(adk.get("dynamic_toolset_count", 0) or 0)
    langchain = frameworks.get("langchain") or {}
    if isinstance(langchain, dict):
        dynamic_total += int(
            langchain.get("dynamic_tool_surface_count", 0) or 0
        )
    crewai = frameworks.get("crewai") or {}
    if isinstance(crewai, dict):
        dynamic_total += int(
            crewai.get("dynamic_tool_surface_count", 0) or 0
        )
    return dynamic_total >= 1
