"""Adopter-facing output must not require knowing the internal identity model.

Invariant 5 of the adoption walk (#327), filed as #329. The message that made
the case was printed at someone running Agents Shipgate on their own
repository for the first time::

    Duplicate tool observation identity: source_type='google_adk_function',
    source_id='google_adk:agent.py',
    native_locator='agent.py#map_salesforce_account_to_sap_bp'

Three internal concepts, none of them in the manifest that person wrote, and
the one recoverable fact — a file was read twice — unstated. Fixing that one
message is not the point; nothing tracked whether adopter-facing strings speak
the adopter's vocabulary, so nothing stopped the next one.

This file is that tracker. It enumerates the adopter-facing strings four ways,
because no single enumeration reaches all of them:

1. **Rendered gap rows.** Every ``EvidenceGap`` kind the report schema
   declares, pushed through the real renderers — the CLI headline, the
   ``Improve evidence:`` action text, and ``fix_task.instructions[]``. Adding
   a gap kind without adopter-facing wording fails here.
2. **Rendered message builders.** Every public builder in
   ``core.source_warnings``, and the declaration scaffold, called and checked
   as the reader sees them. The sweep table must cover the module's ``__all__``,
   so a new builder cannot arrive unswept.
3. **Hand-written prose, statically.** Every string literal written at an emit
   site in the modules that produce console output, next actions, handoff
   prose, and PR comment text — including the branches of the big diagnostic
   resolvers, which no fixture reaches all of.
4. **Shipped artifacts.** The adopter-facing strings in every bundled sample's
   ``report.json`` and ``report.md``, which is what the output actually looks
   like rather than what the code says it should.

Plus three end-to-end runs, because the assembled artifact is the only thing
that can prove the assembly did not put an identifier back: the failure #321
reported, walked through ``scan`` and again through ``verify``, and one real
``insufficient_evidence`` verdict whose ``agent-handoff.json``,
``verifier.json``, and PR comment are swept as written.

The rule itself, and why ``source_id`` is treated differently from
``native_locator``, lives in :mod:`agents_shipgate.core.adopter_text`.
"""

from __future__ import annotations

import ast
import itertools
import json
import re
import subprocess
from pathlib import Path
from typing import get_args

import pytest
from typer.testing import CliRunner

import agents_shipgate.ci.release_decision as rd
from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan.declarations import build_declaration_scaffold
from agents_shipgate.cli.verify.fix_task import _insufficient_evidence_remedies
from agents_shipgate.core import source_warnings as sw
from agents_shipgate.core.adopter_text import (
    DUPLICATE_TOOL_IN_SOURCE,
    INTERNAL_ONLY_TERMS,
    internal_vocabulary,
)
from agents_shipgate.core.domain import Tool
from agents_shipgate.core.evidence_actions import (
    evidence_gap_action_text,
    evidence_gap_headline,
)
from agents_shipgate.core.surface_exclusions import derived_id_kind
from agents_shipgate.schemas.bindings import (
    AgentBindingGraphAssessment,
    AgentBindingIssue,
    AgentBindingNode,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import (
    EvidenceCoverageDecision,
    EvidenceGap,
    ReadinessReport,
    ReleaseDecision,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


# --- the rule is not vacuous -------------------------------------------------

# Verbatim history. Each of these shipped, each sent a reader looking for a
# field that is in no file they have, and each must stay rejected.
HISTORICAL_VIOLATIONS = (
    (
        "Duplicate tool observation identity: source_type='google_adk_function', "
        "source_id='google_adk:agent.py', "
        "native_locator='agent.py#map_salesforce_account_to_sap_bp'"
    ),
    "loaded tool source has a blank source_id",
    (
        "Tool observation obs_v1_"
        "3f9a1c2b8e7d45060f4b9c1e2a7d8f3b5c6e0a19d4f7b2c8e1a3d6f9b0c4e7a25"
        " appears in multiple bindings: orders_process, orders_legacy"
    ),
    "process_order [tool_v2_2c9ee6aefb31] has no declared effect",
    "Unknown adapter source_type 'acme' (install/enable the adapter, or fix a typo)",
    "Accepted values: unique_source_id, stable_native_locator.",
    "member source_id='orders_b', tool='process_order' matched 0 observations",
)

# Anchored spellings that must keep passing. `source_id` really is a manifest
# key; refusing it outright would force these messages to stop naming the field
# the reader has to edit, which is the opposite of the point.
ANCHORED_SPELLINGS = (
    "Add source_id='orders' to the tool_inventories entry, then rerun the scan.",
    "Correct the member to name a configured shipgate.yaml#tool_sources[].id.",
    "Read it from `findings[].fingerprint` in `report.json`.",
    "Declare the exact join at shipgate.yaml#tool_identity.bindings.",
)


@pytest.mark.parametrize("message", HISTORICAL_VIOLATIONS)
def test_the_rule_rejects_the_messages_it_was_written_for(message: str) -> None:
    assert internal_vocabulary(message), message


@pytest.mark.parametrize("message", ANCHORED_SPELLINGS)
def test_a_manifest_key_named_with_its_surface_is_not_internal(message: str) -> None:
    assert internal_vocabulary(message) == (), message


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("customer_agent_v1_deadbeef", id="agent-name-substring"),
        pytest.param("my_tool_v2_deadbeef12", id="tool-name-substring"),
        pytest.param("legacy_fp_0123456789abcdef", id="fingerprint-substring"),
    ],
)
def test_an_adopter_named_identifier_is_not_a_derived_id(label: str) -> None:
    """Agent and tool names are adopter-controlled strings (#329 review).

    An unbounded search called an agent legitimately named
    ``customer_agent_v1_deadbeef`` a derived agent id. Once that agent has an
    evidence gap the conservation invariant refuses the subject and aborts an
    otherwise valid scan — a vocabulary rule turning into an outage.
    """

    assert internal_vocabulary(label) == (), label
    assert derived_id_kind(label) is None, label


@pytest.mark.parametrize(
    "identifier",
    [
        pytest.param("agent_v1:7205d836e4b3fee257d90695", id="agent"),
        pytest.param("tool_v2_" + "a" * 64, id="tool"),
        pytest.param("charge_card [tool_v2_" + "b" * 64 + "]", id="tool-in-a-label"),
        pytest.param("obs_v1_" + "c" * 64, id="observation"),
        pytest.param("fp_f092940f62fbb012", id="fingerprint"),
    ],
)
def test_the_real_shapes_are_still_refused(identifier: str) -> None:
    """Narrowing the patterns must not stop them matching what they exist for."""

    assert internal_vocabulary(identifier), identifier


def test_an_anchor_cannot_rescue_a_field_that_exists_in_no_file() -> None:
    """The two categories are not the same lever.

    `source_id` becomes locatable when the message says which key it is.
    `native_locator` cannot: there is no manifest key, no report field an
    adopter is asked to read, and no file to open. Naming `shipgate.yaml`
    beside it must not launder it.
    """

    anchored = "Set a stable native_locator in shipgate.yaml, then rerun."
    assert "native_locator" in internal_vocabulary(anchored)


# --- 1. every gap kind, through the real renderers ---------------------------


def _gap_kinds() -> tuple[str, ...]:
    return get_args(EvidenceGap.model_fields["kind"].annotation)


