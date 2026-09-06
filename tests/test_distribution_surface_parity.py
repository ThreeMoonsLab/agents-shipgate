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

That is not a hypothetical. Three gaps were registered when this module was
written — the detector's missing ``mcp_server_source`` route (#485) and two
unpublished-pin gaps (#506) — and both fixes landed while this branch was open.
Every row flipped to ``XPASS``, every strict marker failed, and the exemptions
had to be removed to get back to green. :data:`KNOWN_GAPS` is empty because it
worked.

**Deriving beats duplicating, including here.** #506 shipped
``agents_shipgate.published_release`` and ``tests/test_adopter_pins_resolve.py``
while this branch held its own copy of the same table and pin shapes. The copy
is gone: the release constants are imported, the pin shapes are imported, and
what remains is the half that file does not reach — pins **committed** under a
registered surface, found by path rather than from a list, so an example or a
runbook added later is covered without anyone remembering to enumerate it.

**Offline by construction.** Resolvability is judged against committed release
metadata (``.well-known/agents-shipgate.json``), never the network. The live
check that the claimed tag exists on origin is the ``release-tag-consistency``
job in ``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from agents_shipgate import __version__
from agents_shipgate.cli.discovery import detect_workspace
from agents_shipgate.cli.discovery.agent_instructions.adoption_kit import _render_template
from agents_shipgate.cli.discovery.placeholders import placeholder_owner
from agents_shipgate.cli.setup_control import _PLACEHOLDER_REVIEW_TAIL
from agents_shipgate.published_release import (
    LATEST_PUBLISHED_CONTRACT_VERSION,
    LATEST_PUBLISHED_VERSION,
)
from agents_shipgate.schemas.contract import (
    MERGE_VERDICTS,
    RELEASE_DECISIONS,
)

# One definition of the shapes an adopter's machine has to resolve. #506 owns
# that rule and `tests/test_adopter_pins_resolve.py` owns the shapes; importing
# them is the whole point of this module, which exists to stop a second
# implementation of an answer the repository already gives. The same file owns
# the reader-blank allowlist, so `@v<NEW>` cannot be mistaken for a bad ref.
from tests.test_adopter_pins_resolve import PIN_SHAPES, READER_BLANK_REFS, _expected

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
    #: Claim → the tests that prove it *for this surface*. A claim with no test
    #: is an advertisement, so the mapping is claim-keyed rather than a flat
    #: set: it is impossible to register the claim and forget the proof.
    claims: dict[str, tuple[str, ...]]

    def paths(self) -> list[Path]:
        return [REPO_ROOT / root for root in self.roots]


#: A claim may be proved by a test in another module. `#506` owns the pins and
#: contract floors of everything `init` emits, and naming its tests here is the
#: point: the registry records which test proves a claim, not which test *this
#: file* happens to contain. `test_every_claim_names_a_test_that_exists` resolves
#: names in either module.
_ADOPTER_PINS = "tests/test_adopter_pins_resolve.py"

_PIN = (
    "test_executable_pin_resolves_in_a_published_channel",
    f"{_ADOPTER_PINS}::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release",
)
_OWNERSHIP = ("test_surface_routes_human_owned_placeholders_to_a_human",)
_FLOOR = (
    f"{_ADOPTER_PINS}::test_the_shipped_floor_is_decided_against_the_release_the_prompts_pin",
)
_VOCABULARY = ("test_surface_enumerations_match_the_engine_vocabulary",)

SURFACES: tuple[Surface, ...] = (
    Surface(
        "github_action",
        ("action.yml", "scripts/github_action_outputs.py"),
        {
            "merge_verdict_vocabulary": (
                "test_action_input_enumerates_engine_merge_verdicts",
                "test_action_output_script_shares_the_engine_merge_verdicts",
            )
        },
    ),
    Surface(
        "zero_install_detector",
        ("tools/shipgate-detect.py",),
        {"agent_project_verdict": ("test_detector_verdict_matches_cli",)},
    ),
    Surface(
        "emitted_ci_workflow",
        ("src/agents_shipgate/cli/discovery/ci_workflow.py",),
        {
            "executable_pin": (
                f"{_ADOPTER_PINS}::test_the_emitted_workflow_pins_the_release_and_not_the_source_tree",
            )
        },
    ),
    Surface(
        "prompts",
        ("prompts",),
        {
            "executable_pin": _PIN,
            "contract_floor": _FLOOR,
            "release_decision_vocabulary": _VOCABULARY,
            "placeholder_ownership": _OWNERSHIP,
        },
    ),
    Surface(
        "skills",
        ("skills",),
        {
            "executable_pin": _PIN,
            "contract_floor": _FLOOR,
            "release_decision_vocabulary": _VOCABULARY,
            "placeholder_ownership": _OWNERSHIP,
        },
    ),
    Surface(
        "plugins",
        ("plugins",),
        {
            "executable_pin": _PIN,
            "contract_floor": _FLOOR,
            "release_decision_vocabulary": _VOCABULARY,
            "placeholder_ownership": _OWNERSHIP,
        },
    ),
    Surface(
        "adoption_kits",
        ("adoption-kits",),
        {
            "executable_pin": _PIN,
            "contract_floor": _FLOOR,
            "release_decision_vocabulary": _VOCABULARY,
            "placeholder_ownership": _OWNERSHIP,
        },
    ),
    Surface(
        "examples",
        ("examples",),
        {"executable_pin": _PIN, "merge_verdict_vocabulary": _VOCABULARY},
    ),
    Surface("policies", ("policies",), {}),
    Surface(
        "harness",
        ("harness",),
        {
            "merge_verdict_vocabulary": (
                "test_harness_holds_no_drifted_copy_of_the_engine_vocabularies",
            ),
            "release_decision_vocabulary": (
                "test_harness_holds_no_drifted_copy_of_the_engine_vocabularies",
            ),
        },
    ),
    Surface("mcp_server", ("src/agents_shipgate/mcp_server",), {}),
    Surface(
        "design_partner_runbook",
        ("docs/design-partner-verifier-pilot.md",),
        {
            "executable_pin": _PIN,
            "contract_floor": (
                "test_runbook_channel_table_states_the_released_contract_correctly",
            ),
            "placeholder_ownership": _OWNERSHIP,
        },
    ),
)

SURFACES_BY_ID = {surface.id: surface for surface in SURFACES}


@dataclass(frozen=True)
class ParityGap:
    """A surface allowed to disagree with the engine, with an owner."""

    id: str
    #: Every surface the gap covers. The rendered-prompt pin spans four, and a
    #: single id here would have named one of them in the xfail reason a reader
    #: sees for files on the other three.
    surfaces: tuple[str, ...]
    issue: int


#: Empty, and it earned that. Three gaps were registered here when this branch
#: opened — the detector's missing ``mcp_server_source`` route (#485) and two
#: unpublished-pin gaps (#506) — each an ``xfail(strict=True)`` row that would
#: fail the moment its fix landed. Both fixes landed while the branch was open,
#: every row flipped to XPASS, the strict markers failed, and the exemptions had
#: to be removed to get back to green. That is the mechanism working; the record
#: is in `docs/distribution-surfaces.md` § Known parity gaps.
KNOWN_GAPS: tuple[ParityGap, ...] = ()

GAPS_BY_ID = {gap.id: gap for gap in KNOWN_GAPS}


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
# Release channels. Read from the package, not re-derived here.
# --------------------------------------------------------------------------
#
# `agents_shipgate.published_release` is the source of truth for what a
# stranger can actually fetch — `LATEST_PUBLISHED_VERSION` and the
# `CONTRACT_VERSION` that release emits — and `tests/test_adopter_pins_resolve.py`
# binds both to this repository's tag history and to `.well-known`. This module
# held its own copy of that table for one review round; #506 landed the real one
# while this branch was open, and carrying the second would have been the exact
# defect the registry exists to catch.


# --------------------------------------------------------------------------
# Registry integrity: the doc and the code are one another's guard.
# --------------------------------------------------------------------------

_NOT_STATED = "—"


def _registry_text() -> str:
    return REGISTRY_DOC.read_text(encoding="utf-8")


def _registry_section(heading: str, *, stop_at_subsection: bool = False) -> str:
    """The body of one ``##`` section.

    ``stop_at_subsection`` stops at the first ``###`` too. The gaps section needs
    it: its live table is what the code is checked against, while the ``###
    Closed`` table below is history — a record of three exemptions that expired
    on their own — and reading history as a live gap would resurrect all three.
    """

    text = _registry_text()
    marker = f"\n## {heading}\n"
    start = text.find(marker)
    assert start != -1, (
        f"docs/distribution-surfaces.md has no '## {heading}' section. Renaming "
        "a heading breaks the code that reads it — rename it in both places."
    )
    rest = text[start + 1 :]
    ends = [offset for offset in (rest.find("\n## ", 1),) if offset != -1]
    if stop_at_subsection:
        ends += [offset for offset in (rest.find("\n### ", 1),) if offset != -1]
    return rest if not ends else rest[: min(ends)]


def _table_rows(section: str) -> list[list[str]]:
    """Body cells of the one Markdown table in ``section``, header excluded."""

    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= {"-", ":"} and cell for cell in cells):
            continue  # the `| --- |` separator
        rows.append(cells)
    assert rows, f"no table found in section:\n{section[:200]}"
    return rows[1:]  # drop the header


