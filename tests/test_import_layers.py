"""Import layers in ``src/agents_shipgate`` (#1053).

Layering here used to be convention only. This file makes two parts of it
checked:

1. **Layer rules.** A package may not import a layer above it:

   - ``inputs`` must not import ``cli``;
   - ``core`` must not import ``ci`` or ``cli``.

   The imports that break a rule today are listed in
   ``ALLOWED_LAYER_VIOLATIONS``, each with the follow-up that removes it.
   That list only shrinks: an import that breaks a rule and is not listed
   fails, and so does a listed import that no longer exists, so the entry is
   deleted in the change that fixes it.

2. **A ratchet on deferred first-party imports.** An import of an
   ``agents_shipgate`` module inside a function (or lambda) runs only when
   the function does, so it can hide an import cycle that a module-level
   import would have turned into an ``ImportError`` at load time. Their
   number, one per distinct first-party target in an import statement or
   literal dynamic-import call, is
   recorded in ``FUNCTION_LOCAL_FIRST_PARTY_IMPORTS``. A count above it
   fails; a count below it fails too, until the number is lowered to match,
   so the ratchet tightens as imports move out and cannot drift back up
   unseen. Raising it is a deliberate edit to this file, which review sees;
   it suits a deferral made on purpose, such as the lazily loaded commands
   in ``cli/main.py``. Imports of the standard library and of third parties
   are not counted: the optional extras (``mcp``, ``weasyprint``) and
   platform-only modules (``pwd``, ``resource``) have to be imported lazily,
   and none of them can close a cycle through this package.

What counts as importing a module, everywhere in a file (module level, in a
function or class, under ``if TYPE_CHECKING:``, inside ``try``):

- ``import a.b.c`` and ``import a.b.c as d`` import ``a.b.c``;
- ``from a.b import c`` imports ``a.b``, except that ``from agents_shipgate
  import cli`` imports ``agents_shipgate.cli``, since the root package is no
  layer;
- a relative import is resolved against the importing module's package;
- ``importlib.import_module("...")`` and ``__import__("...")`` with a string
  literal import that module; ``import_module`` resolves a relative literal
  against a literal ``package`` or ``__package__``.

Not seen: a module name that is computed rather than written, an importer
called under another name (``load = importlib.import_module``), lookups
through ``sys.modules`` or ``getattr``, and a layer reached through an
intermediate module (``core`` importing a neutral module that imports
``cli``). The rules check direct edges, not their transitive closure.
``tests/test_adapter_static_only.py`` refuses ``importlib`` and ``__import__``
in ``src`` outside its own allowlist, so the dynamic forms are a second line.
"""

from __future__ import annotations

import ast
import re
import textwrap
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
PACKAGE = "agents_shipgate"

#: The scope of code that runs when the module is imported.
MODULE_SCOPE = "<module>"

#: Layer (first package under ``agents_shipgate``) -> layers it must not import.
LAYER_RULES: dict[str, frozenset[str]] = {
    "inputs": frozenset({"cli"}),
    "core": frozenset({"ci", "cli"}),
}

#: Function-local imports of ``agents_shipgate`` modules in ``src``. Lower it
#: when the count falls; see the module docstring before raising it.
FUNCTION_LOCAL_FIRST_PARTY_IMPORTS = 325


@dataclass(frozen=True)
class ImportSite:
    """One import statement or literal dynamic-import call.

    ``path`` is relative to ``src/`` (``agents_shipgate/core/x.py``).
    ``scope`` is the qualified name of the ``def`` or ``class`` whose body
    holds the import, spelled as ``__qualname__`` spells it, or
    ``MODULE_SCOPE``. ``function_local`` is true when a function (or lambda)
    body holds it, at any depth, so it runs when that function is called
    rather than when the module is imported. ``targets`` are the absolute
    module names it imports.
    """

    path: str
    line: int
    scope: str
    function_local: bool
    targets: tuple[str, ...]


@dataclass(frozen=True)
class LayerViolation:
    path: str
    line: int
    scope: str
    target: str


