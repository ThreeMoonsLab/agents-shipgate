from crewai import Agent, Crew
from crewai.tools import BaseTool, tool
from crewai_tools import FileReadTool
from pydantic import BaseModel, Field

raise RuntimeError("agents-shipgate must parse this file without importing it")


class CaseLookupInput(BaseModel):
    case_id: str = Field(..., description="Support case identifier.")


@tool("summarize_case")
def summarize_case(case_id: str) -> dict:
    """Summarize read-only support case metadata for reviewer context."""
    return {"case_id": case_id, "summary": "Customer asked about refund timing."}


class CaseLookupTool(BaseTool):
    name: str = "lookup_case"
    description: str = "Look up read-only metadata for an existing support case."
    args_schema = CaseLookupInput

    def _run(self, case_id: str) -> dict:
        return {"case_id": case_id, "status": "open"}


file_tool = FileReadTool()
case_lookup_tool = CaseLookupTool()

researcher = Agent(
    role="Support case reader",
    goal="Review support case metadata without changing customer records",
    backstory="Reviews existing support evidence for release-readiness examples.",
    tools=[summarize_case, case_lookup_tool, file_tool],
)

crew = Crew(agents=[researcher])
