"""Manifest-free comparison of source-observed application agent wiring.

This entry reads immutable trees and the existing SDK/ADK adapters. It never
constructs a release manifest, supplies declarations, or publishes a verdict.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import typer

from agents_shipgate.cli.discovery import detect_workspace
from agents_shipgate.cli.discovery.artifacts import _candidate_files
from agents_shipgate.cli.scan.source_loading import _build_canonical_tools
from agents_shipgate.cli.verify.git import (
    PromisedObjectsMissingError,
    archive_fetched_tree,
    commit_sha,
    ensure_git_workspace,
    tree_sha,
)
from agents_shipgate.core.agent_bindings import resolve_agent_binding_graph
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.privacy import sanitize_report_payload
from agents_shipgate.core.verification_identity import build_engine_requirement
from agents_shipgate.inputs.google_adk import load_google_adk_artifacts
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.schemas.manifest import ToolSourceConfig

SUPPORTED = frozenset({"openai_agents_sdk", "google_adk"})
SCHEMA_VERSION = "0.1"
MAX_PYTHON_BYTES = 2_000_000


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _scope(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise typer.BadParameter("Scope must be a repository-relative directory without '..'.")
    return path.as_posix()


@dataclass
class Observations:
    scope: str
    status: str = "complete"
    agents: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    # Agents known only as a handoff target: their own construction was not read.
    handoff_only: set[tuple[str, str]] = field(default_factory=set)
    bindings: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    limits: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    coverage_gaps: list[dict[str, Any]] = field(default_factory=list)

    def gap(
        self,
        message: str,
        *,
        source: str | None = None,
        agent: str | None = None,
        tool: str | None = None,
        affects: str = "binding_presence",
    ) -> None:
        self.limits.append(message)
        self.status = "partial"
        item = {
            "source": source,
            "agent": agent,
            "tool": tool,
            "affects": affects,
            "reason": message,
        }
        if item not in self.coverage_gaps:
            self.coverage_gaps.append(item)

    def absence_gaps(
        self, key: tuple[str, str, str], moves: dict[str, str] | None = None
    ) -> list[str]:
        reasons = []
        for gap in self.coverage_gaps:
            path = gap["source"]
            if path is not None:
                path = (moves or {}).get(path, path)
            if (
                gap["affects"] == "binding_presence"
                and (path is None or key[0] == path or key[0].startswith(path + "/"))
                and (gap["agent"] is None or gap["agent"] == key[1])
                and (gap["tool"] is None or gap["tool"] == key[2])
            ):
                reasons.append(gap["reason"])
        return sorted(set(reasons))

    def summary(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "status": self.status,
            "sources": self.sources,
            "agents": list(self.agents.values()),
            "binding_count": len(self.bindings),
            "limits": sorted(set(self.limits)),
            "coverage_gaps": sorted(
                self.coverage_gaps, key=lambda gap: json.dumps(gap, sort_keys=True)
            ),
        }


def _source_path(root: Path, ref: str) -> str:
    # ADK handoff observations can carry "path.py:line" as their source.
    # The line locates evidence; it must never become callable identity.
    if (root / ref).is_file():
        return ref
    path, separator, line = ref.rpartition(":")
    if separator and line.isdecimal() and (root / path).is_file():
        return path
    return ref


def _definition(root: Path, tool: Any) -> dict[str, Any]:
    """Read a unique function's AST; line movement/comments are not changes."""
    path = _source_path(root, tool.source_ref or "")
    symbol = tool.annotations.get("python_symbol")
    if not isinstance(symbol, str):
        symbol = (
            (tool.native_locator or "").split("#")[-1]
            if "#" in (tool.native_locator or "")
            else (tool.function_signature or tool.name).split("(")[0]
        )
    file = root / path
    if not file.is_file() or not file.resolve().is_relative_to(root.resolve()):
        return {"source": path, "line": None, "implementation_sha256": None}
    try:
        tree = ast.parse(file.read_bytes())
        location = tool.source_location or ""
        line = location.rsplit(":", 1)[-1]
        nodes = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == symbol
            and (not line.isdecimal() or n.lineno == int(line))
        ]
        if not line.isdecimal():
            nodes = [n for n in tree.body if n in nodes]
    except (SyntaxError, ValueError, RecursionError):
        nodes = []
    if len(nodes) != 1:
        return {"source": path, "line": None, "implementation_sha256": None}
    node = nodes[0]
    # Docstrings are description evidence, not an implementation change.
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    node.body = body
    # Include defaults and decorators: approval decorators and changed default
    # bounds are review-relevant even when the executable body is unchanged.
    return {
        "source": path,
        "line": node.lineno,
        "implementation_sha256": _digest(
            ast.dump(
                node,
                include_attributes=False,
                **({"show_empty": True} if sys.version_info >= (3, 13) else {}),
            )
        ),
    }


