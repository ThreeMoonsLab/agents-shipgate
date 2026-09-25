"""Manifest-free comparison of source-observed application agent wiring.

This entry reads immutable trees and the existing SDK/ADK adapters. It never
constructs a release manifest, supplies declarations, or publishes a verdict.
"""
from __future__ import annotations

import ast
import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import typer

from agents_shipgate.cli.discovery import detect_workspace
from agents_shipgate.cli.discovery.artifacts import _candidate_files
from agents_shipgate.cli.scan.source_loading import _build_canonical_tools
from agents_shipgate.cli.verify.git import archive_tree, commit_sha, ensure_git_workspace, tree_sha
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
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
    bindings: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    limits: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {"scope": self.scope, "status": self.status,
                "sources": self.sources, "agents": list(self.agents.values()),
                "binding_count": len(self.bindings), "limits": sorted(set(self.limits))}



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
    symbol = (tool.native_locator or "").split("#")[-1] or tool.name
    if "#" not in (tool.native_locator or ""):
        symbol = (tool.function_signature or tool.name).split("(")[0]
    file = root / path
    if not file.is_file() or not file.resolve().is_relative_to(root.resolve()):
        return {"source": path, "line": None, "implementation_sha256": None}
    try:
        nodes = [n for n in ast.walk(ast.parse(file.read_bytes()))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == symbol]
    except (SyntaxError, ValueError, RecursionError):
        nodes = []
    if len(nodes) != 1:
        return {"source": path, "line": None, "implementation_sha256": None}
    node = nodes[0]
    # Docstrings are description evidence, not an implementation change.
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    node.body = body
    # Include defaults and decorators: approval decorators and changed default
    # bounds are review-relevant even when the executable body is unchanged.
    return {"source": path, "line": node.lineno,
            "implementation_sha256": _digest(ast.dump(node, include_attributes=False))}


def observe(tree: Path, scope: str, *, max_python_files: int) -> Observations:
    result = Observations(scope)
    root = tree / scope
    if not root.exists():
        result.status = "absent"
        return result
    if not root.is_dir() or root.is_symlink():
        raise ConfigError(f"Application scope is not a regular directory: {scope}")
    root = root.resolve()
    if not root.is_relative_to(tree.resolve()):
        raise ConfigError(f"Application scope escapes its tree: {scope}")
    python_files = [p for p in _candidate_files(root) if p.suffix == ".py"]
    for file in python_files:
        if file.stat().st_size > MAX_PYTHON_BYTES:
            result.limits.append(f"Python input exceeds {MAX_PYTHON_BYTES} bytes: {file.relative_to(root)}")
    if result.limits:
        result.status = "partial"
        return result
    detected = detect_workspace(root, max_python_files=max_python_files)
    if detected.python_parse_truncated:
        result.limits.append(f"Python discovery truncated at {max_python_files} files.")
    result.limits.extend(f"Discovery could not read {p}." for p in detected.host_discovery_incomplete_paths)
    result.limits.extend(f"Excluded candidate: {item}" for item in detected.excluded_sources)
    # Discovery omits malformed Python; preserve that gap rather than an empty
    # candidate list becoming negative evidence. Bound this second parse too.
    if len(python_files) > max_python_files:
        result.limits.append(f"Python input census exceeds {max_python_files} files.")
    for file in python_files[:max_python_files]:
        if file.is_symlink():
            result.limits.append(f"Linked Python input: {file.relative_to(root)}")
            continue
        try:
            ast.parse(file.read_bytes())
        except (SyntaxError, ValueError, RecursionError, OSError):
            result.limits.append(f"Python input could not be parsed: {file.relative_to(root)}")
    for framework in detected.frameworks:
        if framework.type not in SUPPORTED and framework.candidate_files:
            result.limits.append(f"Application comparison does not yet support {framework.type}: {', '.join(framework.candidate_files)}")
    entries = sorted({(f.type, p) for f in detected.frameworks if f.type in SUPPORTED
                      for p in f.candidate_files})
    sources = [ToolSourceConfig(id=f"{kind}:{path}", type=kind, path=path) for kind, path in entries]
    result.sources = [{"type": s.type, "path": s.path} for s in sources]
    bag = ArtifactBag()
    loaded = []
    for source in sources:
        if source.type == "openai_agents_sdk":
            loaded.append(load_openai_sdk_static_tools(source, None, root))
    adk, artifacts = load_google_adk_artifacts(None, root, sources=sources)
    loaded.extend(adk)
    if artifacts is not None:
        bag.set("google_adk", artifacts)
        result.limits.extend(artifacts.warnings)
    result.limits.extend(w for item in loaded for w in item.warnings)
    result.limits.extend(f"Omitted source surface: {o}" for item in loaded for o in item.omissions)
    tools, warnings = _build_canonical_tools(loaded)
    result.limits.extend(warnings)
    graph, _ = resolve_agent_binding_graph(None, tools, bag, loaded)
    # Deployment-root selection is irrelevant to observed per-agent edges.
    # A root with an explicit empty tool list can also leave unrelated catalog
    # definitions unbound. That is a release-scope concern, not missing observed
    # wiring. Dynamic/partial lists have their own retained reader/graph issues.
    result.limits.extend(i.message for i in graph.issues if i.kind not in
                         {"ambiguous_root_agent", "missing_binding_evidence"})
    tool_by_id = {t.id: t for t in tools}
    agent_keys = {}
    ambiguous_agents = set()
    for agent in graph.agents:
        key = (_source_path(root, agent.source_ref or ""), agent.name)
        agent_keys[agent.agent_id] = key
        if key in result.agents:
            result.limits.append(f"Ambiguous agent identity: {key}")
            ambiguous_agents.add(key)
        result.agents[key] = {"name": agent.name, "source": key[0],
                              "location": agent.source_pointer}
    ambiguous_bindings = set()
    for edge in graph.tool_edges:
        key = agent_keys[edge.agent_id]
        if key in ambiguous_agents:
            continue
        tool = tool_by_id[edge.tool_id]
        definition = _definition(root, tool)
        if definition["implementation_sha256"] is None:
            result.limits.append(f"Implementation location unresolved: {tool.name} ({tool.source_ref}).")
        binding_key = (*key, tool.name)
        if binding_key in ambiguous_bindings:
            continue
        if binding_key in result.bindings:
            result.limits.append(f"Ambiguous tool identity: {binding_key}")
            result.bindings.pop(binding_key)
            ambiguous_bindings.add(binding_key)
            continue
        result.bindings[binding_key] = {
            "agent": key[1], "agent_source": key[0], "tool": tool.name,
            "binding_location": edge.source_pointer, "edge_type": edge.edge_type,
            "definition": definition, "input_schema": tool.input_schema,
            "output_schema": tool.output_schema, "signature": tool.function_signature,
            "evidence_basis": edge.provenance_kind,
        }
    for edge in graph.handoff_edges:
        source, target = agent_keys[edge.source_agent_id], agent_keys[edge.target_agent_id]
        if source in ambiguous_agents or target in ambiguous_agents:
            continue
        key = (*source, f"handoff:{target[0]}:{target[1]}")
        result.bindings[key] = {"agent": source[1], "agent_source": source[0],
                                "tool": target[1], "edge_type": edge.edge_type,
                                "target_source": target[0], "binding_location": edge.source_pointer,
                                "evidence_basis": edge.provenance_kind}
    if result.limits:
        result.status = "partial"
    return result


