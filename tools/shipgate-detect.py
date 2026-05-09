#!/usr/bin/env python3
"""Zero-install Agents Shipgate detector.

Replicates the structural output of ``agents-shipgate detect --json`` for
the most common decision a coding agent needs to make — *is this an agent
project, and which framework(s)?* — without requiring a local install of
the ``agents-shipgate`` package. Stdlib-only, one file.

Usage::

    python3 tools/shipgate-detect.py [--workspace PATH] [--json]
    curl -sSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/tools/shipgate-detect.py \\
        | python3 - --workspace . --json

Output mirrors :class:`agents_shipgate.cli.discovery.signals.DetectResult`
plus a ``script_version`` field. It is a **structural subset** of the
canonical ``agents-shipgate detect --json`` output, NOT a drop-in
replacement: the CLI also emits ``diagnostics[]`` and ``next_actions[]``
arrays (the diagnostic engine), which are intentionally out of scope for
the zero-install path. The contract test pins the verdict — ``is_agent_project``,
fired frameworks, suggested sources — against the CLI on every sample in
``samples/``, so the two cannot drift on the load-bearing fields.

Intentional simplifications vs. the canonical CLI:

- No ``diagnostics[]`` / ``next_actions[]`` (the diagnostic engine is
  not in scope for stdlib-only / zero-install).
- No git-ls-files fast path; ``os.walk`` only.
- Descriptive (not byte-identical) ``evidence`` strings.
- Absolute scores may differ by ±0.5 in edge cases.

The verdict and detected framework set match.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "0.1.0"

# Framework signal vocabulary (mirror of cli/discovery/signals.py).
LANGCHAIN_IMPORTS = {
    "langchain", "langchain.agents", "langchain.tools", "langchain_core",
    "langchain_core.tools", "langchain_core.agents", "langgraph",
    "langgraph.graph", "langgraph.prebuilt",
}
LANGCHAIN_DECORATOR_MODULES = {"langchain.tools", "langchain_core.tools"}
LANGCHAIN_AGENT_CALLS = {"create_agent", "create_react_agent", "AgentExecutor"}
CREWAI_IMPORTS = {"crewai", "crewai.tools", "crewai_tools"}
CREWAI_DECORATOR_MODULES = {"crewai.tools"}
CREWAI_CLASSES = {"Agent", "Crew", "Task"}
GOOGLE_ADK_CLASSES = {
    "Agent", "LlmAgent", "FunctionTool", "LongRunningFunctionTool",
    "OpenAPIToolset", "McpToolset", "MCPToolset",
}
ANTHROPIC_IMPORTS = {"anthropic"}
OPENAI_AGENTS_SDK_IMPORTS = {"agents", "openai_agents"}
OPENAI_AGENTS_SDK_DECORATORS = {
    "function_tool", "agents.function_tool", "openai_agents.function_tool",
}
PACKAGE_HINTS: dict[str, tuple[str, ...]] = {
    "langchain": ("langchain", "langchain-core", "langchain_core", "langgraph"),
    "crewai": ("crewai", "crewai-tools"),
    "google_adk": ("google-adk", "google_adk", "google-genai"),
    "anthropic": ("anthropic",),
    "openai_agents_sdk": ("openai-agents", "openai_agents", "agents"),
    "openai_api": (),
}
FRAMEWORKS = (
    "langchain", "crewai", "google_adk", "anthropic",
    "openai_agents_sdk", "openai_api",
)
OPENAPI_PATTERNS = (
    "*openapi*.yaml", "*openapi*.yml", "*openapi*.json",
    "*swagger*.yaml", "*swagger*.yml", "*swagger*.json",
)
MCP_PATTERNS = ("*mcp*.json", ".agents-shipgate/*.json")
ANTHROPIC_TOOL_PATTERNS = ("tools/*anthropic*tools*.json", "tools/anthropic-tools.json")
ANTHROPIC_POLICY_PATTERNS = ("policies/*anthropic*.yaml", "policies/anthropic-policy.yaml")
OPENAI_API_PATTERNS = (
    ("openai-config.json", "openai-config marker"),
    ("tools/*openai*tools*.json", "openai tool file"),
    ("policies/*openai*.yaml", "openai-api policy file"),
    ("policies/*api*.yaml", "openai-api policy file"),
    ("tests/*openai*cases*.json", "openai-api test cases"),
    ("tests/*api*cases*.json", "openai-api test cases"),
)
CONVENTIONAL_DIRS = ("prompts", "tools", ".agents-shipgate")
SKIP_DIRS = {
    ".agents-private", ".cache", ".claude", ".direnv", ".git", ".hg",
    ".nox", ".svn", ".mypy_cache", ".next", ".pnpm-store", ".pytest_cache",
    ".ruff_cache", ".turbo", ".tox", ".venv", "__pycache__",
    "agents-shipgate-reports", "build", "dist", "env", "node_modules",
    "target", "venv",
}
PYPROJECT_NAME_RE = re.compile(r'^\s*name\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
REQ_TOKEN_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)", re.MULTILINE)


def _walk_files(workspace: Path, max_files: int = 5000) -> list[Path]:
    out: list[Path] = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".venv")
        ]
        for fn in files:
            out.append(Path(root) / fn)
            if len(out) >= max_files:
                return sorted(out)
    return sorted(out)


def _rel(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _matches(rel: str, basename: str, pattern: str) -> bool:
    if fnmatch.fnmatch(rel, pattern):
        return True
    if "/" not in pattern:
        return fnmatch.fnmatch(basename, pattern)
    return fnmatch.fnmatch(rel, f"*/{pattern}")


def _glob(workspace: Path, files: list[Path], patterns: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for p in files:
            rel = _rel(p, workspace)
            if rel in seen or not _matches(rel, p.name, pattern):
                continue
            seen.add(rel)
            found.append(rel)
    return sorted(found)


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return _name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _parse_py(path: Path) -> dict[str, Any] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None
    imports, decos, ctors, names = set(), set(), set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
            for a in node.names:
                imports.add(f"{node.module}.{a.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                n = _name(d)
                if n:
                    decos.add(n)
        elif isinstance(node, ast.Call):
            ctor = _name(node.func)
            if ctor:
                ctors.add(ctor)
                for kw in node.keywords:
                    if (kw.arg == "name" and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)
                            and kw.value.value.strip()
                            and ctor.split(".")[-1] in {"Agent", "LlmAgent"}):
                        names.append(kw.value.value.strip())
    return {"imports": imports, "decorators": decos, "constructors": ctors, "names": names}


def _package_tokens(workspace: Path) -> list[str]:
    tokens: list[str] = []
    for fname in ("pyproject.toml", "requirements.txt"):
        path = workspace / fname
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if fname == "pyproject.toml":
            for line in content.splitlines():
                s = line.strip().strip(",").strip("'\"")
                for sep in ("==", ">=", "<=", "~=", ">", "<"):
                    if sep in s:
                        s = s.split(sep, 1)[0]
                        break
                s = s.strip().strip('"\'')
                if s and re.fullmatch(r"[A-Za-z0-9_.\-]+", s):
                    tokens.append(s)
        else:
            tokens.extend(m.group(1) for m in REQ_TOKEN_RE.finditer(content))
    return tokens


def _add(scores: dict[str, dict[str, Any]], fw: str, pts: float, cls: str,
         evidence: str, candidate: str | None = None) -> None:
    s = scores[fw]
    s["score"] += pts
    if cls == "strong":
        s["has_strong"] = True
    s["evidence"].append(evidence)
    if candidate and candidate not in s["candidate_files"]:
        s["candidate_files"].append(candidate)


def _confidence(score: float) -> str:
    return "high" if score >= 4.0 else "medium" if score >= 2.5 else "low"


def detect(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    files = _walk_files(workspace)
    py_files = [p for p in files if p.suffix == ".py"][:1000]
    py_facts = [(p, f) for p in py_files if (f := _parse_py(p)) is not None]

    scores = {fw: {"score": 0.0, "has_strong": False, "evidence": [], "candidate_files": []}
              for fw in FRAMEWORKS}

    for path, f in py_facts:
        rel = _rel(path, workspace)
        imp, dec, ctr = f["imports"], f["decorators"], f["constructors"]
        if imp & LANGCHAIN_IMPORTS:
            _add(scores, "langchain", 2.0, "strong", f"{rel}: langchain import", rel)
        if "tool" in dec and any(m in imp for m in LANGCHAIN_DECORATOR_MODULES):
            _add(scores, "langchain", 2.0, "strong", f"{rel}: @tool from langchain", rel)
        if ctr & LANGCHAIN_AGENT_CALLS:
            _add(scores, "langchain", 2.0, "strong", f"{rel}: langchain agent call", rel)
        if imp & CREWAI_IMPORTS:
            _add(scores, "crewai", 2.0, "strong", f"{rel}: crewai import", rel)
        if "tool" in dec and any(m in imp for m in CREWAI_DECORATOR_MODULES):
            _add(scores, "crewai", 2.0, "strong", f"{rel}: @tool from crewai", rel)
        if any(c.split(".")[-1] in CREWAI_CLASSES for c in ctr) and (imp & CREWAI_IMPORTS):
            _add(scores, "crewai", 2.0, "strong", f"{rel}: crewai class call", rel)
        if any(m.startswith("google.adk") for m in imp):
            _add(scores, "google_adk", 2.0, "strong", f"{rel}: google.adk import", rel)
            if any(c.split(".")[-1] in GOOGLE_ADK_CLASSES for c in ctr):
                _add(scores, "google_adk", 2.0, "strong",
                     f"{rel}: google.adk class call", rel)
        if imp & ANTHROPIC_IMPORTS or any(m.startswith("anthropic.") for m in imp):
            _add(scores, "anthropic", 2.0, "strong", f"{rel}: anthropic import", rel)
        if imp & OPENAI_AGENTS_SDK_IMPORTS:
            _add(scores, "openai_agents_sdk", 2.0, "strong",
                 f"{rel}: openai-agents import", rel)
        if dec & OPENAI_AGENTS_SDK_DECORATORS:
            _add(scores, "openai_agents_sdk", 2.0, "strong",
                 f"{rel}: @function_tool decorator", rel)

    for token in _package_tokens(workspace):
        for fw, hints in PACKAGE_HINTS.items():
            if token.lower() in {h.lower() for h in hints}:
                _add(scores, fw, 1.0, "medium", f"dependency declared: {token}")

    for p in _glob(workspace, files, ANTHROPIC_TOOL_PATTERNS):
        _add(scores, "anthropic", 2.0, "strong", f"anthropic tool file: {p}")
    for p in _glob(workspace, files, ANTHROPIC_POLICY_PATTERNS):
        _add(scores, "anthropic", 2.0, "strong", f"anthropic policy file: {p}")
    for pattern, label in OPENAI_API_PATTERNS:
        for p in _glob(workspace, files, (pattern,)):
            _add(scores, "openai_api", 2.0, "strong", f"{label}: {p}")

    present_dirs = [d for d in CONVENTIONAL_DIRS if (workspace / d).is_dir()]
    for fw in FRAMEWORKS:
        for d in present_dirs:
            _add(scores, fw, 0.5, "weak", f"conventional dir: {d}/")

    detections: list[dict[str, Any]] = [
        {
            "type": fw,
            "score": round(st["score"], 2),
            "confidence": _confidence(st["score"]),
            "evidence": st["evidence"],
            "candidate_files": st["candidate_files"],
        }
        for fw, st in scores.items()
        if st["score"] >= 2.0 and st["has_strong"]
    ]
    detections.sort(key=lambda d: (-d["score"], d["type"]))

    name_candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for _p, f in py_facts:
        for lit in f["names"]:
            if lit not in seen:
                name_candidates.append({"value": lit, "source": "Agent_name_literal"})
                seen.add(lit)
    if workspace.name not in seen:
        name_candidates.append({"value": workspace.name, "source": "workspace_dir"})

    project_names: list[dict[str, str]] = []
    pyproject = workspace / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        m = PYPROJECT_NAME_RE.search(text)
        if m:
            project_names.append({"value": m.group(1).strip(), "source": "pyproject"})
    project_names.append({"value": workspace.name, "source": "workspace_dir"})

    suggested: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for kind, patterns in (("openapi", OPENAPI_PATTERNS), ("mcp", MCP_PATTERNS)):
        for p in _glob(workspace, files, patterns):
            if p not in seen_paths:
                seen_paths.add(p)
                suggested.append({"type": kind, "path": p})

    is_agent = bool(detections)
    return {
        "is_agent_project": is_agent,
        "frameworks": detections,
        "agent_name_candidates": name_candidates,
        "project_name_candidates": project_names,
        "suggested_sources": suggested,
        "next_action": (
            f"agents-shipgate init --workspace {workspace}"
            if is_agent
            else "Workspace does not appear to be an agent project. No action."
        ),
        "workspace_signals": {
            "python_file_count": len(py_facts),
            "has_pyproject_or_requirements": (
                (workspace / "pyproject.toml").is_file()
                or (workspace / "requirements.txt").is_file()
            ),
            "has_prompts_dir": "prompts" in present_dirs,
            "has_tools_dir": "tools" in present_dirs,
            "conventional_dirs": present_dirs,
        },
        "script_version": SCRIPT_VERSION,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="shipgate-detect",
        description="Zero-install Agents Shipgate detector.",
    )
    parser.add_argument("--workspace", default=".", type=Path)
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON. Default: human-readable summary.")
    args = parser.parse_args(argv)
    result = detect(args.workspace)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if not result["is_agent_project"]:
        print("Workspace does not appear to be an agent project.")
        if result["suggested_sources"]:
            print("Suggested sources (artifact-only):")
            for s in result["suggested_sources"]:
                print(f"- {s['type']}: {s['path']}")
        return 0
    print("Detected agent project. Frameworks:")
    for fw in result["frameworks"]:
        print(f"- {fw['type']} (score={fw['score']}, confidence={fw['confidence']})")
    if result["suggested_sources"]:
        print("\nSuggested sources:")
        for s in result["suggested_sources"]:
            print(f"- {s['type']}: {s['path']}")
    print(f"\nNext: pipx install agents-shipgate && {result['next_action']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
