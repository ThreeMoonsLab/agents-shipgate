"""Trust-model structural invariant: scanner does not execute or import user code.

This test enforces the core trust property of agents-shipgate at the source
level: every Python file under ``src/agents_shipgate/`` (not just
``inputs/``) must parse user files with ``ast.parse``, ``yaml.safe_load``,
or ``json.loads`` only — never through ``exec`` / ``eval`` / ``__import__``
/ ``compile`` builtins, and never via dynamic-import or process-execution
surfaces.

v0.18 (PR #2): the scope widened from adapter sources only to the whole
scanner package. A small allowlist (``ALLOWED_EXCEPTIONS`` below) captures
the four legitimate first-party meta-CLI surfaces with prose rationale —
bootstrap subprocess chaining, git probing in artifacts/triggers, and
self-check ``__import__`` validation. Two contract tests prevent
allowlist rot.

What the scanner catches:

- Bare-name calls to ``exec`` / ``eval`` / ``__import__`` / ``compile``.
- Attribute calls to a fixed forbidden set
  (``importlib.import_module``, ``importlib.util.spec_from_file_location``,
  ``importlib.util.module_from_spec``, ``importlib.machinery.SourceFileLoader``,
  ``runpy.run_path``, ``runpy.run_module``, ``subprocess.{run,call,Popen,
  check_call,check_output}``, ``os.system``, ``os.popen``).
- The full ``os.exec*`` / ``os.spawn*`` / ``os.posix_spawn*`` families
  via prefix matching, so a future Python addition like ``os.execlpe`` is
  caught without an enumeration update.
- Module imports from a forbidden set (``runpy``, ``subprocess``,
  ``importlib``, ``importlib.util``, ``importlib.machinery``, ``builtins``)
  — including ``import X as Y``, ``import X.child``, and
  ``from X.child import ...`` forms. Two explicit exceptions:
  ``importlib.metadata`` (reads installed-package metadata for plugin
  discovery) and ``importlib.resources`` (reads bundled-package files
  inside the agents-shipgate wheel) — neither reads user workspace code.
- **Aliased re-exports.** A two-pass walk first builds alias maps from
  ``import`` and ``from-import`` statements, then resolves attribute chains
  and bare-name calls back to their canonical dotted path before checking
  the forbidden sets. ``import os as oo; oo.system(...)``,
  ``from os import system as sh; sh(...)``, and
  ``from os import execve as runner; runner(...)`` all resolve to
  ``os.system`` / ``os.execve`` and are flagged.
- **Wildcard from-imports** from any tracked module
  (``from os import *``) — they would alias forbidden names into local
  scope and defeat the call-site checks.

What the scanner does NOT catch (acknowledged limitations):

- Flow-sensitive aliasing: ``ev = eval; ev("1+1")`` — no AST signal at the
  call site once the name is reassigned. Requires dataflow.
- Reflective access: ``getattr(os, "system")("ls")``,
  ``globals()["eval"]("...")``, ``__builtins__["eval"](...)``.
- ``import os.path`` followed by ``os.system(...)`` — caught (the top-level
  ``os`` binding is tracked) — but ``import os.path as p; p.path.system(...)``
  is not, because we don't follow attribute paths through aliased submodules.

These bypasses are flow-sensitive and require code review to catch. The lint
is the structural floor; code review is the dataflow ceiling.

Tests live in two layers:

1. **This file** (``test_adapter_static_only.py``) — AST scan of every
   ``src/agents_shipgate/**/*.py`` source (v0.18+; previously scoped to
   ``inputs/*.py`` only). Catches a regression *before* it ships and runs
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
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER_DIR = REPO_ROOT / "src" / "agents_shipgate"


@dataclass(frozen=True)
class Violation:
    """One forbidden-surface hit from ``_scan_source``.

    ``surface`` is the canonical key used by ``ALLOWED_EXCEPTIONS`` and
    contract tests. Shape:

      ``"import:<module>"``      — ``import <module>`` / ``from <module> import …``
      ``"attr_call:<dotted>"``   — call to ``<module>.<func>`` (canonical-resolved)
      ``"name_call:<name>"``     — bare-name call to ``<name>`` (e.g., ``__import__``)
    """

    line: int
    surface: str
    message: str


@dataclass(frozen=True)
class AllowedException:
    """v0.18 (PR #2): per-file allowed forbidden-surface entry, with prose rationale.

    Each entry covers exactly one ``(relative_path, surface)`` pair.
    ``relative_path`` is relative to ``SCANNER_DIR``. Adding an entry is a
    public commitment; the rationale text lands in the audit trail.
    """

    relative_path: str
    surface: str
    rationale: str


# Per-file allowlist of forbidden-surface uses. Each entry MUST correspond to
# a surface that the scanner would actually flag in the named file — the
# ``test_allowlist_entry_matches_real_surface`` contract test fails the moment
# an entry goes stale. Each entry MUST also have a one-sentence prose
# rationale captured in STABILITY.md § "Meta-CLI surfaces (allowlisted, audited)".
ALLOWED_EXCEPTIONS: tuple[AllowedException, ...] = (
    AllowedException(
        relative_path="cli/bootstrap.py",
        surface="import:subprocess",
        rationale=(
            "Meta-CLI: bootstrap shells to the installed agents-shipgate "
            "binary to chain detect → init → scan → apply-patches. Reads "
            "no user code; runs only Shipgate's own CLI."
        ),
    ),
    AllowedException(
        relative_path="cli/bootstrap.py",
        surface="attr_call:subprocess.run",
        rationale="See cli/bootstrap.py import:subprocess rationale.",
    ),
    AllowedException(
        relative_path="cli/discovery/artifacts.py",
        surface="import:subprocess",
        rationale=(
            "Probes the user repo via ``git ls-files`` for file inventory. "
            "Reads git metadata, does not execute user code."
        ),
    ),
    AllowedException(
        relative_path="cli/discovery/artifacts.py",
        surface="attr_call:subprocess.run",
        rationale="See cli/discovery/artifacts.py import:subprocess rationale.",
    ),
    AllowedException(
        relative_path="triggers.py",
        surface="import:subprocess",
        rationale=(
            "Trigger evaluation runs ``git diff`` against the user repo to "
            "determine whether to fire a scan. Reads diff content, does "
            "not execute user code."
        ),
    ),
    AllowedException(
        relative_path="triggers.py",
        surface="attr_call:subprocess.run",
        rationale="See triggers.py import:subprocess rationale.",
    ),
    AllowedException(
        relative_path="cli/self_check.py",
        surface="name_call:__import__",
        rationale=(
            "Self-check validates the installed environment by attempting "
            "an import of a name supplied via CLI flag. Runs only under "
            "`agents-shipgate self-check`, never during scan; targets "
            "installed packages, not user workspace."
        ),
    ),
)


def _violation_allowed(relative_path: str, surface: str) -> bool:
    """v0.18 (PR #2): True iff a (file, surface) pair is allowlisted."""
    return any(
        exc.relative_path == relative_path and exc.surface == surface
        for exc in ALLOWED_EXCEPTIONS
    )

# Bare-name forbidden builtins. Catches direct calls like ``exec("...")``.
FORBIDDEN_NAME_CALLS: frozenset[str] = frozenset(
    {
        "exec",
        "eval",
        "__import__",
        "compile",
    }
)

# Forbidden attribute-chain calls (exact match). Each entry is the
# canonical dotted path AFTER alias resolution. ``os.exec*`` and
# ``os.spawn*`` are intentionally NOT enumerated here — they are caught
# by ``FORBIDDEN_ATTR_CALL_PREFIXES`` below.
FORBIDDEN_ATTR_CALLS_EXACT: frozenset[str] = frozenset(
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
    }
)

# Prefix-matched forbidden families. Covers every ``os.exec*`` /
# ``os.spawn*`` / ``os.posix_spawn*`` variant the os module
# documents — including current names (``execv``, ``execve``,
# ``execvp``, ``execvpe``, ``execl``, ``execle``, ``execlp``,
# ``execlpe``, ``spawnv``, ``spawnve``, ``spawnvp``, ``spawnvpe``,
# ``spawnl``, ``spawnle``, ``spawnlp``, ``spawnlpe``,
# ``posix_spawn``, ``posix_spawnp``) and any future addition under
# these prefixes.
FORBIDDEN_ATTR_CALL_PREFIXES: tuple[str, ...] = (
    "os.exec",
    "os.spawn",
    "os.posix_spawn",
)

# Module imports we forbid outright. ``import X`` / ``import X as Y`` and
# child imports like ``import X.child`` all bind code into the process namespace
# and are rejected.
# ``builtins`` is included because ``builtins.exec`` / ``builtins.eval``
# would otherwise bypass the bare-name checks.
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "runpy",
        "subprocess",
        "importlib",
        "importlib.util",
        "importlib.machinery",
        "builtins",
    }
)