@dataclass(frozen=True)
class AllowedLayerViolation:
    """Imports of ``target`` in ``scope`` of ``path`` that break a layer rule.

    ``count`` is how many there are in that scope, so a second one fails as
    a new violation and a removed one fails as a stale entry. ``reason`` says
    why it is still there and names the issue that removes it. Line numbers
    are not part of the key, so code moving around the import is no change.
    """

    path: str
    scope: str
    target: str
    count: int
    reason: str


ALLOWED_LAYER_VIOLATIONS: tuple[AllowedLayerViolation, ...] = (
    AllowedLayerViolation(
        path="agents_shipgate/core/findings/agent_summary.py",
        scope=MODULE_SCOPE,
        target="agents_shipgate.ci.release_decision",
        count=1,
        reason=(
            "The decision engine lives in ci/. #1053 moves release_decision "
            "and exit_policy to core/decision, behind re-export shims."
        ),
    ),
    AllowedLayerViolation(
        path="agents_shipgate/core/findings/report_builder.py",
        scope=MODULE_SCOPE,
        target="agents_shipgate.ci.release_decision",
        count=1,
        reason=(
            "build_report calls build_release_decision, which lives in ci/. "
            "#1053 moves it to core/decision."
        ),
    ),
    AllowedLayerViolation(
        path="agents_shipgate/core/human_review_decision.py",
        scope="_current_review",
        target="agents_shipgate.cli.current_workspace",
        count=1,
        reason=(
            "Reuses the live-workspace observer shared with `agent control` "
            "rather than a weaker freshness test; the observer belongs in "
            "core (#1053)."
        ),
    ),
    AllowedLayerViolation(
        path="agents_shipgate/core/verification_input_currency.py",
        scope="_validate_base_comparison",
        target="agents_shipgate.cli.verify.git",
        count=1,
        reason=(
            "Reads the base comparison report at its commit with the "
            "verifier's audited Git reader, which lives in cli/verify; the "
            "reader belongs below cli (#1053)."
        ),
    ),
    AllowedLayerViolation(
        path="agents_shipgate/inputs/n8n/_common.py",
        scope=MODULE_SCOPE,
        target="agents_shipgate.cli.discovery.artifacts",
        count=1,
        reason=(
            "Shares discovery's SKIP_DIRS / SKIP_DIR_PREFIXES. #1053 moves "
            "them to a neutral module that cli re-exports."
        ),
    ),
)


# --- the scanner ---------------------------------------------------------------

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def _package_of(path: Path, src_root: Path) -> tuple[str, ...]:
    """The package a relative import in ``path`` resolves against."""

    parts = path.relative_to(src_root).with_suffix("").parts
    return parts[:-1]  # a package's ``__init__`` resolves in the package itself


def _from_targets(node: ast.ImportFrom, package: tuple[str, ...]) -> tuple[str, ...]:
    if node.level:
        if node.level - 1 > len(package):
            return ()  # beyond the top-level package: an ImportError at runtime
        base = package[: len(package) - (node.level - 1)]
        module = ".".join([*base, *([node.module] if node.module else [])])
    else:
        module = node.module or ""
    if module == PACKAGE:
        names = [alias.name for alias in node.names if alias.name != "*"]
        return tuple(f"{module}.{name}" for name in names) or (module,)
    return (module,)


