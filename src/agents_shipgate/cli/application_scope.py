"""The application comparison's scope, derived from the change (#875).

``diff --application`` without ``--scope`` does not compare the whole
repository, nor the directory the change touched. It relates each changed
Python file to the OpenAI Agents SDK and Google ADK files that import it, or
that it imports, a few hops away, and compares the outermost package that
holds each related group: a change to ``backend/app/services/tools.py`` that
``backend/app/services/adk_service.py`` imports is compared in
``backend/app``. Two independent groups are two comparisons, never the root.

Everything is read from the two commits' objects, never imported or run; a
file is an agent file by the same signals discovery scores
(:func:`application_frameworks`).
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from agents_shipgate.cli.discovery.artifacts import _skip_part
from agents_shipgate.cli.discovery.signals import _is_test_path, application_frameworks
from agents_shipgate.cli.verify.git import _blob_contents, _run_git_bounded_output, _TreeBlob

SUPPORTED = frozenset({"openai_agents_sdk", "google_adk"})
#: Import hops a change is related to an agent file through.
MAX_IMPORT_DEPTH = 6
#: Python files one side reads to relate the change; past it, a named limit.
MAX_RELATED_FILES = 2000
#: Files, and bytes, one side reads to find its agent files; past either, a
#: named limit.
MAX_AGENT_CANDIDATES = 5000
MAX_AGENT_CANDIDATE_BYTES = 64 * 1024 * 1024
#: The largest Python file read, as the comparison itself bounds it.
MAX_PYTHON_BYTES = 2_000_000
_LISTING_BYTES = 64 * 1024 * 1024
_BATCH_BYTES = 16 * 1024 * 1024
#: Text every supported agent file holds: an ``agents``/``openai_agents``
#: import, or ``adk`` (``google.adk``, ``from google import adk``).
_AGENT_TEXT = r"(agents|adk|\.(tools|handoffs|mcp_servers|sub_agents))"
#: Classes whose construction, or a subclass of which, makes a file an agent
#: file for scope choice; a module that only defines tools is not one.
_AGENT_CLASSES = frozenset({"Agent", "LlmAgent", "SequentialAgent", "ParallelAgent", "LoopAgent", "BaseAgent"})
#: Keywords a copy of an agent passes its own capabilities by.
_CAPABILITIES = frozenset({"tools", "handoffs", "mcp_servers", "sub_agents"})


@dataclass
class ScopeSelection:
    """The comparisons a change derives, and why: each ``(base scope, head
    scope)``, the same path unless an application directory moved."""

    pairs: list[tuple[str, str]] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)

    @property
    def scopes(self) -> list[str]:
        return [new for _, new in self.pairs]

    def reason(self) -> str:
        if not self.changed:
            return "The change touches no Python source."
        if not self.pairs and all(_is_test_path(path) for path in self.changed):
            return "The change touches no Python source outside tests."
        if not self.pairs:
            shown = ", ".join(self.changed[:5]) + (
                f", and {len(self.changed) - 5} more" if len(self.changed) > 5 else ""
            )
            verb = "is not an agent file, and is not" if len(self.changed) == 1 else "are not agent files, and are not"
            bounded = " within the bounds read" if self.limits else ""
            return (
                f"The change touches no OpenAI Agents SDK or Google ADK agent{bounded}: {shown} "
                f"{verb} imported by one or importing one within {MAX_IMPORT_DEPTH} hops."
            )
        parts = []
        for relation in self.relations:
            changed, agent, via = relation["first"]
            how = (
                f"{changed} is an agent file"
                if changed == agent
                else f"{changed} is related to agent file {agent} through " + " → ".join(via)
            )
            if relation["base_scope"] != relation["scope"]:
                how += f" (moved from {relation['base_scope']})"
            if relation.get("outside"):
                how += (
                    f"; agent files elsewhere also import it and are not compared: "
                    f"{', '.join(relation['outside'][:3])}"
                    + (f", and {len(relation['outside']) - 3} more" if len(relation["outside"]) > 3 else "")
                )
            parts.append(f"{relation['scope']}: {how}")
        return "; ".join(parts)

    def payload(self) -> dict[str, Any]:
        return {
            "mode": "derived",
            "scopes": self.scopes,
            "reason": self.reason(),
            "changed_files": self.changed[:50],
            "relations": [
                {key: value for key, value in relation.items() if key != "first"}
                for relation in self.relations
            ],
            "limits": self.limits,
        }


class _Tree:
    """One commit's Python files, read on demand from its objects."""

    def __init__(self, workspace: Path, commit: str) -> None:
        self.workspace = workspace
        self.commit = commit
        self.blobs: dict[str, _TreeBlob] = {}
        self.inits: set[str] = set()
        self.directories: set[str] = set()
        #: ``path -> what`` of entries that are neither a regular file nor a
        #: directory: a submodule, a link to a module.
        self.special: dict[str, str] = {}
        #: Modules under a directory discovery skips: related, never agents.
        self.skipped: set[str] = set()
        #: Agent files that construct, subclass or copy an agent — not the
        #: modules that only rewire one (``server.py``).
        self.builders: set[str] = set()
        #: Files whose import reach stopped at the hop bound.
        self.cut_from: set[str] = set()
        #: The agent files found, once looked for.
        self.agents: set[str] = set()
        self.listed = self._list()
        self._texts: dict[str, str | None] = {}
        self._imports: dict[str, set[str]] = {}
        #: The modules an import names, without the packages it runs on the
        #: way: ``from app import x`` names ``app/__init__.py``; ``from
        #: app.other import y`` only runs it.
        self._direct: dict[str, set[str]] = {}
        self._sink: set[str] | None = None
        #: ``module -> (builders it imports within MAX_IMPORT_DEPTH, cut)``.
        self._builders_reached: dict[str, tuple[set[str], bool]] = {}
        #: ``(path, on_request) -> _builds_agent`` of that file's text.
        self._builds: dict[tuple[str, bool], bool] = {}
        #: The namespace path entries an importer's absolute imports go
        #: through: ``src`` for ``myapp.tools`` in ``src/myapp`` without an
        #: ``__init__.py``; the scope must hold them for the reader to follow.
        self.import_roots: dict[str, set[str]] = {}
        self._by_stem: dict[str, list[str]] | None = None
        self.limits: list[str] = []
        self._related = 0
        #: Whether following imports stopped at ``MAX_IMPORT_DEPTH`` with more
        #: to follow.
        self.cut_short = False

    def _list(self) -> bool:
        output = _run_git_bounded_output(
            self.workspace,
            ["ls-tree", "-r", "-z", "-l", self.commit],
            max_output_bytes=_LISTING_BYTES,
        )
        if output is None:
            return False
        for raw in output.split(b"\0"):
            meta, _, name = raw.partition(b"\t")
            fields = meta.split()
            path = name.decode("utf-8", errors="replace")
            if len(fields) == 4 and fields[0] == b"160000":
                # A submodule: its content is not in this tree (#875 review).
                self.special[path] = "a submodule, whose content is not read"
                continue
            if len(fields) == 4 and fields[0] == b"120000" and (
                path.endswith(".py") or not PurePosixPath(path).suffix
            ):
                # A link to a module or a directory: what it points at is not
                # read (#875 review).
                self.special[path] = "a link, which is not read"
                continue
            if len(fields) != 4 or fields[0] not in {b"100644", b"100755"}:
                continue
            self.directories.update(str(parent) for parent in PurePosixPath(path).parents if str(parent) != ".")
            if not path.endswith(".py"):
                continue
            if any(_skip_part(part) for part in PurePosixPath(path).parts[:-1]):
                # ``build/``, ``fixtures/``: never an agent file, but an agent
                # may import from it (#875 review).
                self.skipped.add(path)
            try:
                size = int(fields[3])
            except ValueError:
                continue
            self.blobs[path] = _TreeBlob(fields[0].decode(), fields[2].decode(), size)
            if path.endswith("/__init__.py"):
                self.inits.add(str(PurePosixPath(path).parent))
        return True

    def holds(self, directory: str) -> bool:
        return not directory or directory in self.directories

    def application(self, path: str) -> bool:
        """A Python file read to relate the change. A file named like a test
        is read when an agent imports it; it is never an agent file."""

        return path in self.blobs and self.blobs[path].size <= MAX_PYTHON_BYTES

    def texts(self, paths: list[str], *, candidates: bool = False) -> None:
        """Read ``paths`` in bounded batches; past the budget, a named limit."""

        wanted = [path for path in paths if path not in self._texts and self.application(path)]
        if candidates:
            bound, room = MAX_AGENT_CANDIDATES, MAX_AGENT_CANDIDATES
            what = "to find its agent files"
            total_bytes, kept = 0, []
            for path in wanted:
                total_bytes += self.blobs[path].size
                if total_bytes > MAX_AGENT_CANDIDATE_BYTES:
                    self._limit(
                        f"{self.commit[:12]} holds more than {MAX_AGENT_CANDIDATE_BYTES // (1024 * 1024)} MB "
                        f"of Python to read {what}; files past the bound were not read."
                    )
                    break
                kept.append(path)
            wanted = kept
        else:
            bound, room = MAX_RELATED_FILES, MAX_RELATED_FILES - self._related
            what = "to relate the change"
        if len(wanted) > room:
            self._limit(
                f"{self.commit[:12]} holds more than {bound} Python files to read {what}; "
                "files past the bound were not read."
            )
            wanted = wanted[: max(room, 0)]
        if not candidates:
            self._related += len(wanted)
        chunk: list[str] = []
        total = 0
        for path in [*wanted, None]:
            size = self.blobs[path].size if path is not None else 0
            if path is not None and (not chunk or total + size <= _BATCH_BYTES):
                chunk.append(path)
                total += size
                continue
            contents = _blob_contents(self.workspace, [self.blobs[item] for item in chunk]) if chunk else {}
            for item in chunk:
                data = contents.get(self.blobs[item].oid)
                self._texts[item] = data.decode("utf-8", errors="replace") if data is not None else None
            if chunk and not contents:
                self._limit(f"{len(chunk)} Python files of {self.commit[:12]} could not be read.")
            chunk, total = ([path], size) if path is not None else ([], 0)

    def _limit(self, message: str) -> None:
        if message not in self.limits:
            self.limits.append(message)

    def text(self, path: str) -> str | None:
        if path not in self._texts:
            self.texts([path])
        return self._texts.get(path)

    def agent_files(self) -> set[str]:
        """Files that build an agent: discovery scores them as SDK or ADK
        sources, and they construct an agent class or subclass one. A module
        that only defines tools is a changed file an agent may import."""

        output = _run_git_bounded_output(
            self.workspace,
            ["grep", "-l", "-z", "-E", _AGENT_TEXT, self.commit, "--", "*.py"],
            max_output_bytes=_LISTING_BYTES,
            allowed_returncodes=(0, 1),
        )
        prefix = f"{self.commit}:"
        candidates = [
            item.decode("utf-8", errors="replace").removeprefix(prefix)
            for item in (output or b"").split(b"\0")
            if item
        ]
        candidates = [
            path
            for path in candidates
            if self.application(path) and not _is_test_path(path) and path not in self.skipped
        ]
        if output is None:
            self._limit(f"The agent files of {self.commit[:12]} could not be searched.")
        self.texts(candidates, candidates=True)
        wiring: set[str] = set()
        for path in candidates:
            text = self._texts.get(path)
            if text is None:
                continue
            if application_frameworks(path, text) & SUPPORTED and _builds_agent(text):
                self.builders.add(path)
            elif _rewires_agent(text):
                wiring.add(path)
        # A module that rewires an agent is agent wiring only when it imports a
        # module that builds one: ``params.tools = ...`` on another library's
        # object is not (#875 review).
        self._consumers()
        return self.builders | {path for path in wiring if self.imports(path) & self.builders}

    def _consumers(self) -> None:
        """Add the modules that call a function they import from an agent
        builder — ``root_agent = make('bot', [shared_tool])`` with ``make``
        from ``lib/factory.py`` — which build their agent through it (#875
        review)."""

        stems = sorted(
            {
                PurePosixPath(path).parent.name if path.endswith("/__init__.py") else PurePosixPath(path).stem
                for path in self.builders
            }
            - {""}
        )
        if not stems:
            return
        found: list[str] = []
        for start in range(0, len(stems), 200):
            chunk = stems[start : start + 200]
            output = _run_git_bounded_output(
                self.workspace,
                ["grep", "-l", "-z", "-F", *[item for stem in chunk for item in ("-e", stem)], self.commit, "--", "*.py"],
                max_output_bytes=_LISTING_BYTES,
                allowed_returncodes=(0, 1),
            )
            if output is None:
                self._limit(f"The modules importing the agent files of {self.commit[:12]} could not be searched.")
                return
            prefix = f"{self.commit}:"
            found += [
                item.decode("utf-8", errors="replace").removeprefix(prefix) for item in output.split(b"\0") if item
            ]
        candidates = [
            path
            for path in sorted(set(found))
            if path not in self.builders
            and self.application(path)
            and not _is_test_path(path)
            and path not in self.skipped
        ]
        self.texts(candidates, candidates=True)
        for path in candidates:
            text = self._texts.get(path)
            if text is None or not self.imports(path) & self.builders:
                continue
            try:
                module = ast.parse(text)
            except (SyntaxError, ValueError, RecursionError):
                continue
            directory = PurePosixPath(path).parent
            callable_names: set[str] = set()
            for node in ast.walk(module):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if node.level:
                            base = directory
                            for _ in range(node.level - 1):
                                base = base.parent
                            parts = [*base.parts, *(node.module.split(".") if node.module else [])]
                            module_targets = self._exact(parts, [])
                            name_targets = self._exact(parts, [alias.name]) - module_targets
                        elif node.module:
                            module_targets = self._absolute(node.module.split("."), [], path)
                            name_targets = self._absolute(node.module.split("."), [alias.name], path) - module_targets
                        else:
                            continue
                        bound = alias.asname or alias.name
                        # ``from lib.factory import make``: a function of it.
                        if alias.name in set().union(*(self._factories(item) for item in module_targets & self.builders)):
                            callable_names.add(bound)
                        # ``from lib import factory``: the module itself.
                        for item in name_targets & self.builders:
                            callable_names |= {f"{bound}.{name}" for name in self._factories(item)}
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        modules = self._absolute(alias.name.split("."), [], path) & self.builders
                        factories = set().union(*(self._factories(item) for item in modules))
                        if factories:
                            head = alias.asname or alias.name.split(".", 1)[0]
                            callable_names |= {f"{head}.{name}" for name in factories}
            if any(
                isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Name) and node.func.id in callable_names)
                    or _dotted(node.func) in callable_names
                )
                for node in ast.walk(module)
            ):
                self.builders.add(path)

    def importers_of(self, paths: set[str]) -> dict[str, set[str]] | None:
        """``path -> the modules importing it`` for each of ``paths``, read from
        one search of their module names; None past the bound."""

        stems = sorted(
            {
                PurePosixPath(path).parent.name if path.endswith("/__init__.py") else PurePosixPath(path).stem
                for path in paths
            }
            - {""}
        )
        # An import spells the module's name as a word; a relative one
        # (``from . import tool``) only inside the package holding it.
        searches = [
            ["-w", *[item for stem in stems[start : start + 200] for item in ("-e", stem)], "--", "*.py"]
            for start in range(0, len(stems), 200)
        ]
        packages = sorted({self.package_root(str(PurePosixPath(path).parent)) for path in paths} - {"", "."})
        for start in range(0, len(packages), 200):
            searches.append(
                ["-e", "from .", "--", *[f"{package}/*.py" for package in packages[start : start + 200]]]
            )
        found: set[str] = set()
        for search in searches:
            output = _run_git_bounded_output(
                self.workspace,
                ["grep", "-l", "-z", "-F", *search[: search.index("--")], self.commit, *search[search.index("--") :]],
                max_output_bytes=_LISTING_BYTES,
                allowed_returncodes=(0, 1),
            )
            if output is None:
                return None
            prefix = f"{self.commit}:"
            found |= {
                item.decode("utf-8", errors="replace").removeprefix(prefix) for item in output.split(b"\0") if item
            }
        candidates = sorted(
            path for path in found if self.application(path) and not _is_test_path(path) and path not in paths
        )
        if len(candidates) > MAX_RELATED_FILES:
            return None
        self.texts(candidates)
        importers: dict[str, set[str]] = {path: set() for path in paths}
        for candidate in candidates:
            for target in self.direct_imports(candidate) & paths:
                importers[target].add(candidate)
        return importers

    def dependents(self, paths: set[str]) -> tuple[dict[str, set[str]], set[str]] | None:
        """``module -> the files of paths it reaches`` for every module that
        imports one of ``paths``, or a module that does, up to
        ``MAX_IMPORT_DEPTH`` levels — through a package's re-export or a module
        with no agent on the way (#875 review) — and the modules whose
        importers were not searched at that bound. None past the bound."""

        edges: dict[str, set[str]] = {}
        seen = set(paths)
        frontier = set(paths)
        for _ in range(MAX_IMPORT_DEPTH):
            importers = self.importers_of(frontier)
            if importers is None:
                return None
            following: set[str] = set()
            for target, modules in importers.items():
                for module in modules:
                    edges.setdefault(module, set()).add(target)
                    # Through agent files too: one may re-export the change,
                    # or hand on an agent another module copies (#875 review).
                    if module not in seen:
                        seen.add(module)
                        following.add(module)
            frontier = following
            if not frontier:
                break
        origins: dict[str, set[str]] = {path: {path} for path in paths}
        grown = True
        while grown:
            grown = False
            for module, targets in edges.items():
                reached = set().union(*(origins.get(target, set()) for target in targets))
                if not reached <= origins.get(module, set()):
                    origins[module] = origins.get(module, set()) | reached
                    grown = True
        return {module: found for module, found in origins.items() if module not in paths}, frontier

    def builds(self, path: str, *, on_request: bool = False) -> bool:
        """``_builds_agent`` of one file, read once."""

        key = (path, on_request)
        if key not in self._builds:
            text = self.text(path)
            self._builds[key] = text is not None and _builds_agent(text, on_request=on_request)
        return self._builds[key]

    def hands_arguments(self, path: str) -> bool:
        """Whether a module calls something it imports from the repository
        with arguments of its own: what a module does to build an agent
        through another's code (``make("bot", [tool])``). An entry script
        (``from app.__main__ import main; main()``) does not (#875 review)."""

        text = self.text(path)
        try:
            module = ast.parse(text) if text is not None else None
        except (SyntaxError, ValueError, RecursionError):
            return True
        if module is None:
            return True
        directory = PurePosixPath(path).parent
        names: set[str] = set()
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if node.level:
                        base = directory
                        for _ in range(node.level - 1):
                            base = base.parent
                        parts = [*base.parts, *(node.module.split(".") if node.module else [])]
                        found = self._exact(parts, [alias.name])
                    elif node.module:
                        found = self._absolute(node.module.split("."), [alias.name], path)
                    else:
                        found = set()
                    if found:
                        names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if self._absolute(alias.name.split("."), [], path):
                        names.add(alias.asname or alias.name.split(".", 1)[0])
        def rooted(node: ast.AST) -> bool:
            while isinstance(node, ast.Attribute | ast.Call | ast.Subscript):
                node = node.func if isinstance(node, ast.Call) else node.value
            return isinstance(node, ast.Name) and node.id in names

        for node in ast.walk(module):
            if isinstance(node, ast.Call) and (node.args or node.keywords) and rooted(node.func):
                return True
            # ``setattr(factory, "TOOLS", [tool])``.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"setattr", "delattr"}
                and node.args
                and rooted(node.args[0])
            ):
                return True
            # ``factory.TOOLS = [tool]``: state the builder reads (#875 review).
            if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Delete):
                targets = node.targets if isinstance(node, ast.Assign | ast.Delete) else [node.target]
                if any(isinstance(target, ast.Attribute | ast.Subscript) and rooted(target) for target in targets):
                    return True
        return False

    def builders_reached(self, module: str) -> tuple[set[str], bool]:
        """The agent builders ``module`` imports within ``MAX_IMPORT_DEPTH``
        hops, and whether its imports went deeper."""

        cached = self._builders_reached.get(module)
        if cached is not None:
            return cached
        seen, frontier, reached = {module}, [module], set()
        for _ in range(MAX_IMPORT_DEPTH):
            self.texts([target for item in frontier for target in self.imports(item)])
            following = [
                target
                for item in frontier
                for target in sorted(self.imports(item))
                if target not in seen and self.application(target)
            ]
            reached |= {target for target in following if target in self.builders}
            seen.update(following)
            frontier = following
        result = (reached, bool(frontier) and any(self.imports(item) - seen for item in frontier))
        self._builders_reached[module] = result
        return result

    def _factories(self, path: str) -> set[str]:
        """The module-level functions of an agent builder that return an agent
        they construct: ``def make(...): return Agent(...)``, or through a
        local bound to one."""

        text = self._texts.get(path)
        try:
            module = ast.parse(text) if text is not None else None
        except (SyntaxError, ValueError, RecursionError):
            module = None
        if module is None:
            return set()
        found: set[str] = set()
        for function in module.body:
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            bound = {
                target.id
                for node in ast.walk(function)
                if isinstance(node, ast.Assign | ast.AnnAssign)
                and isinstance(node.value, ast.Call)
                and _agent_call(node.value)
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                if isinstance(target, ast.Name)
            }
            for node in ast.walk(function):
                if isinstance(node, ast.Return) and (
                    (isinstance(node.value, ast.Call) and _agent_call(node.value))
                    or (isinstance(node.value, ast.Name) and node.value.id in bound)
                ):
                    found.add(function.name)
        return found

    def imports(self, path: str) -> set[str]:
        """The repository files one module's imports name."""

        if path in self._imports:
            return self._imports[path]
        found: set[str] = set()
        self._imports[path] = found
        direct = self._direct[path] = set()
        text = self.text(path)
        if text is None:
            return found
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return found
        outer, self._sink = self._sink, direct
        try:
            self._import_targets(tree, path, found)
        finally:
            self._sink = outer
        direct.discard(path)
        found.discard(path)
        return found

    def direct_imports(self, path: str) -> set[str]:
        """The modules ``path``'s imports name, not the packages they run."""

        self.imports(path)
        return self._direct.get(path, set())

    def _import_targets(self, tree: ast.Module, path: str, found: set[str]) -> None:
        directory = PurePosixPath(path).parent
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [alias.name for alias in node.names if alias.name != "*"]
                if node.level:
                    base = directory
                    for _ in range(node.level - 1):
                        base = base.parent
                    parts = [*base.parts, *(node.module.split(".") if node.module else [])]
                    found |= self._exact(parts, names)
                elif node.module:
                    found |= self._absolute(node.module.split("."), names, path)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    found |= self._absolute(alias.name.split("."), [], path)
            elif (
                isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")
                    or (isinstance(node.func, ast.Name) and node.func.id in {"import_module", "__import__"})
                )
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and not node.args[0].value.startswith(".")
            ):
                # ``importlib.import_module("app.tools")``: an import by name.
                found |= self._absolute(node.args[0].value.split("."), [], path)

    def _module(self, parts: list[str]) -> list[str]:
        """The module ``parts`` names, and every package ``__init__.py`` the
        import runs on the way: ``pkg.impl`` runs ``pkg/__init__.py`` first."""

        stem = "/".join(parts)
        found = [item for item in (f"{stem}.py", f"{stem}/__init__.py") if item in self.blobs]
        if self._sink is not None:
            self._sink.update(found)
        if found:
            for length in range(1, len(parts)):
                init = "/".join([*parts[:length], "__init__.py"])
                if init in self.blobs:
                    found.append(init)
        return found

    def _exact(self, parts: list[str], names: list[str]) -> set[str]:
        found = set(self._module(parts))
        for name in names:
            found |= set(self._module([*parts, name]))
        return found

    def _absolute(self, parts: list[str], names: list[str], importer: str) -> set[str]:
        """``a.b.c`` wherever the importer's path would find it."""

        if self._by_stem is None:
            self._by_stem = {}
            for path in self.blobs:
                module = path[: -len("/__init__.py")] if path.endswith("/__init__.py") else path[:-3]
                self._by_stem.setdefault(PurePosixPath(module).name, []).append(module)
        own = str(PurePosixPath(importer).parent) if "/" in importer else ""
        found: set[str] = set()
        for spelling in [parts, *([*parts, name] for name in names)]:
            suffix = "/".join(spelling)
            roots = sorted(
                {
                    module[: -len(suffix)].rstrip("/")
                    for module in self._by_stem.get(spelling[-1], [])
                    if module == suffix or module.endswith("/" + suffix)
                }
            )
            # A package directory is not a path entry — ``middlewares/logging.py``
            # in a package is ``tgbot.middlewares.logging``, never ``logging`` —
            # except the importer's own directory, a script's first entry.
            roots = [root for root in roots if not root or root not in self.inits or root == own]
            if not roots:
                continue
            near = [root for root in roots if not root or root == own or importer.startswith(root + "/")]
            if near:
                chosen = [max(near, key=len)]
            elif spelling[0] in sys.stdlib_module_names:
                # ``import logging`` is the standard library unless a module
                # on the importer's own path shadows it.
                chosen = []
            else:
                # Elsewhere, only one path entry holding a package of that
                # name (``libs/shared/src/shared/__init__.py`` of a monorepo)
                # is taken as the module; a loose same-named script is not
                # what runs.
                packaged = [
                    root for root in roots if ("/".join([root, spelling[0]]) if root else spelling[0]) in self.inits
                ]
                chosen = packaged if len(packaged) == 1 and len(roots) == 1 else []
            for root in chosen:
                modules = self._module([*([root] if root else []), *spelling])
                found |= set(modules)
                top = "/".join([root, spelling[0]]) if root else spelling[0]
                if modules and top not in self.inits:
                    # ``myapp.tools`` found through ``src/`` with ``src/myapp``
                    # a namespace package: the reader needs ``src`` in scope.
                    self.import_roots.setdefault(importer, set()).add(root)
        return found

    def package_root(self, directory: str) -> str:
        """The outermost regular package holding ``directory``: relative
        imports climb within it, and absolute ones name it."""

        current = PurePosixPath(directory) if directory else PurePosixPath("")
        if str(current) not in self.inits and directory:
            return directory
        while current.parts and str(current.parent) != "." and str(current.parent) in self.inits:
            current = current.parent
        return str(current) if current.parts else ""