# Exact module import exceptions that would otherwise be caught by a
# forbidden parent module. Keep this small: each exception needs a trust-model
# reason in STABILITY.md.
#
# ``importlib.metadata`` — plugin/adapter discovery via entry_points reads the
#   installed Python environment, not user workspace code.
# ``importlib.resources`` — bundled-package files (e.g. ``fixtures.py``,
#   ``triggers.py``) read content packaged inside the agents-shipgate wheel,
#   not user workspace code.
ALLOWED_FORBIDDEN_MODULE_IMPORTS: frozenset[str] = frozenset(
    {"importlib.metadata", "importlib.resources"}
)

# Modules we allow at the import line (legitimate adapter use) but whose
# specific attributes are still subject to the forbidden-chain checks.
# ``os`` is the canonical example: ``os.path`` / ``os.environ`` /
# ``os.getcwd`` are fine, but ``os.system`` / ``os.exec*`` / ``os.spawn*``
# / ``os.popen`` are out of bounds. We track aliases on these modules so
# ``import os as oo; oo.system(...)`` and
# ``from os import system as sh; sh(...)`` are resolved before checking.
TRACKED_NON_FORBIDDEN_MODULES: frozenset[str] = frozenset({"os"})


def _is_forbidden_chain(chain: str) -> bool:
    """True if a canonical dotted call chain hits the forbidden surface."""
    if chain in FORBIDDEN_ATTR_CALLS_EXACT:
        return True
    return chain.startswith(FORBIDDEN_ATTR_CALL_PREFIXES)


