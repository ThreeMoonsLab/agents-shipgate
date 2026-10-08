"""Manifest-free comparison of source-observed application agent wiring.

This entry reads immutable trees and the existing SDK/ADK adapters. It never
constructs a release manifest, supplies declarations, or publishes a verdict.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import typer

from agents_shipgate.cli.application_scope import derive_scopes
from agents_shipgate.cli.discovery import detect_workspace
from agents_shipgate.cli.discovery.artifacts import _candidate_files, _skip_part
from agents_shipgate.cli.discovery.signals import _is_test_path
from agents_shipgate.cli.scan.source_loading import _build_canonical_tools
from agents_shipgate.cli.verify.git import (
    PromisedObjectsMissingError,
    _run_git_bounded_output,
    archive_fetched_tree,
    commit_sha,
    ensure_git_workspace,
    tree_sha,
)
from agents_shipgate.core.adopter_text import DUPLICATE_TOOL_IN_SOURCE
from agents_shipgate.core.agent_bindings import adk_unnamed_sub_agents, resolve_agent_binding_graph
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.domain import ANY_TOOL
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.privacy import sanitize_report_payload
from agents_shipgate.core.semantic_assessment import (
    REACH_CLAIM_SOURCE,
    REACH_CLAIM_SOURCES,
    assess_tool_semantics,
)
from agents_shipgate.core.verification_identity import build_engine_requirement
from agents_shipgate.inputs.google_adk import adk_agent_subclasses, load_google_adk_artifacts
from agents_shipgate.inputs.object_tools import object_display, reading_object_bindings
from agents_shipgate.inputs.openai_sdk_static import (
    census_module,
    load_openai_sdk_static_tools,
)
from agents_shipgate.inputs.python_imports import (
    ImportResolver,
    RepositoryLayout,
    repository_layout,
)
from agents_shipgate.inputs.tool_reach import read_tool_reach
from agents_shipgate.schemas.manifest import ToolSourceConfig

SUPPORTED = frozenset({"openai_agents_sdk", "google_adk"})
SCHEMA_VERSION = "0.4"
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
    #: Unpopulated submodules under the scope, by scope-relative path.
    submodules: dict[str, str] = field(default_factory=dict)
    #: Links under the scope that resolve to nothing in the tree.
    unresolved_links: list[str] = field(default_factory=list)
    #: Test files under the scope, which never establish the application
    #: (#876): a test double's agent is not the application's agent.
    excluded_tests: list[str] = field(default_factory=list)

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

    def tool_gaps(self, key: tuple[str, str, str]) -> list[str]:
        """Gaps naming this one binding, which even a present binding carries."""

        return sorted(
            {
                gap["reason"]
                for gap in self.coverage_gaps
                if gap["affects"] == "binding_presence"
                and gap["tool"] == key[2]
                and gap["agent"] in (None, key[1])
                and (gap["source"] is None or key[0] == gap["source"])
            }
        )

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
            "excluded_tests": sorted(self.excluded_tests),
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


def _definition(root: Path, tool: Any, agent: str | None = None) -> dict[str, Any]:
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
    code = ast.dump(
        node,
        include_attributes=False,
        **({"show_empty": True} if sys.version_info >= (3, 13) else {}),
    )
    # A tool a factory makes also holds what the factory and this agent's
    # calls to it put in its closure (#865 review).
    calls = (getattr(tool, "extraction", None) or {}).get("factory_calls")
    made_by = [str(item) for item in calls.get(agent, [])] if isinstance(calls, dict) and agent is not None else []
    # ``unknown:<digest>:<which value>``: the call as written is compared,
    # the value it names is not — an open question, not a change.
    unnamed = [item.split(":", 2)[2] for item in made_by if item.startswith("unknown:")]
    # ``function_tool(f, needs_approval=...)``: the wrapper's arguments are part
    # of the tool, as a decorator's are (#910).
    wrapper = (getattr(tool, "extraction", None) or {}).get("wrapper_options_sha256")
    if isinstance(wrapper, str):
        code += "|wrapper:" + wrapper
    if made_by:
        code += "|factory:" + ",".join(item.split(":", 2)[1] if item.startswith("unknown:") else item for item in made_by)
    result = {
        "source": path,
        "line": node.lineno,
        "implementation_sha256": _digest(code),
    }
    if unnamed:
        result["unestablished"] = (
            f"A factory call that makes {tool.name} gives it a value this read cannot name "
            f"({'; '.join(sorted(set(unnamed)))}), which its closure holds; whether that value "
            "changed is not established."
        )
    return result


def _reach(root: Path, tool: Any) -> dict[str, Any]:
    """The outbound HTTP calls the tool's own code makes, read statically (#872).

    Evidence beside the signature, never meaning: what a tool reaches can say
    what a binding lets the model do, but a change confined to a helper is not
    a changed binding here.
    """
    path = _source_path(root, tool.source_ref or "")
    symbol = tool.annotations.get("python_symbol")
    if not isinstance(symbol, str):
        symbol = (
            (tool.native_locator or "").split("#")[-1]
            if "#" in (tool.native_locator or "")
            else (tool.function_signature or tool.name).split("(")[0]
        )
    file = root / path
    if (
        not path.endswith(".py")
        or not file.is_file()
        or not file.resolve().is_relative_to(root.resolve())
    ):
        return {}
    location = tool.source_location or ""
    line = location.rsplit(":", 1)[-1]
    try:
        text = file.read_bytes().decode("utf-8", errors="replace")
        tree = ast.parse(text)
        nodes = [
            (node, enclosing)
            for node, enclosing in _functions(tree)
            if node.name == symbol and (not line.isdecimal() or node.lineno == int(line))
        ]
        if not line.isdecimal():
            nodes = [(node, enclosing) for node, enclosing in nodes if node in tree.body]
        if len(nodes) != 1:
            return {}
        resolver = ImportResolver(root)
        module = resolver.entry(file, tree, text)
        if module is None:
            return {}
        properties = (tool.input_schema or {}).get("properties")
        return read_tool_reach(
            resolver,
            module,
            nodes[0][0],
            model_params=frozenset(properties) if isinstance(properties, dict) else frozenset(),
            enclosing=nodes[0][1],
            is_test=_is_test_path,
        )
    except (SyntaxError, ValueError, RecursionError, OSError):
        return {}


def _functions(
    tree: ast.Module,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.FunctionDef | ast.AsyncFunctionDef | None]]:
    """Every function, with the function that directly encloses it, if any."""
    found: list[
        tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.FunctionDef | ast.AsyncFunctionDef | None]
    ] = []
    stack: list[tuple[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]] = [(tree, None)]
    while stack:
        node, enclosing = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                found.append((child, enclosing))
                stack.append((child, child))
            elif isinstance(child, ast.ClassDef):
                stack.append((child, None))
            else:
                stack.append((child, enclosing))
    return found


def _effect_evidence(tool: Any, reach: dict[str, Any]) -> dict[str, Any]:
    """The existing semantic assessment of the tool, given what it reaches."""
    assessed = assess_tool_semantics(
        tool.model_copy(update={"extraction": {**tool.extraction, "reach": reach}})
    )
    return {
        "conservative_effect": assessed.conservative_effect,
        "status": assessed.effect.status,
        "claims": [
            {
                "value": claim.value,
                "source": claim.source,
                "basis": claim.basis,
                "at": claim.source_pointer,
            }
            for claim in assessed.effect.claims
            if claim.dimension == "effect"
        ],
    }


#: Bytes one repository-directory listing may take.
_MAX_LAYOUT_LISTING_BYTES = 4 * 1024 * 1024


#: The most blob bytes one batched read of a directory's modules holds.
_MAX_LAYOUT_BATCH_BYTES = 16 * 1024 * 1024


def _batch_blobs(workspace: Path, pending: list[tuple[str, str, int]]) -> dict[str, str]:
    """``path -> text`` for ``(path, object, size)`` blobs, in bounded ``cat-file --batch``
    reads; a blob a read does not return is simply absent."""

    texts: dict[str, str] = {}
    chunk: list[tuple[str, str, int]] = []
    total = 0
    for item in [*pending, None]:
        if item is not None and (not chunk or total + item[2] <= _MAX_LAYOUT_BATCH_BYTES):
            chunk.append(item)
            total += item[2]
            continue
        output = _run_git_bounded_output(
            workspace,
            ["cat-file", "--batch"],
            max_output_bytes=sum(size + 128 for _, _, size in chunk),
            input=b"".join(oid.encode("ascii") + b"\n" for _, oid, _ in chunk),
        ) if chunk else None
        offset = 0
        for name, oid, size in chunk if output is not None else []:
            header_end = output.find(b"\n", offset)
            if header_end < 0 or output[offset:header_end].split() != [
                oid.encode("ascii"), b"blob", str(size).encode("ascii")
            ]:
                break
            start = header_end + 1
            texts[name] = output[start : start + size].decode("utf-8", errors="replace")
            offset = start + size + 1
        chunk, total = ([item], item[2]) if item is not None else ([], 0)
    return texts


def _git_layout(workspace: Path, commit: str, scope: str) -> RepositoryLayout:
    """The commit's tree outside the scope, listed one directory at a time (#879 review)."""

    listings: dict[str, tuple[frozenset[str], frozenset[str]] | None] = {}
    #: ``path -> (object, size)`` of every regular ``.py`` file listed.
    blobs: dict[str, tuple[str, int]] = {}
    contents: dict[str, str | None] = {}
    batched: set[str] = set()

    def listing(path: str) -> tuple[frozenset[str], frozenset[str]] | None:
        if path not in listings:
            args = ["--literal-pathspecs", "ls-tree", "-z", "-l", commit]
            if path:
                args += ["--", f"{path}/"]
            output = _run_git_bounded_output(
                workspace, args, max_output_bytes=_MAX_LAYOUT_LISTING_BYTES
            )
            names: set[str] = set()
            links: set[str] = set()
            for raw in (output or b"").split(b"\0"):
                if not raw or b"\t" not in raw:
                    continue
                meta, _, name_bytes = raw.partition(b"\t")
                name = PurePosixPath(name_bytes.decode("utf-8", errors="replace")).name
                names.add(name)
                fields = meta.split()
                if fields[:1] in ([b"120000"], [b"160000"]):
                    # A symbolic link or a submodule: reachable, not read.
                    links.add(name)
                elif fields[:1] in ([b"100644"], [b"100755"]) and len(fields) == 4 and name.endswith(".py"):
                    full = f"{path}/{name}" if path else name
                    blobs[full] = (fields[2].decode("ascii"), int(fields[3]))
            listings[path] = (frozenset(names), frozenset(links)) if names else None
        return listings[path]

    def entries(path: str) -> frozenset[str] | None:
        found = listing(path)
        return found[0] if found is not None else None

    def links(path: str) -> frozenset[str]:
        found = listing(path)
        return found[1] if found is not None else frozenset()

    def read(path: str) -> str | None:
        """A regular ``.py`` file's text — never a link's target path — read
        with its directory's other modules in one ``cat-file --batch``."""

        if path in contents:
            return contents[path]
        directory = str(PurePosixPath(path).parent) if "/" in path else ""
        listing(directory)
        if path not in blobs:
            # Missing, a link, a submodule, or not a regular file.
            return None
        if directory not in batched:
            batched.add(directory)
            pending = [
                (name, *blobs[name])
                for name in sorted(blobs)
                if name not in contents
                and (str(PurePosixPath(name).parent) if "/" in name else "") == directory
                and blobs[name][1] <= _MAX_LAYOUT_LISTING_BYTES
            ]
            # One batch per directory only while it is small: a directory of
            # generated or vendored modules is read file by file, as asked
            # (#879 review).
            if sum(size for _, _, size in pending) <= _MAX_LAYOUT_BATCH_BYTES:
                for name, text in _batch_blobs(workspace, pending).items():
                    contents[name] = text
        if path not in contents:
            oid, size = blobs[path]
            output = (
                _run_git_bounded_output(
                    workspace, ["cat-file", "blob", oid], max_output_bytes=_MAX_LAYOUT_LISTING_BYTES
                )
                if size <= _MAX_LAYOUT_LISTING_BYTES
                else None
            )
            # None: too large or unreadable; an empty file reads as "".
            contents[path] = output.decode("utf-8", errors="replace") if output is not None else None
        return contents[path]

    return RepositoryLayout("" if scope in {"", "."} else scope, entries, links, read)