def _literal(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dynamic_target(node: ast.Call, package: tuple[str, ...]) -> str | None:
    """The module a literal ``import_module`` / ``__import__`` call imports."""

    func = node.func
    name = None
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if (func.value.id, func.attr) in {("importlib", "import_module"), ("builtins", "__import__")}:
            name = func.attr
    if name not in _DYNAMIC_IMPORTERS:
        return None
    keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
    spelled = _literal(node.args[0] if node.args else keywords.get("name"))
    if not spelled:
        return None
    if not spelled.startswith("."):
        return spelled
    if name != "import_module":
        return None  # ``__import__`` takes its anchor from globals; not followed
    anchor_node = node.args[1] if len(node.args) > 1 else keywords.get("package")
    if isinstance(anchor_node, ast.Name) and anchor_node.id == "__package__":
        anchor: tuple[str, ...] | None = package
    else:
        literal = _literal(anchor_node)
        anchor = tuple(literal.split(".")) if literal else None
    if anchor is None:
        return None
    rest = spelled.lstrip(".")
    level = len(spelled) - len(rest)
    if level - 1 > len(anchor):
        return None
    base = anchor[: len(anchor) - (level - 1)]
    return ".".join([*base, *([rest] if rest else [])])


def _import_sites(source: str, relative_path: str, package: tuple[str, ...]) -> list[ImportSite]:
    """Every import in ``source``, with the scope that runs it.

    A ``def``, ``async def`` or ``class`` names a new scope for its *body*
    only: decorators, default arguments, annotations and class bases run in
    the enclosing scope. A lambda body is deferred like a function body, but
    keeps the name of the scope around it. Iterative, so deep nesting cannot
    exhaust the recursion limit.
    """

    sites: list[ImportSite] = []
    pending: list[tuple[ast.AST, str, bool, bool]] = [(ast.parse(source), MODULE_SCOPE, False, False)]
    while pending:
        node, scope, in_function, deferred = pending.pop()
        if isinstance(node, ast.Import):
            targets = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets = _from_targets(node, package)
        elif isinstance(node, ast.Call):
            target = _dynamic_target(node, package)
            targets = (target,) if target else ()
        else:
            targets = ()
        if targets:
            sites.append(ImportSite(relative_path, node.lineno, scope, deferred, targets))
        if isinstance(node, (*_FUNCTION_NODES, ast.ClassDef)):
            if in_function:
                qualname = f"{scope}.<locals>.{node.name}"
            elif scope == MODULE_SCOPE:
                qualname = node.name
            else:
                qualname = f"{scope}.{node.name}"
            is_function = isinstance(node, _FUNCTION_NODES)
            for field, value in ast.iter_fields(node):
                for child in value if isinstance(value, list) else [value]:
                    if not isinstance(child, ast.AST):
                        continue
                    if field == "body":
                        pending.append((child, qualname, is_function, deferred or is_function))
                    else:
                        pending.append((child, scope, in_function, deferred))
        elif isinstance(node, ast.Lambda):
            pending.append((node.args, scope, in_function, deferred))
            pending.append((node.body, scope, in_function, True))
        else:
            pending.extend((child, scope, in_function, deferred) for child in ast.iter_child_nodes(node))
    return sites


def _scan(src_root: Path) -> list[ImportSite]:
    """Every import site in every module of the package under ``src_root``."""

    sites: list[ImportSite] = []
    for path in sorted((src_root / PACKAGE).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(src_root).as_posix()
        sites.extend(_import_sites(path.read_text(encoding="utf-8"), relative, _package_of(path, src_root)))
    return sites


def _layer(module: str) -> str | None:
    """``agents_shipgate.core.x`` -> ``core``; ``None`` outside the package."""

    parts = module.split(".")
    if len(parts) < 2 or parts[0] != PACKAGE:
        return None
    return parts[1]


def _dotted(path: str) -> str:
    """``agents_shipgate/core/x.py`` -> ``agents_shipgate.core.x``."""

    return path.removesuffix(".py").replace("/", ".")


def _layer_violations(sites: Iterable[ImportSite]) -> list[LayerViolation]:
    violations: list[LayerViolation] = []
    for site in sites:
        own = _layer(_dotted(site.path))
        forbidden = LAYER_RULES.get(own or "", frozenset())
        for target in sorted(set(site.targets)):
            if _layer(target) in forbidden:
                violations.append(LayerViolation(site.path, site.line, site.scope, target))
    return violations


def _function_local_first_party(sites: Iterable[ImportSite]) -> list[tuple[ImportSite, str]]:
    return [
        (site, target)
        for site in sites
        if site.function_local
        for target in sorted(set(site.targets))
        if target == PACKAGE or target.startswith(f"{PACKAGE}.")
    ]


def _allowlist_mismatches(
    violations: Iterable[LayerViolation],
    allowlist: Iterable[AllowedLayerViolation],
) -> tuple[list[str], list[str]]:
    """``(unexpected, stale)``: what the allowlist misses, and what it lists in vain."""

    entries = {(entry.path, entry.scope, entry.target): entry for entry in allowlist}
    observed: dict[tuple[str, str, str], list[int]] = {}
    for violation in violations:
        observed.setdefault((violation.path, violation.scope, violation.target), []).append(violation.line)

    unexpected: list[str] = []
    stale: list[str] = []
    for key in sorted(observed):
        path, scope, target = key
        lines = sorted(observed[key])
        entry = entries.get(key)
        allowed = entry.count if entry else 0
        if len(lines) > allowed:
            unexpected.append(
                f"{path}:{','.join(map(str, lines))} ({scope}) imports {target}: "
                f"{_rule_text(path, target)}; {len(lines)} such import(s) here, "
                f"the allowlist permits {allowed}"
            )
        elif len(lines) < allowed:
            stale.append(
                f"{path} ({scope}) imports {target} {len(lines)} time(s); the allowlist "
                f"entry permits {allowed}: lower its count to {len(lines)}"
            )
    for key in sorted(set(entries) - set(observed)):
        path, scope, target = key
        stale.append(
            f"{path} ({scope}) no longer imports {target}: delete its "
            "ALLOWED_LAYER_VIOLATIONS entry"
        )
    return unexpected, stale


def _rule_text(path: str, target: str) -> str:
    return f"{_layer(_dotted(path))} must not import {_layer(target)}"


def _ratchet_problem(count: int, limit: int) -> str | None:
    if count > limit:
        return (
            f"src has {count} function-local imports of agents_shipgate modules; the "
            f"ratchet allows {limit}. A deferred import can hide an import cycle (#1053): "
            "import at module level, or break the cycle that forces the deferral. Read "
            "the docstring of tests/test_import_layers.py before raising the number."
        )
    if count < limit:
        return (
            f"src has {count} function-local imports of agents_shipgate modules, fewer "
            f"than the ratchet's {limit}: set FUNCTION_LOCAL_FIRST_PARTY_IMPORTS = {count} "
            "in tests/test_import_layers.py so the count cannot climb back."
        )
    return None


# --- the repository --------------------------------------------------------------


@pytest.fixture(scope="module")
def source_sites() -> list[ImportSite]:
    return _scan(SRC_ROOT)


def test_no_import_crosses_a_layer_outside_the_allowlist(source_sites: list[ImportSite]) -> None:
    unexpected, _ = _allowlist_mismatches(_layer_violations(source_sites), ALLOWED_LAYER_VIOLATIONS)
    assert not unexpected, (
        "imports that break a layer rule (move the shared code to the lower layer "
        "instead of importing upward):\n" + "\n".join(unexpected)
    )


def test_every_allowlisted_layer_violation_still_exists(source_sites: list[ImportSite]) -> None:
    _, stale = _allowlist_mismatches(_layer_violations(source_sites), ALLOWED_LAYER_VIOLATIONS)
    assert not stale, "the layer allowlist only shrinks:\n" + "\n".join(stale)


def test_function_local_first_party_imports_only_decrease(source_sites: list[ImportSite]) -> None:
    problem = _ratchet_problem(
        len(_function_local_first_party(source_sites)), FUNCTION_LOCAL_FIRST_PARTY_IMPORTS
    )
    assert problem is None, problem


def test_allowlist_entries_are_unique_and_give_a_reason() -> None:
    keys = Counter((entry.path, entry.scope, entry.target) for entry in ALLOWED_LAYER_VIOLATIONS)
    assert [key for key, seen in keys.items() if seen > 1] == []
    for entry in ALLOWED_LAYER_VIOLATIONS:
        assert entry.count >= 1, entry
        assert re.search(r"#\d+", entry.reason), f"{entry.path}: the reason names no issue"


def test_the_scan_reads_every_layer(source_sites: list[ImportSite]) -> None:
    """A scan that silently read nothing would pass every rule above."""

    scanned = {_layer(_dotted(site.path)) for site in source_sites}
    assert {"ci", "cli", "core", "inputs"} <= scanned
    assert len(_function_local_first_party(source_sites)) > 0


# --- the rule itself, on planted sources ----------------------------------------


def _plant(root: Path, files: dict[str, str]) -> list[ImportSite]:
    for relative, text in {"agents_shipgate/__init__.py": "", **files}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        for parent in path.relative_to(root).parents:
            init = root / parent / "__init__.py"
            if parent != Path(".") and not init.exists():
                init.write_text("")
        path.write_text(textwrap.dedent(text))
    return _scan(root)


@pytest.mark.parametrize(
    ("relative", "source", "target"),
    [
        (
            "agents_shipgate/inputs/reader.py",
            "from agents_shipgate.cli.discovery.artifacts import SKIP_DIRS\n",
            "agents_shipgate.cli.discovery.artifacts",
        ),
        (
            "agents_shipgate/inputs/reader.py",
            "def read():\n    import agents_shipgate.cli.main\n",
            "agents_shipgate.cli.main",
        ),
        ("agents_shipgate/inputs/reader.py", "import agents_shipgate.cli as c\n", "agents_shipgate.cli"),
        ("agents_shipgate/inputs/reader.py", "from agents_shipgate import cli\n", "agents_shipgate.cli"),
        ("agents_shipgate/inputs/reader.py", "from ..cli.main import app\n", "agents_shipgate.cli.main"),
        ("agents_shipgate/inputs/n8n/reader.py", "from ...cli import discovery\n", "agents_shipgate.cli"),
        ("agents_shipgate/core/__init__.py", "from .. import ci\n", "agents_shipgate.ci"),
        (
            "agents_shipgate/inputs/reader.py",
            "import importlib\n\ndef read():\n    importlib.import_module('agents_shipgate.cli.main')\n",
            "agents_shipgate.cli.main",
        ),
        (
            "agents_shipgate/inputs/reader.py",
            "from importlib import import_module\nimport_module('.cli.main', 'agents_shipgate')\n",
            "agents_shipgate.cli.main",
        ),
        (
            "agents_shipgate/inputs/sub/reader.py",
            "from importlib import import_module\nimport_module('...cli', package=__package__)\n",
            "agents_shipgate.cli",
        ),
        ("agents_shipgate/inputs/reader.py", "__import__('agents_shipgate.cli.main')\n", "agents_shipgate.cli.main"),
        (
            "agents_shipgate/inputs/reader.py",
            "import importlib\nimportlib.import_module(name='agents_shipgate.cli.main')\n",
            "agents_shipgate.cli.main",
        ),
        # Only the later of two names breaks a rule: inputs may import ci.
        ("agents_shipgate/inputs/reader.py", "from agents_shipgate import ci, cli\n", "agents_shipgate.cli"),
        (
            "agents_shipgate/inputs/reader.py",
            "import importlib\nload = lambda: importlib.import_module('agents_shipgate.cli')\n",
            "agents_shipgate.cli",
        ),
        (
            "agents_shipgate/core/decision.py",
            "from agents_shipgate.ci.release_decision import build_release_decision\n",
            "agents_shipgate.ci.release_decision",
        ),
        (
            "agents_shipgate/core/decision.py",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from agents_shipgate.cli.verify.git import x\n",
            "agents_shipgate.cli.verify.git",
        ),
        (
            "agents_shipgate/core/decision.py",
            "class Reader:\n    def read(self):\n        try:\n            from agents_shipgate.cli import y\n"
            "        except ImportError:\n            pass\n",
            "agents_shipgate.cli",
        ),
    ],
)
def test_a_planted_upward_import_is_reported(tmp_path: Path, relative: str, source: str, target: str) -> None:
    violations = _layer_violations(_plant(tmp_path, {relative: source}))
    assert [(v.path, v.target) for v in violations] == [(relative, target)]
    unexpected, stale = _allowlist_mismatches(violations, ())
    assert len(unexpected) == 1 and target in unexpected[0] and stale == []


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        # Only the rules in LAYER_RULES apply: inputs may import ci.
        ("agents_shipgate/inputs/reader.py", "from agents_shipgate.ci.release_decision import x\n"),
        # A layer is a whole package name, not a prefix of one.
        ("agents_shipgate/core/decision.py", "from agents_shipgate.cli_helpers import x\n"),
        ("agents_shipgate/core/decision.py", "import agents_shipgate.circuit\n"),
        # A submodule called ``cli`` or ``ci`` inside an allowed layer.
        ("agents_shipgate/core/decision.py", "from agents_shipgate.core.ci import x\n"),
        ("agents_shipgate/inputs/reader.py", "from .cli import x\n"),
        # Upward imports are the rule; downward ones are fine.
        ("agents_shipgate/cli/main.py", "from agents_shipgate.core import x\nfrom agents_shipgate.inputs import y\n"),
        ("agents_shipgate/ci/release_decision.py", "from agents_shipgate.core import x\n"),
        # A module name in a string that is not an import.
        ("agents_shipgate/core/decision.py", "NAME = 'agents_shipgate.cli.main'\nprint('agents_shipgate.ci')\n"),
        # A computed name cannot be resolved, and is documented as unseen.
        ("agents_shipgate/inputs/reader.py", "import importlib\nimportlib.import_module(NAME)\n"),
        (
            "agents_shipgate/inputs/reader.py",
            "def read(adapter):\n    adapter.import_module('agents_shipgate.cli.main')\n",
        ),
        ("agents_shipgate/inputs/reader.py", "from agents_shipgate import __version__\n"),
    ],
)
def test_an_allowed_import_is_not_reported(tmp_path: Path, relative: str, source: str) -> None:
    assert _layer_violations(_plant(tmp_path, {relative: source})) == []


