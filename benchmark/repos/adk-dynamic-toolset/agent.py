from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool, LongRunningFunctionTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset import OpenAPIToolset


def guard_support_actions(*args, **kwargs):
    return None


def lookup_case(case_id: str) -> dict:
    """Look up support case metadata for a known case id."""
    return {"status": "ok", "case_id": case_id}


def request_refund_approval(case_id: str, amount: float) -> dict:
    """Create a refund approval request and return its pending status."""
    return {"status": "pending", "approval_id": case_id}


lookup_case_tool = FunctionTool(func=lookup_case)
approval_tool = LongRunningFunctionTool(func=request_refund_approval)
support_api_tools = OpenAPIToolset(
    spec_str=Path("specs/support.openapi.yaml").read_text(),
    spec_str_type="yaml",
)
support_mcp_tools = McpToolset(
    tool_filter=["support.search"],
    inventory_path="inventories/mcp-tools.json",
)

root_agent = LlmAgent(
    name="adk_support_agent",
    instruction="Use support tools for case lookup and approval routing.",
    tools=[lookup_case_tool, approval_tool, support_api_tools, support_mcp_tools],
    before_tool_callback=guard_support_actions,
)
