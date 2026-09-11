"""Google ADK MCP endpoint / credential-reference / filter changes (#538).

Before this, the ADK reader discarded a remote ``McpToolset``'s connection
before anything could compare it: two workspaces differing only in the
endpoint literal and the ``os.environ[...]`` key produced byte-identical
reports. These tests pin the reproduction and every claim made about the fix.

Every fixture below carries a module-level ``raise RuntimeError`` so a run
that ever imported the target code would fail loudly rather than pass quietly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.remote_bindings import (
    CREDENTIAL_REFS_KIND,
    ENDPOINT_KIND,
    LIST_ABSENT,
    LIST_OPAQUE,
    LIST_VALUES,
    TOOL_FILTER_KIND,
    TRANSPORT_KIND,
    decode_list,
    decode_list_summary,
    decode_policy_key,
    encode_list,
    policy_key_for,
    remote_binding_facts,
)
from agents_shipgate.schemas.report import ReadinessReport

MANIFEST = """
version: "0.1"
project:
  name: adk-remote-binding
agent:
  name: search-agent
  declared_purpose:
    - search a remote knowledge base
environment:
  target: production_like
tool_sources:
  - id: adk_search
    type: google_adk
    path: agent.py
"""

# The exact shape issue #538 reproduces on: a remote MCP toolset bound to an
# ADK agent, with a literal endpoint, an environment credential reference and
# a literal tool filter.
BASE_AGENT = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")

search_agent = LlmAgent(
    name="search_agent",
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://readonly.example/mcp",
                headers={"Authorization": os.environ["READ_KEY"]},
            ),
            tool_filter=["search"],
        )
    ],
)
'''


def _write(root: Path, agent_source: str, *, manifest: str = MANIFEST) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "agent.py").write_text(agent_source, encoding="utf-8")
    (root / "shipgate.yaml").write_text(manifest, encoding="utf-8")
    return root


def _scan(project: Path, *, base_report: Path | None = None) -> ReadinessReport:
    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=project / "reports",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=base_report,
        packet_enabled=False,
    )
    return report


def _compare(
    tmp_path: Path,
    base_source: str,
    head_source: str,
    *,
    base_manifest: str = MANIFEST,
    head_manifest: str = MANIFEST,
) -> ReadinessReport:
    """Scan a base workspace, then a head workspace against it."""

    base = _write(tmp_path / "base", base_source, manifest=base_manifest)
    _scan(base)
    head = _write(tmp_path / "head", head_source, manifest=head_manifest)
    return _scan(head, base_report=base / "reports" / "report.json")


def _members(report: ReadinessReport) -> list:
    change = report.capability_change
    assert change is not None
    return [*change.added, *change.broadened, *change.narrowed, *change.removed]


def _remote_members(report: ReadinessReport) -> list:
    return [
        member
        for member in _members(report)
        if "MCP binding" in (member.scope or "")
    ]


def _binding_facts(report: ReadinessReport) -> dict[tuple[str, str], object]:
    return {
        (fact.kind, fact.key): fact
        for fact in report.tool_surface_facts.policies
        if fact.kind.startswith("remote_binding.")
    }


# --- the reproduction -------------------------------------------------------


def test_endpoint_and_credential_change_reach_the_capability_delta(tmp_path):
    """The issue's own reproduction: endpoint + credential reference change.

    Base and head differ only in the URL literal and the environment variable
    name; every source line is preserved. Both must be named, with base and
    head evidence, in the reviewer-facing capability delta.
    """

    head_source = BASE_AGENT.replace(
        "https://readonly.example/mcp", "https://admin.example/mcp"
    ).replace("READ_KEY", "ADMIN_KEY")
    report = _compare(tmp_path, BASE_AGENT, head_source)

    by_scope = {member.scope: member for member in _remote_members(report)}
    endpoint = by_scope["search_agent [adk_search] -> MCP binding #1 endpoint"]
    assert endpoint.before_scope == "https://readonly.example/mcp"
    assert endpoint.after_scope == "https://admin.example/mcp"

    credential = by_scope["search_agent [adk_search] -> MCP binding #1 credential reference"]
    assert credential.before_scope == "headers.Authorization=READ_KEY"
    assert credential.after_scope == "headers.Authorization=ADMIN_KEY"

    # Two axes changed, and only those two.
    assert set(by_scope) == {
        "search_agent [adk_search] -> MCP binding #1 endpoint",
        "search_agent [adk_search] -> MCP binding #1 credential reference",
    }


def test_a_changed_endpoint_claims_no_direction_and_no_risk(tmp_path):
    """``ADMIN_KEY`` proves no privilege level, and nothing may read one in.

    The member says in words that the direction is not established, carries no
    risk tag, and names no tool — a remote leaf the scan never saw must not be
    invented to hang the change on.
    """

    head_source = BASE_AGENT.replace(
        "https://readonly.example/mcp", "https://admin.example/mcp"
    ).replace("READ_KEY", "ADMIN_KEY")
    report = _compare(tmp_path, BASE_AGENT, head_source)

    for member in _remote_members(report):
        assert member.risk_tags == []
        assert member.tool == ""
        assert member.confidence == "medium"
        assert "direction of change is not established" in member.rationale
    assert report.tool_catalog == []


def test_endpoint_only_change_leaves_the_other_axes_alone(tmp_path):
    head_source = BASE_AGENT.replace(
        "https://readonly.example/mcp", "https://admin.example/mcp"
    )
    report = _compare(tmp_path, BASE_AGENT, head_source)

    scopes = {member.scope for member in _remote_members(report)}
    assert scopes == {"search_agent [adk_search] -> MCP binding #1 endpoint"}


def test_credential_reference_only_change_is_named(tmp_path):
    head_source = BASE_AGENT.replace("READ_KEY", "ADMIN_KEY")
    report = _compare(tmp_path, BASE_AGENT, head_source)

    members = _remote_members(report)
    assert [member.scope for member in members] == [
        "search_agent [adk_search] -> MCP binding #1 credential reference"
    ]
    assert members[0].after_scope == "headers.Authorization=ADMIN_KEY"


# --- filter direction -------------------------------------------------------


def test_filter_expansion_is_a_broadening_and_names_what_was_gained(tmp_path):
    head_source = BASE_AGENT.replace(
        'tool_filter=["search"]', 'tool_filter=["search", "delete_index"]'
    )
    report = _compare(tmp_path, BASE_AGENT, head_source)

    change = report.capability_change
    assert change is not None
    broadened = [m for m in change.broadened if "MCP binding" in (m.scope or "")]
    assert len(broadened) == 1
    assert broadened[0].scope == "search_agent [adk_search] -> MCP binding #1 tool filter"
    assert "delete_index" in broadened[0].rationale
    assert broadened[0].confidence == "high"
    assert not [m for m in change.narrowed if "MCP binding" in (m.scope or "")]


def test_filter_contraction_is_a_narrowing(tmp_path):
    base_source = BASE_AGENT.replace(
        'tool_filter=["search"]', 'tool_filter=["search", "delete_index"]'
    )
    report = _compare(tmp_path, base_source, BASE_AGENT)

    change = report.capability_change
    assert change is not None
    narrowed = [m for m in change.narrowed if "MCP binding" in (m.scope or "")]
    assert len(narrowed) == 1
    assert "delete_index" in narrowed[0].rationale
    assert not [m for m in change.broadened if "MCP binding" in (m.scope or "")]


def test_filter_becoming_unreadable_claims_no_direction(tmp_path):
    """A literal filter replaced by a dynamic one is a state change.

    Set membership cannot be compared across it, so the projection must not
    call it a widening or a narrowing.
    """

    head_source = BASE_AGENT.replace(
        'tool_filter=["search"]', "tool_filter=choose_filter()"
    )
    report = _compare(tmp_path, BASE_AGENT, head_source)

    members = [
        m for m in _remote_members(report) if m.scope.endswith("tool filter")
    ]
    assert len(members) == 1
    assert members[0].direction == "broadened"
    assert members[0].after_scope == "(unresolved)"
    assert "direction of change is not established" in members[0].rationale
    # And the reader says why it could not read it.
    evidence = _finding_evidence(report, "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE")
    assert evidence["tool_filter_status"] == "unresolved"
    assert "dynamic_tool_filter" in evidence["limitations"]


# --- no delta where there is no change --------------------------------------


def test_line_movement_formatting_and_comments_produce_no_delta(tmp_path):
    head_source = '''
# A comment that did not exist before.
import os


from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")


# Another comment, and a reflowed constructor call.
search_agent = LlmAgent(
    name="search_agent",
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://readonly.example/mcp", headers={"Authorization": os.environ["READ_KEY"]}
            ),
            tool_filter=[
                "search",
            ],
        )
    ],
)
'''
    report = _compare(tmp_path, BASE_AGENT, head_source)

    assert _remote_members(report) == []
    assert [
        drift
        for drift in report.tool_surface_diff.policy_drift
        if drift.policy_kind.startswith("remote_binding.")
    ] == []


def test_filter_removed_entirely_is_a_broadening(tmp_path):
    """No filter is the widest state, not an empty set."""

    head_source = BASE_AGENT.replace('            tool_filter=["search"],\n', "")
    assert "tool_filter" not in head_source
    report = _compare(tmp_path, BASE_AGENT, head_source)

    change = report.capability_change
    assert change is not None
    broadened = [
        m for m in change.broadened if (m.scope or "").endswith("tool filter")
    ]
    assert len(broadened) == 1
    assert "every tool this endpoint advertises" in broadened[0].rationale
    assert broadened[0].after_scope == "(absent)"


def test_filter_added_where_there_was_none_is_a_narrowing(tmp_path):
    base_source = BASE_AGENT.replace('            tool_filter=["search"],\n', "")
    report = _compare(tmp_path, base_source, BASE_AGENT)

    change = report.capability_change
    assert change is not None
    narrowed = [m for m in change.narrowed if (m.scope or "").endswith("tool filter")]
    assert len(narrowed) == 1
    assert "now limited to search" in narrowed[0].rationale


VARIABLE_TOOLSET = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")

search_tools = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://readonly.example/mcp",
        headers={"Authorization": os.environ["READ_KEY"]},
    ),
    tool_filter=["search"],
)
search_agent = LlmAgent(name="search_agent", tools=[search_tools])
'''


def test_renaming_the_toolset_variable_shows_as_a_replacement_not_a_silence(
    tmp_path,
):
    """A renamed binding is reported as removed plus added, with both sides.

    The slot is part of the binding's identity and the diff is a set
    comparison, so a rename is not paired across. That is deliberate: the pair
    still carries the old endpoint on the removed member and the new one on the
    added member, so a rename can never *hide* a connection change — it only
    declines to call the two the same binding.
    """

    head_source = VARIABLE_TOOLSET.replace("search_tools", "knowledge_tools").replace(
        "https://readonly.example/mcp", "https://admin.example/mcp"
    )
    report = _compare(tmp_path, VARIABLE_TOOLSET, head_source)

    change = report.capability_change
    assert change is not None
    removed = [m for m in change.removed if "MCP binding" in (m.scope or "")]
    added = [m for m in change.added if "MCP binding" in (m.scope or "")]
    assert [m.scope for m in removed] == ["search_agent [adk_search] -> MCP binding search_tools"]
    assert [m.scope for m in added] == ["search_agent [adk_search] -> MCP binding knowledge_tools"]
    assert removed[0].before_scope == "https://readonly.example/mcp"
    assert added[0].after_scope == "https://admin.example/mcp"


# --- attribution ------------------------------------------------------------


TWO_AGENTS = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")

alpha = LlmAgent(
    name="alpha",
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://shared.example/mcp",
                headers={"Authorization": os.environ["ALPHA_KEY"]},
            ),
            tool_filter=["read"],
        )
    ],
)
beta = LlmAgent(
    name="beta",
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://shared.example/mcp",
                headers={"Authorization": os.environ["BETA_KEY"]},
            ),
            tool_filter=["read"],
        )
    ],
)
'''


def test_two_agents_on_one_endpoint_keep_distinct_attribution(tmp_path):
    """Only the agent whose binding moved is named."""

    head_source = TWO_AGENTS.replace(
        """            connection_params=StreamableHTTPConnectionParams(
                url="https://shared.example/mcp",
                headers={"Authorization": os.environ["BETA_KEY"]},
            ),""",
        """            connection_params=StreamableHTTPConnectionParams(
                url="https://elevated.example/mcp",
                headers={"Authorization": os.environ["BETA_KEY"]},
            ),""",
    )
    assert head_source != TWO_AGENTS
    report = _compare(tmp_path, TWO_AGENTS, head_source)

    scopes = {member.scope for member in _remote_members(report)}
    assert scopes == {"beta [adk_search] -> MCP binding #1 endpoint"}


SHARED_TOOLSET = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")

shared_tools = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://shared.example/mcp",
        headers={"Authorization": os.environ["SHARED_KEY"]},
    ),
    tool_filter=["read"],
)
alpha = LlmAgent(name="alpha", tools=[shared_tools])
beta = LlmAgent(name="beta", tools=[shared_tools])
'''


def test_one_shared_toolset_is_attributed_to_every_binding_agent(tmp_path):
    project = _write(tmp_path / "shared", SHARED_TOOLSET)
    report = _scan(project)

    keys = {key for _kind, key in _binding_facts(report)}
    assert keys == {
        "alpha:adk_search:shared_tools",
        "beta:adk_search:shared_tools",
    }


def test_one_agent_dropping_a_shared_toolset_is_removed_for_that_agent_only(
    tmp_path,
):
    head_source = SHARED_TOOLSET.replace(
        'beta = LlmAgent(name="beta", tools=[shared_tools])',
        'beta = LlmAgent(name="beta", tools=[])',
    )
    report = _compare(tmp_path, SHARED_TOOLSET, head_source)

    change = report.capability_change
    assert change is not None
    removed = [m for m in change.removed if "MCP binding" in (m.scope or "")]
    # One member for the whole binding, not one per axis.
    assert [m.scope for m in removed] == ["beta [adk_search] -> MCP binding shared_tools"]
    assert "beta" in removed[0].rationale


TWO_INLINE_BINDINGS = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")

search_agent = LlmAgent(
    name="search_agent",
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://first.example/mcp",
            ),
        ),
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://second.example/mcp",
            ),
        ),
    ],
)
'''


def test_two_inline_bindings_on_one_agent_do_not_collapse(tmp_path):
    """Both bindings keep their own member.

    The capability member's id is hashed from its subject string, so a subject
    that omitted the ordinal slot deduplicated the two into one and discarded
    the second binding's evidence entirely.
    """

    head_source = TWO_INLINE_BINDINGS.replace(
        "https://first.example/mcp", "https://first-moved.example/mcp"
    ).replace("https://second.example/mcp", "https://second-moved.example/mcp")
    report = _compare(tmp_path, TWO_INLINE_BINDINGS, head_source)

    endpoint_members = [
        m for m in _remote_members(report) if (m.scope or "").endswith("endpoint")
    ]
    assert {m.scope for m in endpoint_members} == {
        "search_agent [adk_search] -> MCP binding #1 endpoint",
        "search_agent [adk_search] -> MCP binding #2 endpoint",
    }
    assert {m.before_scope for m in endpoint_members} == {
        "https://first.example/mcp",
        "https://second.example/mcp",
    }
    assert len({m.id for m in endpoint_members}) == 2


def test_a_new_binding_is_one_added_member_not_one_per_axis(tmp_path):
    base_source = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

raise RuntimeError("must never execute")


def lookup(case_id: str) -> dict:
    """Look up one case."""
    return {"case_id": case_id}


search_agent = LlmAgent(name="search_agent", tools=[FunctionTool(func=lookup)])
'''
    report = _compare(tmp_path, base_source, BASE_AGENT)

    change = report.capability_change
    assert change is not None
    added = [m for m in change.added if "MCP binding" in (m.scope or "")]
    assert [m.scope for m in added] == ["search_agent [adk_search] -> MCP binding #1"]
    assert added[0].after_scope == "https://readonly.example/mcp"
    assert "gained a remote MCP binding" in added[0].rationale


# --- limitations ------------------------------------------------------------


def _finding_evidence(report: ReadinessReport, check_id: str) -> dict:
    finding = next(f for f in report.findings if f.check_id == check_id)
    return finding.evidence["toolset"]


DYNAMIC_URL = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")

search_agent = LlmAgent(
    name="search_agent",
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(url=build_url()),
        )
    ],
)
'''


def test_a_dynamic_endpoint_is_unresolved_and_says_so(tmp_path):
    project = _write(tmp_path / "dynamic", DYNAMIC_URL)
    report = _scan(project)

    evidence = _finding_evidence(report, "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE")
    assert evidence["endpoint_status"] == "unresolved"
    assert "endpoint" not in evidence
    assert evidence["limitations"] == ["dynamic_endpoint_expression"]


REBOUND_PARAMS = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")

params = StreamableHTTPConnectionParams(url="https://one.example/mcp")
params = StreamableHTTPConnectionParams(url="https://two.example/mcp")

search_agent = LlmAgent(
    name="search_agent",
    tools=[McpToolset(connection_params=params)],
)
'''


def test_a_rebound_connection_reference_never_guesses_which_one_won(tmp_path):
    project = _write(tmp_path / "rebound", REBOUND_PARAMS)
    report = _scan(project)

    evidence = _finding_evidence(report, "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE")
    assert evidence["endpoint_status"] == "unresolved"
    assert evidence["limitations"] == ["rebound_connection_reference"]
    body = json.dumps(report.model_dump(mode="json"))
    assert "one.example" not in body
    assert "two.example" not in body


SHADOWED_CONSTRUCTOR = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")

StreamableHTTPConnectionParams = local_factory

search_agent = LlmAgent(
    name="search_agent",
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://x.example/mcp",
                headers={"Authorization": os.environ["K"]},
            ),
        )
    ],
)
'''


def test_a_shadowed_connection_constructor_drops_only_the_claim_it_carries(
    tmp_path,
):
    """The transport is the only axis the constructor's identity establishes.

    The endpoint and the credential reference are read from literal arguments
    and survive with the limitation recorded — discarding them would destroy
    exactly the evidence this reader exists to preserve.
    """

    project = _write(tmp_path / "shadowed", SHADOWED_CONSTRUCTOR)
    report = _scan(project)

    evidence = _finding_evidence(report, "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE")
    assert evidence["transport_status"] == "unresolved"
    assert "transport" not in evidence
    assert evidence["endpoint"] == "https://x.example/mcp"
    assert evidence["limitations"] == ["shadowed_connection_constructor"]


def test_a_missing_base_is_a_disabled_delta_not_an_empty_one(tmp_path):
    """No base means no comparison, and the block says so rather than
    reporting "nothing changed"."""

    project = _write(tmp_path / "nobase", BASE_AGENT)
    report = _scan(project)

    change = report.capability_change
    assert change is not None
    assert change.enabled is False
    assert not report.tool_surface_diff.enabled
    # The head-side facts are still carried, so the next run has a base.
    assert _binding_facts(report)


def test_a_parse_failure_fabricates_no_binding(tmp_path):
    """An unreadable entrypoint stays an input error, not an empty answer."""

    project = tmp_path / "broken"
    project.mkdir()
    (project / "agent.py").write_text("def broken(:\n", encoding="utf-8")
    (project / "shipgate.yaml").write_text(MANIFEST, encoding="utf-8")

    with pytest.raises(InputParseError) as excinfo:
        _scan(project)
    assert "agent.py" in str(excinfo.value)


# --- leaf coverage stays a separate claim -----------------------------------


INVENTORY = json.dumps(
    {
        "tools": [
            {
                "name": "search",
                "description": "Search the knowledge base.",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
    }
)

INVENTORY_MANIFEST = MANIFEST + """
google_adk:
  tool_inventories:
    - path: inventories/mcp-tools.json
      source_id: adk_search
