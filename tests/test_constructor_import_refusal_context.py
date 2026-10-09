"""Early constructor refusals remain negative and keep exact result context."""

import ast
from types import SimpleNamespace

import pytest

import agents_shipgate.inputs.list_expressions as expression_module
from agents_shipgate.inputs.list_expressions import _constructor_imports_unchanged
from agents_shipgate.inputs.python_imports import UNREADABLE_MODULE, _Stop
from tests.test_builder_calls import namespace_workspace as namespace_workspace
from tests.test_source_slot_refusal_context import _lists


def test_genuine_import_lookup_stop_keeps_its_original_negative_decision(namespace_workspace, monkeypatch):
    lists = _lists(namespace_workspace, "from agents import Agent\na = Agent(name='App', tools=[])\n")
    view = lists.entry
    view.builder_calls = lists.calls
    view.module_agent_reads = lambda module, call, keyword: False
    call = view.tree.body[-1].value
    reader = lists._resolver
    original = reader._imported_paths
    attempts = 0

    def unread(module, statement):
        nonlocal attempts
        attempts += 1
        original(module, statement)
        raise _Stop(UNREADABLE_MODULE, "selected captured import changed")

    monkeypatch.setattr(reader, "_imported_paths", unread)
    refused = []
    assert not _constructor_imports_unchanged(
        view, view, call, call, checked_modules=frozenset({view.module.path}),
        qualified="agents.Agent", family="agents", refused_context=refused,
    )
    assert attempts == 1
    assert len(refused) == 1 and UNREADABLE_MODULE in refused[0]
    assert "selected captured import changed" in refused[0]


def test_cached_negative_constructor_read_uses_only_its_same_key_context(namespace_workspace, monkeypatch):
    lists = _lists(namespace_workspace, "from agents import Agent\na = Agent(name='App', tools=[])\n")
    view = lists.entry
    view.builder_calls = lists.calls
    view.module_agent_reads = lambda module, call, keyword: False
    call = view.tree.body[-1].value
    original = expression_module._constructor_imports_unchanged
    attempts = 0

    def declined(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        assert original(*args, **kwargs)
        kwargs["refused_context"].append("constructor imports: selected model declined")
        return False

    monkeypatch.setattr(expression_module, "_constructor_imports_unchanged", declined)
    first = []
    assert not lists._reads_agent(view, call, "tools", refused_context=first)
    assert attempts == 1 and first == ["constructor imports: selected model declined"]
    key = next(iter(view.constructor_reads))
    view.constructor_read_contexts[("another key",)] = ("unrelated stale context",)
    again = []
    assert not lists._reads_agent(view, call, "tools", refused_context=again)
    assert attempts == 1 and again == first
    # The positive cache state must not publish its old negative sidecar.
    view.constructor_reads[key] = True
    positive = []
    assert lists._reads_agent(view, call, "tools", refused_context=positive)
    assert positive == [] and attempts == 1


def test_direct_constructor_result_context_is_bound_and_cleared_on_success_or_exception(namespace_workspace, monkeypatch):
    lists = _lists(namespace_workspace, "from agents import Agent\na = Agent(name='App', tools=[])\n")
    call = lists.entry.tree.body[-1].value
    invocation = SimpleNamespace(key=("selected",))
    other_invocation = SimpleNamespace(key=("other",))
    result = False
    attempts = 0

    def read(view, current, keyword, *, refused_context=None):
        nonlocal attempts
        attempts += 1
        if result == "exception":
            # A nested result must not survive an outer failure.
            lists._constructor_change_context = (call, invocation.key, ("nested stale context",))
            raise RuntimeError("selected proof stopped")
        if not result and refused_context is not None:
            refused_context.append("constructor imports: selected negative result")
        return result

    monkeypatch.setattr(lists, "_reads_agent", read)
    assert lists.constructor_changed(call, invocation)
    assert attempts == 1
    expected = ("constructor imports: selected negative result",)
    assert lists.constructor_refusal_context(call, invocation) == expected
    assert lists.constructor_refusal_context(call, other_invocation) == ()
    assert lists.constructor_refusal_context(ast.parse("a = Agent(tools=[])").body[0].value, invocation) == ()
    assert lists._invocation is None
    result = True
    assert not lists.constructor_changed(call, invocation)
    assert lists.constructor_refusal_context(call, invocation) == ()
    result = "exception"
    with pytest.raises(RuntimeError, match="selected proof stopped"):
        lists.constructor_changed(call, invocation)
    assert lists.constructor_refusal_context(call, invocation) == ()
    assert lists._invocation is None
    assert not lists.constructor_changed(call, None)
    assert lists.constructor_refusal_context(call, invocation) == ()


def test_namespace_owner_context_names_the_actual_expression_and_result_key(namespace_workspace, monkeypatch):
    lists = _lists(namespace_workspace, "from agents import Agent\na = Agent(name='App', tools=[])\n")
    module = lists.entry.module
    expression = module.tree.body[-1].value
    reader = lists._resolver
    reader._constructor_container_owners[id(expression)] = (module, expression)
    attempts = 0

    def changed(view, current):
        nonlocal attempts
        attempts += 1
        assert view.tree is module.tree and current is expression
        return True

    monkeypatch.setattr(lists, "_factory_result_changed", changed)
    assert lists.constructor_namespace_changed()
    result_key = lists._constructor_namespace_result[0]
    assert lists._constructor_namespace_refusal == (
        result_key, (f"constructor namespace owner container in {module.ref}:{expression.lineno} refused",),
    )
    assert attempts == 1
    assert lists.constructor_namespace_changed()
    assert attempts == 1


def test_cached_constructor_issue_is_read_without_query_and_only_for_the_exact_call(namespace_workspace, monkeypatch):
    lists = _lists(namespace_workspace, "from agents import Agent\na = Agent(name='App', tools=[])\n")
    module = lists.entry.module
    call = module.tree.body[-1].value
    builder = lists.calls
    key = (id(module.tree), id(call))
    builder._constructors[key] = "selected native constructor refused"

    def query(*args, **kwargs):
        raise AssertionError("cached context must not rerun constructor proof")

    monkeypatch.setattr(builder, "constructor_issue", query)
    assert builder.cached_constructor_issue(module, call) == "selected native constructor refused"
    assert builder.cached_constructor_issue(module, ast.parse("Agent(tools=[])").body[0].value) is None
    lists.entry.builder_calls = builder
    lists._module_agent_reads = lambda module, call, keyword: False
    lists._agent_reads = lambda call, keyword: False
    refused = []
    assert not lists._reads_agent(lists.entry, call, "tools", refused_context=refused)
    assert refused == [f"constructor read in {module.ref}: source consumer predicate did not recognize this construction",
                       f"constructor read in {module.ref}: cached constructor issue: selected native constructor refused"]