def _probe_tool() -> Tool:
    return Tool(
        id="tool_v2_9a1c2b8e7d450612",
        name="process_order",
        source_type="google_adk_function",
        source_id="google_adk:agent.py",
        source_ref="agent.py",
        provider="google_adk:agent.py",
    )


def _gaps_for_every_kind() -> list[EvidenceGap]:
    tool = _probe_tool()
    return [
        rd._semantic_gap(tool, kind=kind, why="static evidence does not prove it")
        for kind in _gap_kinds()
    ]


@pytest.mark.parametrize("kind", _gap_kinds())
def test_every_gap_kind_renders_adopter_facing_text(kind: str) -> None:
    """The three surfaces a gap reaches, each checked as it is printed.

    ``evidence_gap_headline`` is the short form — the CLI ``Improve evidence:``
    clause, the decision reason, the GitHub step summary — and it renders
    without the gap's ``path``, so its wording has to stand alone.
    ``evidence_gap_action_text`` and the ``fix_task`` line both carry the
    target, which is what makes a manifest key locatable.
    """

    gap = rd._semantic_gap(
        _probe_tool(), kind=kind, why="static evidence does not prove it"
    )
    headline = evidence_gap_headline(gap)
    assert internal_vocabulary(headline) == (), headline
    action = evidence_gap_action_text(gap)
    assert internal_vocabulary(action) == (), action


def test_every_gap_kind_renders_an_adopter_facing_fix_task_instruction() -> None:
    """``fix_task.instructions[]`` is the durable form an agent repeats.

    Rendered by the production function rather than reassembled here, so the
    `accepted_values` list is checked in the sentence that actually publishes
    it — a bare list of selector keys means nothing without the target beside
    it, and means something exact with it.
    """

    report = ReadinessReport.model_construct(
        tool_inventory=[],
        source_warnings=[],
        release_decision=ReleaseDecision.model_construct(
            decision="insufficient_evidence",
            reason="test",
            evidence_coverage=EvidenceCoverageDecision.model_construct(
                level="mixed", evidence_gaps=_gaps_for_every_kind()
            ),
        ),
    )
    instructions = _insufficient_evidence_remedies(report)
    assert len(instructions) >= len(_gap_kinds()) - 2, instructions
    for line in instructions:
        assert internal_vocabulary(line) == (), line


def test_a_binding_gap_names_the_agent_the_reader_wrote() -> None:
    """The subject of a binding gap is a label, the way a tool's subject is.

    An issue naming no tool falls back to the agent, and the fallback used to
    be the derived id: ``samples/conductor_agent`` shipped "the agent's tool
    binding graph is incomplete (agent_v1:7205d836…)" as the sentence under
    its verdict. The identity still travels — in ``subject_id`` for tools, and
    in ``binding_surface_facts`` for agents — but the sentence names the agent
    the reader declared and the source it was read from.
    """

    graph = AgentBindingGraphAssessment(
        root_agent_id="agent_v1:507abc67404233d2ccd1c2d1",
        status="partial",
        agents=[
            AgentBindingNode(
                agent_id="agent_v1:507abc67404233d2ccd1c2d1",
                name="closer_agent",
                source_id="google_adk:agent.py",
                source_ref="agent.py",
            )
        ],
        issues=[
            AgentBindingIssue(
                kind="partial_binding_evidence",
                message="Google ADK toolset 'dynamic' is not statically enumerable.",
                agent_id="agent_v1:507abc67404233d2ccd1c2d1",
            )
        ],
    )
    report = ReadinessReport.model_construct(
        binding_surface_facts=graph, tool_catalog=[]
    )
    _coverage, gaps = rd._binding_coverage(report)
    (gap,) = gaps
    assert gap.subject == "closer_agent [google_adk:agent.py]"
    assert internal_vocabulary(evidence_gap_headline(gap)) == ()


def test_an_unresolved_handoff_is_not_labelled_as_the_root_agent() -> None:
    """The gap is about the agent that is missing, not the one that is fine.

    `unresolved_agent_binding` carries `agent_id=None` precisely because the
    endpoint could not be resolved. Falling through to `graph.root_agent_id`
    labelled `root -> missing_worker` as `root [sdk]` and propagated that name
    into the verdict and the fix task (#329 review).
    """

    graph = AgentBindingGraphAssessment(
        root_agent_id="agent_v1:507abc67404233d2ccd1c2d1",
        status="partial",
        agents=[
            AgentBindingNode(
                agent_id="agent_v1:507abc67404233d2ccd1c2d1",
                name="root",
                source_id="sdk",
                source_ref="agents.py",
            )
        ],
        issues=[
            AgentBindingIssue(
                kind="unresolved_agent_binding",
                message="Binding references unresolved agent 'missing_worker'.",
                source="shipgate.yaml",
                source_pointer="shipgate.yaml#/agent_bindings/declarations/0/agent",
            )
        ],
    )
    report = ReadinessReport.model_construct(
        binding_surface_facts=graph, tool_catalog=[]
    )
    _coverage, (gap,) = rd._binding_coverage(report)
    assert gap.subject == "shipgate.yaml#/agent_bindings/declarations/0/agent"
    assert "root" not in gap.subject
    assert internal_vocabulary(evidence_gap_headline(gap)) == ()


def _binding_graph(observations, declarations=None, sources=("a",)):
    """A real graph, built by the production resolver.

    The shapes under test are produced by `resolve_agent_binding_graph`, not by
    hand: the previous fix was written against a constructed issue and missed
    both of the shapes the resolver actually emits (#329 review 2).
    """

    from agents_shipgate.core.agent_bindings import resolve_agent_binding_graph
    from agents_shipgate.core.artifacts import ArtifactBag
    from agents_shipgate.core.domain import AgentBindingObservation, LoadedToolSource

    tool = Tool(
        id="tool_v2_a", name="lookup", source_type="sdk_function", source_id="a"
    )
    loaded = [
        LoadedToolSource(
            source_id=source_id,
            source_type="openai_agents_sdk",
            tools=[tool] if source_id == "a" else [],
            binding_observations=[
                AgentBindingObservation(**observation)
                for observation in observations
                if observation["source_id"] == source_id
            ],
        )
        for source_id in sources
    ]
    manifest = AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "handoff-probe"},
            "agent": {"name": "root", "declared_purpose": ["look things up"]},
            "environment": {"target": "local"},
            "tool_sources": [
                {"id": source_id, "type": "mcp", "path": f"{source_id}.json"}
                for source_id in sources
            ],
            "agent_bindings": {
                "root": {"source_id": "a", "object": "root"},
                **({"declarations": declarations} if declarations else {}),
            },
        }
    )
    graph, _narrowed = resolve_agent_binding_graph(
        manifest, [tool], ArtifactBag(), loaded_sources=loaded
    )
    report = ReadinessReport.model_construct(
        binding_surface_facts=graph, tool_catalog=[]
    )
    _coverage, gaps = rd._binding_coverage(report)
    return graph, gaps


