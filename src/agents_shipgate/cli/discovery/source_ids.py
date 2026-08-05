"""``tool_sources[].id`` derivation for the two ``init`` renderers.

One helper, shared by the auto renderer (``template.py``) and the legacy
``--minimal`` renderer (``artifacts.py``), because both had the same
basename-only rule and therefore the same defect: repeated basenames are
the norm in Python packages (``strix/tools/finish/tool.py``,
``strix/tools/respond/tool.py``, several ``*/tools.py`` modules), and one
id per basename made every such repository render a manifest the schema
rejects — ``tool_sources[].id values must be unique``. The auto path
caught that at its validation gate and wrote nothing; ``--minimal`` had no
gate and wrote the invalid file, so the documented next step (``scan``)
failed with a config error. See issue #307.

Ids are derived from the whole workspace-relative path and carry no
positional component: no ``_2``/``_3`` counter, so a file appearing
earlier in the walk can never renumber the entries after it.

**Stability guarantee, exactly.** An id is a pure function of its own
path *unless another declared path sanitizes to the same id* — sanitizing
is lossy (``a-b/`` and ``a_b/`` both fold to ``a_b``; so do ``a/b_c.py``
and ``a_b/c.py``). Those groups are disambiguated by digest, so adding
such a path and regenerating from scratch changes the ids of every member
of the group, not just the newcomer. Making that case file-local would
mean putting a digest on *every* id, including the common ones this
scheme exists to keep readable. The blast radius is bounded: ``init``
refuses to overwrite an existing ``shipgate.yaml``, so ids are assigned
once per adoption, and the fold classes are rare in one workspace.
``tests/test_init_auto.py`` pins both halves — non-colliding siblings
never shift an id; a colliding one shifts both.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import PurePosixPath

# Short, stable prefixes so a generated id reads as "which adapter, which
# file". Unlisted source types (including third-party adapters) use their
# own type name.
SOURCE_ID_PREFIXES: dict[str, str] = {
    "langchain": "lc",
    "crewai": "crewai",
    "google_adk": "adk",
    "openai_agents_sdk": "openai_sdk",
    "mcp": "mcp",
    "openapi": "openapi",
    "codex_plugin": "codex_plugin",
}

# Generated ids travel into findings and report output, so a deep monorepo
# path is truncated to the most specific segments that fit and made unique
# again by a digest of the full path. The bound holds for every id this
# module returns, disambiguated ones included.
MAX_SOURCE_ID_LENGTH = 64

_DIGEST_LENGTH = 8
_NON_ID_CHARS = re.compile(r"[^a-z0-9]+")


def source_id_for(source_type: str, relative_path: str) -> str:
    """Deterministic id for one detected source file.

    ``relative_path`` is the workspace-relative path as written to the
    manifest's ``path:`` field. Never longer than
    :data:`MAX_SOURCE_ID_LENGTH`.
    """
    return _compose(source_type, relative_path, disambiguate=False)


def assign_source_ids(entries: Sequence[tuple[str, str]]) -> list[str]:
    """Ids for ``(source_type, relative_path)`` pairs, unique by construction.

    Distinct paths can still sanitize to one id (``a-b/tools.py`` and
    ``a_b/tools.py``). Every member of such a group takes a digest of its
    own path — rather than one side keeping the plain form — so which file
    was walked first never decides which one gets renamed. See the module
    docstring for what that costs in stability.
    """
    ids = [source_id_for(source_type, path) for source_type, path in entries]
    collided = {value for value, count in Counter(ids).items() if count > 1}
    if not collided:
        return ids
    resolved = [
        _compose(source_type, path, disambiguate=True) if value in collided else value
        for value, (source_type, path) in zip(ids, entries, strict=True)
    ]
    if len(set(resolved)) == len(resolved):
        return resolved
    # A plain id can equal another entry's disambiguated form when a file
    # is named after that digest (`a_b_spec_<digest of a-b/spec.yaml>`).
    # Contrived, but constructible — and `--minimal` has no validation
    # gate behind it, so give every entry a digest rather than let a
    # duplicate reach the render.
    return [
        _compose(source_type, path, disambiguate=True) for source_type, path in entries
    ]


def _compose(source_type: str, relative_path: str, *, disambiguate: bool) -> str:
    prefix = SOURCE_ID_PREFIXES.get(source_type, source_type)
    segments = _path_segments(relative_path)
    plain = "_".join([prefix, *segments]) if segments else f"{prefix}_root"
    if not disambiguate and len(plain) <= MAX_SOURCE_ID_LENGTH:
        return plain
    return _with_digest(prefix, segments, relative_path)


def _with_digest(prefix: str, segments: list[str], relative_path: str) -> str:
    """``prefix`` + the most specific segments that fit + a path digest.

    The digest is what keeps two truncated (or two identically sanitized)
    paths apart, so the length budget is computed with room for it — a
    source-type prefix long enough to crowd it out is truncated too, which
    keeps the bound true for third-party adapter names of any length.
    """
    digest = _digest(relative_path)
    prefix = prefix[: MAX_SOURCE_ID_LENGTH - len(digest) - 1]
    budget = MAX_SOURCE_ID_LENGTH - len(prefix) - len(digest) - 2
    kept: list[str] = []
    width = 0
    for segment in reversed(segments):
        if width + len(segment) > budget:
            break
        kept.insert(0, segment)
        width += len(segment) + 1
    return "_".join([prefix, *kept, digest])


def _path_segments(relative_path: str) -> list[str]:
    pure = PurePosixPath(relative_path)
    parts = [*pure.parts[:-1], pure.stem] if pure.parts else []
    return [segment for segment in (_slug(part) for part in parts) if segment]


def _slug(value: str) -> str:
    return _NON_ID_CHARS.sub("_", value.lower()).strip("_")


def _digest(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
