"""Refusal text follows the selected actual import, never another proof."""

import ast

import pytest

from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit
from agents_shipgate.inputs.list_expressions import ListExpressions
from agents_shipgate.inputs.python_imports import ImportResolver, ScopeIndex
from tests.test_builder_calls import _source_slot_fixture
from tests.test_builder_calls import namespace_workspace as namespace_workspace


@pytest.mark.parametrize("extra, expected", [("", True), ("fake.Agent([])\n", False)])
def test_actual_source_slot_outcome_is_unchanged_and_only_refusals_are_collected(namespace_workspace, extra, expected):
    resolver, module, statement = _source_slot_fixture(namespace_workspace, extra)
    calls = BuilderCalls(resolver)
    home, function, _ = calls._source_slot_candidate(module, statement)
    refused = []
    assert calls.uncalled_source_slot(home, function, refused_models=refused) is expected
    if expected:
        assert refused == []
    else:
        assert len(refused) == 2
        assert refused[0].startswith("agents: ") and refused[1].startswith("google.adk: ")
        assert all(f"{module.ref}:{statement.lineno}: " in row for row in refused)
        assert all(len(row) <= 1024 for row in refused)
    assert not resolver._checking_source_module_slot


def test_second_genuine_model_success_discards_first_model_refusal(namespace_workspace, monkeypatch):
    resolver, module, statement = _source_slot_fixture(namespace_workspace)
    calls = BuilderCalls(resolver)
    home, function, _ = calls._source_slot_candidate(module, statement)
    original = BuilderCalls.idempotent_source_slot
    attempted = []

    def decline_first(self, caller, node, family):
        result = original(self, caller, node, family)
        attempted.append(family)
        if family == "agents":
            raise CallLimit("first model declined after its genuine proof")
        return result

    monkeypatch.setattr(BuilderCalls, "idempotent_source_slot", decline_first)
    refused = []
    assert calls.uncalled_source_slot(home, function, refused_models=refused)
    assert attempted == ["agents", "google.adk"] and refused == []


def test_selected_import_context_survives_cache_hits_without_same_line_collision(namespace_workspace, monkeypatch):
    root = namespace_workspace
    (root / "tools.py").write_text("def read():\n    return 1\nBASE = [read]\nOTHER = [read]\n")
    text = "from tools import BASE, OTHER\nopaque(BASE)\nopaque(OTHER)\n"
    path = root / "app.py"
    path.write_text(text)
    reader = ImportResolver(root)
    module = reader.entry(path, ast.parse(text), text)
    defining = reader.module(root / "tools.py")
    lists = ListExpressions(ref=module.ref, tree=module.tree, scopes=ScopeIndex(module.tree),
                            bindings=module.bindings, module=module, resolver=reader,
                            agent_reads=lambda call, keyword: False)
    view = lists.entry
    statement = module.tree.body[0]
    first, second = statement.names
    view.changed_import_context[first, statement] = ("agents: selected first import",)
    view.changed_import_context[second, statement] = ("google.adk: selected second import",)
    original = lists._first_change_reaching
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(lists, "_first_change_reaching", counted)
    assert lists._changed_through(view, defining, "BASE") == statement.lineno
    first_suffix = lists._change_refusal_suffix(view, defining, "BASE")
    assert "selected first import" in first_suffix and "selected second import" not in first_suffix
    assert first_suffix.startswith(" Additional constructor refusal context: ")
    view.changed_import_context[first, statement] = ("agents: later unrelated refusal",)
    assert lists._changed_through(view, defining, "BASE") == statement.lineno
    assert calls == 1 and lists._change_refusal_suffix(view, defining, "BASE") == first_suffix
    assert lists._changed_through(view, defining, "OTHER") == statement.lineno
    second_suffix = lists._change_refusal_suffix(view, defining, "OTHER")
    assert "selected second import" in second_suffix and "selected first import" not in second_suffix
    assert calls == 2
    assert lists._changed_through(view, defining, "MISSING") is None
    assert lists._change_refusal_suffix(view, defining, "MISSING") == ""


def _lists(root, text):
    path = root / "app.py"
    path.write_text(text)
    reader = ImportResolver(root)
    module = reader.entry(path, ast.parse(text), text)
    return ListExpressions(ref=module.ref, tree=module.tree, scopes=ScopeIndex(module.tree),
                           bindings=module.bindings, module=module, resolver=reader,
                           agent_reads=lambda call, keyword: keyword == "tools")


def test_successful_genuine_callee_discards_rejected_agent_read_context(namespace_workspace, monkeypatch):
    lists = _lists(namespace_workspace, "def count_items(items):\n    return len(items)\nBASE = []\ncount_items(BASE)\n")
    call = lists.entry.tree.body[-1].value
    original = lists._reads_agent

    def with_refusal(view, current, keyword, *, refused_context=None):
        result = original(view, current, keyword)
        assert not result
        if refused_context is not None:
            refused_context.append("agents: declined consumer model")
        return result

    monkeypatch.setattr(lists, "_reads_agent", with_refusal)
    refused = []
    assert lists._call_reads(lists.entry, refused_reads=refused)(call, 0, None)
    assert refused == []


@pytest.mark.parametrize("matching", [True, False])
def test_constructor_refusal_requires_the_actual_result_key(namespace_workspace, monkeypatch, matching):
    lists = _lists(namespace_workspace, "from agents import Agent\na = Agent(name='App', tools=[])\n")
    call = lists.entry.tree.body[-1].value
    key = ((1, 0, 0, 0, 0, 0, 0), ((), ()))
    other_key = ((2, 0, 0, 0, 0, 0, 0), ((), ()))
    lists._constructor_namespace_result = (key, True)
    lists._constructor_namespace_refusal = (key if matching else other_key, ("agents: selected model",))
    attempts = 0

    def existing_refusal():
        nonlocal attempts
        attempts += 1
        return True

    monkeypatch.setattr(lists, "constructor_namespace_changed", existing_refusal)
    refused = []
    assert not lists._reads_agent(lists.entry, call, "tools", refused_context=refused)
    assert attempts == 1
    if matching:
        assert refused == ["agents: selected model"]
    else:
        assert refused == [f"constructor read in {lists.entry.ref}: retained constructor namespace ownership census refused"]
        assert "agents: selected model" not in refused
