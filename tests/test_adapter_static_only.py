"""Trust-model structural invariant: scanner does not execute or import user code.

This test enforces the core trust property of agents-shipgate at the source
level: every Python file under ``src/agents_shipgate/`` (not just
``inputs/``) must parse user files with ``ast.parse``, ``yaml.safe_load``,
or ``json.loads`` only — never through ``exec`` / ``eval`` / ``__import__``
/ ``compile`` builtins, and never via dynamic-import or process-execution
surfaces.

v0.18 (PR #2): the scope widened from adapter sources only to the whole
scanner package. A small allowlist (``ALLOWED_EXCEPTIONS`` below) captures
legitimate first-party meta-CLI surfaces with prose rationale — bootstrap
subprocess chaining, git probing in artifacts/triggers/verify, and
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

    ``snippet`` is the canonical ``ast.unparse`` of the offending AST node
    (Import/ImportFrom/Call). Combined with ``line`` it lets the allowlist
    pin individual call sites rather than blanket-permitting every
    occurrence in a file.
    """

    line: int
    surface: str
    message: str
    snippet: str


@dataclass(frozen=True)
class AllowedException:
    """v0.18 (PR #2): per-call-site allowed forbidden-surface entry,
    with prose rationale.

    PR #2 review follow-up: the entry now pins ``line`` and ``snippet``
    in addition to ``(relative_path, surface)``. The original
    ``(relative_path, surface)``-only design blanket-permitted every
    occurrence of a surface in a file, so a future unreviewed
    ``subprocess.run(...)`` added to ``triggers.py`` would pass silently.
    Pinning call-site granularity forces every new occurrence to be
    individually allowlisted with rationale.

    ``line`` is the 1-indexed line number of the offending AST node.
    ``snippet`` is the canonical ``ast.unparse`` of the node. The two
    contract tests assert that every entry matches a real violation
    with the same ``(line, snippet)`` AND that every observed violation
    has a matching entry.

    Line drift (inserting a comment that pushes a call down) WILL fail
    the test — that is the intended failure mode, forcing a reviewer to
    confirm the change is benign and bump the entry.
    """

    relative_path: str
    surface: str
    line: int
    snippet: str
    rationale: str