def test_an_incomplete_handoff_is_not_labelled_by_its_source_agent() -> None:
    """The edge is incomplete; the agent that declares it is not the problem.

    `incomplete_handoff_graph` carries `edge.source_agent_id` — the healthy
    referrer — so the branch that trusts `issue.agent_id` subjected the gap
    `root [a]` (#329 review 2).
    """

    graph, gaps = _binding_graph(
        [
            {
                "agent": "root",
                "source_id": "a",
                "source": "framework_extraction",
                "tool_names": ["lookup"],
                "handoff_names": ["worker"],
                "handoffs_complete": False,
                "source_pointer": "agents.py#L12",
            },
            {"agent": "worker", "source_id": "a", "source": "framework_extraction"},
        ]
    )
    (issue,) = [i for i in graph.issues if i.kind == "incomplete_handoff_graph"]
    # The producer really does carry the healthy agent, which is why a rule
    # keyed on "has an agent_id" cannot work here.
    assert issue.agent_id == graph.root_agent_id
    (gap,) = [g for g in gaps if g.kind == "incomplete_handoff_graph"]
    assert gap.subject == "agents.py#L12"
    assert internal_vocabulary(evidence_gap_headline(gap)) == ()


def test_an_ambiguous_handoff_target_is_not_labelled_by_its_referrer() -> None:
    """The other shape: two agents named `worker`, so the target resolves to
    neither — and the issue carries the declaring agent's id outright."""

    graph, gaps = _binding_graph(
        [
            {"agent": "root", "source_id": "a", "source": "framework_extraction"},
            {"agent": "worker", "source_id": "a", "source": "framework_extraction"},
            {"agent": "worker", "source_id": "b", "source": "framework_extraction"},
        ],
        declarations=[
            {
                "agent": "root",
                "complete": True,
                "tools": [{"tool": "lookup", "source_id": "a"}],
                "handoffs": ["worker"],
                "reason": "reviewed wiring",
            }
        ],
        sources=("a", "b"),
    )
    (issue,) = [i for i in graph.issues if i.kind == "unresolved_agent_binding"]
    assert issue.agent_id, "the producer names the declaring agent"
    (gap,) = [g for g in gaps if g.kind == "unresolved_agent_binding"]
    assert gap.subject == "/agent_bindings/declarations/0/handoffs/0"
    assert "root" not in gap.subject
    assert internal_vocabulary(evidence_gap_headline(gap)) == ()


def test_an_identity_gap_sends_the_reader_and_the_agent_to_one_place() -> None:
    """`path` and `expects` are read by different consumers, so they must agree.

    An agent routes on `next_action.path` while a human reads `expects`; the
    two named different sections of shipgate.yaml for `incomplete_tool_identity`
    (#329 review). Asserted together, because either one alone passes.
    """

    gap = rd._semantic_gap(
        _probe_tool(), kind="incomplete_tool_identity", why="test"
    )
    assert gap.next_action.path == "shipgate.yaml#tool_sources"
    assert "shipgate.yaml#tool_sources" in gap.next_action.expects
    assert "tool_identity" not in gap.next_action.expects


def test_an_unnamed_agent_never_falls_back_to_its_id() -> None:
    """The fallback that would abort the scan it describes.

    An extractor that resolves no literal ``name=`` yields a node with an
    empty name. Returning the agent id there is not merely unreadable — the
    conservation invariant refuses a derived id in any gap subject, so the
    scan would raise instead of reporting.
    """

    from agents_shipgate.core.surface_exclusions import agent_subject

    unnamed = AgentBindingNode(
        agent_id="agent_v1:507abc67404233d2ccd1c2d1",
        name="   ",
        source_id="google_adk:agent.py",
        source_ref="agent.py",
    )
    subject = agent_subject(unnamed)
    assert subject == "agent.py"
    assert internal_vocabulary(subject) == ()


def test_an_unknown_agent_falls_back_to_something_readable() -> None:
    """A fallback that returns the unreadable value defeats itself.

    An issue can name an agent the graph did not record — the chain has to end
    somewhere a reader can go, so it ends at the source pointer and then at
    prose, never back at the id.
    """

    graph = AgentBindingGraphAssessment(
        root_agent_id=None,
        status="unknown",
        agents=[],
        issues=[
            AgentBindingIssue(
                kind="missing_binding_evidence",
                message="No static binding edge was proven.",
                agent_id="agent_v1:507abc67404233d2ccd1c2d1",
                source_pointer="agents/closer/agent.py#L12",
            ),
            AgentBindingIssue(
                kind="missing_binding_evidence",
                message="No static binding edge was proven.",
                agent_id="agent_v1:d1c2d1404233507abc674042",
            ),
        ],
    )
    report = ReadinessReport.model_construct(
        binding_surface_facts=graph, tool_catalog=[]
    )
    _coverage, gaps = rd._binding_coverage(report)
    assert [gap.subject for gap in gaps] == [
        "agents/closer/agent.py#L12",
        "agent binding graph",
    ]
    for gap in gaps:
        assert internal_vocabulary(gap.subject) == ()


# --- 2. every rendered message builder ---------------------------------------

# One probe per public message builder in `core.source_warnings`. The values
# are deliberately realistic: a synthetic id makes a vocabulary guard as
# vacuous as it makes a shape guard.
SOURCE_WARNING_PROBES: dict[str, str] = {
    "adk_unresolved_tool_warning": sw.adk_unresolved_tool_warning(
        "smart_closer", "map_salesforce_account_to_sap_bp"
    ),
    "invalid_tool_binding_warning": sw.invalid_tool_binding_warning(
        "orders_process",
        [
            sw.unmatched_binding_member("orders_b", "process_order", 0),
            sw.zero_observation_binding_member("orders_empty", "process_order"),
            sw.unknown_binding_member_source("orders_typo", "process_order"),
        ],
    ),
    "unmatched_binding_member": sw.unmatched_binding_member(
        "orders_b", "process_order", 2
    ),
    "zero_observation_binding_member": sw.zero_observation_binding_member(
        "orders_empty", "process_order"
    ),
    "unknown_binding_member_source": sw.unknown_binding_member_source(
        "orders_typo", "process_order"
    ),
    "unknown_inventory_source_warning": sw.unknown_inventory_source_warning(
        "tools/inventory.json", "orders_typo", ["orders_a", "orders_b"]
    ),
    "self_referential_inventory_warning": sw.self_referential_inventory_warning(
        "tools/inventory.json"
    ),
    "unbound_inventory_duplicate_warning": sw.unbound_inventory_duplicate_warning(
        "tools/inventory.json", "orders_a", ["process_order", "refund_order"]
    ),
    "ambiguous_inventory_merge_warning": sw.ambiguous_inventory_merge_warning(
        "tools/inventory.json", "orders_a", ["process_order"]
    ),
}

# Public names in `core.source_warnings` that do not write a message: the
# grouping machinery, the decoders, and the display normalizer.
SOURCE_WARNING_NON_MESSAGES = frozenset(
    {
        "SourceWarningGroup",
        "group_source_warnings",
        "unresolved_adk_tool_symbols",
        "visible_skeleton",
        "withdraw_completed_adk_tool_warnings",
    }
)


