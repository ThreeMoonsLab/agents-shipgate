"""Tests for ``shipgate init`` auto-default behavior + ``--minimal`` snapshot.

The auto-default produces a *valid* shipgate.yaml that scans cleanly
against the real loaders, replacing v0.5's CHANGE_ME-heavy template for
workspaces that already look like agent projects.

``--minimal`` preserves the v0.5 output, except for the ``tool_sources``
ids it shares with the auto renderer (see the issue #307 section below).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.discovery import (
    detect_workspace,
    render_auto_manifest,
    render_manifest_template,
    source_ids,
)
from agents_shipgate.cli.discovery.source_ids import (
    MAX_SOURCE_ID_LENGTH,
    _digest,
    assign_source_ids,
    source_id_for,
)
from agents_shipgate.cli.main import app
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _copy_sample(name: str, dst: Path) -> Path:
    """Copy a sample workspace to ``dst`` minus the curated shipgate.yaml,
    so ``init`` writes a fresh one."""
    src = SAMPLES / name
    shutil.copytree(src, dst)
    target = dst / "shipgate.yaml"
    if target.exists():
        target.unlink()
    reports = dst / "agents-shipgate-reports"
    if reports.exists():
        shutil.rmtree(reports)
    return dst


def _validates(text: str) -> AgentsShipgateManifest:
    """Helper: parse + validate a generated manifest."""
    return AgentsShipgateManifest.model_validate(yaml.safe_load(text))


def test_auto_init_langchain_emits_valid_manifest_with_python_source(tmp_path: Path) -> None:
    workspace = _copy_sample("simple_langchain_agent", tmp_path / "lc")
    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    manifest = _validates(text)
    assert any(
        s.type == "langchain" and s.path == "agent.py"
        for s in manifest.tool_sources
    )


def test_auto_init_anthropic_emits_artifact_block_not_tool_source(tmp_path: Path) -> None:
    """Anthropic is artifact-only: per C3 it lives under ``anthropic:``,
    NOT as a tool_sources entry."""
    workspace = _copy_sample("simple_anthropic_agent", tmp_path / "anth")
    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    manifest = _validates(text)
    # Must NOT have an "anthropic" tool source (no such type).
    assert not any(s.type == "anthropic" for s in manifest.tool_sources)
    assert manifest.anthropic is not None
    assert manifest.anthropic.prompt_files == ["prompts/support_refund.md"]
    assert [t.path for t in manifest.anthropic.tools] == ["tools/anthropic-tools.json"]
    assert [p.path for p in manifest.anthropic.policy_rules] == [
        "policies/anthropic-policy.yaml"
    ]


def test_auto_init_adk_extracts_agent_name_from_literal(tmp_path: Path) -> None:
    """ADK sample defines ``Agent(name="adk_support_agent")``. Auto-init
    must use this for ``agent.name`` (not the dir name, not pyproject)."""
    workspace = _copy_sample("google_adk_agent", tmp_path / "adk")
    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    manifest = _validates(text)
    assert manifest.agent.name == "adk_support_agent"
    assert any(s.type == "google_adk" for s in manifest.tool_sources)


def test_auto_init_openai_api_emits_full_artifact_block(tmp_path: Path) -> None:
    workspace = _copy_sample("simple_openai_api_agent", tmp_path / "openai")
    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    manifest = _validates(text)
    assert manifest.openai_api is not None
    assert manifest.openai_api.prompt_files == ["prompts/support_refund.md"]
    assert [t.path for t in manifest.openai_api.tools] == ["tools/openai-tools.json"]


def test_auto_init_empty_workspace_falls_back_to_change_me_stub(tmp_path: Path) -> None:
    """No detected framework → emit a CHANGE_ME tool_sources entry so the
    schema (which requires ≥ 1 source/config block) still passes."""
    detect = detect_workspace(tmp_path)
    text = render_auto_manifest(tmp_path, detect)
    manifest = _validates(text)
    assert any(s.id == "CHANGE_ME" for s in manifest.tool_sources)


def test_minimal_template_byte_exact_to_legacy_output(tmp_path: Path) -> None:
    """``--minimal`` must reproduce the v0.5 template character-for-character
    so users with snapshot tests against today's `init` output can pin to it."""
    (tmp_path / "api.openapi.yaml").write_text(
        "openapi: 3.1.0\ninfo:\n  title: T\n  version: '1'\npaths: {}\n",
        encoding="utf-8",
    )
    legacy = render_manifest_template(tmp_path.resolve())
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--workspace", str(tmp_path), "--minimal"],
    )
    assert result.exit_code == 0
    # CliRunner trims a trailing newline; legacy has its own trailing
    # newline. Compare with both stripped to avoid runner-specific quirks.
    assert result.output.rstrip("\n") == legacy.rstrip("\n")