def _backticked(cell: str) -> set[str]:
    """The backticked identifiers in one table cell; empty for ``—``."""

    if cell.strip() == _NOT_STATED:
        return set()
    return set(re.findall(r"`([^`]+)`", cell))


def _documented_surfaces() -> dict[str, dict[str, set[str]]]:
    """The registry table, as ``{id: {"roots": …, "claims": …, "tests": …}}``."""

    documented: dict[str, dict[str, set[str]]] = {}
    for cells in _table_rows(_registry_section("The registry")):
        assert len(cells) == 5, f"unexpected registry row shape: {cells!r}"
        surface_id, root, claims, proven_by, _narrower = cells
        ids = _backticked(surface_id)
        assert len(ids) == 1, f"registry row must open with one `id`: {cells!r}"
        documented[ids.pop()] = {
            # Roots are written with a trailing slash for directories, which is
            # legible in a table and not how a path is spelled in code.
            "roots": {value.rstrip("/") for value in _backticked(root)},
            "claims": _backticked(claims),
            "tests": _backticked(proven_by),
        }
    return documented


def _documented_gaps() -> dict[str, set[str]]:
    """The *live* gap table, as ``{gap id: surface ids}``. Empty is a valid state."""

    section = _registry_section("Known parity gaps", stop_at_subsection=True)
    if not any(line.strip().startswith("|") for line in section.splitlines()):
        return {}
    documented: dict[str, set[str]] = {}
    for cells in _table_rows(section):
        assert len(cells) == 4, f"unexpected gap row shape: {cells!r}"
        ids = _backticked(cells[0])
        assert len(ids) == 1, f"gap row must open with one `id`: {cells!r}"
        documented[ids.pop()] = _backticked(cells[1])
    return documented


