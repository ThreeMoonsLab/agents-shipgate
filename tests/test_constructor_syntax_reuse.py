"""Structural reuse never substitutes an ownership or identity outcome."""

import ast

import pytest

from agents_shipgate.inputs.python_imports import (
    REBOUND_NAME,
    ImportResolver,
    _external_constructor_use,
    _Stop,
)


def _module(reader, path, text):
    path.write_text(text)
    return reader.entry(path, ast.parse(text), text)


def test_actual_syntax_build_is_once_and_preserves_order(tmp_path, monkeypatch):
    reader = ImportResolver(tmp_path)
    text = "from agents import Agent\nimport foreign\nvalue = foreign.a.b\na = Agent(name='App', tools=[])\n"
    module = _module(reader, tmp_path / "one.py", text)
    reader._constructor_scope(module)
    original = ast.walk
    raw = tuple(original(module.tree))
    builds = 0

    def counted(tree):
        nonlocal builds
        if tree is module.tree:
            builds += 1
        return original(tree)

    monkeypatch.setattr(ast, "walk", counted)
    summary = reader._constructor_syntax(module)
    for _ in range(16):
        assert reader._constructor_syntax(module) is summary
    assert builds == 1 and summary.nodes == raw
    assert summary.imports == tuple(item for item in raw if isinstance(item, ast.Import | ast.ImportFrom))
    assert all(any(item is raw_node for raw_node in raw) for item in summary.references)
    assert any(isinstance(item, ast.Attribute) and item.attr == "b" for item in summary.references)
    assert not any(isinstance(item, ast.Attribute) and item.attr == "a" for item in summary.references)
    other = _module(reader, tmp_path / "two.py", text)
    assert reader._constructor_syntax(other) is not summary
    assert reader._constructor_syntax(other).nodes[0] is other.tree
    assert not any(item is other_node for item in summary.references for other_node in reader._constructor_syntax(other).references)


def test_reused_syntax_rechecks_actual_semantic_outcomes(tmp_path, monkeypatch):
    reader = ImportResolver(tmp_path)
    text = "from agents import Agent\na = Agent(name='App', tools=[])\n"
    module = _module(reader, tmp_path / "app.py", text)
    original = reader._constructor_reference
    calls = 0
    reject = False

    def counted(home, node, scopes):
        nonlocal calls
        calls += 1
        result = original(home, node, scopes)
        if reject and isinstance(node, ast.Name) and node.id == "Agent":
            return {**result, "local_constructor_provider": "fresh provider refusal"}
        return result

    monkeypatch.setattr(reader, "_constructor_reference", counted)
    assert _external_constructor_use(reader, module, "agents", {module.path}, allow_owner_routes=False) is None
    previous = calls
    reject = True
    with pytest.raises(_Stop) as caught:
        _external_constructor_use(reader, module, "agents", {module.path}, allow_owner_routes=False)
    assert calls > previous and caught.value.reason == REBOUND_NAME
    assert caught.value.detail == "fresh provider refusal"
