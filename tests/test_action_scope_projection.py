"""One action, one permission list — and the route that proves it.

An action's permissions were spelled twice. The action lens took the row's
``scopes:`` list when it had one and the source's auth scopes otherwise;
``_assess_authority`` took the row's list only where a *reviewed* authority
record existed. The two rules agree only by luck, and
``CapabilityFactV1._semantic_projection_is_consistent`` *requires* them to
agree — so on the shapes where they disagreed, rebuilding a capability fact
from a serialized ``ActionFact`` raised, and a legal manifest turned
``verify --base`` into ``internal_error`` (exit 4).

Declaring authority once per source (#410 increment 3) closed the reviewed
half by normalizing both manifest sites into one record. What was left is
every row that declares scopes with no reviewed authority at either site: ten
of the twenty-eight shapes swept below raised until one resolver
(``resolve_action_scopes``) decided both.

Two things keep the whole class closed:

* the sweep below, which walks every combination of source authority and
  declaration row and asserts the two spellings resolve to one list — so
  neither half can reopen without a failing test.
* one real ``verify --base`` run. The invariant is enforced on exactly one
  route: ``checks/mcp_permissions.py`` reaches
  ``capability_fact_from_action_fact`` only when a base diff reference is
  available. A plain ``scan`` never touches it, so no sample golden and no
  scan-level test can see this class — it surfaces the first time someone
  compares against a base.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capabilities import capability_fact_from_action_fact
from agents_shipgate.core.domain import AuthInfo, Tool, ToolParameter
from agents_shipgate.core.lenses.action_surface import action_to_fact, build_action
from agents_shipgate.core.semantic_assessment import assess_tool_semantics
from agents_shipgate.schemas.manifest import (
    ActionDeclarationConfig,
    AgentsShipgateManifest,
)

runner = CliRunner()


# --- the sweep -------------------------------------------------------------


#: Source authority the tool publishes, by name.
_SOURCES: dict[str, dict | None] = {
    "no source auth": None,
    "source scoped [crm.read]": {"type": "oauth2", "scopes": ["crm.read"]},
    "source scoped [crm.read, crm.write]": {
        "type": "oauth2",
        "scopes": ["crm.read", "crm.write"],
    },
    "source auth without scopes": {"type": "oauth2", "scopes": []},
}

#: ``action_surface.actions[]`` rows, minus the ``tool`` selector. The manifest
#: validator constrains which of these co-occur (``scoped`` requires non-empty
#: ``scopes``; ``none``/``unscoped``/``ambient`` require empty ones), so the
#: rows here are the ones an adopter can actually write.
_ROWS: dict[str, dict] = {
    "no declaration": {},
    "bare scopes, same as source": {"scopes": ["crm.read"]},
    "bare scopes, drops one": {"scopes": ["crm.read"]},
    "bare scopes, adds one": {"scopes": ["crm.read", "crm.admin"]},
    "reviewed scoped authority": {
        "scopes": ["crm.read"],
        "authority": {"mode": "scoped", "auth_type": "oauth2"},
    },
    "reviewed anonymous authority": {"authority": {"mode": "none"}},
    "reviewed unscoped authority": {
        "authority": {
            "mode": "unscoped",
            "auth_type": "oauth2",
            "reason": "reviewed as unscoped",
        }
    },
}


def _tool(auth: dict | None) -> Tool:
    return Tool(
        id="t_read_thing",
        name="read_thing",
        description="Read a CRM record.",
        source_type="mcp",
        source_id="crm",
        annotations={"readOnlyHint": True},
        auth=AuthInfo(**auth) if auth else AuthInfo(),
        parameters=[ToolParameter(name="id", required=True)],
        extraction_confidence="high",
    )


def _manifest() -> AgentsShipgateManifest:
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "action-scope-projection"},
            "agent": {"name": "agent", "declared_purpose": ["publish one scope list"]},
            "environment": {"target": "local"},
            "tool_sources": [{"id": "crm", "type": "mcp", "path": "tools.json"}],
        }
    )


def test_the_action_and_its_authority_publish_one_permission_list() -> None:
    """``required_scopes`` and ``authority.scopes`` are one list, every shape.

    The assertion that matters is the third one: building the capability fact
    from the serialized ``ActionFact`` is what ``verify --base`` does, and it
    is what raised.
    """

    manifest = _manifest()
    shapes = 0
    declared_wins = 0  # the row's list replaced a different source list
    source_stands = 0  # no row list, so the source's own scopes stand
    for source_name, auth in _SOURCES.items():
        for row_name, row in _ROWS.items():
            shapes += 1
            declaration = ActionDeclarationConfig.model_validate(
                {"tool": "read_thing", "source_id": "crm", **row}
            )
            tool = _tool(auth)
            tool.semantic_assessment = assess_tool_semantics(tool, declaration)
            action = build_action(
                manifest,
                agent_id="agent",
                tool=tool,
                declaration=declaration,
            )
            fact = action_to_fact(action)
            where = f"{source_name} + {row_name}"

            required = sorted(set(fact.required_scopes))
            assert fact.semantic_assessment is not None, where
            published = sorted(set(fact.semantic_assessment.authority.scopes))
            assert required == published, (
                f"{where}: the action requires {required} while its authority "
                f"publishes {published} — two permission lists on one action"
            )
            # The call `verify --base` makes. It validates the projection.
            capability = capability_fact_from_action_fact(fact)
            assert sorted(capability.authority.scopes) == required, where

            source_scopes = sorted({scope for scope in (auth or {}).get("scopes", [])})
            if row.get("scopes"):
                if sorted(set(row["scopes"])) != source_scopes:
                    declared_wins += 1
            elif source_scopes:
                source_stands += 1

    # A sweep that never exercises either resolution branch would pass while
    # asserting nothing, so both are counted rather than assumed.
    assert shapes == len(_SOURCES) * len(_ROWS)
    assert declared_wins >= 6, declared_wins
    assert source_stands >= 4, source_stands


# --- the guard the shared list makes necessary -----------------------------


def test_declared_scopes_cannot_silently_drop_a_source_proven_scope() -> None:
    """A row's list replaces the source's, so narrowing it has to be visible.

    Publishing the row's list on the authority dimension is what closes the
    divergence — and it is also what would let ``scopes: [crm.read]`` erase a
    ``crm.write`` grant the source proves, with a ``structural`` status and no
    issue raised. The reviewed ``authority:`` route has always refused that
    (``_authority_declaration_conflicts``); the subset rule now reads the
    *resolved* list, so a bare one goes through it too.
    """

    tool = _tool({"type": "oauth2", "scopes": ["crm.read", "crm.write"]})
    narrowing = ActionDeclarationConfig.model_validate(
        {"tool": "read_thing", "source_id": "crm", "scopes": ["crm.read"]}
    )

    narrowed = assess_tool_semantics(tool, narrowing)

    assert narrowed.authority.status == "conflicting"
    assert narrowed.authority.mode == "unknown"
    assert [issue.kind for issue in narrowed.authority.issues] == [
        "conflicting_authority_evidence"
    ]
    assert not narrowed.pass_eligible
    # The dropped scope stays readable: the source's own claim carries it.
    source_claims = [
        claim for claim in narrowed.authority.claims if claim.basis == "protocol_structure"
    ]
    assert source_claims and source_claims[0].evidence["scopes"] == ["crm.read", "crm.write"]

    # And the conflict is answerable by the row that raised it — otherwise it
    # would be a wall, not a question.
    restored = assess_tool_semantics(
        tool,
        ActionDeclarationConfig.model_validate(
            {
                "tool": "read_thing",
                "source_id": "crm",
                "scopes": ["crm.read", "crm.write"],
            }
        ),
    )
    assert restored.authority.status == "structural"
    assert not restored.authority.issues


def test_a_broader_declared_scope_list_is_a_broadening_not_a_conflict() -> None:
    """Adding scopes is an explicit expansion the broad-scope policies see."""

    tool = _tool({"type": "oauth2", "scopes": ["crm.read"]})

    assessed = assess_tool_semantics(
        tool,
        ActionDeclarationConfig.model_validate(
            {"tool": "read_thing", "source_id": "crm", "scopes": ["crm.read", "crm.admin"]}
        ),
    )

    assert assessed.authority.status == "structural"
    assert not assessed.authority.issues
    assert list(assessed.authority.scopes) == ["crm.admin", "crm.read"]


# --- the route that only a base comparison reaches -------------------------


def _repo_declaring_only_scopes(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n", encoding="utf-8")
    (repo / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "read_thing",
                        "description": "Read a CRM record.",
                        "annotations": {"readOnlyHint": True},
                        "inputSchema": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    # The reported shape: a row that lists `scopes:` and nothing else. No
    # `authority:` block, so before the fix the authority dimension published
    # an empty list while the action required `crm.read`.
    (repo / "shipgate.yaml").write_text(
        """
