import json
from pathlib import Path

import pytest

from agents_shipgate.checks.manifest_scope import (
    _purpose_is_read_only,
    _tool_matches_prohibited_action,
)
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.domain import Tool


def test_purpose_read_only_detection_uses_tokens_not_substrings():
    assert not _purpose_is_read_only("interview customer support agents")
    assert not _purpose_is_read_only("preview and review generated reports")
    assert _purpose_is_read_only("read-only ticket lookups")


# --- the prohibited-action matcher (#1035) ---------------------------------

DELETE_OR_EXPORT = "delete or export customer data outside a compliance request"


def _tool(name: str, description: str) -> Tool:
    return Tool(
        id=f"tool:{name}",
        name=name,
        description=description,
        source_type="mcp",
        source_id="tools",
        extraction_confidence="high",
    )


@pytest.mark.parametrize(
    ("name", "description"),
    [
        # "or" used to match inside "priority" and "for"; with "customer"
        # that was two overlapping words, and the gap moved `passed` to
        # `insufficient_evidence` on wording alone.
        ("tickets.set_priority", "Set ticket priority for a customer to high or low."),
        ("tickets.set_priority", "Set ticket priority to high or low."),
        # ... and inside "order".
        ("orders.lookup", "Look up an order for a customer."),
        # "or" inside "report" plus a real "export": one shared word, not two.
        ("reports.export", "Export a sales report."),
        # Two shared nouns but no shared action verb: same subject, not the
        # prohibited action.
        ("crm.read_customer_data", "Read customer data for a support ticket."),
    ],
)
def test_word_overlap_without_a_shared_action_verb_does_not_match(name, description):
    assert not _tool_matches_prohibited_action(_tool(name, description), DELETE_OR_EXPORT)


@pytest.mark.parametrize(
    ("name", "description", "prohibited"),
    [
        ("crm.delete_customer_data", "Delete a customer's data.", DELETE_OR_EXPORT),
        # camelCase names and inflections reach the same words.
        ("deleteCustomerData", "Deletes customer records.", DELETE_OR_EXPORT),
        ("crm.export", "Exports customer data as CSV.", DELETE_OR_EXPORT),
        (
            "cancel_shipment",
            "Cancel a pending shipment.",
            "cancel shipments without operations confirmation",
        ),
        ("terminate_account", "Terminate a merchant account.", "terminate the merchant account"),
        (
            "gmail.send_customer_email",
            "Send an email to a customer.",
            "send external customer communications without preview",
        ),
    ],
)
def test_a_shared_write_verb_and_a_second_word_match(name, description, prohibited):
    assert _tool_matches_prohibited_action(_tool(name, description), prohibited)


# --- end to end: a word match is advisory (#1055, Option A) -----------------

_MANIFEST = """version: "0.1"
project: {name: ticket-agent}
agent:
  name: ticket-assistant
  declared_purpose: [triage support tickets]
  prohibited_actions:
    - delete or export customer data outside a compliance request
environment: {target: local}
tool_sources:
  - {id: ticket_tools, type: mcp, path: tools.json}
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tool: %(tool)s, source_id: ticket_tools}]
      handoffs: []
      reason: reviewed local fixture binding
permissions:
  scopes: [tickets:write]
action_surface:
  actions:
    - tool: %(tool)s
      effect: write
      scopes: [tickets:write]
      authority: {mode: scoped, auth_type: oauth2, credential_mode: delegated}
%(checks)s
ci: {mode: advisory}
"""


def _workspace(tmp_path: Path, *, tool: str, description: str, checks: str = "") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "shipgate.yaml").write_text(
        _MANIFEST % {"tool": tool, "checks": checks}, encoding="utf-8"
    )
    (tmp_path / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": tool,
                        "description": description,
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "ticket_id": {"type": "string"},
                                "priority": {"type": "string", "enum": ["high", "low"]},
                            },
                            "required": ["ticket_id", "priority"],
                            "additionalProperties": False,
                        },
                        "auth": {"type": "oauth2", "scopes": ["tickets:write"]},
                        "owner": "support-platform",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return tmp_path / "shipgate.yaml"


def _scan(config: Path, **kwargs):
    report, _ = run_scan(
        config_path=config,
        output_dir=config.parent / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
        **kwargs,
    )
    return report


def test_for_a_customer_no_longer_moves_a_reviewed_write_tool_off_passed(tmp_path):
    """The #1035 reproduction: three words added to a description flipped
    ``passed`` to ``insufficient_evidence``, and nothing could clear it."""

    plain = _scan(
        _workspace(
            tmp_path / "plain",
            tool="tickets.set_priority",
            description="Set ticket priority to high or low.",
        )
    )
    reworded = _scan(
        _workspace(
            tmp_path / "reworded",
            tool="tickets.set_priority",
            description="Set ticket priority for a customer to high or low.",
        )
    )

    assert plain.release_decision.decision == "passed"
    assert reworded.release_decision.decision == "passed"
    assert not any(
        finding.check_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT" for finding in reworded.findings
    )


def test_a_genuine_word_match_is_a_published_advisory_that_raises_no_gap(tmp_path):
    config = _workspace(
        tmp_path / "genuine",
        tool="tickets.purge_customer_data",
        description="Delete customer data attached to a closed ticket.",
    )

    report = _scan(config)

    finding = next(
        item for item in report.findings if item.check_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
    )
    assert finding.blocks_release is False
    assert finding.severity == "medium"
    assert finding.support is not None and finding.support.policy_eligible is False
    assert finding.support.evidence_bases == ["inferred_keyword"]
    assert not any(
        gap.policy_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
        for gap in report.policy_evidence_gaps
    )
    rule = next(
        item
        for item in report.release_decision.contribution_rules
        if item.check_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
    )
    assert (rule.category, rule.rule) == ("excluded", "unsupported_evidence")
    assert finding.requires_human_review is False
    assert finding.agent_action == "informational"

    # A finding, unlike the gap it replaced, can be suppressed with a reason,
    # and --no-heuristics filters it like every other keyword finding.
    ignored = _scan(
        _workspace(
            tmp_path / "ignored",
            tool="tickets.purge_customer_data",
            description="Delete customer data attached to a closed ticket.",
            checks=(
                "checks:\n  ignore:\n"
                "    - check_id: SHIP-SCOPE-PROHIBITED-TOOL-PRESENT\n"
                "      tool: tickets.purge_customer_data\n"
                "      reason: reviewed; purges only closed-ticket attachments\n"
            ),
        )
    )
    assert all(
        item.suppressed
        for item in ignored.findings
        if item.check_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
    )
    filtered = _scan(config, no_heuristics=True)
    assert all(
        item.suppressed
        for item in filtered.findings
        if item.check_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
    )
    assert filtered.release_decision.decision == report.release_decision.decision
