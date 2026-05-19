from agents_shipgate.checks import registry
from agents_shipgate.schemas.checks import CheckMetadata


class FakeEntryPoint:
    value = "acme_checks:run"

    def __init__(self) -> None:
        self.loaded = False

    def load(self):
        self.loaded = True

        def run(context):
            return []

        run.AGENTS_SHIPGATE_METADATA = CheckMetadata(
            id="SHIP-PLUGIN-TEST",
            category="plugin",
            default_severity="low",
            description="Plugin test check.",
        )
        return run


def test_plugin_entry_points_are_not_loaded_by_default(monkeypatch):
    fake = FakeEntryPoint()
    monkeypatch.delenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", raising=False)
    monkeypatch.setattr(registry, "entry_points", lambda group: [fake])

    checks = registry.check_functions()

    assert fake.loaded is False
    assert all(check.__module__.startswith("agents_shipgate.checks") for check in checks)


def test_plugin_entry_points_load_only_when_enabled(monkeypatch):
    fake = FakeEntryPoint()
    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    monkeypatch.setattr(registry, "entry_points", lambda group: [fake])

    catalog = registry.check_catalog()

    assert fake.loaded is True
    assert any(check.id == "SHIP-PLUGIN-TEST" for check in catalog)
