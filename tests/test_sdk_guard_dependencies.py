from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agents_shipgate.core.guard_dependencies import (
    associate_guard_dependencies,
    compare_guard_dependencies,
)
from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    reset_static_input_snapshot,
)
from agents_shipgate.core.tool_identity import build_tool_identity_catalog
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.schemas.manifest import AgentsShipgateManifest, ToolIdentityConfig

AGENT = '''from agents import Agent, function_tool
from .guards import permitted as allowed

@function_tool
def refund(approved: bool, within_limit: bool) -> bool:
    """Synthetic refund guard fixture; no tool executes during a scan."""
    if not allowed(approved, within_limit):
        return False
    return True

agent = Agent(name="Refund", tools=[refund])
'''


def write_workspace(
    root: Path, predicate: str = "approved and within_limit"
) -> AgentsShipgateManifest:
    package = root / "refund_agent"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("")
    (package / "agent.py").write_text(AGENT)
    (package / "guards.py").write_text(
        f"def permitted(approved: bool, within_limit: bool) -> bool:\n    return {predicate}\n"
    )
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "guard-evidence-fixture"},
            "agent": {"name": "Refund", "declared_purpose": ["exercise static guard comparison"]},
            "environment": {"target": "local"},
            "tool_sources": [
                {"id": "sdk", "type": "openai_agents_sdk", "path": "refund_agent/agent.py"}
            ],
            "output": {"directory": "agents-shipgate-reports", "formats": ["markdown", "json"]},
        }
    )


def read_workspace(root: Path, manifest: AgentsShipgateManifest):
    loaded = load_openai_sdk_static_tools(manifest.tool_sources[0], manifest, root)
    tools, _ = build_tool_identity_catalog([loaded], ToolIdentityConfig())
    return associate_guard_dependencies([loaded], tools)


@pytest.mark.parametrize(
    ("old", "new", "direction"),
    [
        ("approved and within_limit", "approved", "predicate_widened"),
        ("approved", "approved and within_limit", "predicate_narrowed"),
        ("approved and within_limit", "within_limit and approved", "predicate_unchanged"),
        ("approved", "within_limit", "predicate_changed"),
        ("False", "True", "predicate_widened"),
    ],
)
def test_imported_guard_comparison_preserves_actual_source_evidence(tmp_path, old, new, direction):
    manifest = write_workspace(tmp_path, old)
    before = read_workspace(tmp_path, manifest)
    declaration = (tmp_path / "refund_agent/agent.py").read_bytes()
    write_workspace(tmp_path, new)
    after = read_workspace(tmp_path, manifest)
    assert (tmp_path / "refund_agent/agent.py").read_bytes() == declaration
    assert before[0].status == after[0].status == "observed"
    (comparison,) = compare_guard_dependencies(after, before)
    assert comparison.direction == direction
    assert comparison.before.guard_path == comparison.after.guard_path == "refund_agent/guards.py"
    assert comparison.before.guard_line == comparison.after.guard_line == 1
    assert comparison.before.call_line == 7
    assert (
        after[0].inputs[-1].sha256
        == hashlib.sha256((tmp_path / "refund_agent/guards.py").read_bytes()).hexdigest()
    )
    assert comparison.finding_exclusion_eligible is False
    assert after[0].dependency_coverage == "incomplete"


@pytest.mark.parametrize(
    ("path", "contents", "reason"),
    [
        (
            "refund_agent/guards.py",
            "def permitted(a: bool, b: bool):\n    return a and setting\n",
            "predicate_dependency_not_supported",
        ),
        (
            "refund_agent/guards.py",
            "def permitted(a: bool, b: bool):\n    return True or dynamic()\n",
            "predicate_dependency_not_supported",
        ),
        (
            "refund_agent/guards.py",
            "import os\ndef permitted(a: bool, b: bool):\n    return True\n",
            "guard_module_execution_or_configuration",
        ),
        (
            "refund_agent/guards.py",
            "def permitted(a: bool, b: bool):\n    return True\ndef permitted(a: bool, b: bool):\n    return False\n",
            "ambiguous_guard_definition",
        ),
        (
            "refund_agent/guards.py",
            "def permitted(a: bool, b: bool):\n    if a:\n        return b\n    return False\n",
            "predicate_dependency_not_supported",
        ),
        (
            "refund_agent/__init__.py",
            "raise RuntimeError('must never execute')\n",
            "package_initializer_execution",
        ),
        (
            "refund_agent/agent.py",
            AGENT.replace("return True", "allowed = lambda a, b: True\n    return True"),
            "guard_shadowed_in_tool",
        ),
        (
            "refund_agent/agent.py",
            AGENT.replace("from .guards import permitted as allowed", "from .guards import *"),
            "wildcard_import",
        ),
        (
            "refund_agent/agent.py",
            AGENT.replace("from .guards", "from guards"),
            "unsupported_import_resolution",
        ),
        (
            "refund_agent/agent.py",
            AGENT.replace(
                "if not allowed(approved, within_limit):",
                "if not allowed(approved, configuration):",
            ),
            "guard_argument_configuration_or_expression",
        ),
        (
            "refund_agent/agent.py",
            AGENT.replace("return False", "raise PermissionError('no')"),
            "denial_not_a_literal_early_return",
        ),
        (
            "refund_agent/agent.py",
            AGENT + "\nallowed = None\n",
            "module_execution_or_configuration",
        ),
    ],
)
def test_unknown_dependencies_never_mean_no_influence(tmp_path, path, contents, reason):
    manifest = write_workspace(tmp_path)
    before = read_workspace(tmp_path, manifest)
    (tmp_path / path).write_text(contents)
    after = read_workspace(tmp_path, manifest)
    assert after[0].status in {"unresolved", "ambiguous"}
    assert after[0].reason == reason
    (comparison,) = compare_guard_dependencies(after, before)
    assert comparison.direction == "unresolved"
    assert comparison.finding_exclusion_eligible is False


