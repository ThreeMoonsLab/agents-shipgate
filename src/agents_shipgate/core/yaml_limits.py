"""Resource bounds for static YAML reads that retain SafeLoader semantics."""

from __future__ import annotations

from itertools import chain
from typing import Any

import yaml

MAX_YAML_DEPTH = 64
MAX_YAML_NODES = 100_000


class YamlLimitError(yaml.YAMLError):
    """The static YAML reader could not bound the document."""


class BoundedSafeLoader(yaml.SafeLoader):
    """Check source and expanded node counts before constructing values."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._composition_depth = 0
        self._nodes = 0

    def compose_node(self, parent: yaml.Node | None, index: Any) -> yaml.Node:
        self._nodes += 1
        if self._nodes > MAX_YAML_NODES:
            raise YamlLimitError(f"YAML exceeds the {MAX_YAML_NODES:,}-node limit")
        self._composition_depth += 1
        try:
            if self._composition_depth > MAX_YAML_DEPTH:
                raise YamlLimitError(f"YAML exceeds the nesting limit of {MAX_YAML_DEPTH}")
            return super().compose_node(parent, index)
        finally:
            self._composition_depth -= 1

    def get_single_node(self) -> yaml.Node | None:
        node = super().get_single_node()
        if node is not None:
            _check_expansion(node)
        return node


def _check_expansion(root: yaml.Node) -> None:
    active: set[yaml.Node] = set()
    measured: dict[yaml.Node, tuple[int, int]] = {}

    def measure(node: yaml.Node, depth: int) -> tuple[int, int]:
        if node in active:
            raise YamlLimitError("recursive YAML aliases are unsupported")
        if depth > MAX_YAML_DEPTH:
            raise YamlLimitError(f"YAML exceeds the nesting limit of {MAX_YAML_DEPTH}")
        if isinstance(node, yaml.ScalarNode):
            return 1, 1
        if node in measured:
            count, height = measured[node]
            if depth + height - 1 > MAX_YAML_DEPTH:
                raise YamlLimitError(f"YAML exceeds the nesting limit of {MAX_YAML_DEPTH}")
            return count, height

        active.add(node)
        count, height = 1, 1
        children = chain.from_iterable(node.value) if isinstance(node, yaml.MappingNode) else node.value
        for child in children:
            child_count, child_height = measure(child, depth + 1)
            count += child_count
            if count > MAX_YAML_NODES:
                raise YamlLimitError(f"expanded YAML exceeds the {MAX_YAML_NODES:,}-node limit")
            height = max(height, child_height + 1)
        active.remove(node)
        measured[node] = count, height
        return count, height

    measure(root, 1)