def test_init_cli_auto_default_emits_auto_detected_payload(tmp_path: Path) -> None:
    workspace = _copy_sample("simple_langchain_agent", tmp_path / "lc")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--json"],
    )
    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert payload["created"] is True
    assert "auto_detected" in payload
    assert payload["auto_detected"]["is_agent_project"] is True
    assert any(
        fw["type"] == "langchain"
        for fw in payload["auto_detected"]["frameworks"]
    )


def test_artifact_only_openai_workspace_emits_openai_api_block(tmp_path: Path) -> None:
    """A workspace with prompts/ and tools/openai-tools.json — but NO
    Python framework imports — must still get an ``openai_api:`` block
    rather than a CHANGE_ME stub.

    Regression for v0.6 reviewer feedback: the openai_api block was
    gated on framework detection, which only fires for openai-config.json
    or `from agents import` Python source.
    """
    workspace = tmp_path / "openai_artifact_only"
    workspace.mkdir()
    (workspace / "prompts").mkdir()
    (workspace / "tools").mkdir()
    (workspace / "prompts" / "support.md").write_text("You are helpful.", encoding="utf-8")
    (workspace / "tools" / "openai-tools.json").write_text("[]", encoding="utf-8")

    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    manifest = _validates(text)
    assert manifest.openai_api is not None
    assert manifest.openai_api.prompt_files == ["prompts/support.md"]
    assert [t.path for t in manifest.openai_api.tools] == ["tools/openai-tools.json"]


def test_artifact_only_openai_workspace_does_not_emit_anthropic_block(tmp_path: Path) -> None:
    """The OpenAI artifact-only workspace must NOT also emit an
    ``anthropic:`` block (prompts/ overlaps both adapters by glob)."""
    workspace = tmp_path / "openai_artifact_only2"
    workspace.mkdir()
    (workspace / "prompts").mkdir()
    (workspace / "tools").mkdir()
    (workspace / "prompts" / "support.md").write_text("hi", encoding="utf-8")
    (workspace / "tools" / "openai-tools.json").write_text("[]", encoding="utf-8")

    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    assert "openai_api:" in text
    assert "anthropic:" not in text


def test_init_json_agent_name_matches_yaml_when_no_literal(tmp_path: Path) -> None:
    """JSON ``auto_detected.agent_name`` must reflect the value the
    manifest actually carries, not the first candidate. Regression for
    v0.6 reviewer feedback: the JSON used to claim a workspace-dir name
    while the YAML still had CHANGE_ME.
    """
    workspace = _copy_sample("simple_langchain_agent", tmp_path / "lc")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--json"],
    )
    assert result.exit_code == 0, result.output
    import json as _json

    payload = _json.loads(result.output)
    yaml_text = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    if "name: CHANGE_ME" in yaml_text:
        # When the template emits CHANGE_ME, JSON must NOT report a value.
        assert payload["auto_detected"]["agent_name"] is None
    else:
        # When a strong literal IS used, JSON value must match YAML.
        assert payload["auto_detected"]["agent_name"] is not None
        assert (
            f"name: {payload['auto_detected']['agent_name']}" in yaml_text
            or f'name: "{payload["auto_detected"]["agent_name"]}"' in yaml_text
        )
    # All candidates surfaced separately so agents can override.
    assert "agent_name_candidates" in payload["auto_detected"]
    assert all(
        "source" in c for c in payload["auto_detected"]["agent_name_candidates"]
    )