def _is_forbidden_module_import(module: str) -> bool:
    """True if ``module`` imports a forbidden module or one of its children."""
    if module in ALLOWED_FORBIDDEN_MODULE_IMPORTS:
        return False
    return any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for forbidden in FORBIDDEN_MODULES
    )


def _resolve_attribute_chain_all(
    node: ast.Attribute, module_aliases: dict[str, set[str]]
) -> list[str]:
    """Return every canonical dotted chain a ``a.b.c`` Attribute could resolve to.

    Substitutes the root Name through ``module_aliases`` for **all** modules the
    local name was ever bound to in the file. If the root was bound to multiple
    modules (``import os as p; import pathlib as p``), each binding produces one
    candidate chain. The caller flags the call if *any* candidate is forbidden.

    Returns an empty list for chains rooted in something other than a Name
    (e.g. ``func().attr``) — those are out of scope for static lint.

    Conservative union-of-bindings: an order-aware single-pass walk would be
    more precise, but for trust-model lint we want to flag a chain as soon as
    *any* possible binding leads to a forbidden surface. False positives here
    force a code review of suspicious aliasing patterns, which is the right
    failure mode.
    """
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return []
    parts.append(current.id)
    parts.reverse()
    root = parts[0]
    if root not in module_aliases:
        # Root was never bound by an import in this file. Treat the
        # textual root as canonical — catches ``os.system(...)`` written
        # without a prior ``import os`` (broken code that the lint should
        # still call out structurally).
        return [".".join(parts)]
    return [
        ".".join(canonical_root.split(".") + parts[1:])
        for canonical_root in module_aliases[root]
    ]