def _violation(path: str, scope: str, target: str, line: int = 1) -> LayerViolation:
    return LayerViolation(path, line, scope, target)


_ENTRY = AllowedLayerViolation(
    path="agents_shipgate/inputs/reader.py",
    scope="read",
    target="agents_shipgate.cli.main",
    count=1,
    reason="planted (#1053)",
)


def test_an_allowlisted_violation_passes_wherever_it_moves_in_its_scope() -> None:
    for line in (3, 30):
        assert _allowlist_mismatches([_violation(_ENTRY.path, "read", _ENTRY.target, line)], [_ENTRY]) == ([], [])


@pytest.mark.parametrize(
    ("violations", "kind"),
    [
        # The import was removed: the entry must go too.
        ([], "stale"),
        # A second import of the same module in the same scope.
        (
            [
                _violation(_ENTRY.path, "read", _ENTRY.target, 3),
                _violation(_ENTRY.path, "read", _ENTRY.target, 9),
            ],
            "unexpected",
        ),
        # The same import in another scope is a new site, and the old entry is stale.
        ([_violation(_ENTRY.path, MODULE_SCOPE, _ENTRY.target)], "both"),
        # Another cli module from the same scope.
        (
            [
                _violation(_ENTRY.path, "read", _ENTRY.target),
                _violation(_ENTRY.path, "read", "agents_shipgate.cli.verify.git"),
            ],
            "unexpected",
        ),
    ],
)
def test_the_allowlist_only_shrinks(violations: list[LayerViolation], kind: str) -> None:
    unexpected, stale = _allowlist_mismatches(violations, [_ENTRY])
    assert bool(unexpected) == (kind in {"unexpected", "both"})
    assert bool(stale) == (kind in {"stale", "both"})