def test_init_json_agent_name_matches_yaml_when_literal_present(tmp_path: Path) -> None:
    """ADK fixture has ``Agent(name="adk_support_agent")`` — JSON must
    report it and the YAML must use it (no CHANGE_ME)."""
    workspace = _copy_sample("google_adk_agent", tmp_path / "adk")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--json"],
    )
    assert result.exit_code == 0, result.output
    import json as _json

    payload = _json.loads(result.output)
    assert payload["auto_detected"]["agent_name"] == "adk_support_agent"
    yaml_text = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    assert "name: adk_support_agent" in yaml_text
    assert "name: CHANGE_ME" not in yaml_text


def test_init_auto_flag_is_accepted_as_no_op(tmp_path: Path) -> None:
    """``--auto`` is a self-documenting alias; auto is the default since v0.6."""
    workspace = _copy_sample("simple_langchain_agent", tmp_path / "lc")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--auto", "--write"],
    )
    assert result.exit_code == 0
    assert (workspace / "shipgate.yaml").exists()


# --- Cold-start regression: unparseable glob matches must never be written ---
# Found by mining stripe/ai at cd8cee5 (PR #232 era): a Cursor plugin
# `mcp.json` is an mcpServers-style host config that matches the `*mcp*.json`
# suggestion glob. init --write used to declare it as an `mcp` tool source,
# and the very next documented step — `scan -c shipgate.yaml` — exited 3 with
# "MCP tools file must contain a tools array".

_CURSOR_PLUGIN_MCP = """{
  "mcpServers": {
    "stripe": {
      "command": "npx",
      "args": ["-y", "@stripe/mcp", "--tools=all"],
      "env": {"STRIPE_SECRET_KEY": "sk_test_CHANGE_ME"}
    }
  }
}
"""

_MCP_TOOLS_EXPORT = """{
  "tools": [
    {
      "name": "create_payment_link",
      "description": "Create a payment link for an order checkout flow.",
      "inputSchema": {"type": "object", "properties": {"amount": {"type": "integer"}}}
    }
  ]
}
"""


def _cursor_config_workspace(tmp_path: Path, *, with_export: bool) -> Path:
    workspace = tmp_path / "ws"
    plugin_dir = workspace / "providers" / "cursor" / "plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "mcp.json").write_text(_CURSOR_PLUGIN_MCP, encoding="utf-8")
    if with_export:
        tools = workspace / "tools"
        tools.mkdir()
        (tools / "payments-mcp.json").write_text(_MCP_TOOLS_EXPORT, encoding="utf-8")
    return workspace


