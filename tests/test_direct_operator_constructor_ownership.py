"""Inline operator syntax must retain the full source-function ownership proof."""

import pytest

from tests.test_builder_bindings import (
    _assert_namespace_mutation_result,
    _namespace_mutation_observations,
)
from tests.test_builder_calls import namespace_workspace as namespace_workspace


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("extra_use", ["", "fake.Agent(name='Used', tools=SHARED)\n",
                                       "fake.Agent.__defaults__ = (framework.Agent,)\n"])
def test_inline_operator_same_function_write_keeps_call_and_metadata_obligations(
    namespace_workspace, framework, extra_use
):
    built, warnings = _namespace_mutation_observations(
        namespace_workspace, framework,
        "import operator\noperator.ior(fake.__dict__, {'Agent': fake.Agent})\n" + extra_use,
    )
    _assert_namespace_mutation_result(built, warnings, bool(extra_use))
