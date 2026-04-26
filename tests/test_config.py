from pathlib import Path

import pytest

from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.errors import ConfigError

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")


def test_load_sample_manifest():
    manifest = load_manifest(SAMPLE)
    assert manifest.version == "0.1"
    assert manifest.project.name == "support-refund-agent"
    assert manifest.agent.name == "refund-assistant"
    assert len(manifest.tool_sources) == 4


def test_requires_suppression_reason(tmp_path):
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text(
        """
version: "0.1"
project:
  name: invalid
agent:
  name: invalid-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
checks:
  ignore:
    - check_id: SHIP-SCHEMA-BROAD-FREE-TEXT
      reason: ""
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_manifest(manifest_path)


def test_rejects_manifest_typos_even_when_other_scope_text_exists(tmp_path):
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text(
        """
version: "0.1"
project:
  name: typo-test
agent:
  name: typo-agent
  instructions_preview: test instructions
  declared_purpoze:
    - typo should fail
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Did you mean declared_purpose"):
        load_manifest(manifest_path)


def test_missing_default_config_points_to_init_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError, match="agents-shipgate init --workspace . --write"):
        load_manifest(Path("shipgate.yaml"))


def test_unsupported_manifest_version_has_clear_error(tmp_path):
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text('version: "0.2"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="Unsupported manifest version"):
        load_manifest(manifest_path)


def test_yaml_unsafe_constructor_is_rejected(tmp_path):
    marker = tmp_path / "yaml_executed"
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text(
        f"!!python/object/apply:pathlib.Path.write_text ['{marker}', 'executed']\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_manifest(manifest_path)

    assert not marker.exists()
