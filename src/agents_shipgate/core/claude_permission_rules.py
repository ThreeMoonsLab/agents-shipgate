"""How Claude Code reads the permission rules of one settings source.

One model, shared by `check` (``core.host_boundary``) and the comparator that
`diff`, `audit --host --drift` and `verify`'s host comparison project
(``core.host_grants``), so the routes cannot read one rule two ways. Every
reading below is documented on Claude Code's permissions page,
https://code.claude.com/docs/en/permissions, read 2026-10-06:

* **Spelling** ("Wildcard patterns", "Match all uses of a tool"): a trailing
  ``:*`` is a trailing `` *`` and ``Bash(*)`` is ``Bash``.
  :func:`agents_shipgate.core.permission_lattice.canonical_rule` folds exactly
  those, and nothing else (#918).
* **Coverage**: an added allow rule that an allow rule the same source already
  had at the base matches entirely adds no command, whether that rule stays
  (#941), leaves (#918, one rule replaced by several proven subsets) or is a
  bare tool name over a path-scoped rule (#969). Only a decided
  ``subsumes(...) is True`` or the same canonical spelling counts; an
  undecidable pair is never covered.
* **Unconsulted path rules** ("Read and Edit"): "Claude Code checks file
  permissions against `Edit(path)` and `Read(path)` rules only. If you write a
  path rule for `Write`, `NotebookEdit`, `Glob`, or the legacy `MultiEdit`
  tool instead, Claude Code accepts the rule but never consults it" (v2.1.210
  and later). A tool-name rule with no path, such as a bare ``Write`` deny, is
  matched at the tool level and stays meaningful (#938).
* **Carve-outs** ("Read and Edit"): "A deny or ask pattern that starts with
  `!` is a gitignore negation. It carves the paths it matches out of the
  `path` or `./path` rules listed before it", reaches "only rules from the
  same source", "A `!` rule listed first carves nothing out", and cannot
  reach a rule anchored with ``/``, ``~/`` or ``//`` (#974).

What this module never does is pair rules by likeness or guess an overlap:
whether a carve-out's pattern actually matches anything an earlier rule
matched is not decided, so a carve-out is read as reaching every earlier rule
it *can* reach. That over-reads its effect, which is the direction a review
gate can afford.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from agents_shipgate.core.permission_lattice import (
    canonical_rule,
    parse_rule,
    subsumes,
)

#: Tools whose path-scoped rules Claude Code accepts and never consults.
UNCONSULTED_PATH_TOOLS = frozenset({"Write", "NotebookEdit", "Glob", "MultiEdit"})

#: Tools whose deny and ask lists read a leading ``!`` as a carve-out.
CARVE_OUT_TOOLS = frozenset({"Read", "Edit"})

#: Path prefixes a ``!`` pattern cannot reach ("Read and Edit": "the pattern
#: can't reach a rule anchored with one of those prefixes"). ``//`` starts
#: with ``/``.
_ANCHORED_PREFIXES = ("/", "~/")


def _tidy(rule: str) -> bool:
    """Written ``Tool`` or ``Tool(argument)`` with no padding around either part.

    The page does not say whether padding is read, so a padded text is never
    one documented spelling of another rule, nor a carve-out, nor a cover.
    """

    parsed = parse_rule(rule)
    layout = parsed.tool if parsed.argument is None else f"{parsed.tool}({parsed.argument})"
    return rule == layout


def _path_argument(rule: str) -> tuple[str, str] | None:
    """``(tool, argument)`` of a ``Tool(argument)`` rule, else ``None``."""

    parsed = parse_rule(rule)
    if parsed.argument is None or not parsed.raw.endswith(")"):
        return None
    return parsed.tool.strip(), parsed.argument.strip()


def every_path_pattern(argument: str) -> bool:
    """``*``, ``**``, ``**/*``, ``/**`` and the like: a pattern of nothing but stars."""

    pattern = argument[2:] if argument.startswith("./") else argument
    return "*" in pattern and set(pattern) <= {"*", "/"}


def unconsulted_path_rule(rule: str) -> bool:
    """Whether Claude Code accepts this rule and never consults it (#938).

    ``Write(.env)`` is; ``Write`` is not. A pattern of nothing but stars
    (``Write(*)``, ``Write(**)``, ``Write(**/*)``) is read as the whole tool,
    as ``Bash(*)`` is ``Bash``: calling a rule that may be enforced "not
    consulted" would hide a removed denial, so only a rule that names a
    narrower path is set aside.
    """

    parts = _path_argument(rule)
    if parts is None:
        return False
    tool, argument = parts
    return tool in UNCONSULTED_PATH_TOOLS and bool(argument) and not every_path_pattern(argument)


def is_carve_out(rule: str) -> bool:
    """A ``Read(!…)`` or ``Edit(!…)`` rule: in a deny or ask list, a carve-out.

    Only as written: a padded ``Read( !x)`` may be a literal path, a
    restriction, and removing a restriction must not read as removing a
    carve-out.
    """

    parts = _path_argument(rule)
    return (
        parts is not None and _tidy(rule)
        and parts[0] in CARVE_OUT_TOOLS and parts[1].startswith("!")
    )


def _carvable(carve_out: str, earlier: str) -> bool:
    """Whether ``carve_out`` can except paths from ``earlier`` (same tool, relative path)."""

    carve, rule = _path_argument(carve_out), _path_argument(earlier)
    if carve is None or rule is None or carve[0] != rule[0]:
        return False
    argument = rule[1]
    return bool(argument) and not argument.startswith("!") and not argument.startswith(
        _ANCHORED_PREFIXES
    )


def carve_out_predecessors(rules: Sequence[str]) -> dict[str, list[str]]:
    """For each carve-out in one ordered deny or ask list, the rules it can carve from.

    ``rules`` is one source's list in file order. A rule listed after the
    carve-out is never one of them; a carve-out listed more than once reaches
    what any of its occurrences reaches. Sorted, so the answer is a property
    of the order that matters and not of the order that does not.
    """

    reached: dict[str, set[str]] = {}
    for index, rule in enumerate(rules):
        if not is_carve_out(rule):
            continue
        found = reached.setdefault(rule, set())
        found.update(earlier for earlier in rules[:index] if _carvable(rule, earlier))
    return {rule: sorted(found) for rule, found in reached.items()}


def carve_out_widens(
    head_from: Iterable[str], base_from: Iterable[str] | None, base_rules: Iterable[str]
) -> bool:
    """Whether a carve-out now excepts paths from a restriction the base already had.

    ``head_from`` is what it can carve from at the head; ``base_from`` what the
    same carve-out could carve from at the base, ``None`` when it was absent or
    its position was not recorded. A rule it now follows that the source
    already declared at the base, and that it did not already follow, had its
    matches restricted at the base and has some of them excepted now: that is
    a restriction removed, however narrow. A rule new at the head restricted
    nothing at the base, so carving from it removes nothing the base had.
    """

    known = set(base_from or ())
    base = set(base_rules)
    return any(rule in base and rule not in known for rule in head_from)


def _may_cover(base: str, rule: str) -> bool:
    """Whether ``base`` is an allow rule that can approve anything ``rule`` names.

    Tool names are compared exactly: Claude Code matches canonical tool names
    ("Tool name wildcards"), so a differently cased name is another tool. A
    tool-name glob in an allow list counts only after a literal
    ``mcp__<server>__`` prefix; "An unanchored allow glob such as `"*"`,
    `"B*"`, or `"mcp__*"` is skipped with a warning and doesn't auto-approve
    anything", so it covers nothing.
    """

    wide, narrow = parse_rule(base), parse_rule(rule)
    tool = wide.tool.strip()
    if wide.argument is None:
        if tool.startswith("mcp__"):
            server, separator, _tool = tool[len("mcp__"):].partition("__")
            plain = bool(server) and "*" not in server
            return plain and (separator or "*" not in tool) and narrow.argument is None and (
                narrow.tool.strip().startswith(f"mcp__{server}")
            )
        if "*" in tool:
            return False
    return tool == narrow.tool.strip()


def covering_rule(rule: str, base_allow: Iterable[str]) -> str | None:
    """An allow rule from the base that matches everything ``rule`` matches, if decided.

    Only a decided ``True`` or the same canonical spelling counts, between
    rules of the same tool, and never from a rule Claude Code skips or never
    consults. The first such rule in sorted order is named, so the answer does
    not depend on input order.
    """

    for base in sorted(set(base_allow)):
        if unconsulted_path_rule(base) or not _tidy(base) or not _may_cover(base, rule):
            continue
        if same_spelling(base, rule) or subsumes(base, rule) is True:
            return base
    return None


def same_spelling(left: str, right: str) -> bool:
    """Two texts of one documented grant (``Bash(x:*)`` and ``Bash(x *)``, ``Bash(*)`` and ``Bash``).

    The tool name must match exactly and neither text may be padded: the
    lattice reads ``bash`` and `` Bash( x )`` loosely, and Claude Code is not
    documented to.
    """

    if left == right or not (_tidy(left) and _tidy(right)):
        return False
    if parse_rule(left).tool != parse_rule(right).tool:
        return False
    return canonical_rule(left) == canonical_rule(right)


__all__ = [
    "CARVE_OUT_TOOLS",
    "UNCONSULTED_PATH_TOOLS",
    "carve_out_predecessors",
    "carve_out_widens",
    "covering_rule",
    "every_path_pattern",
    "is_carve_out",
    "same_spelling",
    "unconsulted_path_rule",
]
