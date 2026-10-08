"""Private namespace readers reuse actual syntax without sharing proofs."""

import ast

import pytest

from agents_shipgate.inputs.python_imports import (
    REBOUND_NAME,
    ImportResolver,
    _CapturedImportSearchResolver,
    _external_constructor_use,
    _module,
    _RepositoryConstructorResolver,
    _Stop,
)
from tests.test_builder_calls import namespace_workspace as namespace_workspace


def _source(root):
    text = "from agents import Agent\na = Agent(name='App', tools=[])\n"
    path = root / "app.py"
    path.write_text(text)
    reader = ImportResolver(root)
    module = reader.entry(path, ast.parse(text), text)
    return reader, module


@pytest.mark.parametrize("kind", ["captured", "repository"])
def test_separate_readers_share_only_retained_actual_ast_roles(namespace_workspace, monkeypatch, kind):
    source, module = _source(namespace_workspace)
    if kind == "captured":
        original = module

        def build():
            reader = _CapturedImportSearchResolver(source, [module])
            return reader, module
    else:
        original = source._layout_module(module.ref)
        assert original is not None

        def build():
            reader = _RepositoryConstructorResolver(source)
            return reader, reader.module(reader.scope_root / module.ref)

    scope = source._constructor_scope(original)
    walk = ast.walk
    walks = 0

    def counted(tree):
        nonlocal walks
        if tree is original.tree:
            walks += 1
        return walk(tree)

    monkeypatch.setattr(ast, "walk", counted)
    summary = source._constructor_syntax(original)
    assert walks == 1
    first, wrapper = build()
    second, other_wrapper = build()
    # Wrapper construction still indexes its own module metadata. Count only
    # the subsequent scope/syntax queries this sharing change promises to reuse.
    before_queries = walks
    for reader, current in [(first, wrapper), (second, other_wrapper)]:
        assert current.tree is original.tree
        assert reader._constructor_scope(current) is scope
        assert reader._constructor_syntax(current) is summary
        assert reader._constructor_scopes is not source._constructor_scopes
        assert reader._constructor_syntax_trees is not source._constructor_syntax_trees
        assert reader._constructor_namespace_owners is not source._constructor_namespace_owners
        assert reader._constructor_member_sinks is not source._constructor_member_sinks
    assert walks == before_queries
    # Same path and bytes with a separately parsed tree is not the retained wrapper.
    clone = _module(wrapper.path, wrapper.ref, ast.parse(wrapper.text), wrapper.text)
    assert first._constructor_syntax(clone) is not summary
    assert first._constructor_scope(clone) is not scope


@pytest.mark.parametrize("kind", ["captured", "repository"])
def test_shared_syntax_never_substitutes_fresh_provider_outcomes(namespace_workspace, monkeypatch, kind):
    source, module = _source(namespace_workspace)
    if kind == "captured":
        reader = _CapturedImportSearchResolver(source, [module])
        current = module
    else:
        reader = _RepositoryConstructorResolver(source)
        current = reader.module(reader.scope_root / module.ref)
    source_summary = reader._constructor_syntax(current)
    original = reader._constructor_reference
    reject = False
    reads = 0

    def counted(home, node, scopes):
        nonlocal reads
        reads += 1
        result = original(home, node, scopes)
        if reject and isinstance(node, ast.Name) and node.id == "Agent":
            return {**result, "local_constructor_provider": "fresh wrapper provider refusal"}
        return result

    monkeypatch.setattr(reader, "_constructor_reference", counted)
    assert _external_constructor_use(reader, current, "agents", {current.path}, allow_owner_routes=False) is None
    before = reads
    reject = True
    with pytest.raises(_Stop) as caught:
        _external_constructor_use(reader, current, "agents", {current.path}, allow_owner_routes=False)
    assert reads > before and caught.value.reason == REBOUND_NAME
    assert caught.value.detail == "fresh wrapper provider refusal"
    assert reader._constructor_syntax(current) is source_summary