def observe(tree: Path, scope: str, *, max_python_files: int) -> Observations:
    result = Observations(scope)
    root = tree / scope
    if root.is_symlink():
        raise ConfigError(f"Application scope is not a regular directory: {scope}")
    if not root.exists():
        result.status = "absent"
        result.limits.append(
            f"Scope {scope!r} is absent in this tree. If the application moved, "
            "select its old path with --base-scope and new path with --scope."
        )
        return result
    if not root.is_dir() or root.is_symlink():
        raise ConfigError(f"Application scope is not a regular directory: {scope}")
    root = root.resolve()
    if not root.is_relative_to(tree.resolve()):
        raise ConfigError(f"Application scope escapes its tree: {scope}")
    python_files = [p for p in _candidate_files(root) if p.suffix == ".py"]
    for file in python_files:
        if file.stat().st_size > MAX_PYTHON_BYTES:
            result.gap(f"Python input exceeds {MAX_PYTHON_BYTES} bytes: {file.relative_to(root)}")
    if result.limits:
        result.status = "partial"
        return result
    detected = detect_workspace(root, max_python_files=max_python_files)
    if detected.python_parse_truncated:
        result.gap(f"Python discovery truncated at {max_python_files} files.")
    for path in detected.host_discovery_incomplete_paths:
        result.gap(f"Discovery could not read {path}.", source=path)
    for item in detected.excluded_sources:
        result.gap(f"Excluded candidate: {item}", source=item.get("path"))
    # Discovery omits malformed Python; preserve that gap rather than an empty
    # candidate list becoming negative evidence. Bound this second parse too.
    if len(python_files) > max_python_files:
        result.gap(f"Python input census exceeds {max_python_files} files.")
    for file in python_files[:max_python_files]:
        if file.is_symlink():
            result.gap(
                f"Linked Python input: {file.relative_to(root)}",
                source=file.relative_to(root).as_posix(),
            )
            continue
        try:
            ast.parse(file.read_bytes())
        except (SyntaxError, ValueError, RecursionError, OSError):
            result.gap(
                f"Python input could not be parsed: {file.relative_to(root)}",
                source=file.relative_to(root).as_posix(),
            )
    for framework in detected.frameworks:
        if framework.type not in SUPPORTED and framework.candidate_files:
            for path in framework.candidate_files:
                result.gap(
                    f"Application comparison does not yet support {framework.type}: {path}",
                    source=path,
                )
    entries = sorted(
        {(f.type, p) for f in detected.frameworks if f.type in SUPPORTED for p in f.candidate_files}
    )
    sources = [
        ToolSourceConfig(id=f"{kind}:{path}", type=kind, path=path) for kind, path in entries
    ]
    result.sources = [{"type": s.type, "path": s.path} for s in sources]
    for source in sources:
        _observe_source(result, root, source)
    return result


