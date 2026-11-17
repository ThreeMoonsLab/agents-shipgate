import json
from pathlib import Path

import pytest

from agents_shipgate.checks.manifest_scope import (
    _purpose_is_read_only,
    _tool_matches_prohibited_action,
)
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.domain import Tool, ToolRiskHint


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
        ("customer.get_data", "Get customer data."),
    ],
)
def test_word_overlap_without_a_shared_action_verb_does_not_match(name, description):
    assert not _tool_matches_prohibited_action(_tool(name, description), DELETE_OR_EXPORT)


@pytest.mark.parametrize(
    ("prohibited", "name", "description"),
    [
        # One shared word, even a verb, is not the prohibited action.
        ("export customer data", "export_records", ""),
        ("send email", "email.read", "Read email."),
        ("send email", "email_search", "Search sent email."),
        # A tool named for the whole safeguard the prohibited action lacks is
        # that safeguard: the preview, not the unpreviewed send.
        ("send external email without preview", "send_email_preview", "Render an email."),
        # Noun-first phrasing: the listed verb is the verb, not the leading
        # noun, so a shared subject alone does not match (#1035 review 2).
        (
            "Customer data must never be exported",
            "tickets.add_comment",
            "Add a comment with customer data.",
        ),
        ("customer data deletion", "update_customer_data", "Update customer data."),
        # A shared verb with no shared object.
        ("make payments", "make_appointment", "Book an appointment."),
        ("send emails to customers", "send_slack_message", "Send a message to the support channel."),
    ],
)
def test_other_non_matches(prohibited, name, description):
    # Known advisory false positives this rule keeps — homographs and a shared
    # generic noun, such as "issue refunds" against ``github.create_issue`` or
    # "close customer accounts" against ``close_ticket`` — are listed in
    # docs/checks.md, not pinned here: recall over precision for an advisory
    # a ``checks.ignore`` entry silences (#1035 review 3).
    assert not _tool_matches_prohibited_action(_tool(name, description), prohibited)


@pytest.mark.parametrize(
    ("prohibited", "name", "description"),
    [
        # Passive, modal and negated phrasing (#1035 review 3, probe5).
        ("Customer data must not be deleted", "crm.delete_customer_data", ""),
        ("Customer records must never be deleted", "delete_customer_record", ""),
        ("Refunds must not be issued without approval", "issue_refund", ""),
        ("The agent must never delete customer records", "delete_customer_record", ""),
        ("Never let the agent send emails to customers", "send_customer_email", ""),
        ("Do not allow deletion of customer data", "crm.delete_customer_data", ""),
        ("Prevent deletion of customer data", "crm.delete_customer_data", ""),
        ("No deletion of customer data", "crm.delete_customer_data", ""),
        ("Emails to customers must not be sent without review", "send_customer_email", ""),
        # The verb is only in the description, and not its first word.
        ("delete customer data", "crm.cleanup", "Permanently delete customer records."),
        ("delete customer records", "crm.cleanup", "Permanently delete customer records."),
        ("delete customer data", "crm.purge", "Delete customer data and records."),
        ("delete customer data", "crm.maintenance", "This tool deletes customer data."),
        ("delete customer data", "crm.maintenance", "Deletes customer data permanently."),
        ("send emails to customers", "notify", "Sends an email to the customer."),
        ("delete customer data", "crm.records", "Use this tool to delete customer data."),
        ("refund payments", "stripe.refund", "Refund a payment."),
        # Noun-first tool names.
        ("delete customer data", "customer_delete", ""),
        ("delete records", "records.purge", ""),
        ("delete records", "records.bulk_delete", ""),
        ("delete customer records", "crm.customer.hard_delete", ""),
        ("issue refunds", "refund_create", ""),
        ("issue refunds", "refunds.create", ""),
        ("issue refunds", "stripe.refunds.create", ""),
        ("cancel subscriptions", "subscription_cancel", ""),
        # Several verbs and objects, adjacent or not.
        ("delete or export customer data or records", "delete_records", ""),
        ("delete or export customer data or records", "export_records", ""),
        ("delete customer data or export it", "export_customer_data", ""),
        ("delete customer data or export records", "export_records", ""),
        ("delete, export or share customer data", "share_customer_data", ""),
        ("delete customer data, or export it to third parties", "export_customer_data", ""),
        # A listed verb used as the object of an unlisted one.
        ("process refunds without approval", "process_refund", ""),
        ("schedule payments", "schedule_payment", ""),
        ("process payments", "process_payment", ""),
        ("start deployments", "start_deployment", ""),
        ("request refunds", "request_refund", ""),
        ("approve refunds", "approve_refund", ""),
        ("export customer data", "export_records", "Export customer records to CSV."),
        ("export customer data", "data_export", "Exports customer data."),
    ],
)
def test_realistic_phrasings_of_a_genuine_prohibited_action_match(prohibited, name, description):
    assert _tool_matches_prohibited_action(_tool(name, description), prohibited)


