"""Computed names have source identity, without invented runtime identity."""

from __future__ import annotations

import ast

import pytest

from agents_shipgate.inputs.agent_construction_identity import construction_identities
from agents_shipgate.inputs.python_imports import ScopeIndex
from tests.test_application_diff import SDK, commit, git, run


def identities(source):
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Agent"]
    return construction_identities(calls, ScopeIndex(tree), "agent.py", set())


@pytest.mark.parametrize("construction", [
    "def build():\n    return Agent(name=f'helper-{suffix}', tools=[lookup])\n",
    "class Helper:\n    def __init__(self):\n        self.agent = Agent(name=f'helper-{suffix}', tools=[lookup])\n",
])
def test_source_label_survives_line_move(construction):
    before, refusals = identities(construction)
    after, other_refusals = identities(construction.replace("    return", "    marker = 1\n    return").replace("        self.agent", "        marker = 1\n        self.agent"))
    assert list(before.values()) == list(after.values())
    assert not refusals and not other_refusals


def test_two_unnamed_sites_are_ambiguous_instead_of_numbered():
    source = "def build():\n    if enabled:\n        return Agent(name=first, tools=[lookup])\n    return Agent(name=second, tools=[execute])\n"
    labels, refusals = identities(source)
    assert not labels and len(refusals) == 2
    assert all("join distinct" in reason for reason in refusals.values())


def test_attribute_construction_can_move_between_class_methods():
    before, refusals = identities("class Helper:\n    def __init__(self):\n        self.agent = Agent(name=value, tools=[])\n")
    after, other_refusals = identities("class Helper:\n    def build(self):\n        self.agent = Agent(name=value, tools=[])\n")
    assert list(before.values()) == list(after.values()) == ["Helper.self.agent@agent.py"]
    assert not refusals and not other_refusals


def test_attribute_constructions_in_two_methods_are_ambiguous():
    labels, refusals = identities("class Helper:\n    def first(self):\n        self.agent = Agent(name=value)\n    def second(self):\n        self.agent = Agent(name=other)\n")
    assert not labels and len(refusals) == 2


def test_an_attribute_outside_a_class_keeps_its_function_scope():
    labels, refusals = identities("def build(self):\n    self.agent = Agent(name=value)\n")
    assert list(labels.values()) == ["build.self.agent@agent.py"]
    assert not refusals


def test_shared_local_names_are_qualified():
    source = "def a():\n    agent = Agent(name=value, tools=[])\ndef b():\n    agent = Agent(name=value, tools=[])\n"
    labels, refusals = identities(source)
    assert set(labels.values()) == {"a.agent@agent.py", "b.agent@agent.py"}
    assert not refusals


def test_fallback_cannot_collide_with_a_literal_identity():
    tree = ast.parse("def build():\n    return Agent(name=value)\n")
    (call,) = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    labels, refusals = construction_identities([call], ScopeIndex(tree), "agent.py", {"build@agent.py"})
    assert not labels and "join distinct" in refusals[id(call)]


def source(name="f'helper-{suffix}'", builder="build", tools="[lookup]"):
    return SDK[:SDK.index("agent =")] + f"def {builder}():\n    return Agent(name={name}, tools={tools})\n\nagent = {builder}()\n"