def _meaning(binding: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in binding.items() if k not in
            {"binding_location", "definition", "evidence_basis", "agent_source"}} | {
                "implementation_sha256": binding.get("definition", {}).get("implementation_sha256")}


def compare(base: Observations, head: Observations, *,
            target_moves: dict[str, str] | None = None) -> list[dict[str, Any]]:
    rows = []
    for key in sorted(base.bindings.keys() | head.bindings.keys()):
        before, after = base.bindings.get(key), head.bindings.get(key)
        if before is not None and after is not None:
            before_meaning = _meaning(before)
            if "target_source" in before_meaning:
                path = before_meaning["target_source"]
                before_meaning["target_source"] = (target_moves or {}).get(path, path)
            if before_meaning == _meaning(after):
                continue
        if before is None and base.status == "partial":
            head.limits.append(f"Cannot establish whether {key[1]}.{key[2]} was absent at base.")
            continue
        if after is None and head.status == "partial":
            head.limits.append(f"Cannot establish removal of {key[1]}.{key[2]} from incomplete head inputs.")
            continue
        kind = "added" if before is None else "removed" if after is None else "changed"
        rows.append({"agent": key[1], "agent_source": key[0], "tool": key[2],
                     "change": kind, "before": before, "after": after,
                     "why": {"added": "The source now binds this callable to this agent.",
                             "removed": "The source no longer binds this callable to this agent.",
                             "changed": "The bound callable's interface or implementation changed; authority direction is not established."}[kind],
                     "review_question": f"Should {key[1]} have this {kind} binding to {key[2]}? Review the before/after signature and implementation locations."})
    return rows



