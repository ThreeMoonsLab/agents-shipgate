"""Enumerate every filesystem path a manifest declares an adapter will read.

``verification_identity`` needs this when it cannot observe the adapter read
boundary directly — a plan prepared without evaluating policy
(``verification prepare``), or a committed-tree ``verify`` whose scan reads an
archived snapshot rather than the worktree the static-input snapshot is bound
to. In those modes the declared list *is* the input set: a path that never
becomes a plan blob can change bytes without moving ``input_set_id``, which is
exactly the claim ``verification-plan.json``, ``verify-run.json``, the terminal
receipt, and attestations exist to make.

The enumeration runs on the raw mapping produced by ``yaml.safe_load`` rather
than on ``AgentsShipgateManifest``, because request identity must still be
constructible for a manifest that fails validation. It therefore has to
recognize declared paths structurally. Two rules do that:

1. a ``path:`` key anywhere inside a path-bearing block — the shape every
   ``ArtifactPathConfig`` field accepts; and
2. any key named after a field whose type carries an ``ArtifactPathConfig``,
   because ``_parse_artifact_entries`` also accepts a bare string in place of
   that object (``tools: [tools/openai.json]``).

The field names for rule 2 are read off the manifest models at import time, so
a new artifact list on an existing block — or a whole new framework block — is
covered without editing this module. Only the handful of path fields that are
*not* typed as ``ArtifactPathConfig`` need naming, in ``_UNTYPED_PATH_FIELDS``.

Scope is deliberately "paths an adapter opens", which is the same boundary the
observed branch captures. Three declared paths are therefore left out:

- ``output.directory`` is where reports are written, not an input;
- ``organization.audit.registry`` is existence-tested and never read, so
  hashing its bytes would churn identity on edits that cannot change a
  decision; and
- ``baseline.audit_log`` is an append-only side log resolved against the
  baseline file, not an adapter input.
"""

from __future__ import annotations

from typing import Any, get_args

from agents_shipgate.schemas.manifest._artifacts import ArtifactPathConfig
from agents_shipgate.schemas.manifest.root import AgentsShipgateManifest

#: Declared paths whose field type is a plain string rather than an
#: ``ArtifactPathConfig``, keyed by the top-level block they live under. An
#: empty set registers a block for the ``path:`` rule alone —
#: ``tool_sources[].path`` is a bare ``str`` field, not an artifact object.
_UNTYPED_PATH_FIELDS: dict[str, frozenset[str]] = {
    # agent.sdk.entrypoint is the OpenAI Agents SDK entrypoint used when a
    # tool_sources entry of that type omits its own path.
    "agent": frozenset({"entrypoint"}),
    # prompt_files accepts {path: ...} but collapses to list[str] on load, so
    # it is not ArtifactPathConfig-typed and cannot be derived below.
    "anthropic": frozenset({"prompt_files"}),
    "openai_api": frozenset({"prompt_files"}),
    "tool_sources": frozenset(),
}


def _carries_artifact_path(annotation: Any) -> bool:
    if isinstance(annotation, type):
        return issubclass(annotation, ArtifactPathConfig)
    return any(_carries_artifact_path(arg) for arg in get_args(annotation))


def _nested_models(annotation: Any) -> list[type]:
    if isinstance(annotation, type):
        return [annotation] if hasattr(annotation, "model_fields") else []
    return [model for arg in get_args(annotation) for model in _nested_models(arg)]


def _artifact_field_names(model: type, seen: set[type]) -> set[str]:
    """Every YAML key under ``model`` whose value declares an artifact path."""

    if model in seen:
        return set()
    seen.add(model)
    names: set[str] = set()
    for name, field in model.model_fields.items():
        if _carries_artifact_path(field.annotation):
            # populate_by_name means both spellings load; openai_api's
            # api_model_config is aliased to `model_config` in YAML.
            names.add(name)
            if field.alias:
                names.add(field.alias)
            continue
        for nested in _nested_models(field.annotation):
            names |= _artifact_field_names(nested, seen)
    return names


def _derive_blocks() -> dict[str, frozenset[str]]:
    blocks = {block: set(names) for block, names in _UNTYPED_PATH_FIELDS.items()}
    for block, field in AgentsShipgateManifest.model_fields.items():
        names: set[str] = set()
        if _carries_artifact_path(field.annotation):
            names.add(block)
        for nested in _nested_models(field.annotation):
            if not issubclass(nested, ArtifactPathConfig):
                names |= _artifact_field_names(nested, set())
        if names:
            blocks.setdefault(block, set()).update(names)
    return {block: frozenset(names) for block, names in sorted(blocks.items())}


#: Manifest blocks that declare adapter inputs, mapped to the keys inside them
#: that may carry a bare path string. Derived from the manifest models; see the
#: module docstring.
DECLARED_INPUT_PATH_BLOCKS: dict[str, frozenset[str]] = _derive_blocks()


def declared_manifest_input_paths(raw: Any) -> list[str]:
    """Return every declared input path in ``raw``, in stable sorted order.

    ``raw`` is a ``yaml.safe_load`` result and may be any shape; anything that
    is not a mapping yields an empty list. Paths are returned exactly as
    declared — resolution and containment are the caller's boundary.
    """

    if not isinstance(raw, dict):
        return []
    found: list[str] = []
    for block, path_keys in DECLARED_INPUT_PATH_BLOCKS.items():
        _walk(raw.get(block), path_keys=path_keys, found=found)
    return sorted(set(found))


def _walk(node: Any, *, path_keys: frozenset[str], found: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "path" or key in path_keys:
                _collect(value, found)
            else:
                _walk(value, path_keys=path_keys, found=found)
    elif isinstance(node, list):
        for item in node:
            _walk(item, path_keys=path_keys, found=found)


def _collect(value: Any, found: list[str]) -> None:
    """Collect the declared path(s) in a bare string / {path: ...} / list."""

    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        candidate = value.get("path")
        if isinstance(candidate, str):
            found.append(candidate)
    elif isinstance(value, list):
        for item in value:
            _collect(item, found)


__all__ = ["DECLARED_INPUT_PATH_BLOCKS", "declared_manifest_input_paths"]
