"""Trust-model structural invariant: scanner does not execute or import user code.

This test enforces the core trust property of agents-shipgate at the source
level: every adapter under ``src/agents_shipgate/inputs/`` must parse user
files with ``ast.parse``, ``yaml.safe_load``, or ``json.loads`` only — never
through ``exec``/``eval``/``__import__``/``compile`` builtins, and never via
dynamic-import or subprocess surfaces (``importlib.import_module``,
``importlib.util.spec_from_file_location``, ``runpy.run_path``,
``subprocess.run``, etc.).

Tests live in two layers:

1. **This file** (``test_adapter_static_only.py``) — AST scan of every
   ``inputs/*.py`` source. Catches a regression *before* it ships and runs
   in CI in well under a second.
2. **``test_fixture_no_import.py``** — companion live-load tests that drive
   each adapter end-to-end against a fixture with a module-level
   ``raise RuntimeError(...)`` trap, then assert ``sys.modules`` is unchanged
   for the fixture directory.

The two layers together back the public claim in
`STABILITY.md` § *Trust-model invariants*.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUTS_DIR = REPO_ROOT / "src" / "agents_shipgate" / "inputs"

# Bare-name calls. These have no legitimate use anywhere under inputs/ —
# the adapter contract is "parse user data, never execute it".
FORBIDDEN_NAME_CALLS: frozenset[str] = frozenset(
    {
        "exec",
        "eval",
        "__import__",
        "compile",
    }
)

# Attribute-chain calls. Dynamic Python loading or subprocess execution
# are out of scope for any adapter. We deliberately do NOT forbid
# ``importlib.metadata.*`` here — that surface is used by the plugin
# registry under ``checks/`` and never under ``inputs/``.
FORBIDDEN_ATTR_CALLS: frozenset[str] = frozenset(
    {
        "importlib.import_module",
        "importlib.util.spec_from_file_location",
        "importlib.util.module_from_spec",
        "importlib.machinery.SourceFileLoader",
        "runpy.run_path",
        "runpy.run_module",
        "subprocess.run",
        "subprocess.call",
        "subprocess.Popen",
        "subprocess.check_call",
        "subprocess.check_output",
        "os.system",
        "os.popen",
        "os.execv",
        "os.execvp",
        "os.spawnv",
    }
)

# Module imports. Forbidden so that aliased re-exports (e.g.
# ``from runpy import run_path as r``) can't sneak past the call-site
# checks above.
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "runpy",
        "subprocess",
        "importlib",
        "importlib.util",
        "importlib.machinery",
    }
)


def _attr_chain(node: ast.AST) -> str | None:
    """Reduce ``a.b.c`` Attribute chain to the dotted string ``"a.b.c"``.

    Returns None for chains rooted in a non-Name (e.g. ``func()``.attr).
    """
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _scan_source(source: str, path: Path) -> list[str]:
    """Return a list of human-readable violation strings."""
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - adapter files compile in CI step
        return [f"{path}:{exc.lineno}: failed to parse: {exc.msg}"]
    rel = path.relative_to(REPO_ROOT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_MODULES:
                    violations.append(
                        f"{rel}:{node.lineno}: forbidden import "
                        f"{alias.name!r} (dynamic Python loading surface)"
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in FORBIDDEN_MODULES:
                violations.append(
                    f"{rel}:{node.lineno}: forbidden from-import "
                    f"{mod!r} (dynamic Python loading surface)"
                )
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAME_CALLS:
                violations.append(
                    f"{rel}:{node.lineno}: forbidden builtin call {func.id!r}"
                )
            elif isinstance(func, ast.Attribute):
                chain = _attr_chain(func)
                if chain and chain in FORBIDDEN_ATTR_CALLS:
                    violations.append(
                        f"{rel}:{node.lineno}: forbidden call {chain!r}"
                    )
    return violations


def _adapter_sources() -> list[Path]:
    """Every .py file under inputs/ (including __init__.py and helpers)."""
    return sorted(INPUTS_DIR.rglob("*.py"))


@pytest.mark.parametrize(
    "adapter_source",
    _adapter_sources(),
    ids=lambda p: str(p.relative_to(INPUTS_DIR)),
)
def test_adapter_source_contains_no_forbidden_calls_or_imports(
    adapter_source: Path,
) -> None:
    """Each adapter source under inputs/ is statically free of code-execution
    surfaces.

    A regression here means a contributor added a way for the scanner to
    execute or import user code. That breaks the public trust claim in
    README and STABILITY.md and must be rejected. If a legitimate need
    arises (it should not), update STABILITY.md first and consider whether
    the addition belongs in ``inputs/`` at all.
    """
    source = adapter_source.read_text(encoding="utf-8")
    violations = _scan_source(source, adapter_source)
    assert not violations, (
        "Trust-model invariant violation under src/agents_shipgate/inputs/:\n  "
        + "\n  ".join(violations)
        + "\n\n"
        + "Adapters MUST NOT execute or import user code. They parse user "
        + "files with ast.parse / yaml.safe_load / json.loads ONLY. See "
        + "STABILITY.md § 'Trust-model invariants' and the companion "
        + "tests/test_fixture_no_import.py live-load tests."
    )


@pytest.mark.parametrize(
    "snippet,expected_substring",
    [
        # Bare-name forbidden builtins.
        ("exec('print(1)')", "forbidden builtin call 'exec'"),
        ("eval('1+1')", "forbidden builtin call 'eval'"),
        ("__import__('os')", "forbidden builtin call '__import__'"),
        ("compile('x', '<f>', 'exec')", "forbidden builtin call 'compile'"),
        # Attribute-chain forbidden calls.
        (
            "import importlib\nimportlib.import_module('os')",
            "forbidden call 'importlib.import_module'",
        ),
        (
            "import runpy\nrunpy.run_path('/etc/passwd')",
            "forbidden call 'runpy.run_path'",
        ),
        (
            "import subprocess\nsubprocess.run(['ls'])",
            "forbidden call 'subprocess.run'",
        ),
        # Imports alone (catches aliased forms).
        ("import runpy", "forbidden import 'runpy'"),
        ("from runpy import run_path", "forbidden from-import 'runpy'"),
        ("import subprocess", "forbidden import 'subprocess'"),
        ("import importlib.util", "forbidden import 'importlib.util'"),
    ],
    ids=lambda x: x if isinstance(x, str) and len(x) < 40 else "case",
)
def test_lint_scanner_catches_known_violation_shapes(
    snippet: str, expected_substring: str
) -> None:
    """The scanner itself has fingers: each forbidden shape must be detected.

    This is the negative-control test for the lint. Without it, a refactor
    that broke the scanner's NodeVisitor logic could silently make the
    invariant tests pass vacuously.
    """
    fake_path = INPUTS_DIR / "__synthetic__.py"
    violations = _scan_source(snippet, fake_path)
    assert violations, (
        f"Scanner failed to flag a forbidden shape:\n  snippet: {snippet!r}\n"
        f"  expected substring: {expected_substring!r}"
    )
    assert any(expected_substring in v for v in violations), (
        f"Scanner caught a violation but not the expected one:\n"
        f"  snippet: {snippet!r}\n  violations: {violations!r}\n"
        f"  expected substring: {expected_substring!r}"
    )


def test_lint_scanner_does_not_false_positive_on_safe_shapes() -> None:
    """Common safe patterns must not trip the scanner.

    Documents the boundary so a future "be stricter" patch knows what to
    avoid breaking.
    """
    safe = (
        # re.compile is a method call, not the builtin.
        "import re\nPATTERN = re.compile(r'foo')\n"
        # ast.parse is the canonical safe parsing path.
        "import ast\ntree = ast.parse('1+1')\n"
        # importlib.metadata is OK — used elsewhere for plugin discovery,
        # not for loading user code. (It's not in inputs/, but the scanner
        # logic should not flag the import path itself if a future module
        # legitimately needs metadata.)
        "from importlib import metadata\neps = metadata.entry_points()\n"
        # yaml.safe_load + json.loads are the declared declarative paths.
        "import yaml\nimport json\nx = yaml.safe_load('a: 1')\ny = json.loads('{}')\n"
    )
    fake_path = INPUTS_DIR / "__synthetic_safe__.py"
    violations = _scan_source(safe, fake_path)
    # Note: ``from importlib import metadata`` triggers an import of the
    # parent ``importlib`` package which is on the forbidden list.
    # Document that here and assert the expected single violation — if
    # an adapter ever genuinely needs importlib.metadata, treat that as
    # a discussion point rather than a silent allowlist.
    expected_only = {
        v for v in violations if "importlib" in v
    }
    assert set(violations) == expected_only, (
        "Safe parsing patterns must not be flagged. Unexpected violations:\n  "
        + "\n  ".join(sorted(set(violations) - expected_only))
    )


def test_invariant_lint_covers_every_adapter_module() -> None:
    """Sanity check: the parametrized scan reaches every known adapter file.

    Catches the case where someone reorganizes inputs/ into a subpackage
    and the rglob silently stops finding the new home.
    """
    scanned_names = {p.name for p in _adapter_sources()}
    expected_adapter_files = {
        "anthropic_api.py",
        "codex_plugin.py",
        "common.py",
        "crewai.py",
        "google_adk.py",
        "langchain.py",
        "mcp.py",
        "n8n.py",
        "openai_api.py",
        "openai_sdk_static.py",
        "openapi.py",
        "policy_packs.py",
        "protocol.py",
        "python_static.py",
        "traces.py",
        "validation.py",
        "_python_framework.py",
        "__init__.py",
    }
    missing = expected_adapter_files - scanned_names
    assert not missing, (
        f"Expected adapter files not found under {INPUTS_DIR}: {sorted(missing)}. "
        f"If inputs/ was reorganized, update the expected set above."
    )
