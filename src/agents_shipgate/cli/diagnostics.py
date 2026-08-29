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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_shipgate.core.adopter_text import (
    DUPLICATE_ACROSS_ARTIFACTS,
    DUPLICATE_TOOL_IN_SOURCE,
    REPEATED_SOURCE_ENTRY,
    source_label,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.detect import DetectResult, WorkspaceSignals
from agents_shipgate.schemas.diagnostics import (
    DIAG_CHANGE_ME_PLACEHOLDERS,
    DIAG_CODEX_PLUGIN_PACKAGE_DETECTED,
    DIAG_DYNAMIC_TOOLSETS_ONLY,
    DIAG_INVALID_MANIFEST,
    DIAG_MCP_OPENAPI_ARTIFACT_ONLY,
    DIAG_MISSING_MANIFEST,
    DIAG_MISSING_SOURCE_FILE,
    DIAG_NO_AGENT_SURFACE,
    DIAG_NO_PRODUCTION_PERMISSIONS,
    DIAG_NON_AGENT_LIBRARY,
    DIAG_PURE_PROMPT_EXPERIMENT,
    DIAG_UNKNOWN_ADAPTER_SOURCE_TYPE,
    DIAG_ZERO_TOOLS,
    Diagnostic,
    NextAction,
)
from agents_shipgate.schemas.manifest import (
    MANIFEST_PLACEHOLDER_VALUE,
    builtin_tool_source_types_text,
)


def _quote_path(value: str | Path) -> str:
    """POSIX-shell-quote a path for inclusion in a `command` field.

    `next_actions[].command` is a single shell string per the v1 contract,
    so paths with spaces or shell metacharacters must be quoted before
    interpolation. ``shlex.quote`` returns the input verbatim when no
    quoting is needed, which keeps the existing rank-1 commands stable
    in the common case of simple paths.
    """
    return shlex.quote(str(value))

# --- Catalog of diagnostic IDs ---------------------------------------------
# Stable identifiers; surfaced in JSON and cross-linked from
# docs/diagnostics.md. See tests/test_diagnostics.py for stability checks.


# --- Public resolvers -------------------------------------------------------


def diagnose_missing_manifest(workspace: Path) -> list[Diagnostic]:
    """``shipgate.yaml`` is absent. The agent should start with verify preview."""
    workspace_q = _quote_path(workspace)
    return [
        Diagnostic(
            id=DIAG_MISSING_MANIFEST,
            title="No shipgate.yaml in this workspace",
            severity="block",
            next_actions=[
                NextAction(
                    kind="command",
                    command=f"agents-shipgate verify --workspace {workspace_q} --preview --json",
                    why=(
                        "Ask the verify flow whether this workspace needs "
                        "Shipgate configuration before writing a manifest."
                    ),
                    expects=(
                        "JSON preview result with the next setup or skip "
                        "action."
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


def diagnose_unknown_adapter_source_type(
    manifest_path: Path,
    *,
    source_type: str,
    plugins_enabled: bool,
    message: str,
) -> list[Diagnostic]:
    """v0.20 (PR #111 review follow-up #5): ``shipgate.yaml`` references
    a ``tool_sources[].type`` value that no registered adapter handles.

    Distinct from ``SHIP-DIAG-INVALID-MANIFEST``: the manifest passes
    Pydantic validation (``type`` is open ``str`` for third-party
    adapter support) but the dispatcher can't resolve the source type.
    The right rank-1 action depends on whether plugin discovery is
    currently enabled:

    - **plugins disabled**: install the third-party adapter package
      AND enable discovery via ``AGENTS_SHIPGATE_ENABLE_PLUGINS=1``
      (or remove ``--no-plugins``).
    - **plugins enabled**: install the adapter package, or fix a typo
      against a built-in name.

    The "edit shipgate.yaml" path that ``diagnose_invalid_manifest``
    emits would be misleading here — the manifest itself is valid;
    the user just needs to install/enable the matching adapter.

    Unless the type is the manifest placeholder, in which case the edit *is*
    the answer and both other routes are wrong: there is no package to install
    for ``CHANGE_ME`` and no typo to fix. It is the value ``init`` writes when
    discovery found no tool surface, and it is the first failure a scaffolded
    manifest hits — ahead of the missing ``path``, which ``input_parse_recovery``
    has always routed as a placeholder (#441).
    """

    if source_type == MANIFEST_PLACEHOLDER_VALUE:
        next_actions = [
            NextAction(
                kind="edit",
                path=str(manifest_path),
                why=(
                    f"tool_sources[].type is still {source_type!r} — the "
                    "placeholder `agents-shipgate init` writes when it finds "
                    "no tool surface to read. Nothing was inferred here: name "
                    "the source this repository publishes and the path to it. "
                    f"Built-ins: {builtin_tool_source_types_text()}; an "
                    "installed third-party adapter's own source type is "
                    "equally valid."
                ),
                expects=(
                    "tool_sources[].type names a built-in source type or one "
                    "an installed adapter registers, and tool_sources[].path "
                    "resolves to a file in this workspace."
                ),
            ),
            NextAction(
                kind="command",
                command=render_command(
                    ["doctor", "--config", str(manifest_path), "--json"]
                ),
                why=(
                    "List every field this manifest still leaves unresolved, "
                    "not only the one the scan stopped on."
                ),
                expects=(
                    "A doctor payload whose placeholders[] is empty for this "
                    "manifest."
                ),
            ),
        ]
    elif plugins_enabled:
        next_actions = [
            NextAction(
                kind="command",
                command="pip install <third-party-adapter-package>",
                why=(
                    f"Install the third-party package that ships an "
                    f"adapter for {source_type!r} via the "
                    f"`agents_shipgate.adapters` entry-point group. "
                    f"Re-run the scan to confirm it appears in "
                    f"`report.loaded_adapters[]` with "
                    f"`validation_status=\"valid\"`."
                ),
                expects=(
                    f"`agents-shipgate doctor -c {_quote_path(manifest_path)} "
                    f"--json` lists {source_type!r} under sources[] "
                    f"with no warning."
                ),
            ),
            NextAction(
                kind="edit",
                path=str(manifest_path),
                why=(
                    f"If {source_type!r} is a typo, fix it. Built-in "
                    f"source types: {builtin_tool_source_types_text()}."
                ),
                expects=(
                    "Manifest references only built-in source types or "
                    "source types registered by installed third-party "
                    "adapter packages."
                ),
            ),
        ]
    else:
        next_actions = [
            NextAction(
                kind="command",
                command=(
                    "AGENTS_SHIPGATE_ENABLE_PLUGINS=1 agents-shipgate "
                    f"scan -c {_quote_path(manifest_path)}"
                ),
                why=(
                    "Enable third-party adapter discovery and re-run "
                    "the scan. If the adapter package is also "
                    "installed, this resolves the shipgate.yaml "
                    "tool_sources[].type."
                ),
                expects=(
                    f"Scan completes; `report.loaded_adapters[]` "
                    f"contains an entry for {source_type!r} with "
                    f"`validation_status=\"valid\"`."
                ),
            ),
            NextAction(
                kind="command",
                command="pip install <third-party-adapter-package>",
                why=(
                    f"If discovery is already enabled and "
                    f"{source_type!r} still doesn't resolve, the "
                    "adapter package isn't installed. Install it, "
                    "then re-run."
                ),
                expects=(
                    f"`pip show <package>` succeeds; `agents-shipgate "
                    f"doctor -c {_quote_path(manifest_path)} --json` "
                    f"lists {source_type!r} under sources[]."
                ),
            ),
            NextAction(
                kind="edit",
                path=str(manifest_path),
                why=(
                    f"If {source_type!r} is a typo, fix it. Built-in "
                    f"source types: {builtin_tool_source_types_text()}."
                ),
                expects=(
                    "Manifest references only built-in source types or "
                    "source types registered by installed third-party "
                    "adapter packages."
                ),
            ),
        ]

    return [
        Diagnostic(
            id=DIAG_UNKNOWN_ADAPTER_SOURCE_TYPE,
            # The title names the remedy the selected route actually is. The
            # single "(install/enable the adapter, or fix a typo)" title named,
            # for the placeholder, exactly the two remedies its own branch says
            # do not apply — and a title is what a reader sees before any
            # `next_actions[]` entry (#441 review).
            title=(
                f"tool_sources[].type is still the {source_type!r} placeholder "
                "in shipgate.yaml (name the source this repository publishes)"
                if source_type == MANIFEST_PLACEHOLDER_VALUE
                else (
                    f"No adapter handles tool_sources[].type {source_type!r} "
                    "in shipgate.yaml (install/enable the adapter, or fix a typo)"
                )
            ),
            severity="block",
            next_actions=next_actions,
        )
    ]


def input_parse_recovery(
    exc: InputParseError, *, manifest_path: Path | None = None
) -> list[NextAction]:
    """The ranked recovery for an ``input_parse_error``, for every command.

    ``scan``, ``verify``, and the verifier assembly path each caught this
    exception and each wrote its own recovery, so a failure that had a precise
    route on one command got "inspect the file referenced in the error" on the
    next — the second-implementation bug class (#322). One resolver, three
    call sites.

    Routing is on the exception's typed ``details["failure"]`` where it has
    one. That is what lets the *message* be rewritten for a human without
    breaking the route (#329): prose is for the reader, ``details`` is for the
    caller, and neither is parsed to derive the other. The ``CHANGE_ME`` branch
    predates typed details and still sniffs the text, because the placeholder
    is genuinely a property of the manifest's contents rather than of a failure
    site.
    """

    details = exc.details
    # The manifest the run actually read, which is not always the one the CLI
    # was spelled with: `--workspace` discovery can select a sole nested
    # `services/billing/shipgate.yaml`, and `verify` may be invoked with no
    # `--config` at all. `run_scan` records the resolved path on the way out,
    # so an `edit` action cannot name an unrelated trust root in the caller's
    # working directory (#329 review). The argument is the fallback for the
    # failures raised before a scan started.
    manifest = str(details.get("manifest_path") or manifest_path or "shipgate.yaml")
    # A failure evaluated against a ref that is not the checked-out tree has no
    # file the reader can open: the archive is gone and the working tree may
    # already hold the fix. Say which commit and which path within it, and
    # publish no `path` at all (#329 review 3).
    evaluated_ref = details.get("evaluated_ref")
    manifest_in_ref = details.get("manifest_in_ref")
    if evaluated_ref and manifest_in_ref:
        return [
            NextAction(
                kind="review",
                why=(
                    f"This failure is in {manifest_in_ref} as of {evaluated_ref}, "
                    "which is not the tree you have checked out. Inspect that "
                    "commit — the working copy may already differ."
                ),
                expects=(
                    f"{manifest_in_ref} at {evaluated_ref} no longer produces "
                    "this failure."
                ),
            )
        ]
    if details.get("failure") == DUPLICATE_TOOL_IN_SOURCE:
        return [_duplicate_tool_action(details, manifest=manifest)]
    # `init --write` on a workspace where detect found no sources leaves
    # CHANGE_ME placeholders; scanning then fails here. Route to the
    # placeholder fix, not the generic missing-file advice.
    #
    # Decided on typed manifest state, not on a substring of the failure text:
    # a filled-in manifest pointing at a missing file named
    # `CHANGE_ME-tools.json` matched the old search and was told it still held
    # template placeholders (#329 review 3). A caller that recorded nothing
    # gets the generic route — "we did not look" is not "there are none".
    placeholders = details.get("manifest_placeholders")
    if placeholders:
        fields = ", ".join(str(field) for field in list(placeholders)[:4])
        return [
            NextAction(
                kind="edit",
                # The manifest the run read: `--workspace` can select a nested
                # one, and this branch discarding it sent the reader to a
                # different file than the one holding the placeholders.
                path=manifest,
                why=(
                    f"{manifest} still declares CHANGE_ME at {fields}. Edit "
                    "those fields to point at real artifacts, or run "
                    "`agents-shipgate doctor` to list every placeholder."
                ),
                expects=(
                    "tool_sources entries reference files that exist in this "
                    "workspace."
                ),
            )
        ]
    return [
        NextAction(
            kind="review",
            why=(
                "Inspect the file referenced in the error; ensure it exists, is "
                "valid, and resolves under the manifest directory."
            ),
            expects=(
                "Referenced file is present, parseable, and inside the manifest "
                "directory."
            ),
        )
    ]


def _duplicate_tool_action(
    details: Mapping[str, Any], *, manifest: str
) -> NextAction:
    """One target, chosen from the cause the duplicate check reported.

    A repeated entry is repaired in the manifest and a duplicate definition is
    repaired in the artifact, so naming both would leave a consumer routing on
    ``path`` free to delete a source declaration when the file was the problem.
    Only the manifest is ever an ``edit`` target. A declared artifact path has
    no single base — the manifest's for most sources, the *entrypoint's* for an
    inventory a framework file mounts — so publishing one as a routable path
    named a file that did not exist (#329 review 3). The artifact still
    appears in the sentence, where it is a name to grep for rather than a path
    to open, and the duplicate-inside-an-artifact case routes to review.
    """

    tool_name = str(details.get("tool_name") or "")
    source_id = str(details.get("source_id") or "")
    source_file = details.get("source_file")
    where = source_label(
        file_path=source_file if isinstance(source_file, str) else None,
        source_id=source_id,
    )
    if details.get("cause") == DUPLICATE_ACROSS_ARTIFACTS:
        other_file = details.get("other_source_file")
        other = source_label(
            file_path=other_file if isinstance(other_file, str) else None,
            source_id=source_id,
        )
        # Review, not edit. Two repairs are available and they live in
        # different files — rename one of the declarations, or bring both under
        # one tool_sources entry — so publishing either as a routable `path`
        # would send a consumer to change the file the reader had not chosen.
        return NextAction(
            kind="review",
            why=(
                f"{other} and {where} both declare the tool {tool_name!r} "
                "under one name, so one capability arrived twice. Either "
                "rename one declaration, or cover both files with a single "
                f"tool_sources entry in {manifest} so they are reconciled as "
                "one."
            ),
            expects=(
                "Each capability is declared once, or the files declaring it "
                "are read by one tool source."
            ),
        )
    if details.get("cause") == REPEATED_SOURCE_ENTRY:
        return NextAction(
            kind="edit",
            path=manifest,
            why=(
                f"{manifest} reads {where} twice as one tool source, so the "
                f"tool {tool_name!r} arrived twice. Remove the repeated entry."
            ),
            expects="Each tool source names its artifact exactly once.",
        )
    return NextAction(
        kind="review",
        why=(
            f"{where} produced the tool {tool_name!r} twice, so one artifact "
            f"defines it more than once. Find it under the tool source "
            f"{source_id!r} declared in {manifest} and remove the duplicate "
            "definition; the manifest entry itself is correct."
        ),
        expects=f"The artifact behind {source_id!r} defines each tool once.",
    )


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


def _at_workspace_root(signals: WorkspaceSignals, name: str) -> bool:
    """Whether that conventional directory sits at the workspace root.

    ``has_prompts_dir`` / ``has_tools_dir`` answer "anywhere in this workspace",
    which is the question #441 needed. A negative control that describes the
    *shape* of the workspace needs the narrower one, and
    ``conventional_dirs`` holds workspace-relative paths whose root spelling is
    the bare name.
    """

    return name in signals.conventional_dirs


def _no_agent_surface_why(signals: WorkspaceSignals) -> str:
    """Why nothing here is a Shipgate target, naming what *was* found.

    The flat "no framework imports, no tool artifacts, and no prompt directory"
    is a list of absences, and it stopped being the whole truth once the
    conventional-dir signal started reading below the workspace root (#441): a
    repository whose tools live at
    ``awslabs/billing_cost_management_mcp_server/tools/`` was told nothing
    tool-shaped was found, while ``workspace_signals.has_tools_dir`` in the
    same payload said otherwise. A reader deciding whether to trust the verdict
    needs the signal that was seen and *not* found sufficient, because that is
    the one they would otherwise go looking for.
    """

    # `conventional_dirs` holds workspace-relative paths, so this names the
    # directory a reader can actually open: `awslabs/…/tools/`, not `tools/`
    # for a repository whose root has no `tools/` at all.
    found = [f"{path}/" for path in signals.conventional_dirs]
    if not found:
        return (
            "Workspace has no framework imports, no tool artifacts, and no "
            "prompt directory."
        )
    return (
        f"Workspace has {_join_and(found)} but no framework imports and no "
        "parseable tool artifact, so there is no declared surface to gate. "
        "A conventional directory alone is not one."
    )


def _join_and(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


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
    has_codex_plugin = bool(result.codex_plugin_candidates)

    # If a manifest already exists, none of the *workspace-classification*
    # diagnostics here are interesting — the agent is past detect. Only
    # surface the artifact-only nudge when relevant.
    if not has_manifest:
        # Every negative control below is a claim about the *whole* workspace,
        # and a capped parse read part of one. On a truncated walk each of them
        # publishes a `stop` action, which routing turns into
        # `setup_not_applicable` — a terminal machine route for a scan that
        # said it was inconclusive, on exactly the repositories the cap cuts
        # (#399 review). Emitting nothing here hands the route to
        # `_detect_advance`, which returns the higher-cap retry.
        #
        # The guard is the *raw* parse-completeness bit, not
        # `agent_scope_truncated`. That one also requires more than one
        # candidate scope, which is right for a claim about the candidate list
        # and wrong for a claim about the workspace: a single-scope repository
        # with a root `pyproject.toml` and its only agent past the cap reports
        # `agent_scope_truncated: false`, and gating on it published exactly
        # the terminal negative this is here to prevent.
        if (
            not is_agent
            and not has_suggested
            and not has_codex_plugin
            and not result.python_parse_truncated
        ):
            # Negative-control precedence
            if (
                # A *root* `prompts/`, not one anywhere in the tree. "Only
                # prompts/ is present" is a claim about the shape of the
                # workspace, and #441 widened `has_prompts_dir` to mean
                # "somewhere" — which made this fire on a thirty-file
                # TypeScript MCP server with `src/prompts/`, a repository that
                # flatly contradicts the sentence below. `conventional_dirs`
                # carries located paths, and a root directory is spelled as its
                # bare name, so this is the same question the field answered
                # before the widening.
                _at_workspace_root(signals, "prompts")
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
                                why=_no_agent_surface_why(signals),
                            )
                        ],
                    )
                )

        # Both nudges below name a root `init --write`, and setup routing
        # ranks a diagnostic ahead of the advance — so anything unsettled here
        # published a command over the top of the route that would have said
        # so (#399 review). They fire only where that command both succeeds
        # and adopts a complete surface: a settled scope says the workspace is
        # one manifest's boundary, and a complete parse says the tool surface
        # that manifest would declare was actually read. A settled scope does
        # not imply the second — a one-project workspace is `"single"` however
        # early the parse stopped — and gating on it alone let the artifact
        # nudge outrank the full-count retry and adopt a truncated surface.
        settled = result.agent_scope == "single" and not result.python_parse_truncated
        if not is_agent and has_suggested and not has_codex_plugin and settled:
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

        if not is_agent and has_codex_plugin and settled:
            diagnostics.append(
                Diagnostic(
                    id=DIAG_CODEX_PLUGIN_PACKAGE_DETECTED,
                    title="Codex plugin package detected without Python framework",
                    severity="info",
                    next_actions=[
                        NextAction(
                            kind="command",
                            command=(
                                f"agents-shipgate init --workspace "
                                f"{_quote_path(workspace)} --write"
                            ),
                            why=(
                                "Codex plugin packages are valid static "
                                "Shipgate targets; init writes codex_plugin "
                                "tool_sources."
                            ),
                            expects=(
                                "shipgate.yaml is created with codex_plugin "
                                "sources prefilled."
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
    # Suppress this for Codex plugin packages: their static surface can contain
    # skills/apps/hooks/server stubs without any enumerable tools.
    # Surfaces the three canonical recovery paths from agent-recipes.md
    # Recipe 2 as separate next_actions with explicit `expects` fields,
    # so the agent can pick the one that matches the runtime architecture
    # without having to re-derive the recovery vocabulary from prose.
    if payload.get("total_tools", 0) == 0 and not payload.get("codex_plugin_surface"):
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
                            "Diagnose: re-run with --verbose to see "
                            "source-load warnings and dynamic-toolset hints. "
                            "Pick the recovery that matches the actual cause."
                        ),
                        expects=(
                            "warnings[] entries explain why each tool_source "
                            "produced 0 tools (e.g. 'factory wrapper hides "
                            "tools from AST', 'MCP server unreachable')."
                        ),
                    ),
                    NextAction(
                        kind="edit",
                        path=str(manifest_path),
                        why=(
                            "Recovery 1 of 3 — add an explicit MCP export. "
                            "If the agent speaks MCP at runtime, dump the "
                            "resolved tool list to a JSON file (canonical "
                            "path: `.agents-shipgate/mcp-export.json`) and "
                            "add a tool_sources entry with `type: mcp`."
                        ),
                        expects=(
                            "tool_sources gains a `type: mcp` entry pointing "
                            "at a JSON file with a non-empty `tools` array; "
                            "doctor reports `total_tools >= 1` on the next run."
                        ),
                    ),
                    NextAction(
                        kind="edit",
                        path=str(manifest_path),
                        why=(
                            "Recovery 2 of 3 — add an OpenAPI spec. If the "
                            "tool surface is HTTP-shaped, declare the spec "
                            "via a tool_sources entry with `type: openapi`."
                        ),
                        expects=(
                            "tool_sources gains a `type: openapi` entry "
                            "pointing at a file containing `paths`; doctor "
                            "reports `total_tools` matching the documented "
                            "operations."
                        ),
                    ),
                    NextAction(
                        kind="edit",
                        path=str(manifest_path),
                        why=(
                            "Recovery 3 of 3 — provide a local tool inventory. "
                            "LangChain, CrewAI, and Google ADK accept "
                            "`{framework}.tool_inventories[]` JSON listings "
                            "of resolved tools, useful when factories or "
                            "wrappers hide the surface from static AST. See "
                            "`docs/agent-recipes.md` Recipe 2 for the "
                            "canonical recovery paths."
                        ),
                        expects=(
                            "the matching framework block (e.g. "
                            "`langchain.tool_inventories`) lists at least "
                            "one JSON file; doctor reports `total_tools >= 1`."
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


def ranked_diagnostics(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    """Order diagnostics by urgency: block > warn > info.

    Within each severity bucket the input order is preserved, so callers can
    shape the catalog output deterministically.

    Split out from :func:`top_next_actions` because the control projection needs
    the *diagnostic* — its id decides who owns the route and which action kind
    it is — not just its rank-1 action. Two orderings of the same list would
    eventually disagree about which condition is primary, and then the JSON
    payload's ``next_actions[0]`` and its ``control.next_action`` would name
    different work.
    """

    severity_rank = {"block": 0, "warn": 1, "info": 2}
    return [
        diag
        for _, diag in sorted(
            enumerate(diagnostics),
            key=lambda item: (severity_rank[item[1].severity], item[0]),
        )
    ]


def top_next_actions(
    diagnostics: list[Diagnostic], *, limit: int = 3
) -> list[NextAction]:
    """Flatten ranked rank-1 actions across diagnostics."""

    return [diag.next_actions[0] for diag in ranked_diagnostics(diagnostics)[:limit]]


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
    conductor = frameworks.get("conductor") or {}
    if isinstance(conductor, dict):
        dynamic_total += int(
            conductor.get("dynamic_tool_surface_count", 0) or 0
        )
    return dynamic_total >= 1