# Per-call-site allowlist of forbidden-surface uses. Each entry MUST
# correspond to a real call site (verified by
# ``test_allowlist_entry_matches_real_surface``). Every observed forbidden
# surface in the scanner MUST have a matching entry (verified by
# ``test_no_unallowlisted_forbidden_surface_in_scanner``). Rationale text
# is mirrored in STABILITY.md § "Meta-CLI surfaces (allowlisted, audited)".
#
# Adding a new call site requires adding an entry here AND updating the
# rationale; the entries are intentionally verbose so review-by-grep
# captures the full audit trail.
ALLOWED_EXCEPTIONS: tuple[AllowedException, ...] = (
    # cli/bootstrap.py — chains detect/init/scan/apply-patches against
    # Shipgate's own CLI. The argv is sys.executable + ``-m agents_shipgate``,
    # never user input.
    AllowedException(
        relative_path="cli/bootstrap.py",
        surface="import:subprocess",
        line=31,
        snippet="import subprocess",
        rationale=(
            "Bootstrap chains detect → init → scan → apply-patches against "
            "the installed agents-shipgate CLI via subprocess. Reads no "
            "user code; argv is always sys.executable + Shipgate's own "
            "module/flags."
        ),
    ),
    AllowedException(
        relative_path="cli/bootstrap.py",
        surface="attr_call:subprocess.run",
        line=73,
        snippet=(
            "subprocess.run(argv, cwd=str(cwd), env=env, "
            "capture_output=True, text=True, check=False)"
        ),
        rationale=(
            "_run_step: invokes the installed agents-shipgate CLI as a "
            "subprocess with argv constructed inside Shipgate (no user "
            "input executed as a command)."
        ),
    ),
    # cli/discovery/artifacts.py — probes the user repo via git ls-files
    # for file inventory. Reads git metadata; no shell, no user-code exec.
    AllowedException(
        relative_path="cli/discovery/artifacts.py",
        surface="import:subprocess",
        line=6,
        snippet="import subprocess",
        rationale=(
            "Discovery uses ``git ls-files`` to enumerate user-repo files "
            "without walking ignored paths. argv is a fixed git invocation; "
            "no shell, no user-code exec."
        ),
    ),
    AllowedException(
        relative_path="cli/discovery/artifacts.py",
        surface="attr_call:subprocess.run",
        line=552,
        snippet=(
            "subprocess.run(['git', '--no-replace-objects', '-C', "
            "str(workspace), 'rev-parse', '--show-toplevel'], check=False, "
            "capture_output=True, env=env, text=True, timeout=2)"
        ),
        rationale=(
            "_git_candidate_files step 1: ``git -C <workspace> "
            "rev-parse --show-toplevel`` to locate the repo root. "
            "Fixed argv, sanitized Git environment, capture-only, no shell, "
            "and a hard timeout."
        ),
    ),
    AllowedException(
        relative_path="cli/discovery/artifacts.py",
        surface="attr_call:subprocess.Popen",
        line=634,
        snippet=(
            "subprocess.Popen(['git', '--no-replace-objects', '-C', "
            "str(workspace), *args], env=env, stdout=subprocess.PIPE, "
            "stderr=subprocess.DEVNULL)"
        ),
        rationale=(
            "_git_candidate_files step 2: ``git -C <workspace> "
            "ls-files -co --exclude-standard --full-name -z -- .`` to "
            "enumerate tracked + untracked-not-ignored files in NUL-"
            "delimited form. The fixed-argv, sanitized-environment collector "
            "drains incrementally and kills Git at a hard byte or wall-clock "
            "bound; no shell or user-code execution."
        ),
    ),
    # cli/trigger.py — the `agents-shipgate trigger` subcommand imports
    # subprocess ONLY to catch ``subprocess.CalledProcessError`` from the
    # verifier's shared audited Git collector. It issues no subprocess call
    # of its own.
    AllowedException(
        relative_path="cli/trigger.py",
        surface="import:subprocess",
        line=23,
        snippet="import subprocess",
        rationale=(
            "trigger subcommand catches subprocess.CalledProcessError from "
            "the shared audited Git collector reached through "
            "triggers._git_diff_context; this file issues no subprocess "
            "call itself."
        ),
    ),
    # cli/verify/git.py — verify orchestrates local base/head git reads.
    # It never fetches and never executes user code.
    AllowedException(
        relative_path="cli/verify/git.py",
        surface="import:subprocess",
        line=6,
        snippet="import subprocess",
        rationale=(
            "Verify uses local git commands to resolve refs, collect diffs, "
            "resolve git metadata paths, and copy/fsck exact base/head object "
            "graphs into isolated stores. Fixed argv, no shell, and no "
            "network fetch."
        ),
    ),
    AllowedException(
        relative_path="cli/verify/git.py",
        surface="attr_call:subprocess.Popen",
        line=1954,
        snippet=(
            "subprocess.Popen(cmd, env=env, stderr=subprocess.PIPE, "
            "stdin=subprocess.PIPE if input is not None else "
            "subprocess.DEVNULL, stdout=subprocess.PIPE)"
        ),
        rationale=(
            "The shared bounded Git collector drains fixed local Git argv "
            "incrementally and kills them at a hard byte or wall-clock "
            "bound. It covers diff/name/attribute/inventory reads and "
            "retained-manifest discovery without a shell, user-code "
            "execution, or fetch. stderr is piped (not discarded) and "
            "drained on its own thread under a small cap so an input "
            "failure can be classified; the excerpt is diagnostic only."
        ),
    ),
    AllowedException(
        relative_path="cli/verify/git.py",
        surface="attr_call:subprocess.run",
        line=2198,
        snippet=(
            "subprocess.run(cmd, capture_output=capture_output, check=check, "
            "env=env, input=input, stderr=stderr, stdin=stdin, stdout=stdout, "
            "text=text, timeout=timeout)"
        ),
        rationale=(
            "Single _run_process boundary for verify: executes Git argv "
            "assembled inside Shipgate (local ref/diff reads plus "
            "pack-objects, index-pack, and fsck for immutable snapshots). "
            "No shell, user-code execution, or fetch."
        ),
    ),
    # core/authorization_execution.py — guarded consumer for an externally
    # signed, typed Git push. It invokes only a host-protected Git binary and
    # exact internally assembled argv after revalidation.
    AllowedException(
        relative_path="core/authorization_execution.py",
        surface="import:subprocess",
        line=16,
        snippet="import subprocess",
        rationale=(
            "The guarded authorization executor snapshots/fscks one signed "
            "commit graph and invokes its exact force-with-lease Git push. "
            "The host Git binary and HTTPS helper are protected; no shell or "
            "user-code execution is used."
        ),
    ),
    AllowedException(
        relative_path="core/authorization_execution.py",
        surface="attr_call:subprocess.run",
        line=518,
        snippet=(
            "subprocess.run(cmd, capture_output=capture_output, check=check, "
            "cwd=cwd, env=env, input=input, stderr=stderr, stdin=stdin, "
            "stdout=stdout, text=text, timeout=timeout)"
        ),
        rationale=(
            "Single _run_process boundary for guarded authorization: all "
            "callers use list argv, shell=False by default, a sanitized "
            "environment, protected Git/transport paths, and bounded timeouts."
        ),
    ),
    AllowedException(
        relative_path="core/authorization_execution.py",
        surface="attr_call:subprocess.Popen",
        line=553,
        snippet=(
            "subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE, "
            "stdout=subprocess.PIPE, stderr=subprocess.PIPE)"
        ),
        rationale=(
            "The isolated graph snapshot parent-streams Git pack-objects "
            "through fixed pipes so stdout, stderr, and wall-clock use are "
            "bounded without a thread-unsafe preexec hook. Fixed Git argv, "
            "sanitized environment, no shell, no user-code execution."
        ),
    ),
    # cli/fixture.py — the ai_generated_refund_pr demo fixture creates a
    # tiny temporary git history so users can reproduce verifier artifacts.
    # It never reads or executes user code.
    AllowedException(
        relative_path="cli/fixture.py",
        surface="import:subprocess",
        line=10,
        snippet="import subprocess",
        rationale=(
            "Fixture run catches subprocess.CalledProcessError and invokes "
            "local git to create a temporary first-party sample repo for the "
            "ai_generated_refund_pr verifier demo. Fixed argv, no shell, no "
            "network, no user-code execution."
        ),
    ),
    AllowedException(
        relative_path="cli/fixture.py",
        surface="attr_call:subprocess.run",
        line=519,
        snippet=(
            "subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)"
        ),
        rationale=(
            "_git helper for the ai_generated_refund_pr fixture: runs local "
            "git init/config/add/commit/update-ref commands against a "
            "temporary bundled fixture copy. argv is assembled inside "
            "Shipgate, with no shell, no network fetch, and no user-code "
            "execution."
        ),
    ),
    # cli/self_check.py — validates the installed environment via __import__
    # with the module name supplied as a CLI flag. Targets installed
    # packages, never user workspace.
    AllowedException(
        relative_path="cli/self_check.py",
        surface="name_call:__import__",
        line=144,
        snippet="__import__(module_name)",
        rationale=(
            "self-check probes whether named modules import cleanly in the "
            "installed environment. Runs only under ``agents-shipgate "
            "self-check``, never during scan; module_name is a curated "
            "list of Shipgate's own modules."
        ),
    ),
    # importlib.resources.files — bundled-package access. Anchor is the
    # literal 'agents_shipgate' string at both call sites (verified by
    # snippet pinning); any future call with a non-literal anchor would
    # change the snippet and fail the test, forcing re-review.
    AllowedException(
        relative_path="triggers.py",
        surface="import:importlib.resources.files",
        line=26,
        snippet="from importlib.resources import files",
        rationale=(
            "triggers.py reads the bundled docs/triggers.json catalog from "
            "the installed wheel via importlib.resources.files. The from-"
            "import line is pinned alongside the call site to catch a "
            "future refactor that imports files() for a different anchor."
        ),
    ),
    AllowedException(
        relative_path="triggers.py",
        surface="attr_call:importlib.resources.files",
        line=143,
        snippet="files('agents_shipgate')",
        rationale=(
            "Resolves the bundled trigger catalog (docs/triggers.json) "
            "inside the agents-shipgate wheel. Anchor is the literal "
            "'agents_shipgate' string (snippet pinning enforces this; a "
            "future non-literal anchor would change the snippet and fail "
            "the contract test)."
        ),
    ),
    AllowedException(
        relative_path="fixtures.py",
        surface="import:importlib.resources.files",
        line=12,
        snippet="from importlib.resources import files",
        rationale=(
            "fixtures.py reads the bundled samples/ directory from the "
            "installed wheel. From-import line pinned alongside the call "
            "site for the same reason as triggers.py."
        ),
    ),
    AllowedException(
        relative_path="fixtures.py",
        surface="attr_call:importlib.resources.files",
        line=178,
        snippet="files('agents_shipgate')",
        rationale=(
            "Resolves the bundled fixture directory (samples/*) inside the "
            "agents-shipgate wheel. Anchor is the literal "
            "'agents_shipgate' string (snippet pinning enforces this)."
        ),
    ),
    AllowedException(
        relative_path="cli/discovery/agent_instructions/adoption_kit.py",
        surface="import:importlib.resources.files",
        line=9,
        snippet="from importlib.resources import files",
        rationale=(
            "adoption_kit.py reads bundled first-party adoption-kit files "
            "from the installed wheel. From-import line pinned alongside "
            "the call site for the same literal-anchor review as triggers.py."
        ),
    ),
    AllowedException(
        relative_path="cli/discovery/agent_instructions/adoption_kit.py",
        surface="attr_call:importlib.resources.files",
        line=359,
        snippet="files('agents_shipgate')",
        rationale=(
            "Resolves bundled adoption-kits/* content inside the "
            "agents-shipgate wheel. Anchor is the literal 'agents_shipgate' "
            "string (snippet pinning enforces this); downstream customization "
            "uses explicit repo-local override files, not dynamic imports."
        ),
    ),
)


