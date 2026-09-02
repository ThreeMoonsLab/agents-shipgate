import stripe
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.mcp_tool import McpToolset


def lookup_case(case_id: str) -> dict:
    """Look up support case metadata for a known case id."""
    return {"status": "ok", "case_id": case_id}


def issue_refund(case_id: str, amount_cents: int) -> dict:
    """Refund the charge attached to a support case through Stripe."""
    charge_id = lookup_case(case_id)["charge_id"]
    refund = stripe.Refund.create(charge=charge_id, amount=amount_cents)
    return {"refund_id": refund.id, "status": refund.status}


lookup_case_tool = FunctionTool(func=lookup_case)
refund_tool = FunctionTool(func=issue_refund)
support_mcp_tools = McpToolset(
    tool_filter=["support.search"],
    inventory_path="inventories/support-mcp.json",
)

billing_agent = LlmAgent(
    name="billing_agent",
    instruction="Issue the refund the support agent hands over, for the amount it states.",
    tools=[refund_tool],
)

root_agent = LlmAgent(
    name="support_agent",
    instruction=(
        "Use the support tools to look up the case and search related articles. "
        "Hand refund requests to the billing agent."
    ),
    tools=[lookup_case_tool, support_mcp_tools],
    sub_agents=[billing_agent],
)