def test_every_source_warning_builder_is_swept() -> None:
    """A new warning builder cannot arrive without a probe.

    The enumeration is the module's own ``__all__``, so the table cannot go
    stale silently — which is the whole reason this is a sweep and not nine
    separate regression tests.
    """

    published = set(sw.__all__)
    assert SOURCE_WARNING_NON_MESSAGES <= published, (
        "the non-message list names something core.source_warnings no longer "
        f"publishes: {sorted(SOURCE_WARNING_NON_MESSAGES - published)}"
    )
    unswept = published - set(SOURCE_WARNING_PROBES) - SOURCE_WARNING_NON_MESSAGES
    assert not unswept, (
        "core.source_warnings publishes message builders this file does not "
        f"sweep: {sorted(unswept)}. Add a probe to SOURCE_WARNING_PROBES, or "
        "record it in SOURCE_WARNING_NON_MESSAGES if it writes no message."
    )


@pytest.mark.parametrize("name", sorted(SOURCE_WARNING_PROBES))
def test_source_warnings_speak_the_adopters_vocabulary(name: str) -> None:
    message = SOURCE_WARNING_PROBES[name]
    assert internal_vocabulary(message) == (), message


def test_grouped_source_warnings_speak_the_adopters_vocabulary() -> None:
    """The grouped display projection is a second renderer, with its own prose."""

    raw = [
        sw.adk_unresolved_tool_warning("smart_closer", symbol)
        for symbol in ("map_account", "map_product")
    ] + [
        sw.invalid_tool_binding_warning(
            f"bind_{index}",
            [sw.zero_observation_binding_member("orders_empty", "process_order")],
        )
        for index in range(2)
    ]
    groups = sw.group_source_warnings(raw)
    assert groups
    for group in groups:
        assert internal_vocabulary(group.message) == (), group.message


def test_the_declaration_scaffold_speaks_the_adopters_vocabulary() -> None:
    """The one file the scaffold writes, checked whole.

    Its lines are fragments by construction — a YAML key on one line, the
    comment naming what it accepts on another — so the assembled file is the
    only honest unit to check.
    """

    graph = AgentBindingGraphAssessment(
        root_agent_id=None,
        status="unknown",
        agents=[
            AgentBindingNode(
                agent_id="a1", name="AlphaAgent", source_id="adk", source_ref="agent.py"
            )
        ],
        issues=[AgentBindingIssue(kind="ambiguous_root_agent", message="ambiguous")],
    )
    report = ReadinessReport.model_construct(binding_surface_facts=graph)
    _coverage, binding_gaps = rd._binding_coverage(report)
    scaffold = build_declaration_scaffold(
        [*binding_gaps, *_gaps_for_every_kind()], agents=graph.agents
    )
    assert scaffold is not None
    # The scaffold writes a *tool* id into the block for the reader to paste,
    # which is what keeps a selector unambiguous when two tools share a name
    # (#388). Nothing asks them to know what it is. Only that kind is
    # forgiven: an agent id here would still be an offender, and prose about
    # an id is refused either way.
    assert internal_vocabulary(scaffold, given_id_kinds={"tool"}) == (), scaffold


# --- 3. hand-written prose at the emit sites ---------------------------------

# Modules whose strings are written by hand where they are emitted: console
# output, next actions, handoff and PR prose, and the failures that abort
# before a report exists. `cli/scan/declarations.py` is deliberately absent —
# it emits one assembled file, swept whole above, and its per-line fragments
# would read as violations of a rule the file satisfies.
ADOPTER_FACING_MODULES = (
    "ci/github_summary.py",
    "cli/explain_finding.py",
    "ci/release_decision.py",
    "cli/_helpers.py",
    "cli/_register_scan.py",
    "cli/diagnostics.py",
    "cli/scan/writing.py",
    "cli/verify/fix_task.py",
    "cli/verify/orchestrator.py",
    "inputs/adapter_validation.py",
    "core/agent_control.py",
    "core/agent_control_envelope.py",
    "core/agent_controls.py",
    "core/agent_handoff.py",
    "core/evidence_actions.py",
    "core/findings/subject_rollup.py",
    "core/source_warnings.py",
    "core/tool_identity.py",
    "report/markdown.py",
    "report/pr_comment.py",
    "report/summary_text.py",
)

# A string that is only an identifier is a dict key or a field name, not
# something anyone reads. `{"native_locator": locator}` is the identity model
# doing its job.
_KEY_LIKE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[\])?(\.[A-Za-z_][A-Za-z0-9_]*(\[\])?)*$")

# Below this, a literal is a fragment of a sentence assembled elsewhere rather
# than a sentence. Fragments are checked in their assembled form by the
# rendered sweeps above; checking them here would flag ": member source_id="
# for saying less than the message it is part of. Applies only to the terms an
# anchor can rescue: a term from INTERNAL_ONLY_TERMS is refused at any length.
_MIN_SENTENCE_WORDS = 5


def _scope_chains(tree: ast.Module) -> dict[int, tuple[ast.AST, ...]]:
    """Every node's enclosing scopes, innermost first.

    Name resolution has to be lexical or the sweep launders violations across
    unrelated functions: two ``guidance`` locals, one unanchored and one
    naming ``shipgate.yaml``, were concatenated into every f-string that
    interpolated either, and the unrelated anchor cleared the offender.
    """

    chains: dict[int, tuple[ast.AST, ...]] = {}

    def visit(node: ast.AST, chain: tuple[ast.AST, ...]) -> None:
        chains[id(node)] = chain
        inner = chain
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            inner = (node, *chain)
        for child in ast.iter_child_nodes(node):
            visit(child, inner)

    visit(tree, (tree,))
    return chains


def _render_literal(node: ast.AST, names: dict[str, list[str]]) -> str | None:
    """The text of a string expression, with interpolations as ``{}``.

    One level of local-name substitution, because a sentence assembled from a
    variable is still one sentence to the reader: ``_inventory_remediation``
    builds its manifest snippet into ``binding`` and interpolates it into a
    return value that names ``shipgate.yaml``. Judging the fragment alone
    would call an anchored message unanchored.
    """

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue) and isinstance(
                value.value, ast.Name
            ):
                parts.append(" ".join(names.get(value.value.id, ("{}",))))
            else:
                parts.append("{}")
        return "".join(parts)
    return None


def _literal_alternatives(node: ast.AST, names: dict[str, list[str]]) -> list[str]:
    """Every string this expression can evaluate to, kept apart.

    Concatenating the two arms of a conditional is the same laundering as
    concatenating two scopes: an anchored branch clears an unanchored one, and
    only one of them is ever what the reader sees.
    """

    if isinstance(node, ast.IfExp):
        return [
            text
            for arm in (node.body, node.orelse)
            for text in _literal_alternatives(arm, names)
        ]
    rendered = _render_literal(node, names)
    return [] if rendered is None else [rendered]


#: Call shapes that put a string in front of a reader with no further
#: processing: the first argument of ``typer.echo``, and the fields of the
#: structured actions and errors. A string here is a complete message, so the
#: fragment and dict-key exemptions below must not apply to it — a probe with
#: ``typer.echo("native_locator")`` looked key-like and one with a four-word
#: sentence looked like a fragment, and neither was reported (#329 review 3).
_EMITTING_KEYWORDS = frozenset(
    {"why", "expects", "title", "message", "reason", "recommendation", "remediation"}
)
_EMITTING_CALLS = frozenset({"echo", "secho"})