def test_an_entry_whose_count_exceeds_the_imports_is_stale() -> None:
    entry = AllowedLayerViolation(_ENTRY.path, "read", _ENTRY.target, 2, "planted (#1053)")
    unexpected, stale = _allowlist_mismatches([_violation(_ENTRY.path, "read", _ENTRY.target)], [entry])
    assert unexpected == [] and len(stale) == 1 and "lower its count to 1" in stale[0]


def test_the_ratchet_counts_only_deferred_first_party_imports(tmp_path: Path) -> None:
    sites = _plant(
        tmp_path,
        {
            "agents_shipgate/cli/main.py": """\
                import json
                import importlib
                from agents_shipgate.core import errors  # module level: not counted
                from . import helpers  # module level, relative: not counted

                @decorate(importlib.import_module("agents_shipgate.core"))  # runs at import
                def command(option=importlib.import_module("agents_shipgate.inputs")):
                    import yaml  # third party: not counted
                    from pathlib import Path  # standard library: not counted
                    from agents_shipgate.cli.check import check  # counted
                    from .verify import verify  # counted (relative)
                    importlib.import_module("agents_shipgate.cli.diff")  # counted

                class Loader:
                    from agents_shipgate.core import x  # class body runs at import: not counted

                    def load(self):
                        def inner():
                            import agents_shipgate.packet  # counted
                        return lambda: importlib.import_module("agents_shipgate.report")  # counted

                LOAD = lambda: importlib.import_module("agents_shipgate.checks")  # counted: runs when called
            """,
        },
    )
    counted = _function_local_first_party(sites)
    assert sorted(site.line for site, _ in counted) == [10, 11, 12, 19, 20, 22]
    assert {site.scope for site, _ in counted} == {
        "command",
        "Loader.load.<locals>.inner",
        "Loader.load",
        MODULE_SCOPE,
    }