def _observe_source(result: Observations, root: Path, source: ToolSourceConfig) -> None:
    """Each reader's unlocated gaps cover its input file, not other sources.

    A reader's agent-level observations narrow that further. Cross-module
    binding resolution remains the reader's responsibility, never a name join.
    """
    bag = ArtifactBag()
    if source.type == "openai_agents_sdk":
        loaded = [load_openai_sdk_static_tools(source, None, root)]
        artifacts = None
    else:
        loaded, artifacts = load_google_adk_artifacts(None, root, sources=[source])
        if artifacts is not None:
            bag.set("google_adk", artifacts)
    attributed = set()
    constructed, handoff_targets = set(), set()
    for item in loaded:
        for observation in item.binding_observations:
            path = _source_path(root, observation.source)
            constructed.add((path, observation.agent))
            handoff_targets.update((path, name) for name in observation.handoff_names)
            if not observation.tools_complete or not observation.handoffs_complete:
                for message in observation.issues or ["Incomplete observed binding list."]:
                    result.gap(
                        message,
                        source=_source_path(root, observation.source),
                        agent=observation.agent,
                    )
                    attributed.add(message)
        for warning in item.warnings:
            if warning not in attributed:
                result.gap(warning, source=source.path)
                attributed.add(warning)
        for omission in item.omissions:
            result.gap(f"Omitted source surface: {omission}", source=source.path)
    if artifacts is not None:
        for warning in artifacts.warnings:
            if warning not in attributed:
                result.gap(warning, source=source.path)
                attributed.add(warning)
    result.handoff_only |= handoff_targets - constructed
    tools, warnings = _build_canonical_tools(loaded)
    for warning in warnings:
        result.gap(warning, source=source.path)
    graph, _ = resolve_agent_binding_graph(None, tools, bag, loaded)
    tool_by_id = {t.id: t for t in tools}
    agent_keys = {}
    ambiguous_agents = set()
    for agent in graph.agents:
        key = (_source_path(root, agent.source_ref or ""), agent.name)
        agent_keys[agent.agent_id] = key
        if key in result.agents:
            result.gap(f"Ambiguous agent identity: {key}", source=key[0], agent=key[1])
            ambiguous_agents.add(key)
            for binding in list(result.bindings):
                if binding[:2] == key:
                    result.bindings.pop(binding)
        result.agents[key] = {
            "name": agent.name,
            "source": key[0],
            "location": agent.source_pointer,
        }
    for issue in graph.issues:
        if (
            issue.kind in {"ambiguous_root_agent", "missing_binding_evidence"}
            or issue.message in attributed
        ):
            continue
        agent_key = agent_keys.get(issue.agent_id)
        result.gap(
            issue.message,
            source=agent_key[0] if agent_key else source.path,
            agent=agent_key[1] if agent_key else None,
        )
    ambiguous_bindings = set()
    for edge in graph.tool_edges:
        key = agent_keys[edge.agent_id]
        if key in ambiguous_agents:
            continue
        tool = tool_by_id[edge.tool_id]
        definition = _definition(root, tool)
        if definition["implementation_sha256"] is None:
            result.gap(
                f"Implementation location unresolved: {tool.name} ({tool.source_ref}).",
                source=key[0],
                agent=key[1],
                tool=tool.name,
                affects="implementation",
            )
        binding_key = (*key, tool.name)
        if binding_key in ambiguous_bindings:
            continue
        if binding_key in result.bindings:
            result.gap(
                f"Ambiguous tool identity: {binding_key}",
                source=key[0],
                agent=key[1],
                tool=tool.name,
            )
            result.bindings.pop(binding_key)
            ambiguous_bindings.add(binding_key)
            continue
        result.bindings[binding_key] = {
            "agent": key[1],
            "agent_source": key[0],
            "tool": tool.name,
            "binding_location": edge.source_pointer,
            "edge_type": edge.edge_type,
            "definition": definition,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
            "signature": tool.function_signature,
            "evidence_basis": edge.provenance_kind,
        }
    for edge in graph.handoff_edges:
        source, target = agent_keys[edge.source_agent_id], agent_keys[edge.target_agent_id]
        if source in ambiguous_agents or target in ambiguous_agents:
            continue
        key = (*source, f"handoff:{target[0]}:{target[1]}")
        result.bindings[key] = {
            "agent": source[1],
            "agent_source": source[0],
            "tool": target[1],
            "edge_type": edge.edge_type,
            "target_source": target[0],
            "binding_location": edge.source_pointer,
            "evidence_basis": edge.provenance_kind,
        }


def _meaning(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in binding.items()
        if k not in {"binding_location", "definition", "evidence_basis", "agent_source"}
    } | {"implementation_sha256": binding.get("definition", {}).get("implementation_sha256")}