def _test_names_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


def _defined_test_names() -> set[str]:
    """Every proving-test name a registry row may use.

    Bare names are this module's own. A ``<module path>::<name>`` entry names a
    test in another file — which the registry has to allow, because a claim is
    proved by whichever test actually proves it, and #506 owns the pins and
    floors of everything ``init`` emits. Both forms are resolved against real
    source, so neither can name a function that is not there.
    """

    names = set(_test_names_in(Path(__file__)))
    for relpath in {_ADOPTER_PINS}:
        module = REPO_ROOT / relpath
        assert module.is_file(), f"registry names tests in {relpath}, which is gone"
        names |= {f"{relpath}::{name}" for name in _test_names_in(module)}
    return names


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

    documented = set(_documented_surfaces())
    registered = set(SURFACES_BY_ID)
    assert documented == registered, (
        "docs/distribution-surfaces.md and SURFACES disagree about which "
        f"surfaces exist: only in the doc {sorted(documented - registered)}, "
        f"only in code {sorted(registered - documented)}."
    )


def test_registry_rows_use_the_closed_claims_vocabulary():
    for surface in SURFACES:
        unknown = set(surface.claims) - CLAIMS
        assert not unknown, (
            f"{surface.id} claims {sorted(unknown)}, which is outside the closed "
            "vocabulary. Add it to CLAIMS and to the registry's claims table, or "
            "spell the claim with an existing name."
        )


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.id)
def test_registry_row_agrees_with_the_code_in_both_directions(surface: Surface):
    """Roots, claims and proving tests are compared as *sets*, not one way.

    A one-directional check lets the document advertise a claim the code does
    not register, so a reader is told a surface is audited when nothing audits
    it and the suite stays green. The registry is only worth reading if it
    cannot say more than the code does.
    """

    row = _documented_surfaces()[surface.id]
    assert row["roots"] == set(surface.roots), (
        f"{surface.id}: the registry's Root column and Surface.roots disagree — "
        f"only in the doc {sorted(row['roots'] - set(surface.roots))}, only in "
        f"code {sorted(set(surface.roots) - row['roots'])}."
    )
    assert row["claims"] == set(surface.claims), (
        f"{surface.id}: the registry's Claims column and Surface.claims disagree "
        f"— only in the doc {sorted(row['claims'] - set(surface.claims))}, only "
        f"in code {sorted(set(surface.claims) - row['claims'])}."
    )
    proving = {name for names in surface.claims.values() for name in names}
    assert row["tests"] == proving, (
        f"{surface.id}: the registry's 'Proven by' column and the tests "
        f"registered per claim disagree — only in the doc "
        f"{sorted(row['tests'] - proving)}, only in code "
        f"{sorted(proving - row['tests'])}."
    )


