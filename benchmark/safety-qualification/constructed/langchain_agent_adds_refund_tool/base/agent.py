from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class LookupInput(BaseModel):
    case_id: str = Field(..., description="Support case identifier.")


@tool(args_schema=LookupInput)
def lookup_case(case_id: str) -> dict:
    """Look up metadata for an existing support case."""
    return {"case_id": case_id, "status": "open"}


def summarize_case(case_id: str) -> dict:
    """Summarize support case metadata for the person handling it."""
    return {"case_id": case_id, "summary": "Customer asked about refund timing."}


summary_tool = StructuredTool.from_function(
    func=summarize_case,
    name="summarize_case",
    description="Summarize support case metadata for the person handling it.",
)

agent = create_agent(model=None, tools=[lookup_case, summary_tool])