@dataclass
class _Discovered:
    """One side after discovery, before any module is read (#876 review).

    Whether either side found an agent source decides whether the census
    runs, so both sides are discovered before either is read.
    """

    result: Observations
    root: Path | None = None
    python_files: list[Path] = field(default_factory=list)
    linked: set[str] = field(default_factory=set)
    detected: Any = None
    tests: set[str] = field(default_factory=set)
    #: ``(framework, path)`` for every supported agent source to read.
    entries: list[tuple[str, str]] = field(default_factory=list)


def discover(
    tree: Path,
    scope: str,
    *,
    max_python_files: int,
    gitlinks: dict[str, str] | None = None,
) -> _Discovered:
    result = Observations(scope)
    for path, commit in (gitlinks or {}).items():
        # A gitlink outside the scope arrived with an in-scope link's target,
        # which discovery never walks into; it is not this scope's to name.
        if scope == ".":
            result.submodules[path] = commit
        elif PurePosixPath(path).is_relative_to(scope):
            result.submodules[PurePosixPath(path).relative_to(scope).as_posix()] = commit
    root = tree / scope
    if root.is_symlink():
        raise ConfigError(f"Application scope is not a regular directory: {scope}")
    if not root.exists():
        result.status = "absent"
        result.limits.append(
            f"Scope {scope!r} is absent in this tree. If the application moved, "
            "select its old path with --base-scope and new path with --scope."
        )
        return _Discovered(result)
    if not root.is_dir() or root.is_symlink():
        raise ConfigError(f"Application scope is not a regular directory: {scope}")
    root = root.resolve()
    if not root.is_relative_to(tree.resolve()):
        raise ConfigError(f"Application scope escapes its tree: {scope}")
    python_files = [p for p in _candidate_files(root) if p.suffix == ".py"]
    # Test code is not the application (#876). A test double's agent must not
    # establish the scope, and a test file's own defects (a tool defined twice,
    # an unsupported framework, a parse failure) must not stand in for the
    # application's. Tests are listed, not read.
    result.excluded_tests = sorted(
        p.relative_to(root).as_posix()
        for p in python_files
        if _is_test_path(p.relative_to(root).as_posix())
    )
    tests = set(result.excluded_tests)
    python_files = [p for p in python_files if p.relative_to(root).as_posix() not in tests]
    for file in python_files:
        if file.stat().st_size > MAX_PYTHON_BYTES:
            result.gap(f"Python input exceeds {MAX_PYTHON_BYTES} bytes: {file.relative_to(root)}")
    if result.limits:
        result.status = "partial"
        return _Discovered(result)
    linked = _observe_links(result, tree.resolve(), root, python_files)
    detected = detect_workspace(root, max_python_files=max_python_files)
    if detected.python_parse_truncated:
        result.gap(f"Python discovery truncated at {max_python_files} files.")
    # `detected.host_discovery_incomplete_paths` is deliberately not a gap. It
    # is the host-configuration census, which counts every link that could
    # conceal a host path, `CLAUDE.md -> AGENTS.md` included. The links that can
    # conceal application source were censused above, by `_observe_links`.
    for item in detected.excluded_sources:
        result.gap(f"Excluded candidate: {item}", source=item.get("path"))
    entries = sorted(
        {
            (f.type, p)
            for f in detected.frameworks
            if f.type in SUPPORTED
            for p in f.candidate_files
            if p not in linked and p not in tests
        }
    )
    return _Discovered(result, root, python_files, linked, detected, tests, entries)