def test_cold_start_init_then_scan_with_mcpservers_config_present(tmp_path: Path) -> None:
    """init must never write a tool_sources entry the scan input adapters
    reject: the documented cold-start flow is `init --write` → `scan`, and
    one poison entry breaks it out of the box on real repos."""
    import json as _json

    workspace = _cursor_config_workspace(tmp_path, with_export=True)
    runner = CliRunner()

    result = runner.invoke(
        app, ["init", "--workspace", str(workspace), "--write", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["created"] is True
    excluded = payload["auto_detected"]["excluded_sources"]
    assert [entry["path"] for entry in excluded] == ["providers/cursor/plugin/mcp.json"]
    assert "mcpServers" in excluded[0]["reason"]

    manifest_text = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    manifest = _validates(manifest_text)
    # The real export is declared; the host config is hinted, never declared.
    assert [s.path for s in manifest.tool_sources] == ["tools/payments-mcp.json"]
    assert "#   providers/cursor/plugin/mcp.json" in manifest_text

    scan_result = runner.invoke(
        app, ["scan", "--config", str(workspace / "shipgate.yaml")]
    )
    assert scan_result.exit_code == 0, scan_result.output
    assert "Input parsing error" not in scan_result.output


def test_init_config_only_workspace_writes_stub_not_poison_source(tmp_path: Path) -> None:
    """With ONLY the host config present, init falls back to the CHANGE_ME
    stub (pre-existing empty-workspace contract) and hints the excluded
    file — it must not declare the config as a tool source."""
    workspace = _cursor_config_workspace(tmp_path, with_export=False)
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--workspace", str(workspace), "--write"])
    assert result.exit_code == 0, result.output
    assert "Excluded 1 detected file(s)" in result.output
    manifest_text = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    manifest = _validates(manifest_text)
    assert all(
        s.path != "providers/cursor/plugin/mcp.json" for s in manifest.tool_sources
    )
    assert any(s.id == "CHANGE_ME" for s in manifest.tool_sources)
    assert "#   providers/cursor/plugin/mcp.json" in manifest_text


def test_minimal_template_excludes_mcpservers_config(tmp_path: Path) -> None:
    """The legacy --minimal discovery path shares the parse probe: the host
    config must not appear as a tool source there either."""
    workspace = _cursor_config_workspace(tmp_path, with_export=True)
    template = render_manifest_template(workspace.resolve())
    assert "tools/payments-mcp.json" in template
    assert "providers/cursor/plugin/mcp.json" not in template


# --- Repeated basenames must not collide into one id (issue #307) ---
# Found on usestrix/strix, a real OpenAI Agents SDK project: one `tool.py`
# per tool package (`strix/tools/finish/tool.py`, `.../respond/tool.py`, …)
# plus several `*/tools.py` modules. Ids derived from the basename alone
# produced `openai_sdk_tool` three times, and the schema rejects a manifest
# whose `tool_sources[].id` values repeat — so auto-init wrote nothing at
# all on a conventional Python layout.

_FUNCTION_TOOL_MODULE = """from agents import function_tool


@function_tool
def do_thing(target: str) -> str:
    \"\"\"Do the thing.\"\"\"
    return target
"""


def _openai_sdk_workspace(root: Path, relative_paths: list[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "strixlike"\ndependencies = ["openai-agents"]\n',
        encoding="utf-8",
    )
    for relative in relative_paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_FUNCTION_TOOL_MODULE, encoding="utf-8")
    return root


_REPEATED_BASENAMES = [
    "strix/tools/finish/tool.py",
    "strix/tools/load_skill/tool.py",
    "strix/tools/respond/tool.py",
    "strix/agents/tools.py",
    "strix/runtime/tools.py",
]


def test_auto_init_repeated_basenames_emit_unique_source_ids(tmp_path: Path) -> None:
    workspace = _openai_sdk_workspace(tmp_path / "strixlike", _REPEATED_BASENAMES)
    detect = detect_workspace(workspace)
    manifest = _validates(render_auto_manifest(workspace, detect))

    declared = {s.path for s in manifest.tool_sources}
    assert declared == set(_REPEATED_BASENAMES)
    ids = [s.id for s in manifest.tool_sources]
    assert len(set(ids)) == len(ids)
    # Derived from the whole relative path, so the id still names its file.
    by_path = {s.path: s.id for s in manifest.tool_sources}
    assert by_path["strix/tools/finish/tool.py"] == "openai_sdk_strix_tools_finish_tool"
    assert by_path["strix/agents/tools.py"] == "openai_sdk_strix_agents_tools"


def test_cold_start_init_then_scan_with_repeated_basenames(tmp_path: Path) -> None:
    """The documented cold-start flow — `init --write` → `scan` — must
    survive a repo that ships one `tool.py` per tool package. Before the
    fix, `init` exited 4 with `internal_error` and wrote no manifest."""
    import json as _json

    workspace = _openai_sdk_workspace(tmp_path / "strixlike", _REPEATED_BASENAMES)
    runner = CliRunner()

    result = runner.invoke(
        app, ["init", "--workspace", str(workspace), "--write", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["created"] is True

    scan_result = runner.invoke(
        app, ["scan", "--config", str(workspace / "shipgate.yaml")]
    )
    assert scan_result.exit_code == 0, scan_result.output
    assert "Config error" not in scan_result.output


def test_preview_then_init_scan_removes_preview_handoff(tmp_path: Path) -> None:
    """A later scan must not expose the preview route as current evidence."""

    workspace = _openai_sdk_workspace(
        tmp_path / "preview-then-scan",
        ["agent/tools.py"],
    )
    runner = CliRunner()

    preview = runner.invoke(
        app,
        ["verify", "--workspace", str(workspace), "--preview", "--json"],
    )
    assert preview.exit_code == 0, preview.output
    reports = workspace / "agents-shipgate-reports"
    handoff_path = reports / "agent-handoff.json"
    verifier_path = reports / "verifier.json"
    pr_comment_path = reports / "pr-comment.md"
    preview_handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert preview_handoff["operation"] == "verify_preview"
    assert preview_handoff["control"]["state"] == "agent_action_required"
    assert verifier_path.is_file()
    assert pr_comment_path.is_file()

    initialized = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--json"],
    )
    assert initialized.exit_code == 0, initialized.output

    scanned = runner.invoke(
        app,
        [
            "scan",
            "--config",
            str(workspace / "shipgate.yaml"),
            "--suggest-patches",
        ],
    )
    assert scanned.exit_code == 0, scanned.output
    report = json.loads((reports / "report.json").read_text(encoding="utf-8"))
    assert report["release_decision"]["decision"] == "insufficient_evidence"
    assert not handoff_path.exists()
    assert not verifier_path.exists()
    assert not pr_comment_path.exists()


def test_auto_init_source_ids_are_stable_when_a_sibling_appears(tmp_path: Path) -> None:
    """Ids carry no positional component: a file added earlier in the walk
    must not renumber the entries after it, the way a `_2`/`_3` suffix
    would. (The one case that does shift an existing id — a path that
    sanitizes to the same string — is pinned below.)"""
    before_ws = _openai_sdk_workspace(
        tmp_path / "before", ["pkg/beta/tool.py", "pkg/gamma/tool.py"]
    )
    after_ws = _openai_sdk_workspace(
        tmp_path / "after",
        ["pkg/alpha/tool.py", "pkg/beta/tool.py", "pkg/gamma/tool.py"],
    )

    def ids_by_path(workspace: Path) -> dict[str, str]:
        manifest = _validates(
            render_auto_manifest(workspace, detect_workspace(workspace))
        )
        return {s.path: s.id for s in manifest.tool_sources}

    before = ids_by_path(before_ws)
    after = ids_by_path(after_ws)
    assert before == {path: after[path] for path in before}


def test_minimal_template_repeated_basenames_emit_unique_source_ids(
    tmp_path: Path,
) -> None:
    """`--minimal` is the documented recovery path, and it had the same
    basename-only id rule — with no validation gate, so it wrote the
    invalid manifest and `scan` failed on it with a config error."""
    workspace = tmp_path / "services"
    spec = (
        "openapi: 3.1.0\ninfo:\n  title: T\n  version: '1'\npaths:\n"
        "  /thing:\n    get:\n      operationId: get_thing\n"
        "      responses:\n        '200':\n          description: ok\n"
    )
    for service in ("billing", "support"):
        target = workspace / service / "openapi.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(spec, encoding="utf-8")

    manifest = _validates(render_manifest_template(workspace.resolve()))
    ids = [s.id for s in manifest.tool_sources]
    assert sorted(ids) == ["openapi_billing_openapi", "openapi_support_openapi"]


def test_source_id_helper_disambiguates_identically_sanitized_paths() -> None:
    # Sanitizing is lossy: `a-b/` and `a_b/` fold to the same string. Every
    # member of the group takes a digest of its own path, so walk order
    # never decides which one is renamed.
    entries = [("openapi", "a-b/spec.yaml"), ("openapi", "a_b/spec.yaml")]
    collided = assign_source_ids(entries)
    assert len(set(collided)) == 2
    assert all(value.startswith("openapi_a_b_spec_") for value in collided)
    # Assignment is a pure function of the entries, not of the call.
    assert collided == assign_source_ids(entries)
    # Reordering the same set renames the same files, not different ones.
    assert dict(zip([p for _t, p in entries], collided, strict=True)) == dict(
        zip(
            [p for _t, p in reversed(entries)],
            assign_source_ids(list(reversed(entries))),
            strict=True,
        )
    )

    # Unknown source types (third-party adapters) keep their own name.
    assert source_id_for("my_custom_source", "specs/api.yaml") == (
        "my_custom_source_specs_api"
    )


def test_source_ids_shift_only_for_entries_whose_ids_collide(
    tmp_path: Path,
) -> None:
    """The narrowed stability guarantee, pinned in both directions.

    An id is a pure function of its own path *unless it collides* with
    another entry's id; then both members take a digest, so the one that
    was already there changes too. Making that file-local would mean a
    digest on every id, including the readable common ones. Nothing
    outside the collision is re-keyed.
    """
    alone = assign_source_ids([("openapi", "a-b/spec.yaml")])
    assert alone == ["openapi_a_b_spec"]

    with_twin = assign_source_ids(
        [("openapi", "a-b/spec.yaml"), ("openapi", "a_b/spec.yaml")]
    )
    assert with_twin[0] != alone[0]
    assert with_twin[0].startswith("openapi_a_b_spec_")

    # An unrelated third source is untouched by that group's rename.
    with_bystander = assign_source_ids(
        [
            ("openapi", "a-b/spec.yaml"),
            ("openapi", "a_b/spec.yaml"),
            ("openapi", "billing/openapi.yaml"),
        ]
    )
    assert with_bystander[:2] == with_twin
    assert with_bystander[2] == "openapi_billing_openapi"


def test_source_id_length_bound_holds_after_disambiguation() -> None:
    """The cap is enforced on the value that ships, not on the value before
    the collision digest is appended: two near-limit paths that sanitize
    alike used to render 67-character ids."""
    near_limit = ["a" * 20 + "/" + "b" * 20 + f"/spec{sep}one.yaml" for sep in "-_"]
    plain = [source_id_for("openapi", path) for path in near_limit]
    assert len(plain[0]) == 58 and plain[0] == plain[1]  # fits, and collides

    resolved = assign_source_ids([("openapi", path) for path in near_limit])
    assert len(set(resolved)) == 2
    assert all(len(value) <= MAX_SOURCE_ID_LENGTH for value in resolved)

    # A source-type prefix long enough to crowd out the digest is truncated
    # too, so the bound holds for third-party adapter names of any length.
    long_type = "my_" + "very_" * 15 + "custom_source"
    assert len(source_id_for(long_type, "specs/api.yaml")) <= MAX_SOURCE_ID_LENGTH
    long_prefix_collision = assign_source_ids(
        [(long_type, "a-b/spec.yaml"), (long_type, "a_b/spec.yaml")]
    )
    assert len(set(long_prefix_collision)) == 2
    assert all(len(v) <= MAX_SOURCE_ID_LENGTH for v in long_prefix_collision)

    # Deep monorepo paths stay readable: most specific segments, then a
    # digest of the full path so truncation cannot merge two files.
    deep = source_id_for(
        "openai_agents_sdk",
        "packages/services/payments/src/agents/tools/refunds/tools.py",
    )
    assert len(deep) <= MAX_SOURCE_ID_LENGTH
    assert deep.startswith("openai_sdk_")
    assert "refunds_tools" in deep
    assert deep != source_id_for(
        "openai_agents_sdk",
        "packages/services/billing/src/agents/tools/refunds/tools.py",
    )


def test_source_ids_stay_unique_when_a_plain_id_matches_a_digest_form() -> None:
    """Contrived but constructible: a file named after the digest another
    entry will be given. `--minimal` has no validation gate behind it, so
    the assignment must not hand a duplicate to the render — and it must
    settle that conflict without re-keying anything else."""
    twin_digest = _digest("a-b/spec.yaml", 8)
    entries = [
        ("openapi", "a-b/spec.yaml"),
        ("openapi", "a_b/spec.yaml"),
        ("openapi", f"a_b/spec_{twin_digest}.yaml"),
        ("openapi", "billing/openapi.yaml"),
    ]
    # The third file's plain id is exactly what the first one is renamed to.
    assert source_id_for(*entries[2]) == f"openapi_a_b_spec_{twin_digest}"

    resolved = assign_source_ids(entries)
    assert len(set(resolved)) == 4
    assert all(len(value) <= MAX_SOURCE_ID_LENGTH for value in resolved)
    # Only the two entries that actually tied move to the wider digest: the
    # third member of the sanitized class keeps its 8-hex id, and the
    # bystander keeps the id its own path produced.
    assert resolved[1] == f"openapi_a_b_spec_{_digest('a_b/spec.yaml', 8)}"
    assert resolved[3] == "openapi_billing_openapi"


# The pair below shares an 8-hex SHA-256 prefix *and* one sanitized class,
# so the first disambiguation round hands both entries the same id. Found by
# enumerating the 2**17 `-`/`_` spellings of one path and looking for a
# repeated digest — seconds of work, which is why a digest prefix cannot be
# treated as a unique key.
_DIGEST_PREFIX_TWINS = (
    "a_b_c-d_e-f-g_h_i-j_k_l_m-n-o-p_q_r/spec.yaml",
    "a-b_c_d-e_f_g_h-i-j-k_l-m_n_o-p_q-r/spec.yaml",
)


def test_source_ids_survive_a_real_digest_prefix_collision() -> None:
    first, second = _DIGEST_PREFIX_TWINS
    assert source_id_for("openapi", first) == source_id_for("openapi", second)
    assert _digest(first, 8) == _digest(second, 8)

    resolved = assign_source_ids([("openapi", first), ("openapi", second)])
    assert len(set(resolved)) == 2
    assert all(len(value) <= MAX_SOURCE_ID_LENGTH for value in resolved)
    # Settled by widening, not by numbering: still a digest of each path.
    assert resolved[0].endswith(_digest(first, 16))
    assert resolved[1].endswith(_digest(second, 16))


def test_source_ids_number_tied_paths_when_every_digest_width_collides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uniqueness must not rest on an assumption about the hash. With the
    digest forced to collide at every width, the tied paths are numbered in
    sorted-path order — a property of the set, not of the walk."""
    monkeypatch.setattr(source_ids, "_digest", lambda path, width: "f" * width)
    entries = [
        ("openapi", "a-b/spec.yaml"),
        ("openapi", "a_b/spec.yaml"),
        ("openapi", "a.b/spec.yaml"),
    ]

    resolved = assign_source_ids(entries)
    assert len(set(resolved)) == 3
    assert all(len(value) <= MAX_SOURCE_ID_LENGTH for value in resolved)
    # "a-b" < "a.b" < "a_b" by path, so that is the numbering order.
    assert [resolved[0][-2:], resolved[2][-2:], resolved[1][-2:]] == ["_0", "_1", "_2"]
    assert assign_source_ids(list(reversed(entries))) == list(reversed(resolved))