def test_the_risk_tag_rules_still_run_for_a_tool_named_for_the_safeguard():
    """Only the word comparison is skipped for ``send_email_preview``."""

    tool = _tool("send_email_preview", "Render an email.").model_copy(
        update={
            "risk_hints": [
                ToolRiskHint(
                    tag="customer_communication",
                    source="keyword",
                    confidence="high",
                    basis="inferred_keyword",
                    provenance_kind="keyword_heuristic",
                )
            ]
        }
    )

    assert _tool_matches_prohibited_action(tool, "send external email without preview")


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
        (
            "gmail.send_customer_email",
            "Send an email to a customer.",
            "send external email without preview",
        ),
        # The prohibited action's leading word is its verb, whatever it is
        # (#1035 review: a closed verb list silenced these).
        ("broker.buy_stock", "Buy stock for the account.", "buy stock without approval"),
        ("broker.sell_stock", "", "sell stock"),
        ("trade_stock", "", "trade stock"),
        ("withdraw_funds", "", "withdraw funds"),
        ("move_funds", "", "move funds"),
        ("spend_money", "", "spend money"),
        ("wire_money", "", "wire money"),
        ("book_flight", "", "book flights"),
        ("reset_password", "", "reset passwords"),
        ("kill_process", "", "kill processes"),
        ("rotate_key", "", "rotate keys"),
        ("notify_customer", "", "notify customers"),
        # "shut down" and ``shutdown`` are one word.
        ("shutdown_servers", "Shut down servers.", "shut down servers"),
        ("server.shutdown", "Shut down a server.", "shut down production servers"),
        # The explicit synonym table: update/modify/edit/change,
        # delete/purge/remove/erase/wipe, buy/purchase, execute/run,
        # terminate/close, create/issue/make.
        ("update_user_permissions", "", "modify user permissions"),
        ("crm.purge_customer", "Purge all data for a customer.", "delete customer data"),
        ("purchase_item", "", "buy items"),
        ("run_shell_command", "", "execute shell commands"),
        ("close_account", "Close a customer account.", "terminate customer accounts"),
        ("stripe.create_refund", "Create a refund.", "issue refunds"),
        ("payments.create_payment", "", "make payments"),
        ("loan_approval", "", "approve loan applications"),
        ("send_customer_email", "", "Do not e-mail customers"),
        ("crm.delete_customer_record", "", "Never delete customer records"),
        # A clause after "without" names a safeguard; a tool sharing only part
        # of it is still the prohibited action (#1035 review 2).
        ("send_customer_email", "", "send email without customer consent"),
        ("crm.delete_customer_data", "", "delete customer data without customer approval"),
        ("export_data", "", "export data without a data processing agreement"),
        ("send_user_email", "", "send emails without user confirmation"),
        ("refund_order", "", "refund orders without a refund reason"),
        ("issue_refund", "", "issue refunds unless the order is under $50"),
        # Noun-first phrasing still matches the tool that does it.
        ("export_customer_data", "Export customer data.", "Customer data must never be exported"),
        ("share_pii", "Share PII with a third party.", "PII disclosure to third parties"),
        ("deploy_production", "", "deploy to production except during a change window"),
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
    - %(prohibited)s
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


def _workspace(
    tmp_path: Path,
    *,
    tool: str,
    description: str,
    checks: str = "",
    prohibited: str = DELETE_OR_EXPORT,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "shipgate.yaml").write_text(
        _MANIFEST % {"tool": tool, "checks": checks, "prohibited": prohibited},
        encoding="utf-8",
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
    # Visible in the capability/intent diff, labelled advisory, sorted after
    # every gating misalignment, and the source of no suggested scenario.
    misalignment = next(
        item for item in report.misalignments if item.kind == "prohibited_action_present"
    )
    assert misalignment.gap.startswith("Advisory (lexical evidence, does not gate): ")
    assert report.misalignments[-1] == misalignment
    assert not any(
        scenario.scenario_type == "prohibited_action" for scenario in report.suggested_scenarios
    )
    # Nor does it select a capability or change a capability's control status.
    assert not any(finding.id in fact.related_findings for fact in report.capability_facts)

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


def test_a_partial_safeguard_word_in_the_tool_name_does_not_hide_the_action(tmp_path):
    """#1035 review 2: ``crm.send_customer_email`` against "send email without
    customer consent". main raised a gap; the second cut published nothing,
    because the tool name shared "customer" with the safeguard clause.
    """

    report = _scan(
        _workspace(
            tmp_path / "consent",
            tool="crm.send_customer_email",
            description="Send an email to a customer.",
            prohibited="send email without customer consent",
        )
    )

    finding = next(
        item for item in report.findings if item.check_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
    )
    assert finding.blocks_release is False
    assert not any(
        gap.policy_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
        for gap in report.policy_evidence_gaps
    )


def test_a_tool_whose_verb_is_only_in_its_description_is_published(tmp_path):
    """#1035 review 3: ``crm.cleanup`` "Permanently delete customer records."
    against "delete customer data". main raised a gap; the cycle-3 clause
    parser published nothing, because the description's first word is not
    its verb.
    """

    report = _scan(
        _workspace(
            tmp_path / "cleanup",
            tool="crm.cleanup",
            description="Permanently delete customer records.",
            prohibited="delete customer data",
        )
    )

    finding = next(
        item for item in report.findings if item.check_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
    )
    assert finding.evidence["prohibited_action"] == "delete customer data"
    assert finding.blocks_release is False
    assert not any(
        gap.policy_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
        for gap in report.policy_evidence_gaps
    )


def test_a_prohibited_action_with_an_unlisted_verb_is_still_published(tmp_path):
    """#1035 review: "buy stock without approval" against ``broker.buy_stock``.

    main raised a gap for it; the first cut of this change, which needed a
    verb from a closed list, published nothing. The prohibited action's
    leading word is its verb, so the match is a visible advisory.
    """

    report = _scan(
        _workspace(
            tmp_path / "buy",
            tool="broker.buy_stock",
            description="Buy stock for the account.",
            prohibited="buy stock without approval",
        )
    )

    finding = next(
        item for item in report.findings if item.check_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
    )
    assert finding.evidence["prohibited_action"] == "buy stock without approval"
    assert finding.blocks_release is False
    assert not any(
        gap.policy_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
        for gap in report.policy_evidence_gaps
    )
    assert report.release_decision.decision == "passed"
