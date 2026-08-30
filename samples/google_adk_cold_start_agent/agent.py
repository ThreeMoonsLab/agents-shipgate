from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset import OpenAPIToolset


def assemble_case_timeline(case_id: str) -> dict:
    """Assemble the ordered activity timeline for one support case."""
    return {"case_id": case_id, "entries": []}


def update_case_index(case_id: str) -> dict:
    """Rebuild the stored search index entry for one support case."""
    return {"case_id": case_id, "indexed": True}


def list_case_attachments(case_id: str) -> dict:
    """Return the file attachments recorded against one support case."""
    return {"case_id": case_id, "attachments": []}


def record_case_outcome(case_id: str, outcome: str) -> dict:
    """Store the reviewed outcome string on one support case."""
    return {"case_id": case_id, "outcome": outcome}


def issue_goodwill_refund(case_id: str, amount: float) -> dict:
    """Issue a goodwill refund against the original payment method."""
    return {"case_id": case_id, "amount": amount}


timeline_tool = FunctionTool(func=assemble_case_timeline)
index_tool = FunctionTool(func=update_case_index)
attachments_tool = FunctionTool(func=list_case_attachments)
outcome_tool = FunctionTool(func=record_case_outcome)
refund_tool = FunctionTool(func=issue_goodwill_refund)
ops_mcp_tools = McpToolset(
    tool_filter=[
        "ops.queue_backfill",
        "support.get_update_history",
        "ops.export_case_bundle",
    ],
    inventory_path="inventories/ops-mcp-tools.json",
)
ops_api_tools = OpenAPIToolset(
    spec_str=Path("specs/ops.openapi.yaml").read_text(),
    spec_str_type="yaml",
)

root_agent = LlmAgent(
    name="adk_ops_agent",
    instruction="Review support cases and route refunds for approval.",
    tools=[
        timeline_tool,
        index_tool,
        attachments_tool,
        outcome_tool,
        refund_tool,
        ops_mcp_tools,
        ops_api_tools,
    ],
)
