"""Unresolved attribute fallback is bounded and always remains unread."""

from __future__ import annotations

import ast

import pytest

from agents_shipgate.inputs.python_imports import (
    MAX_STEPS,
    RESOLUTION_LIMIT,
    ImportResolver,
    _external_constructor_use,
    _Stop,
)


def _reader(root, text):
    path = root / "app.py"
    path.write_text(text)
    reader = ImportResolver(root)
    module = reader.entry(path, ast.parse(text), text)
    return reader, module


@pytest.mark.parametrize("depth", [40, 80, 320])
def test_unresolved_prefix_work_stops_with_named_obligation(tmp_path, monkeypatch, depth):
    text = "opaque." + ".".join(f"field{i}" for i in range(depth)) + "\nfrom agents import Agent\n"
    reader, module = _reader(tmp_path, text)
    original = reader._constructor_reference
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(reader, "_constructor_reference", counted)
    with pytest.raises(_Stop) as caught:
        _external_constructor_use(reader, module, "agents", {module.path}, allow_owner_routes=False)
    assert caught.value.reason == RESOLUTION_LIMIT
    assert "app.py:1" in caught.value.detail and "unresolved attribute path" in caught.value.detail
    assert calls <= MAX_STEPS


def test_known_terminal_projection_keeps_its_existing_semantic_route(tmp_path, monkeypatch):
    text = "from agents import Agent\nAgent." + ".".join(f"field{i}" for i in range(320)) + "\n"
    reader, module = _reader(tmp_path, text)
    original = reader._constructor_reference
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(reader, "_constructor_reference", counted)
    issue = _external_constructor_use(reader, module, "agents", {module.path}, allow_owner_routes=False)
    assert issue is None  # Preserve the existing bare-expression boundary.
    assert calls <= 2