def _align_exact_moves(workspace: Path, base: str, head: str,
                       old: Observations, new: Observations) -> list[dict[str, str]]:
    from agents_shipgate.cli.verify.git import _run_git

    diff = _run_git(workspace, ["diff", "--no-ext-diff", "--no-textconv", "--name-status",
                                "-z", "-M100%", base, head, "--"])
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
        old.limits.append(f"Ambiguous relocated binding: {key}")
        old.status = "partial"
    old.bindings = aligned
    old_names = {name for path, name in old.agents if moves.get(path, path) not in
                 {new_path for new_path, new_name in new.agents if new_name == name}}
    new_names = {name for path, name in new.agents if path not in
                 {moves.get(old_path, old_path) for old_path, old_name in old.agents if old_name == name}}
    for name in sorted(old_names & new_names):
        message = f"Agent {name!r} occurs at different unpaired source paths; select --base-scope/--scope or review relocation."
        old.limits.append(message)
        new.limits.append(message)
        old.status = new.status = "partial"
    return [{"base_source": a, "head_source": b, "basis": "git_rename_identical_blob"}
            for a, b in sorted(moves.items())]


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

def run_application_diff(*, workspace: Path, base: str | None, head: str,
                         scope: str, base_scope: str | None,
                         max_python_files: int, json_output: bool) -> int:
    from agents_shipgate.cli.diff import _one_line, _resolve_base

    workspace = ensure_git_workspace(workspace)
    scope, old_scope = _scope(scope), _scope(base_scope if base_scope is not None else scope)
    if not head.strip() or head.startswith("-"):
        raise typer.BadParameter("Head ref must be non-empty and cannot start with a dash.")
    head_commit = commit_sha(workspace, head)
    if head_commit is None:
        raise typer.BadParameter(f"Head ref {head!r} is unavailable locally. Fetch it first.")
    from agents_shipgate.cli.verify.git import detect_default_base

    base_ref = base if base is not None else detect_default_base(
        workspace, head_commit, allow_local_when_no_remote=True, allow_equal_head=True)
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
        archive_tree(workspace, base_commit, scratch / "base")
        archive_tree(workspace, head_commit, scratch / "head")
        old = observe(scratch / "base", old_scope, max_python_files=max_python_files)
        new = observe(scratch / "head", scope, max_python_files=max_python_files)
        moves = _align_exact_moves(workspace, base_commit, head_commit, old, new)
        rows = compare(old, new, target_moves={m["base_source"]: m["head_source"] for m in moves})
        for row in rows:
            row["before"] = _published_binding(row["before"], old_scope)
            row["after"] = _published_binding(row["after"], scope)
    status = "partial" if "partial" in {old.status, new.status} else "compared"
    if not old.agents and not new.agents and status == "compared":
        status = "not_established"
    payload = {
        "application_comparison_schema_version": SCHEMA_VERSION,
        "comparison_status": status, "static_analysis_only": True,
        "comparison_basis": "source_observed_per_agent_wiring",
        "input_origin": "independent_tree_discovery", "engine": engine,
        "base": {"requested_ref": base_ref, "requested_commit": requested_base_commit,
                 "compared_commit": base_commit, "tree": tree_sha(workspace, base_commit), **old.summary()},
        "head": {"requested_ref": head, "compared_commit": head_commit,
                 "tree": tree_sha(workspace, head_commit), **new.summary()},
        "options": {"max_python_files": max_python_files, "max_python_bytes": MAX_PYTHON_BYTES},
        "source_correspondence": moves, "rows": rows,
        "limits": ["Covers supported OpenAI Agents SDK and Google ADK source wiring only.",
                   "Deployment-root reachability, runtime behavior, indirect helper effects and business authority are not established.",
                   "This comparison is advisory evidence and supplies no release verdict or merge permission."],
    }
    payload["comparison_id"] = _digest(payload)
    payload = sanitize_report_payload(payload)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"Application comparison: {status} ({base_commit[:12]} → {head_commit[:12]})")
        for row in payload["rows"]:
            typer.echo(f"{row['change'].upper()}  {_one_line(row['agent'])} → {_one_line(row['tool'])}")
            for side in ("before", "after"):
                value = row[side]
                if value is None:
                    typer.echo(f"  {side}: no observed binding")
                else:
                    definition = value.get("definition", {})
                    typer.echo(f"  {side}: {_one_line(value.get('signature') or value['tool'])} at {_one_line(value.get('binding_location'))}")
                    if definition:
                        typer.echo(f"    implementation: {_one_line(definition['source'])}:{definition['line']} ({str(definition['implementation_sha256'])[:12]})")
            typer.echo(f"  {_one_line(row['why'])}")
            typer.echo(f"  Review: {_one_line(row['review_question'])}")
        if not rows:
            typer.echo("No supported application agents were established." if status == "not_established" else
                       "No established binding/interface/implementation changes in the observed surface.")
        for side in ("base", "head"):
            for limit in payload[side]["limits"]:
                typer.echo(f"  {side} limit: {_one_line(limit)}")
        typer.echo(payload["limits"][-1])
    return 0