def _lookup_allowed(
    relative_path: str,
    surface: str,
    line: int,
    snippet: str,
) -> AllowedException | None:
    """v0.18 (PR #2): match a violation to an ``AllowedException`` on all
    four fields. Returns the matching entry, or ``None`` if the violation
    is not allowlisted.

    Per-call-site pinning closes the P1 review hole: a future unreviewed
    ``subprocess.run(...)`` added to an already-allowlisted file no
    longer slips past, because its ``line`` and ``snippet`` will not
    match any existing entry.
    """
    for exc in ALLOWED_EXCEPTIONS:
        if (
            exc.relative_path == relative_path
            and exc.surface == surface
            and exc.line == line
            and exc.snippet == snippet
        ):
            return exc
    return None


def _violation_allowed(relative_path: str, surface: str, line: int, snippet: str) -> bool:
    return _lookup_allowed(relative_path, surface, line, snippet) is not None


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
        # NOTE: ``importlib.resources.*`` is intentionally NOT enumerated
        # here — it is caught by the ``importlib.resources.`` entry in
        # ``FORBIDDEN_ATTR_CALL_PREFIXES`` below. The prefix catches
        # ``files``, ``read_text``, ``read_binary``, ``path``,
        # ``open_text``, ``open_binary``, ``is_resource``, ``contents``,
        # ``as_file``, and any future addition under the module — all of
        # which take an anchor package argument and could bypass the
        # dynamic-import lint if left unrestricted.
    }
)

