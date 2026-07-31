from __future__ import annotations

from pathlib import Path

import pytest

from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    reset_static_input_snapshot,
)
from agents_shipgate.inputs.common import walk_input_tree


def test_recursive_input_inventory_rejects_late_nested_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    snapshot = StaticInputSnapshot(tmp_path)
    token = activate_static_input_snapshot(snapshot)
    try:
        assert walk_input_tree(source) == [nested]
        (nested / "late.json").write_text("{}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="changed (?:identity|entries)"):
            snapshot.finish()
    finally:
        reset_static_input_snapshot(token)