def test_package_candidate_or_missing_guard_is_unresolved(tmp_path):
    manifest = write_workspace(tmp_path)
    package = tmp_path / "refund_agent/guards"
    package.mkdir()
    (row,) = read_workspace(tmp_path, manifest)
    assert row.reason == "ambiguous_module_package_candidate"
    package.rmdir()
    (tmp_path / "refund_agent/guards.py").unlink()
    (row,) = read_workspace(tmp_path, manifest)
    assert row.status == "unresolved" and row.reason == "dependency_input_unavailable"


def test_snapshot_captures_guard_bytes_and_negative_import_lookup(tmp_path):
    manifest = write_workspace(tmp_path)
    snapshot = StaticInputSnapshot(tmp_path)
    token = activate_static_input_snapshot(snapshot)
    try:
        (row,) = read_workspace(tmp_path, manifest)
        assert row.status == "observed"
        assert (tmp_path / "refund_agent/guards.py") in snapshot.dependency_paths()
        assert (tmp_path / "refund_agent/guards") in snapshot.absent_dependency_paths()
        assert (tmp_path / "__init__.py") in snapshot.absent_dependency_paths()
        (tmp_path / "refund_agent/guards").mkdir()
        with pytest.raises(ValueError, match="changed (?:identity|entries)"):
            snapshot.finish()
    finally:
        reset_static_input_snapshot(token)


def test_roundtrip_missing_duplicate_or_invalid_predicate_stays_unresolved(tmp_path):
    manifest = write_workspace(tmp_path)
    (row,) = read_workspace(tmp_path, manifest)
    roundtripped = type(row).model_validate_json(row.model_dump_json())
    assert roundtripped == row
    assert compare_guard_dependencies([row], [])[0].direction == "unresolved"
    assert compare_guard_dependencies([row, row], [row])[0].direction == "unresolved"
    corrupt = row.model_copy(update={"allowed_inputs": [999]})
    assert compare_guard_dependencies([corrupt], [row])[0].direction == "unresolved"


def test_observation_membership_survives_an_inventory_primary(tmp_path):
    manifest = write_workspace(tmp_path)
    loaded = load_openai_sdk_static_tools(manifest.tool_sources[0], manifest, tmp_path)
    tools, _ = build_tool_identity_catalog([loaded], ToolIdentityConfig())
    member = tools[0].observation_ids[0]
    primary = tools[0].model_copy(
        update={
            "id": "reviewed-canonical-tool",
            "name": "inventory-refund",
            "source_type": "mcp",
            "source_ref": "inventory.json",
            "observation_id": "inventory-member",
            "observation_ids": ["inventory-member", member],
        }
    )
    (row,) = associate_guard_dependencies([loaded], [primary])
    assert row.tool_id == "reviewed-canonical-tool"
    assert row.observation_id == member and row.guard_path == "refund_agent/guards.py"
    (ambiguous,) = associate_guard_dependencies([loaded], [primary, tools[0]])
    assert ambiguous.tool_id is None and ambiguous.status == "ambiguous"


@pytest.mark.parametrize(
    "binding",
    [
        "from .guards import permitted as allowed",
        "import builtins as allowed",
        "def allowed(a, b):\n        return True",
        "class allowed:\n        pass",
        "try:\n        pass\n    except Exception as allowed:\n        pass",
        "match approved:\n        case allowed:\n            pass",
        "global allowed",
        "nonlocal allowed",
    ],
)
def test_later_binding_cannot_look_like_the_module_guard(tmp_path, binding):
    manifest = write_workspace(tmp_path)
    (tmp_path / "refund_agent/agent.py").write_text(
        AGENT.replace("    return True", f"    {binding}\n    return True")
    )
    (row,) = read_workspace(tmp_path, manifest)
    assert row.status == "unresolved" and row.reason == "guard_shadowed_in_tool"


def test_shared_guard_associates_each_caller_but_not_an_unrelated_tool(tmp_path):
    manifest = write_workspace(tmp_path)
    second = AGENT.split("@function_tool", 1)[1].split("agent =", 1)[0]
    second = "@function_tool" + second.replace("def refund(", "def refund_second(")
    unrelated = "@function_tool\ndef lookup(approved: bool) -> bool:\n    return approved\n\n"
    path = tmp_path / "refund_agent/agent.py"
    path.write_text(
        AGENT.replace("agent =", second + unrelated + "agent =").replace(
            "tools=[refund]", "tools=[refund, refund_second, lookup]"
        )
    )
    before = read_workspace(tmp_path, manifest)
    guard = tmp_path / "refund_agent/guards.py"
    guard.write_text(guard.read_text().replace("approved and within_limit", "approved"))
    comparisons = compare_guard_dependencies(read_workspace(tmp_path, manifest), before)
    assert {row.tool_name for row in comparisons if row.direction == "predicate_widened"} == {
        "refund",
        "refund_second",
    }
    assert next(row for row in comparisons if row.tool_name == "lookup").direction == "unresolved"


def test_reader_limit_keeps_tool_and_explicit_unknown(tmp_path, monkeypatch):
    from agents_shipgate.inputs import sdk_guard_dependencies

    manifest = write_workspace(tmp_path)
    monkeypatch.setattr(sdk_guard_dependencies, "MAX_GUARD_AST_NODES", 2)
    (row,) = read_workspace(tmp_path, manifest)
    assert row.tool_id and row.status == "unresolved" and row.reason == "tool_module_limit"
