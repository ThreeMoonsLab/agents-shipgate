from __future__ import annotations

from pathlib import Path

from agents_shipgate.checks import registry
from agents_shipgate.cli.scan import run_scan


class FakeDist:
    metadata = {"Name": "acme-shipgate-checks"}
    version = "1.2.3"


def test_plugin_checks_disabled_by_default(monkeypatch):
    loaded = {"called": False}

    class FakeEntryPoint:
        value = "acme_shipgate_checks:run"

        def load(self):
            loaded["called"] = True
            return lambda context: []

    monkeypatch.delenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", raising=False)
    monkeypatch.setattr(
        registry,
        "entry_points",
        lambda group: [FakeEntryPoint()],
    )

    assert registry._plugin_checks() == []
    assert loaded["called"] is False


def test_plugin_checks_load_only_when_enabled(monkeypatch):
    def plugin(context):
        return []

    metadata = {
        "id": "ACME-CHECK",
        "category": "custom",
        "default_severity": "medium",
        "description": "Custom plugin check.",
    }
    plugin.AGENTS_SHIPGATE_METADATA = metadata

    class FakeEntryPoint:
        value = "acme_shipgate_checks:run"

        def load(self):
            return plugin

    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    monkeypatch.setattr(
        registry,
        "entry_points",
        lambda group: [FakeEntryPoint()],
    )

    assert registry._plugin_checks() == [plugin]
    catalog = registry.check_catalog()
    assert any(check.id == "ACME-CHECK" for check in catalog)


def test_plugin_checks_can_be_forced_off(monkeypatch):
    loaded = {"called": False}

    class FakeEntryPoint:
        value = "acme_shipgate_checks:run"

        def load(self):
            loaded["called"] = True
            return lambda context: []

    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    monkeypatch.setattr(registry, "entry_points", lambda group: [FakeEntryPoint()])

    assert registry._plugin_checks(plugins_enabled=False) == []
    assert loaded["called"] is False


def test_builtin_distribution_entry_points_are_skipped(monkeypatch):
    loaded = {"called": False}

    class BuiltinDist:
        metadata = {"Name": "agents-shipgate"}
        version = "0.2.0"

    class FakeEntryPoint:
        dist = BuiltinDist()
        value = "agents_shipgate.checks.evil:run"

        def load(self):
            loaded["called"] = True
            return lambda context: []

    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    monkeypatch.setattr(registry, "entry_points", lambda group: [FakeEntryPoint()])

    assert registry._plugin_checks() == []
    assert loaded["called"] is False


def test_report_includes_loaded_plugin_provenance(monkeypatch, tmp_path):
    def plugin(context):
        return []

    plugin.AGENTS_SHIPGATE_METADATA = {
        "id": "ACME-CHECK",
        "category": "custom",
        "default_severity": "medium",
        "description": "Custom plugin check.",
    }

    class FakeEntryPoint:
        name = "acme"
        value = "acme_shipgate_checks:run"
        dist = FakeDist()

        def load(self):
            return plugin

    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    monkeypatch.setattr(registry, "entry_points", lambda group: [FakeEntryPoint()])

    report, _ = run_scan(
        config_path=Path("samples/clean_read_only_agent/shipgate.yaml"),
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.loaded_plugins == [
        {
            "name": "acme",
            "value": "acme_shipgate_checks:run",
            "distribution": "acme-shipgate-checks",
            "version": "1.2.3",
            "check_id": "ACME-CHECK",
        }
    ]
