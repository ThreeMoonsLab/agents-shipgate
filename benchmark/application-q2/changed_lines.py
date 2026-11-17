"""Locate changed constructor arguments and decorated tool functions from pinned Git diffs."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{40}")
_REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")
_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
_CONSTRUCTORS = {
    "agents.Agent", "agents.agent.Agent", "google.adk.Agent",
    "google.adk.agents.Agent", "google.adk.agents.LlmAgent",
    "google.adk.agents.SequentialAgent", "google.adk.agents.ParallelAgent",
    "google.adk.agents.LoopAgent",
}
_DECORATORS = {"agents.function_tool", "agents.tool.function_tool"}
_WRAPPERS = {
    "agents.FunctionTool", "agents.tool.FunctionTool",
    "google.adk.tools.FunctionTool", "google.adk.tools.function_tool.FunctionTool",
}
_CAPABILITY_ARGUMENTS = {"tools", "sub_agents", "handoffs", "mcp_servers"}
_DIFF = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames"]


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(
        ["git", "--no-replace-objects", *args],
        cwd=repo,
        stderr=subprocess.DEVNULL,
        timeout=60,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"},
    )


def _import_names(tree: ast.Module) -> dict[str, str]:
    """Read direct imports and withhold spellings with competing bindings."""
    possibilities: dict[str, set[str | None]] = defaultdict(set)
    module_imports = set(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                root = imported.name.split(".", 1)[0]
                meaning = imported.name if imported.asname else root
                possibilities[imported.asname or root].add(meaning if node in module_imports else None)
        elif isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name == "*":
                    return {}
                meaning = f"{node.module}.{imported.name}"
                possibilities[imported.asname or imported.name].add(
                    meaning if node in module_imports and node.module and not node.level else None
                )
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            possibilities[node.id].add(None)
        elif isinstance(node, ast.arg):
            possibilities[node.arg].add(None)
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
            possibilities[node.name].add(None)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            possibilities[node.rest].add(None)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            possibilities[node.name].add(None)
    return {
        name: meaning
        for name, options in possibilities.items()
        if len(options) == 1 and (meaning := next(iter(options))) is not None
    }


def _qualified(node: ast.expr, imports: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return imports.get(node.id)
    if isinstance(node, ast.Attribute) and (owner := _qualified(node.value, imports)):
        return f"{owner}.{node.attr}"
    return None


def _markers(source: bytes, changed: set[int]) -> list[dict]:
    tree = ast.parse(source)
    imports = _import_names(tree)

    def first_changed(node: ast.Call | ast.keyword | ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
        return min(changed.intersection(range(node.lineno, (node.end_lineno or node.lineno) + 1)), default=None)

    markers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            constructor = _qualified(node.func, imports)
            if constructor in _CONSTRUCTORS:
                for kw in node.keywords:
                    if kw.arg in _CAPABILITY_ARGUMENTS and (line := first_changed(kw)) is not None:
                        markers.append({
                            "line": line, "kind": "constructor_argument",
                            "constructor": constructor, "argument": kw.arg,
                        })
            elif constructor in _WRAPPERS and (line := first_changed(node)) is not None:
                markers.append({"line": line, "kind": "tool_wrapper", "constructor": constructor})
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if any(
                _qualified(dec.func if isinstance(dec, ast.Call) else dec, imports) in _DECORATORS
                for dec in node.decorator_list
            ):
                start = min(dec.lineno for dec in node.decorator_list)
                lines = changed.intersection(range(start, (node.end_lineno or node.lineno) + 1))
                if lines:
                    markers.append({"line": min(lines), "kind": "decorated_function", "function": node.name})
    return sorted(markers, key=lambda marker: (marker["line"], marker["kind"]))


def _changed_lines(diff: bytes) -> tuple[set[int], set[int]]:
    sides: tuple[set[int], set[int]] = (set(), set())
    for match in _HUNK.finditer(diff):
        old, old_count, new, new_count = match.groups()
        for lines, start, count in ((sides[0], old, old_count), (sides[1], new, new_count)):
            first = int(start)
            lines.update(range(first, first + int(count or b"1")))
    return sides


def inspect_member(member: dict, clones: Path) -> dict:
    slug, repository = member["slug"], member["repository"]
    base, head = member.get("merge_base"), member.get("head")
    markers: list[dict] = []
    unread: list[dict] = []
    row = {"slug": slug, "merge_base": base, "head": head, "markers": markers, "unread": unread}
    if (
        not _REPOSITORY.fullmatch(repository) or repository.split("/")[1] in {".", ".."}
        or not all(isinstance(ref, str) and _SHA.fullmatch(ref) for ref in (base, head))
    ):
        return {**row, "status": "unavailable", "reason": "The repository or pinned refs are invalid."}
    repo = clones / repository.replace("/", "__")
    try:
        paths = _git(repo, *_DIFF, "--name-only", "-z", base, head, "--", "*.py")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {**row, "status": "unavailable", "reason": "The pinned Git diff could not be read."}
    for raw in paths.split(b"\0"):
        if not raw:
            continue
        path = os.fsdecode(raw)
        try:
            patch = _git(repo, *_DIFF, "--text", "--unified=0", base, head, "--", f":(literal){path}")
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            unread.append({"path": path, "reason": "Could not read the diff for this file."})
            continue
        for side, ref, changed in zip(("base", "head"), (base, head), _changed_lines(patch), strict=True):
            if not changed:
                continue
            try:
                source = _git(repo, "show", f"{ref}:{path}")
                found = _markers(source, changed)
            except (
                OSError, ValueError, SyntaxError, RecursionError,
                subprocess.CalledProcessError, subprocess.TimeoutExpired,
            ):
                unread.append({"path": path, "side": side, "reason": "Could not read Python source."})
                continue
            markers.extend({"path": path, "side": side, **marker} for marker in found)
    return {**row, "status": "partial" if unread else "read"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--clones", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    members = json.loads(args.corpus.read_bytes())["members"]
    with ThreadPoolExecutor(max(1, args.jobs)) as workers:
        results = list(workers.map(lambda member: inspect_member(member, args.clones), members))
    print(json.dumps(results, ensure_ascii=True, indent=2))
    return int(any(result["status"] != "read" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
