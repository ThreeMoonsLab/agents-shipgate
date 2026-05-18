"""Tests for the M5 plugin validation pipeline.

Five load-time gates + runtime result validation + ``--strict-plugins``
exit policy. Each test sets up a fake entry point via monkeypatch and
asserts the resulting ``loaded_plugins[]`` entry plus the visible scan
findings.

Conventions:

* Plugin tests run via ``run_scan`` (not ``_plugin_check_records`` in
  isolation) so we exercise the production code path end-to-end.
* Every assertion on ``loaded_plugins`` uses ``len() == 1`` + dict
  subscript so a future additive field doesn't churn the test diff.
* The shared fixture path is ``samples/clean_read_only_agent/shipgate.yaml``
  for parity with the existing v0.x plugin tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents_shipgate.checks import registry
from agents_shipgate.checks.plugin_validation import (
    BAD_FLOOR,
    BAD_METADATA,
    BAD_SIGNATURE,
    DYNAMIC_DEFAULT_NOT_SUPPORTED,
    ID_COLLISION,
    LOAD_FAILED,
    VALID,
)
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.models import CheckMetadata, Finding

CLEAN_FIXTURE = Path("samples/clean_read_only_agent/shipgate.yaml")


class _Dist:
    metadata = {"Name": "test-plugin-dist"}
    version = "9.9.9"


def _entry_point(load_fn, *, name="plugin", value="pkg.module:run", dist=_Dist()):
    class EP:
        pass

    EP.name = name
    EP.value = value
    EP.dist = dist
    EP.load = staticmethod(load_fn)
    return EP()


def _patch_entries(monkeypatch, entries):
    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    monkeypatch.setattr(registry, "entry_points", lambda group: entries)


# --- Gate 1: load -----------------------------------------------------------


def test_gate_load_failure_is_recorded(monkeypatch, tmp_path):
    def boom():
        raise ImportError("simulated module load failure")

    _patch_entries(monkeypatch, [_entry_point(boom)])

    report, exit_code = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    assert exit_code == 0, "lenient default must not fail on plugin issues"
    assert len(report.loaded_plugins) == 1
    record = report.loaded_plugins[0]
    assert record["validation_status"] == LOAD_FAILED
    assert any("simulated module load failure" in err for err in record["validation_errors"])
    assert record["check_id"] is None
    assert record["runtime_errors"] == []


# --- Gate 2: signature ------------------------------------------------------


def test_gate_signature_rejects_non_callable(monkeypatch, tmp_path):
    _patch_entries(monkeypatch, [_entry_point(lambda: "not a function")])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == BAD_SIGNATURE
    assert record["check_id"] is None


def test_gate_signature_rejects_zero_arg_callable(monkeypatch, tmp_path):
    def no_args():
        return []

    no_args.AGENTS_SHIPGATE_METADATA = _good_metadata()
    _patch_entries(monkeypatch, [_entry_point(lambda: no_args)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == BAD_SIGNATURE
    assert any("ScanContext" in err for err in record["validation_errors"])


def test_gate_signature_accepts_optional_extras(monkeypatch, tmp_path):
    """Plugins with extra default-valued positional parameters are OK."""

    def with_extras(context, *, config=None):
        return []

    with_extras.AGENTS_SHIPGATE_METADATA = _good_metadata()
    _patch_entries(monkeypatch, [_entry_point(lambda: with_extras)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    assert report.loaded_plugins[0]["validation_status"] == VALID


def test_gate_signature_rejects_required_keyword_only(monkeypatch, tmp_path):
    """Regression for the v1 plan: a plugin with a required keyword-only
    parameter would always TypeError when called as ``plugin(context)``,
    but was marked ``valid`` because the gate only counted positional
    parameters.

    The validation contract is "valid means callable at runtime by the
    scanner". A required kw-only param violates that contract.
    """

    def plugin_with_required_kwarg(context, *, required_config):
        return []

    plugin_with_required_kwarg.AGENTS_SHIPGATE_METADATA = _good_metadata(
        "ACME-REQUIRED-KWARG"
    )
    _patch_entries(
        monkeypatch, [_entry_point(lambda: plugin_with_required_kwarg)]
    )

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == BAD_SIGNATURE
    assert any(
        "required_config" in err for err in record["validation_errors"]
    ), record["validation_errors"]


def test_gate_signature_accepts_optional_keyword_only(monkeypatch, tmp_path):
    """Optional keyword-only parameters (with defaults) are fine — the
    scanner never passes kwargs, so defaults apply."""

    def plugin_with_optional_kwarg(context, *, optional_config=None):
        return []

    plugin_with_optional_kwarg.AGENTS_SHIPGATE_METADATA = _good_metadata(
        "ACME-OPTIONAL-KWARG"
    )
    _patch_entries(
        monkeypatch, [_entry_point(lambda: plugin_with_optional_kwarg)]
    )

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    assert report.loaded_plugins[0]["validation_status"] == VALID


# --- Gate 3: metadata -------------------------------------------------------


def test_gate_metadata_missing(monkeypatch, tmp_path):
    def plugin(context):
        return []

    # Intentionally no AGENTS_SHIPGATE_METADATA.
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == BAD_METADATA
    assert any("missing" in err for err in record["validation_errors"])


def test_gate_metadata_malformed(monkeypatch, tmp_path):
    def plugin(context):
        return []

    plugin.AGENTS_SHIPGATE_METADATA = {"id": "ACME-BAD"}  # missing required fields
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == BAD_METADATA


def test_gate_metadata_accepts_check_id_alias(monkeypatch, tmp_path):
    """v0.17 M5: plugins may use ``check_id`` instead of ``id``."""

    def plugin(context):
        return []

    plugin.AGENTS_SHIPGATE_METADATA = {
        "check_id": "ACME-ALIAS",
        "category": "custom",
        "default_severity": "medium",
        "description": "Custom plugin check via check_id alias.",
    }
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == VALID
    assert record["check_id"] == "ACME-ALIAS"


# --- Gate 4: id_collision ---------------------------------------------------


def test_gate_id_collision_with_builtin(monkeypatch, tmp_path):
    def plugin(context):
        return []

    plugin.AGENTS_SHIPGATE_METADATA = {
        "id": "SHIP-POLICY-APPROVAL-MISSING",  # built-in ID
        "category": "policy",
        "default_severity": "info",
        "description": "Shadow attempt.",
    }
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == ID_COLLISION
    assert any("reserved" in err for err in record["validation_errors"])


def test_gate_id_collision_between_plugins(monkeypatch, tmp_path):
    def make_plugin(label):
        def plugin(context):
            return []

        plugin.AGENTS_SHIPGATE_METADATA = {
            "id": "ACME-DUPLICATE",
            "category": "custom",
            "default_severity": "medium",
            "description": f"Plugin {label}.",
        }
        return plugin

    p1 = make_plugin("one")
    p2 = make_plugin("two")
    _patch_entries(
        monkeypatch,
        [
            _entry_point(lambda: p1, name="first", value="first:run"),
            _entry_point(lambda: p2, name="second", value="second:run"),
        ],
    )

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    assert len(report.loaded_plugins) == 2
    assert report.loaded_plugins[0]["validation_status"] == VALID
    assert report.loaded_plugins[1]["validation_status"] == ID_COLLISION


# --- Gate 5: bad_floor ------------------------------------------------------


def test_gate_floor_higher_than_default_is_rejected(monkeypatch, tmp_path):
    def plugin(context):
        return []

    plugin.AGENTS_SHIPGATE_METADATA = {
        "id": "ACME-FLOOR",
        "category": "custom",
        "default_severity": "medium",
        "floor_severity": "high",  # higher than default — nonsensical
        "description": "Floor higher than default.",
    }
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == BAD_FLOOR


# --- Gate: dynamic_default_not_supported (v0.18 / PR #1) --------------------


def test_plugin_with_dynamic_default_is_rejected(monkeypatch, tmp_path):
    """A plugin declaring ``dynamic_default=True`` cannot wire into
    ``cli/scan.py:_dynamic_check_defaults``, so it can never receive
    the manifest-effective default needed for tier-crossing comparison.
    The gate rejects with ``dynamic_default_not_supported``.
    """
    def plugin(context):
        return []

    plugin.AGENTS_SHIPGATE_METADATA = {
        "id": "ACME-DYNAMIC",
        "category": "custom",
        "default_severity": "high",
        "floor_severity": "medium",  # valid floor — but dynamic_default still rejected
        "description": "Plugin trying to be dynamic.",
        "dynamic_default": True,
    }
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == DYNAMIC_DEFAULT_NOT_SUPPORTED
    assert any(
        "cli/scan.py" in err for err in record["validation_errors"]
    ), f"expected error to mention cli/scan.py, got: {record['validation_errors']}"


def test_plugin_dynamic_default_without_floor_lands_in_dynamic_status(monkeypatch, tmp_path):
    """Crucial placement test: a plugin declaring ``dynamic_default=True``
    WITHOUT ``floor_severity`` must NOT land in ``bad_floor``. The
    pre-Pydantic raw-metadata check runs BEFORE the
    ``CheckMetadata`` model validator that would otherwise mis-classify
    it. Status: ``dynamic_default_not_supported``.
    """
    def plugin(context):
        return []

    plugin.AGENTS_SHIPGATE_METADATA = {
        "id": "ACME-DYNAMIC-NO-FLOOR",
        "category": "custom",
        "default_severity": "high",
        "description": "Plugin with dynamic_default and no floor.",
        "dynamic_default": True,
        # NOTE: no floor_severity — would trigger CheckMetadata validator
    }
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == DYNAMIC_DEFAULT_NOT_SUPPORTED, (
        f"expected dynamic_default_not_supported, got "
        f"{record['validation_status']!r} — placement regression: the "
        f"gate must run before _coerce_metadata."
    )


# --- Runtime validation -----------------------------------------------------


def test_runtime_plugin_exception_is_captured(monkeypatch, tmp_path):
    def plugin(context):
        raise RuntimeError("simulated runtime crash")

    plugin.AGENTS_SHIPGATE_METADATA = _good_metadata("ACME-RUNTIME-CRASH")
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, exit_code = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert record["validation_status"] == VALID
    # Plugin loaded fine, but the call raised — captured in runtime_errors.
    assert any("simulated runtime crash" in err for err in record["runtime_errors"])
    # Other findings (built-ins) still ran.
    assert exit_code == 0


def test_runtime_plugin_must_return_list(monkeypatch, tmp_path):
    def plugin(context):
        return "not a list"

    plugin.AGENTS_SHIPGATE_METADATA = _good_metadata("ACME-WRONG-RETURN")
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert any("list[Finding]" in err for err in record["runtime_errors"])


def test_runtime_plugin_cannot_smuggle_other_check_ids(monkeypatch, tmp_path):
    """Critical trust rule: a plugin cannot emit findings under a check
    ID other than the one it declared in metadata. Such findings are
    dropped and recorded in runtime_errors.
    """

    def plugin(context):
        # Plugin claims it owns ACME-DECLARED but tries to emit a
        # finding under SHIP-POLICY-APPROVAL-MISSING (a built-in).
        return [
            Finding(
                check_id="SHIP-POLICY-APPROVAL-MISSING",
                category="policy",
                severity="critical",
                title="Smuggled finding",
                recommendation="—",
                provenance_kind="policy_pack",
            )
        ]

    plugin.AGENTS_SHIPGATE_METADATA = _good_metadata("ACME-DECLARED")
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert any(
        "does not match plugin declared id" in err for err in record["runtime_errors"]
    ), record["runtime_errors"]
    # The smuggled finding does not appear.
    smuggled = [f for f in report.findings if f.title == "Smuggled finding"]
    assert smuggled == []


def test_runtime_plugin_non_finding_items_dropped(monkeypatch, tmp_path):
    def plugin(context):
        return [
            "not a finding",
            Finding(
                check_id="ACME-MIXED",
                category="custom",
                severity="medium",
                title="A real finding",
                recommendation="Land it.",
                provenance_kind="policy_pack",
            ),
        ]

    plugin.AGENTS_SHIPGATE_METADATA = _good_metadata("ACME-MIXED")
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    report, _ = run_scan(
        config_path=CLEAN_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    record = report.loaded_plugins[0]
    assert any("non-Finding item dropped" in err for err in record["runtime_errors"])
    titles = [f.title for f in report.findings if f.check_id == "ACME-MIXED"]
    assert titles == ["A real finding"]


# --- --strict-plugins exit policy -------------------------------------------


def test_strict_plugins_exits_nonzero_on_validation_failure(monkeypatch, tmp_path):
    """Lenient mode exits 0; ``--strict-plugins`` should exit ≥ 4 when
    any plugin failed validation.

    Drives ``--strict-plugins`` through the CLI layer because the flag
    is a CLI concern, not part of ``run_scan``'s contract.
    """

    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    def broken(context):
        return []

    # Metadata missing -> bad_metadata.
    _patch_entries(monkeypatch, [_entry_point(lambda: broken)])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            str(CLEAN_FIXTURE),
            "--out",
            str(tmp_path),
            "--ci-mode",
            "advisory",
            "--strict-plugins",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 4, result.output


def test_strict_plugins_passes_when_all_plugins_valid(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    def plugin(context):
        return []

    plugin.AGENTS_SHIPGATE_METADATA = _good_metadata("ACME-STRICT-OK")
    _patch_entries(monkeypatch, [_entry_point(lambda: plugin)])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            str(CLEAN_FIXTURE),
            "--out",
            str(tmp_path),
            "--ci-mode",
            "advisory",
            "--strict-plugins",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output


# --- Helpers ----------------------------------------------------------------


def _good_metadata(check_id: str = "ACME-VALID") -> dict:
    return {
        "id": check_id,
        "category": "custom",
        "default_severity": "medium",
        "description": "Valid plugin check.",
    }


# --- CheckMetadata floor invariant -----------------------------------------


def test_check_metadata_rejects_floor_above_default():
    with pytest.raises(ValueError, match="floor_severity"):
        CheckMetadata(
            id="ACME-BAD-FLOOR",
            category="custom",
            default_severity="medium",
            floor_severity="critical",
            description="Bad floor.",
        )


def test_check_metadata_accepts_check_id_alias():
    metadata = CheckMetadata.model_validate(
        {
            "check_id": "ACME-ALIAS",
            "category": "custom",
            "default_severity": "high",
            "description": "Alias.",
        }
    )
    assert metadata.id == "ACME-ALIAS"
