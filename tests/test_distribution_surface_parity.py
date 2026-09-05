"""One engine, many distribution surfaces — and a test that they agree (#497).

`docs/distribution-surfaces.md` is the human-readable registry: each surface,
what it claims, and which test proves the claim. This module is the machine
half. The two are checked against each other, so neither can be edited alone.

The invariant, in both halves:

    Every surface that answers a question the engine also answers must give
    the engine's answer, or say in the registry what it does not answer.

`#485` is why this exists. After `#431` taught the CLI to read an MCP server's
tool surface out of TypeScript or Go source, `tools/shipgate-detect.py` — the
documented zero-install front door — went on answering ``is_agent_project:
false`` for the vendor MCP servers the CLI now accepts, and CI stayed green: the
existing parity test compares the two on ``samples/``, and no sample contained
one. The fixtures under ``tests/fixtures/distribution_parity/`` are that missing
case, minimized, and they are shared with `#485`'s conformance corpus.

**Known gaps are rows, not omissions.** A surface allowed to diverge gets an
entry in :data:`KNOWN_GAPS`, a row in the registry's *Known parity gaps* table
with an owning issue, and ``xfail(strict=True)`` on the parity row. It fails
today; the day the owning fix lands the row starts passing, the strict marker
turns that into a failure, and the gap has to be retired. A gap cannot rot here
unnoticed, which is the property the previous arrangement lacked.

**Offline by construction.** Resolvability is judged against committed release
metadata (``.well-known/agents-shipgate.json``), never the network. The live
check that the claimed tag exists on origin is the ``release-tag-consistency``
job in ``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from agents_shipgate import __version__
from agents_shipgate.cli.discovery import detect_workspace
from agents_shipgate.cli.discovery.agent_instructions.adoption_kit import _render_template
from agents_shipgate.cli.discovery.ci_workflow import (
    WORKFLOW_RELATIVE_PATH,
    write_ci_workflow,
)
from agents_shipgate.cli.discovery.placeholders import placeholder_owner
from agents_shipgate.cli.setup_control import _PLACEHOLDER_REVIEW_TAIL
from agents_shipgate.schemas.contract import (
    CONTRACT_VERSION,
    MERGE_VERDICTS,
    MINIMUM_CONTROL_CONTRACT_VERSION,
    RELEASE_DECISIONS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_DOC = REPO_ROOT / "docs" / "distribution-surfaces.md"
DETECTOR_SCRIPT = REPO_ROOT / "tools" / "shipgate-detect.py"
CORPUS_ROOT = REPO_ROOT / "tests" / "fixtures" / "distribution_parity"
SAMPLES_ROOT = REPO_ROOT / "samples"
WELL_KNOWN = REPO_ROOT / ".well-known" / "agents-shipgate.json"


# --------------------------------------------------------------------------
# The registry, in code. The mirror of `docs/distribution-surfaces.md`.
# --------------------------------------------------------------------------

#: Every claim a surface may make. Closed set: a surface that answers something
#: outside it is answering a question the engine does not, which is a product
#: decision and not a parity question.
CLAIMS = frozenset(
    {
        "agent_project_verdict",
        "merge_verdict_vocabulary",
        "release_decision_vocabulary",
        "placeholder_ownership",
        "executable_pin",
        "contract_floor",
    }
)


@dataclass(frozen=True)
class Surface:
    """One distribution surface and the engine answers it restates."""

    id: str
    #: Repo-relative paths. A surface with no claims still declares its roots —
    #: that is what keeps the top-level classifier honest.
    roots: tuple[str, ...]
    claims: frozenset[str]

    def paths(self) -> list[Path]:
        return [REPO_ROOT / root for root in self.roots]


SURFACES: tuple[Surface, ...] = (
    Surface("github_action", ("action.yml", "scripts/github_action_outputs.py"),
            frozenset({"merge_verdict_vocabulary"})),
    Surface("zero_install_detector", ("tools/shipgate-detect.py",),
            frozenset({"agent_project_verdict"})),
    Surface("emitted_ci_workflow", ("src/agents_shipgate/cli/discovery/ci_workflow.py",),
            frozenset({"executable_pin"})),
    Surface("prompts", ("prompts",),
            frozenset({"executable_pin", "contract_floor", "merge_verdict_vocabulary",
                       "placeholder_ownership"})),
    Surface("skills", ("skills",),
            frozenset({"executable_pin", "contract_floor", "merge_verdict_vocabulary",
                       "placeholder_ownership"})),
    Surface("plugins", ("plugins",),
            frozenset({"executable_pin", "contract_floor", "merge_verdict_vocabulary",
                       "placeholder_ownership"})),
    Surface("adoption_kits", ("adoption-kits",),
            frozenset({"executable_pin", "contract_floor", "merge_verdict_vocabulary",
                       "placeholder_ownership"})),
    Surface("examples", ("examples",),
            frozenset({"executable_pin", "merge_verdict_vocabulary"})),
    Surface("policies", ("policies",), frozenset()),
    Surface("harness", ("harness",), frozenset({"merge_verdict_vocabulary"})),
    Surface("mcp_server", ("src/agents_shipgate/mcp_server",), frozenset()),
    Surface("design_partner_runbook", ("docs/design-partner-verifier-pilot.md",),
            frozenset({"executable_pin", "contract_floor", "placeholder_ownership"})),
)

SURFACES_BY_ID = {surface.id: surface for surface in SURFACES}


@dataclass(frozen=True)
class ParityGap:
    """A surface allowed to disagree with the engine, with an owner."""

    id: str
    surface: str
    issue: int


KNOWN_GAPS: tuple[ParityGap, ...] = (
    ParityGap("detector-mcp-server-source", "zero_install_detector", 485),
    ParityGap("emitted-workflow-unpublished-pin", "emitted_ci_workflow", 506),
    ParityGap("rendered-prompt-unpublished-pin", "prompts", 506),
)

GAPS_BY_ID = {gap.id: gap for gap in KNOWN_GAPS}


def _gap_marks(gap_id: str) -> list[pytest.MarkDecorator]:
    """``xfail(strict=True)`` naming the gap and the issue that owns it."""

    gap = GAPS_BY_ID[gap_id]
    return [
        pytest.mark.xfail(
            strict=True,
            reason=(
                f"known parity gap {gap.id!r} on surface {gap.surface!r}, owned by "
                f"#{gap.issue}. When that lands this row passes, the strict marker "
                "fails, and the gap is retired from KNOWN_GAPS and from "
                "docs/distribution-surfaces.md."
            ),
        )
    ]


#: Every tracked top-level repository entry that is *not* part of a distribution
#: surface, with the reason. An entry that is neither here nor the first
#: component of some ``Surface.roots`` entry fails
#: :func:`test_every_top_level_entry_is_classified` — which is how a new
#: directory becomes visible instead of silently unwatched.
NOT_A_DISTRIBUTION_SURFACE: dict[str, str] = {
    ".agents": "this repository's own adoption of the kits; the shipped copies are skills/, plugins/ and adoption-kits/",
    ".claude": "this repository's own Claude Code configuration (dogfood)",
    ".claude-plugin": "this repository's own plugin marketplace entry (dogfood)",
    ".cursor": "this repository's own Cursor rules (dogfood)",
    ".cursorrules": "this repository's own Cursor rules (dogfood)",
    ".github": "this repository's CI and issue templates; not shipped to an adopter",
    ".gitattributes": "repository mechanics",
    ".gitignore": "repository mechanics",
    ".pre-commit-hooks.yaml": "repository mechanics",
    ".well-known": "the channel metadata this registry reads; the source of truth for executable_pin, not a restatement of it",
    "AGENTS.md": "repository documentation, pinned by tests/test_public_surface_contract.py",
    "CHANGELOG.md": "repository documentation, pinned by tests/test_public_surface_contract.py",
    "CLAUDE.md": "repository documentation, pinned by tests/test_public_surface_contract.py",
    "CODE_OF_CONDUCT.md": "repository documentation",
    "CONTRIBUTING.md": "repository documentation",
    "LICENSE": "repository documentation",
    "README.md": "repository documentation, pinned by tests/test_public_surface_contract.py",
    "ROADMAP.md": "repository documentation, pinned by tests/test_public_surface_contract.py",
    "SECURITY.md": "repository documentation",
    "STABILITY.md": "repository documentation",
    "action.yml": "the github_action surface's own root",
    "assets": "images",
    "benchmark": "the accuracy corpus; an input to measurement, not a published answer",
    "ci_sharding.py": "test-run mechanics",
    "conftest.py": "test-run mechanics",
    "constraints": "hash-locked dependency pins",
    "llms-full.txt": "generated agent-discovery text, pinned by tests/test_public_surface_contract.py",
    "llms.txt": "generated agent-discovery text, pinned by tests/test_public_surface_contract.py",
    "pyproject.toml": "packaging",
    "samples": "fixture workspaces the engine is run against, not surfaces that answer",
    "shipgate": "the repository launcher",
    "shipgate-self.yaml": "this repository's own manifest (dogfood)",
    "shipgate.yaml": "this repository's own manifest (dogfood)",
    "tests": "this suite",
}


# --------------------------------------------------------------------------
# Release channels, read from committed metadata. No network.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishedBuild:
    """What a published build actually implements.

    Committed rather than derived at run time so the suite is deterministic in a
    shallow clone. :func:`test_published_build_table_matches_the_tag` reads the
    real tag back and corroborates it wherever the tag object is present, and
    ``release-tag-consistency`` in ``.github/workflows/ci.yml`` is what proves
    the tag exists at all.
    """

    version: str
    contract_version: str


PUBLISHED_BUILDS: dict[str, PublishedBuild] = {
    # v0.15.0 shipped `CONTRACT_VERSION = "10"` and no
    # `MINIMUM_CONTROL_CONTRACT_VERSION` at all: the agent-control envelope
    # landed after the tag. Surfaces that name this build must not demand a
    # contract it cannot reach.
    "0.15.0": PublishedBuild("0.15.0", "10"),
}


def published_version() -> str:
    """The newest release a reader can actually install, from committed metadata."""

    payload = json.loads(WELL_KNOWN.read_text(encoding="utf-8"))
    latest = payload["release_status"]["latest_release"]
    assert latest.startswith("v"), f"latest_release {latest!r} should be a v-prefixed tag"
    return latest[1:]


def source_build_contract() -> str:
    """The contract of the tree emitting these surfaces."""

    return CONTRACT_VERSION


def contract_of(version: str) -> str:
    """The runtime contract the build ``version`` implements.

    The source build is whatever this tree is; anything else has to be in
    :data:`PUBLISHED_BUILDS`, because a claim about a build nobody recorded is
    not a claim anybody can check.
    """

    if version == __version__:
        return source_build_contract()
    build = PUBLISHED_BUILDS.get(version)
    assert build is not None, (
        f"No committed record of what build {version!r} implements. Add it to "
        "PUBLISHED_BUILDS (and to the channel table in "
        "docs/distribution-surfaces.md) before a surface names it."
    )
    return build.contract_version


# --------------------------------------------------------------------------
# Registry integrity: the doc and the code are one another's guard.
# --------------------------------------------------------------------------

_REGISTRY_ROW = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|", re.MULTILINE)
_GAP_ROW = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*`?([a-z0-9_, `]+?)`?\s*\|", re.MULTILINE)


def _registry_text() -> str:
    return REGISTRY_DOC.read_text(encoding="utf-8")


def _registry_section(heading: str) -> str:
    text = _registry_text()
    start = text.index(f"\n## {heading}\n")
    rest = text[start + 1 :]
    end = rest.find("\n## ", 1)
    return rest if end == -1 else rest[:end]


def test_registry_document_exists_and_is_referenced_from_contributing():
    """A registry nobody is pointed at is a registry nobody reads."""

    assert REGISTRY_DOC.is_file(), f"{REGISTRY_DOC} is missing"
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "docs/distribution-surfaces.md" in contributing, (
        "CONTRIBUTING.md must point at docs/distribution-surfaces.md — surface "
        "discipline governs whether a new surface may exist, the registry "
        "governs what an existing one is allowed to say."
    )


def test_registry_document_lists_exactly_the_registered_surfaces():
    """The doc and :data:`SURFACES` are one list, kept in two places."""

    documented = set(_REGISTRY_ROW.findall(_registry_section("The registry")))
    registered = set(SURFACES_BY_ID)
    assert documented == registered, (
        "docs/distribution-surfaces.md and SURFACES disagree about which "
        f"surfaces exist: only in the doc {sorted(documented - registered)}, "
        f"only in code {sorted(registered - documented)}."
    )


def test_registry_rows_use_the_closed_claims_vocabulary():
    for surface in SURFACES:
        unknown = surface.claims - CLAIMS
        assert not unknown, (
            f"{surface.id} claims {sorted(unknown)}, which is outside the closed "
            "vocabulary. Add it to CLAIMS and to the registry's claims table, or "
            "spell the claim with an existing name."
        )


def test_registry_documents_every_claim_a_surface_makes():
    """Each surface's row in the doc names the claims the code registers."""

    section = _registry_section("The registry")
    rows = {
        match.group(1): section[match.start() : section.find("\n", match.start())]
        for match in _REGISTRY_ROW.finditer(section)
    }
    for surface in SURFACES:
        row = rows[surface.id]
        for claim in sorted(surface.claims):
            assert f"`{claim}`" in row, (
                f"docs/distribution-surfaces.md's {surface.id} row does not name "
                f"claim `{claim}`. The row is what a reader checks the surface "
                "against, so a claim missing from it is a claim nobody audits."
            )
        if not surface.claims:
            assert "—" in row or "no claim" in row.lower(), (
                f"{surface.id} registers no claim; its row must say so, and say why."
            )