def test_every_claim_names_a_test_that_exists():
    """A row's proving test is the registry's whole product.

    It shipped stale on the first draft of this change: the `harness` row named
    ``test_surface_states_only_engine_merge_verdicts`` after that test had been
    replaced, so the document's central promise was already false and nothing
    noticed. Names are checked against this module's own definitions, so a
    rename fails here instead of rotting in a table.
    """

    defined = _defined_test_names()
    for surface in SURFACES:
        for claim, names in surface.claims.items():
            assert names, (
                f"{surface.id} registers claim {claim!r} with no proving test. A "
                "claim nothing proves is an advertisement: add the test, or drop "
                "the claim and say so in the row's last column."
            )
            missing = set(names) - defined
            assert not missing, (
                f"{surface.id}'s {claim!r} claim names {sorted(missing)}, which "
                f"is not defined in {Path(__file__).name}."
            )


def test_registered_surface_roots_exist():
    for surface in SURFACES:
        for path in surface.paths():
            assert path.exists(), f"{surface.id} names a missing root: {path}"


def _tracked_top_level_entries() -> set[str]:
    return {rel.split("/", 1)[0] for rel in _tracked_files()}


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
    section = _registry_section("Known parity gaps", stop_at_subsection=True)
    documented = _documented_gaps()
    for gap in KNOWN_GAPS:
        unregistered = set(gap.surfaces) - set(SURFACES_BY_ID)
        assert not unregistered, (
            f"gap {gap.id!r} names unregistered surfaces {sorted(unregistered)}"
        )
        assert gap.id in documented, (
            f"gap {gap.id!r} has no row in the registry's Known parity gaps table"
        )
        assert documented[gap.id] == set(gap.surfaces), (
            f"gap {gap.id!r} covers {sorted(gap.surfaces)} in code but "
            f"{sorted(documented[gap.id])} in the registry. The xfail reason a "
            "reader sees names these surfaces, so a disagreement points them at "
            "the wrong place."
        )
        assert f"/issues/{gap.issue}" in section, (
            f"gap {gap.id!r} names #{gap.issue}, which the registry's gap table "
            "does not link. A gap without a reachable owner is a defect."
        )


