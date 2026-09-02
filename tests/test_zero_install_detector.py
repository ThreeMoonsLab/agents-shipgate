"""Golden-parity tests for the zero-install ``tools/shipgate-detect.py``.

Pins the script's structural verdict to ``agents-shipgate detect --json``
(via :func:`agents_shipgate.cli.discovery.detect_workspace`) on every
sample fixture in ``samples/``. The contract is **structural parity**,
not byte parity: same ``is_agent_project``, same set of fired
frameworks, same ``suggested_sources`` and ``excluded_sources``.
Evidence/reason strings and absolute framework scores are intentionally
simplified — a coding agent uses the script to make a yes/no decision, not
to re-derive the report.

``agent_name_candidates`` is the one exception, pinned byte for byte. It is
not a yes/no signal: it names the agent a generated manifest declares as
the reviewed identity, so a script that ranked differently would send an
agent to fix a different agent than ``init`` did.

If a new sample is added or the canonical detection rules change, this
test catches drift between the script and the CLI immediately.
"""

from __future__ import annotations

import importlib.util
import subprocess
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
        "agent_scope",
        "agent_project_candidates",
        "agent_scope_truncated",
        "python_parse_truncated",
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


def test_framework_vocabulary_names_every_cli_omission(script_module):
    """Every framework the CLI can report is either here or a named omission.

    The parity test above compares the two on ``samples/``, which is only as
    strong as the fixtures: a detection the CLI gains and the script does not
    is invisible to it until a sample exercises the difference. That is exactly
    what happened with ``mcp_server_source`` (#431) — the CLI reads an MCP
    server's tool names out of TypeScript or Go registration sites, no sample
    contains one, and the script goes on reporting a repository like
    ``mongodb-js/mongodb-mcp-server`` as *not an agent project*.

    So the omission is written down instead of discovered. Adding a detection
    to the CLI now fails here until it is either ported or listed, and the list
    is the thing a reader can check against the script's own documented
    simplifications.
    """

    from agents_shipgate.cli.discovery.signals import _initial_framework_scores

    # Documented, deliberate, and filed as #485. Porting the reader means a
    # second implementation of the load-bearing matcher, which needs its own
    # increment and a conformance corpus shared with the package.
    known_omissions = {"mcp_server_source"}

    cli = set(_initial_framework_scores())
    script = set(script_module.FRAMEWORKS)

    assert script <= cli, (
        f"the script reports frameworks the CLI cannot: {sorted(script - cli)}"
    )
    assert cli - script == known_omissions, (
        "the script's framework vocabulary drifted from the CLI's: "
        f"{sorted((cli - script) ^ known_omissions)}. Port the detection, or "
        "add it to `known_omissions` here and to the script's `Intentional "
        "simplifications` list."
    )
    for omitted in known_omissions:
        assert omitted in SCRIPT_PATH.read_text(encoding="utf-8"), (
            f"{omitted!r} is a known omission but the script never says so; "
            "a reader of the script cannot discover it."
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

    assert script_result["agent_scope"] == cli_result["agent_scope"], (
        f"{sample_dir.name}: agent_scope diverged "
        f"(script={script_result['agent_scope']!r}, "
        f"cli={cli_result['agent_scope']!r}). An agent that consults the "
        "zero-install path must not adopt a scope the CLI refuses."
    )
    assert (
        script_result["python_parse_truncated"]
        == cli_result["python_parse_truncated"]
    ), (
        f"{sample_dir.name}: python_parse_truncated diverged "
        f"(script={script_result['python_parse_truncated']!r}, "
        f"cli={cli_result['python_parse_truncated']!r}). It is the guard every "
        "whole-workspace negative is gated on, `is_agent_project: false` "
        "included, so the two detectors must agree about whether their own "
        "classification is complete (#399 review)."
    )
    assert (
        script_result["agent_scope_truncated"]
        == cli_result["agent_scope_truncated"]
    ), (
        f"{sample_dir.name}: agent_scope_truncated diverged "
        f"(script={script_result['agent_scope_truncated']!r}, "
        f"cli={cli_result['agent_scope_truncated']!r}). It says whether "
        "agent_project_candidates enumerates the workspace or only the part "
        "the parse reached; a caller that reads a truncated list as complete "
        "concludes its own project is not an agent project (#395)."
    )
    script_projects = sorted(
        (c["path"], tuple(c["agent_names"]))
        for c in script_result["agent_project_candidates"]
    )
    cli_projects = sorted(
        (c["path"], tuple(c["agent_names"]))
        for c in cli_result["agent_project_candidates"]
    )
    assert script_projects == cli_projects, (
        f"{sample_dir.name}: agent_project_candidates diverged "
        f"(script={script_projects!r}, cli={cli_projects!r})."
    )

    cli_signals = cli_result["workspace_signals"]
    script_signals = script_result["workspace_signals"]
    assert set(script_signals) == set(cli_signals), (
        f"{sample_dir.name}: workspace_signals keys diverged "
        f"(script={set(script_signals)!r}, cli={set(cli_signals)!r})."
    )
    # Keys alone let the same named field mean two different things. When the
    # canonical detector started locating conventional directories anywhere in
    # the tree (#441), the script kept reading only the workspace root — so
    # `has_tools_dir` was `true` on one side and `false` on the other while
    # this test stayed green. These three are the located-directory answer, so
    # they are compared by value.
    for key in ("has_prompts_dir", "has_tools_dir", "conventional_dirs"):
        assert script_signals[key] == cli_signals[key], (
            f"{sample_dir.name}: workspace_signals[{key!r}] diverged "
            f"(script={script_signals[key]!r}, cli={cli_signals[key]!r}). "
            "The script must mirror cli/discovery/signals.py:"
            "_conventional_dir_locations exactly."
        )

    assert script_result["agent_name_candidates"] == cli_result["agent_name_candidates"], (
        f"{sample_dir.name}: agent_name_candidates diverged.\n"
        f"script={script_result['agent_name_candidates']!r}\n"
        f"cli={cli_result['agent_name_candidates']!r}\n"
        "The ranking decides which agent the generated manifest declares as "
        "the reviewed identity, so this one is byte parity, not structural: "
        "the script's rules must match "
        "cli/discovery/signals.py:_rank_agent_name_candidates exactly."
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


_ADK_AGENT = """\
from google.adk.agents import Agent


def act(x: str) -> str:
    \"\"\"Act.\"\"\"
    return "ok"


root_agent = Agent(name="{name}", tools=[act])
"""


def test_script_and_cli_agree_on_a_truncated_walk(script_module, tmp_path):
    """The parity samples all sit far under the cap, so they only ever pin
    ``agent_scope_truncated: false``. A workspace that actually truncates is
    where the two detectors could disagree about whether their own candidate
    list is complete — and an agent that consults the zero-install path must
    not read a capped list as an enumeration any more than it may read a
    capped scope verdict as settled (#395, #399 review)."""

    repo = tmp_path / "capped"
    for name in ("aa_one", "aa_two", "zz_hidden"):
        project = repo / name
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (project / "agent.py").write_text(
            _ADK_AGENT.format(name=name), encoding="utf-8"
        )
    filler = repo / "mm_filler"
    filler.mkdir()
    for index in range(1200):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")

    script_result = script_module.detect(repo)
    cli_result = detect_workspace(repo.resolve()).model_dump(mode="json")

    assert cli_result["agent_scope_truncated"] is True
    assert script_result["agent_scope_truncated"] is True
    assert cli_result["python_parse_truncated"] is True
    assert script_result["python_parse_truncated"] is True
    assert (
        script_result["workspace_signals"]["python_file_total"]
        == cli_result["workspace_signals"]["python_file_total"]
    )
    assert script_result["agent_scope"] == cli_result["agent_scope"]
    assert (
        script_result["workspace_signals"]["project_root_count"]
        == cli_result["workspace_signals"]["project_root_count"]
    )
    # Both name a recovery that actually changes the outcome: repeating the
    # same capped run reproduces the same verdict.
    assert "--max-python-files" in script_result["next_action"]
    assert "--max-python-files" in cli_result["next_action"]


def test_script_and_cli_agree_on_a_capped_single_scope(script_module, tmp_path):
    """A one-project workspace is `agent_scope: "single"` however early the
    parse stopped, so the scope branches never fire and both detectors fell
    through to "Workspace does not appear to be an agent project. No action."
    — a terminal false negative for an agent sitting past the cap (#399
    review)."""

    repo = tmp_path / "capped-single"
    filler = repo / "aa_filler"
    filler.mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "capped"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    for index in range(1001):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "zz_agent.py").write_text(
        _ADK_AGENT.format(name="hidden_agent"), encoding="utf-8"
    )

    script_result = script_module.detect(repo)
    cli_result = detect_workspace(repo.resolve()).model_dump(mode="json")

    for label, result in (("script", script_result), ("cli", cli_result)):
        assert result["python_parse_truncated"] is True, label
        assert result["agent_scope"] == "single", label
        assert result["is_agent_project"] is False, label
        assert "does not appear to be an agent project" not in result["next_action"], (
            f"{label}: a capped parse published a terminal false negative"
        )
        total = result["workspace_signals"]["python_file_total"]
        assert f"--max-python-files {total}" in result["next_action"], label

    # Human output takes the same route, ahead of every verdict below it.
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        script_module.main(["--workspace", str(repo)])
    printed = buffer.getvalue()
    assert "does not appear to be an agent project" not in printed
    assert "--max-python-files" in printed


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


def _write_ranking_probe(root: Path) -> None:
    """Both issue shapes in one workspace: an ADK coordinator bound through
    ``App(root_agent=…)`` with a name resolved from an adjacent config
    module, two literal sub-agents, and a one-character test literal."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.py").write_text(
        'import os\n\nAGENT_NAME = os.environ.get("AGENT_NAME", "SmartCloserAgent")\n',
        encoding="utf-8",
    )
    (root / "agent.py").write_text(
        "from config import AGENT_NAME\n"
        "from google.adk.agents import LlmAgent\n"
        "from google.adk.apps import App\n"
        "from google.adk.tools import FunctionTool\n\n"
        'salesforce_agent = LlmAgent(name="SalesforceAgent")\n'
        'sap_agent = LlmAgent(name="SapAgent")\n'
        # Annotated on purpose: the assignment-target lookup that resolves
        # `App(root_agent=root_agent)` has to read AnnAssign as well as Assign.
        "root_agent: LlmAgent = LlmAgent(\n"
        "    name=AGENT_NAME,\n"
        "    sub_agents=[salesforce_agent, sap_agent],\n"
        "    tools=[FunctionTool(func=lambda: None)],\n"
        ")\n"
        'app = App(name="smart_closer_app", root_agent=root_agent)\n',
        encoding="utf-8",
    )
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_agent.py").write_text(
        'from google.adk.agents import LlmAgent\n\nfixture = LlmAgent(name="t")\n',
        encoding="utf-8",
    )


def test_script_agent_name_ranking_matches_cli(script_module, tmp_path):
    """Samples all carry a single unambiguous name literal, so they cannot
    catch a ranking divergence. This workspace can: it has a hierarchy, a
    cross-module constant, a test-only literal, and a value below the
    quality floor. The script and the CLI must agree on all of it —
    disagreeing would have `init` and the zero-install path name different
    agents as the reviewed identity."""
    _write_ranking_probe(tmp_path / "smart_closer")
    script_result = script_module.detect(tmp_path / "smart_closer")
    cli_result = detect_workspace((tmp_path / "smart_closer").resolve()).model_dump(
        mode="json"
    )
    assert script_result["agent_name_candidates"] == cli_result["agent_name_candidates"]
    assert cli_result["agent_name_candidates"][0]["value"] == "SmartCloserAgent"
    assert cli_result["agent_name_candidates"][0]["role"] == "root_agent"
    assert [
        c["value"] for c in cli_result["agent_name_candidates"] if not c["selectable"]
    ] == ["t", "smart_closer"]


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    for key, value in (("user.email", "t@example.test"), ("user.name", "T")):
        subprocess.run(
            ["git", "-C", str(root), "config", key, value],
            check=True,
            capture_output=True,
        )


def test_script_ignores_gitignored_files_like_the_cli(script_module, tmp_path):
    """Canonical detection lists the workspace through Git, so a
    `.gitignore`d module is invisible to `init`. A script that walked it
    anyway would rank a name `init` can never write — the parity claim has
    to cover the inventory, not just the ranking rules."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / ".gitignore").write_text("ignored_agent.py\n", encoding="utf-8")
    (repo / "real.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="RealAgent")\n',
        encoding="utf-8",
    )
    # Sorts before real.py, so source order alone would have preferred it.
    (repo / "ignored_agent.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="AAAIgnoredAgent")\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "init"], check=True, capture_output=True
    )

    script_result = script_module.detect(repo)
    cli_result = detect_workspace(repo.resolve()).model_dump(mode="json")
    assert script_result["agent_name_candidates"] == cli_result["agent_name_candidates"]
    assert "AAAIgnoredAgent" not in {
        c["value"] for c in script_result["agent_name_candidates"]
    }


def test_script_drops_python_symlinks_that_escape_the_workspace(script_module, tmp_path):
    """A symlink pointing outside is not part of the workspace no matter
    what its name says. Ranking a name out of one also leaks the outside
    absolute path into `path`."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="AAAEscapedAgent")\n',
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="RealAgent")\n',
        encoding="utf-8",
    )
    (repo / "escaped.py").symlink_to(outside / "escaped.py")

    script_result = script_module.detect(repo)
    cli_result = detect_workspace(repo.resolve()).model_dump(mode="json")
    assert script_result["agent_name_candidates"] == cli_result["agent_name_candidates"]
    values = {c["value"] for c in script_result["agent_name_candidates"]}
    assert "AAAEscapedAgent" not in values
    assert all(
        c["path"] is None or not c["path"].startswith("/")
        for c in script_result["agent_name_candidates"]
    )


def test_script_reaches_python_sources_behind_many_assets(script_module, tmp_path):
    """The bound belongs on Python *parses*, not on the file inventory. A
    global file cap lets an asset-heavy repository exhaust its budget before
    the walk reaches any source at all, and the script then reports a
    different agent than `init` — or none."""
    repo = tmp_path / "assets"
    blobs = repo / "assets"
    # Deeper than the assets, so `os.walk` is guaranteed to enumerate all
    # 5200 blobs before it can reach the source — the old global cap
    # returned mid-directory and never descended.
    deep = blobs / "deep"
    deep.mkdir(parents=True)
    for index in range(5200):
        (blobs / f"{index:05d}.bin").write_bytes(b"x")
    (deep / "agent.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="BuriedAgent")\n',
        encoding="utf-8",
    )

    script_result = script_module.detect(repo)
    cli_result = detect_workspace(repo.resolve()).model_dump(mode="json")
    assert script_result["agent_name_candidates"] == cli_result["agent_name_candidates"]
    assert "BuriedAgent" in {c["value"] for c in script_result["agent_name_candidates"]}


def test_script_git_inventory_is_read_incrementally(script_module, monkeypatch):
    """`capture_output=True` would materialise the whole inventory before
    any size check could reject it, making the cap decorative. The reader
    must stop at the bound, so a stream far larger than the cap can be
    rejected without ever being held in full."""
    captured: dict[str, int] = {}
    real_popen = script_module.subprocess.Popen

    class _Endless:
        """Emits far more than the cap; records how much was actually read."""

        def __init__(self) -> None:
            self.read_bytes = 0

        def read(self, size: int) -> bytes:
            self.read_bytes += size
            captured["read"] = self.read_bytes
            return b"x" * size

    class _Process:
        def __init__(self) -> None:
            self.stdout = _Endless()

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            captured["killed"] = 1

    monkeypatch.setattr(script_module.subprocess, "Popen", lambda *a, **k: _Process())
    try:
        out = script_module._git_inventory_bounded(
            Path("."), [], env={}, max_output_bytes=1024
        )
    finally:
        monkeypatch.setattr(script_module.subprocess, "Popen", real_popen)

    assert out is None, "an overrunning inventory must be rejected, not returned"
    assert captured.get("killed") == 1, "the child must be killed on overrun"
    # Bounded means bounded: a few chunks past the cap, not the whole stream.
    assert captured["read"] <= 1024 + 4 * 64 * 1024


def test_script_fails_instead_of_walking_when_git_inventory_overruns(
    script_module, tmp_path, monkeypatch
):
    """Canonical discovery raises `DiscoveryError` rather than falling back
    to an unbounded walk. Falling back would do exactly the work the bound
    exists to refuse, and would answer from a different inventory than
    `init` used."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "agent.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="RealAgent")\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        script_module, "_git_inventory_bounded", lambda *a, **k: None
    )
    with pytest.raises(script_module.DiscoveryError):
        script_module.detect(repo)


