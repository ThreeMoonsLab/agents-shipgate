"""A nesting bound for the host configuration the static readers parse (#1154).

The parsers accept nesting the readers then cannot walk: `json.loads` returns
a 5,000-deep array, and the recursive redaction, digest and rule walks that
follow it raised ``RecursionError``, so `diff`, `check` and `audit --host`
crashed with a traceback and `verify` reported ``internal_error``. TOML and
YAML parsers recurse themselves and raise it while parsing.

:func:`bounded_parse` turns both into one typed refusal,
:class:`NestingLimitExceeded`, before any walk sees the document. Every reader
then reports the file as one it could not parse, naming it, the way it reports
any other file it cannot read. The bound sits far above any real host
configuration (a settings, MCP, hooks, Codex or workflow file nests well under
twenty levels) and far below what the walks can take.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: The deepest container nesting a host configuration may have. A top-level
#: object is one level; each object or array inside another adds one.
MAX_HOST_CONFIG_DEPTH = 64


class NestingLimitExceeded(ValueError):
    """A structured document nested deeper than :data:`MAX_HOST_CONFIG_DEPTH`."""

    def __init__(self, limit: int = MAX_HOST_CONFIG_DEPTH) -> None:
        self.limit = limit
        super().__init__(f"nested more than {limit} levels deep")


def deeper_than(value: Any, limit: int = MAX_HOST_CONFIG_DEPTH) -> bool:
    """Whether ``value`` nests objects or arrays more than ``limit`` levels deep.

    Iterative, so it cannot itself recurse too deep. A container reached again
    (a YAML alias shares one) is walked again only from a deeper level, so
    shared and cyclic structures cost at most ``limit`` visits per container:
    a cycle deepens on every pass and is refused once it passes the bound.
    """

    deepest: dict[int, int] = {}
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > limit:
            return True
        if deepest.get(id(item), 0) >= depth:
            continue
        deepest[id(item)] = depth
        stack.extend((child, depth + 1) for child in children)
    return False


def bounded_parse(
    loads: Callable[[str], Any], text: str, *, limit: int = MAX_HOST_CONFIG_DEPTH
) -> Any:
    """``loads(text)``, refused with :class:`NestingLimitExceeded` past the depth bound."""

    try:
        value = loads(text)
    except RecursionError:
        raise NestingLimitExceeded(limit) from None
    if deeper_than(value, limit):
        raise NestingLimitExceeded(limit)
    return value


__all__ = [
    "MAX_HOST_CONFIG_DEPTH",
    "NestingLimitExceeded",
    "bounded_parse",
    "deeper_than",
]
