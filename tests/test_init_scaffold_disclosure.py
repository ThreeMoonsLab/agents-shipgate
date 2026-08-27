"""#441 — ``init`` writes a manifest for a workspace ``detect`` declines.

Three defects, one walk of ``awslabs/mcp#4489`` (a FastMCP Python MCP server):

1. ``detect`` says "not a Shipgate target"; ``init`` — the command the control
   loop routes to from ``verify --preview`` — writes a manifest anyway, and
   reports it exactly as it reports a manifest it inferred.
2. The manifest it writes declares ``type: openapi`` for a Python MCP server.
   ``id`` and ``path`` were flagged as placeholders; ``type`` was not, so the
   one value the tool chose without evidence was the one nothing told the
   reader to question.
3. The server's tools live at
   ``awslabs/billing_cost_management_mcp_server/tools/`` and
   ``has_tools_dir`` was false, because the conventional-dir check read only
   the workspace root.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.discovery import detect_workspace, render_auto_manifest
from agents_shipgate.cli.discovery.artifacts import render_manifest_template
from agents_shipgate.cli.discovery.manifest_scaffold import (
    SCAFFOLD_SUMMARY,
    scaffold_tool_sources_block,
)
from agents_shipgate.cli.discovery.placeholders import collect_placeholders
from agents_shipgate.cli.main import app
from agents_shipgate.schemas.manifest import (
    BUILTIN_TOOL_SOURCE_TYPES,
    MANIFEST_PLACEHOLDER_VALUE,
    AgentsShipgateManifest,
)


def _fastmcp_server(root: Path) -> Path:
    """The reproduction: a FastMCP Python server whose tools sit under the
    import package, not the workspace root."""

    workspace = root / "src" / "billing-cost-management-mcp-server"
    package = workspace / "awslabs" / "billing_cost_management_mcp_server"
    (package / "tools").mkdir(parents=True)
    (workspace / "pyproject.toml").write_text(
        '[project]\nname = "awslabs.billing-cost-management-mcp-server"\n'
        'version = "0.1.0"\ndependencies = ["mcp[cli]>=1.11.0"]\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "server.py").write_text(
        "from mcp.server.fastmcp import FastMCP\n\n"
        "server = FastMCP(name='billing-cost-management-mcp-server')\n\n\n"
        "@server.tool(name='budgets', description='Get budgets')\n"
        "async def budgets(ctx):\n    return {}\n",
        encoding="utf-8",
    )
    (package / "tools" / "budget_tools.py").write_text(
        "async def budget_server(ctx):\n    return {}\n", encoding="utf-8"
    )
    return workspace


# --- Defect 3: the conventional dir below the workspace root ----------------


def test_nested_tools_dir_contributes_its_conventional_dir_signal(
    tmp_path: Path,
) -> None:
    result = detect_workspace(_fastmcp_server(tmp_path))
    assert result.workspace_signals.has_tools_dir is True
    # The path, not the name. Once the signal reads the whole tree a bare
    # `tools` is no longer a location, and this repository has no `./tools`
    # for a reader following the field to open.
    assert result.workspace_signals.conventional_dirs == [
        "awslabs/billing_cost_management_mcp_server/tools"
    ]


def test_negative_control_names_a_directory_that_exists(tmp_path: Path) -> None:
    workspace = _fastmcp_server(tmp_path)
    result = detect_workspace(workspace)
    (found,) = result.workspace_signals.conventional_dirs
    assert (workspace / found).is_dir()
    assert not (workspace / "tools").exists()


def test_conventional_dir_at_the_root_still_counts_when_empty(tmp_path: Path) -> None:
    """The inventory is a list of *files*; an empty directory has no entry in
    it. The root check stays for exactly that reason."""

    (tmp_path / "prompts").mkdir()
    signals = detect_workspace(tmp_path).workspace_signals
    assert signals.has_prompts_dir is True
    assert signals.conventional_dirs == ["prompts"]


def test_many_nested_tools_dirs_contribute_one_signal_each(tmp_path: Path) -> None:
    """Deduped by name, so a monorepo cannot accumulate the weak credit.

    The evidence line names the shallowest occurrence, because that is the one
    a reader would have looked for.
    """

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["langchain"]\n',
        encoding="utf-8",
    )
    for service in ("alpha", "beta", "gamma"):
        directory = tmp_path / "services" / service / "tools"
        directory.mkdir(parents=True)
        (directory / "helper.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "agent.py").write_text(
        "from langchain.agents import initialize_agent\n", encoding="utf-8"
    )
    result = detect_workspace(tmp_path)
    langchain = next(fw for fw in result.frameworks if fw.type == "langchain")
    dir_evidence = [line for line in langchain.evidence if "conventional dir" in line]
    assert dir_evidence == ["conventional dir: services/alpha/tools/"]


def test_negative_control_names_the_conventional_dir_it_found(tmp_path: Path) -> None:
    """The flat "no tool artifacts" list stopped being the whole truth once the
    signal read below the root: the same payload reported ``has_tools_dir``."""

    workspace = _fastmcp_server(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["detect", "--workspace", str(workspace), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    (diagnostic,) = payload["diagnostics"]
    assert diagnostic["id"] == "SHIP-DIAG-NO-AGENT-SURFACE"
    why = diagnostic["next_actions"][0]["why"]
    assert "awslabs/billing_cost_management_mcp_server/tools/" in why
    assert "A conventional directory alone is not one." in why


def test_a_nested_prompts_dir_is_not_a_pure_prompt_experiment(tmp_path: Path) -> None:
    """"Only prompts/ is present" is a claim about the shape of the workspace.

    Widening ``has_prompts_dir`` to mean "anywhere" made that negative control
    fire on a TypeScript MCP server with ``src/prompts/`` — the mongodb-mcp-server
    shape the issue names as the common case — and the sentence it publishes is
    contradicted by the thirty source files beside it.
    """

    (tmp_path / "package.json").write_text(
        '{"name": "mongodb-mcp-server"}', encoding="utf-8"
    )
    source = tmp_path / "src" / "server"
    source.mkdir(parents=True)
    for index in range(30):
        (source / f"mod{index}.ts").write_text(
            f"export const x{index} = 1;\n", encoding="utf-8"
        )
    prompts = tmp_path / "src" / "prompts"
    prompts.mkdir()
    (prompts / "system.md").write_text("hello\n", encoding="utf-8")

    runner = CliRunner()
    payload = json.loads(
        runner.invoke(app, ["detect", "--workspace", str(tmp_path), "--json"]).stdout
    )
    assert payload["workspace_signals"]["has_prompts_dir"] is True
    (diagnostic,) = payload["diagnostics"]
    assert diagnostic["id"] == "SHIP-DIAG-NO-AGENT-SURFACE"
    assert "src/prompts/" in diagnostic["next_actions"][0]["why"]


def test_a_root_prompts_dir_is_still_a_pure_prompt_experiment(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "a.md").write_text("hi\n", encoding="utf-8")

    runner = CliRunner()
    payload = json.loads(
        runner.invoke(app, ["detect", "--workspace", str(tmp_path), "--json"]).stdout
    )
    (diagnostic,) = payload["diagnostics"]
    assert diagnostic["id"] == "SHIP-DIAG-PURE-PROMPT-EXPERIMENT"


# --- Defect 2: a field chosen without evidence is flagged as one -------------


def test_scaffold_marks_type_as_unresolved(tmp_path: Path) -> None:
    rendered = render_auto_manifest(tmp_path, detect_workspace(tmp_path))
    assert rendered.tool_surface_origin == "scaffold"
    manifest = AgentsShipgateManifest.model_validate(yaml.safe_load(rendered.text))
    (source,) = manifest.tool_sources
    assert source.type == MANIFEST_PLACEHOLDER_VALUE
    assert source.id == MANIFEST_PLACEHOLDER_VALUE
    assert source.path == MANIFEST_PLACEHOLDER_VALUE


def test_scaffold_type_reaches_placeholders(tmp_path: Path) -> None:
    """The acceptance criterion: every field the template chose without
    evidence is in ``placeholders[]``."""

    rendered = render_auto_manifest(tmp_path, detect_workspace(tmp_path))
    paths = {entry["path"] for entry in collect_placeholders(rendered.text)}
    assert {
        "tool_sources[0].id",
        "tool_sources[0].type",
        "tool_sources[0].path",
    } <= paths


def test_scaffold_comment_lists_every_accepted_type(tmp_path: Path) -> None:
    """Rendered from the schema's tuple, so a new built-in adapter cannot leave
    this comment describing a set the loader no longer accepts."""

    comment = "\n".join(
        line for line in scaffold_tool_sources_block() if line.startswith("#")
    )
    for source_type in BUILTIN_TOOL_SOURCE_TYPES:
        assert source_type in comment


def test_minimal_template_scaffolds_the_same_way(tmp_path: Path) -> None:
    """``--minimal`` is the route ``init`` publishes when the auto render fails
    validation, so leaving the guess there would hand the recovery path the
    defect."""

    rendered = render_manifest_template(tmp_path.resolve())
    assert rendered.tool_surface_origin == "scaffold"
    manifest = AgentsShipgateManifest.model_validate(yaml.safe_load(rendered.text))
    assert [source.type for source in manifest.tool_sources] == [
        MANIFEST_PLACEHOLDER_VALUE
    ]


# --- Defect 1: init says what it wrote --------------------------------------


def test_init_states_that_the_source_block_is_a_scaffold(tmp_path: Path) -> None:
    workspace = _fastmcp_server(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["init", "--workspace", str(workspace), "--write", "--json"]
    )
    payload = json.loads(result.stdout)

    assert payload["created"] is True
    assert payload["tool_surface_origin"] == "scaffold"
    assert SCAFFOLD_SUMMARY in payload["manifest_message"]
    assert "tool_sources[0].type" in {
        entry["path"] for entry in payload["placeholders"]
    }


def test_init_dry_run_control_reason_states_the_scaffold(tmp_path: Path) -> None:
    """A dry run writes nothing, so no manifest placeholder outranks the
    advance and the envelope's reason is init's own."""

    workspace = _fastmcp_server(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert payload["tool_surface_origin"] == "scaffold"
    assert SCAFFOLD_SUMMARY in payload["control"]["reason"]
    assert payload["control"]["next_action"]["command"] is not None


def test_init_human_output_states_the_scaffold(tmp_path: Path) -> None:
    workspace = _fastmcp_server(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--workspace", str(workspace), "--write"])
    assert SCAFFOLD_SUMMARY in result.stdout


def test_init_on_a_detected_workspace_is_unaffected(tmp_path: Path) -> None:
    """Regression guard for the samples ``detect`` accepts."""

    import shutil

    samples = Path(__file__).resolve().parent.parent / "samples"
    for name in ("google_adk_agent", "mcp_only_server"):
        workspace = tmp_path / name
        shutil.copytree(samples / name, workspace)
        (workspace / "shipgate.yaml").unlink(missing_ok=True)
        runner = CliRunner()
        result = runner.invoke(
            app, ["init", "--workspace", str(workspace), "--json"]
        )
        payload = json.loads(result.stdout)
        assert payload["tool_surface_origin"] == "detected", name
        assert MANIFEST_PLACEHOLDER_VALUE not in yaml.safe_load(payload["template"])[
            "tool_sources"
        ][0]["type"], name


def test_scaffold_advance_is_an_edit_not_a_scan(tmp_path: Path) -> None:
    """A published next step has to be able to change the answer. A scan of the
    scaffold cannot: the registry has nothing to dispatch ``CHANGE_ME`` to.

    Asserted on ``_init_advance`` directly because that is where the route is
    selected, and because the human-owned ``declared_purpose`` placeholder
    currently outranks this route on every freshly written manifest — the
    behaviour a live payload shows is the one asserted in
    ``test_written_scaffold_publishes_the_human_declaration`` below.
    """

    from agents_shipgate.cli._register_init import _init_advance
    from agents_shipgate.schemas.diagnostics import NextAction

    target = tmp_path / "shipgate.yaml"
    scan = NextAction(
        kind="command",
        command="agents-shipgate scan -c shipgate.yaml",
        why="w",
        expects="e",
    )
    advance, kind, decision, blocking = _init_advance(
        workspace=tmp_path,
        target=target,
        write=True,
        manifest_status="written",
        manifest_exit=0,
        next_action_create=scan,
        skipped_target=None,
        tool_surface_origin="scaffold",
    )
    assert (kind, decision, blocking) == ("configure", "setup_incomplete", False)
    assert advance.kind == "edit"
    assert advance.path == str(target)
    assert advance.command is None

    detected, kind, decision, _ = _init_advance(
        workspace=tmp_path,
        target=target,
        write=True,
        manifest_status="written",
        manifest_exit=0,
        next_action_create=scan,
        skipped_target=None,
    )
    assert detected is scan
    assert (kind, decision) == ("rerun", "setup_complete")


def test_written_scaffold_publishes_the_human_declaration(tmp_path: Path) -> None:
    """What a caller actually reads after ``init --write`` on a scaffold.

    The declaration outranks every agent-owned placeholder, so no command is
    offered — which is why ``tool_surface_origin`` and ``manifest_message``
    have to carry the scaffold fact, and why neither may be dropped.
    """

    workspace = _fastmcp_server(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["init", "--workspace", str(workspace), "--write", "--json"]
    )
    payload = json.loads(result.stdout)
    assert payload["control"]["control_state"] == "human_review_required"
    assert payload["control"]["next_action"]["command"] is None
    assert payload["tool_surface_origin"] == "scaffold"
    assert SCAFFOLD_SUMMARY in payload["manifest_message"]


def test_tool_surface_origin_is_null_when_this_run_wrote_nothing(
    tmp_path: Path,
) -> None:
    """Same authority rule ``placeholders`` follows: on ``skipped_existing``
    the render was discarded, so it describes no file the caller can open."""

    workspace = _fastmcp_server(tmp_path)
    runner = CliRunner()
    first = runner.invoke(
        app, ["init", "--workspace", str(workspace), "--write", "--json"]
    )
    assert json.loads(first.stdout)["tool_surface_origin"] == "scaffold"

    second = runner.invoke(
        app, ["init", "--workspace", str(workspace), "--write", "--json"]
    )
    payload = json.loads(second.stdout)
    assert payload["manifest_status"] == "skipped_existing"
    assert payload["tool_surface_origin"] is None


# --- The scaffold's own failure is routed as a placeholder ------------------


def test_registry_message_names_the_placeholder_not_a_missing_package() -> None:
    """``type: CHANGE_ME`` is the first thing a scan of the scaffold trips on.

    Before it was routed, the message told the reader to enable third-party
    adapter discovery and install a package — for a value the tool itself
    wrote.
    """

    from agents_shipgate.core.errors import ConfigError
    from agents_shipgate.inputs.protocol import REGISTRY

    try:
        REGISTRY.require(MANIFEST_PLACEHOLDER_VALUE)
    except ConfigError as exc:
        message = str(exc)
    else:  # pragma: no cover - require() must raise for an unknown type
        raise AssertionError("expected ConfigError")

    assert "placeholder" in message
    assert "AGENTS_SHIPGATE_ENABLE_PLUGINS" not in message
    assert "install" not in message


def test_unknown_adapter_diagnostic_routes_the_placeholder_to_an_edit(
    tmp_path: Path,
) -> None:
    from agents_shipgate.cli.diagnostics import diagnose_unknown_adapter_source_type

    manifest = tmp_path / "shipgate.yaml"
    (diagnostic,) = diagnose_unknown_adapter_source_type(
        manifest,
        source_type=MANIFEST_PLACEHOLDER_VALUE,
        plugins_enabled=False,
        message="No adapter registered for source type 'CHANGE_ME'.",
    )
    rank_one = diagnostic.next_actions[0]
    assert rank_one.kind == "edit"
    assert rank_one.path == str(manifest)
    assert "pip install" not in " ".join(
        action.command or "" for action in diagnostic.next_actions
    )


def test_unknown_adapter_diagnostic_lists_every_builtin_type(tmp_path: Path) -> None:
    """Both prose copies of this list had dropped ``codex_config`` and
    ``conductor``, so the recovery named two accepted values as unaccepted."""

    from agents_shipgate.cli.diagnostics import diagnose_unknown_adapter_source_type

    for plugins_enabled in (True, False):
        (diagnostic,) = diagnose_unknown_adapter_source_type(
            tmp_path / "shipgate.yaml",
            source_type="openapii",
            plugins_enabled=plugins_enabled,
            message="No adapter registered for source type 'openapii'.",
        )
        prose = " ".join(action.why or "" for action in diagnostic.next_actions)
        for source_type in BUILTIN_TOOL_SOURCE_TYPES:
            assert source_type in prose, (plugins_enabled, source_type)


def test_minimal_template_on_a_bare_workspace_validates(tmp_path: Path) -> None:
    """The guard that selects the scaffold used to test the artifact *dict*,
    which has fixed keys and is always truthy — so a source-less workspace got
    an empty ``openai_api:`` block and a manifest the schema rejects."""

    rendered = render_manifest_template(tmp_path.resolve())
    AgentsShipgateManifest.model_validate(yaml.safe_load(rendered.text))
    assert "openai_api:" not in rendered.text


def test_minimal_template_still_emits_openai_api_for_a_real_artifact(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "openai-tools.json").write_text(
        '{"tools": [{"type": "function", "function": {"name": "t", '
        '"parameters": {"type": "object", "properties": {}}}}]}',
        encoding="utf-8",
    )
    rendered = render_manifest_template(tmp_path.resolve())
    assert rendered.tool_surface_origin == "detected"
    assert "openai_api:" in rendered.text


def test_minimal_template_does_not_declare_openai_api_for_bare_prompts(
    tmp_path: Path,
) -> None:
    """A bare ``prompts/`` is ambiguous — an Anthropic-only project has one —
    so neither renderer anchors an ``openai_api:`` block on it.

    Before the dead-guard fix the ``--minimal`` renderer emitted the block for
    every workspace, this one included. Pinned so the unmarked declaration
    cannot come back.
    """

    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system.md").write_text("hello\n", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "svc.openapi.yaml").write_text(
        "openapi: 3.1.0\ninfo:\n  title: T\n  version: '1'\npaths: {}\n",
        encoding="utf-8",
    )
    rendered = render_manifest_template(tmp_path.resolve())
    assert rendered.tool_surface_origin == "detected"
    assert "tools/svc.openapi.yaml" in rendered.text
    assert "openai_api:" not in rendered.text
    AgentsShipgateManifest.model_validate(yaml.safe_load(rendered.text))


def test_conventional_dir_scan_reports_the_shallowest_of_many(tmp_path: Path) -> None:
    """Correctness with many files sharing few parents — the shape the
    inventory actually has.

    The *cost* of that shape is why the scan walks distinct parent directories
    on strings rather than calling ``relative_to`` per file: the obvious
    spelling took 4.4 s on a 120k-file inventory, against 42 ms for this one,
    on a whole-workspace pass ``detect`` already runs for exactly the monorepos
    #363 and #395 are about. That is a benchmark, not an assertion — wall-clock
    does not belong in a unit test — so what is pinned here is the answer.
    """

    from agents_shipgate.cli.discovery.signals import _conventional_dir_locations

    root = tmp_path.resolve()
    files = [
        root / "services" / f"s{index % 4}" / "tools" / f"f{index}.py"
        for index in range(400)
    ]
    files.append(root / "z" / "prompts" / "late.md")
    assert _conventional_dir_locations(root, files=files) == {
        "prompts": "z/prompts",
        "tools": "services/s0/tools",
    }


def test_a_file_at_the_workspace_root_contributes_no_conventional_dir(
    tmp_path: Path,
) -> None:
    """``tools.py`` at the root is a file, not a ``tools/`` directory.

    The behaviour, not the fast path that implements it: the early ``continue``
    for a root-level file only saves work — with it removed the empty parent
    slice yields nothing either way — so this pins the answer rather than
    pretending to guard the branch.
    """

    from agents_shipgate.cli.discovery.signals import _conventional_dir_locations

    root = tmp_path.resolve()
    assert _conventional_dir_locations(root, files=[root / "tools.py"]) == {}


def test_conventional_dir_scan_ignores_paths_outside_the_workspace(
    tmp_path: Path,
) -> None:
    """A resolved symlink can point out of the tree; it is not this workspace's
    signal. ``relative_to`` raised for these, and the prefix test replaced it."""

    from agents_shipgate.cli.discovery.signals import _conventional_dir_locations

    root = (tmp_path / "ws").resolve()
    root.mkdir()
    outside = (tmp_path / "elsewhere" / "tools" / "x.py").resolve()
    assert _conventional_dir_locations(root, files=[outside]) == {}
