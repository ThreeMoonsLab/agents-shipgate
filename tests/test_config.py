import json
from pathlib import Path

import pytest
import yaml
from jsonschema import validate

from agents_shipgate.config.loader import KNOWN_MANIFEST_FIELDS, load_manifest
from agents_shipgate.core.errors import ConfigError

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")


def test_load_sample_manifest():
    manifest = load_manifest(SAMPLE)
    assert manifest.version == "0.1"
    assert manifest.project.name == "support-refund-agent"
    assert manifest.agent.name == "refund-assistant"
    assert len(manifest.tool_sources) == 5


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


def test_misplaced_known_field_does_not_suggest_itself(tmp_path):
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text(
        """
version: "0.1"
project:
  name: misplaced
agent:
  name: misplaced-agent
declared_purpose:
  - misplaced at the wrong nesting level
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as info:
        load_manifest(manifest_path)
    message = str(info.value)
    assert "declared_purpose" in message
    assert "Did you mean declared_purpose" not in message


def test_known_manifest_fields_are_derived_from_schema():
    assert "function_schemas" in KNOWN_MANIFEST_FIELDS
    assert "policy_rules" in KNOWN_MANIFEST_FIELDS
    assert "model_config" in KNOWN_MANIFEST_FIELDS
    assert "policy_packs" in KNOWN_MANIFEST_FIELDS


def test_manifest_examples_validate_against_generated_schema():
    schema = json.loads(Path("docs/manifest-v0.1.json").read_text(encoding="utf-8"))

    for path in [
        Path("docs/manifest-v0.1.example.minimal.yaml"),
        Path("docs/manifest-v0.1.example.full.yaml"),
    ]:
        validate(instance=yaml.safe_load(path.read_text(encoding="utf-8")), schema=schema)


def test_removed_top_level_severity_override_alias_has_migration_error(tmp_path):
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text(
        """
version: "0.1"
project:
  name: removed-alias
agent:
  name: removed-alias-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
check_severity_overrides:
  SHIP-DOC-MISSING-DESCRIPTION: critical
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="move these entries under checks.severity_overrides"):
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


# --- manifest type mismatches are edits, not crashes (#387) -----------------

MINIMAL_MANIFEST = """version: "0.1"
project:
  name: repro
agent:
  name: repro-agent
environment: dev
"""


def _write_manifest(tmp_path: Path, extra: str) -> Path:
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text(MINIMAL_MANIFEST + extra, encoding="utf-8")
    return manifest_path


# Every ``mode="before"`` validator that coerces a manifest value, given the
# wrong YAML shape. The expected substring is the manifest path the reader
# has to go edit; `pytest.raises` proves the exception class is the one the
# config-loading boundary catches.
WRONG_SHAPE_MANIFESTS: list[tuple[str, str, str]] = [
    (
        "google_adk mapping for a list",
        "google_adk:\n  tool_inventories:\n    adk_agent: tool-inventory.json\n",
        "google_adk.tool_inventories",
    ),
    (
        "google_adk scalar entry",
        "google_adk:\n  python_entrypoints:\n    - 3\n",
        "google_adk.python_entrypoints",
    ),
    (
        "anthropic prompt_files mapping",
        "anthropic:\n  prompt_files:\n    a: b\n",
        "anthropic.prompt_files",
    ),
    (
        "openai_api model_config list",
        "openai_api:\n  model_config:\n    - a\n",
        "openai_api.model_config",
    ),
    (
        "openai_api function_schemas mapping",
        "openai_api:\n  function_schemas:\n    a: b\n",
        "openai_api.function_schemas",
    ),
    (
        "codex_plugins mcp_tool_inventories mapping",
        "codex_plugins:\n  mcp_tool_inventories:\n    a: b\n",
        "codex_plugins.mcp_tool_inventories",
    ),
    (
        "policies mapping for a list",
        "policies:\n  require_approval_for_tools:\n    refund: yes\n",
        "policies.require_approval_for_tools",
    ),
    (
        "policy_packs mapping",
        "checks:\n  policy_packs:\n    a: b\n",
        "checks.policy_packs",
    ),
    (
        "severity_overrides list",
        "checks:\n  severity_overrides:\n    - SHIP-TOOL-DESC\n",
        "checks.severity_overrides",
    ),
    (
        "n8n workflows mapping",
        "n8n:\n  workflows:\n    a: b\n",
        "n8n.workflows",
    ),
    (
        "crewai python_entrypoints mapping",
        "crewai:\n  python_entrypoints:\n    a: b\n",
        "crewai.python_entrypoints",
    ),
    (
        "langchain python_entrypoints mapping",
        "langchain:\n  python_entrypoints:\n    a: b\n",
        "langchain.python_entrypoints",
    ),
]


@pytest.mark.parametrize(
    ("extra", "expected_path"),
    [(extra, path) for _, extra, path in WRONG_SHAPE_MANIFESTS],
    ids=[name for name, _, _ in WRONG_SHAPE_MANIFESTS],
)
def test_manifest_type_mismatch_is_a_config_error_naming_the_field(
    tmp_path, extra: str, expected_path: str
) -> None:
    """A mapping where a list belongs is a typo, and typos are ConfigError.

    ``TypeError`` raised inside a Pydantic validator is *not* converted into
    a ``ValidationError`` — it propagates past the config-loading boundary
    into the generic internal-error handler, which told the user their own
    manifest mistake was a Shipgate bug and asked them to file an issue
    (#387).
    """

    manifest_path = _write_manifest(tmp_path, extra)

    with pytest.raises(ConfigError) as excinfo:
        load_manifest(manifest_path)

    assert expected_path in str(excinfo.value)