"""


def test_supplying_leaf_inventory_cannot_clear_the_endpoint_delta(tmp_path):
    """Leaf coverage and connection identity are separate claims.

    A head that both changes the endpoint *and* supplies the inventory the
    scan asked for must still name the endpoint change: an answer to one
    question is not an answer to the other.
    """

    head_source = BASE_AGENT.replace(
        "https://readonly.example/mcp", "https://admin.example/mcp"
    ).replace(
        "            tool_filter=[\"search\"],",
        "            tool_filter=[\"search\"],\n"
        '            inventory_path="inventories/mcp-tools.json",',
    )
    assert "inventory_path" in head_source

    base = _write(tmp_path / "base", BASE_AGENT)
    _scan(base)
    head = _write(tmp_path / "head", head_source, manifest=INVENTORY_MANIFEST)
    (head / "inventories").mkdir()
    (head / "inventories" / "mcp-tools.json").write_text(INVENTORY, encoding="utf-8")
    report = _scan(head, base_report=base / "reports" / "report.json")

    # The leaves are now enumerated...
    assert any(tool["name"] == "search" for tool in report.tool_catalog)
    # ...and the endpoint change is still named.
    scopes = {member.scope for member in _remote_members(report)}
    assert "search_agent [adk_search] -> MCP binding #1 endpoint" in scopes


# --- redaction --------------------------------------------------------------


SECRETS_IN_SOURCE = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")

search_agent = LlmAgent(
    name="search_agent",
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://alice:sup3rs3cret@host.example/mcp?api_key=AKIAIOSFODNN7EXAMPLE&page=1",
                headers={
                    "Authorization": "Bearer hunter2hunter2hunter2hunter2",
                    "X-Client": "shipgate",
                },
            ),
        )
    ],
)
'''


def test_literal_credentials_are_withheld_and_the_gap_is_named(tmp_path):
    project = _write(tmp_path / "secrets", SECRETS_IN_SOURCE)
    report = _scan(project)

    body = json.dumps(report.model_dump(mode="json"))
    assert "sup3rs3cret" not in body
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert "hunter2hunter2hunter2hunter2" not in body
    # The host and the non-credential query parameter are ordinary evidence.
    assert "host.example" in body
    assert "page=1" in body

    evidence = _finding_evidence(report, "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE")
    assert evidence["credential_status"] == "redacted"
    # The header *name* is published so the credential is locatable and
    # comparable; the value is a marker, never a byte of the secret.
    assert evidence["credential_refs"] == [
        "headers.Authorization=<literal credential withheld>"
    ]
    assert set(evidence["limitations"]) == {
        "endpoint_credentials_redacted",
        "literal_credential_value",
    }


def test_only_reference_names_are_published(tmp_path):
    """An environment reference publishes the variable name, never a value."""

    project = _write(tmp_path / "refs", BASE_AGENT)
    report = _scan(project)

    evidence = _finding_evidence(report, "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE")
    assert evidence["credential_refs"] == ["headers.Authorization=READ_KEY"]
    assert evidence["credential_status"] == "environment_reference"


# --- the unchanged controls -------------------------------------------------


def test_the_release_verdict_and_finding_set_do_not_move(tmp_path):
    """Zero deltas on the unchanged controls.

    The connection facts are evidence, not a second gate: the same workspace
    that was ``insufficient_evidence`` for an unenumerable toolset still is,
    with the same check ids, whether or not the endpoint moved.
    """

    head_source = BASE_AGENT.replace(
        "https://readonly.example/mcp", "https://admin.example/mcp"
    )
    base = _write(tmp_path / "base", BASE_AGENT)
    base_report = _scan(base)
    head = _write(tmp_path / "head", head_source)
    head_report = _scan(head, base_report=base / "reports" / "report.json")

    assert base_report.release_decision.decision == "insufficient_evidence"
    assert head_report.release_decision.decision == "insufficient_evidence"
    assert {f.check_id for f in base_report.findings} == {
        f.check_id for f in head_report.findings
    }


# --- review findings, PR #540 -----------------------------------------------


STDIO_NESTED = '''
import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

raise RuntimeError("must never execute")

search_agent = LlmAgent(
    name="search_agent",
    tools=[
        McpToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command="npx",
                    args=["server"],
                    env={"API_KEY": os.environ["READ_KEY"]},
                ),
            ),
        )
    ],
)
'''


def test_stdio_credentials_are_read_from_the_nested_server_params(tmp_path):
    """ADK nests stdio's ``env`` under ``server_params``.

    Reading only the outer call reported ``credential_status: "absent"`` — a
    false claim of absence — and a changed credential reference then produced
    no delta at all (PR #540 review).
    """

    project = _write(tmp_path / "stdio", STDIO_NESTED)
    report = _scan(project)

    evidence = _finding_evidence(report, "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE")
    assert evidence["credential_status"] == "environment_reference"
    assert evidence["credential_refs"] == ["server_params.env.API_KEY=READ_KEY"]


def test_a_changed_nested_stdio_credential_reference_is_named(tmp_path):
    head_source = STDIO_NESTED.replace("READ_KEY", "ADMIN_KEY")
    report = _compare(tmp_path, STDIO_NESTED, head_source)

    members = [
        m for m in _remote_members(report) if (m.scope or "").endswith("credential reference")
    ]
    assert len(members) == 1
    assert members[0].before_scope == "server_params.env.API_KEY=READ_KEY"
    assert members[0].after_scope == "server_params.env.API_KEY=ADMIN_KEY"


def test_unreadable_nested_server_params_are_unresolved_not_absent(tmp_path):
    source = STDIO_NESTED.replace(
        """                server_params=StdioServerParameters(
                    command="npx",
                    args=["server"],
                    env={"API_KEY": os.environ["READ_KEY"]},
                ),""",
        "                server_params=build_params(),",
    )
    assert "build_params()" in source
    project = _write(tmp_path / "stdio_dynamic", source)
    report = _scan(project)

    evidence = _finding_evidence(report, "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE")
    assert evidence["credential_status"] == "unresolved"
    assert "unresolved_nested_server_params" in evidence["limitations"]


def test_adding_a_hardcoded_credential_beside_a_reference_is_a_delta(tmp_path):
    """A credential being *added*, not a rotation of already-withheld bytes.

    The literal was recorded only as a limitation, which the carried summary
    and hash never saw, so the head read as unchanged (PR #540 review).
    """

    head_source = BASE_AGENT.replace(
        '{"Authorization": os.environ["READ_KEY"]}',
        '{"Authorization": os.environ["READ_KEY"], "api_key": "demo-password"}',
    )
    assert head_source != BASE_AGENT
    report = _compare(tmp_path, BASE_AGENT, head_source)

    members = [
        m for m in _remote_members(report) if (m.scope or "").endswith("credential reference")
    ]
    assert len(members) == 1
    assert members[0].before_scope == "headers.Authorization=READ_KEY"
    assert members[0].after_scope == (
        "headers.Authorization=READ_KEY, headers.api_key=<literal credential withheld>"
    )
    assert "demo-password" not in json.dumps(report.model_dump(mode="json"))


ENCODED_QUERY_SECRET = '''
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

raise RuntimeError("must never execute")

search_agent = LlmAgent(
    name="search_agent",
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://host.example/mcp?api%5Fkey=demo-password&access_token=demo-password&page=1",
            ),
        )
    ],
)
'''


def test_encoded_and_oauth_query_credentials_are_redacted(tmp_path):
    """``api%5Fkey`` decodes to ``api_key``, and ``access_token`` is one too.

    Matching the raw spelling let the first through, and the exact
    sensitive-key vocabulary had never held the second (PR #540 review).
    """

    project = _write(tmp_path / "query", ENCODED_QUERY_SECRET)
    report = _scan(project)

    body = json.dumps(report.model_dump(mode="json"))
    assert "demo-password" not in body
    assert "page=1" in body

    evidence = _finding_evidence(report, "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE")
    assert "demo-password" not in evidence["endpoint"]
    assert "endpoint_credentials_redacted" in evidence["limitations"]


TWO_SOURCE_MANIFEST = """
version: "0.1"
project:
  name: adk-remote-binding
agent:
  name: search-agent
  declared_purpose:
    - search a remote knowledge base
environment:
  target: production_like
tool_sources:
  - id: adk_search
    type: google_adk
    path: agent.py
  - id: adk_other
    type: google_adk
    path: other.py
"""


def _write_two_sources(root, first: str, second: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / "agent.py").write_text(first, encoding="utf-8")
    (root / "other.py").write_text(second, encoding="utf-8")
    (root / "shipgate.yaml").write_text(TWO_SOURCE_MANIFEST, encoding="utf-8")
    return root


def test_two_sources_with_the_same_agent_name_keep_both_capability_members(
    tmp_path,
):
    """The member id is hashed from the subject, so the subject needs the source.

    Two configured ADK files each declaring ``search_agent`` at inline slot
    ``#1`` produced two policy-drift rows and, after member deduplication, one
    member — one source's endpoint change disappeared (PR #540 review).
    """

    other = BASE_AGENT.replace(
        "https://readonly.example/mcp", "https://other-readonly.example/mcp"
    )
    base = _write_two_sources(tmp_path / "base", BASE_AGENT, other)
    _scan(base)
    head = _write_two_sources(
        tmp_path / "head",
        BASE_AGENT.replace("https://readonly.example/mcp", "https://admin.example/mcp"),
        other.replace(
            "https://other-readonly.example/mcp", "https://other-admin.example/mcp"
        ),
    )
    report = _scan(head, base_report=base / "reports" / "report.json")

    endpoints = {
        m.scope: (m.before_scope, m.after_scope)
        for m in _remote_members(report)
        if (m.scope or "").endswith("endpoint")
    }
    assert endpoints == {
        "search_agent [adk_search] -> MCP binding #1 endpoint": (
            "https://readonly.example/mcp",
            "https://admin.example/mcp",
        ),
        "search_agent [adk_other] -> MCP binding #1 endpoint": (
            "https://other-readonly.example/mcp",
            "https://other-admin.example/mcp",
        ),
    }


# --- the carriage codec -----------------------------------------------------


class _Binding:
    """Minimal stand-in with the attributes the codec reads."""

    def __init__(self, agent: str, source_id: str, slot: str) -> None:
        self.agent = agent
        self.source_id = source_id
        self.slot = slot


@pytest.mark.parametrize(
    "agent,source_id,slot",
    [
        ("search_agent", "adk_search", "#1"),
        ("weird:agent", "odd:source", "odd:slot"),
        ("100%", "%3A", "%25"),
        ("", "", ""),
    ],
)
def test_policy_key_round_trips(agent, source_id, slot):
    key = policy_key_for(_Binding(agent, source_id, slot))
    assert decode_policy_key(key) == (agent, source_id, slot)


def test_a_short_or_malformed_key_reads_back_without_raising():
    """A base report is an input; a malformed key must not crash a head scan."""

    assert decode_policy_key("only_agent") == ("only_agent", "", "")
    assert decode_policy_key("") == ("", "", "")


def test_two_sources_declaring_the_same_agent_name_keep_separate_bindings():
    from agents_shipgate.core.domain import AgentRemoteBinding

    facts = remote_binding_facts(
        [
            AgentRemoteBinding(
                agent="root_agent", source_id="one", slot="#1", provider="p"
            ),
            AgentRemoteBinding(
                agent="root_agent", source_id="two", slot="#1", provider="p"
            ),
        ]
    )
    assert {fact.key for fact in facts} == {"root_agent:one:#1", "root_agent:two:#1"}


@pytest.mark.parametrize(
    "values",
    [
        ["search"],
        ["read", "write"],
        ["a, b", "c"],
        ["100%", "%2C"],
        # A value spelled exactly like a status sentinel must not decode as
        # one: the escape on ``(`` is what keeps "the argument is not there"
        # apart from "the argument is there and says this".
        ["(absent)"],
        ["(unresolved)", "read"],
    ],
)
def test_value_list_round_trips(values):
    assert decode_list(encode_list(values)) == sorted(values)


def test_an_empty_value_list_is_not_an_absent_argument():
    """The two are different claims and the codec keeps them apart.

    ``tool_filter=[]`` bounds a binding to no tools at all — the narrowest
    state — while no filter leaves everything the endpoint advertises
    reachable. Folding them together made the narrowest read as the widest.
    """

    assert decode_list_summary("(absent)") == (LIST_ABSENT, [])
    assert decode_list_summary("(unresolved)") == (LIST_OPAQUE, [])
    assert decode_list_summary("read") == (LIST_VALUES, ["read"])


def test_an_empty_filter_is_a_widening_because_adk_ignores_it(tmp_path):
    """``tool_filter=[]`` is no filter at all, not a filter of nothing.

    ADK's ``BaseToolset._is_tool_selected`` returns ``True`` for any falsy
    filter (adk-python 2.8.0), and ``McpToolset.get_tools`` uses that
    predicate, so an empty list exposes every advertised tool. Comparing it as
    the empty *set* reported the widest state as the narrowest (PR #540
    review).
    """

    head_source = BASE_AGENT.replace('tool_filter=["search"]', "tool_filter=[]")
    report = _compare(tmp_path, BASE_AGENT, head_source)

    change = report.capability_change
    assert change is not None
    broadened = [m for m in change.broadened if (m.scope or "").endswith("tool filter")]
    assert len(broadened) == 1
    assert "every tool this endpoint advertises" in broadened[0].rationale
    assert not [m for m in change.narrowed if (m.scope or "").endswith("tool filter")]


def test_adding_an_empty_filter_where_there_was_none_is_no_capability_change(
    tmp_path,
):
    """Different source text, identical authority.

    The policy-drift row still records the edit; the capability block reports
    nothing, because nothing about what the agent may call moved.
    """

    base_source = BASE_AGENT.replace('            tool_filter=["search"],\n', "")
    head_source = BASE_AGENT.replace('tool_filter=["search"]', "tool_filter=[]")
    report = _compare(tmp_path, base_source, head_source)

    assert [
        drift
        for drift in report.tool_surface_diff.policy_drift
        if drift.policy_kind == TOOL_FILTER_KIND
    ]
    assert not [m for m in _remote_members(report) if (m.scope or "").endswith("tool filter")]


def test_every_axis_gets_exactly_one_row_per_binding(tmp_path):
    from agents_shipgate.core.domain import AgentRemoteBinding

    binding = AgentRemoteBinding(
        agent="a",
        source_id="s",
        slot="#1",
        provider="google_adk_mcp",
    )
    facts = remote_binding_facts([binding, binding])
    assert [fact.kind for fact in facts] == sorted(
        [ENDPOINT_KIND, CREDENTIAL_REFS_KIND, TOOL_FILTER_KIND, TRANSPORT_KIND]
    )
    assert {fact.key for fact in facts} == {"a:s:#1"}