def observe(found: _Discovered, *, max_python_files: int, census: bool) -> Observations:
    """Read one discovered side: every module's parse, the census, its sources.

    ``census`` is False only when neither side found a supported agent source
    (#876 review). No agent is then read on either side, so no census limit
    could qualify a row: it would only turn ``not_established`` into
    ``partial`` over the repository's own ``.tools`` (``artifacts.tools.append``).
    """

    result = found.result
    if found.root is None:
        return result
    root, python_files, linked = found.root, found.python_files, found.linked
    # Discovery omits malformed Python; preserve that gap rather than an empty
    # candidate list becoming negative evidence. Bound this second parse too.
    if len(python_files) > max_python_files:
        result.gap(f"Python input census exceeds {max_python_files} files.")
    parsed: dict[str, tuple[ast.Module, str]] = {}
    for file in python_files[:max_python_files]:
        relative = file.relative_to(root).as_posix()
        if relative in linked:
            continue
        try:
            raw = file.read_bytes()
            tree = ast.parse(raw)
        except (SyntaxError, ValueError, RecursionError, OSError):
            result.gap(
                f"Python input could not be parsed: {relative}",
                source=relative,
            )
            continue
        if census:
            parsed[relative] = (tree, raw.decode("utf-8", errors="replace"))
    for framework in found.detected.frameworks:
        if framework.type not in SUPPORTED and framework.candidate_files:
            for path in framework.candidate_files:
                if path in found.tests:
                    continue
                result.gap(
                    f"Application comparison does not yet support {framework.type}: {path}",
                    source=path,
                )
    if census:
        _census(result, root, parsed, python_files, found.detected, found.entries)
    sources = [
        ToolSourceConfig(id=f"{kind}:{path}", type=kind, path=path) for kind, path in found.entries
    ]
    result.sources = [{"type": s.type, "path": s.path} for s in sources]
    for source in sources:
        _observe_source(result, root, source)
    return result


def _census(
    result: Observations,
    root: Path,
    parsed: dict[str, tuple[ast.Module, str]],
    python_files: list[Path],
    detected: Any,
    entries: list[tuple[str, str]],
) -> None:
    """What can change an agent outside the constructions its reader reads.

    A copy of an agent that passes its own tools, or a change to an agent's
    tools, can live in a module no reader reads as an SDK source; and an
    agent subclass defined in one module can be instantiated in another
    (#876 review).
    """

    censuses: dict[str, Any] = {}
    #: Google ADK agent subclasses each module defines, by name, with their line.
    adk_subclasses: dict[str, dict[str, int]] = {}
    # The SDK reader reads its own sources' copies and changes.
    read_as_sdk = {
        path
        for framework in detected.frameworks
        if framework.type == "openai_agents_sdk"
        for path in framework.candidate_files
    }
    read_as_adk = {
        path
        for framework in detected.frameworks
        if framework.type == "google_adk"
        for path in framework.candidate_files
    }
    # Top-level names a module in the scope can be imported by.
    local_modules = frozenset(
        {
            PurePosixPath(file.relative_to(root).as_posix()).parts[0].removesuffix(".py")
            for file in python_files
        }
        # The scope, and every package above it, may be what its modules
        # import through (``from svc.app.agents_def import …``).
        | {root.name}
        | set(PurePosixPath(result.scope).parts)
    )
    for relative, (tree, text) in parsed.items():
        censuses[relative] = census_module(
            tree,
            text,
            local_modules,
            read_as_sdk=relative in read_as_sdk,
            read_as_adk=relative in read_as_adk,
        )
        if "google.adk" in text:
            found = adk_agent_subclasses(tree)
            if found:
                adk_subclasses[relative] = found
    sdk_sources = {path for kind, path in entries if kind == "openai_agents_sdk"}
    for path, census in sorted(censuses.items()):
        if path in sdk_sources:
            # The SDK reader reads these itself, against the agents it saw.
            continue
        if census.copies:
            result.gap(
                f"An agent copy at {path}:{census.copies[0]} passes its own tools, handoffs "
                "or MCP servers in a module that does not import the OpenAI Agents SDK; it "
                "is not read.",
                source=path,
            )
        changes = [
            line for line, built in census.changes if not _plain_scope_class(built, censuses)
        ]
        if changes:
            result.gap(
                f"An object's tools, handoffs, MCP servers or sub-agents are changed at "
                f"{path}:{changes[0]}, in a module the comparison does not read for "
                "that; agents it reaches are not established.",
                source=path,
            )
    # A subclass is read where it is used. The defining module's reader names
    # an instance it builds there; every other module that uses the class is
    # named here. A subclass nothing uses is no agent (#876 review).
    defined = (
        ("an OpenAI Agents SDK", {path: census.subclasses for path, census in censuses.items()}),
        ("a Google ADK", adk_subclasses),
    )
    for framework, table in defined:
        for defining, classes in sorted(table.items()):
            for name, line in sorted(classes.items()):
                for path in _subclass_users(name, defining, censuses):
                    result.gap(
                        f"{path} uses {name}, {framework} agent subclass defined at "
                        f"{defining}:{line}; agents built from it are not read.",
                        source=path,
                    )


def _subclass_users(name: str, defining: str, censuses: dict[str, Any]) -> list[str]:
    """Every other module whose code uses ``name`` from ``defining``.

    A package that re-exports the class (``from app.core import *``) provides
    it too, transitively.
    """

    providers = {defining}
    grown = True
    while grown:
        grown = False
        for path, other in censuses.items():
            if path not in providers and any(
                other.reexports(name, provider) for provider in providers
            ):
                providers.add(path)
                grown = True
    return [
        path
        for path, other in sorted(censuses.items())
        if path != defining and any(other.uses(name, provider) for provider in providers)
    ]


def _plain_scope_class(built: tuple[str, str] | None, censuses: dict[str, Any]) -> bool:
    """Whether a receiver was built from a class the scope defines that is no agent.

    ``p = Payload(); p.tools = specs`` with ``Payload`` a model in
    ``app/schemas.py`` changes a request payload, not an agent (#876 review).
    """
    if built is None:
        return False
    tail, name = built
    defining = [
        census
        for path, census in censuses.items()
        if PurePosixPath(path).stem == tail
        or (PurePosixPath(path).name == "__init__.py" and PurePosixPath(path).parent.name == tail)
    ]
    return bool(defining) and all(
        name in census.classes and name not in census.subclasses for census in defining
    )


def _resolved(path: Path) -> Path | None:
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _observe_links(
    result: Observations, tree: Path, root: Path, python_files: list[Path]
) -> set[str]:
    """Census every link under the scope, and gap the ones that hide source.

    Discovery's inventory cannot be the census: it drops a path that does not
    resolve, or resolves out of the scope, so a dangling `agent.py` link read as
    a removed agent. Nothing here is read through a link. Returns the linked
    `*.py` paths, which are never reader inputs.

    - A `*.py` link whose target is a Python input this scope already reads is
      compared at that target's own path; reading the alias too made one agent
      two ambiguous ones. Any other `*.py` link is a gap over its path.
    - A link to a directory outside the scope that holds Python is a gap: the
      scope's reader never walks it.
    - A link that resolves to nothing in the tree is a gap only where the other
      side reads source at or beneath it (`_reconcile_unresolved_links`), so
      `agent/VERSION -> ../../VERSION` changes nothing.
    - Any other link (`CLAUDE.md -> AGENTS.md`, a directory read at its own
      path) is outside Python discovery, as in a checkout.
    """

    read_directly = {p.resolve() for p in python_files if not p.is_symlink()}
    linked_python: set[str] = set()
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not _skip_part(name)]
        for name in sorted(dirnames + filenames):
            path = Path(directory) / name
            if not path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            target = _resolved(path)
            if name.endswith(".py"):
                linked_python.add(relative)
                if target not in read_directly:
                    result.gap(f"Linked Python input: {relative}", source=relative)
            elif target is None or not target.is_relative_to(tree):
                result.unresolved_links.append(relative)
            elif target.is_dir() and not _read_by_scope(root, target) and any(
                candidate.suffix == ".py" for candidate in target.rglob("*")
            ):
                result.gap(
                    f"Linked directory holds Python outside the scope: {relative}",
                    source=relative,
                )
    return linked_python