@pytest.mark.parametrize(
    ("source", "deferred"),
    [
        # A class body inside a function runs when the function does.
        ("def build():\n    class Local:\n        from agents_shipgate.core import x\n", True),
        # A lambda's default argument runs when the lambda is made, here at import.
        (
            "import importlib\nload = lambda m=importlib.import_module('agents_shipgate.core'): m\n",
            False,
        ),
    ],
)
def test_deferral_follows_the_code_that_runs_the_import(
    tmp_path: Path, source: str, deferred: bool
) -> None:
    sites = _plant(tmp_path, {"agents_shipgate/cli/main.py": source})
    first_party = [site for site in sites if any(t.startswith(f"{PACKAGE}.") for t in site.targets)]
    assert [site.function_local for site in first_party] == [deferred]
    assert len(_function_local_first_party(sites)) == int(deferred)


@pytest.mark.parametrize(
    ("count", "fragment"),
    [
        (322, "the ratchet allows 321"),
        (320, "set FUNCTION_LOCAL_FIRST_PARTY_IMPORTS = 320"),
    ],
)
def test_the_ratchet_fails_both_ways(count: int, fragment: str) -> None:
    problem = _ratchet_problem(count, 321)
    assert problem is not None and fragment in problem
    assert _ratchet_problem(321, 321) is None


def test_the_ratchet_catches_a_new_deferred_import(tmp_path: Path) -> None:
    before = _plant(tmp_path, {"agents_shipgate/core/x.py": "def f():\n    from agents_shipgate.core import y\n"})
    limit = len(_function_local_first_party(before))
    after = _plant(
        tmp_path,
        {"agents_shipgate/core/x.py": "def f():\n    from agents_shipgate.core import y\n    from agents_shipgate.core import z\n"},
    )
    assert _ratchet_problem(len(_function_local_first_party(after)), limit) is not None


def test_the_ratchet_counts_each_first_party_target_in_one_statement(tmp_path: Path) -> None:
    sites = _plant(
        tmp_path,
        {"agents_shipgate/cli/x.py": "def f():\n    import json, agents_shipgate.core, agents_shipgate.schemas\n"},
    )

    assert len(_function_local_first_party(sites)) == 2