def _scan_source(source: str, path: Path) -> list[Violation]:
    """Return a list of structured ``Violation`` objects.

    v0.18 (PR #2): returns structured violations instead of preformatted
    strings so callers can route by ``surface`` (e.g., consult
    ``ALLOWED_EXCEPTIONS``). Use ``_format_violation`` for the
    human-readable form when rendering errors.

    Two passes:

    1. Walk every ``Import`` / ``ImportFrom`` and accumulate alias maps as
       **unions of bindings**. ``module_aliases[local]`` is the set of every
       canonical module that local name was ever bound to in the file;
       ``name_aliases[local]`` is the set of every ``(module, attr)`` pair
       a from-import alias could resolve to. This deliberately ignores
       statement order — a later ``import pathlib as os`` does NOT erase
       an earlier ``import os`` binding, because the earlier ``os.system(...)``
       call at lines between still resolves through the original ``os``.
    2. Walk every ``Call`` and resolve names through the alias unions. A
       call is flagged if *any* possible resolution hits the forbidden
       surface. False positives are acceptable for trust-model lint —
       suspicious aliasing should be a code-review trigger.
    """
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - adapter files compile in CI step
        return [
            Violation(
                line=exc.lineno or 0,
                surface="parse_error",
                message=f"{path}:{exc.lineno}: failed to parse: {exc.msg}",
            )
        ]
    rel = path.relative_to(REPO_ROOT)
    violations: list[Violation] = []

    # --- Pass 1: imports ---------------------------------------------------
    # module_aliases: locally-bound name -> {every canonical module path it
    # was ever bound to in this file}.
    #   ``import os``                              -> {"os": {"os"}}
    #   ``import os as op``                        -> {"op": {"os"}}
    #   ``import os; import pathlib as os``        -> {"os": {"os", "pathlib"}}
    #   ``import os.path``                         -> {"os": {"os"}}
    #   ``import os.path as p``                    -> {"p": {"os.path"}}
    module_aliases: dict[str, set[str]] = {}
    # name_aliases: locally-bound name -> {every (canonical_module, attr) it
    # was ever bound to in this file}.
    #   ``from os import system``        -> {"system": {("os", "system")}}
    #   ``from os import system as sh``  -> {"sh":     {("os", "system")}}
    name_aliases: dict[str, set[tuple[str, str]]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_module_import(alias.name):
                    violations.append(
                        Violation(
                            line=node.lineno,
                            surface=f"import:{alias.name}",
                            message=(
                                f"{rel}:{node.lineno}: forbidden import "
                                f"{alias.name!r} (dynamic Python loading surface)"
                            ),
                        )
                    )
                if alias.asname:
                    module_aliases.setdefault(alias.asname, set()).add(alias.name)
                else:
                    # ``import os.path`` binds the top-level ``os`` locally.
                    top = alias.name.split(".")[0]
                    module_aliases.setdefault(top, set()).add(top)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if _is_forbidden_module_import(mod):
                violations.append(
                    Violation(
                        line=node.lineno,
                        surface=f"import:{mod}",
                        message=(
                            f"{rel}:{node.lineno}: forbidden from-import "
                            f"{mod!r} (dynamic Python loading surface)"
                        ),
                    )
                )
                # Skip the per-name pass below — the whole module is forbidden.
                continue
            for alias in node.names:
                if alias.name == "*":
                    if mod in TRACKED_NON_FORBIDDEN_MODULES:
                        violations.append(
                            Violation(
                                line=node.lineno,
                                surface=f"import:{mod}.*",
                                message=(
                                    f"{rel}:{node.lineno}: forbidden wildcard "
                                    f"from-import from {mod!r} "
                                    f"(would alias forbidden names into local scope)"
                                ),
                            )
                        )
                    continue
                # Flag the from-import line itself when the imported
                # attribute resolves to a forbidden surface — gives a
                # clearer error than waiting for the call site.
                if mod in TRACKED_NON_FORBIDDEN_MODULES:
                    canonical = f"{mod}.{alias.name}"
                    if _is_forbidden_chain(canonical):
                        violations.append(
                            Violation(
                                line=node.lineno,
                                surface=f"attr_call:{canonical}",
                                message=(
                                    f"{rel}:{node.lineno}: forbidden from-import "
                                    f"of {canonical!r}"
                                ),
                            )
                        )
                    local = alias.asname or alias.name
                    name_aliases.setdefault(local, set()).add((mod, alias.name))

    # --- Pass 2: call sites ------------------------------------------------
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            # Direct bare-name builtin: e.g. ``exec("...")``.
            if func.id in FORBIDDEN_NAME_CALLS:
                violations.append(
                    Violation(
                        line=node.lineno,
                        surface=f"name_call:{func.id}",
                        message=(
                            f"{rel}:{node.lineno}: forbidden builtin call "
                            f"{func.id!r}"
                        ),
                    )
                )
                continue
            # Aliased ``from X import Y[ as Z]; Z(...)``. Iterate every
            # possible (module, attr) binding for this local name. Flag
            # the call once if any resolution is forbidden.
            if func.id in name_aliases:
                for mod, attr in sorted(name_aliases[func.id]):
                    canonical = f"{mod}.{attr}"
                    if _is_forbidden_chain(canonical):
                        via = (
                            f" (via from-import alias {func.id!r})"
                            if func.id != attr
                            else f" (via from-import of {attr!r})"
                        )
                        violations.append(
                            Violation(
                                line=node.lineno,
                                surface=f"attr_call:{canonical}",
                                message=(
                                    f"{rel}:{node.lineno}: forbidden call "
                                    f"{canonical!r}{via}"
                                ),
                            )
                        )
                        break
        elif isinstance(func, ast.Attribute):
            # Iterate every possible resolution of the attribute chain
            # (the root may have been bound to multiple modules in the
            # file). Flag the call once if any resolution is forbidden.
            for chain in _resolve_attribute_chain_all(func, module_aliases):
                if _is_forbidden_chain(chain):
                    violations.append(
                        Violation(
                            line=node.lineno,
                            surface=f"attr_call:{chain}",
                            message=(
                                f"{rel}:{node.lineno}: forbidden call {chain!r}"
                            ),
                        )
                    )
                    break
    return violations


def _scanner_sources() -> list[Path]:
    """v0.18 (PR #2): every .py file under src/agents_shipgate/ (not just
    ``inputs/``). Skips ``__pycache__`` debris.
    """
    return sorted(
        p for p in SCANNER_DIR.rglob("*.py") if "__pycache__" not in p.parts
    )


@pytest.mark.parametrize(
    "scanner_source",
    _scanner_sources(),
    ids=lambda p: str(p.relative_to(SCANNER_DIR)),
)
def test_scanner_source_contains_no_forbidden_calls_or_imports(
    scanner_source: Path,
) -> None:
    """Each scanner source is statically free of code-execution surfaces,
    EXCEPT for the four legitimate first-party meta-CLI surfaces captured
    in ``ALLOWED_EXCEPTIONS`` (each with prose rationale).

    A regression here means a contributor added a way for the scanner to
    execute or import user code. That breaks the public trust claim in
    README and STABILITY.md and must be rejected. If a legitimate need
    arises (it should not), update STABILITY.md first and add an
    ``ALLOWED_EXCEPTIONS`` entry with prose rationale.

    v0.18 (PR #2) widens this from ``src/agents_shipgate/inputs/`` to the
    entire scanner package.
    """
    rel = str(scanner_source.relative_to(SCANNER_DIR))
    source = scanner_source.read_text(encoding="utf-8")
    violations = _scan_source(source, scanner_source)
    unallowed = [
        v for v in violations if not _violation_allowed(rel, v.surface)
    ]
    assert not unallowed, (
        "Trust-model invariant violation under src/agents_shipgate/:\n  "
        + "\n  ".join(v.message for v in unallowed)
        + "\n\n"
        + "The scanner MUST NOT execute or import user code. Parse with "
        + "ast.parse / yaml.safe_load / json.loads ONLY. See "
        + "STABILITY.md § 'Trust-model invariants' and the companion "
        + "tests/test_fixture_no_import.py live-load tests. If the surface "
        + "is a legitimate first-party meta-CLI operation, add an "
        + "ALLOWED_EXCEPTIONS entry with prose rationale and document it "
        + "in STABILITY.md § 'Meta-CLI surfaces (allowlisted, audited)'."
    )


@pytest.mark.parametrize(
    "snippet,expected_substring",
    [
        # --- Bare-name forbidden builtins ---
        ("exec('print(1)')", "forbidden builtin call 'exec'"),
        ("eval('1+1')", "forbidden builtin call 'eval'"),
        ("__import__('os')", "forbidden builtin call '__import__'"),
        ("compile('x', '<f>', 'exec')", "forbidden builtin call 'compile'"),
        # --- Attribute-chain forbidden calls (exact set) ---
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
        (
            "import os\nos.system('ls')",
            "forbidden call 'os.system'",
        ),
        (
            "import os\nos.popen('ls')",
            "forbidden call 'os.popen'",
        ),
        # --- os.exec* / os.spawn* / os.posix_spawn* prefix families ---
        (
            "import os\nos.execv('/bin/sh', ['sh'])",
            "forbidden call 'os.execv'",
        ),
        (
            "import os\nos.execve('/bin/sh', ['sh'], {})",
            "forbidden call 'os.execve'",
        ),
        (
            "import os\nos.execvpe('sh', ['sh'], {})",
            "forbidden call 'os.execvpe'",
        ),
        (
            "import os\nos.execlpe('sh', 'sh', {})",
            "forbidden call 'os.execlpe'",
        ),
        (
            "import os\nos.spawnv(0, '/bin/sh', ['sh'])",
            "forbidden call 'os.spawnv'",
        ),
        (
            "import os\nos.spawnvp(0, 'sh', ['sh'])",
            "forbidden call 'os.spawnvp'",
        ),
        (
            "import os\nos.spawnvpe(0, 'sh', ['sh'], {})",
            "forbidden call 'os.spawnvpe'",
        ),
        (
            "import os\nos.posix_spawn('/bin/sh', ['sh'], {})",
            "forbidden call 'os.posix_spawn'",
        ),
        (
            "import os\nos.posix_spawnp('sh', ['sh'], {})",
            "forbidden call 'os.posix_spawnp'",
        ),
        # --- Module-alias bypass: ``import X as Y; Y.forbidden(...)`` ---
        (
            "import os as operating_system\noperating_system.system('ls')",
            "forbidden call 'os.system'",
        ),
        (
            "import os as o\no.execv('/bin/sh', ['sh'])",
            "forbidden call 'os.execv'",
        ),
        (
            "import os as o\no.posix_spawn('sh', ['sh'], {})",
            "forbidden call 'os.posix_spawn'",
        ),
        # --- From-import alias bypass ---
        (
            "from os import system\nsystem('ls')",
            "forbidden from-import of 'os.system'",
        ),
        (
            "from os import system as sh\nsh('ls')",
            "forbidden from-import of 'os.system'",
        ),
        (
            "from os import execv as run_binary\nrun_binary('/bin/sh', ['sh'])",
            "forbidden from-import of 'os.execv'",
        ),
        (
            "from os import execve\nexecve('/bin/sh', ['sh'], {})",
            "forbidden from-import of 'os.execve'",
        ),
        # --- Wildcard from-import from tracked module ---
        (
            "from os import *",
            "forbidden wildcard from-import from 'os'",
        ),
        # --- Order-of-import rebind bypass ---
        # The reviewer's case: a later ``import pathlib as os`` must not
        # erase the earlier ``import os`` binding for purposes of the
        # call-site check at the lines between. Union-of-bindings means
        # ``os.system(...)`` resolves through *both* ``os`` and ``pathlib``
        # and ``os.system`` is forbidden regardless of statement order.
        (
            "import os\nos.system('echo hi')\nimport pathlib as os\n",
            "forbidden call 'os.system'",
        ),
        (
            "import os as runner\nrunner.execve('/bin/sh', ['sh'])\n"
            "import pathlib as runner\n",
            "forbidden call 'os.execve'",
        ),
        (
            "from os import system\nsystem('ls')\n"
            "from pathlib import system\n",
            "forbidden from-import of 'os.system'",
        ),
        # Even when the FORBIDDEN binding comes *after* the safe one,
        # the union catches it.
        (
            "import pathlib as os\nos.system('echo hi')\nimport os\n",
            "forbidden call 'os.system'",
        ),
        # --- ``builtins`` module surfaces ---
        ("import builtins", "forbidden import 'builtins'"),
        ("import builtins as b", "forbidden import 'builtins'"),
        ("from builtins import eval", "forbidden from-import 'builtins'"),
        (
            "from builtins import eval as e",
            "forbidden from-import 'builtins'",
        ),
        # --- Existing imports-alone checks for forbidden modules ---
        ("import runpy", "forbidden import 'runpy'"),
        ("from runpy import run_path", "forbidden from-import 'runpy'"),
        ("import subprocess", "forbidden import 'subprocess'"),
        ("import importlib.util", "forbidden import 'importlib.util'"),
        ("import subprocess.run", "forbidden import 'subprocess.run'"),
        ("import runpy.extra", "forbidden import 'runpy.extra'"),
        (
            "from subprocess.foo import bar",
            "forbidden from-import 'subprocess.foo'",
        ),
        (
            "from importlib.util.extra import loader",
            "forbidden from-import 'importlib.util.extra'",
        ),
    ],
    ids=lambda x: x if isinstance(x, str) and len(x) < 40 else "case",
)
def test_lint_scanner_catches_known_violation_shapes(
    snippet: str, expected_substring: str
) -> None:
    """The scanner itself has fingers: each forbidden shape must be detected.

    This is the negative-control test for the lint. Without it, a refactor
    that broke the scanner's NodeVisitor logic could silently make the
    invariant tests pass vacuously. Cases below cover every bypass pattern
    documented in the module docstring (bare names, attribute chains, prefix
    families, module aliases, from-import aliases, wildcard imports, and
    the ``builtins`` surface).
    """
    fake_path = SCANNER_DIR / "__synthetic__.py"
    violations = _scan_source(snippet, fake_path)
    assert violations, (
        f"Scanner failed to flag a forbidden shape:\n  snippet: {snippet!r}\n"
        f"  expected substring: {expected_substring!r}"
    )
    assert any(expected_substring in v.message for v in violations), (
        f"Scanner caught a violation but not the expected one:\n"
        f"  snippet: {snippet!r}\n  violations: {[v.message for v in violations]!r}\n"
        f"  expected substring: {expected_substring!r}"
    )


def test_lint_scanner_does_not_false_positive_on_safe_shapes() -> None:
    """Common safe patterns must not trip the scanner.

    Documents the boundary so a future "be stricter" patch knows what to
    avoid breaking. Anything an adapter under inputs/ legitimately does
    with ``re``, ``ast``, ``os.path`` / ``os.environ`` / ``os.getcwd``,
    ``yaml``, or ``json`` must remain green.
    """
    safe = (
        # re.compile is a method call, not the builtin.
        "import re\nPATTERN = re.compile(r'foo')\n"
        # ast.parse is the canonical safe parsing path.
        "import ast\ntree = ast.parse('1+1')\n"
        # Legitimate os surface used by real adapters.
        "import os\nROOT = os.environ.get('FOO', '')\n"
        "import os\nABS = os.path.join('a', 'b')\n"
        "import os.path\nABS = os.path.abspath('x')\n"
        "from os import getcwd\ncwd = getcwd()\n"
        "from os import environ\nval = environ.get('FOO')\n"
        # yaml.safe_load + json.loads are the declared declarative paths.
        "import yaml\nimport json\nx = yaml.safe_load('a: 1')\ny = json.loads('{}')\n"
        # importlib.metadata is explicitly allowed for installed-package metadata.
        "import importlib.metadata\n"
        "from importlib.metadata import version\npkg_version = version('agents-shipgate')\n"
    )
    fake_path = SCANNER_DIR / "__synthetic_safe__.py"
    violations = _scan_source(safe, fake_path)
    assert not violations, (
        "Safe parsing patterns must not be flagged. Unexpected violations:\n  "
        + "\n  ".join(sorted(v.message for v in violations))
    )


# --- v0.18 (PR #2): allowlist contract tests --------------------------------


@pytest.mark.parametrize(
    "exc",
    ALLOWED_EXCEPTIONS,
    ids=lambda e: f"{e.relative_path}:{e.surface}",
)
def test_allowlist_entry_matches_real_surface(exc: AllowedException) -> None:
    """v0.18 (PR #2) stale-allowlist guard.

    Every ``ALLOWED_EXCEPTIONS`` entry MUST correspond to a surface
    that the scanner would actually flag in the named file. If the
    file is later refactored to remove the legitimate meta-CLI surface
    (e.g., self-check stops calling ``__import__``), this test fails
    and forces the contributor to remove the now-unused allowlist
    entry — preventing rot.
    """
    path = SCANNER_DIR / exc.relative_path
    assert path.exists(), (
        f"allowlisted file does not exist: {exc.relative_path}"
    )
    source = path.read_text(encoding="utf-8")
    violations = _scan_source(source, path)
    surfaces = {v.surface for v in violations}
    assert exc.surface in surfaces, (
        f"allowlisted surface {exc.surface!r} not present in "
        f"{exc.relative_path!r}; entry is stale, remove or refactor. "
        f"Observed surfaces in this file: {sorted(surfaces)!r}"
    )


def test_no_unallowlisted_forbidden_surface_in_scanner() -> None:
    """v0.18 (PR #2): the whole-scanner sweep with allowlist applied
    produces zero violations. This is the runtime gate PR #2 hardens.

    The parametrized ``test_scanner_source_contains_no_forbidden_calls_or_imports``
    above catches per-file regressions. This non-parametrized form gives
    a single, definitive PASS/FAIL signal at the end of the test session.
    A failure message lists every ``(relative_path, surface)`` pair the
    contributor must address — either by refactoring the offending file
    or by adding an ``ALLOWED_EXCEPTIONS`` entry with prose rationale.
    """
    unallowed: list[tuple[str, str]] = []
    for path in _scanner_sources():
        rel = str(path.relative_to(SCANNER_DIR))
        for violation in _scan_source(path.read_text(encoding="utf-8"), path):
            if not _violation_allowed(rel, violation.surface):
                unallowed.append((rel, violation.surface))
    assert unallowed == [], (
        "Scanner has forbidden surfaces with no allowlist entry. "
        "Add an ALLOWED_EXCEPTIONS entry with prose rationale, or "
        "refactor the surface out. Unallowlisted: "
        + ", ".join(f"{path}::{surface}" for path, surface in unallowed)
    )


def test_scanner_sources_covers_known_files() -> None:
    """v0.18 (PR #2): non-vacuity guard.

    The pre-v0.18 ``test_invariant_lint_covers_every_adapter_module``
    pinned a hardcoded 18-file set; that approach didn't scale to the
    widened whole-scanner scope (~80 files). This replacement asserts a
    small set of always-present anchor files AND a minimum count,
    catching both silent ``rglob`` breakage and accidental
    scope-narrowing (e.g., a typo making ``SCANNER_DIR`` point at an
    empty directory would make every other test in this file vacuously
    pass).
    """
    sources = _scanner_sources()
    relative_paths = {str(p.relative_to(SCANNER_DIR)) for p in sources}
    required = {
        "cli/scan.py",
        "inputs/protocol.py",
        "inputs/mcp.py",
        "checks/registry.py",
        "checks/plugin_validation.py",
        "core/models.py",
        "core/severity_overrides.py",
        "core/findings.py",
    }
    missing = required - relative_paths
    assert not missing, (
        f"_scanner_sources() missing known files: {sorted(missing)}. "
        f"Check SCANNER_DIR ({SCANNER_DIR}) and the rglob predicate."
    )
    assert len(sources) >= 50, (
        f"_scanner_sources() returned only {len(sources)} files; "
        f"expected >= 50 (whole-scanner coverage). Investigate whether "
        f"SCANNER_DIR was scoped down."
    )