def _read_by_scope(root: Path, target: Path) -> bool:
    return target.is_relative_to(root) and not any(
        _skip_part(part) for part in target.relative_to(root).parts
    )


def _observe_source(result: Observations, root: Path, source: ToolSourceConfig) -> None:
    """Each reader's unlocated gaps cover its input file, not other sources.

    A reader's agent-level observations narrow that further. Cross-module
    binding resolution remains the reader's responsibility, never a name join.
    """
    bag = ArtifactBag()
    # Tools bound as objects are read for this comparison only (#910).
    with reading_object_bindings(_is_test_path):
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
            # A binding the reader made on a guess is reported, never as an
            # established row: each tool the name may really be carries the
            # reason on the side it is present, and the whole agent does when
            # one of them cannot be named (#879 review).
            for tool_name, message in observation.tool_issues.items():
                result.gap(
                    message,
                    source=_source_path(root, observation.source),
                    agent=observation.agent,
                    tool=None if tool_name == ANY_TOOL else tool_name,
                )
                attributed.add(message)
        for warning in item.warnings:
            if warning not in attributed:
                result.gap(warning, source=source.path)
                attributed.add(warning)
        for omission in item.omissions:
            result.gap(f"Omitted source surface: {omission}", source=source.path)
    if artifacts is not None:
        # A tool reference the reader could not follow to a definition
        # carries its agent and named reason beside the warning (#864): scope
        # the gap to that agent and say why, rather than covering the file.
        # One warning can stand for several agents: a module-level wrapper
        # two agents share has one sentence. Every record gets its own gap, so
        # no agent's uncertainty is carried by another's (#879 review).
        unresolved: dict[str, list[dict[str, Any]]] = {}
        for record in artifacts.unresolved_references:
            if isinstance(record.get("warning"), str):
                unresolved.setdefault(record["warning"], []).append(record)
        for warning in artifacts.warnings:
            if warning not in attributed:
                records = unresolved.get(warning)
                if not records:
                    result.gap(warning, source=source.path)
                for record in records or []:
                    detail = record["detail"]
                    result.gap(
                        warning
                        if detail in warning
                        else f"{warning} Not resolved because {detail}.",
                        source=source.path,
                        agent=record["agent_name"],
                    )
                attributed.add(warning)
    result.handoff_only |= handoff_targets - constructed
    # One identity a reader observed at more than one construction site. The
    # reader already names it as incomplete; a tool both sites bind the same
    # way is one binding of it, not an ambiguous one (#876 review).
    sites: dict[tuple[str, str], int] = {}
    # Where each tool is listed when one ADK name is constructed more than
    # once: the row points at a construction that lists it (#872).
    tool_sites: dict[tuple[str, str], dict[str, list[str]]] = {}
    # What a tool or handoff is bound under when only under a condition
    # (#909); ``None`` once any construction binds it unconditionally.
    bound_when: dict[tuple[str, str], dict[str, list[str] | None]] = {}
    for item in loaded:
        for observation in item.binding_observations:
            site = (_source_path(root, observation.source), observation.agent)
            sites[site] = sites.get(site, 0) + 1
            construction_locations = tool_sites.setdefault(site, {})
            for name, locations in observation.tool_sites.items():
                construction_locations[name] = list(dict.fromkeys((*construction_locations.get(name, []), *locations)))
            for name, locations in observation.handoff_sites.items():
                label = f"handoff:{name}"
                construction_locations[label] = list(dict.fromkeys((*construction_locations.get(label, []), *locations)))
            held = bound_when.setdefault(site, {})
            for name in (*observation.tool_names, *observation.object_bindings):
                _hold(held, name, observation.tool_conditions.get(name))
            for name in observation.handoff_names:
                _hold(held, f"handoff:{name}", observation.handoff_conditions.get(name))
    # A Google ADK agent's sub-agents are its records', not its observation's.
    for record in artifacts.sub_agents if artifacts is not None else []:
        if "sub_agent_count" not in record or not isinstance(record.get("agent_name"), str):
            continue
        held = bound_when.setdefault(
            (_source_path(root, str(record.get("source_ref") or "")), record["agent_name"]), {}
        )
        conditions = record.get("conditions") or {}
        for name in record.get("sub_agents") or []:
            _hold(held, f"handoff:{name}", conditions.get(name))
    tools, warnings = _canonical_tools(result, source, loaded)
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
    for record in artifacts.sub_agents if artifacts is not None else []:
        if "unread" in record and isinstance(record.get("agent_name"), str):
            # A sub-agent list read only in part: a limit on its agent, at its
            # construction, not on every agent of the file (#909 review).
            message = adk_unnamed_sub_agents(record["agent_name"], record)
            result.gap(
                message,
                source=_source_path(root, str(record.get("source_ref") or "")),
                agent=record["agent_name"],
            )
            attributed.add(message)
    if artifacts is not None:
        # A Google ADK list read only in part is a limit its agent already
        # carries; the toolset record beside it names no agent, and would
        # otherwise cover every agent in the file (#909 review).
        incomplete = {
            (_source_path(root, observation.source), observation.agent)
            for item in loaded
            for observation in item.binding_observations
            if not observation.tools_complete
        }
        dynamic = [toolset for toolset in artifacts.toolsets if toolset.dynamic or not toolset.resolved]
        if dynamic and all(
            toolset.kind == "dynamic"
            and toolset.agent_name
            and (_source_path(root, toolset.source_ref or ""), toolset.agent_name) in incomplete
            for toolset in dynamic
        ):
            attributed.update(
                f"Google ADK toolset {toolset.name or toolset.kind!r} is not statically enumerable."
                for toolset in dynamic
            )
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
        definition = _definition(root, tool, key[1])
        if definition.get("unestablished"):
            result.gap(
                definition["unestablished"],
                source=key[0],
                agent=key[1],
                tool=tool.name,
                affects="implementation",
            )
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
        binding = {
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
            **_import_path(tool, key[0]),
        }
        condition = bound_when.get(key, {}).get(tool.name)
        if condition:
            binding["bound_when"] = condition
        listed = tool_sites.get(key, {}).get(tool.name)
        if listed:
            binding["binding_location"] = listed[0]
            if len(listed) > 1:
                binding["construction_sites"] = listed
        if binding_key not in result.bindings:
            reach = _reach(root, tool)
            if reach:
                binding["reach"] = reach
            binding["effect_evidence"] = _effect_evidence(tool, reach)
        if binding_key in result.bindings:
            if sites.get(key, 0) > 1 and _meaning(result.bindings[binding_key]) == _meaning(
                binding
            ):
                # Both constructions of one merged identity bind this callable
                # identically: a true binding of that identity, kept beside the
                # gap that already names its constructions.
                continue
            result.gap(
                f"Ambiguous tool identity: {binding_key}",
                source=key[0],
                agent=key[1],
                tool=tool.name,
            )
            result.bindings.pop(binding_key)
            ambiguous_bindings.add(binding_key)
            continue
        result.bindings[binding_key] = binding
    for item in loaded:
        for observation in item.binding_observations:
            key = (_source_path(root, observation.source), observation.agent)
            if key in ambiguous_agents:
                continue
            for name, payload in observation.object_bindings.items():
                binding_key = (*key, name)
                if binding_key in ambiguous_bindings:
                    continue
                binding = _object_binding(key, name, payload, observation.source_pointer)
                if payload.get("unread"):
                    result.gap(
                        binding["definition"]["unestablished"],
                        source=key[0],
                        agent=key[1],
                        tool=name,
                        affects="implementation",
                    )
                condition = bound_when.get(key, {}).get(name)
                if condition:
                    binding["bound_when"] = condition
                listed = tool_sites.get(key, {}).get(name)
                if listed:
                    binding["binding_location"] = listed[0]
                    if len(listed) > 1:
                        binding["construction_sites"] = listed
                if binding_key in result.bindings:
                    if sites.get(key, 0) > 1 and _meaning(result.bindings[binding_key]) == _meaning(binding):
                        continue
                    result.gap(
                        f"Ambiguous tool identity: {binding_key}",
                        source=key[0],
                        agent=key[1],
                        tool=name,
                    )
                    result.bindings.pop(binding_key)
                    ambiguous_bindings.add(binding_key)
                    continue
                result.bindings[binding_key] = binding
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
        condition = bound_when.get(source, {}).get(f"handoff:{target[1]}")
        if condition:
            result.bindings[key]["bound_when"] = condition
        listed = tool_sites.get(source, {}).get(f"handoff:{target[1]}")
        if listed:
            result.bindings[key]["binding_location"] = listed[0]
            if len(listed) > 1:
                result.bindings[key]["construction_sites"] = listed


