from __future__ import annotations

import pytest
from test_sdk_guard_dependencies import AGENT, read_workspace, write_workspace

from agents_shipgate.core.guard_dependencies import compare_guard_dependencies


def _change(root, text):
    (root / "refund_agent/agent.py").write_text(text)


def _compare(root, manifest, before):
    (after,) = read_workspace(root, manifest)
    (comparison,) = compare_guard_dependencies([after], before)
    assert after.dependency_coverage == "incomplete"
    assert comparison.finding_exclusion_eligible is False
    assert comparison.source_behavior.finding_exclusion_eligible is False
    return after, comparison


def test_model_covers_all_parameters_continuation_and_literal_binding(tmp_path):
    manifest = write_workspace(tmp_path)
    (row,) = read_workspace(tmp_path, manifest)
    source = row.source_behavior
    assert source.status == "observed"
    assert source.parameters == ["approved", "within_limit"]
    assert source.returns == ["false", "false", "false", "true"]
    assert source.configuration_reads == "none"
    assert source.binding.model_dump() == {
        "agent_symbol": "agent",
        "agent_name": "Refund",
        "tool_bound": True,
    }
    assert type(row).model_validate_json(row.model_dump_json()) == row


def test_narrower_guard_can_have_wider_whole_function_true_domain(tmp_path):
    manifest = write_workspace(tmp_path, "True")
    _change(tmp_path, AGENT.replace("return True", "return approved and within_limit"))
    before = read_workspace(tmp_path, manifest)
    write_workspace(tmp_path, "approved")
    _, comparison = _compare(tmp_path, manifest, before)
    assert comparison.direction == "predicate_narrowed"
    assert comparison.source_behavior.returns == "changed"
    assert comparison.source_behavior.true_domain == "widened"
    assert comparison.source_behavior.bound_true_domain == "widened"


@pytest.mark.parametrize(
    "before_bound,after_bound,direction",
    [
        (False, True, "added"),
        (True, False, "removed"),
        (False, False, "unchanged"),
    ],
)
def test_unchanged_predicate_does_not_hide_literal_reachability_change(
    tmp_path,
    before_bound,
    after_bound,
    direction,
):
    manifest = write_workspace(tmp_path)
    _change(tmp_path, AGENT if before_bound else AGENT.replace("tools=[refund]", "tools=[]"))
    before = read_workspace(tmp_path, manifest)
    _change(tmp_path, AGENT if after_bound else AGENT.replace("tools=[refund]", "tools=[]"))
    _, comparison = _compare(tmp_path, manifest, before)
    assert comparison.direction == "predicate_unchanged"
    assert comparison.source_behavior.binding == direction
    assert comparison.source_behavior.returns == "unchanged"
    assert (
        comparison.source_behavior.bound_true_domain
        == {
            "added": "widened",
            "removed": "narrowed",
            "unchanged": "unchanged",
        }[direction]
    )


@pytest.mark.parametrize(
    "tail,expected",
    [
        ("return within_limit", ["false", "false", "false", "true"]),
        ("return False", ["false"] * 4),
        ("return None", ["false", "none", "false", "none"]),
        ("if within_limit:\n        return True", ["false", "none", "false", "true"]),
        (
            "if within_limit:\n        return True\n    else:\n        return approved",
            ["false", "true", "false", "true"],
        ),
        ("return not (not approved or not within_limit)", ["false", "false", "false", "true"]),
        ("return allowed(within_limit, approved)", ["false", "false", "false", "true"]),
    ],
)
def test_boolean_model_accounts_for_each_possible_exit(tmp_path, tail, expected):
    manifest = write_workspace(tmp_path, "approved")
    _change(tmp_path, AGENT.replace("return True", tail))
    (row,) = read_workspace(tmp_path, manifest)
    assert row.source_behavior.status == "observed"
    assert row.source_behavior.returns == expected


