"""Imported constructor patches retain binding uncertainty."""

from __future__ import annotations

import pytest

from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_framework_constructor_bindings import (
    _run,
)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("layout", ["direct", "caller"])
@pytest.mark.parametrize("factory", [False, True])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
@pytest.mark.parametrize("patch", ["reexport", "reexport_metadata", "star_reexport", "namespace_reexport", "namespace_alias", "bridge_opaque", "helper_opaque", "exec", "exec_alias", "builtins", "dynamic", "globals", "vars", "conditional_reexport", "rebound_reexport", "saved_rebound"])
def test_imported_constructor_patch_cannot_establish_a_binding(tmp_path, framework, layout, factory, patch, change):
    result, observations, warnings = _run(tmp_path, framework, layout, patch, change, factory=factory)
    assert warnings and observations
    assert all(not item.tools_complete and not item.handoffs_complete for item in observations)
    assert all(row["change"] == "not_established" for row in result["rows"])
    assert result["comparison_status"] != "compared"