# Prefix-matched forbidden families. Each prefix captures a family of
# attribute calls where enumerating every member is fragile (Python adds
# new variants over time) or where the security boundary is
# module-wide rather than per-function. The ``importlib.resources.``
# entry was added in v0.18 PR #2 review follow-up: previously only
# ``importlib.resources.files`` was forbidden, but ``read_text``,
# ``read_binary``, etc. take the same anchor argument and produced no
# violation. Adding the prefix forces every ``importlib.resources.*``
# call to be allowlisted with snippet pinning, so a non-literal anchor
# (``read_text(user_var, "x")``) would fail the contract test the same
# way ``files(user_var)`` does.
FORBIDDEN_ATTR_CALL_PREFIXES: tuple[str, ...] = (
    "os.exec",
    "os.spawn",
    "os.posix_spawn",
    # v0.18 (PR #2 review follow-up): catches files, read_text,
    # read_binary, path, open_text, open_binary, is_resource, contents,
    # as_file, and any future addition. Trailing dot ensures the prefix
    # only matches sub-attributes (``importlib.resources.X``), not the
    # module itself.
    "importlib.resources.",
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
#   not user workspace code. The import line is allowed here so name_aliases
#   get built, but every ``importlib.resources.<attr>(...)`` call is caught
#   by the ``importlib.resources.`` prefix in ``FORBIDDEN_ATTR_CALL_PREFIXES``
#   above (v0.18 PR #2 review follow-up — initially only ``files`` was
#   forbidden, leaving ``read_text``/``read_binary``/``path``/etc. as a
#   parallel hole). Each call site needs an ALLOWED_EXCEPTIONS entry with
#   snippet pinning so a non-literal anchor would fail the contract test.
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
#
# v0.18 (PR #2 review follow-up): ``importlib.resources`` joins this set so
# ``from importlib.resources import files`` (and ``read_text``,
# ``read_binary``, etc.) builds a name_alias and the subsequent call site
# can be resolved to a canonical ``importlib.resources.<attr>`` chain that
# the ``importlib.resources.`` prefix in ``FORBIDDEN_ATTR_CALL_PREFIXES``
# catches.
TRACKED_NON_FORBIDDEN_MODULES: frozenset[str] = frozenset({"os", "importlib.resources"})


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
        module == forbidden or module.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_MODULES
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
        ".".join(canonical_root.split(".") + parts[1:]) for canonical_root in module_aliases[root]
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
                snippet="",
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
                            snippet=ast.unparse(node),
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
                        snippet=ast.unparse(node),
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
                                snippet=ast.unparse(node),
                            )
                        )
                    continue
                # Flag the from-import line itself when the imported
                # attribute resolves to a forbidden surface — gives a
                # clearer error than waiting for the call site. v0.18
                # PR #2 follow-up: emit surface=``import:<canonical>`` so
                # the from-import line is distinguishable from the call
                # site (which emits ``attr_call:<canonical>``) when both
                # exist in the same file.
                if mod in TRACKED_NON_FORBIDDEN_MODULES:
                    canonical = f"{mod}.{alias.name}"
                    if _is_forbidden_chain(canonical):
                        violations.append(
                            Violation(
                                line=node.lineno,
                                surface=f"import:{canonical}",
                                message=(
                                    f"{rel}:{node.lineno}: forbidden from-import of {canonical!r}"
                                ),
                                snippet=ast.unparse(node),
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
                        message=(f"{rel}:{node.lineno}: forbidden builtin call {func.id!r}"),
                        snippet=ast.unparse(node),
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
                                message=(f"{rel}:{node.lineno}: forbidden call {canonical!r}{via}"),
                                snippet=ast.unparse(node),
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
                            message=(f"{rel}:{node.lineno}: forbidden call {chain!r}"),
                            snippet=ast.unparse(node),
                        )
                    )
                    break
    return violations