def compare(
    base: Observations, head: Observations, *, target_moves: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    rows = []
    for key in sorted(base.bindings.keys() | head.bindings.keys()):
        before, after = base.bindings.get(key), head.bindings.get(key)
        uncertainty = {}
        kind = "added" if before is None else "removed" if after is None else "changed"
        if before is None:
            reasons = base.absence_gaps(key, target_moves)
            if reasons:
                uncertainty["base"] = reasons
        elif after is None:
            reasons = head.absence_gaps(key)
            if reasons:
                uncertainty["head"] = reasons
        else:
            before_meaning = _meaning(before)
            if "target_source" in before_meaning:
                path = before_meaning["target_source"]
                before_meaning["target_source"] = (target_moves or {}).get(path, path)
            for side, value in (("base", before), ("head", after)):
                if "definition" in value and value["definition"]["implementation_sha256"] is None:
                    uncertainty[side] = ["The bound callable's implementation could not be read."]
            if before_meaning == _meaning(after) and not uncertainty:
                continue
        candidate_change = kind
        if uncertainty:
            kind = "not_established"
        rows.append(
            {
                "agent": key[1],
                "agent_source": key[0],
                "tool": key[2],
                "change": kind,
                "candidate_change": candidate_change if uncertainty else None,
                "uncertainty": uncertainty,
                "before": before,
                "after": after,
                "why": {
                    "added": "The source now binds this callable to this agent.",
                    "removed": "The selected source path no longer binds this callable to this agent; check scope limits for relocation.",
                    "not_established": "This candidate change cannot be established from the affected inputs; it is not a no-change result.",
                    "changed": "The bound callable's interface or implementation changed; authority direction is not established.",
                }[kind],
                "review_question": (
                    f"Resolve the named uncertainty before treating {key[1]}.{key[2]} as {candidate_change}."
                    if uncertainty
                    else f"Should {key[1]} have this {kind} binding to {key[2]}? Review the before/after signature and implementation locations."
                ),
            }
        )
    return rows


def _align_exact_moves(
    workspace: Path, base: str, head: str, old: Observations, new: Observations
) -> list[dict[str, str]]:
    from agents_shipgate.cli.verify.git import _run_git

    diff = _run_git(
        workspace,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "-z",
            "-M100%",
            base,
            head,
            "--",
        ],
    )
    if diff.returncode:
        raise ConfigError("Could not establish source relocation evidence.")
    parts = iter(diff.stdout.split("\0"))
    moves = {}
    for status in parts:
        if not status:
            continue
        source = next(parts)
        if status.startswith(("R", "C")):
            target = next(parts)
            if status == "R100":
                try:
                    left = PurePosixPath(source).relative_to(old.scope).as_posix()
                    right = PurePosixPath(target).relative_to(new.scope).as_posix()
                except ValueError:
                    continue
                moves[left] = right
    # Exact Git blob identity supplies file correspondence only, not deployed
    # agent identity. Preserve original locations in each before/after record.
    aligned = {}
    collisions = set()
    for (path, agent, tool), value in old.bindings.items():
        if "target_source" in value:
            target = value["target_source"]
            tool = f"handoff:{moves.get(target, target)}:{value['tool']}"
        key = (moves.get(path, path), agent, tool)
        if key in aligned:
            collisions.add(key)
        aligned[key] = value
    for key in collisions:
        aligned.pop(key, None)
        new.bindings.pop(key, None)
        old.gap(f"Ambiguous relocated binding: {key}", source=key[0], agent=key[1], tool=key[2])
    old.bindings = aligned
    old_names = {
        name
        for path, name in old.agents
        if moves.get(path, path)
        not in {new_path for new_path, new_name in new.agents if new_name == name}
    }
    new_names = {
        name
        for path, name in new.agents
        if path
        not in {
            moves.get(old_path, old_path) for old_path, old_name in old.agents if old_name == name
        }
    }
    for name in sorted(old_names & new_names):
        message = f"Agent {name!r} occurs at different unpaired source paths; select --base-scope/--scope or review relocation."
        old.gap(message, agent=name)
        new.gap(message, agent=name)
    return [
        {"base_source": a, "head_source": b, "basis": "git_rename_identical_blob"}
        for a, b in sorted(moves.items())
    ]


