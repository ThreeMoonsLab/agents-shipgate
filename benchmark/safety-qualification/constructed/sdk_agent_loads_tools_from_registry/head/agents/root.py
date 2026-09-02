from agents import Agent
from tool_registry import load_tools

triage_assistant = Agent(
    name="triage-assistant",
    instructions="Look up the ticket, then draft a reply for the support engineer.",
    tools=load_tools("triage"),
)