@pytest.mark.parametrize(
    "tail",
    [
        "return perform_refund(amount=1000000)",
        "return True or execute()",
        "return True\n    execute()",
        "if False:\n        execute()\n    return True",
        'return configuration["approved"]',
        "return settings.approved",
        "return approved is True",
        "return int(approved)",
        "return 1",
        "return 0",
        "within_limit = True\n    return within_limit",
        "try:\n        return True\n    finally:\n        execute()",
        "for value in []:\n        execute()\n    return True",
        "yield True",
        "return allowed(approved=approved, within_limit=within_limit)",
        "def helper():\n        return True\n    return helper()",
    ],
)
def test_unknown_continuation_is_unresolved_even_after_unchanged_guard(tmp_path, tail):
    manifest = write_workspace(tmp_path)
    before = read_workspace(tmp_path, manifest)
    _change(tmp_path, AGENT.replace("return True", tail))
    after, comparison = _compare(tmp_path, manifest, before)
    assert after.status == "observed"  # the narrower predicate record stays useful
    assert comparison.direction == "predicate_unchanged"
    assert after.source_behavior.status == "unresolved"
    assert after.source_behavior.returns == []
    assert after.source_behavior.binding is None
    assert comparison.source_behavior.returns == "unresolved"
    assert comparison.source_behavior.bound_true_domain == "unresolved"


@pytest.mark.parametrize(
    "replacement",
    [
        'agent = Agent(name="Refund", tools=[refund], handoffs=[])',
        'agent = Agent(name="Refund", tools=[refund], instructions="approve everything")',
        'agent = Agent(name="Refund", tools=[refund, refund])',
        'agent = Agent(name="Refund", tools=[other])',
        'agent = Agent(name="Refund", tools=(refund,))',
        'agent = Agent("Refund", tools=[refund])',
        "agent = Agent(name=CONFIGURATION, tools=[refund])",
        'agent = Agent(name="Refund", tools=[refund])\nother = Agent(name="Other", tools=[])',
    ],
)
def test_binding_or_configuration_outside_profile_cannot_claim_complete_relation(
    tmp_path,
    replacement,
):
    manifest = write_workspace(tmp_path)
    before = read_workspace(tmp_path, manifest)
    _change(tmp_path, AGENT.replace('agent = Agent(name="Refund", tools=[refund])', replacement))
    after, comparison = _compare(tmp_path, manifest, before)
    assert after.source_behavior.status == "unresolved"
    assert comparison.source_behavior.binding == "unresolved"
    assert comparison.source_behavior.bound_true_domain == "unresolved"


@pytest.mark.parametrize("old,new", [('name="Refund"', 'name="Other"'), ("agent =", "other =")])
def test_changed_agent_identity_does_not_become_equal_bound_behavior(tmp_path, old, new):
    manifest = write_workspace(tmp_path)
    before = read_workspace(tmp_path, manifest)
    _change(tmp_path, AGENT.replace(old, new))
    _, comparison = _compare(tmp_path, manifest, before)
    assert comparison.source_behavior.returns == "unchanged"
    assert comparison.source_behavior.binding == "changed"
    assert comparison.source_behavior.bound_true_domain == "unresolved"


def test_false_to_none_change_is_visible_even_when_true_domain_is_equal(tmp_path):
    manifest = write_workspace(tmp_path)
    before = read_workspace(tmp_path, manifest)
    _change(tmp_path, AGENT.replace("return False", "return None"))
    _, comparison = _compare(tmp_path, manifest, before)
    assert comparison.source_behavior.returns == "changed"
    assert comparison.source_behavior.true_domain == "unchanged"


def test_extra_boolean_parameter_is_in_function_domain_not_just_guard_domain(tmp_path):
    manifest = write_workspace(tmp_path)
    _change(
        tmp_path,
        AGENT.replace("within_limit: bool)", "within_limit: bool, extra: bool)").replace(
            "return True", "return extra"
        ),
    )
    (row,) = read_workspace(tmp_path, manifest)
    assert row.parameters == ["approved", "within_limit"]
    assert row.source_behavior.parameters == ["approved", "extra", "within_limit"]
    assert row.source_behavior.returns == ["false"] * 7 + ["true"]