def test_registered_surface_roots_exist():
    for surface in SURFACES:
        for path in surface.paths():
            assert path.exists(), f"{surface.id} names a missing root: {path}"


def _tracked_top_level_entries() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line.split("/", 1)[0] for line in out.splitlines() if line}


def _unclassified(entries: set[str], surfaces: tuple[Surface, ...]) -> set[str]:
    """Top-level entries that are neither a surface root nor declared out of scope."""

    covered = {root.split("/", 1)[0] for surface in surfaces for root in surface.roots}
    return entries - covered - set(NOT_A_DISTRIBUTION_SURFACE)


def test_every_top_level_entry_is_classified():
    """A new top-level directory is unwatched until somebody says what it is.

    That is the state most of the ten surfaces were in when #497 was filed. This
    is the guard that makes the next one visible on the pull request that adds
    it, rather than on the day an adopter hits it.
    """

    unclassified = _unclassified(_tracked_top_level_entries(), SURFACES)
    assert not unclassified, (
        f"unclassified top-level entries: {sorted(unclassified)}. Register each "
        "as a distribution surface in SURFACES (and in "
        "docs/distribution-surfaces.md), or record why it is not one in "
        "NOT_A_DISTRIBUTION_SURFACE."
    )


def test_classifier_rejects_an_unregistered_top_level_entry():
    """Negative control for the guard above."""

    entries = _tracked_top_level_entries() | {"brand-new-surface"}
    assert _unclassified(entries, SURFACES) == {"brand-new-surface"}


