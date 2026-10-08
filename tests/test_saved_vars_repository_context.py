"""Saved namespace writes are proved inside a stable real repository boundary."""

import pytest

from tests.test_builder_bindings import (
    _assert_namespace_mutation_result,
    _namespace_mutation_observations,
)
from tests.test_builder_calls import namespace_workspace as namespace_workspace


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
def test_saved_vars_namespace_write_retains_the_actual_owner_in_a_repository(
    namespace_workspace, framework, same_namespace,
):
    namespace = "framework" if same_namespace else "fake"
    helper = f"replace = vars\nslots = replace({namespace})\nslots['Agent'] = fake.Agent\n"
    built, warnings = _namespace_mutation_observations(namespace_workspace, framework, helper)
    _assert_namespace_mutation_result(built, warnings, same_namespace)