def _scanner_sources() -> list[Path]:
    """v0.18 (PR #2): every .py file under src/agents_shipgate/ (not just
    ``inputs/``). Skips ``__pycache__`` debris.
    """
    return sorted(p for p in SCANNER_DIR.rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize(
    "scanner_source",
    _scanner_sources(),
    ids=lambda p: str(p.relative_to(SCANNER_DIR)),
)
def test_scanner_source_contains_no_forbidden_calls_or_imports(
    scanner_source: Path,
) -> None:
    """Each scanner source is statically free of code-execution surfaces,
    EXCEPT for the audited first-party meta-CLI surfaces captured
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
    unallowed = [v for v in violations if not _violation_allowed(rel, v.surface, v.line, v.snippet)]
    assert not unallowed, (
        "Trust-model invariant violation under src/agents_shipgate/:\n  "
        + "\n  ".join(
            f"{v.message}  (surface={v.surface!r}, snippet={v.snippet!r})" for v in unallowed
        )
        + "\n\n"
        + "The scanner MUST NOT execute or import user code. Parse with "
        + "ast.parse / yaml.safe_load / json.loads ONLY. See "
        + "STABILITY.md § 'Trust-model invariants' and the companion "
        + "tests/test_fixture_no_import.py live-load tests. If the surface "
        + "is a legitimate first-party meta-CLI operation, add an "
        + "ALLOWED_EXCEPTIONS entry with the exact line, snippet, and "
        + "prose rationale, and document it in STABILITY.md § 'Meta-CLI "
        + "surfaces (allowlisted, audited)'. Existing entries cover "
        + "specific call sites only — adding a new occurrence of an "
        + "already-allowlisted surface in the same file STILL requires a "
        + "new entry."
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
            "import os as runner\nrunner.execve('/bin/sh', ['sh'])\nimport pathlib as runner\n",
            "forbidden call 'os.execve'",
        ),
        (
            "from os import system\nsystem('ls')\nfrom pathlib import system\n",
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
        # --- importlib.resources.* prefix family (v0.18 PR #2 review
        # follow-up: not just files() — every anchor-taking callable
        # under importlib.resources is forbidden via the
        # ``importlib.resources.`` prefix entry in
        # FORBIDDEN_ATTR_CALL_PREFIXES). The reviewer reproduced
        # ``read_text`` and ``files`` as the canonical bypass shapes;
        # this parametrize block locks both, plus a few siblings, so a
        # regression that narrows the prefix back to ``files`` only
        # fails immediately.
        (
            "from importlib.resources import files\nfiles('agents_shipgate')\n",
            "forbidden call 'importlib.resources.files'",
        ),
        (
            "from importlib.resources import read_text\nread_text(pkg, 'x')\n",
            "forbidden call 'importlib.resources.read_text'",
        ),
        (
            "from importlib.resources import read_binary\nread_binary(pkg, 'x')\n",
            "forbidden call 'importlib.resources.read_binary'",
        ),
        (
            "from importlib.resources import open_text\nopen_text(pkg, 'x')\n",
            "forbidden call 'importlib.resources.open_text'",
        ),
        (
            "from importlib.resources import path\npath(pkg, 'x')\n",
            "forbidden call 'importlib.resources.path'",
        ),
        (
            "from importlib.resources import is_resource\nis_resource(pkg, 'x')\n",
            "forbidden call 'importlib.resources.is_resource'",
        ),
        (
            "from importlib.resources import as_file\nas_file(some_traversable)\n",
            "forbidden call 'importlib.resources.as_file'",
        ),
        # Attribute-chain form: ``import importlib.resources;
        # importlib.resources.read_text(pkg, "x")``.
        (
            "import importlib.resources\nimportlib.resources.read_text(pkg, 'x')\n",
            "forbidden call 'importlib.resources.read_text'",
        ),
        # Aliased attribute-chain form.
        (
            "import importlib.resources as res\nres.read_text(pkg, 'x')\n",
            "forbidden call 'importlib.resources.read_text'",
        ),
        # From-import alias for the broader surface.
        (
            "from importlib.resources import read_text as rt\nrt(pkg, 'x')\n",
            "forbidden call 'importlib.resources.read_text'",
        ),
    ],
    ids=lambda x: x if isinstance(x, str) and len(x) < 40 else "case",
)
def test_lint_scanner_catches_known_violation_shapes(snippet: str, expected_substring: str) -> None:
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
    ids=lambda e: f"{e.relative_path}:{e.line}:{e.surface}",
)
def test_allowlist_entry_matches_real_surface(exc: AllowedException) -> None:
    """v0.18 (PR #2) stale-allowlist guard.

    Every ``ALLOWED_EXCEPTIONS`` entry MUST correspond to a real
    violation in the named file with the SAME ``(surface, line,
    snippet)`` quadruple. Three failure modes are detected:

    - **Removed surface:** file refactored to remove the call site;
      entry has no matching violation → fail (entry is stale).
    - **Line drift:** call site moved to a different line (e.g.,
      a comment was added above); entry's ``line`` no longer
      matches → fail (forces re-review of the move).
    - **Snippet drift:** call site's argv or shape changed (e.g.,
      ``subprocess.run(['git', 'log'])`` was edited to
      ``subprocess.run(user_input)``); entry's ``snippet`` no
      longer matches → fail (forces re-review of the semantic
      change).

    PR #2 review follow-up: the original ``(file, surface)``-only
    match blanket-permitted every occurrence in a file. The
    quadruple match restricts each entry to a single call site.
    """
    path = SCANNER_DIR / exc.relative_path
    assert path.exists(), f"allowlisted file does not exist: {exc.relative_path}"
    source = path.read_text(encoding="utf-8")
    violations = _scan_source(source, path)
    same_surface = [v for v in violations if v.surface == exc.surface]
    assert same_surface, (
        f"allowlisted surface {exc.surface!r} not present in "
        f"{exc.relative_path!r} at all; entry is stale, remove or "
        f"refactor. Observed surfaces in this file: "
        f"{sorted({v.surface for v in violations})!r}"
    )
    same_line = [v for v in same_surface if v.line == exc.line]
    assert same_line, (
        f"allowlisted entry for {exc.relative_path}:{exc.surface} pins "
        f"line {exc.line}, but no violation of that surface was found "
        f"at that line. Lines actually flagged: "
        f"{sorted({v.line for v in same_surface})!r}. Either the call "
        f"moved (update the entry) or the entry is stale."
    )
    matching = [v for v in same_line if v.snippet == exc.snippet]
    assert matching, (
        f"allowlisted entry for {exc.relative_path}:{exc.line}:"
        f"{exc.surface} has snippet {exc.snippet!r}, but the actual "
        f"violation at that line has snippet "
        f"{same_line[0].snippet!r}. The call's argv or shape changed "
        f"— re-review the rationale and update the snippet."
    )


def test_allowed_exceptions_have_no_duplicates() -> None:
    """v0.18 (PR #2 review follow-up): every ``ALLOWED_EXCEPTIONS``
    entry must be a distinct ``(relative_path, surface, line)`` triple.
    Two entries pointing at the same call site would both match the
    same violation, hiding a real audit-trail bug.
    """
    keys = [(e.relative_path, e.surface, e.line) for e in ALLOWED_EXCEPTIONS]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, (
        f"ALLOWED_EXCEPTIONS contains duplicate (relative_path, "
        f"surface, line) entries: {duplicates}. Each call site must "
        f"have exactly one entry."
    )


def test_no_unallowlisted_forbidden_surface_in_scanner() -> None:
    """v0.18 (PR #2): the whole-scanner sweep with allowlist applied
    produces zero unallowlisted violations. This is the runtime gate
    PR #2 hardens.

    The parametrized
    ``test_scanner_source_contains_no_forbidden_calls_or_imports``
    above catches per-file regressions. This non-parametrized form
    gives a single, definitive PASS/FAIL signal at the end of the
    test session. A failure message lists every unallowlisted
    ``(relative_path, line, surface, snippet)`` quadruple the
    contributor must address — either by refactoring the offending
    file or by adding an ``ALLOWED_EXCEPTIONS`` entry with prose
    rationale.

    PR #2 review follow-up: the previous version matched on
    ``(file, surface)`` only, so a future second occurrence of an
    already-allowlisted surface in the same file would silently pass.
    The quadruple match closes that hole.
    """
    unallowed: list[tuple[str, int, str, str]] = []
    for path in _scanner_sources():
        rel = str(path.relative_to(SCANNER_DIR))
        for violation in _scan_source(path.read_text(encoding="utf-8"), path):
            if not _violation_allowed(rel, violation.surface, violation.line, violation.snippet):
                unallowed.append((rel, violation.line, violation.surface, violation.snippet))
    assert unallowed == [], (
        "Scanner has forbidden surfaces with no allowlist entry. "
        "Add an ALLOWED_EXCEPTIONS entry with prose rationale, or "
        "refactor the surface out. Unallowlisted:\n  "
        + "\n  ".join(
            f"{rel}:{line} {surface}  snippet={snippet!r}"
            for rel, line, surface, snippet in unallowed
        )
    )


def test_allowed_exceptions_pin_subprocess_per_call_site() -> None:
    """v0.18 (PR #2 review follow-up): regression test for the P1
    bypass the reviewer caught.

    Trigger evaluation now delegates to the verifier's audited Git transport,
    so ``triggers.py`` has no subprocess exception. Discovery has one bounded
    root probe and one bounded inventory collector; verify has one shared
    conventional process boundary and one shared bounded-output boundary.

    If the test fails because lines drifted, that is the intended
    failure mode — the contributor must re-review the move and bump
    the entries. If it fails because the count dropped, an existing
    entry has gone stale (the call was removed but the entry remains).
    """
    by_file_surface: dict[tuple[str, str], list[AllowedException]] = {}
    for exc in ALLOWED_EXCEPTIONS:
        by_file_surface.setdefault((exc.relative_path, exc.surface), []).append(exc)

    assert not [
        exc
        for exc in ALLOWED_EXCEPTIONS
        if exc.relative_path == "triggers.py"
        and exc.surface.startswith(("import:subprocess", "attr_call:subprocess."))
    ], "triggers.py must use the verifier's audited Git transport"

    artifacts_subprocess_run = by_file_surface.get(
        ("cli/discovery/artifacts.py", "attr_call:subprocess.run"), []
    )
    artifacts_subprocess_popen = by_file_surface.get(
        ("cli/discovery/artifacts.py", "attr_call:subprocess.Popen"), []
    )
    assert len(artifacts_subprocess_run) == 1, (
        "Expected one root-resolution subprocess.run exception for cli/discovery/artifacts.py."
    )
    assert len(artifacts_subprocess_popen) == 1, (
        "Expected one bounded inventory subprocess.Popen exception for cli/discovery/artifacts.py."
    )
    verify_subprocess_run = by_file_surface.get(
        ("cli/verify/git.py", "attr_call:subprocess.run"), []
    )
    verify_subprocess_popen = by_file_surface.get(
        ("cli/verify/git.py", "attr_call:subprocess.Popen"), []
    )
    assert len(verify_subprocess_run) == 1, (
        f"Expected 1 AllowedException entry for cli/verify/git.py "
        f"subprocess.run (the shared fixed-argv process boundary), got "
        f"{len(verify_subprocess_run)}."
    )
    assert len(verify_subprocess_popen) == 1, (
        "Expected one AllowedException entry for cli/verify/git.py "
        "subprocess.Popen (the shared bounded-output Git boundary)."
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
        "cli/scan/orchestrator.py",
        "cli/scan/source_loading.py",
        "inputs/protocol.py",
        "inputs/mcp.py",
        "checks/registry.py",
        "checks/plugin_validation.py",
        "core/domain.py",
        "core/severity_overrides.py",
        "core/findings/report_builder.py",
        "core/findings/identity.py",
        "schemas/report.py",
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