def test_stale_classifications_are_removed():
    """A reason for a path that no longer exists is a reason nobody can check."""

    entries = _tracked_top_level_entries()
    stale = set(NOT_A_DISTRIBUTION_SURFACE) - entries
    assert not stale, (
        f"NOT_A_DISTRIBUTION_SURFACE names entries that are gone: {sorted(stale)}."
    )


def test_known_gaps_are_documented_with_an_owner():
    section = _registry_section("Known parity gaps")
    for gap in KNOWN_GAPS:
        assert gap.surface in SURFACES_BY_ID, (
            f"gap {gap.id!r} names surface {gap.surface!r}, which is not registered"
        )
        assert f"`{gap.id}`" in section, (
            f"gap {gap.id!r} has no row in the registry's Known parity gaps table"
        )
        assert f"/issues/{gap.issue}" in section, (
            f"gap {gap.id!r} names #{gap.issue}, which the registry's gap table "
            "does not link. A gap without a reachable owner is a defect."
        )


def test_registry_documents_no_gap_the_code_has_retired():
    documented = set(_GAP_ROW.findall(_registry_section("Known parity gaps")))
    documented_ids = {gap_id for gap_id, _surface in documented}
    assert documented_ids == set(GAPS_BY_ID), (
        "the registry's gap table and KNOWN_GAPS disagree: only in the doc "
        f"{sorted(documented_ids - set(GAPS_BY_ID))}, only in code "
        f"{sorted(set(GAPS_BY_ID) - documented_ids)}."
    )


# --------------------------------------------------------------------------
# agent_project_verdict — the detector script against the CLI.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ParityWorkspace:
    """One workspace both implementations are asked about."""

    id: str
    path: Path
    #: The gap that explains a disagreement, when one is expected.
    gap: str | None = None


PARITY_WORKSPACES: tuple[ParityWorkspace, ...] = (
    # Shapes both implementations already agree on. Without these the gap rows
    # below would be the whole test, and a comparator that always reported a
    # divergence would look correct.
    ParityWorkspace("sample_mcp_only_server", SAMPLES_ROOT / "mcp_only_server"),
    ParityWorkspace("sample_support_refund_agent", SAMPLES_ROOT / "support_refund_agent"),
    ParityWorkspace("sample_simple_langchain_agent", SAMPLES_ROOT / "simple_langchain_agent"),
    # #485's exact case, minimized: an MCP server whose tool surface exists only
    # as registration sites in TypeScript / Go source.
    ParityWorkspace(
        "ts_registertool_positive",
        CORPUS_ROOT / "ts_registertool_positive",
        gap="detector-mcp-server-source",
    ),
    ParityWorkspace(
        "go_tool_struct_positive",
        CORPUS_ROOT / "go_tool_struct_positive",
        gap="detector-mcp-server-source",
    ),
    # The provenance gate: the registration idiom is spelled exactly, but the
    # repository declares no MCP dependency, so neither implementation may claim
    # a tool surface. Both answer `false`, and they must go on doing so after
    # #485 lands — otherwise the port fails open on a coincidence of spelling.
    ParityWorkspace(
        "ts_no_mcp_dependency_negative",
        CORPUS_ROOT / "ts_no_mcp_dependency_negative",
    ),
)


