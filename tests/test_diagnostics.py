"""Tests for the ranked next-action diagnostics module."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agents_shipgate.cli import diagnostics as diag_mod
from agents_shipgate.cli.diagnostics import (
    ALL_DIAGNOSTIC_IDS,
    DIAG_CHANGE_ME_PLACEHOLDERS,
    DIAG_DYNAMIC_TOOLSETS_ONLY,
    DIAG_INVALID_MANIFEST,
    DIAG_MCP_OPENAPI_ARTIFACT_ONLY,
    DIAG_MISSING_MANIFEST,
    DIAG_MISSING_SOURCE_FILE,
    DIAG_NO_AGENT_SURFACE,
    DIAG_NO_PRODUCTION_PERMISSIONS,
    DIAG_NON_AGENT_LIBRARY,
    DIAG_PURE_PROMPT_EXPERIMENT,
    DIAG_ZERO_TOOLS,
    Diagnostic,
    NextAction,
    diagnose_detect,
    diagnose_doctor,
    diagnose_invalid_manifest,
    diagnose_missing_manifest,
    top_next_actions,
)
from agents_shipgate.cli.discovery.signals import (
    DetectResult,
    WorkspaceSignals,
)
from agents_shipgate.cli.main import app as typer_app

# --- NextAction model invariants -------------------------------------------


class TestNextActionValidator:
    def test_command_kind_requires_command(self) -> None:
        with pytest.raises(ValueError, match="kind='command' requires"):
            NextAction(kind="command", command=None, why="x")

    def test_edit_kind_requires_path(self) -> None:
        with pytest.raises(ValueError, match="kind='edit' requires"):
            NextAction(kind="edit", path=None, why="x")

    def test_stop_kind_forbids_command(self) -> None:
        with pytest.raises(ValueError, match="kind='stop' must not"):
            NextAction(kind="stop", command="oops", why="x")

    def test_review_kind_minimal(self) -> None:
        action = NextAction(kind="review", why="check it")
        assert action.command is None
        assert action.path is None


class TestNextActionLegacyProjection:
    def test_command_projects_to_command_string(self) -> None:
        action = NextAction(kind="command", command="agents-shipgate scan", why="x")
        assert action.to_legacy_string() == "agents-shipgate scan"

    def test_edit_projects_to_edit_path(self) -> None:
        action = NextAction(kind="edit", path="shipgate.yaml:14", why="x")
        assert action.to_legacy_string() == "Edit shipgate.yaml:14"

    def test_review_projects_to_review_why(self) -> None:
        action = NextAction(kind="review", why="needs human review")
        assert action.to_legacy_string() == "Review: needs human review"

    def test_stop_projects_to_stop_why(self) -> None:
        action = NextAction(kind="stop", why="not an agent project")
        assert action.to_legacy_string() == "Stop: not an agent project"

    def test_legacy_string_is_non_empty_for_every_kind(self) -> None:
        kinds = ["command", "edit", "review", "stop"]
        actions = [
            NextAction(kind="command", command="cmd", why="w"),
            NextAction(kind="edit", path="p", why="w"),
            NextAction(kind="review", why="w"),
            NextAction(kind="stop", why="w"),
        ]
        for kind, action in zip(kinds, actions, strict=True):
            assert action.to_legacy_string(), f"{kind} produced empty string"


# --- Diagnostic invariants --------------------------------------------------


class TestDiagnosticInvariants:
    def test_next_actions_min_length_one(self) -> None:
        with pytest.raises(ValueError):
            Diagnostic(
                id="SHIP-DIAG-X",
                title="t",
                severity="info",
                next_actions=[],
            )

    def test_severity_must_be_known(self) -> None:
        with pytest.raises(ValueError):
            Diagnostic(
                id="SHIP-DIAG-X",
                title="t",
                severity="oops",  # type: ignore[arg-type]
                next_actions=[NextAction(kind="review", why="w")],
            )


# --- Catalog stability ------------------------------------------------------


class TestCatalogStability:
    def test_all_ids_have_diag_prefix(self) -> None:
        for diag_id in ALL_DIAGNOSTIC_IDS:
            assert diag_id.startswith("SHIP-DIAG-"), diag_id

    def test_all_ids_unique(self) -> None:
        assert len(ALL_DIAGNOSTIC_IDS) == len(set(ALL_DIAGNOSTIC_IDS))

    def test_all_ids_listed_in_diagnostics_doc(self) -> None:
        doc_path = (
            Path(__file__).resolve().parents[1] / "docs" / "diagnostics.md"
        )
        if not doc_path.is_file():
            pytest.skip("docs/diagnostics.md is added in PR7; skipping")
        text = doc_path.read_text(encoding="utf-8")
        for diag_id in ALL_DIAGNOSTIC_IDS:
            assert diag_id in text, f"{diag_id} not documented in {doc_path}"


# --- diagnose_missing_manifest ---------------------------------------------


class TestDiagnoseMissingManifest:
    def test_emits_missing_manifest_diagnostic(self, tmp_path: Path) -> None:
        diags = diagnose_missing_manifest(tmp_path)
        assert [d.id for d in diags] == [DIAG_MISSING_MANIFEST]
        assert diags[0].severity == "block"
        assert diags[0].next_actions[0].kind == "command"
        assert "agents-shipgate detect" in diags[0].next_actions[0].command

    def test_command_quotes_workspace_with_spaces(
        self, tmp_path: Path
    ) -> None:
        """Paths with spaces must round-trip through shlex.split()."""
        import shlex

        spaced = tmp_path / "with space"
        diags = diagnose_missing_manifest(spaced)
        for action in diags[0].next_actions:
            parts = shlex.split(action.command)
            assert parts[0] == "agents-shipgate"
            ws_idx = parts.index("--workspace")
            assert parts[ws_idx + 1] == str(spaced)


class TestDiagnoseInvalidManifest:
    def test_emits_edit_action_pointing_at_manifest(
        self, tmp_path: Path
    ) -> None:
        manifest = tmp_path / "shipgate.yaml"
        diags = diagnose_invalid_manifest(
            manifest, message="schema validation failed: project required"
        )
        assert [d.id for d in diags] == [DIAG_INVALID_MANIFEST]
        assert diags[0].severity == "block"
        # Rank-1 action points the agent at the file, not at detect/init —
        # init refuses to overwrite an existing file, so dispatching there
        # would loop.
        rank_one = diags[0].next_actions[0]
        assert rank_one.kind == "edit"
        assert str(manifest) in rank_one.path
        assert "schema validation failed" in rank_one.why


# --- diagnose_detect — negative-control precedence -------------------------


def _det(
    *,
    is_agent_project: bool = False,
    suggested_sources: list[dict[str, str]] | None = None,
    py_files: int = 0,
    pyproject: bool = False,
    prompts: bool = False,
    tools_dir: bool = False,
    conventional_dirs: list[str] | None = None,
) -> DetectResult:
    return DetectResult(
        is_agent_project=is_agent_project,
        suggested_sources=suggested_sources or [],
        workspace_signals=WorkspaceSignals(
            python_file_count=py_files,
            has_pyproject_or_requirements=pyproject,
            has_prompts_dir=prompts,
            has_tools_dir=tools_dir,
            conventional_dirs=conventional_dirs or [],
        ),
    )


class TestDiagnoseDetect:
    def test_pure_prompt_experiment(self, tmp_path: Path) -> None:
        result = _det(
            prompts=True, py_files=0, conventional_dirs=["prompts"]
        )
        diags = diagnose_detect(
            result, has_manifest=False, workspace=tmp_path
        )
        assert [d.id for d in diags] == [DIAG_PURE_PROMPT_EXPERIMENT]
        assert diags[0].next_actions[0].kind == "stop"

    def test_non_agent_library(self, tmp_path: Path) -> None:
        result = _det(py_files=12, pyproject=True)
        diags = diagnose_detect(
            result, has_manifest=False, workspace=tmp_path
        )
        assert [d.id for d in diags] == [DIAG_NON_AGENT_LIBRARY]
        assert diags[0].next_actions[0].kind == "stop"

    def test_no_agent_surface_catchall(self, tmp_path: Path) -> None:
        # Empty workspace (no python, no prompts, no tools, no pyproject)
        result = _det()
        diags = diagnose_detect(
            result, has_manifest=False, workspace=tmp_path
        )
        assert [d.id for d in diags] == [DIAG_NO_AGENT_SURFACE]
        assert diags[0].next_actions[0].kind == "stop"

    def test_mcp_openapi_artifact_only(self, tmp_path: Path) -> None:
        result = _det(
            is_agent_project=False,
            suggested_sources=[{"type": "mcp", "path": "tools.json"}],
        )
        diags = diagnose_detect(
            result, has_manifest=False, workspace=tmp_path
        )
        assert [d.id for d in diags] == [DIAG_MCP_OPENAPI_ARTIFACT_ONLY]
        action = diags[0].next_actions[0]
        assert action.kind == "command"
        assert "init --workspace" in action.command
        assert "--write" in action.command

    def test_pure_prompt_takes_precedence_over_non_agent(
        self, tmp_path: Path
    ) -> None:
        # Pure prompt: prompts present, py_files=0 — even if pyproject also
        # present, prompts dir is the most specific signal.
        result = _det(
            prompts=True,
            py_files=0,
            pyproject=True,
            conventional_dirs=["prompts"],
        )
        diags = diagnose_detect(
            result, has_manifest=False, workspace=tmp_path
        )
        assert DIAG_PURE_PROMPT_EXPERIMENT in [d.id for d in diags]
        assert DIAG_NON_AGENT_LIBRARY not in [d.id for d in diags]
        assert DIAG_NO_AGENT_SURFACE not in [d.id for d in diags]

    def test_no_diagnostics_when_manifest_present(
        self, tmp_path: Path
    ) -> None:
        # Even on a non-agent workspace, if a manifest exists, detect's
        # workspace-classification diagnostics are not interesting.
        result = _det()
        diags = diagnose_detect(
            result, has_manifest=True, workspace=tmp_path
        )
        assert diags == []

    def test_agent_project_no_diagnostics(self, tmp_path: Path) -> None:
        # When detection succeeded, this resolver has nothing to add.
        result = _det(is_agent_project=True, py_files=20, pyproject=True)
        diags = diagnose_detect(
            result, has_manifest=False, workspace=tmp_path
        )
        assert diags == []


# --- diagnose_doctor --------------------------------------------------------


class TestDiagnoseDoctor:
    def test_zero_tools(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "shipgate.yaml"
        manifest_path.write_text("version: '0.1'\n", encoding="utf-8")
        payload = {
            "total_tools": 0,
            "frameworks": {},
            "manifest_summary": {
                "environment_target": "local",
                "has_permissions": False,
                "has_policies": False,
                "scope_count": 0,
            },
            "unresolved_sources": [],
        }
        diags = diagnose_doctor(
            payload,
            manifest_path=manifest_path,
            manifest_text="",
            placeholders=[],
        )
        assert any(d.id == DIAG_ZERO_TOOLS for d in diags)

    def test_missing_source_file(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "shipgate.yaml"
        payload = {
            "total_tools": 5,
            "frameworks": {},
            "manifest_summary": {
                "environment_target": "local",
                "has_permissions": False,
                "has_policies": False,
                "scope_count": 0,
            },
            "unresolved_sources": [
                {
                    "id": "support_openapi",
                    "declared_path": "specs/missing.yaml",
                    "line": 14,
                }
            ],
        }
        diags = diagnose_doctor(
            payload,
            manifest_path=manifest_path,
            manifest_text="",
            placeholders=[],
        )
        diag = next(d for d in diags if d.id == DIAG_MISSING_SOURCE_FILE)
        assert diag.severity == "block"
        action = diag.next_actions[0]
        assert action.kind == "edit"
        assert "shipgate.yaml:14" in action.path

    def test_change_me_placeholders(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "shipgate.yaml"
        payload = {
            "total_tools": 1,
            "frameworks": {},
            "manifest_summary": {
                "environment_target": "local",
                "has_permissions": False,
                "has_policies": False,
                "scope_count": 1,
            },
            "unresolved_sources": [],
        }
        placeholders = [
            {"path": "agent.name", "current": "CHANGE_ME", "line": 4},
        ]
        diags = diagnose_doctor(
            payload,
            manifest_path=manifest_path,
            manifest_text="",
            placeholders=placeholders,
        )
        diag = next(d for d in diags if d.id == DIAG_CHANGE_ME_PLACEHOLDERS)
        assert diag.severity == "warn"
        assert diag.next_actions[0].kind == "edit"
        assert "shipgate.yaml:4" in diag.next_actions[0].path

    def test_dynamic_toolsets_only(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "shipgate.yaml"
        payload = {
            "total_tools": 1,
            "frameworks": {
                "google_adk": {"dynamic_toolset_count": 2},
                "langchain": {},
                "crewai": {},
            },
            "manifest_summary": {
                "environment_target": "local",
                "has_permissions": True,
                "has_policies": False,
                "scope_count": 1,
            },
            "unresolved_sources": [],
        }
        diags = diagnose_doctor(
            payload,
            manifest_path=manifest_path,
            manifest_text="",
            placeholders=[],
        )
        assert any(d.id == DIAG_DYNAMIC_TOOLSETS_ONLY for d in diags)

    def test_dynamic_toolsets_skipped_when_tools_present(
        self, tmp_path: Path
    ) -> None:
        manifest_path = tmp_path / "shipgate.yaml"
        payload = {
            "total_tools": 5,
            "frameworks": {
                "google_adk": {"dynamic_toolset_count": 1},
            },
            "manifest_summary": {
                "environment_target": "local",
                "has_permissions": True,
                "has_policies": False,
                "scope_count": 1,
            },
            "unresolved_sources": [],
        }
        diags = diagnose_doctor(
            payload,
            manifest_path=manifest_path,
            manifest_text="",
            placeholders=[],
        )
        assert all(
            d.id != DIAG_DYNAMIC_TOOLSETS_ONLY for d in diags
        )

    def test_production_no_permissions(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "shipgate.yaml"
        payload = {
            "total_tools": 3,
            "frameworks": {},
            "manifest_summary": {
                "environment_target": "production",
                "has_permissions": False,
                "has_policies": False,
                "scope_count": 0,
            },
            "unresolved_sources": [],
        }
        diags = diagnose_doctor(
            payload,
            manifest_path=manifest_path,
            manifest_text="",
            placeholders=[],
        )
        assert any(
            d.id == DIAG_NO_PRODUCTION_PERMISSIONS for d in diags
        )

    def test_no_diagnostics_for_healthy_manifest(
        self, tmp_path: Path
    ) -> None:
        manifest_path = tmp_path / "shipgate.yaml"
        payload = {
            "total_tools": 5,
            "frameworks": {},
            "manifest_summary": {
                "environment_target": "production",
                "has_permissions": True,
                "has_policies": True,
                "scope_count": 3,
            },
            "unresolved_sources": [],
        }
        diags = diagnose_doctor(
            payload,
            manifest_path=manifest_path,
            manifest_text="",
            placeholders=[],
        )
        assert diags == []


# --- top_next_actions -------------------------------------------------------


class TestTopNextActions:
    def test_empty_input_returns_empty(self) -> None:
        assert top_next_actions([]) == []

    def test_severity_ordering_block_warn_info(self) -> None:
        warn = Diagnostic(
            id="SHIP-DIAG-W",
            title="warn",
            severity="warn",
            next_actions=[
                NextAction(kind="command", command="warn-cmd", why="w")
            ],
        )
        block = Diagnostic(
            id="SHIP-DIAG-B",
            title="block",
            severity="block",
            next_actions=[
                NextAction(kind="command", command="block-cmd", why="w")
            ],
        )
        info = Diagnostic(
            id="SHIP-DIAG-I",
            title="info",
            severity="info",
            next_actions=[
                NextAction(kind="command", command="info-cmd", why="w")
            ],
        )
        # Pass in non-severity order to verify the sort.
        flattened = top_next_actions([warn, info, block])
        assert [a.command for a in flattened] == [
            "block-cmd",
            "warn-cmd",
            "info-cmd",
        ]

    def test_limit_caps_output(self) -> None:
        diags = [
            Diagnostic(
                id=f"SHIP-DIAG-{i}",
                title="t",
                severity="warn",
                next_actions=[
                    NextAction(kind="command", command=f"c{i}", why="w")
                ],
            )
            for i in range(5)
        ]
        assert len(top_next_actions(diags, limit=3)) == 3


# --- Cross-check: command actions reference real subcommands ---------------


class TestRankOneCommandsAreRoutable:
    def _typer_subcommands(self) -> set[str]:
        names: set[str] = set()
        for command_info in typer_app.registered_commands:
            if command_info.name:
                names.add(command_info.name)
            else:
                # typer derives the command name from the callback name when
                # `name=` is not set (e.g. `scan`, `init`, `doctor`).
                names.add(command_info.callback.__name__)
        for group in typer_app.registered_groups:
            if group.name:
                names.add(group.name)
        return names

    def test_command_actions_use_known_subcommands(
        self, tmp_path: Path
    ) -> None:
        registered = self._typer_subcommands()
        # Build every diagnostic by calling each resolver on a triggering
        # input, then assert each kind="command" rank-1 action parses to a
        # subcommand the typer app actually exposes.
        commands_to_check: list[str] = []
        diags = diagnose_missing_manifest(tmp_path)
        commands_to_check.extend(
            a.command for d in diags for a in d.next_actions if a.kind == "command"
        )
        result = _det(
            is_agent_project=False,
            suggested_sources=[{"type": "mcp", "path": "x.json"}],
        )
        diags = diagnose_detect(
            result, has_manifest=False, workspace=tmp_path
        )
        commands_to_check.extend(
            a.command for d in diags for a in d.next_actions if a.kind == "command"
        )
        manifest_path = tmp_path / "shipgate.yaml"
        diags = diagnose_doctor(
            {
                "total_tools": 0,
                "frameworks": {},
                "manifest_summary": {
                    "environment_target": "local",
                    "has_permissions": False,
                    "has_policies": False,
                    "scope_count": 0,
                },
                "unresolved_sources": [],
            },
            manifest_path=manifest_path,
            manifest_text="",
            placeholders=[],
        )
        commands_to_check.extend(
            a.command for d in diags for a in d.next_actions if a.kind == "command"
        )

        assert commands_to_check, "expected at least one command action"
        pattern = re.compile(r"^agents-shipgate\s+([\w-]+)")
        for command in commands_to_check:
            match = pattern.match(command)
            assert match, f"command does not start with agents-shipgate: {command!r}"
            subcommand = match.group(1)
            assert subcommand in registered, (
                f"command {command!r} references unknown subcommand "
                f"{subcommand!r}; registered: {sorted(registered)}"
            )


# --- Module-level guard: catalog tuple is exhaustive -----------------------


def test_all_diag_constants_exported_in_all_diagnostic_ids() -> None:
    """Every ``DIAG_*`` module constant should be in ``ALL_DIAGNOSTIC_IDS``.
    Catches the bug where a new diagnostic is added but the catalog tuple
    forgets it (which would silently break the docs-link test)."""
    constants = {
        value
        for name, value in vars(diag_mod).items()
        if name.startswith("DIAG_") and isinstance(value, str)
    }
    assert constants == set(ALL_DIAGNOSTIC_IDS)