def _agent_call(call: ast.Call) -> bool:
    func = call.func.value if isinstance(call.func, ast.Subscript) else call.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    return name in _AGENT_CLASSES


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else None
    return node.id if isinstance(node, ast.Name) else None


def _rewires(node: ast.AST) -> bool:
    """``x.tools = ...``, ``x.tools += ...``, ``x.tools.append(...)``,
    ``setattr(x, "tools", ...)``."""

    if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return any(isinstance(item, ast.Attribute) and item.attr in _CAPABILITIES for item in targets)
    if isinstance(node, ast.Call):
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in {"append", "extend", "insert", "remove", "pop", "clear"}
            and isinstance(func.value, ast.Attribute)
            and func.value.attr in _CAPABILITIES
        ):
            return True
        return (
            isinstance(func, ast.Name)
            and func.id in {"setattr", "delattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _CAPABILITIES
        )
    return False


def _rewired_values(node: ast.AST) -> list[ast.expr]:
    """What a rewire (``_rewires``) gives the capability; a removal gives nothing."""

    if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
        return [node.value] if node.value is not None else []
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in {"append", "extend", "insert"}:
            return list(node.args[-1:]) + [item.value for item in node.keywords]
        if node.func.attr in {"remove", "pop", "clear"}:
            return []
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr":
        return list(node.args[2:3])
    return []


def _copied_values(call: ast.Call) -> list[ast.expr]:
    """The capabilities a copy passes: ``clone(tools=...)``, ``update={"tools": ...}``;
    a non-literal ``update`` is its own value."""

    found: list[ast.expr] = []
    for item in call.keywords:
        if item.arg in _CAPABILITIES:
            found.append(item.value)
        elif item.arg == "update":
            if isinstance(item.value, ast.Dict):
                found += [
                    value
                    for key, value in zip(item.value.keys, item.value.values, strict=False)
                    if key is None or not isinstance(key, ast.Constant) or key.value in _CAPABILITIES
                ]
            else:
                found.append(item.value)
    return found


def _rewires_agent(text: str) -> bool:
    """Whether a module changes an agent's capabilities after construction: wiring,
    whatever framework signal it carries (``server.py`` importing an app's
    agent and appending a tool, #875 review)."""

    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return False
    return any(_rewires(node) for node in ast.walk(tree))


def _builds_agent(text: str, *, on_request: bool = False) -> bool:
    """Whether a module constructs an agent class (under any name it imports it
    as), subclasses one, copies an agent with capabilities of its own
    (``base.clone(tools=[...])``, ``replace(agent, tools=...)``), or changes an
    agent's capabilities after construction (``support.tools.append(x)``) —
    all of which the readers treat as agent wiring (#876, #875 review).

    ``on_request``: only inside a function or class, or by subclassing — an
    agent another module can have built with its own tools, not only one the
    module builds once (#875 review)."""

    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return False
    # ``from agents import Agent as SdkAgent``, ``from google.adk.agents import
    # LlmAgent as Llm``.
    aliases = {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").split(".", 1)[0] in {"agents", "google"}
        for alias in node.names
        if alias.asname and alias.name in _AGENT_CLASSES
    }

    #: Names the module binds at import: its functions, classes, imports and
    #: top-level assignments.
    module_names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            module_names.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            module_names |= {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    module_names.add(target.id)

    def named(node: ast.AST) -> bool:
        if isinstance(node, ast.Subscript):
            node = node.value
        if isinstance(node, ast.Attribute):
            return node.attr in _AGENT_CLASSES
        return isinstance(node, ast.Name) and (node.id in _AGENT_CLASSES or node.id in aliases)

    agents = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and named(node.value.func)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    def copies(call: ast.Call) -> bool:
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name not in {"clone", "replace", "model_copy"}:
            return False
        for item in call.keywords:
            if item.arg in _CAPABILITIES:
                return True
            # Google ADK / pydantic: ``agent.clone(update={"tools": [...]})``;
            # an ``update`` that is not a literal only on an agent the module
            # constructs — a settings model's ``model_copy`` is not (#875 review).
            if item.arg != "update":
                continue
            if isinstance(item.value, ast.Dict):
                if any(
                    key is None or not isinstance(key, ast.Constant) or key.value in _CAPABILITIES
                    for key in item.value.keys
                ):
                    return True
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in agents:
                return True
        return False

    def own(value: ast.expr) -> bool:
        """A single name the module binds at import: ``agent.tools.append(lookup)``."""

        return isinstance(value, ast.Name) and value.id in module_names

    def fixed(value: ast.expr) -> bool:
        """``[lookup, search]``: a literal of names the module binds at import."""

        return isinstance(value, ast.List | ast.Tuple) and all(
            isinstance(item, ast.Name) and item.id in module_names for item in value.elts
        )

    if on_request:
        # On request: an agent built with capabilities another module can
        # supply or change — ``tools=tools``, ``list(REGISTRY.items)``,
        # ``self.tools``, ``**config`` — or an agent subclass; one built with
        # a fixed list of its own names (``tools=[lookup]``) takes nothing
        # from a caller but a copy or a rewire, which is the caller's own
        # construction (#875 review).
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(named(base) for base in node.bases):
                return True
            if isinstance(node, ast.Call) and named(node.func) and any(
                item.arg is None or (item.arg in _CAPABILITIES and not fixed(item.value)) for item in node.keywords
            ):
                return True
            # Or given afterwards: a rewire or a copy whose capabilities are
            # not the module's own (``agent.tools.append(extra)``,
            # ``BASE.clone(tools=tools)``) (#875 review).
            if _rewires(node) and not all(fixed(value) or own(value) for value in _rewired_values(node)):
                return True
            if isinstance(node, ast.Call) and copies(node) and not all(
                fixed(value) or own(value) for value in _copied_values(node)
            ):
                return True
        return False
    roots: list[ast.AST] = [tree]
    for root in roots:
        for node in ast.walk(root):
            if isinstance(node, ast.Call) and (named(node.func) or copies(node)):
                return True
            if _rewires(node) and not on_request:
                return True
            if isinstance(node, ast.ClassDef) and any(named(base) for base in node.bases):
                return True
    return False


def _common_directory(paths: set[str]) -> str:
    parents = [PurePosixPath(path).parent.parts for path in paths]
    common: list[str] = []
    for position, part in enumerate(parents[0]):
        if all(len(item) > position and item[position] == part for item in parents):
            common.append(part)
        else:
            break
    return "/".join(common)


def _relate(tree: _Tree, changed: set[str], agents: set[str]) -> list[tuple[set[str], dict[str, Any]]]:
    """Each changed file and an agent file an import path joins, with the
    files a scope needs to read that agent: the path, the agents it imports,
    the repository modules it imports, and the path entries they go through."""

    groups: list[tuple[set[str], dict[str, Any]]] = []

    def reach(start: str) -> dict[str, list[str]]:
        paths = {start: [start]}
        frontier = [start]
        for _ in range(MAX_IMPORT_DEPTH):
            following: list[str] = []
            tree.texts([target for item in frontier for target in tree.imports(item)])
            for item in frontier:
                for target in sorted(tree.imports(item)):
                    if target not in paths and tree.application(target):
                        paths[target] = [*paths[item], target]
                        following.append(target)
            frontier = following
        if frontier and any(tree.imports(item) for item in frontier):
            tree.cut_short = True
            tree.cut_from.add(start)
        return paths

    def needed(agent: str, members: set[str]) -> set[str]:
        files = set(members) | {target for target in tree.imports(agent) if tree.application(target)}
        # A path entry the reader must hold, as a file directly under it.
        for item in list(files):
            files |= {f"{root}/__entry__" if root else "__entry__" for root in tree.import_roots.get(item, ())}
        return files

    reached_from: dict[str, dict[str, list[str]]] = {}
    for path in sorted(changed & agents):
        groups.append((needed(path, {path}), {"changed": path, "agent": path, "via": [path]}))
    for agent in sorted(agents):
        reached = reached_from[agent] = reach(agent)
        for path in sorted(changed & set(reached)):
            if path == agent:
                continue
            members = {*reached[path], *(item for item in reached if item in agents)}
            groups.append((needed(agent, members), {"changed": path, "agent": agent, "via": reached[path]}))
    for path in sorted(changed - agents):
        if _is_test_path(path):
            # A test that imports an agent is not the application: it relates
            # only when an agent imports it (#875 review).
            continue
        reached = reach(path)
        for agent in sorted(agents & set(reached)):
            groups.append((needed(agent, set(reached[agent])), {"changed": path, "agent": agent, "via": reached[agent]}))
    return groups


def derive_scopes(workspace: Path, base_commit: str, head_commit: str) -> ScopeSelection:
    """The comparisons the change between two commits touches (#875)."""

    selection = ScopeSelection()
    output = _run_git_bounded_output(
        workspace,
        ["diff", "--name-status", "-z", "-M", base_commit, head_commit],
        max_output_bytes=_LISTING_BYTES,
    )
    if output is None:
        selection.limits.append(
            "The change between the two commits could not be listed; pass --scope."
        )
        return selection
    fields = [item.decode("utf-8", errors="replace") for item in output.split(b"\0")]
    changed_paths: set[str] = set()
    renames: list[tuple[str, str]] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        if status[:1] in {"R", "C"} and index + 2 < len(fields):
            old, new = fields[index + 1], fields[index + 2]
            changed_paths |= {old, new}
            if status[:1] == "R" and old.endswith(".py") and new.endswith(".py"):
                renames.append((old, new))
            index += 3
        else:
            changed_paths.add(fields[index + 1] if index + 1 < len(fields) else "")
            index += 2
    # A file named like a test is related when an agent imports it
    # (``app/test_runner.py``); it is never an agent file itself.
    python = sorted(path for path in changed_paths if path.endswith(".py"))
    selection.changed = python
    #: ``(side, scope, relation, outside)`` for every scope a change needs.
    found: list[tuple[str, str, dict[str, Any], list[str]]] = []
    trees: dict[str, _Tree] = {}
    for side, commit in (("base", base_commit), ("head", head_commit)):
        tree = trees[side] = _Tree(workspace, commit)
        if not tree.listed:
            selection.limits.append(f"The tree of {commit[:12]} could not be listed; pass --scope.")
            continue
        changed = {path for path in python if tree.application(path)}
        if changed:
            agents = tree.agent_files()
            tree.agents = agents
            by_changed: dict[str, list[tuple[str, dict[str, Any]]]] = {}
            for members, relation in _relate(tree, changed, agents):
                scope = tree.package_root(_common_directory(members))
                by_changed.setdefault(relation["changed"], []).append((scope, relation))
            for pairs in by_changed.values():
                union = _common_directory({f"{scope}/__entry__" if scope else "__entry__" for scope, _ in pairs})
                union = tree.package_root(union)
                if union:
                    # Every agent the change reaches, in the package holding them.
                    found.extend((side, union, relation, []) for _, relation in pairs)
                    continue
                # Only the repository root holds them all: compare where the
                # change's nearest agents are, and name the others, which
                # makes the answer partial rather than widening it (#875).
                nearest = [
                    item for item in pairs
                    if not any(other[0] != item[0] and _contains(item[0], other[0]) for other in pairs)
                ]
                kept = {scope for scope, _ in nearest}
                outside = sorted(
                    {
                        relation["agent"]
                        for scope, relation in pairs
                        if scope not in kept and not any(_contains(item, relation["agent"]) for item in kept)
                    }
                )
                for scope, relation in nearest:
                    found.append((side, scope, relation, outside))
    pairs = _pairs(found, renames, trees)
    # A changed application file no agent is related to is not compared: name
    # it, with why, rather than let the other scopes read as the whole change
    # (#875 review).
    # Related to a module that builds an agent, on either side, under either
    # path of a rename; being inside a compared scope is not enough — its
    # consumer may be outside it — nor is a module that only rewires an agent.
    related = {
        relation["changed"]
        for side, _, relation, _ in found
        if relation["agent"] in trees[side].builders
    }
    wired = {relation["changed"] for _, _, relation, _ in found} - related
    for old, new in renames:
        if old in related or new in related:
            related |= {old, new}
    unread: dict[str, str] = {}
    for tree in trees.values():
        if not tree.listed:
            continue
        for path in sorted(changed_paths):
            if path in tree.special and path not in unread:
                unread[path] = f"it is {tree.special[path]}"
        for path in python:
            if path in unread or path in related or _is_test_path(path) or path not in tree.blobs:
                continue
            if not tree.application(path):
                unread[path] = f"it exceeds {MAX_PYTHON_BYTES} bytes and is not read"
            elif path in wired:
                unread[path] = "only a module that rewires an agent relates to it"
            elif pairs:
                unread[path] = (
                    f"no agent file reaches it within {MAX_IMPORT_DEPTH} import hops, and longer "
                    "paths were not followed"
                    if tree.cut_short
                    else "no agent file imports it, and it imports no agent file within "
                    f"{MAX_IMPORT_DEPTH} hops"
                )
    # An agent outside every compared scope whose imports go deeper than the
    # bound may reach the change past it (#875 review).
    deep = sorted(
        {
            agent
            for side, tree in trees.items()
            for agent in tree.cut_from & tree.agents
            if not any(_contains(new if side == "head" else old, agent) for old, new in pairs)
        }
    )
    if pairs and deep and python:
        selection.limits.append(
            f"Agent files outside the compared scopes import more than {MAX_IMPORT_DEPTH} hops deep, "
            "so whether they reach the change is not established: "
            + ", ".join(deep[:5])
            + (f", and {len(deep) - 5} more" if len(deep) > 5 else "")
            + "."
        )
    # A module that imports the change — directly, through a package's
    # re-export or through other modules — and reaches an agent builder builds
    # an agent from it, however it spells that: a wrapper class, a static
    # method, a helper. It is compared only inside a scope that also holds the
    # change and those builders (#875 review).
    consumers: set[str] = set()
    uncertain: set[str] = set()
    for side, tree in trees.items():
        here = {path for path in python if path in tree.blobs and not _is_test_path(path)}
        if not tree.listed or not pairs or not here:
            continue
        closure = tree.dependents(here)
        if closure is None:
            selection.limits.append(
                f"The modules importing the change in {tree.commit[:12]} are more than the bound; "
                "agents built outside the compared scopes may use it."
            )
            continue
        dependents, frontier = closure
        compared = [new if side == "head" else old for old, new in pairs]
        if any(not any(_contains(scope, module) for scope in compared) for module in frontier):
            selection.limits.append(
                f"Modules import the change in {tree.commit[:12]} through more than {MAX_IMPORT_DEPTH} "
                "others, so whether agents built outside the compared scopes use it is not established."
            )
        for module, origins in sorted(dependents.items()):
            if module in tree.agents:
                continue
            builders, cut = tree.builders_reached(module)
            # One that builds on request, or an agent this module builds,
            # copies or changes itself: importing a module's agent is not
            # building one (#875 review).
            builders = {item for item in builders if tree.builds(item, on_request=True)} or (
                builders if tree.builds(module) else set()
            )
            inside = [scope for scope in compared if _contains(scope, module)]
            if builders and (tree.builds(module) or tree.hands_arguments(module)):
                if not any(all(_contains(scope, item) for item in origins | builders) for scope in inside):
                    consumers.add(module)
            elif cut and not inside:
                uncertain.add(module)
    if consumers:
        shown_consumers = sorted(consumers)
        selection.limits.append(
            "Modules import the change and an agent builder that no compared scope holds together, "
            "so the agents they build are not compared: "
            + ", ".join(shown_consumers[:5])
            + (f", and {len(shown_consumers) - 5} more" if len(shown_consumers) > 5 else "")
            + "."
        )
    uncertain -= consumers
    if uncertain:
        shown_uncertain = sorted(uncertain)
        selection.limits.append(
            f"Modules import the change and more than {MAX_IMPORT_DEPTH} hops of other modules, so "
            "whether they build an agent from it is not established: "
            + ", ".join(shown_uncertain[:5])
            + (f", and {len(shown_uncertain) - 5} more" if len(shown_uncertain) > 5 else "")
            + "."
        )
    if unread:
        shown = sorted(unread.items())
        selection.limits.append(
            "Changed files are not related to a compared agent, so their effect is not "
            "compared: "
            + "; ".join(f"{path} ({reason})" for path, reason in shown[:5])
            + (f"; and {len(shown) - 5} more" if len(shown) > 5 else "")
            + "."
        )
    if not pairs and any(tree.cut_short for tree in trees.values()):
        # "No agent" only as far as the imports were followed.
        selection.limits.append(
            f"Imports were followed {MAX_IMPORT_DEPTH} hops from each changed and agent file; "
            "a longer path from the change to an agent is not established."
        )
    # Every bound the trees hit, the searches after the relations included
    # (#875 review).
    for tree in trees.values():
        selection.limits.extend(item for item in tree.limits if item not in selection.limits)
    selection.pairs = [(old or ".", new or ".") for old, new in sorted(pairs)]
    for old_scope, new_scope in sorted(pairs):
        related = [
            (relation, outside)
            for side, scope, relation, outside in found
            if _contains(new_scope if side == "head" else old_scope, scope)
        ]
        outside = sorted({path for _, names in related for path in names if not any(_contains(new, path) for _, new in pairs)})
        first = related[0][0] if related else {"changed": "", "agent": "", "via": []}
        selection.relations.append(
            {
                "scope": new_scope or ".",
                "base_scope": old_scope or ".",
                "changed": sorted({relation["changed"] for relation, _ in related}),
                "agent_files": sorted({relation["agent"] for relation, _ in related}),
                "via": first["via"],
                "outside": outside,
                "first": (first["changed"], first["agent"], first["via"]),
            }
        )
        if outside:
            limit = (
                f"Agent files outside {new_scope or '.'} also import the change and are not compared: "
                + ", ".join(outside[:5])
                + (f", and {len(outside) - 5} more" if len(outside) > 5 else "")
                + "."
            )
            if limit not in selection.limits:
                selection.limits.append(limit)
    return selection


def _pairs(
    found: list[tuple[str, str, dict[str, Any], list[str]]],
    renames: list[tuple[str, str]],
    trees: dict[str, _Tree],
) -> list[tuple[str, str]]:
    """The ``(base scope, head scope)`` comparisons: the same path, reduced to
    the outermost of nested ones, except where a module moved.

    A module moved inside one package is one comparison of that package. An
    application directory moved as a whole — its old scope gone from the head,
    its new one absent from the base — is one relocation, compared old path to
    new path. A module moved between two applications that both remain is a
    change to each, never a root-wide scope (#875)."""

    scopes = {scope for _, scope, _, _ in found}
    relocations: set[tuple[str, str]] = set()
    base_tree, head_tree = trees.get("base"), trees.get("head")
    for old, new in renames:
        old_scope = next((scope for side, scope, _, _ in found if side == "base" and _contains(scope, old)), None)
        new_scope = next((scope for side, scope, _, _ in found if side == "head" and _contains(scope, new)), None)
        if old_scope is None and new_scope is None:
            continue
        joint = (head_tree or base_tree).package_root(_common_directory({old, new})) if (head_tree or base_tree) else ""
        if joint:
            scopes.add(joint)
            continue
        if (
            old_scope is not None
            and new_scope is not None
            and old_scope != new_scope
            and head_tree is not None
            and base_tree is not None
            and not head_tree.holds(old_scope)
            and not base_tree.holds(new_scope)
        ):
            relocations.add((old_scope, new_scope))
    # ``apps/foo -> services/foo`` holds ``apps/foo/sub -> services/foo/sub``:
    # one comparison, not two that repeat its rows (#875 review).
    relocations = {
        (old, new)
        for old, new in relocations
        if not any(
            (outer_old, outer_new) != (old, new)
            and _contains(outer_old, old)
            and _contains(outer_new, new)
            for outer_old, outer_new in relocations
        )
    }
    moved = {scope for pair in relocations for scope in pair}
    kept: list[str] = []
    for scope in sorted(scopes - moved, key=lambda item: (len(PurePosixPath(item).parts) if item else 0, item)):
        # A scope inside a relocated one is compared with it.
        if not any(_contains(outer, scope) for outer in [*kept, *moved]):
            kept.append(scope)
    return [(scope, scope) for scope in kept] + sorted(relocations)


def _contains(outer: str, inner: str) -> bool:
    return not outer or inner == outer or inner.startswith(outer + "/")