def _still_named_at(root: Path, path: str, name: str) -> int | None:
    """First line at which ``path`` still binds ``name`` or passes ``name=name``.

    Those are the two agent identities the readers key on: the SDK's bound
    variable and ADK's ``name=`` literal. An import binds as an assignment
    does, so ``from factory import agent`` keeps the name here.
    """
    file = root / path
    if not path or not file.is_file() or not file.resolve().is_relative_to(root.resolve()):
        return None
    try:
        tree = ast.parse(file.read_bytes())
    except (SyntaxError, ValueError, RecursionError, OSError):
        return None  # observe() already recorded this file as a gap.
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == name
    ] + [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if (alias.asname or alias.name.split(".", 1)[0]) == name
    ] + [
        node.value.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "name"
        and isinstance(node.value, ast.Constant)
        and node.value.value == name
    ]
    return min(lines, default=None)


def _unobserved_agent_gaps(
    old: Observations, new: Observations, base_root: Path, head_root: Path, moves: dict[str, str]
) -> None:
    """An agent observed on one side is absent from the other only if it is gone.

    Readers see only the constructions they support. An ``Agent`` subclass
    passing ``tools`` through ``super().__init__``, a factory, ``Agent[Ctx]``
    or ``.clone()`` keeps the agent while hiding its wiring. When the other
    side's file still names the agent, its bindings there are unobserved,
    never removed or newly added. Called after ``_align_exact_moves``, so
    ``old.bindings`` are keyed by head paths.
    """
    unmoves = {head: base for base, head in moves.items()}
    old_agents = {
        (moves.get(path, path), name) for path, name in old.agents.keys() - old.handoff_only
    }
    for side, root, agents, bindings, to_side in (
        (new, head_root, new.agents.keys() - new.handoff_only, old.bindings, lambda path: path),
        (old, base_root, old_agents, new.bindings, lambda path: unmoves.get(path, path)),
    ):
        for path, name in sorted({key[:2] for key in bindings} - agents):
            line = _still_named_at(root, to_side(path), name)
            if line is not None:
                side.gap(
                    f"{to_side(path)}:{line} still names agent {name!r}, but no supported "
                    "agent construction was observed for it (for example an Agent "
                    "subclass, factory or clone); its bindings on this side are not "
                    "established.",
                    source=to_side(path),
                    agent=name,
                )


def _location(scope: str, path: str | None) -> str | None:
    if path is None:
        return None
    return str(PurePosixPath(scope) / path)


def _published_binding(binding: dict[str, Any] | None, scope: str) -> dict[str, Any] | None:
    if binding is None:
        return None
    result = dict(binding)
    result["agent_source"] = _location(scope, result["agent_source"])
    result["binding_location"] = _location(scope, result.get("binding_location"))
    if "target_source" in result:
        result["target_source"] = _location(scope, result["target_source"])
    if "definition" in result:
        result["definition"] = dict(result["definition"])
        result["definition"]["source"] = _location(scope, result["definition"]["source"])
    return result