def test_manifest_type_mismatch_reports_the_shape_that_was_written(
    tmp_path,
) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        "google_adk:\n  tool_inventories:\n    adk_agent: tool-inventory.json\n",
    )

    with pytest.raises(ConfigError) as excinfo:
        load_manifest(manifest_path)

    assert "but is a mapping" in str(excinfo.value)


def test_no_schema_module_raises_typeerror() -> None:
    """The class, not the instance.

    Any ``raise TypeError`` reachable from a validator is a latent
    "tell the user to file a bug" path. Banning the statement outright in
    the schema package is cheaper to enforce than proving reachability, and
    a schema module has no legitimate use for it: every rejection here is a
    rejection of *manifest input*.
    """

    import ast

    offenders: list[str] = []
    for path in sorted(Path("src/agents_shipgate/schemas").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            raised = node.exc
            if isinstance(raised, ast.Call):
                raised = raised.func
            if isinstance(raised, ast.Name) and raised.id == "TypeError":
                offenders.append(f"{path}:{node.lineno}")

    assert offenders == [], (
        "raise ValueError instead — Pydantic converts ValueError into a "
        f"ValidationError, and TypeError escapes as an internal error: {offenders}"
    )


# --- absent, empty, and malformed are three states (#384) -------------------


def test_absent_empty_and_non_mapping_manifests_have_distinct_messages(
    tmp_path,
) -> None:
    """One message for three states sent readers to fix a file that was
    never there — and the control envelope built on it disagreed with its
    own ``next_action`` about whether the workspace had a manifest at all.
    """

    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    a_list = tmp_path / "list.yaml"
    a_list.write_text("- a\n- b\n", encoding="utf-8")

    messages = {}
    for label, path in (
        ("absent", tmp_path / "absent-dir" / "shipgate.yaml"),
        ("empty", empty),
        ("list", a_list),
    ):
        with pytest.raises(ConfigError) as excinfo:
            load_manifest(path)
        messages[label] = str(excinfo.value)

    assert len(set(messages.values())) == 3, messages
    assert "not found" in messages["absent"]
    assert "must contain a YAML object" not in messages["absent"]
    assert "is empty" in messages["empty"]
    assert "must contain a YAML object" in messages["list"]


def test_absent_manifest_read_through_a_snapshot_still_reports_not_found(
    tmp_path,
) -> None:
    """``doctor``/``scan`` read the manifest as bytes and collapse a failed
    read into ``b""`` so the failure hashes the same input the diagnosis
    came from. That is deliberate — but ``b""`` parses as an empty document,
    so the absent case reached the YAML shape check (#384).
    """

    from agents_shipgate.cli.scan.inspect import inspect_sources

    with pytest.raises(ConfigError, match="Config file not found"):
        inspect_sources(config_path=tmp_path / "absent" / "shipgate.yaml")


# --- #386: tool_inventories[].source_id --------------------------------------

# ``MINIMAL_MANIFEST`` above is deliberately invalid (``environment: dev`` is a
# scalar) because every user of it asserts a ``ConfigError``. The success-path
# checks below need a manifest that actually loads.
_VALID_MANIFEST = """version: "0.1"
project:
  name: repro
agent:
  name: repro-agent
  declared_purpose:
    - repro
environment:
  target: local
"""


def _write_valid_manifest(tmp_path: Path, extra: str) -> Path:
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text(_VALID_MANIFEST + extra, encoding="utf-8")
    return manifest_path


@pytest.mark.parametrize(
    "block",
    [
        "google_adk:\n  tool_inventories:\n    - path: inv.json\n      source_id: adk\n",
        "langchain:\n  tool_inventories:\n    - path: inv.json\n      source_id: lc\n",
        "crewai:\n  tool_inventories:\n    - path: inv.json\n      source_id: crew\n",
        "n8n:\n  tool_inventories:\n    - path: inv.json\n      source_id: flows\n",
    ],
    ids=["google_adk", "langchain", "crewai", "n8n"],
)
def test_every_framework_inventory_accepts_a_source_binding(tmp_path, block: str) -> None:
    """One field, four frameworks: the defect and the fix are shared code."""

    manifest = load_manifest(_write_valid_manifest(tmp_path, block))
    section = block.split(":", 1)[0]
    entry = getattr(manifest, section).tool_inventories[0]
    assert entry.path == "inv.json"
    assert entry.source_id


def test_bare_path_inventory_entries_still_parse(tmp_path) -> None:
    """The pre-#386 spelling is unchanged; `source_id` is optional."""

    manifest = load_manifest(
        _write_valid_manifest(
            tmp_path, "google_adk:\n  tool_inventories:\n    - inv.json\n"
        )
    )
    entry = manifest.google_adk.tool_inventories[0]
    assert entry.path == "inv.json"
    assert entry.source_id is None
    assert entry.optional is False


def test_inventory_entry_still_rejects_an_unknown_key(tmp_path) -> None:
    """Adding one field must not open the model to arbitrary ones.

    #386 was reported after probing `source`, `tool_source`, `for_source` and
    four more spellings; every one of them must keep failing loudly rather than
    being silently accepted and ignored.
    """

    manifest_path = _write_valid_manifest(
        tmp_path,
        "google_adk:\n  tool_inventories:\n    - path: inv.json\n      for_source: adk\n",
    )

    with pytest.raises(ConfigError):
        load_manifest(manifest_path)