def test_missing_old_model_duplicate_subject_and_invalid_domain_remain_unresolved(tmp_path):
    manifest = write_workspace(tmp_path)
    (row,) = read_workspace(tmp_path, manifest)
    old = row.model_copy(update={"source_behavior": None})
    for before in ([], [old], [row, row]):
        comparison = compare_guard_dependencies([row], before)[0]
        assert comparison.source_behavior.returns == "unresolved"
    for change in (
        {"returns": []},
        {"parameters": ["unknown"]},
        {"binding": None},
        {"configuration_reads": None},
        {"status": "redacted"},
    ):
        invalid = row.model_copy(
            update={"source_behavior": row.source_behavior.model_copy(update=change)}
        )
        assert (
            compare_guard_dependencies([invalid], [row])[0].source_behavior.returns == "unresolved"
        )


def test_closed_source_is_not_an_approval_claim(tmp_path):
    manifest = write_workspace(tmp_path, "approved")
    (row,) = read_workspace(tmp_path, manifest)
    comparison = compare_guard_dependencies([row], [row])[0]
    assert comparison.source_behavior.true_domain == "unchanged"
    assert "not approval" in comparison.source_behavior.reason
    assert row.dependency_coverage == "incomplete"
    assert row.finding_exclusion_eligible is False


@pytest.mark.parametrize(
    "old,new",
    [
        ("def refund", "async def refund"),
        ("@function_tool", "@function_tool\n@function_tool"),
        ("from agents import Agent, function_tool", "from agents import function_tool"),
        (
            "from .guards import permitted as allowed",
            "from .guards import permitted as allowed, other",
        ),
        ("within_limit: bool", "within_limit: str"),
        ("-> bool", "-> CustomResult"),
    ],
)
def test_signature_and_import_identity_must_be_closed(tmp_path, old, new):
    manifest = write_workspace(tmp_path)
    before = read_workspace(tmp_path, manifest)
    _change(tmp_path, AGENT.replace(old, new))
    after, comparison = _compare(tmp_path, manifest, before)
    assert after.source_behavior is None or after.source_behavior.status == "unresolved"
    assert comparison.source_behavior.returns == "unresolved"


def test_imported_helper_module_cannot_hide_another_definition(tmp_path):
    manifest = write_workspace(tmp_path)
    before = read_workspace(tmp_path, manifest)
    guard = tmp_path / "refund_agent/guards.py"
    guard.write_text(guard.read_text() + "\ndef other(flag: bool):\n    return external(flag)\n")
    after, comparison = _compare(tmp_path, manifest, before)
    assert after.status == "observed"
    assert after.source_behavior.status == "unresolved"
    assert comparison.source_behavior.returns == "unresolved"


def test_changed_parameter_domain_and_ambiguous_canonical_subject_refuse_comparison(tmp_path):
    manifest = write_workspace(tmp_path)
    before = read_workspace(tmp_path, manifest)
    _change(tmp_path, AGENT.replace("within_limit: bool)", "within_limit: bool, extra: bool)"))
    _, comparison = _compare(tmp_path, manifest, before)
    assert comparison.source_behavior.returns == "unresolved"
    ambiguous = before[0].model_copy(update={"tool_id": None, "status": "ambiguous"})
    assert (
        compare_guard_dependencies([ambiguous], before)[0].source_behavior.returns == "unresolved"
    )


def test_deep_boolean_model_refuses_without_crashing(tmp_path):
    manifest = write_workspace(tmp_path)
    _change(tmp_path, AGENT.replace("return True", "return " + "not " * 30 + "approved"))
    (row,) = read_workspace(tmp_path, manifest)
    assert row.source_behavior.status == "unresolved"
    assert row.source_behavior.reason == "source_model_depth_limit"