def _object_binding(
    key: tuple[str, str], name: str, payload: dict[str, Any], pointer: str | None
) -> dict[str, Any]:
    """One tool bound as an object (#910): its identity is the compared meaning.

    ``object`` holds what the binding is — an MCP server's transport, host or
    program, credential names and filter; the agent an agent tool wraps; a
    hosted or built-in tool's name — and never a URL's path or query, a header
    value or a command's arguments, which only a digest stands for. Where it
    is built is evidence, as a function's location is.
    """

    location = str(payload.get("location") or "")
    path, _, line = location.rpartition(":")
    definition: dict[str, Any] = {
        "source": path or location,
        "line": int(line) if line.isdecimal() else None,
        "implementation_sha256": _digest(payload["identity"]),
    }
    unread = payload.get("unread") or []
    if unread:
        definition["unestablished"] = (
            f"What {name} is bound to is read only in part: {'; '.join(unread)}. Whether it "
            "changed is not established."
        )
    binding: dict[str, Any] = {
        "agent": key[1],
        "agent_source": key[0],
        "tool": name,
        "binding_location": pointer,
        "edge_type": "object_tool",
        "definition": definition,
        "object": payload["identity"],
        "evidence_basis": "ast_extraction",
    }
    if payload.get("evidence"):
        binding["object_evidence"] = payload["evidence"]
    return binding


def _hold(held: dict[str, list[str] | None], name: str, alternatives: list[str] | None) -> None:
    """Record one construction's condition for ``name``: unconditional wins."""

    if name in held and held[name] is None:
        return
    held[name] = None if alternatives is None else sorted(set(held.get(name) or []) | set(alternatives))