def _load_detector_script() -> Any:
    spec = importlib.util.spec_from_file_location(
        "shipgate_detect_distribution_parity", DETECTOR_SCRIPT
    )
    if spec is None or spec.loader is None:  # pragma: no cover - packaging accident
        raise RuntimeError(f"Could not load {DETECTOR_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def detector() -> Any:
    return _load_detector_script()


def detector_divergences(
    script_result: dict[str, Any], cli_result: dict[str, Any]
) -> list[str]:
    """Every way the two answers differ, in the reader's terms.

    Structural parity, not byte parity: the script is documented as a subset
    (no ``diagnostics[]``, no ``next_actions[]``, simplified evidence strings).
    What has to agree is what a first-contact evaluator acts on — whether this
    is an agent project, which frameworks fired, and which sources are in or out.
    """

    divergences: list[str] = []
    if script_result["is_agent_project"] != cli_result["is_agent_project"]:
        divergences.append(
            "is_agent_project: script="
            f"{script_result['is_agent_project']!r} cli={cli_result['is_agent_project']!r}"
        )
    for key in ("frameworks",):
        script_types = sorted(entry["type"] for entry in script_result[key])
        cli_types = sorted(entry["type"] for entry in cli_result[key])
        if script_types != cli_types:
            divergences.append(f"{key}: script={script_types!r} cli={cli_types!r}")
    for key in ("suggested_sources", "excluded_sources"):
        script_pairs = sorted((e["type"], e["path"]) for e in script_result[key])
        cli_pairs = sorted((e["type"], e["path"]) for e in cli_result[key])
        if script_pairs != cli_pairs:
            divergences.append(f"{key}: script={script_pairs!r} cli={cli_pairs!r}")
    return divergences


def _detector_row(workspace: ParityWorkspace) -> Any:
    marks = _gap_marks(workspace.gap) if workspace.gap else []
    return pytest.param(workspace, marks=marks, id=workspace.id)


@pytest.mark.parametrize(
    "workspace", [_detector_row(w) for w in PARITY_WORKSPACES]
)
def test_detector_verdict_matches_cli(detector: Any, workspace: ParityWorkspace):
    """`tools/shipgate-detect.py` answers what `detect` answers.

    The script cannot import the package — being importable-free is its entire
    value, since it is what an agent curls onto a repository that has not
    adopted anything. So it keeps its own implementation and takes this test as
    its contract.
    """

    script_result = detector.detect(workspace.path)
    cli_result = detect_workspace(workspace.path.resolve()).model_dump(mode="json")
    divergences = detector_divergences(script_result, cli_result)
    assert not divergences, (
        f"{workspace.id}: the zero-install detector and the CLI disagree:\n  "
        + "\n  ".join(divergences)
    )


def test_detector_comparison_reports_a_seeded_divergence():
    """Negative control: the comparator is not vacuously empty."""

    empty: dict[str, Any] = {
        "is_agent_project": False,
        "frameworks": [],
        "suggested_sources": [],
        "excluded_sources": [],
    }
    changed = {
        "is_agent_project": True,
        "frameworks": [{"type": "mcp_server_source"}],
        "suggested_sources": [{"type": "mcp_server_source", "path": "src"}],
        "excluded_sources": [],
    }
    divergences = detector_divergences(empty, changed)
    assert len(divergences) == 3, divergences
    assert not detector_divergences(changed, changed)


def test_parity_corpus_covers_the_shape_that_produced_the_gap():
    """The corpus has to contain the case, not merely a case.

    A row that both implementations happen to agree on proves nothing about
    #485. These fixtures exist because the CLI reads a tool surface out of them
    that no ``samples/`` fixture carries.
    """

    positives = [w for w in PARITY_WORKSPACES if w.gap == "detector-mcp-server-source"]
    assert len(positives) >= 2, "want a TypeScript and a Go registration-site case"
    for workspace in positives:
        cli_result = detect_workspace(workspace.path.resolve()).model_dump(mode="json")
        assert cli_result["is_agent_project"] is True, workspace.id
        assert [f["type"] for f in cli_result["frameworks"]] == ["mcp_server_source"], (
            f"{workspace.id} must exercise the mcp_server_source route specifically"
        )


def test_parity_corpus_negative_is_rejected_by_the_provenance_gate():
    """The negative case is negative for the *right* reason.

    It spells the TypeScript SDK idiom exactly and declares no MCP dependency,
    so a reader that claimed a tool surface from it would be resting a proof on
    a spelling — the fail-open shape #393 named. The CLI refuses it today, and
    #485's port has to refuse it too.
    """

    workspace = CORPUS_ROOT / "ts_no_mcp_dependency_negative"
    source = (workspace / "src" / "server.ts").read_text(encoding="utf-8")
    assert ".registerTool(" in source, "the negative case must spell the idiom"
    manifest = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
    declared = " ".join(manifest.get("dependencies", {}))
    assert "modelcontextprotocol" not in declared and "mcp" not in declared
    cli_result = detect_workspace(workspace.resolve()).model_dump(mode="json")
    assert cli_result["is_agent_project"] is False


# --------------------------------------------------------------------------
# merge_verdict_vocabulary — surfaces that enumerate the engine's verdicts.
# --------------------------------------------------------------------------

_ACTION_SUPPORTED_VALUES = re.compile(r"Supported values:\s*([a-z_,\s]+)\.")


def _action_input_description(name: str) -> str:
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
    return str(action["inputs"][name]["description"])


def verdict_tokens(text: str) -> set[str]:
    """The merge verdicts an ``action.yml`` ``Supported values:`` clause names."""

    match = _ACTION_SUPPORTED_VALUES.search(text)
    assert match, f"no 'Supported values:' clause in {text!r}"
    return {token.strip() for token in match.group(1).split(",") if token.strip()}


def test_action_input_enumerates_engine_merge_verdicts():
    """`fail_on_merge_verdicts` is what a caller gates on. Its list is the engine's.

    An enumeration is the strongest kind of parity row available on a
    documentation surface: adding a verdict to the engine fails here until the
    published input catches up, rather than shipping an input that silently
    rejects the new value.
    """

    documented = verdict_tokens(_action_input_description("fail_on_merge_verdicts"))
    assert documented == set(MERGE_VERDICTS), (
        "action.yml's fail_on_merge_verdicts input and "
        "agents_shipgate.schemas.contract.MERGE_VERDICTS disagree: only "
        f"documented {sorted(documented - set(MERGE_VERDICTS))}, only in the "
        f"engine {sorted(set(MERGE_VERDICTS) - documented)}."
    )


def test_action_output_script_shares_the_engine_merge_verdicts():
    """The Action's extraction script must accept exactly what the engine emits."""

    from scripts.github_action_outputs import MERGE_VERDICTS as SCRIPT_MERGE_VERDICTS

    assert SCRIPT_MERGE_VERDICTS == set(MERGE_VERDICTS), (
        "scripts/github_action_outputs.py holds a second copy of the merge "
        "verdict vocabulary and it has drifted from "
        "agents_shipgate.schemas.contract.MERGE_VERDICTS."
    )


def test_verdict_token_parser_rejects_a_seeded_extra_value():
    """Negative control for the enumeration comparison."""

    seeded = "Supported values: blocked, human_review_required, needs_a_wizard."
    assert verdict_tokens(seeded) != set(MERGE_VERDICTS)
    assert "needs_a_wizard" in verdict_tokens(seeded)


def test_well_known_publishes_the_engine_vocabularies():
    """The discovery document is read by consumers that never import the package."""

    payload = json.loads(WELL_KNOWN.read_text(encoding="utf-8"))
    assert payload["merge_verdicts"] == list(MERGE_VERDICTS)
    assert payload["release_decisions"] == list(RELEASE_DECISIONS)


def _surface_files(surface: Surface, suffixes: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for root in surface.paths():
        if root.is_file():
            if root.suffix in suffixes:
                files.append(root)
            continue
        files.extend(
            path
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.suffix in suffixes
        )
    return files


def _alternation(pattern: re.Pattern[str]) -> set[str]:
    """The literal alternatives a ``\\b(a|b|c)\\b`` pattern accepts."""

    match = re.search(r"\(([a-z_|]+)\)", pattern.pattern)
    assert match, f"not a simple alternation: {pattern.pattern!r}"
    return set(match.group(1).split("|"))


def test_harness_holds_no_drifted_copy_of_the_engine_vocabularies():
    """The adoption harness classifies runs by verdict, from its own constants.

    It cannot import the report it reads — it grades artifacts produced by
    whatever build the cell ran — so it carries literal vocabularies. Each one
    is derivable from the engine's, and this is where that derivation is
    checked: adding a verdict fails here until the grader knows about it, rather
    than silently grading the new value as "not blocking".
    """

    from agents_shipgate.schemas.verifier import map_merge_verdict
    from harness.adoption.matrix import EXPECTED_BLOCK_VERDICTS
    from harness.adoption.scorer.rules import (
        _BLOCKING_VERDICTS,
        _DECISION_TO_MERGE_VERDICT,
        DECISION_VALUE_RE,
        MERGE_VERDICT_VALUE_RE,
    )

    assert _alternation(MERGE_VERDICT_VALUE_RE) == set(MERGE_VERDICTS)
    assert _alternation(DECISION_VALUE_RE) == set(RELEASE_DECISIONS)
    # Everything that is not `mergeable` stops the agent. Spelled as a
    # derivation so a sixth verdict lands on the stopping side by default,
    # which is the fail-closed direction.
    assert _BLOCKING_VERDICTS == set(MERGE_VERDICTS) - {"mergeable"}
    assert EXPECTED_BLOCK_VERDICTS <= _BLOCKING_VERDICTS
    assert _DECISION_TO_MERGE_VERDICT == {
        decision: map_merge_verdict(decision) for decision in RELEASE_DECISIONS
    }


def test_alternation_reader_sees_a_seeded_extra_value():
    """Negative control: the reader above is not returning the engine's own set."""

    assert _alternation(re.compile(r"\b(mergeable|needs_a_wizard)\b")) == {
        "mergeable",
        "needs_a_wizard",
    }


# --------------------------------------------------------------------------
# placeholder_ownership — who may fill a manifest placeholder.
# --------------------------------------------------------------------------

#: Phrases the engine itself uses when it routes a placeholder to a person. A
#: surface that names a human-owned field must quote one of them, so a reader
#: gets the same answer from the prompt and from the tool. Asserted below to be
#: substrings of the engine's own message, which is what makes this a binding
#: rather than an independent paraphrase that can drift.
ENGINE_HUMAN_OWNERSHIP_PHRASES: tuple[str, ...] = (
    "must be supplied by a human",
    "Shipgate never invents",
    "a declaration nobody made",
)

#: Manifest paths a distribution surface is likely to name. Their owner comes
#: from the engine, never from this list. Dotted paths only: a bare block name
#: like ``permissions`` reads as ordinary prose (``control.permissions.edit``),
#: and a rule that fired on it would flag the sentences that *do* route
#: correctly. The blanket-instruction rule below is what covers the rest.
NAMEABLE_PLACEHOLDER_PATHS: tuple[str, ...] = (
    "agent.name",
    "agent.declared_purpose",
    "agent.prohibited_actions",
    "project.name",
)

#: An instruction to fill placeholders *as a class* — "replace every CHANGE_ME",
#: "replace CHANGE_ME values". It names no field, so it cannot distinguish the
#: two owners, and the manifest has human-owned blocks in it. This is the exact
#: wording that shipped in the design-partner runbook and the Codex kit.
_BLANKET_PLACEHOLDER_INSTRUCTION = re.compile(
    r"\b(?:replace|resolve|fill(?:\s+in)?|set)\b[^.\n]{0,80}?\b(?:every|all|any)\b"
    r"[^.\n]{0,80}?CHANGE_ME"
    r"|\b(?:replace|resolve|fill(?:\s+in)?|set)\b[^.\n]{0,40}?`?CHANGE_ME`?\s+values",
    re.IGNORECASE,
)

#: How far after a mention the human-routing phrase may sit. One long paragraph.
_OWNERSHIP_WINDOW = 1200


def test_engine_ownership_phrases_come_from_the_engine():
    """The phrases above are the engine's words, not this test's."""

    for phrase in ENGINE_HUMAN_OWNERSHIP_PHRASES:
        assert phrase in _PLACEHOLDER_REVIEW_TAIL, (
            f"{phrase!r} is no longer in the message the CLI publishes when it "
            "routes a placeholder to a human. The engine reworded, so every "
            "surface quoting it has to be re-checked — update both together."
        )


def _routed(text: str, start: int) -> bool:
    window = text[start : start + _OWNERSHIP_WINDOW]
    return any(phrase in window for phrase in ENGINE_HUMAN_OWNERSHIP_PHRASES)


def human_owned_mentions_without_routing(text: str) -> list[str]:
    """Every way this text hands a human-owned placeholder to a coding agent.

    Two shapes, because the surfaces used both. Naming the field
    (``agent.declared_purpose``) and naming the class (``replace every
    CHANGE_ME``) are the same instruction; only the first is greppable, and the
    second is the one that shipped in the design-partner runbook.
    """

    unrouted: list[str] = []
    for field in NAMEABLE_PLACEHOLDER_PATHS:
        if placeholder_owner(field) != "human":
            continue
        for match in re.finditer(rf"\b{re.escape(field)}\b", text):
            if not _routed(text, match.start()):
                unrouted.append(f"{field} at offset {match.start()}")
                break
    for match in _BLANKET_PLACEHOLDER_INSTRUCTION.finditer(text):
        if not _routed(text, match.start()):
            unrouted.append(
                f"blanket placeholder instruction at offset {match.start()}: "
                f"{match.group(0)!r}"
            )
    return unrouted


def _placeholder_surfaces() -> list[Path]:
    """Registered surface files that tell a reader to resolve placeholders."""

    files: list[Path] = []
    for surface in SURFACES:
        if "placeholder_ownership" not in surface.claims:
            continue
        for path in _surface_files(surface, (".md",)):
            if "CHANGE_ME" in path.read_text(encoding="utf-8"):
                files.append(path)
    return files


@pytest.mark.parametrize(
    "path", _placeholder_surfaces(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_surface_routes_human_owned_placeholders_to_a_human(path: Path):
    """Purpose, effect, authority and binding are a person's to declare.

    ``init`` already routes them: while one is unresolved it returns
    ``control.next_action.actor: "human"`` and ``permissions.edit: false``. A
    bundled prompt that tells the coding agent to derive
    ``agent.declared_purpose`` from the README contradicts the gate it is
    installing — and the surface is the copy the agent actually reads.
    """

    unrouted = human_owned_mentions_without_routing(path.read_text(encoding="utf-8"))
    assert not unrouted, (
        f"{path.relative_to(REPO_ROOT)} names human-owned manifest fields "
        f"without routing them to a person: {unrouted}. The engine's own "
        f"wording is: {_PLACEHOLDER_REVIEW_TAIL.strip()}"
    )


def test_placeholder_routing_check_catches_a_blanket_instruction():
    """Negative control: the wording that was actually shipped must fail."""

    shipped_before = (
        "Replace every CHANGE_ME value in shipgate.yaml using the agent's "
        "system prompt, README, main agent module, or owner-provided context. "
        "Set agent.declared_purpose from the repository."
    )
    assert human_owned_mentions_without_routing(shipped_before)
    repaired = shipped_before + (
        " agent.declared_purpose must be supplied by a human."
    )
    assert not human_owned_mentions_without_routing(repaired)


def test_agent_owned_placeholders_are_not_routed_to_a_human():
    """The rule is ownership, not caution.

    Sending ``agent.name`` to a person stops a turn for work the agent owns,
    which is the failure in the other direction and just as real.
    """

    assert placeholder_owner("agent.name") == "coding_agent"
    assert not human_owned_mentions_without_routing(
        "Replace agent.name with the agent's actual role, read from the source."
    )


# --------------------------------------------------------------------------
# executable_pin — a ref a reader will actually run.
# --------------------------------------------------------------------------

_VERSION = r"\d+\.\d+\.\d+(?:[A-Za-z]+\d*)?"
PIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("action", re.compile(rf"ThreeMoonsLab/agents-shipgate@v({_VERSION})")),
    ("pip", re.compile(rf"agents-shipgate==({_VERSION})")),
    ("uvx", re.compile(rf"agents-shipgate@({_VERSION})")),
)

#: Exactly the files that pin the emitting build rather than a published one.
#: A per-surface exemption would excuse the *next* file to drift as well, which
#: is how the gap became invisible in the first place; this is a ledger, and
#: :func:`test_unpublished_pin_ledger_is_exact` keeps it equal to reality in
#: both directions.
_UNPUBLISHED_PIN_FILES: frozenset[str] = frozenset(
    {
        "adoption-kits/claude-code-skill/prompts/add-shipgate-to-repo.md",
        "adoption-kits/claude-code-skill/prompts/decide-shipgate-relevance.md",
        "plugins/claude-code/skills/agents-shipgate/prompts/add-shipgate-to-repo.md",
        "plugins/claude-code/skills/agents-shipgate/prompts/decide-shipgate-relevance.md",
        "prompts/add-shipgate-to-repo.md",
        "prompts/decide-shipgate-relevance.md",
        "skills/agents-shipgate/prompts/add-shipgate-to-repo.md",
        "skills/agents-shipgate/prompts/decide-shipgate-relevance.md",
    }
)
_PIN_GAP = "rendered-prompt-unpublished-pin"


def unpublished_pins(text: str, *, published: str) -> list[tuple[str, str]]:
    """Every pin in ``text`` naming a version other than the published release."""

    found: list[tuple[str, str]] = []
    for kind, pattern in PIN_PATTERNS:
        for match in pattern.finditer(text):
            if match.group(1) != published:
                found.append((kind, match.group(1)))
    return found


def _rendered(path: Path) -> str:
    """A kit template as the adopter receives it.

    ``adoption-kits/`` is the renderer's *input*: its pins are
    ``{{ shipgate_version }}``, so reading the literal would check a template
    variable rather than the ref a reader runs. Substituted through the
    package's own renderer, not a copy of it — a harness that re-implemented
    rendering to check for second implementations would be the joke it sounds
    like, and would go on passing after the real renderer changed. Untemplated
    files pass through unchanged.
    """

    return _render_template(path.read_text(encoding="utf-8"))


def test_rendering_here_is_the_packages_own_rendering():
    """Pin the helper above to the renderer, so a context change reaches it."""

    assert _render_template("{{ shipgate_version }}") == __version__
    assert (
        _render_template("{{ minimum_control_contract_version }}")
        == MINIMUM_CONTROL_CONTRACT_VERSION
    )
    assert _render_template("no placeholders here") == "no placeholders here"


def _pin_bearing_paths() -> list[Path]:
    """Every registered surface file that names a version a reader would run."""

    paths: list[Path] = []
    for surface in SURFACES:
        if "executable_pin" not in surface.claims:
            continue
        for path in _surface_files(surface, (".md", ".yml", ".yaml", ".json", ".txt")):
            rendered = _rendered(path)
            if any(pattern.search(rendered) for _kind, pattern in PIN_PATTERNS):
                paths.append(path)
    return paths


def _pin_bearing_files() -> list[Any]:
    return [
        pytest.param(
            path,
            marks=(
                _gap_marks(_PIN_GAP)
                if str(path.relative_to(REPO_ROOT)) in _UNPUBLISHED_PIN_FILES
                else []
            ),
            id=str(path.relative_to(REPO_ROOT)),
        )
        for path in _pin_bearing_paths()
    ]


def test_unpublished_pin_ledger_is_exact():
    """The ledger must equal the divergence, in both directions.

    Exempting a *surface* would excuse the next file on it to drift too, which
    is how this became invisible. Exempting named files means a new one fails
    loudly, and a repaired one turns its ``xfail(strict=True)`` row into a
    failure until the ledger is trimmed.
    """

    published = published_version()
    diverging = {
        str(path.relative_to(REPO_ROOT))
        for path in _pin_bearing_paths()
        if unpublished_pins(_rendered(path), published=published)
    }
    assert diverging == _UNPUBLISHED_PIN_FILES, (
        "_UNPUBLISHED_PIN_FILES and reality disagree: newly drifted "
        f"{sorted(diverging - _UNPUBLISHED_PIN_FILES)}, stale ledger entries "
        f"{sorted(_UNPUBLISHED_PIN_FILES - diverging)}. A new entry needs the "
        f"#{GAPS_BY_ID[_PIN_GAP].issue} gap to cover it; a stale one means the "
        "gap is closing and should be retired."
    )


@pytest.mark.parametrize("path", _pin_bearing_files())
def test_executable_pin_resolves_in_a_published_channel(path: Path):
    """A ref a stranger executes must exist on the day it is written.

    That is strictly stronger than reproducibility-by-pinning and implies it.
    A pin to the emitting build is reproducible and still 404s for the whole
    window in which the tree is ahead of the newest tag — 56 days, at the point
    #506 was filed.
    """

    published = published_version()
    offenders = unpublished_pins(_rendered(path), published=published)
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} pins "
        + ", ".join(f"{kind} {version}" for kind, version in offenders)
        + f"; the newest published release is {published}. Name a published ref, "
        "or mark the route as the preview channel it is — see "
        "docs/distribution-surfaces.md § Release channels."
    )


def test_pin_scanner_catches_a_seeded_wrong_ref():
    """Negative control: a wrong emitted Action ref fails the responsible check."""

    assert unpublished_pins(
        "uses: ThreeMoonsLab/agents-shipgate@v9.9.9", published="0.15.0"
    ) == [("action", "9.9.9")]
    assert not unpublished_pins(
        "uses: ThreeMoonsLab/agents-shipgate@v0.15.0", published="0.15.0"
    )


def test_published_version_metadata_agrees_with_the_source_tree():
    """The two committed numbers must be the two they claim to be."""

    payload = json.loads(WELL_KNOWN.read_text(encoding="utf-8"))
    assert payload["version"] == __version__, (
        ".well-known/agents-shipgate.json must carry the source build's version"
    )
    assert published_version() in PUBLISHED_BUILDS, (
        f"the newest published release {published_version()!r} has no entry in "
        "PUBLISHED_BUILDS, so nothing in this suite knows what it implements."
    )


@_gap_marks("emitted-workflow-unpublished-pin")[0]
def test_emitted_ci_workflow_pins_a_published_ref(tmp_path: Path, monkeypatch):
    """The one artifact written into a stranger's repository and then executed.

    GitHub resolves ``uses:`` before any step runs, so an unresolvable ref makes
    a first-time adopter's very first Shipgate run a red check about *our*
    repository. Everything else on this surface is a copy-paste snippet a person
    reads; this one is generated, committed and executed without being read.
    """

    monkeypatch.delenv("AGENTS_SHIPGATE_WORKFLOW_REF", raising=False)
    result = write_ci_workflow(tmp_path)
    assert result.status == "written"
    emitted = (tmp_path / WORKFLOW_RELATIVE_PATH).read_text(encoding="utf-8")
    offenders = unpublished_pins(emitted, published=published_version())
    assert not offenders, (
        "init --write --ci emitted "
        + ", ".join(f"{kind} {version}" for kind, version in offenders)
        + f"; the newest published release is {published_version()}."
    )


def test_emitted_ci_workflow_check_catches_a_bad_override(tmp_path: Path, monkeypatch):
    """Negative control, through the emitter rather than around it."""

    monkeypatch.setenv("AGENTS_SHIPGATE_WORKFLOW_REF", "v9.9.9")
    assert write_ci_workflow(tmp_path).status == "written"
    emitted = (tmp_path / WORKFLOW_RELATIVE_PATH).read_text(encoding="utf-8")
    assert unpublished_pins(emitted, published=published_version()) == [
        ("action", "9.9.9")
    ]


# --------------------------------------------------------------------------
# contract_floor — a floor the named build can actually reach.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractFloorClaim:
    """A surface demanding contract N of a reader it also told what to install."""

    path: str
    floor: re.Pattern[str]
    build: re.Pattern[str]
    #: True when the file is a kit template and must be rendered before reading.
    templated: bool = False


CONTRACT_FLOOR_CLAIMS: tuple[ContractFloorClaim, ...] = (
    ContractFloorClaim(
        "adoption-kits/claude-code-skill/prompts/add-shipgate-to-repo.md",
        re.compile(r"requires \*\*runtime contract (\d+)\*\*"),
        re.compile(rf"`agents-shipgate` ({_VERSION}) or newer"),
        templated=True,
    ),
    ContractFloorClaim(
        "prompts/add-shipgate-to-repo.md",
        re.compile(r"requires \*\*runtime contract (\d+)\*\*"),
        re.compile(rf"`agents-shipgate` ({_VERSION}) or newer"),
    ),
)


@pytest.mark.parametrize(
    "claim", CONTRACT_FLOOR_CLAIMS, ids=lambda claim: claim.path
)
def test_contract_floor_is_reachable_in_the_build_the_surface_names(
    claim: ContractFloorClaim,
):
    """A floor and an install command in one file have to be satisfiable together.

    The kits once shipped a floor of 14/15 beside a pinned runner that reports
    contract 10, so an agent following them literally could never satisfy the
    check it was told to gate on. Both values are rendered from the same build
    for exactly this reason; this asserts the property rather than the mechanism.
    """

    path = REPO_ROOT / claim.path
    text = _rendered(path) if claim.templated else path.read_text(encoding="utf-8")
    floor_match = claim.floor.search(text)
    build_match = claim.build.search(text)
    assert floor_match and build_match, (
        f"{claim.path} no longer states both a contract floor and the build it "
        "expects. Either the surface was rewritten — update "
        "CONTRACT_FLOOR_CLAIMS — or one of the two was dropped, which is the "
        "defect this row exists to catch."
    )
    unreachable = floor_out_of_reach(floor_match.group(1), build_match.group(1))
    assert unreachable is None, f"{claim.path}: {unreachable}"


def floor_out_of_reach(floor: str, build: str) -> str | None:
    """Why ``build`` cannot satisfy a demand for runtime contract ``floor``."""

    reached = int(contract_of(build))
    if reached >= int(floor):
        return None
    return (
        f"demands runtime contract {floor} and names build {build}, which "
        f"implements contract {reached}"
    )


def test_contract_floor_check_catches_an_unsatisfiable_floor():
    """Negative control, on the pairing the surfaces actually shipped.

    ``pipx install agents-shipgate`` yields the published release, and the
    current floor is above it — which is what the design-partner runbook and the
    plugin metadata each asserted was fine.
    """

    published = published_version()
    assert floor_out_of_reach(MINIMUM_CONTROL_CONTRACT_VERSION, published) is not None
    assert floor_out_of_reach("14", published) is not None
    assert floor_out_of_reach(contract_of(published), published) is None
    assert floor_out_of_reach(MINIMUM_CONTROL_CONTRACT_VERSION, __version__) is None


def test_runbook_channel_table_states_the_released_contract_correctly():
    """The design-partner runbook names one channel per partner, with its contract.

    It used to teach a v0.15.0 route that needed "runtime contract 14" — a
    number the named build does not reach and never did — while its read order
    named `control.state`, which that build does not emit at all.
    """

    text = (REPO_ROOT / "docs" / "design-partner-verifier-pilot.md").read_text(
        encoding="utf-8"
    )
    published = published_version()
    row = re.search(
        rf"\|\s*Published release `v{re.escape(published)}`\s*\|[^|]*\|\s*(\d+)\s*\|",
        text,
    )
    assert row, (
        "docs/design-partner-verifier-pilot.md must carry a channel row for the "
        f"published release v{published} naming the contract it implements."
    )
    assert row.group(1) == contract_of(published), (
        f"the runbook says the published release implements contract "
        f"{row.group(1)}; PUBLISHED_BUILDS records {contract_of(published)}."
    )


def test_published_build_table_matches_the_tag():
    """Corroborate the committed table against the tag it describes.

    Committed rather than derived so a shallow clone stays deterministic; read
    back here whenever the tag object is present, which it is in any full clone
    and in the CI job that fetches tags.
    """

    for version, build in PUBLISHED_BUILDS.items():
        tag = f"v{version}"
        probe = subprocess.run(
            ["git", "cat-file", "-e", f"{tag}^{{commit}}"],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        if probe.returncode != 0:
            # A check that skips in CI is not a check, so CI is required to
            # provide the tag — `.github/workflows/ci.yml`'s `suite` job
            # checks out with `fetch-depth: 0` and `fetch-tags: true` for this.
            assert not os.environ.get("CI"), (
                f"{tag} is not in this clone. The suite's checkout must fetch "
                "full history and tags, or this corroboration silently stops "
                "running."
            )
            pytest.skip(f"{tag} is not present in this clone")
        blob = subprocess.run(
            ["git", "show", f"{tag}:src/agents_shipgate/schemas/contract.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        match = re.search(r'CONTRACT_VERSION:\s*Literal\["(\d+)"\]', blob)
        assert match, f"could not read CONTRACT_VERSION out of {tag}"
        assert match.group(1) == build.contract_version, (
            f"PUBLISHED_BUILDS says {tag} implements contract "
            f"{build.contract_version}; the tag says {match.group(1)}."
        )


def test_resolvability_is_judged_offline():
    """This harness reads committed metadata, never the network (#497).

    ``release-tag-consistency`` already wrote down why the live check belongs to
    a push-triggered job rather than to the suite: the PyPI index is CDN-cached
    and lags publish by minutes, so a query here would false-fail right after a
    legitimate release. Pinning the property stops a later "just check PyPI"
    from turning a deterministic check into a flaky one.
    """

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    networked = imported & {"urllib", "http", "socket", "requests", "httpx", "ftplib"}
    assert not networked, (
        f"the parity harness imports {sorted(networked)}. Resolvability is "
        "judged against committed release metadata; the live tag check belongs "
        "to .github/workflows/ci.yml's release-tag-consistency job."
    )
