from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

raise RuntimeError("agents-shipgate must parse this file without importing it")


class LookupInput(BaseModel):
    case_id: str = Field(..., description="Support case identifier.")


@tool(args_schema=LookupInput)
def lookup_case(case_id: str) -> dict:
    """Look up read-only metadata for an existing support case."""
    return {"case_id": case_id, "status": "open"}


def summarize_case(case_id: str) -> dict:
    """Summarize read-only support case metadata for reviewer context."""
    return {"case_id": case_id, "summary": "Customer asked about refund timing."}


summary_tool = StructuredTool.from_function(
    func=summarize_case,
    name="summarize_case",
    description="Summarize read-only support case metadata for reviewer context.",
)

agent = create_agent(model=None, tools=[lookup_case, summary_tool])
