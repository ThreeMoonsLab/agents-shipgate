"""Golden-parity tests for the zero-install ``tools/shipgate-detect.py``.

Pins the script's structural verdict to ``agents-shipgate detect --json``
(via :func:`agents_shipgate.cli.discovery.detect_workspace`) on every
sample fixture in ``samples/``. The contract is **structural parity**,
not byte parity: same ``is_agent_project``, same set of fired
frameworks, same ``suggested_sources`` and ``excluded_sources``.
Evidence/reason strings and absolute scores are intentionally simplified
— a coding agent uses the script to make a yes/no decision, not to
re-derive the report.

If a new sample is added or the canonical detection rules change, this
test catches drift between the script and the CLI immediately.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from agents_shipgate.cli.discovery import detect_workspace
from agents_shipgate.inputs.codex_plugin import resolve_local_codex_marketplace_roots

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "tools" / "shipgate-detect.py"
SAMPLES_ROOT = REPO_ROOT / "samples"

# Hidden directories under samples/ are reference material (anti-patterns,
# READMEs), not detector inputs. The published fixtures are the regular
# top-level dirs.
_HIDDEN_PREFIXES = ("_", ".")

CANONICAL_KEYS = frozenset(
    {
        "is_agent_project",
        "frameworks",
        "agent_name_candidates",
        "project_name_candidates",
        "suggested_sources",
        "excluded_sources",
        "next_action",
        "workspace_signals",
    }
)


def _sample_dirs() -> list[Path]:
    return sorted(
        p
        for p in SAMPLES_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(_HIDDEN_PREFIXES)
    )


def _sample_ids() -> list[str]:
    return [p.name for p in _sample_dirs()]


def _load_script_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "shipgate_detect_zero_install", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["shipgate_detect_zero_install"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


def test_script_path_exists():
    """The zero-install script must live at the published path. The
    raw GitHub URL in docs/zero-install.md and llms.txt is what coding
    agents fetch — moving the file silently would break the public
    contract."""
    assert SCRIPT_PATH.is_file(), (
        f"tools/shipgate-detect.py not found at {SCRIPT_PATH}. The "
        "raw GitHub URL is part of the agent-facing public surface."
    )


def test_script_does_not_claim_drop_in_parity(script_module):
    """The script is documented as a structural subset of
    ``agents-shipgate detect --json``, NOT a drop-in replacement.
    Specifically, the canonical CLI emits ``diagnostics[]`` and
    ``next_actions[]`` arrays; the zero-install script does not.

    Pin the absence so docs/zero-install.md, llms.txt, and the script's
    docstring stay accurate. If we ever decide to ship a stdlib-only
    diagnostic engine, update those wording surfaces in the same PR
    that flips this test."""
    result = script_module.detect(SAMPLES_ROOT / "support_refund_agent")
    assert "diagnostics" not in result, (
        "The zero-install script must not emit `diagnostics[]` — it's "
        "documented as a structural subset of the canonical CLI. If "
        "you add this field, update the docstring in "
        "tools/shipgate-detect.py, docs/zero-install.md, and llms.txt "
        "to match."
    )
    assert "next_actions" not in result, (
        "The zero-install script must not emit `next_actions[]` — see "
        "the docstring in tools/shipgate-detect.py for the rationale "
        "(diagnostic engine is out of scope for the zero-install path)."
    )


def test_script_emits_canonical_top_level_keys(script_module):
    """The script's JSON output must carry the same top-level keys as
    DetectResult, plus ``script_version`` to distinguish it from the
    canonical CLI."""
    result = script_module.detect(SAMPLES_ROOT / "support_refund_agent")
    missing = CANONICAL_KEYS - set(result)
    assert not missing, (
        f"Zero-install detector output missing canonical keys: {sorted(missing)}. "
        "Output must be a structural superset of DetectResult."
    )
    assert "script_version" in result, (
        "Zero-install detector must emit script_version so consumers "
        "can distinguish it from the canonical CLI's output."
    )


# Samples the zero-install script does not yet detect with full parity.
# Adding parity to `tools/shipgate-detect.py` is tracked separately; the
# CLI's signals.py already covers these cases.
SCRIPT_PARITY_GAPS: frozenset[str] = frozenset({"n8n_workflow_agent"})


def _write_skipped_fixture_signals(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "agent.py").write_text(
        "from langchain.tools import tool\n\n@tool\ndef lookup():\n    return 'x'\n",
        encoding="utf-8",
    )
    tools = root / "tools"
    tools.mkdir()
    (tools / "payments-mcp.json").write_text(
        '{"tools": [{"name": "create_payment_link", "description": "Create link."}]}',
        encoding="utf-8",
    )
    (root / "broken-mcp.json").write_text("{not json", encoding="utf-8")
    plugin = root / "plugin" / ".codex-plugin"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text("{}", encoding="utf-8")


@pytest.mark.parametrize("sample_dir", _sample_dirs(), ids=_sample_ids())
def test_script_verdict_matches_cli(script_module, sample_dir):
    """Structural parity: for every sample, the zero-install script
    must agree with the canonical CLI on (a) ``is_agent_project``,
    (b) the set of fired frameworks, (c) the set of suggested-source
    types and paths, (d) the set of excluded-source types and paths,
    and (e) workspace-signals keys."""
    if sample_dir.name in SCRIPT_PARITY_GAPS:
        pytest.skip(
            f"{sample_dir.name}: zero-install script parity not yet implemented "
            "(see SCRIPT_PARITY_GAPS)."
        )
    script_result = script_module.detect(sample_dir)
    cli_result = detect_workspace(sample_dir.resolve()).model_dump(mode="json")

    assert script_result["is_agent_project"] == cli_result["is_agent_project"], (
        f"{sample_dir.name}: is_agent_project diverged "
        f"(script={script_result['is_agent_project']}, "
        f"cli={cli_result['is_agent_project']})."
    )

    script_frameworks = sorted(f["type"] for f in script_result["frameworks"])
    cli_frameworks = sorted(f["type"] for f in cli_result["frameworks"])
    assert script_frameworks == cli_frameworks, (
        f"{sample_dir.name}: framework set diverged "
        f"(script={script_frameworks!r}, cli={cli_frameworks!r}). "
        "The script's scoring rules must match cli/discovery/signals.py."
    )

    script_sources = sorted(
        (s["type"], s["path"]) for s in script_result["suggested_sources"]
    )
    cli_sources = sorted(
        (s["type"], s["path"]) for s in cli_result["suggested_sources"]
    )
    assert script_sources == cli_sources, (
        f"{sample_dir.name}: suggested_sources diverged "
        f"(script={script_sources!r}, cli={cli_sources!r})."
    )

    script_excluded = sorted(
        (s["type"], s["path"]) for s in script_result["excluded_sources"]
    )
    cli_excluded = sorted(
        (s["type"], s["path"]) for s in cli_result["excluded_sources"]
    )
    assert script_excluded == cli_excluded, (
        f"{sample_dir.name}: excluded_sources diverged "
        f"(script={script_excluded!r}, cli={cli_excluded!r}). "
        "The script's stdlib parse probe must reject the same JSON "
        "candidates as cli/discovery/artifacts.py:probe_suggested_source."
    )

    script_codex = sorted(
        (s["mode"], s["path"]) for s in script_result["codex_plugin_candidates"]
    )
    cli_codex = sorted(
        (s["mode"], s["path"]) for s in cli_result["codex_plugin_candidates"]
    )
    assert script_codex == cli_codex, (
        f"{sample_dir.name}: codex_plugin_candidates diverged "
        f"(script={script_codex!r}, cli={cli_codex!r})."
    )

    cli_signals = cli_result["workspace_signals"]
    script_signals = script_result["workspace_signals"]
    assert set(script_signals) == set(cli_signals), (
        f"{sample_dir.name}: workspace_signals keys diverged "
        f"(script={set(script_signals)!r}, cli={set(cli_signals)!r})."
    )


@pytest.mark.parametrize("sample_dir", _sample_dirs(), ids=_sample_ids())
def test_script_finds_at_least_one_python_file_when_cli_does(
    script_module, sample_dir
):
    """The script's ``os.walk`` and the CLI's git-aware walker may
    legitimately differ on file counts (e.g. samples with build
    artifacts), but if the CLI sees Python files in a sample, the
    script must too — otherwise framework detection is impossible."""
    script_result = script_module.detect(sample_dir)
    cli_result = detect_workspace(sample_dir.resolve()).model_dump(mode="json")
    cli_count = cli_result["workspace_signals"]["python_file_count"]
    script_count = script_result["workspace_signals"]["python_file_count"]
    if cli_count > 0:
        assert script_count > 0, (
            f"{sample_dir.name}: CLI found {cli_count} python files but "
            f"the zero-install script found 0. Walk logic diverged — "
            "check SKIP_DIRS and the os.walk pruning."
        )


def test_script_and_cli_skip_common_fixture_dirs(script_module, tmp_path):
    _write_skipped_fixture_signals(tmp_path / "fixtures")

    script_result = script_module.detect(tmp_path)
    cli_result = detect_workspace(tmp_path.resolve()).model_dump(mode="json")

    for result in (script_result, cli_result):
        assert result["is_agent_project"] is False
        assert result["frameworks"] == []
        assert result["suggested_sources"] == []
        assert result["excluded_sources"] == []
        assert result["codex_plugin_candidates"] == []
        assert result["workspace_signals"]["python_file_count"] == 0


def test_script_and_cli_dedupe_marketplace_covered_package(script_module, tmp_path):
    plugin = tmp_path / "plugins/reviewer/.codex-plugin/plugin.json"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("{not-json", encoding="utf-8")
    marketplace = tmp_path / ".agents/plugins/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(
        '{"plugins":[{"name":"reviewer","source":'
        '{"source":"local","path":"plugins/reviewer"}}]}',
        encoding="utf-8",
    )

    script_result = script_module.detect(tmp_path)
    cli_result = detect_workspace(tmp_path.resolve()).model_dump(mode="json")

    expected = [("marketplace", ".agents/plugins/marketplace.json")]
    assert [
        (item["mode"], item["path"])
        for item in script_result["codex_plugin_candidates"]
    ] == expected
    assert [
        (item["mode"], item["path"])
        for item in cli_result["codex_plugin_candidates"]
    ] == expected


def test_script_and_cli_reject_oversized_marketplace_for_dedupe(
    script_module,
    tmp_path,
):
    plugin = tmp_path / "plugins/reviewer/.codex-plugin/plugin.json"
    plugin.parent.mkdir(parents=True)
    plugin.write_text('{"name":"reviewer"}', encoding="utf-8")
    marketplace = tmp_path / ".agents/plugins/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(
        '{"plugins":[{"name":"reviewer","source":'
        '{"source":"local","path":"plugins/reviewer"}}],"padding":"'
        + ("x" * (10 * 1024 * 1024))
        + '"}',
        encoding="utf-8",
    )

    script_result = script_module.detect(tmp_path)
    cli_result = detect_workspace(tmp_path.resolve()).model_dump(mode="json")

    expected = {
        ("marketplace", ".agents/plugins/marketplace.json"),
        ("package", "plugins/reviewer"),
    }
    assert {
        (item["mode"], item["path"])
        for item in script_result["codex_plugin_candidates"]
    } == expected
    assert {
        (item["mode"], item["path"])
        for item in cli_result["codex_plugin_candidates"]
    } == expected


def test_script_and_cli_reject_plugin_manifest_symlink_escape_from_coverage(
    script_module,
    tmp_path,
):
    plugin = tmp_path / "plugins/reviewer/.codex-plugin/plugin.json"
    plugin.parent.mkdir(parents=True)
    outside_manifest = tmp_path.parent / f"{tmp_path.name}-outside-plugin.json"
    outside_manifest.write_text('{"name":"reviewer"}', encoding="utf-8")
    try:
        plugin.symlink_to(outside_manifest)
    except OSError as exc:  # pragma: no cover - platform permission fallback
        pytest.skip(f"symlinks unavailable: {exc}")
    marketplace = tmp_path / ".agents/plugins/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(
        '{"plugins":[{"name":"reviewer","source":'
        '{"source":"local","path":"plugins/reviewer"}}]}',
        encoding="utf-8",
    )

    script_result = script_module.detect(tmp_path)
    cli_result = detect_workspace(tmp_path.resolve()).model_dump(mode="json")

    assert script_module._local_marketplace_roots(tmp_path, [marketplace]) == set()
    assert resolve_local_codex_marketplace_roots(
        marketplace_path=marketplace,
        base_dir=tmp_path,
    ) == ()
    expected = {("marketplace", ".agents/plugins/marketplace.json")}
    assert {
        (item["mode"], item["path"])
        for item in script_result["codex_plugin_candidates"]
    } == expected
    assert {
        (item["mode"], item["path"])
        for item in cli_result["codex_plugin_candidates"]
    } == expected


def test_script_detects_workspace_named_fixture_dir(script_module, tmp_path):
    workspace = tmp_path / "fixtures"
    workspace.mkdir()
    (workspace / "agent.py").write_text(
        "from langchain.tools import tool\n\n@tool\ndef lookup():\n    return 'x'\n",
        encoding="utf-8",
    )

    script_result = script_module.detect(workspace)
    cli_result = detect_workspace(workspace.resolve()).model_dump(mode="json")

    assert script_result["is_agent_project"] is True
    assert cli_result["is_agent_project"] is True
    assert [fw["type"] for fw in script_result["frameworks"]] == ["langchain"]
    assert [fw["type"] for fw in cli_result["frameworks"]] == ["langchain"]
    assert script_result["workspace_signals"]["python_file_count"] == 1
    assert cli_result["workspace_signals"]["python_file_count"] == 1


def test_script_excludes_mcpservers_config_like_cli(script_module, tmp_path):
    """The load-bearing parse-probe case: a Cursor-style ``mcpServers``
    host config matches the ``*mcp*.json`` glob but is not a tools-array
    export. No published sample carries one, so this pins the behavior
    directly — script and CLI must BOTH drop it from suggested_sources
    and report it under excluded_sources. Regression for the zero-install
    half of the init->scan cold-start fix (the canonical CLI was fixed in
    cli/discovery; the script is a separate stdlib re-implementation)."""
    plugin_dir = tmp_path / "providers" / "cursor" / "plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "mcp.json").write_text(
        '{"mcpServers": {"stripe": {"command": "npx"}}}', encoding="utf-8"
    )
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "payments-mcp.json").write_text(
        '{"tools": [{"name": "create_payment_link", "description": '
        '"Create a payment link for checkout."}]}',
        encoding="utf-8",
    )

    script_result = script_module.detect(tmp_path)
    cli_result = detect_workspace(tmp_path.resolve()).model_dump(mode="json")

    assert script_result["suggested_sources"] == [
        {"type": "mcp", "path": "tools/payments-mcp.json"}
    ]
    assert [
        (s["type"], s["path"]) for s in script_result["suggested_sources"]
    ] == [(s["type"], s["path"]) for s in cli_result["suggested_sources"]]
    assert [
        (s["type"], s["path"]) for s in script_result["excluded_sources"]
    ] == [(s["type"], s["path"]) for s in cli_result["excluded_sources"]] == [
        ("mcp", "providers/cursor/plugin/mcp.json")
    ]
    assert "mcpServers" in script_result["excluded_sources"][0]["reason"]


def test_script_keeps_yaml_openapi_as_suggestion(script_module, tmp_path):
    """The stdlib probe is JSON-only (no YAML parser). A ``.yaml`` OpenAPI
    spec must be kept as a suggestion, never wrongly excluded — and a
    valid spec stays in parity with the CLI, which parses it for real."""
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "support.openapi.yaml").write_text(
        "openapi: 3.1.0\ninfo:\n  title: T\n  version: '1'\npaths: {}\n",
        encoding="utf-8",
    )
    script_result = script_module.detect(tmp_path)
    cli_result = detect_workspace(tmp_path.resolve()).model_dump(mode="json")
    assert script_result["suggested_sources"] == [
        {"type": "openapi", "path": "specs/support.openapi.yaml"}
    ]
    assert script_result["excluded_sources"] == []
    assert [
        (s["type"], s["path"]) for s in cli_result["suggested_sources"]
    ] == [("openapi", "specs/support.openapi.yaml")]


def test_script_excludes_swagger2_json_like_cli(script_module, tmp_path):
    """A Swagger 2.0 *JSON* document matches the ``*swagger*.json`` glob
    but the openapi adapter only accepts OpenAPI 3.x. JSON is parseable
    with stdlib, so the script must exclude it exactly like the CLI."""
    (tmp_path / "legacy-swagger.json").write_text(
        '{"swagger": "2.0", "info": {"title": "t", "version": "1"}, "paths": {}}',
        encoding="utf-8",
    )
    script_result = script_module.detect(tmp_path)
    cli_result = detect_workspace(tmp_path.resolve()).model_dump(mode="json")
    assert script_result["suggested_sources"] == []
    assert [
        (s["type"], s["path"]) for s in script_result["excluded_sources"]
    ] == [(s["type"], s["path"]) for s in cli_result["excluded_sources"]] == [
        ("openapi", "legacy-swagger.json")
    ]