version: "0.1"
project: {name: scopes-only-row}
agent:
  name: crm-agent
  declared_purpose: [read crm records]
environment: {target: local}
tool_sources:
  - id: crm
    type: mcp
    path: tools.json
action_surface:
  actions:
    - tool: read_thing
      source_id: crm
      scopes: [crm.read]
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tool: read_thing, source_id: crm}]
      handoffs: []
      reason: reviewed scopes-only fixture binding
""".lstrip(),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    (repo / "NOTES.md").write_text("second commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "head"], cwd=repo, check=True)
    return repo


def test_a_row_that_declares_only_scopes_survives_verify_base(tmp_path: Path) -> None:
    """The user-visible failure: exit 4, ``internal_error``, on a legal manifest."""

    repo = _repo_declaring_only_scopes(tmp_path)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "HEAD~1",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload.get("error") != "internal_error", payload
    assert payload["base_status"] == "succeeded"

    # Non-vacuity: the crash lives in the MCP capability comparison, which runs
    # only over MCP actions present on *both* sides. A fixture whose action
    # surface came back empty would pass this test while proving nothing.
    base_report = json.loads(
        (repo / "agents-shipgate-reports" / "verification-base-report.json").read_text(
            encoding="utf-8"
        )
    )
    head_report = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text(encoding="utf-8")
    )
    for report in (base_report, head_report):
        [action] = report["action_surface_facts"]["actions"]
        assert action["source_type"] == "mcp"
        assert action["required_scopes"] == ["crm.read"]
        assert action["semantic_assessment"]["authority"]["scopes"] == ["crm.read"]
