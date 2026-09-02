from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.mcp_tool import McpToolset


def lookup_case(case_id: str) -> dict:
    """Look up support case metadata for a known case id."""
    return {"status": "ok", "case_id": case_id}


lookup_case_tool = FunctionTool(func=lookup_case)
support_mcp_tools = McpToolset(
    tool_filter=["support.search"],
    inventory_path="inventories/support-mcp.json",
)
calendar_tools = McpToolset(
    tool_filter=["calendar.create_event"],
    inventory_path="inventories/calendar-mcp.json",
)

root_agent = LlmAgent(
    name="support_agent",
    instruction=(
        "Use the support tools to look up the case and search related articles. "
        "When the customer asks for a call, book it on the support calendar."
    ),
    tools=[lookup_case_tool, support_mcp_tools, calendar_tools],
)