def _noun(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    """A tool bound as an object is not a callable the source defines (#910)."""

    return "tool object" if (after or before or {}).get("object") else "callable"


def _bound(binding: dict[str, Any]) -> str:
    alternatives = binding.get("bound_when")
    return "only when " + " or ".join(alternatives) if alternatives else "unconditionally"


def _canonical_tools(
    result: Observations, source: ToolSourceConfig, loaded: list[Any]
) -> tuple[list[Any], list[str]]:
    """Build one source's catalog; a tool name defined twice is a limit on that name.

    The catalog refuses a duplicate definition, which is right for a reviewed
    manifest and wrong here: one file defining `_tool` twice refused every
    other agent's comparison (#876). The name is dropped from this source's
    tools and binding observations, so no agent binds either definition, and
    it is a gap over every binding of that name in this file; the agents'
    other tools are still compared. Ported from PR #880.
    """

    while True:
        try:
            return _build_canonical_tools(loaded)
        except InputParseError as exc:
            if exc.details.get("failure") != DUPLICATE_TOOL_IN_SOURCE:
                raise
            name = exc.details.get("tool_name")
            before = sum(len(item.tools) for item in loaded)
            for item in loaded:
                item.tools = [tool for tool in item.tools if tool.name != name]
                for observation in item.binding_observations:
                    observation.tool_names = [n for n in observation.tool_names if n != name]
                    observation.tool_locators.pop(name, None)
                    observation.tool_issues.pop(name, None)
            if not isinstance(name, str) or sum(len(item.tools) for item in loaded) == before:
                raise
            result.gap(
                f"{source.path} defines the tool {name!r} more than once; which "
                "definition an agent binds is not established.",
                source=source.path,
                tool=name,
            )


def _reconcile_submodules(base: Observations, head: Observations) -> None:
    """Name each submodule the comparison did not read, by what it can hide.

    Discovery never reads into a submodule; a checkout without
    ``--recurse-submodules`` leaves an empty directory, and so does the
    materializer. The same gitlink commit on both sides is the same content, so
    it cannot carry a change: it is named as a limit and nothing more. Any
    other gitlink is a gap over its own path on each side that has it, and over
    the whole scope when it is the selected scope itself.
    """

    for path in sorted(base.submodules.keys() | head.submodules.keys()):
        before, after = base.submodules.get(path), head.submodules.get(path)
        where = "the selected scope" if path == "." else path
        if before == after:
            message = f"Submodule content is not read (unchanged commit {before[:12]}): {where}"
            base.limits.append(message)
            head.limits.append(message)
            continue
        for side, commit in ((base, before), (head, after)):
            if commit is not None:
                side.gap(
                    f"Submodule content is not read (commit {commit[:12]}): {where}",
                    source=None if path == "." else path,
                )


def _reconcile_unresolved_links(base: Observations, head: Observations) -> None:
    """Gap a link that resolves to nothing where the other side reads source.

    Such a link hides nothing on its own: its content is not in the
    repository on either side. It hides a change only when it replaced source
    the other side reads, at its path or beneath it, which would otherwise be
    reported as a definite removal or addition.
    """

    for side, other in ((base, head), (head, base)):
        read = {path for path, _name in other.agents} | {s["path"] for s in other.sources}
        for link in side.unresolved_links:
            if any(path == link or path.startswith(link + "/") for path in read):
                side.gap(f"Linked input resolves outside the tree: {link}", source=link)


def _import_path(tool: Any, agent_source: str) -> dict[str, Any]:
    """How the agent's module reached a definition in another module (#864).

    Each step names the module read, the line of the binding followed and that
    module's digest. Evidence, not meaning: moving an import is not a change.
    """
    raw = tool.extraction.get("import_resolutions")
    if not isinstance(raw, list):
        return {}
    paths = [
        item
        for item in raw
        if isinstance(item, dict)
        and item.get("steps")
        and item["steps"][0].get("path") == agent_source
    ]
    return {"import_path": paths} if paths else {}


def _meaning(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in binding.items()
        if k
        not in {
            "binding_location",
            "definition",
            "evidence_basis",
            "agent_source",
            "import_path",
            "construction_sites",
            "reach",
            "effect_evidence",
            "object_evidence",
        }
    } | {"implementation_sha256": binding.get("definition", {}).get("implementation_sha256")}


def compare(
    base: Observations, head: Observations, *, target_moves: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    rows = []
    for key in sorted(base.bindings.keys() | head.bindings.keys()):
        before, after = base.bindings.get(key), head.bindings.get(key)
        uncertainty = {}
        kind = "added" if before is None else "removed" if after is None else "changed"
        only_condition = False
        if before is None:
            reasons = base.absence_gaps(key, target_moves)
            if reasons:
                uncertainty["base"] = reasons
            present = head.tool_gaps(key)
            if present:
                uncertainty["head"] = present
        elif after is None:
            reasons = head.absence_gaps(key)
            if reasons:
                uncertainty["head"] = reasons
            present = base.tool_gaps(key)
            if present:
                uncertainty["base"] = present
        else:
            before_meaning = _meaning(before)
            if "target_source" in before_meaning:
                path = before_meaning["target_source"]
                before_meaning["target_source"] = (target_moves or {}).get(path, path)
            for side, value in (("base", before), ("head", after)):
                if "definition" in value and value["definition"]["implementation_sha256"] is None:
                    uncertainty[side] = ["The bound callable's implementation could not be read."]
            # Only the condition it is bound under changed (#909): a change,
            # stated as one, whose direction is not established.
            only_condition = before_meaning != _meaning(after) and (
                {**before_meaning, "bound_when": None} == {**_meaning(after), "bound_when": None}
            )
            if only_condition:
                # A part of the list that was not read may hold the tool some
                # other way, so the condition is not established either.
                for side, observed, moves in (("base", base, target_moves), ("head", head, None)):
                    reasons = observed.absence_gaps(key, moves)
                    if reasons:
                        uncertainty.setdefault(side, []).extend(reasons)
            if before_meaning != _meaning(after):
                # A factory value this read cannot name moved with the code
                # around it: a candidate change, not an established one.
                for side, value in (("base", before), ("head", after)):
                    reason = value.get("definition", {}).get("unestablished")
                    if reason:
                        uncertainty.setdefault(side, []).append(reason)
            for side, observed in (("base", base), ("head", head)):
                reasons = observed.tool_gaps(key)
                if reasons:
                    uncertainty.setdefault(side, []).extend(reasons)
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
                "why": (
                    f"Only the condition changed: bound {_bound(before)} at the base and "
                    f"{_bound(after)} at the head. Conditions are read as source text, never "
                    "evaluated, so whether the agent holds it more or less often is not established."
                )
                if only_condition and not uncertainty
                else {
                    "added": f"The source now binds this {_noun(before, after)} to this agent"
                    + (f", {_bound(after)}." if after and after.get("bound_when") else "."),
                    "removed": f"The selected source path no longer binds this {_noun(before, after)} to this agent"
                    + (f" (it was bound {_bound(before)})" if before and before.get("bound_when") else "")
                    + "; check scope limits for relocation.",
                    "not_established": "This candidate change cannot be established from the affected inputs; it is not a no-change result.",
                    "changed": "What the bound tool object is changed (its `object`); authority direction is not established."
                    if (before or {}).get("object") and (after or {}).get("object")
                    else "The bound callable's interface or implementation changed; authority direction is not established.",
                }[kind],
                "review_question": (
                    f"Resolve the named uncertainty before treating {key[1]}.{key[2]} as {candidate_change}."
                    if uncertainty
                    else f"Should {key[1]} hold {key[2]} {_bound(after)} rather than {_bound(before)}?"
                    if only_condition
                    else f"Should {key[1]} have this {kind} binding to {key[2]}? Review what the tool object is and where it is built."
                    if _noun(before, after) == "tool object"
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
    if "construction_sites" in result:
        result["construction_sites"] = [
            _location(scope, site) for site in result["construction_sites"]
        ]
    if "target_source" in result:
        result["target_source"] = _location(scope, result["target_source"])
    if "object_evidence" in result and result["object_evidence"].get("agent_source"):
        result["object_evidence"] = {
            **result["object_evidence"],
            "agent_source": _location(scope, result["object_evidence"]["agent_source"]),
        }
    if "definition" in result:
        # Why the implementation is unknown is published as the row's
        # uncertainty, not as a field of the definition.
        result["definition"] = {key: value for key, value in result["definition"].items() if key != "unestablished"}
        result["definition"]["source"] = _location(scope, result["definition"]["source"])
    if "import_path" in result:
        result["import_path"] = [
            {
                **item,
                "steps": [
                    {**step, "path": _location(scope, step["path"])} for step in item["steps"]
                ],
                "inputs": [
                    {**entry, "path": _location(scope, entry["path"])}
                    for entry in item.get("inputs", [])
                ],
                **(
                    {"definition": _location(scope, item["definition"])}
                    if "definition" in item
                    else {}
                ),
            }
            for item in result["import_path"]
        ]
    if "reach" in result:
        reach = result["reach"]
        result["reach"] = {
            **reach,
            "calls": [
                {
                    **call,
                    "at": _location(scope, call["at"]),
                    "via": [_location(scope, hop) for hop in call["via"]],
                }
                for call in reach["calls"]
            ],
            **(
                {
                    "effects": [
                        {
                            **effect,
                            "at": _location(scope, effect["at"]),
                            "via": [_location(scope, hop) for hop in effect["via"]],
                        }
                        for effect in reach["effects"]
                    ]
                }
                if "effects" in reach
                else {}
            ),
            "limits": [{**item, "at": _location(scope, item["at"])} for item in reach["limits"]],
            "effect_claims": [
                {**item, "at": _location(scope, item["at"])} for item in reach["effect_claims"]
            ],
        }
    if "effect_evidence" in result:
        result["effect_evidence"] = {
            **result["effect_evidence"],
            "claims": [
                {**claim, "at": _location(scope, claim["at"])}
                if claim["source"] in REACH_CLAIM_SOURCES
                else claim
                for claim in result["effect_evidence"]["claims"]
            ],
        }
    return result


def _object_lines(binding: dict[str, Any]) -> list[str]:
    """What a tool bound as an object is, one fact per line (#910)."""

    identity = binding["object"]
    definition = binding.get("definition") or {}
    lines = [
        object_display({"identity": identity})
        + (f" at {definition['source']}:{definition['line']}" if definition.get("line") else "")
    ]
    if identity.get("kind") == "agent_tool":
        source = (binding.get("object_evidence") or {}).get("agent_source")
        lines[0] += f" (agent in {source})" if source else ""
    for item in identity.get("credential_sources") or []:
        target = next(
            f"{kind} {item[kind]}" if item[kind] else kind
            for kind in ("header", "auth", "query", "field", "userinfo", "server_env")
            if kind in item
        )
        sources = [f"env {name}" for name in item.get("env", [])]
        if item.get("from"):
            sources.append("a value made from " + ", ".join(item["from"]))
        if item.get("literal"):
            sources.append("a literal (not printed)")
        lines.append(f"  credential: {', '.join(sources) or 'a computed value'} → {target}")
    return lines


#: Libraries whose effect is named by a function, not a method on an object.
_FUNCTION_LIBRARIES = frozenset({"builtins", "subprocess", "os", "shutil", "asyncio", "io"})


def _effect_phrase(effect: dict[str, Any]) -> str:
    """One effect beyond HTTP as a reviewer reads it (#913)."""

    words = [effect["family"], effect["operation"]]
    if effect["family"] in {"cloud", "messaging"} and effect.get("service"):
        words.append(effect["service"])
    if effect.get("statement"):
        words.append(effect["statement"] + (" on" if effect.get("target") else ""))
    if effect.get("target"):
        words.append(effect["target"])
    if effect.get("shell"):
        words.append("through a shell")
    library = effect["library"]
    call = effect["call"] if library in _FUNCTION_LIBRARIES else f"{library} {effect['call']}"
    if effect["family"] == "database" and effect.get("service"):
        call = f"{effect['service']}, {call}"
    return " ".join(words) + f" ({call})"


def _effect_lines(effects: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for effect in effects:
        groups.setdefault(_effect_phrase(effect), []).append(effect)
    for phrase, group in groups.items():
        first = group[0]
        via = f" via {' → '.join(first['via'])}" if first["via"] else ""
        more = (
            f", and {len(group) - 1} more call site{'s' if len(group) > 2 else ''}"
            if len(group) > 1
            else ""
        )
        lines.append(f"reaches: {phrase} at {first['at']}{via}{more}")
        hosts = sorted({host for effect in group for host in effect.get("host", [])})
        if hosts:
            lines.append(f"  host: {', '.join(hosts)}")
        supplied: list[str] = []
        for effect in group:
            for item in effect.get("model_supplied", []):
                fact = f"{item['param']} → {item['into']}"
                if fact not in supplied:
                    supplied.append(fact)
        if supplied:
            lines.append("  model-supplied: " + "; ".join(supplied))
        credentials: list[str] = []
        for effect in group:
            for item in effect.get("credential_sources", []):
                target = next(
                    (f"{kind} {item[kind]}" if item[kind] else kind for kind in ("keyword", "userinfo") if kind in item),
                    "a credential",
                )
                sources = [f"env {name}" for name in item.get("env", [])]
                if item.get("from"):
                    sources.append("a value made from model-supplied " + ", ".join(item["from"]))
                if item.get("literal"):
                    sources.append("a literal (not printed)")
                fact = f"  credential: {', '.join(sources) or 'a computed value'} → {target}"
                if fact not in credentials:
                    credentials.append(fact)
        lines.extend(credentials)
    return lines


def _reach_lines(binding: dict[str, Any]) -> list[str]:
    """A side's outbound calls and effect evidence, one fact per line (#872)."""
    from agents_shipgate.report.human_order import EFFECT_EVIDENCE_LABELS

    reach = binding.get("reach") or {}
    calls = reach.get("calls") or []
    limits = reach.get("limits") or []
    lines: list[str] = []
    groups: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
    for call in calls:
        key = (call["method"] or "an unread method", call["url"], call.get("graphql"))
        groups.setdefault(key, []).append(call)
    for (method, url, graphql), group in groups.items():
        first = group[0]
        operation = f" (GraphQL {graphql})" if graphql else ""
        via = f" via {' → '.join(first['via'])}" if first["via"] else ""
        more = (
            f", and {len(group) - 1} more call site{'s' if len(group) > 2 else ''}"
            if len(group) > 1
            else ""
        )
        lines.append(f"reaches: {method} {url}{operation} at {first['at']}{via}{more}")
        facts: list[str] = []
        for call in group:
            for item in call.get("fields", []):
                name = item.get("field")
                if name is None:
                    continue
                if "values" in item:
                    fact = f"  field {name} ∈ {{{', '.join(item['values'])}}}" + (
                        f", decided by {', '.join(item['decided_by'])}"
                        if item.get("decided_by")
                        else ""
                    )
                elif "value" in item:
                    fact = f"  field {name} = {json.dumps(item['value'])}"
                else:
                    continue
                if fact not in facts:
                    facts.append(fact)
        supplied: list[str] = []
        for call in group:
            for item in call.get("model_supplied", []):
                fact = f"{item['param']} → {item['into']}"
                if fact not in supplied:
                    supplied.append(fact)
        if supplied:
            facts.append("  model-supplied: " + "; ".join(supplied))
        credentials: dict[str, int] = {}
        for call in group:
            for item in call.get("credential_sources", []):
                target = next(
                    f"{kind} {item[kind]}" if item[kind] else kind
                    for kind in ("header", "auth", "query", "field", "userinfo")
                    if kind in item
                )
                sources = [f"env {name}" for name in item.get("env", [])]
                if item.get("from"):
                    sources.append(
                        "a value made from model-supplied " + ", ".join(item["from"])
                    )
                if item.get("literal"):
                    sources.append("a literal (not printed)")
                source = ", ".join(sources) or "a computed value"
                fact = f"  credential: {source} → {target}"
                credentials[fact] = credentials.get(fact, 0) + 1
        for fact, count in credentials.items():
            # A credential only some call sites send (a 401 retry without it)
            # is not said of all of them.
            facts.append(fact if count >= len(group) else f"{fact} (at some call sites)")
        lines.extend(facts)
    effects = reach.get("effects") or []
    lines.extend(_effect_lines(effects))
    evidence = binding.get("effect_evidence")
    if evidence and (calls or effects or limits):
        status = evidence["status"]
        label = EFFECT_EVIDENCE_LABELS.get(status, status)
        basis = ""
        claims = [c for c in evidence["claims"] if c["source"] in REACH_CLAIM_SOURCES]
        if status == "structural" and claims:
            claim = next(
                (c for c in claims if c["value"] == evidence["conservative_effect"]), claims[0]
            )
            effect = next((item for item in effects if item["at"] == claim["at"]), None)
            if claim["value"] == "read":
                basis = (
                    ": every call was followed, and everything it reaches reads"
                    if effects
                    else ": every call was followed, and every outbound call reads"
                )
            elif claim["source"] != REACH_CLAIM_SOURCE and effect is not None:
                basis = f": {effect['family']} {effect['operation']} at {claim['at']}"
            else:
                basis = f": outbound call at {claim['at']}"
        lines.append(f"effect: {evidence['conservative_effect']} ({label}{basis})")
    for item in limits[:3]:
        lines.append(f"reach limit: {item['at']} {item['why']}")
    hidden = len(limits) - 3 + reach.get("more_limits", 0)
    if hidden > 0:
        lines.append(f"reach limit: and {hidden} more")
    return lines


def run_application_diff(
    *,
    workspace: Path,
    base: str | None,
    head: str,
    scope: str | None,
    base_scope: str | None,
    max_python_files: int,
    json_output: bool,
) -> int:
    from agents_shipgate.cli.diff import _refuse_objects_missing, _resolve_base
    from agents_shipgate.cli.verify.git import promised_objects_missing

    workspace = ensure_git_workspace(workspace)
    if scope is None and base_scope is not None:
        raise typer.BadParameter(
            "--base-scope names the old path of an explicitly selected application; pass --scope too."
        )
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
    for side, ref, commit in (("base", base_ref, base_commit), ("head", head, head_commit)):
        if promised_objects_missing(workspace, commit):
            # A partial clone: name the hydration it needs before anything
            # reads the tree, the derived scope included (#817, #875).
            _refuse_objects_missing(workspace, ref, commit, side=side)
    engine = build_engine_requirement(plugins_enabled=False).model_dump(mode="json")
    sides = {
        "base": {
            "requested_ref": base_ref,
            "requested_commit": requested_base_commit,
            "compared_commit": base_commit,
            "tree": tree_sha(workspace, base_commit),
        },
        "head": {
            "requested_ref": head,
            "compared_commit": head_commit,
            "tree": tree_sha(workspace, head_commit),
        },
    }
    if scope is None:
        # No --scope: the change chooses it (#875).
        selection = derive_scopes(workspace, base_commit, head_commit)
        pairs = list(selection.pairs)
        scope_selection = selection.payload()
    else:
        scope, old_scope = _scope(scope), _scope(base_scope if base_scope is not None else scope)
        pairs = [(old_scope, scope)]
        scope_selection = {
            "mode": "explicit",
            "scopes": [scope],
            "base_scope": old_scope,
            "reason": "Selected with --scope.",
        }
    comparisons = [
        _compare_scopes(
            workspace,
            sides,
            (base_ref, base_commit, old_scope),
            (head, head_commit, new_scope),
            max_python_files=max_python_files,
            engine=engine,
            derived=scope is None,
        )
        for old_scope, new_scope in pairs
    ]
    if len(comparisons) == 1:
        payload = {**comparisons[0], "scope_selection": scope_selection}
        if scope_selection.get("limits") and payload["comparison_status"] == "compared":
            # The derivation stopped at a bound: an agent it did not reach
            # may be related too (#875).
            payload["comparison_status"] = "partial"
    else:
        payload = _combined(comparisons, sides, scope_selection, engine, max_python_files)
    payload = sanitize_report_payload(payload)
    payload["comparison_id"] = _digest(payload)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_comparison(payload, base_commit, head_commit)
    return 0


_LIMITS = [
    "Covers supported OpenAI Agents SDK and Google ADK source wiring only.",
    "Deployment-root reachability, runtime behavior, effects other than the outbound HTTP calls and library effects a row names, and business authority are not established.",
    "This comparison is advisory evidence and supplies no release verdict or merge permission.",
]


def _compare_scopes(
    workspace: Path,
    sides: dict[str, dict[str, Any]],
    base: tuple[str, str, str],
    head: tuple[str, str, str],
    *,
    max_python_files: int,
    engine: dict[str, Any],
    derived: bool,
) -> dict[str, Any]:
    """One comparison of ``base`` and ``head`` (``(ref, commit, scope)``)."""

    from agents_shipgate.cli.diff import _refuse_objects_missing

    (base_ref, base_commit, old_scope), (head_ref, head_commit, scope) = base, head
    with tempfile.TemporaryDirectory(prefix="shipgate-application-diff-") as raw:
        scratch = Path(raw)
        gitlinks: dict[str, dict[str, str]] = {}
        for side, ref, commit, selected_scope in (
            ("base", base_ref, base_commit, old_scope),
            ("head", head_ref, head_commit, scope),
        ):

            def in_scope(path: str, selected: str = selected_scope) -> bool:
                return (
                    selected == "." or path == selected or path.startswith(selected + "/")
                )

            # Always scoped, the root included: the scoped materializer
            # recreates links rather than refusing them, so an unrelated
            # `CLAUDE.md -> AGENTS.md` meets the reader as the link it is, and
            # the reader decides what it would follow. It also packs the tree
            # instead of walking history.
            try:
                gitlinks[side] = archive_fetched_tree(
                    workspace, commit, scratch / side, scope=in_scope, record_gitlinks=True
                )
            except PromisedObjectsMissingError:
                _refuse_objects_missing(workspace, ref, commit, side=side)
        # Each side's imports are read against its own commit's tree: the
        # materialized scope alone cannot say whether ``from common.patches
        # import ...`` is the application's code or an installed package.
        old_layout = _git_layout(workspace, base_commit, old_scope)
        new_layout = _git_layout(workspace, head_commit, scope)
        with repository_layout(old_layout):
            old_found = discover(
                scratch / "base",
                old_scope,
                max_python_files=max_python_files,
                gitlinks=gitlinks["base"],
            )
        with repository_layout(new_layout):
            new_found = discover(
                scratch / "head", scope, max_python_files=max_python_files, gitlinks=gitlinks["head"]
            )
        # The census names what can change an agent its reader did not read.
        # With no agent source on either side there is no agent to qualify
        # (#876 review).
        census = bool(old_found.entries or new_found.entries)
        with repository_layout(old_layout):
            old = observe(old_found, max_python_files=max_python_files, census=census)
        with repository_layout(new_layout):
            new = observe(new_found, max_python_files=max_python_files, census=census)
        if old.status == new.status == "absent" and not derived:
            raise ConfigError(
                f"Neither comparison tree contains the selected scopes: "
                f"base={old_scope!r}, head={scope!r}. Check --scope/--base-scope."
            )
        _reconcile_submodules(old, new)
        _reconcile_unresolved_links(old, new)
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
    return {
        "application_comparison_schema_version": SCHEMA_VERSION,
        "comparison_status": status,
        "static_analysis_only": True,
        "comparison_basis": "source_observed_per_agent_wiring",
        "input_origin": "independent_tree_discovery",
        "engine": engine,
        "base": {**sides["base"], **old.summary()},
        "head": {**sides["head"], **new.summary()},
        "options": {"max_python_files": max_python_files, "max_python_bytes": MAX_PYTHON_BYTES},
        "source_correspondence": moves,
        "rows": rows,
        "limits": list(_LIMITS),
    }


def _combined(
    comparisons: list[dict[str, Any]],
    sides: dict[str, dict[str, Any]],
    scope_selection: dict[str, Any],
    engine: dict[str, Any],
    max_python_files: int,
) -> dict[str, Any]:
    """No scope, or several: one result that holds each comparison (#875).

    With none, the change touches no supported agent, and the answer says so;
    it is not a failure. With several, rows are every comparison's, each
    path spelled from the repository root."""

    statuses = {item["comparison_status"] for item in comparisons}
    if not comparisons or statuses == {"not_established"}:
        status = "not_established"
    elif statuses == {"compared"}:
        status = "compared"
    else:
        status = "partial"
    gaps = scope_selection.get("limits", [])
    if gaps:
        # A bound reached or an agent left outside: the answer is incomplete,
        # never "no agent" (#875).
        status = "partial"

    def side(name: str) -> dict[str, Any]:
        # The same fields one comparison's side has, every path spelled from
        # the repository root; each comparison keeps its own detail.
        def rooted(item: dict[str, Any], path: str | None) -> str | None:
            return _location(item[name]["scope"], path)

        return {
            **sides[name],
            "scope": None,
            "scopes": [item[name]["scope"] for item in comparisons],
            "status": "not_selected"
            if not comparisons
            else "partial"
            if any(item[name]["status"] == "partial" for item in comparisons)
            else "complete",
            "sources": [
                {**source, "path": rooted(item, source.get("path"))}
                for item in comparisons
                for source in item[name]["sources"]
            ],
            "agents": [
                {
                    **agent,
                    "source": rooted(item, agent.get("source")),
                    "location": rooted(item, agent.get("location")),
                }
                for item in comparisons
                for agent in item[name]["agents"]
            ],
            "binding_count": sum(item[name]["binding_count"] for item in comparisons),
            "limits": sorted(
                {*gaps, *(f"{item[name]['scope']}: {limit}" for item in comparisons for limit in item[name]["limits"])}
            ),
            "coverage_gaps": [
                {**gap, "source": rooted(item, gap.get("source"))}
                for item in comparisons
                for gap in item[name]["coverage_gaps"]
            ],
            "excluded_tests": sorted(
                str(rooted(item, path)) for item in comparisons for path in item[name].get("excluded_tests", [])
            ),
        }

    return {
        "application_comparison_schema_version": SCHEMA_VERSION,
        "comparison_status": status,
        "static_analysis_only": True,
        "comparison_basis": "source_observed_per_agent_wiring",
        "input_origin": "independent_tree_discovery",
        "engine": engine,
        "base": side("base"),
        "head": side("head"),
        "options": {"max_python_files": max_python_files, "max_python_bytes": MAX_PYTHON_BYTES},
        "source_correspondence": [
            {
                **move,
                "base_source": _location(item["base"]["scope"], move["base_source"]),
                "head_source": _location(item["head"]["scope"], move["head_source"]),
            }
            for item in comparisons
            for move in item["source_correspondence"]
        ],
        "rows": [
            {**row, "agent_source": _location(item["head"]["scope"], row.get("agent_source"))}
            for item in comparisons
            for row in item["rows"]
        ],
        "comparisons": comparisons,
        "scope_selection": scope_selection,
        "limits": list(_LIMITS),
    }


def _print_comparison(payload: dict[str, Any], base_commit: str, head_commit: str) -> None:
    from agents_shipgate.cli.diff import _one_line

    status = payload["comparison_status"]
    typer.echo(f"Application comparison: {status} ({base_commit[:12]} → {head_commit[:12]})")
    selection = payload.get("scope_selection") or {}
    if selection.get("mode") == "derived":
        scopes = ", ".join(selection["scopes"]) or "none"
        typer.echo(f"scope: {_one_line(scopes)} (derived: {_one_line(selection['reason'])})")
        for limit in selection.get("limits", []):
            typer.echo(f"  scope limit: {_one_line(limit)}")
    if payload.get("comparisons") == []:
        typer.echo("The change touches no supported application agent; nothing was compared.")
    for item in payload.get("comparisons", [payload]):
        if "comparisons" in payload:
            typer.echo(f"Scope {item['head']['scope']}: {item['comparison_status']}")
        _print_rows(item, _one_line)
    typer.echo(payload["limits"][-1])


def _print_rows(payload: dict[str, Any], _one_line: Any) -> None:
    status = payload["comparison_status"]
    rows = payload["rows"]
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
                also = [
                    site
                    for site in value.get("construction_sites", [])
                    if site != value.get("binding_location")
                ]
                typer.echo(
                    f"  {side}: {_one_line(value.get('signature') or value['tool'])} at {_one_line(value.get('binding_location'))}"
                    + (f" (also listed at {_one_line(', '.join(also))})" if also else "")
                )
                if value.get("bound_when"):
                    typer.echo(f"    bound {_one_line(_bound(value))}")
                if value.get("object"):
                    for line in _object_lines(value):
                        typer.echo(f"    {_one_line(line)}")
                elif definition:
                    typer.echo(
                        f"    implementation: {_one_line(definition['source'])}:{definition['line']} ({str(definition['implementation_sha256'])[:12]})"
                    )
                for path in value.get("import_path", []):
                    hops = " → ".join(
                        f"{step['path']}:{step['line']}"
                        for step in path["steps"]
                        if step.get("line") is not None
                    )
                    typer.echo(f"    imported: {_one_line(hops)}")
                for line in _reach_lines(value):
                    typer.echo(f"    {_one_line(line)}")
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
    # Test files are never the application (#876); say which were left
    # out, so a product module that only looks like a test is visible.
    excluded = sorted(
        set(payload["base"].get("excluded_tests", []))
        | set(payload["head"].get("excluded_tests", []))
    )
    if excluded:
        shown = ", ".join(_one_line(path) for path in excluded[:5])
        more = f", and {len(excluded) - 5} more" if len(excluded) > 5 else ""
        typer.echo(f"Test files not read as the application ({len(excluded)}): {shown}{more}")
