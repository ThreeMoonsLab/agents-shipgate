"""The ownership fixture must preserve descriptor and alias boundaries."""

import pytest

from agents_shipgate.cli import application_diff
from agents_shipgate.core import verification_identity
from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    reset_static_input_snapshot,
)
from tests.constructor_engine_fixture import _frozen_application_engine


@pytest.fixture(scope="module")
def actual_engine():
    return verification_identity.build_engine_requirement(plugins_enabled=False)


def _builder(monkeypatch, actual_engine, transform=None):
    calls = []

    def build(*, plugins_enabled):
        calls.append(plugins_enabled)
        result = actual_engine.model_copy(deep=True)
        return transform(result, len(calls), plugins_enabled) if transform else result

    monkeypatch.setattr(verification_identity, "build_engine_requirement", build)
    monkeypatch.setattr(application_diff, "build_engine_requirement", build)
    return build, calls


def test_real_descriptor_boundaries_and_defensive_copies(monkeypatch, actual_engine):
    build, calls = _builder(monkeypatch, actual_engine)
    with _frozen_application_engine() as frozen:
        assert application_diff.build_engine_requirement is frozen
        assert verification_identity.build_engine_requirement is build
        first = frozen(plugins_enabled=False)
        second = frozen(plugins_enabled=False)
        assert first == second == actual_engine
        assert first is not second and first is not actual_engine
        first.__dict__["version"] = "mutated copy"
        assert frozen(plugins_enabled=False) == actual_engine
        assert calls == [False]
    assert calls == [False, False]
    assert application_diff.build_engine_requirement is build


@pytest.mark.parametrize("mode", [True, None, 0, "false"])
def test_other_modes_delegate_to_the_original_builder(monkeypatch, actual_engine, mode):
    build, calls = _builder(monkeypatch, actual_engine)
    with _frozen_application_engine() as frozen:
        assert frozen(plugins_enabled=mode) == actual_engine
        assert len(calls) == 2 and calls[1] is mode
    assert len(calls) == 3 and calls[-1] is False
    assert application_diff.build_engine_requirement is build


@pytest.mark.parametrize("field", list(verification_identity.VerificationEngineRequirement.model_fields))
def test_every_descriptor_field_drift_is_rejected(monkeypatch, actual_engine, field):
    def drift(result, number, mode):
        return result.model_copy(update={field: "changed"}) if number > 1 else result

    build, calls = _builder(monkeypatch, actual_engine, drift)
    with pytest.raises(AssertionError, match="Engine inputs changed"):
        with _frozen_application_engine():
            pass
    assert calls == [False, False]
    assert application_diff.build_engine_requirement is build


def test_setup_failure_does_not_replace_the_alias(monkeypatch, actual_engine):
    def fail(result, number, mode):
        raise RuntimeError("setup failed")

    build, calls = _builder(monkeypatch, actual_engine, fail)
    with pytest.raises(RuntimeError, match="setup failed"):
        with _frozen_application_engine():
            pytest.fail("setup failure cannot yield")
    assert calls == [False]
    assert application_diff.build_engine_requirement is build


def test_body_failure_remains_visible_and_restores_the_alias(monkeypatch, actual_engine):
    build, calls = _builder(monkeypatch, actual_engine)
    with pytest.raises(ValueError, match="body failed"):
        with _frozen_application_engine():
            raise ValueError("body failed")
    assert calls == [False, False]
    assert application_diff.build_engine_requirement is build


def test_teardown_builder_failure_preserves_body_context_and_restores(monkeypatch, actual_engine):
    def fail(result, number, mode):
        if number > 1:
            raise RuntimeError("teardown failed")
        return result

    build, calls = _builder(monkeypatch, actual_engine, fail)
    with pytest.raises(RuntimeError, match="teardown failed") as error:
        with _frozen_application_engine():
            raise ValueError("body failed")
    assert isinstance(error.value.__context__, ValueError)
    assert str(error.value.__context__) == "body failed"
    assert calls == [False, False]
    assert application_diff.build_engine_requirement is build


def test_an_existing_patch_is_refused_without_overwriting(monkeypatch, actual_engine):
    _build, calls = _builder(monkeypatch, actual_engine)
    def alien(**kwargs):
        return actual_engine

    monkeypatch.setattr(application_diff, "build_engine_requirement", alien)
    with pytest.raises(AssertionError, match="already replaced"):
        with _frozen_application_engine():
            pytest.fail("alien patch cannot yield")
    assert not calls
    assert application_diff.build_engine_requirement is alien


def test_nested_reuse_is_refused_without_disturbing_the_owner(monkeypatch, actual_engine):
    build, calls = _builder(monkeypatch, actual_engine)
    with _frozen_application_engine() as frozen:
        with pytest.raises(AssertionError, match="already replaced"):
            with _frozen_application_engine():
                pytest.fail("nested reuse cannot yield")
        assert application_diff.build_engine_requirement is frozen
        assert frozen(plugins_enabled=False) == actual_engine
    assert calls == [False, False]
    assert application_diff.build_engine_requirement is build


@pytest.mark.parametrize("owner", ["application", "core"])
def test_builder_replacement_during_the_context_is_rejected(monkeypatch, actual_engine, owner):
    build, calls = _builder(monkeypatch, actual_engine)

    def alien(**kwargs):
        return actual_engine

    target = application_diff if owner == "application" else verification_identity
    with pytest.raises(AssertionError, match=f"{owner.title()} engine builder changed"):
        with _frozen_application_engine():
            monkeypatch.setattr(target, "build_engine_requirement", alien)
    assert calls == [False]
    assert application_diff.build_engine_requirement is build
    assert verification_identity.build_engine_requirement is (alien if owner == "core" else build)


@pytest.mark.parametrize("active_at_entry", [False, True])
def test_active_snapshot_uses_fresh_metadata(monkeypatch, actual_engine, tmp_path, active_at_entry):
    build, calls = _builder(monkeypatch, actual_engine)
    snapshot = StaticInputSnapshot(tmp_path)
    token = activate_static_input_snapshot(snapshot) if active_at_entry else None
    try:
        with _frozen_application_engine() as selected:
            if active_at_entry:
                assert selected is build
                assert application_diff.build_engine_requirement is build
            else:
                token = activate_static_input_snapshot(snapshot)
            try:
                assert selected(plugins_enabled=False) == actual_engine
                assert selected(plugins_enabled=False) == actual_engine
                assert len(calls) == (2 if active_at_entry else 3)
            finally:
                reset_static_input_snapshot(token)
                token = None
            if not active_at_entry:
                assert selected(plugins_enabled=False) == actual_engine
                assert len(calls) == 3
    finally:
        if token is not None:
            reset_static_input_snapshot(token)
    assert len(calls) == (2 if active_at_entry else 4)
    assert application_diff.build_engine_requirement is build