def test_registry_documents_no_gap_the_code_has_retired():
    documented_ids = set(_documented_gaps())
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
        "sample_mcp_source_only_server", SAMPLES_ROOT / "mcp_source_only_server"
    ),
    ParityWorkspace(
        "go_tool_struct_positive", CORPUS_ROOT / "go_tool_struct_positive"
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


@pytest.mark.parametrize(
    "workspace", PARITY_WORKSPACES, ids=lambda w: w.id
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


def test_parity_corpus_covers_the_registration_site_route():
    """The corpus has to contain the case, not merely a case.

    A row both implementations happen to agree on proves nothing about #485.
    Two shapes have to be present: the TypeScript one, now a committed sample,
    and the Go one, which `samples/` still has none of. When these rows were
    added the detector answered `false` for both and they were registered gaps;
    #485 landed, they turned green, and the gaps were retired.
    """

    positives = [
        w
        for w in PARITY_WORKSPACES
        if w.id in {"sample_mcp_source_only_server", "go_tool_struct_positive"}
    ]
    assert len(positives) == 2, "want a TypeScript and a Go registration-site case"
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


@lru_cache(maxsize=1)
def _tracked_files() -> tuple[str, ...]:
    """Every tracked path, once. Collection asks for this dozens of times."""

    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return tuple(line for line in out.splitlines() if line)


def _surface_files(surface: Surface, suffixes: tuple[str, ...]) -> list[Path]:
    """Tracked files under a surface's roots, filtered by suffix.

    Tracked, not walked: the classifier reads ``git ls-files`` and the file
    scanners must read the same repository, or an untracked scratch file under
    ``prompts/`` becomes a parametrised row and fails the suite for a reason
    nobody else can reproduce.
    """

    prefixes = tuple(root.rstrip("/") for root in surface.roots)
    return [
        REPO_ROOT / rel
        for rel in sorted(_tracked_files())
        if Path(rel).suffix in suffixes
        and any(rel == prefix or rel.startswith(f"{prefix}/") for prefix in prefixes)
    ]


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
    """Pin the helper above to the renderer, so a context change reaches it.

    ``shipgate_version`` renders the newest *published* release, not this tree's
    version — that is #506's fix, and asserting it here is what would have
    caught this helper still substituting ``__version__`` after the rule moved.
    """

    assert _render_template("{{ shipgate_version }}") == LATEST_PUBLISHED_VERSION
    assert _render_template("{{ shipgate_version }}") != __version__, (
        "the source tree and the newest published release are the same version, "
        "so this assertion no longer distinguishes the two rules. That is the "
        "state right after a release; re-check it at the next version bump."
    )
    assert _render_template("no placeholders here") == "no placeholders here"


#: A braced set literal, the one shape that is unambiguously a surface saying
#: "these are the values" — ``decision ∈ {"blocked", "review_required", …}``.
#: A comma- or slash-joined run is *not* enough: "the `blocked`/`review_required`
#: decisions" and "fails CI on `blocked`, `review_required`, `insufficient_evidence`"
#: are correct partial statements, and a rule that flagged them would force
#: prose to be reworded to satisfy a test rather than a reader.
_SET_LITERAL = re.compile(r"\{[^{}\n]{0,300}\}")

_ALL_VOCABULARY_TOKENS: frozenset[str] = frozenset(MERGE_VERDICTS) | frozenset(
    RELEASE_DECISIONS
)


def documented_vocabulary_sets(text: str) -> list[set[str]]:
    """Every braced literal that reads as a statement of an engine vocabulary.

    **All** members come back, including ones the engine does not define. An
    earlier version projected each literal onto the expected values before
    comparing, which made an invented verdict vanish: adding ``needs_a_wizard``
    to the setup prompt's otherwise-complete release-decision set still compared
    equal, so a surface could advertise a value the engine never emits while the
    registry claimed exact vocabulary parity.

    A literal is a candidate when two or more of its members are engine tokens.
    Membership in *which* vocabulary is then the caller's comparison, so a
    literal mixing the two fails rather than being skipped by both — the
    skip was the other half of the same fail-open.
    """

    sets: list[set[str]] = []
    for match in _SET_LITERAL.finditer(text):
        members = {
            member.strip().strip("`\"'").strip()
            for member in match.group(0)[1:-1].split(",")
        }
        members.discard("")
        if len(members & _ALL_VOCABULARY_TOKENS) < 2:
            continue
        sets.append(members)
    return sets


_MERGE_VERDICT_COMPARISON = re.compile(
    r"""merge_verdict\s*[=!]=\s*['"]([a-z_]+)['"]"""
)


def _surfaces_proven_by(test_name: str) -> list[Surface]:
    return [
        surface
        for surface in SURFACES
        if any(test_name in names for names in surface.claims.values())
    ]


def _vocabulary_surface_files() -> list[Any]:
    return [
        pytest.param(path, id=str(path.relative_to(REPO_ROOT)))
        for surface in _surfaces_proven_by(
            "test_surface_enumerations_match_the_engine_vocabulary"
        )
        for path in _surface_files(surface, (".md", ".yml", ".yaml"))
    ]


@pytest.mark.parametrize("path", _vocabulary_surface_files())
def test_surface_enumerations_match_the_engine_vocabulary(path: Path):
    """A surface that spells the engine's vocabulary as a set must spell all of it.

    Listing four of five teaches a reader to handle four cases, and the dropped
    one is what their workflow then falls through on. A passing mention is left
    alone — that is a reference, not a claim about the set.

    The same test catches a stale literal from the other side: a workflow that
    compares ``merge_verdict`` against a token the engine never emits gates on a
    condition that can never be true.
    """

    text = _rendered(path)
    relpath = path.relative_to(REPO_ROOT)
    accepted = (set(MERGE_VERDICTS), set(RELEASE_DECISIONS))
    for listed in documented_vocabulary_sets(text):
        assert listed in accepted, (
            f"{relpath} spells a verdict set as {sorted(listed)}, which is "
            f"neither MERGE_VERDICTS {sorted(MERGE_VERDICTS)} nor "
            f"RELEASE_DECISIONS {sorted(RELEASE_DECISIONS)}. "
            f"Not in either: {sorted(listed - set(MERGE_VERDICTS) - set(RELEASE_DECISIONS))}. "
            f"Missing from the nearest match: "
            f"{sorted(min(accepted, key=lambda v: len(v ^ listed)) - listed)}."
        )
    for token in set(_MERGE_VERDICT_COMPARISON.findall(text)):
        assert token in set(MERGE_VERDICTS), (
            f"{relpath} compares merge_verdict against {token!r}, which the "
            "engine never emits, so the condition can never be true."
        )


def test_vocabulary_guard_is_not_vacuous():
    """Both halves of the guard must find something to check.

    A regex that matches nothing reads exactly like a regex that found no
    problem. The first draft of this module shipped such a scan — it looked for
    ``merge_verdict == "…"`` in Python across seven surface roots and matched in
    none of them.
    """

    literals = 0
    comparisons = 0
    for surface in _surfaces_proven_by(
        "test_surface_enumerations_match_the_engine_vocabulary"
    ):
        for path in _surface_files(surface, (".md", ".yml", ".yaml")):
            text = _rendered(path)
            literals += len(documented_vocabulary_sets(text))
            comparisons += len(_MERGE_VERDICT_COMPARISON.findall(text))
    assert literals >= 4, (
        f"only {literals} vocabulary set literals found on the surfaces that "
        "claim to state one; the setup prompt spells the release decisions in "
        "four shipped copies."
    )
    assert comparisons >= 1, (
        "no `merge_verdict == '…'` comparison found on any registered surface, "
        "so that half of the guard is checking nothing."
    )


def _vocabulary_set_verdicts(text: str) -> list[bool]:
    """Whether each candidate set in ``text`` is one the guard accepts."""

    accepted = (set(MERGE_VERDICTS), set(RELEASE_DECISIONS))
    return [listed in accepted for listed in documented_vocabulary_sets(text)]


def test_vocabulary_reader_judges_a_set_by_all_of_its_members():
    """Negative controls, including the two fail-open shapes review found.

    An extra unsupported value used to vanish, because each literal was
    projected onto the expected vocabulary before being compared; and a literal
    mixing the two vocabularies was skipped by both, so it was checked by
    neither. Both now fail.
    """

    complete = '`decision` ∈ `{"blocked", "review_required", "insufficient_evidence", "passed"}`'
    assert documented_vocabulary_sets(complete) == [set(RELEASE_DECISIONS)]
    assert _vocabulary_set_verdicts(complete) == [True]

    # A complete, valid set plus one value the engine never emits.
    invented = (
        '{"blocked", "review_required", "insufficient_evidence", "passed", '
        '"needs_a_wizard"}'
    )
    assert documented_vocabulary_sets(invented) == [
        set(RELEASE_DECISIONS) | {"needs_a_wizard"}
    ]
    assert _vocabulary_set_verdicts(invented) == [False]

    # Short, and mixed. Neither may pass, and neither may be skipped.
    assert _vocabulary_set_verdicts('{"blocked", "review_required", "passed"}') == [False]
    assert _vocabulary_set_verdicts('{"mergeable", "passed"}') == [False]

    # A passing mention, and a correct partial statement in prose, are not
    # claims about the set.
    assert documented_vocabulary_sets("treat it as review_required.") == []
    assert (
        documented_vocabulary_sets(
            "fails CI on `blocked`, `review_required`, `insufficient_evidence`"
        )
        == []
    )
    # The complete merge-verdict set is accepted on its own terms, not read as
    # an over-long release-decision set.
    assert _vocabulary_set_verdicts("{" + ", ".join(MERGE_VERDICTS) + "}") == [True]


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
    """Every Markdown file on a surface that claims ``placeholder_ownership``.

    Deliberately *not* filtered to files containing the literal ``CHANGE_ME``.
    That filter was the first draft's, and it made the guard vacuous for the
    shape it most needed to catch: a prompt that says "set
    `agent.declared_purpose` from the README" without ever writing the
    placeholder literal drops out of the parametrisation entirely. A guard
    scoped to one spelling is no guard for any other.
    """

    return [
        path
        for surface in SURFACES
        if "placeholder_ownership" in surface.claims
        for path in _surface_files(surface, (".md",))
    ]


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


def test_placeholder_ownership_guard_is_not_vacuous():
    """The scan must find human-owned fields to have an opinion about.

    Widening it to every Markdown file on the surface removed the ``CHANGE_ME``
    filter but could just as easily have left it with nothing to look at. Seven
    files name ``agent.declared_purpose``; if a rewording drops that below the
    handful the shipped prompts carry, this is the row that says so rather than
    the guard quietly passing on an empty set.
    """

    human = [
        field
        for field in NAMEABLE_PLACEHOLDER_PATHS
        if placeholder_owner(field) == "human"
    ]
    assert human, "no human-owned field in the scanned vocabulary"
    mentions = sum(
        len(re.findall(rf"\b{re.escape(field)}\b", path.read_text(encoding="utf-8")))
        for path in _placeholder_surfaces()
        for field in human
    )
    assert mentions >= 5, (
        f"only {mentions} human-owned field mentions across the surfaces that "
        "claim placeholder_ownership; the setup prompt names "
        "agent.declared_purpose in four shipped copies, and the runbook and both "
        "recipe pages name it too."
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
#
# `tests/test_adopter_pins_resolve.py` owns everything `init` *emits*: it
# renders the workflow and every `--agent-instructions` target and sweeps them.
# This checks the other half — the pins **committed** under a registered
# surface, found by path rather than from a list, so a CI example or a runbook
# added later is covered without anyone remembering to enumerate it. The shapes
# are imported rather than restated; a second copy of them here is the defect
# this module was written to catch.

#: ``pip install -U "agents-shipgate>=X.Y"`` — a *floor*, not a pin, so the rule
#: is reachability rather than equality. Not one of #506's shapes because
#: nothing `init` emits carries one; the design-partner runbook does.
INSTALL_FLOOR_PATTERN = re.compile(r"agents-shipgate>=(\d+(?:\.\d+){1,2})")


def _release(version: str) -> tuple[int, ...]:
    """A dotted version as a comparable 3-tuple; ``0.15`` is ``(0, 15, 0)``."""

    components = re.split(r"[.]", version)[:3]
    assert all(part.isdigit() for part in components), (
        f"{version!r} is not a plain release version. Install floors are "
        "compared against the published release, which is a stable tag."
    )
    return tuple([int(part) for part in components] + [0] * (3 - len(components)))


def unresolvable_pins(text: str) -> list[tuple[str, str]]:
    """Every pin or floor in ``text`` a reader could not resolve today.

    A pin must name the published release; a floor must be at or below it. Both
    fail the same way for the reader — the install errors — so both are
    reported. Reader blanks (``@v<NEW>``) are the prompts teaching an adopter
    which line to edit, and #506 enumerates them rather than pattern-matching
    them, so an allowlist here cannot drift from that one.
    """

    found: list[tuple[str, str]] = []
    for label, pattern, _why in PIN_SHAPES:
        for match in pattern.finditer(text):
            value = match.group(1)
            if value in READER_BLANK_REFS:
                continue
            if value != _expected(label):
                found.append((label, value))
    for match in INSTALL_FLOOR_PATTERN.finditer(text):
        if _release(match.group(1)) > _release(LATEST_PUBLISHED_VERSION):
            found.append(("pip floor", match.group(1)))
    return found


#: Refs a committed example deliberately leaves unpinned, and the ref it uses.
#: ``@main`` resolves — it is a branch — so this is not #506's defect, and it is
#: the alternative #497 allows: "a resolvable supported path **or an explicit
#: version/contract incompatibility**". ``check_run_policy`` postdates the newest
#: release, so the example cannot pin one and says so in its own header.
#:
#: Enumerated, never inferred, for the same reason #506 enumerates reader
#: blanks: an allowlist that guessed at "looks deliberate" is one bad guess away
#: from excusing a silent ``@main`` somewhere nobody meant it.
DECLARED_UNPINNED_REFS: dict[str, str] = {
    "examples/github-actions/10-check-run-annotations.yml": "main",
}


def test_declared_unpinned_refs_explain_themselves():
    """An exception is only an exception if the file says why, in the file.

    Both ends are held: the ref named here is the one actually present, and the
    file states the incompatibility that makes pinning impossible. An entry that
    stopped being true would otherwise go on excusing whatever the file now says.
    """

    for relpath, ref in DECLARED_UNPINNED_REFS.items():
        path = REPO_ROOT / relpath
        assert path.is_file(), f"{relpath} is listed but does not exist"
        text = path.read_text(encoding="utf-8")
        assert f"agents-shipgate@{ref}" in text, (
            f"{relpath} is excused for `@{ref}`, which it does not use."
        )
        assert f"newer than v{LATEST_PUBLISHED_VERSION}" in text, (
            f"{relpath} leaves a ref unpinned without stating which capability "
            f"postdates v{LATEST_PUBLISHED_VERSION}. An unexplained `@{ref}` is "
            "the unpinned-ref defect, not an exception to it."
        )
        assert "After release, pin" in text, (
            f"{relpath} must say what to do once a release carries the feature, "
            "or the exception has no end."
        )


def _pin_bearing_paths() -> list[Path]:
    """Every registered surface file that names a version a reader would run."""

    paths: list[Path] = []
    for surface in SURFACES:
        if "executable_pin" not in surface.claims:
            continue
        for path in _surface_files(surface, (".md", ".yml", ".yaml", ".json", ".txt")):
            rendered = _rendered(path)
            if any(pattern.search(rendered) for _label, pattern, _why in PIN_SHAPES) or (
                INSTALL_FLOOR_PATTERN.search(rendered)
            ):
                paths.append(path)
    return paths


@pytest.mark.parametrize(
    "path", _pin_bearing_paths(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_executable_pin_resolves_in_a_published_channel(path: Path):
    """A ref a stranger executes must exist on the day it is written.

    That is strictly stronger than reproducibility-by-pinning and implies it. A
    pin to the emitting build is reproducible and still 404s for the whole
    window in which the tree is ahead of the newest tag — 56 days, at the point
    #506 was filed.
    """

    relpath = str(path.relative_to(REPO_ROOT))
    declared = DECLARED_UNPINNED_REFS.get(relpath)
    offenders = [
        (label, value)
        for label, value in unresolvable_pins(_rendered(path))
        if not (label == "action ref" and value == declared)
    ]
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} names "
        + ", ".join(f"{label} {value}" for label, value in offenders)
        + f"; the newest published release is {LATEST_PUBLISHED_VERSION}. Name a "
        "published ref, or mark the route as the preview channel it is — see "
        "docs/distribution-surfaces.md § Release channels."
    )


def test_pin_scanner_catches_a_seeded_wrong_ref():
    """Negative control: a wrong ref, and a wrong package version beside a good ref.

    The second case is the one a ref-only scanner misses: `action.yml` installs
    `agents-shipgate==${{ inputs.shipgate_version }}`, so a workflow can pass
    Action-ref resolution and then fail at `pip install`.
    """

    assert unresolvable_pins("uses: ThreeMoonsLab/agents-shipgate@v9.9.9") == [
        ("action ref", "v9.9.9")
    ]
    assert not unresolvable_pins(
        f"uses: ThreeMoonsLab/agents-shipgate@v{LATEST_PUBLISHED_VERSION}"
    )
    good_ref_bad_version = (
        f"      - uses: ThreeMoonsLab/agents-shipgate@v{LATEST_PUBLISHED_VERSION}\n"
        "        with:\n"
        "          shipgate_version: '9.9.9'\n"
    )
    assert unresolvable_pins(good_ref_bad_version) == [
        ("shipgate_version input", "9.9.9")
    ]
    # A reader blank is a blank, not a bad ref.
    assert not unresolvable_pins("uses: ThreeMoonsLab/agents-shipgate@v<NEW>")


def test_pin_scanner_catches_an_unreachable_install_floor():
    """A floor is judged reachable, not equal — a pin's rule would misread it."""

    assert not unresolvable_pins('pip install -U "agents-shipgate>=0.15"')
    assert not unresolvable_pins('pip install -U "agents-shipgate>=0.13"')
    assert unresolvable_pins('pip install -U "agents-shipgate>=9.9"') == [
        ("pip floor", "9.9")
    ]
    assert _release("0.15") == (0, 15, 0) == _release("0.15.0")


def test_the_committed_sweep_adds_files_the_emitted_sweep_cannot_see():
    """This module earns its place only where #506's does not reach.

    #506 renders what `init` writes; these are files a reader opens in the
    repository, which that sweep never sees. If this set ever empties, the
    scanner here is redundant and should go.
    """

    scanned = {str(path.relative_to(REPO_ROOT)) for path in _pin_bearing_paths()}
    committed_only = {rel for rel in scanned if rel.startswith(("examples/", "docs/"))}
    assert committed_only, (
        "no committed-only surface carries a pin any more; #506's emitted sweep "
        "would then cover everything this scanner does."
    )


# --------------------------------------------------------------------------
# contract_floor — a floor the named build can actually reach.
# --------------------------------------------------------------------------
#
# The prompts' floor is no longer a literal to scan for. #506 made it a rendered
# statement — `contract_floor_prose` decides, in one place, whether the release
# the prompt pins reports the floor the prompt demands, and writes the honest
# sentence either way — and `test_adopter_pins_resolve.py` proves it against the
# tag. This module had a regex scan for the older prose; it is deleted rather
# than kept beside the real rule, and the registry points the claim at the test
# that owns it.
#
# What remains here is the one surface #506 does not render: the design-partner
# runbook states, in a channel table a human reads, which contract the published
# release implements. Nothing else checks that number.


def test_runbook_channel_table_states_the_released_contract_correctly():
    """The design-partner runbook names one channel per partner, with its contract.

    It used to teach a v0.15.0 route that needed "runtime contract 14" — a
    number the named build does not reach and never did — while its read order
    named `control.state`, which that build does not emit at all.
    """

    text = (REPO_ROOT / "docs" / "design-partner-verifier-pilot.md").read_text(
        encoding="utf-8"
    )
    row = re.search(
        rf"\|\s*Published release `v{re.escape(LATEST_PUBLISHED_VERSION)}`\s*\|[^|]*\|\s*(\d+)\s*\|",
        text,
    )
    assert row, (
        "docs/design-partner-verifier-pilot.md must carry a channel row for the "
        f"published release v{LATEST_PUBLISHED_VERSION} naming the contract it "
        "implements."
    )
    assert row.group(1) == LATEST_PUBLISHED_CONTRACT_VERSION, (
        f"the runbook says the published release implements contract "
        f"{row.group(1)}; agents_shipgate.published_release records "
        f"{LATEST_PUBLISHED_CONTRACT_VERSION}."
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
