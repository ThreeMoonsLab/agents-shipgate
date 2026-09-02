from pathlib import Path

import yaml
from crewai import Agent, Crew
from crewai.tools import BaseTool, tool
from pydantic import BaseModel, Field

TOOL_PROFILES = Path(__file__).with_name("tool_profiles.yaml")


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


TOOL_FACTORIES = {
    "summarize_case": lambda: summarize_case,
    "lookup_case": CaseLookupTool,
}


def build_tools(profile: str) -> list:
    """Instantiate the tools a profile lists, so each deployment picks its own set."""
    names = yaml.safe_load(TOOL_PROFILES.read_text(encoding="utf-8"))[profile]
    return [TOOL_FACTORIES[name]() for name in names]


reader = Agent(
    role="Support case reader",
    goal="Summarize support case metadata without changing customer records",
    backstory="Reads existing support evidence for the on-call engineer.",
    tools=build_tools("support_reader"),
)

crew = Crew(agents=[reader])