def _definitely_emitted(tree: ast.Module) -> set[int]:
    """Nodes whose string value is handed straight to a reader."""

    emitted: set[int] = set()

    def mark(node: ast.AST) -> None:
        emitted.add(id(node))
        # An `x if c else y` in an emitting position emits both arms.
        if isinstance(node, ast.IfExp):
            mark(node.body)
            mark(node.orelse)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else (
            node.func.id if isinstance(node.func, ast.Name) else ""
        )
        if name in _EMITTING_CALLS and node.args:
            mark(node.args[0])
        for keyword in node.keywords:
            if keyword.arg in _EMITTING_KEYWORDS:
                mark(keyword.value)
    return emitted


def _emitted_strings(path: Path) -> list[tuple[int, str, bool]]:
    """Every string written at an emit site in ``path``.

    Docstrings are excluded — they are written for the person editing this
    repository, and the identity model is exactly the right vocabulary there.
    So are the parts inside an f-string, which are read as the whole they
    belong to, and the right-hand sides that were substituted into one.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    chains = _scope_chains(tree)
    definite = _definitely_emitted(tree)

    # String assignments, keyed by (owning scope, name) rather than by name,
    # and kept in source order so a use can take the definition that reaches
    # it rather than every definition the scope ever makes.
    assigned: dict[tuple[int, str], list[ast.Assign]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _literal_alternatives(node.value, {})
        ):
            owner = chains[id(node)][0]
            assigned.setdefault((id(owner), node.targets[0].id), []).append(node)
    for bindings in assigned.values():
        bindings.sort(key=lambda assign: (assign.lineno, assign.col_offset))

    def _reaching(
        scope_chain: tuple[ast.AST, ...], name: str, use: ast.AST
    ) -> list[ast.Assign]:
        """The definition of ``name`` in effect at ``use``.

        Lexical, then positional: the innermost scope that binds the name, and
        within it the last assignment above the use. Taking every assignment
        in the scope let a later anchored definition clear an earlier
        unanchored one that had already been interpolated (#329 review 2).
        """

        for scope in scope_chain:
            bindings = assigned.get((id(scope), name))
            if not bindings:
                continue
            line = getattr(use, "lineno", 0)
            earlier = [b for b in bindings if b.lineno <= line]
            # A use above every definition in its scope is a forward
            # reference — a closure called later — so every definition is
            # reachable and each is kept as its own alternative.
            return [earlier[-1]] if earlier else bindings
        return []

    # Only names an f-string actually interpolates are substituted, and only
    # those have their assignment consumed. Consuming every string assigned to
    # a name would open the hole this whole file exists to close: a sentence
    # built as `guidance = "…"` and passed to `NextAction(why=guidance)` is
    # never interpolated anywhere, so it would be excluded from the sweep and
    # checked nowhere — which is exactly how `_register_scan` used to write
    # its recovery prose.
    names_at: dict[int, dict[str, list[str]]] = {}
    substituted: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        local: dict[str, list[str]] = {}
        for value in node.values:
            if not (
                isinstance(value, ast.FormattedValue)
                and isinstance(value.value, ast.Name)
            ):
                continue
            bindings = _reaching(chains[id(node)], value.value.id, node)
            if not bindings:
                continue
            local[value.value.id] = [
                text
                for assign in bindings
                for text in _literal_alternatives(assign.value, {})
            ]
            for assign in bindings:
                substituted.update(id(inner) for inner in ast.walk(assign.value))
        if local:
            names_at[id(node)] = local

    docstrings: set[int] = set()
    inside_fstring: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            docstrings.add(id(body[0].value))
        if isinstance(node, ast.JoinedStr):
            inside_fstring.update(id(part) for part in node.values)

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if id(node) in docstrings or id(node) in inside_fstring:
            continue
        if id(node) in substituted:
            continue
        local = names_at.get(id(node), {})
        for alternatives in _cartesian(node, local):
            found.append((node.lineno, alternatives, id(node) in definite))
    return found


def _cartesian(node: ast.AST, names: dict[str, list[str]]) -> list[str]:
    """Every string ``node`` can render to, one per combination of choices.

    A name bound to two alternatives — the arms of a conditional, or two
    reachable definitions — must not be joined into one string, or either can
    clear the other. With two such names it is not enough to vary them one at
    a time either: specializing `left` while `right` stays joined leaves the
    anchor from `right` in every rendering, and the one combination that
    matters — both unanchored — is never emitted (#329 review 3).
    """

    keys = [key for key, values in names.items() if len(values) > 1]
    if not keys:
        return _literal_alternatives(node, names)
    rendered: list[str] = []
    for combination in itertools.product(*(names[key] for key in keys)):
        chosen = {**names, **dict(zip(keys, ([v] for v in combination), strict=True))}
        rendered.extend(_literal_alternatives(node, chosen))
    return rendered


def test_the_extractor_sees_a_sentence_built_through_a_variable(tmp_path) -> None:
    """The sweep is only as good as what it can see.

    A recovery sentence assigned to a local and handed to ``NextAction`` is
    how `scan` wrote this exact copy before #329, and it is invisible to any
    extractor that treats a named string as a fragment of something else. The
    substitution pass has to consume *only* the names an f-string actually
    interpolates, or the commonest shape in these modules is swept nowhere.
    """

    probe = tmp_path / "probe.py"
    probe.write_text(
        'def f():\n'
        '    guidance = (\n'
        '        "Inspect the source_type in the error and fix it before "\n'
        '        "the scan can resolve anything at all."\n'
        '    )\n'
        '    return NextAction(kind="review", why=guidance, expects=guidance)\n',
        encoding="utf-8",
    )
    found = [text for _line, text, _definite in _emitted_strings(probe)]
    assert any("Inspect the source_type" in text for text in found), found
    assert any(internal_vocabulary(text) for text in found)


def test_the_extractor_resolves_a_name_in_its_own_scope(tmp_path) -> None:
    """A name is looked up lexically, or the sweep launders across functions.

    Two functions both assigning ``guidance`` — one unanchored, one naming
    ``shipgate.yaml`` — were rendered as the concatenation of both into every
    f-string that interpolated either, and the unrelated anchor cleared the
    offender while both right-hand sides were marked substituted and checked
    nowhere.
    """

    probe = tmp_path / "probe.py"
    probe.write_text(
        'def a():\n'
        '    guidance = "Inspect source_id before proceeding anywhere else."\n'
        '    return f"{guidance}"\n'
        '\n'
        'def b():\n'
        '    guidance = "Open shipgate.yaml and correct the entry, then rerun."\n'
        '    return f"{guidance}"\n',
        encoding="utf-8",
    )
    rendered = {
        text: internal_vocabulary(text)
        for _line, text, _definite in _emitted_strings(probe)
    }
    offending = [text for text, bad in rendered.items() if bad]
    assert len(offending) == 1, rendered
    assert "source_id" in offending[0]
    assert "shipgate.yaml" not in offending[0]


def test_the_extractor_varies_every_alternative_together(tmp_path) -> None:
    """Two names with alternatives need the product, not one at a time.

    Specializing `left` while `right` stayed joined left `right`'s anchor in
    every rendering, so all four strings passed and the one combination that
    matters — both unanchored — was never emitted (#329 review 3).
    """

    probe = tmp_path / "probe.py"
    probe.write_text(
        'def f(flag, other):\n'
        '    left = "Inspect source_id first." if flag else "Open shipgate.yaml now."\n'
        '    right = "Then check source_type." if other else "Then read report.json."\n'
        '    return f"{left} {right}"\n',
        encoding="utf-8",
    )
    rendered = {
        text: internal_vocabulary(text)
        for _line, text, _definite in _emitted_strings(probe)
    }
    assert len(rendered) == 4, rendered
    both_unanchored = [text for text, bad in rendered.items() if len(bad) == 2]
    assert len(both_unanchored) == 1, rendered
    assert internal_vocabulary(both_unanchored[0]) == ("source_id", "source_type")


def test_the_extractor_reads_an_interpolated_fragment_in_its_whole(tmp_path) -> None:
    """And the other direction, which is why the substitution exists.

    ``_inventory_remediation`` builds its manifest snippet into a local and
    interpolates it into a sentence that names ``shipgate.yaml``. Judged on
    its own the fragment is an unanchored ``source_id``; judged as what the
    reader sees, it is anchored.
    """

    probe = tmp_path / "probe.py"
    probe.write_text(
        'def f(source):\n'
        '    binding = "an entry carrying `source_id: <the source above>`"\n'
        '    return f"Reference it from shipgate.yaml as {binding}, then rerun."\n',
        encoding="utf-8",
    )
    for _line, text, _definite in _emitted_strings(probe):
        assert internal_vocabulary(text) == (), text


#: Modules whose strings legitimately carry an identifier the reader already
#: has, mapped to *which kind* they were given. `explain-finding` takes a
#: fingerprint as its argument: its help shows an example of the shape to
#: supply, and its unknown-fingerprint error echoes the value the reader just
#: typed. Neither asks the reader to decode anything, and both name
#: `findings[].fingerprint` in `report.json` as where the right one lives —
#: which is what the term rules check, and they still apply here.
#:
#: Per kind, because a surface with a reason for one shape has no reason for
#: the other three: a module-wide exemption forgave an agent id in
#: `explain-finding` prose that nobody had supplied (#329 review 3).
MODULES_WITH_GIVEN_IDS: dict[str, frozenset[str]] = {
    "cli/explain_finding.py": frozenset({"finding"}),
}


@pytest.mark.parametrize("module", ADOPTER_FACING_MODULES)
def test_emitted_prose_speaks_the_adopters_vocabulary(module: str) -> None:
    given_ids = MODULES_WITH_GIVEN_IDS.get(module, frozenset())
    offenders: list[str] = []
    swept = 0
    for lineno, text, definite in _emitted_strings(
        REPO_ROOT / "src/agents_shipgate" / module
    ):
        stripped = text.strip()
        # The two exemptions exist for strings whose emit-site is unknown: a
        # dict key that looks like prose, a fragment assembled elsewhere.
        # Neither can be true of a string handed straight to `typer.echo` or
        # to a `NextAction` field, so a known emit site suspends both.
        if not stripped or (not definite and _KEY_LIKE.match(stripped)):
            continue
        swept += 1
        if definite or len(text.split()) >= _MIN_SENTENCE_WORDS:
            hits = list(internal_vocabulary(text, given_id_kinds=given_ids))
        else:
            hits = [term for term in INTERNAL_ONLY_TERMS if term in text]
        if hits:
            offenders.append(f"{module}:{lineno} {sorted(set(hits))} :: {text!r}")
    assert swept, f"{module} yielded no strings — the extractor stopped working"
    assert not offenders, "\n".join(offenders)


def test_a_real_adapter_validation_message_is_swept() -> None:
    """The producer whose output `report.md` prints, pinned on a real message.

    `report/markdown.py` renders every `validation_errors` / `runtime_errors`
    entry under Loaded Adapters, so these strings are adopter-facing — and
    both the producer and those two keys were missing from the sweep, so
    `source_type 'mcp' is reserved by a built-in adapter` shipped while every
    guard passed (#329 review 3).
    """

    from agents_shipgate.inputs.adapter_validation import (
        validate_adapter_entry_point,
    )

    class _Colliding:
        source_type = "mcp"
        scope = "per_source"
        artifact_class = None

        def load(self, *args, **kwargs):  # pragma: no cover - never called
            raise AssertionError("validation must not load")

    class _EntryPoint:
        name = "acme"
        value = "acme.adapter:Adapter"
        dist = None

        def load(self):
            return _Colliding()

    loaded = validate_adapter_entry_point(
        _EntryPoint(),
        builtin_source_types={"mcp"},
        already_registered_source_types=set(),
    )
    errors = loaded.info["validation_errors"]
    assert errors, loaded.info
    for message in errors:
        assert internal_vocabulary(message) == (), message
        assert "tool_sources[].type" in message


def test_the_given_id_allowance_is_narrow_and_still_checks_the_terms() -> None:
    """The carve-out exempts the shape, never the vocabulary.

    Listing a module here would be a way to smuggle a whole file past the
    sweep if it exempted anything else, so this pins both halves: an echoed
    fingerprint is allowed, and a sentence about `native_locator` in the same
    module is not.
    """

    assert set(MODULES_WITH_GIVEN_IDS) <= set(ADOPTER_FACING_MODULES)
    echoed = (
        "Unknown fingerprint: fp_f092940f62fbb012. No entry in "
        "findings[].fingerprint matches it."
    )
    assert internal_vocabulary(echoed, given_id_kinds={"finding"}) == ()
    assert internal_vocabulary(echoed) == ("fp_f092940f62fbb012",)
    # The allowance is per kind: a shape nobody supplied is still an offender.
    other_kind = "Agent agent_v1:7205d836e4b3fee257d90695 could not be resolved."
    assert internal_vocabulary(other_kind, given_id_kinds={"finding"})
    smuggled = "Set a stable native_locator in shipgate.yaml, then rerun."
    assert "native_locator" in internal_vocabulary(smuggled, given_id_kinds={"finding"})


# --- 4. what the shipped artifacts actually say ------------------------------

# Keys whose values a person reads and acts on. Report internals — the tool
# catalog, `identity_assessment`, evidence blocks — are deliberately absent:
# they are the identity model, they are consumed by tooling, and #329 puts
# them out of scope on purpose.
ADOPTER_FACING_KEYS = frozenset(
    {
        "agent_repair_instructions",
        "expects",
        "human_review",
        "instructions",
        "message",
        "next_action",
        "reason",
        "recommendation",
        "remediation",
        "runtime_errors",
        "source_warnings",
        "suggested_fixes",
        "title",
        "validation_errors",
        "warnings",
        "why",
    }
)


def _adopter_strings(node: object, path: str, key: str | None = None):
    if isinstance(node, dict):
        for name, value in node.items():
            yield from _adopter_strings(value, f"{path}.{name}", name)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _adopter_strings(value, f"{path}[{index}]", key)
    elif isinstance(node, str) and key in ADOPTER_FACING_KEYS:
        yield path, node


SAMPLE_REPORTS = sorted(REPO_ROOT.glob("samples/*/expected/report.json"))
SAMPLE_MARKDOWN = sorted(REPO_ROOT.glob("samples/*/expected/report.md"))


def test_the_shipped_artifacts_are_actually_found() -> None:
    """A glob that matches nothing parametrizes to nothing and asserts nothing.

    The two sweeps below are the only check against what the output *is*
    rather than what the code says it should be, so an empty sample set has to
    fail rather than pass silently.
    """

    assert len(SAMPLE_REPORTS) >= 5, SAMPLE_REPORTS
    assert len(SAMPLE_MARKDOWN) >= 5, SAMPLE_MARKDOWN


@pytest.mark.parametrize(
    "report_path",
    SAMPLE_REPORTS,
    ids=lambda path: path.parent.parent.name,
)
def test_sample_reports_speak_the_adopters_vocabulary(report_path: Path) -> None:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    offenders = [
        f"{where} {internal_vocabulary(text)} :: {text[:200]!r}"
        for where, text in _adopter_strings(payload, "")
        if internal_vocabulary(text)
    ]
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "markdown_path",
    SAMPLE_MARKDOWN,
    ids=lambda path: path.parent.parent.name,
)
def test_sample_markdown_speaks_the_adopters_vocabulary(markdown_path: Path) -> None:
    offenders = [
        f"{markdown_path.name}:{number} {internal_vocabulary(line)} :: {line.strip()!r}"
        for number, line in enumerate(
            markdown_path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if internal_vocabulary(line)
    ]
    assert not offenders, "\n".join(offenders)


# --- the failure this issue was filed for, end to end ------------------------


_DUPLICATE_ENTRYPOINT_AGENT = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def map_salesforce_account_to_sap_bp(account_id: str) -> dict:
    """Map a Salesforce account onto an SAP business partner."""
    return {"sap_bp": account_id}


root_agent = LlmAgent(
    name="smart_closer",
    instruction="Close deals.",
    tools=[FunctionTool(func=map_salesforce_account_to_sap_bp)],
)
'''

_DUPLICATE_ENTRYPOINT_MANIFEST = """version: "0.1"
project:
  name: adk-duplicate-entrypoint
agent:
  name: smart_closer
  declared_purpose:
    - map salesforce records onto sap records
environment:
  target: local
google_adk:
  python_entrypoints:
    - agent.py
    - agent.py
"""


def test_a_repeated_entrypoint_is_reported_in_the_adopters_terms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#321's failure, walked the way an adopter meets it.

    Every string on the path — the console line, the envelope ``message``, the
    ranked ``next_actions[]`` — has to be actionable without the identity
    model, and the identity model has to still be there for a bug report.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "agent.py").write_text(_DUPLICATE_ENTRYPOINT_AGENT, encoding="utf-8")
    (workspace / "shipgate.yaml").write_text(
        _DUPLICATE_ENTRYPOINT_MANIFEST, encoding="utf-8"
    )
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--out",
            str(tmp_path / "reports"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 3, result.output

    console = [
        line
        for line in result.output.splitlines()
        if line and not line.startswith("{")
    ]
    assert console
    for line in console:
        assert internal_vocabulary(line) == (), line

    envelope = json.loads(
        next(line for line in result.output.splitlines() if line.startswith("{"))
    )
    assert envelope["error"] == "input_parse_error"
    for field in (envelope["message"], envelope["next_action"]):
        assert internal_vocabulary(field) == (), field
    for action in envelope["next_actions"]:
        for text in (action["why"], action["expects"]):
            assert internal_vocabulary(text) == (), text

    # Every adopter-facing failure names a file, symbol, or manifest key.
    assert "agent.py" in envelope["message"]
    assert "map_salesforce_account_to_sap_bp" in envelope["message"]
    assert "shipgate.yaml" in envelope["message"]

    # ...and the identity model is still on the wire, for the bug report the
    # adopter should not have to reverse-engineer from prose.
    details = envelope["details"]
    assert details["failure"] == DUPLICATE_TOOL_IN_SOURCE
    assert details["source_type"] == "google_adk_function"
    assert details["native_locator"] == "agent.py#map_salesforce_account_to_sap_bp"


_DYNAMIC_TOOLKIT_AGENT = '''
from google.adk.agents import LlmAgent

from .toolkit import build_tools

root_agent = LlmAgent(
    name="closer_agent",
    instruction="Route approvals and send confirmations.",
    tools=build_tools(),
)
'''

_DYNAMIC_TOOLKIT_MANIFEST = """version: "0.1"
project:
  name: adk-dynamic-toolkit
agent:
  name: closer_agent
  declared_purpose:
    - route approvals
environment:
  target: local
google_adk:
  python_entrypoints:
    - agent.py
"""


def _git_repo(workspace: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=workspace, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=workspace, check=True
    )
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=workspace, check=True)


def test_the_handoff_and_pr_comment_of_a_real_verdict_are_swept(tmp_path: Path) -> None:
    """The two surfaces #329 names that no static list can vouch for.

    ``agent-handoff.json`` prose and PR comment text are assembled from gap
    rows, source-warning text, and decision reasons — all covered structurally
    above, but only an actual run proves the assembly did not put an internal
    identifier back. A dynamic ADK toolkit is the richest cheap verdict: it
    reaches `insufficient_evidence` with a binding gap, a source warning, and
    a `fix_task` full of instructions.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "agent.py").write_text(_DYNAMIC_TOOLKIT_AGENT, encoding="utf-8")
    (workspace / "shipgate.yaml").write_text(
        _DYNAMIC_TOOLKIT_MANIFEST, encoding="utf-8"
    )
    _git_repo(workspace)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(workspace),
            "--config",
            str(workspace / "shipgate.yaml"),
            "--ci-mode",
            "advisory",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output

    reports = workspace / "agents-shipgate-reports"
    report = json.loads((reports / "report.json").read_text(encoding="utf-8"))
    # The run has to be the interesting one, or this sweeps an empty verdict.
    assert report["release_decision"]["decision"] == "insufficient_evidence"
    assert report["source_warnings"]

    offenders: list[str] = []
    for name in ("agent-handoff.json", "verifier.json", "report.json"):
        payload = json.loads((reports / name).read_text(encoding="utf-8"))
        offenders += [
            f"{name}{where} {internal_vocabulary(text)} :: {text[:200]!r}"
            for where, text in _adopter_strings(payload, "")
            if internal_vocabulary(text)
        ]
    comment = (reports / "pr-comment.md").read_text(encoding="utf-8")
    offenders += [
        f"pr-comment.md:{number} {internal_vocabulary(line)} :: {line.strip()!r}"
        for number, line in enumerate(comment.splitlines(), start=1)
        if internal_vocabulary(line)
    ]
    assert not offenders, "\n".join(offenders)

    # And the reader is named something they can open, not a digest.
    instructions = json.loads(
        (reports / "verifier.json").read_text(encoding="utf-8")
    )["fix_task"]["instructions"]
    assert any("closer_agent" in line for line in instructions), instructions


_DUPLICATE_IN_ARTIFACT_TOOLS = """{"tools": [
  {"name": "pay", "description": "pay an invoice for a customer account"},
  {"name": "pay", "description": "pay an invoice for a customer account"}
]}
"""

_DUPLICATE_IN_ARTIFACT_MANIFEST = """version: "0.1"
project:
  name: dup-mcp
agent:
  name: billing-agent
  declared_purpose:
    - pay invoices
environment:
  target: local
tool_sources:
  - id: billing
    type: mcp
    path: tools.json
"""


def test_a_duplicate_inside_an_artifact_edits_the_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other cause of the same failure, with the opposite repair.

    One `tool_sources` entry whose `tools.json` defines `pay` twice is not a
    repeated manifest entry, and the structured action carries exactly one
    `path`. Naming the manifest here lets a consumer routing on it delete the
    source declaration and lose coverage (#329 review), so the check reports
    which cause it saw and the action follows it.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tools.json").write_text(
        _DUPLICATE_IN_ARTIFACT_TOOLS, encoding="utf-8"
    )
    (workspace / "shipgate.yaml").write_text(
        _DUPLICATE_IN_ARTIFACT_MANIFEST, encoding="utf-8"
    )
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--out",
            str(tmp_path / "reports"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 3, result.output
    envelope = json.loads(
        next(line for line in result.output.splitlines() if line.startswith("{"))
    )
    assert envelope["details"]["cause"] == "duplicate_in_source_artifact"
    (action,) = envelope["next_actions"]
    # No artifact path is published as a routable target: a declared path has
    # no single base — the manifest's for most sources, the entrypoint's for an
    # inventory a framework file mounts — so it named files that did not exist
    # (#329 review 3). The artifact is in the sentence, to grep for.
    assert action["kind"] == "review"
    assert action.get("path") is None
    assert "tools.json" in action["why"]
    assert str(workspace / "shipgate.yaml") in action["why"]
    assert internal_vocabulary(action["why"]) == (), action["why"]
    assert "tools.json" in envelope["message"]


def test_a_nested_manifest_is_the_one_the_edit_action_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--workspace` discovery selects the manifest; the CLI spelling does not.

    Reproducing the repeated entrypoint through `scan --workspace <repo>`
    emitted `next_actions[0].path = "shipgate.yaml"`, so an agent following
    the documented route would edit an unrelated trust root in its own working
    directory (#329 review). The run records the manifest it read.
    """

    repo = tmp_path / "repo"
    nested = repo / "services" / "billing"
    nested.mkdir(parents=True)
    (nested / "agent.py").write_text(_DUPLICATE_ENTRYPOINT_AGENT, encoding="utf-8")
    (nested / "shipgate.yaml").write_text(
        _DUPLICATE_ENTRYPOINT_MANIFEST, encoding="utf-8"
    )
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        ["scan", "--workspace", str(repo), "--out", str(tmp_path / "reports"), "--format", "json"],
    )
    assert result.exit_code == 3, result.output
    envelope = json.loads(
        next(line for line in result.output.splitlines() if line.startswith("{"))
    )
    action = envelope["next_actions"][0]
    assert action["kind"] == "edit"
    assert Path(action["path"]) == nested / "shipgate.yaml"
    assert envelope["details"]["manifest_path"] == str(nested / "shipgate.yaml")


def test_verify_without_a_config_flag_still_names_the_manifest_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`verify` defaults `--config`, so the raw flag is `None` at the handler."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "agent.py").write_text(_DUPLICATE_ENTRYPOINT_AGENT, encoding="utf-8")
    (workspace / "shipgate.yaml").write_text(
        _DUPLICATE_ENTRYPOINT_MANIFEST, encoding="utf-8"
    )
    _git_repo(workspace)
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(workspace), "--ci-mode", "advisory", "--format", "json"],
    )
    assert result.exit_code == 3, result.output
    envelope = json.loads(
        next(line for line in result.output.splitlines() if line.startswith("{"))
    )
    action = envelope["next_actions"][0]
    assert Path(action["path"]) == workspace / "shipgate.yaml"


_OPENAI_TOOLS = (
    '[{"type": "function", "function": {"name": "pay", "description": '
    '"pay an invoice for a customer account", "parameters": {"type": "object", '
    '"properties": {}}}}]\n'
)

_OPENAI_REPEATED_MANIFEST = """version: "0.1"
project:
  name: oai-dup
agent:
  name: billing-agent
  declared_purpose:
    - pay invoices
environment:
  target: local
openai_api:
  tools:
    - tools.json
    - tools.json
"""


def test_an_aggregating_loader_still_reports_a_repeated_manifest_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`read_index` identifies the adapter batch, not the artifact read.

    The OpenAI and Anthropic loaders fold every configured artifact into one
    `LoadedToolSource`, so listing one file twice collides *inside* one read
    and was reported as a duplicate definition — sending the reader to edit a
    perfectly valid JSON file (#329 review 2). The manifest settles it.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tools.json").write_text(_OPENAI_TOOLS, encoding="utf-8")
    (workspace / "shipgate.yaml").write_text(
        _OPENAI_REPEATED_MANIFEST, encoding="utf-8"
    )
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--out",
            str(tmp_path / "reports"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 3, result.output
    envelope = json.loads(
        next(line for line in result.output.splitlines() if line.startswith("{"))
    )
    assert envelope["details"]["cause"] == "repeated_source_entry"
    (action,) = envelope["next_actions"]
    assert Path(action["path"]) == workspace / "shipgate.yaml"


def test_an_archived_head_names_the_checkout_manifest_not_the_temporary_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`verify --base/--head` scans a temporary archive that it then deletes.

    `run_scan` records the manifest it read, which there is a path inside that
    archive — so the recovery named a file that no longer existed by the time
    anyone read it (#329 review 2).
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "agent.py").write_text(_DUPLICATE_ENTRYPOINT_AGENT, encoding="utf-8")
    (workspace / "shipgate.yaml").write_text(
        _DUPLICATE_ENTRYPOINT_MANIFEST, encoding="utf-8"
    )
    _git_repo(workspace)
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(workspace),
            "--config",
            str(workspace / "shipgate.yaml"),
            "--base",
            "HEAD",
            "--head",
            "HEAD",
            "--ci-mode",
            "advisory",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 3, result.output
    envelope = json.loads(
        next(line for line in result.output.splitlines() if line.startswith("{"))
    )
    published = Path(envelope["next_actions"][0]["path"])
    assert published == workspace / "shipgate.yaml"
    assert published.is_file(), "the emitted recovery names a file that still exists"
    assert Path(envelope["details"]["manifest_path"]).is_file()


def test_verify_routes_the_same_failure_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`verify` is the command an adopter runs (#327), not `scan`.

    The two caught this exception separately and wrote separate recoveries, so
    the precise route existed on one command and not the other. One resolver,
    asserted from both ends.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "agent.py").write_text(_DUPLICATE_ENTRYPOINT_AGENT, encoding="utf-8")
    (workspace / "shipgate.yaml").write_text(
        _DUPLICATE_ENTRYPOINT_MANIFEST, encoding="utf-8"
    )
    # `verify` reads a committed worktree; without a checkout it stops at a
    # config error long before the failure under test.
    _git_repo(workspace)
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(workspace),
            "--config",
            str(workspace / "shipgate.yaml"),
            "--ci-mode",
            "advisory",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 3, result.output

    envelope = json.loads(
        next(line for line in result.output.splitlines() if line.startswith("{"))
    )
    assert envelope["error"] == "input_parse_error"
    assert internal_vocabulary(envelope["message"]) == (), envelope["message"]
    (action,) = envelope["next_actions"]
    assert action["kind"] == "edit"
    assert "map_salesforce_account_to_sap_bp" in action["why"]
    assert internal_vocabulary(action["why"]) == (), action["why"]
    assert envelope["details"]["failure"] == DUPLICATE_TOOL_IN_SOURCE
