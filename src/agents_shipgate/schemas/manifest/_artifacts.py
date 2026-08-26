from __future__ import annotations

import posixpath
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agents_shipgate.schemas.manifest._common import (
    STRICT_MODEL_CONFIG,
    describe_yaml_shape,
)


class ArtifactPathConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    path: str
    optional: bool = False

    @field_validator("path", mode="before")
    @classmethod
    def canonical_path(cls, value: Any) -> Any:
        """One spelling per file, decided where the file is declared.

        ``./tools.json`` and ``tools.json`` are the same artifact, and the
        loaders carry the spelling into ``source_ref`` — so declaring both read
        one file twice and produced *two* canonical tools, with no duplicate
        detected and no warning (#329 review 3). Collapsing the spelling at the
        boundary makes them one declaration, which the existing duplicate check
        then sees.

        Left alone when the value carries a backslash: a Windows-style path is
        not this function's to reinterpret, and `posixpath` would not normalize
        it correctly anyway. ``../outside.yaml`` is preserved exactly, so the
        containment checks downstream still see what the author wrote.
        """

        if not isinstance(value, str) or not value.strip() or "\\" in value:
            return value
        return posixpath.normpath(value)


class NamedArtifactPathConfig(ArtifactPathConfig):
    name: str | None = None
    downstream_critical_fields: list[str] = Field(default_factory=list)


def _parse_artifact_entries(value: Any) -> list[ArtifactPathConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            "must be a list of artifact paths, but is "
            f"{describe_yaml_shape(value)}"
        )
    entries: list[ArtifactPathConfig] = []
    for index, item in enumerate(value):
        if isinstance(item, ArtifactPathConfig):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(ArtifactPathConfig(path=item))
        elif isinstance(item, dict):
            entries.append(ArtifactPathConfig.model_validate(item))
        else:
            raise ValueError(
                f"entry {index} must be a path string or an object with a "
                f"path, but is {describe_yaml_shape(item)}"
            )
    return entries


def _parse_named_artifact_entries(value: Any) -> list[NamedArtifactPathConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            "must be a list of artifact paths, but is "
            f"{describe_yaml_shape(value)}"
        )
    entries: list[NamedArtifactPathConfig] = []
    for index, item in enumerate(value):
        if isinstance(item, NamedArtifactPathConfig):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(NamedArtifactPathConfig(path=item))
        elif isinstance(item, dict):
            entries.append(NamedArtifactPathConfig.model_validate(item))
        else:
            raise ValueError(
                f"entry {index} must be a path string or an object with a "
                f"path, but is {describe_yaml_shape(item)}"
            )
    return entries


#: Manifest lists whose entries are read *for tools*. Everything else a
#: framework block declares — traces, eval sets, test cases, prompt files,
#: policy rules, credential stubs — is read for other reasons, and a path
#: repeated in one of those says nothing about a duplicate tool. Keying the
#: repeat set globally let ``openai_api.test_cases`` poison ``openai_api.tools``
#: and make a real in-file duplicate look like a repeated source (#329
#: review 3).
#:
#: ``tests/test_config.py`` asserts every artifact list on the manifest model is
#: either named here or deliberately excluded, so a new one cannot arrive
#: unclassified.
TOOL_PRODUCING_ARTIFACT_LISTS: frozenset[str] = frozenset(
    {
        "agent_configs",
        "function_schemas",
        "mcp_tool_inventories",
        "python_entrypoints",
        "tool_inventories",
        "tools",
        "workflows",
    }
)


def _normalized_declared_path(path: str) -> str:
    """One spelling for one file.

    ``tools.json`` and ``./tools.json`` are the same artifact declared twice,
    and comparing raw spellings reported no repeat for that pair while
    reporting a false one for ``./tools.json`` twice (#329 review 3).
    """

    return posixpath.normpath(path.replace("\\", "/"))


def repeated_declared_artifacts(manifest: BaseModel) -> frozenset[str]:
    """Normalized paths a single tool-producing list names more than once.

    A manifest that lists one file twice under, say, ``openai_api.tools`` is
    always a mistake, and it is the only place the mistake is *visible*: the
    loaders that aggregate every configured artifact into one source hand the
    catalog two identical observations with no record of having read the file
    twice, so the duplicate check downstream cannot tell that shape from a file
    that genuinely defines a tool twice (#329 review). Answering from the
    config keeps the two apart without teaching every loader to carry
    occurrence provenance.

    Counted *within* one list. Two entries in different lists are two
    declarations of different things, and neither is a repeat of the other.

    ``tool_sources`` is skipped and needs no help: each entry becomes its own
    source, so a repeat there is already two reads.
    """

    repeated: set[str] = set()

    def visit(value: Any, field_name: str) -> None:
        if isinstance(value, BaseModel):
            for name, item in value.__dict__.items():
                if name != "tool_sources":
                    visit(item, name)
            return
        if not isinstance(value, list):
            return
        counts_for_tools = field_name in TOOL_PRODUCING_ARTIFACT_LISTS
        seen: set[str] = set()
        for item in value:
            if counts_for_tools and isinstance(item, ArtifactPathConfig) and item.path:
                normalized = _normalized_declared_path(item.path)
                if normalized in seen:
                    repeated.add(normalized)
                seen.add(normalized)
            visit(item, field_name)

    visit(manifest, "")
    return frozenset(repeated)


class ToolInventoryConfig(ArtifactPathConfig):
    """A reviewed tool inventory, optionally bound to the source it completes.

    ``source_id`` names the ``tool_sources[].id`` (or framework-entrypoint
    source id) whose surface this file enumerates. Without it the inventory is
    an *independent* source: its entries become new observations that merely
    happen to share names with the statically-extracted ones, so the catalog
    grows and the ``incomplete_surface`` gap keyed to the original source is
    never satisfied (#386). With it, each named tool is joined to that source's
    observation through the one reviewed-binding engine in
    ``core/tool_identity.py`` — nothing is ever joined by name alone.

    Entries the completed source does not expose stay standalone observations:
    an inventory exists precisely because static extraction may have missed
    tools, and a tool nobody wired is honestly reported as unbound rather than
    silently attributed to an agent.
    """

    model_config = STRICT_MODEL_CONFIG

    source_id: str | None = None


def _parse_tool_inventory_entries(value: Any) -> list[ToolInventoryConfig]:
    """Parse ``<framework>.tool_inventories``, accepting the bare-path form."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            "must be a list of tool inventories, but is "
            f"{describe_yaml_shape(value)}"
        )
    entries: list[ToolInventoryConfig] = []
    for index, item in enumerate(value):
        if isinstance(item, ToolInventoryConfig):
            entries.append(item)
        elif isinstance(item, ArtifactPathConfig):
            entries.append(
                ToolInventoryConfig(path=item.path, optional=item.optional)
            )
        elif isinstance(item, str):
            entries.append(ToolInventoryConfig(path=item))
        elif isinstance(item, dict):
            entries.append(ToolInventoryConfig.model_validate(item))
        else:
            raise ValueError(
                f"entry {index} must be a path string or an object with a path "
                "(and optionally source_id, the tool source this inventory "
                f"completes), but is {describe_yaml_shape(item)}"
            )
    return entries