def test_script_cli_reports_inventory_failure_as_nonzero_exit(
    script_module, tmp_path, monkeypatch, capsys
):
    """The failure has to reach the caller. A coding agent piping this
    script needs a non-zero exit, not a verdict built from a fallback."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "agent.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="RealAgent")\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        script_module, "_git_inventory_bounded", lambda *a, **k: None
    )
    exit_code = script_module.main(["--workspace", str(repo), "--json"])
    assert exit_code == 1
    assert "static output bounds" in capsys.readouterr().err


def test_script_keeps_the_logical_path_of_contained_symlinks(script_module, tmp_path):
    """Resolution proves containment; it must not rename the entry. With
    `agent.py -> source.txt` both entries collapse onto `source.txt`, the
    `.py` suffix disappears, and the script reports zero Python files and
    `is_agent_project: false` where the CLI reports an agent project — the
    go/no-go verdict itself, not just the ranking."""
    repo = tmp_path / "aliased"
    repo.mkdir()
    (repo / "source.txt").write_text(
        "from google.adk.agents import Agent\n"
        "from google.adk.apps import App\n\n"
        'root_agent = Agent(name="AliasRoot")\n'
        'app = App(name="a", root_agent=root_agent)\n',
        encoding="utf-8",
    )
    (repo / "agent.py").symlink_to("source.txt")

    script_result = script_module.detect(repo)
    cli_result = detect_workspace(repo.resolve()).model_dump(mode="json")
    assert script_result["is_agent_project"] == cli_result["is_agent_project"] is True
    assert script_result["agent_name_candidates"] == cli_result["agent_name_candidates"]
    assert "AliasRoot" in {c["value"] for c in script_result["agent_name_candidates"]}


def test_script_fallback_walk_refuses_an_unbounded_inventory(
    script_module, tmp_path, monkeypatch
):
    """Without Git there is nothing bounding the walk, so a downloaded tree
    of millions of unrelated assets would consume unbounded time and memory
    before detection saw one Python file. The ceiling refuses rather than
    truncating: a partial inventory is a partial scope verdict."""
    repo = tmp_path / "huge"
    (repo / "assets").mkdir(parents=True)
    for index in range(40):
        (repo / "assets" / f"{index:03d}.bin").write_bytes(b"x")
    (repo / "agent.py").write_text(
        'from agents import Agent\n\nagent = Agent(name="RealAgent")\n', encoding="utf-8"
    )

    monkeypatch.setattr(script_module, "MAX_WALK_FILES", 5)
    monkeypatch.setattr(script_module, "_git_files", lambda _w: None)
    with pytest.raises(script_module.DiscoveryError):
        script_module.detect(repo)


def test_script_binding_rules_match_the_cli(script_module, tmp_path):
    """The binding model decides which agent a manifest names, so the two
    implementations have to read Python the same way — a shadowed
    constructor, a late global rebinding, a conditional inline root, a
    retired root, and a wildcard import all have to land identically."""
    cases = {
        "shadowed": "from google.adk.agents import LlmAgent as RealAgent\n"
        "from google.adk.apps import App as RealApp\n\n"
        "def Agent(name):\n    return object()\n\n"
        'fake = Agent(name="FabricatedRoot")\n'
        'app = RealApp(name="a", root_agent=RealAgent(name="ActualRoot"))\n',
        "late_global": "from google.adk.agents import Agent\n"
        "from google.adk.apps import App\n\n"
        'root = Agent(name="StaleGlobalRoot")\n'
        "def make_app():\n    return App(name='a', root_agent=root)\n"
        'root = Agent(name="ActualGlobalRoot")\n'
        "app = make_app()\n",
        "inline_branch": "import os\nfrom google.adk.agents import Agent\n"
        "from google.adk.apps import App\n\n"
        "if os.getenv('TIER'):\n"
        "    app = App(name='a', root_agent=Agent(name='BranchOne'))\n"
        "else:\n"
        "    app = App(name='a', root_agent=Agent(name='BranchTwo'))\n",
        "retired": "from google.adk.agents import Agent\n"
        'root_agent = Agent(name="StaleRoot")\ndel root_agent\n',
        "star": "from google.adk.agents import Agent\nfrom replacement import *\n"
        'root_agent = Agent(name="StaleRoot")\n',
        "global_decl": "from google.adk.agents import Agent\n"
        'root_agent = Agent(name="OldRoot")\n'
        "def install():\n    global root_agent\n"
        "    root_agent = Agent(name='NewRoot')\ninstall()\n",
        # Round-4 cases: provenance is a question about a location.
        "late_import": "def Agent(*, name):\n    return object()\n"
        'root_agent = Agent(name="FabricatedRoot")\n'
        "from google.adk.agents import Agent\n",
        "cond_import": "import os\n"
        'if os.getenv("USE"):\n    from google.adk.agents import Agent as A\n'
        'root_agent = A(name="MaybeRoot")\n',
        "dotted_fake": "from google.adk.agents import Agent as _Real\n"
        "class fake:\n    class Agent:\n"
        "        def __init__(self, name):\n            pass\n"
        'root_agent = fake.Agent(name="FabricatedRoot")\n',
        "dotted_real": "import google.adk.agents as adk\n"
        'root_agent = adk.LlmAgent(name="DottedRoot")\n',
        "attr_ctor": "import google.adk.agents as adk\n"
        "def fake(**kw):\n    return object()\n"
        "adk.Agent = fake\n"
        'root_agent = adk.Agent(name="FabricatedRoot")\n',
        "attr_env": "import os\nfrom google.adk.agents import Agent\n"
        "def fake(a, b):\n    return 'Runtime'\n"
        "os.getenv = fake\n"
        'NAME = os.getenv("NAME", "FabricatedRoot")\n'
        "root_agent = Agent(name=NAME)\n",
        "comprehension": "from google.adk.agents import Agent\n"
        "from google.adk.apps import App\n"
        'worker = Agent(name="WorkerAgent")\n'
        "_ = [App for App in ()]\n"
        'app = App(name="a", root_agent=Agent(name="ActualRoot"))\n',
        "default_header": "from google.adk.agents import Agent\n"
        "from google.adk.apps import App\n"
        'worker = Agent(name="WorkerAgent")\n'
        "def configure(App=App(name='a', root_agent=Agent(name='ActualRoot'))):\n"
        "    return App\napp = configure()\n",
        "branch_def": "import os\nfrom google.adk.agents import Agent\n"
        "from google.adk.apps import App\n"
        "USE = os.getenv('USE')\n"
        "if USE:\n    def build():\n"
        "        return App(name='a', root_agent=Agent(name='BranchOne'))\n"
        "else:\n    def build():\n"
        "        return App(name='a', root_agent=Agent(name='BranchTwo'))\n"
        "app = build()\n",
        "star_ctor": "from google.adk.agents import Agent\n"
        "from google.adk.apps import App\nfrom replacement import *\n"
        'app = App(name="a", root_agent=Agent(name="FabricatedRoot"))\n',
    }
    for label, body in cases.items():
        project = tmp_path / label
        project.mkdir()
        (project / "agent.py").write_text(body, encoding="utf-8")
        script_result = script_module.detect(project)
        cli_result = detect_workspace(project.resolve()).model_dump(mode="json")
        assert (
            script_result["agent_name_candidates"]
            == cli_result["agent_name_candidates"]
        ), f"{label}: binding resolution diverged from the CLI"


def test_script_locates_nested_conventional_dirs_like_the_cli(
    script_module, tmp_path
):
    """The samples all keep their conventional directories at the root, so the
    per-sample parity check above cannot see this divergence.

    #441 moved conventional-directory discovery below the workspace root in the
    canonical detector. The script kept reading only the root, and because the
    parity assertion compared `workspace_signals` *keys*, `has_tools_dir` was
    `true` on one side and `false` on the other with every test green. This
    fixture is the reproduction from that issue: a Python distribution whose
    tools live under the import package.
    """

    workspace = tmp_path / "src" / "billing-cost-management-mcp-server"
    package = workspace / "awslabs" / "billing_cost_management_mcp_server"
    (package / "tools").mkdir(parents=True)
    (workspace / "pyproject.toml").write_text(
        '[project]\nname = "awslabs.billing"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "tools" / "budget_tools.py").write_text(
        "async def budget_server(ctx):\n    return {}\n", encoding="utf-8"
    )
    (workspace / "docs").mkdir()
    (workspace / "docs" / "prompts").mkdir()
    (workspace / "docs" / "prompts" / "system.md").write_text("hi\n", encoding="utf-8")

    script_signals = script_module.detect(workspace)["workspace_signals"]
    cli_signals = detect_workspace(workspace.resolve()).model_dump(mode="json")[
        "workspace_signals"
    ]

    for key in ("has_prompts_dir", "has_tools_dir", "conventional_dirs"):
        assert script_signals[key] == cli_signals[key], (
            f"workspace_signals[{key!r}] diverged "
            f"(script={script_signals[key]!r}, cli={cli_signals[key]!r})."
        )
    # And the value is the located path, not the bare name — the thing a
    # reader of either surface can open.
    assert cli_signals["conventional_dirs"] == [
        "docs/prompts",
        "awslabs/billing_cost_management_mcp_server/tools",
    ]
    assert cli_signals["has_tools_dir"] is True
