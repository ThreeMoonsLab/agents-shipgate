from crewai import Agent, Crew
from crewai.tools import BaseTool, tool
from pydantic import BaseModel, Field


class CaseLookupInput(BaseModel):
    case_id: str = Field(..., description="Support case identifier.")


@tool("summarize_case")
def summarize_case(case_id: str) -> dict:
    """Summarize support case metadata for the person handling it."""
    return {"case_id": case_id, "summary": "Customer asked about refund timing."}


class CaseLookupTool(BaseTool):
    name: str = "lookup_case"
    description: str = "Look up metadata for an existing support case."
    args_schema = CaseLookupInput

    def _run(self, case_id: str) -> dict:
        return {"case_id": case_id, "status": "open"}


case_lookup_tool = CaseLookupTool()

reader = Agent(
    role="Support case reader",
    goal="Summarize support case metadata without changing customer records",
    backstory="Reads existing support evidence for the on-call engineer.",
    tools=[summarize_case, case_lookup_tool],
)

crew = Crew(agents=[reader])
