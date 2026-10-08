"""Constant engine metadata for constructor ownership test modules.

These cases vary disposable application Git trees, while engine inputs stay
fixed. Every CLI comparison, snapshot and reader still runs. Only these tests
reduce per-comparison metadata reconstruction; identity/invalidation tests keep
the real builder. Boundary equality cannot detect transient change-and-restore,
so this fixture belongs only to modules with invariant engine inputs.
"""

from contextlib import contextmanager

import pytest

from agents_shipgate.cli import application_diff
from agents_shipgate.core import verification_identity
from agents_shipgate.core.static_inputs import active_static_input_snapshot


@contextmanager
def _frozen_application_engine():
    """Use actual worker metadata and restore the genuine alias on every exit."""
    builder = verification_identity.build_engine_requirement
    if application_diff.build_engine_requirement is not builder:
        raise AssertionError("Application engine builder was already replaced")
    if active_static_input_snapshot() is not None:
        yield builder
        return
    expected = builder(plugins_enabled=False)

    def frozen(*, plugins_enabled):
        if plugins_enabled is not False or active_static_input_snapshot() is not None:
            return builder(plugins_enabled=plugins_enabled)
        return expected.model_copy(deep=True)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(application_diff, "build_engine_requirement", frozen)
        try:
            yield frozen
        finally:
            if application_diff.build_engine_requirement is not frozen:
                raise AssertionError("Application engine builder changed during constructor regression module")
            if verification_identity.build_engine_requirement is not builder:
                raise AssertionError("Core engine builder changed during constructor regression module")
            if builder(plugins_enabled=False) != expected:
                raise AssertionError("Engine inputs changed during constructor regression module")


@pytest.fixture(scope="module", autouse=True)
def frozen_constructor_engine():
    with _frozen_application_engine():
        yield
