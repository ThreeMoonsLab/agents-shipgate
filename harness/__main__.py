"""Top-level dispatcher for harness families.

Invocations::

    python -m harness                Show usage + discovered families.
    python -m harness --help / -h    Same as above.
    python -m harness list           One family per line: ``<name>\\t<description>``.
    python -m harness <name> [args]  Forward to ``python -m harness.<name>``.

Forwarding is done via :mod:`subprocess` so the family's own
``__main__.py`` runs with ``sys.argv[0]`` set exactly as if it were
invoked directly with ``python -m harness.<name>``. This avoids the
Typer/Click prog-name detection corner cases that ``runpy``-based
forwarding hits, and keeps the family's own ``--help`` output
identical between direct and dispatched invocation.

The dispatcher is a developer convenience, not a packaged entry
point. Direct ``python -m harness.<name>`` invocation continues to
work; the dispatcher exists so future families don't each need a
custom invocation pattern in CI scripts and docs.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Bootstrap sys.path the same way ``harness/adoption/__main__.py`` does
# so the colocated ``src/`` wins over any editable install from a
# sibling worktree. Without this a checked-in
# ``agents_shipgate`` import from a different worktree could shadow
# the working tree under test. We only need this for the in-process
# ``discover_harnesses()`` call below — the subprocess child inherits
# the environment but Python's own ``-m`` flag handles its sys.path.
_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (_REPO_ROOT, _REPO_ROOT / "src"):
    _s = str(_path)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from harness import HarnessSpec, discover_harnesses  # noqa: E402

_USAGE_HEADER = """\
Usage: python -m harness <command> [args...]

Commands:
  list               One harness per line: ``<name>\\t<description>``.
  <name> [args...]   Forward to ``python -m harness.<name>``.
  --help, -h, help   Show this message.

Discovered harness families:
"""

_USAGE_FOOTER = """\

See harness/README.md for the convention every family must follow.
"""


def _format_families(specs: list[HarnessSpec]) -> str:
    if not specs:
        return "  (none — add a family under harness/<name>/)"
    width = max(len(spec.name) for spec in specs)
    return "\n".join(
        f"  {spec.name:<{width}}  {spec.description}" for spec in specs
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m harness``.

    Returns 0 on success, 2 on unknown harness, or the forwarded
    family's own exit code on a successful dispatch. Argv parsing is
    intentionally hand-rolled (no Typer, no argparse) so this stays a
    thin dispatch shim with no surprises for the family's own argv
    layer.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    specs = discover_harnesses()

    # ``--help`` / ``-h`` / ``help`` / no args → usage + family list.
    if not args or args[0] in ("--help", "-h", "help"):
        sys.stdout.write(_USAGE_HEADER)
        sys.stdout.write(_format_families(specs))
        sys.stdout.write("\n" + _USAGE_FOOTER)
        return 0

    # ``list`` → tab-separated one-per-line, for piping.
    if args[0] == "list":
        for spec in specs:
            sys.stdout.write(f"{spec.name}\t{spec.description}\n")
        return 0

    # Otherwise treat the first positional as a harness name and
    # forward to ``python -m harness.<name>`` via subprocess. Unknown
    # names are rejected with a routable error and exit 2 (config-
    # error convention shared with the main agents-shipgate CLI).
    name = args[0]
    by_name = {spec.name: spec for spec in specs}
    if name not in by_name:
        sys.stderr.write(f"error: no harness named {name!r}\n")
        available = ", ".join(spec.name for spec in specs) or "(none)"
        sys.stderr.write(f"available: {available}\n")
        sys.stderr.write(
            "Run ``python -m harness --help`` for the full convention.\n"
        )
        return 2

    # Forward via subprocess so the child's ``sys.argv[0]`` matches a
    # direct ``python -m harness.<name>`` invocation exactly. This
    # keeps the child's Typer/Click ``--help`` output indistinguishable
    # from direct invocation, which is the whole point of the
    # convention.
    completed = subprocess.run(
        [sys.executable, "-m", f"harness.{name}", *args[1:]],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
