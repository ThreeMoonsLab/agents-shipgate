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

Ids are derived from the whole workspace-relative path, so an id depends
only on the file it names. A positional suffix (``_2``, ``_3``) would
instead renumber existing entries whenever an unrelated file appears
earlier in the walk, churning anything keyed on the id.
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
# again by a digest of the full path.
MAX_SOURCE_ID_LENGTH = 64

_DIGEST_LENGTH = 8
_NON_ID_CHARS = re.compile(r"[^a-z0-9]+")


def source_id_for(source_type: str, relative_path: str) -> str:
    """Deterministic id for one detected source file.

    ``relative_path`` is the workspace-relative path as written to the
    manifest's ``path:`` field.
    """
    prefix = SOURCE_ID_PREFIXES.get(source_type, source_type)
    segments = _path_segments(relative_path)
    source_id = "_".join([prefix, *segments]) if segments else f"{prefix}_root"
    if len(source_id) <= MAX_SOURCE_ID_LENGTH:
        return source_id

    digest = _digest(relative_path)
    budget = MAX_SOURCE_ID_LENGTH - len(prefix) - len(digest) - 2
    kept: list[str] = []
    width = 0
    for segment in reversed(segments):
        if width + len(segment) > budget:
            break
        kept.insert(0, segment)
        width += len(segment) + 1
    return "_".join([prefix, *kept, digest])


def assign_source_ids(entries: Sequence[tuple[str, str]]) -> list[str]:
    """Ids for ``(source_type, relative_path)`` pairs, unique by construction.

    Distinct paths can still sanitize to one id (``a-b/tools.py`` and
    ``a_b/tools.py``). Both sides then take a digest of their own path
    rather than one side keeping the plain form, so which file was walked
    first never decides which one gets renamed.
    """
    ids = [source_id_for(source_type, path) for source_type, path in entries]
    collided = {value for value, count in Counter(ids).items() if count > 1}
    if not collided:
        return ids
    return [
        f"{value}_{_digest(path)}" if value in collided else value
        for value, (_source_type, path) in zip(ids, entries, strict=True)
    ]


def _path_segments(relative_path: str) -> list[str]:
    pure = PurePosixPath(relative_path)
    parts = [*pure.parts[:-1], pure.stem] if pure.parts else []
    return [segment for segment in (_slug(part) for part in parts) if segment]


def _slug(value: str) -> str:
    return _NON_ID_CHARS.sub("_", value.lower()).strip("_")


def _digest(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
