"""Caller arguments keep the condition around the agent construction."""

import pytest

from tests.test_builder_bindings import _files, _read
from tests.test_imported_tool_bindings import _commit, _compare, _git, _write


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("negative", [False, True])
def test_conditional_construction_retains_arguments_and_caller_condition(tmp_path, framework, negative):
    body = (
        "    if enabled:\n        return None\n    else:\n        return Agent(name='Built', tools=tools)\n"
        if negative else "    if enabled:\n        return Agent(name='Built', tools=tools)\n    return None\n"
    )
    _write(tmp_path, _files(framework, "if selected:\n    a = build([read])\n", body=body))
    observations, warnings = _read(tmp_path, framework)
    (observation,) = observations
    assert warnings == [] and observation.tools_complete
    assert observation.tool_names == ["read"]
    conditions = observation.tool_conditions["read"]
    assert conditions == [
        f"the caller's condition `selected` holds and the construction's {'negated condition' if negative else 'condition'} `enabled` holds",
    ]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_conditional_construction_tool_addition_names_caller_and_definition(tmp_path, framework):
    body = "    if enabled:\n        return Agent(name='Built', tools=tools)\n    return None\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _files(framework, "a = build([read])\n", body=body))
    head = _commit(tmp_path, _files(framework, "a = build([read, write])\n", body=body))
    result = _compare(tmp_path, base, head, "--scope", ".")
    row = next(row for row in result["rows"] if row["agent"] == "Built" and row["tool"] == "write")
    assert row["change"] == "added"
    assert row["after"]["bound_when"] == ["the construction's condition `enabled` holds"]
    assert "app.py:4" in row["after"]["construction_sites"]
    assert row["after"]["definition"]["source"] == "tools.py"


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("wrapper", ["if False", "for item in items", "while enabled"])
def test_unsupported_or_unreachable_construction_keeps_its_limit(tmp_path, framework, wrapper):
    body = f"    {wrapper}:\n        return Agent(name='Built', tools=tools)\n    return None\n"
    _write(tmp_path, _files(framework, "a = build([read])\n", body=body))
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not observation.tools_complete for observation in observations)