def test_computed_builder_is_identified_and_line_move_is_not_add_remove(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    text = source()
    base = commit(tmp_path, {"agent.py": text})
    head = commit(tmp_path, {"agent.py": text.replace("    return Agent", "    marker = 1\n    return Agent")})
    result = run(tmp_path, base, head)
    assert any(agent["name"] == "build@agent.py" for agent in result["head"]["agents"])
    assert not any(row["change"] in {"added", "removed"} for row in result["rows"])


@pytest.mark.parametrize("before,after", [
    (source(), source(builder="construct")),
    (source(name="'Literal'"), source()),
])
def test_identity_basis_rename_is_ambiguous_not_an_add_and_remove(tmp_path, before, after):
    git(tmp_path, "init", "-q", "-b", "main")
    base = commit(tmp_path, {"agent.py": before})
    head = commit(tmp_path, {"agent.py": after})
    result = run(tmp_path, base, head)
    assert result["rows"]
    assert result["comparison_status"] == "partial"
    assert all(row["change"] == "not_established" for row in result["rows"])
    assert any("correspondence" in limit for limit in result["head"]["limits"])


@pytest.mark.parametrize("reverse", [False, True])
def test_unread_factory_at_a_source_identity_is_not_added_or_removed(tmp_path, reverse):
    git(tmp_path, "init", "-q", "-b", "main")
    before, after = source(), source().replace("return Agent(", "return create(")
    if reverse:
        before, after = after, before
    base = commit(tmp_path, {"agent.py": before})
    head = commit(tmp_path, {"agent.py": after})
    result = run(tmp_path, base, head)
    assert result["comparison_status"] == "partial"
    assert result["rows"] and all(row["change"] == "not_established" for row in result["rows"])
    assert any("build@agent.py" in limit for limit in result["base" if reverse else "head"]["limits"])


def test_handoff_target_source_label_rename_is_not_an_add_and_remove(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    text = SDK[:SDK.index("agent =")] + """def build():
    worker = Agent(name=f'helper-{suffix}', tools=[lookup])
    triage = Agent(name='triage', handoffs=[worker])
    return triage
def other():
    worker = Agent(name=f'other-{suffix}', tools=[execute])
    return worker
agent = build()
second = other()
"""
    base = commit(tmp_path, {"agent.py": text})
    head = commit(tmp_path, {"agent.py": text.replace("build", "construct")})
    result = run(tmp_path, base, head)
    handoffs = [row for row in result["rows"] if (row["before"] or row["after"])["edge_type"] == "handoff"]
    assert len(handoffs) == 2
    assert all(row["change"] == "not_established" for row in handoffs)
    assert all(any("correspondence" in reason for reasons in row["uncertainty"].values() for reason in reasons) for row in handoffs)


def test_new_computed_construction_does_not_make_a_false_identity_rename(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    base = commit(tmp_path, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(tmp_path, {"agent.py": SDK.replace("TOOLS", "[lookup]") + "\nAgent(name=f'helper-{suffix}', tools=[execute])\n"})
    result = run(tmp_path, base, head)
    assert not any("identity changed" in limit for limit in result["head"]["limits"])


def test_source_file_move_with_a_comment_is_ambiguous_not_added_and_removed(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    text = source()
    base = commit(tmp_path, {"before.py": text})
    (tmp_path / "before.py").unlink()
    head = commit(tmp_path, {"after.py": text + "# moved\n"})
    result = run(tmp_path, base, head)
    assert result["comparison_status"] == "partial"
    assert result["rows"] and all(row["change"] == "not_established" for row in result["rows"])
    assert any("correspondence" in reason for reason in result["head"]["limits"])


def test_two_template_classes_keep_named_method_limits(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    text = SDK[:SDK.index("agent =")] + """class First:
    def __init__(self):
        self.agent = Agent(name=self.get_name(), tools=self.get_tools())
    def get_name(self):
        return 'first'
    def get_tools(self):
        return [lookup]
class Second:
    def __init__(self):
        self.agent = Agent(name=self.get_name(), tools=self.get_tools())
    def get_name(self):
        return 'second'
    def get_tools(self):
        return [execute]
"""
    base = commit(tmp_path, {"agent.py": text})
    head = commit(tmp_path, {"agent.py": text.replace("return [execute]", "return [lookup, execute]")})
    result = run(tmp_path, base, head)
    assert result["comparison_status"] == "partial"
    names = {agent["name"] for agent in result["head"]["agents"]}
    assert names == {"First.self.agent@agent.py", "Second.self.agent@agent.py"}
    assert any("get_tools" in limit for limit in result["head"]["limits"])
