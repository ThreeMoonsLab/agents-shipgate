from agents import Agent
from reply_tools import draft_reply
from ticket_tools import lookup_ticket

triage_assistant = Agent(
    name="triage-assistant",
    instructions="Look up the ticket, then draft a reply for the support engineer.",
    tools=[lookup_ticket, draft_reply],
)