def run_application_diff(
    *,
    workspace: Path,
    base: str | None,
    head: str,
    scope: str,
    base_scope: str | None,
    max_python_files: int,
    json_output: bool,
) -> int:
    from agents_shipgate.cli.diff import _one_line, _refuse_objects_missing, _resolve_base

    workspace = ensure_git_workspace(workspace)
    scope, old_scope = _scope(scope), _scope(base_scope if base_scope is not None else scope)
    if not head.strip() or head.startswith("-"):
        raise typer.BadParameter("Head ref must be non-empty and cannot start with a dash.")
    head_commit = commit_sha(workspace, head)
    if head_commit is None:
        raise typer.BadParameter(f"Head ref {head!r} is unavailable locally. Fetch it first.")
    from agents_shipgate.cli.verify.git import detect_default_base

    base_ref = (
        base
        if base is not None
        else detect_default_base(
            workspace, head_commit, allow_local_when_no_remote=True, allow_equal_head=True
        )
    )
    if base_ref is None:
        # Keep the established missing-base diagnostic and recovery wording.
        _resolve_base(workspace, None, head_commit)
        raise ConfigError("No comparison base is available.")
    if not base_ref.strip() or base_ref.startswith("-"):
        raise typer.BadParameter("Base ref must be non-empty and cannot start with a dash.")
    requested_base_commit = commit_sha(workspace, base_ref)
    _, base_commit = _resolve_base(workspace, requested_base_commit or base_ref, head_commit)
    engine = build_engine_requirement(plugins_enabled=False).model_dump(mode="json")
    with tempfile.TemporaryDirectory(prefix="shipgate-application-diff-") as raw:
        scratch = Path(raw)
        for side, ref, commit, selected_scope in (
            ("base", base_ref, base_commit, old_scope),
            ("head", head, head_commit, scope),
        ):

            def in_scope(path: str, selected: str = selected_scope) -> bool:
                return path == selected or path.startswith(selected + "/")

            try:
                archive_fetched_tree(
                    workspace,
                    commit,
                    scratch / side,
                    scope=None if selected_scope == "." else in_scope,
                )
            except PromisedObjectsMissingError:
                _refuse_objects_missing(workspace, ref, commit, side=side)
        old = observe(scratch / "base", old_scope, max_python_files=max_python_files)
        new = observe(scratch / "head", scope, max_python_files=max_python_files)
        if old.status == new.status == "absent":
            raise ConfigError(
                f"Neither comparison tree contains the selected scopes: "
                f"base={old_scope!r}, head={scope!r}. Check --scope/--base-scope."
            )
        moves = _align_exact_moves(workspace, base_commit, head_commit, old, new)
        target_moves = {m["base_source"]: m["head_source"] for m in moves}
        _unobserved_agent_gaps(
            old, new, scratch / "base" / old_scope, scratch / "head" / scope, target_moves
        )
        rows = compare(old, new, target_moves=target_moves)
        for row in rows:
            row["before"] = _published_binding(row["before"], old_scope)
            row["after"] = _published_binding(row["after"], scope)
    status = "partial" if "partial" in {old.status, new.status} else "compared"
    if not old.agents and not new.agents and status == "compared":
        status = "not_established"
    payload = {
        "application_comparison_schema_version": SCHEMA_VERSION,
        "comparison_status": status,
        "static_analysis_only": True,
        "comparison_basis": "source_observed_per_agent_wiring",
        "input_origin": "independent_tree_discovery",
        "engine": engine,
        "base": {
            "requested_ref": base_ref,
            "requested_commit": requested_base_commit,
            "compared_commit": base_commit,
            "tree": tree_sha(workspace, base_commit),
            **old.summary(),
        },
        "head": {
            "requested_ref": head,
            "compared_commit": head_commit,
            "tree": tree_sha(workspace, head_commit),
            **new.summary(),
        },
        "options": {"max_python_files": max_python_files, "max_python_bytes": MAX_PYTHON_BYTES},
        "source_correspondence": moves,
        "rows": rows,
        "limits": [
            "Covers supported OpenAI Agents SDK and Google ADK source wiring only.",
            "Deployment-root reachability, runtime behavior, indirect helper effects and business authority are not established.",
            "This comparison is advisory evidence and supplies no release verdict or merge permission.",
        ],
    }
    payload = sanitize_report_payload(payload)
    payload["comparison_id"] = _digest(payload)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"Application comparison: {status} ({base_commit[:12]} → {head_commit[:12]})")
        for row in payload["rows"]:
            typer.echo(
                f"{row['change'].upper()}  {_one_line(row['agent'])} → {_one_line(row['tool'])}"
            )
            for side in ("before", "after"):
                value = row[side]
                if value is None:
                    typer.echo(
                        f"  {side}: "
                        + (
                            "binding presence not established"
                            if row["uncertainty"]
                            else "no observed binding"
                        )
                    )
                else:
                    definition = value.get("definition", {})
                    typer.echo(
                        f"  {side}: {_one_line(value.get('signature') or value['tool'])} at {_one_line(value.get('binding_location'))}"
                    )
                    if definition:
                        typer.echo(
                            f"    implementation: {_one_line(definition['source'])}:{definition['line']} ({str(definition['implementation_sha256'])[:12]})"
                        )
            typer.echo(f"  {_one_line(row['why'])}")
            for side, reasons in row["uncertainty"].items():
                for reason in reasons:
                    typer.echo(f"  {side} uncertainty: {_one_line(reason)}")
            typer.echo(f"  Review: {_one_line(row['review_question'])}")
        if not rows:
            typer.echo(
                "No supported application agents were established."
                if status == "not_established"
                else "Incomplete comparison; this is not a no-change result."
                if status == "partial"
                else "No established binding/interface/implementation changes in the observed surface."
            )
        for side in ("base", "head"):
            for limit in payload[side]["limits"]:
                typer.echo(f"  {side} limit: {_one_line(limit)}")
        typer.echo(payload["limits"][-1])
    return 0
