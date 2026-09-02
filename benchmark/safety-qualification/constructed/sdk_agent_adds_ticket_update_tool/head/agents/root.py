from agents import Agent
from reply_tools import draft_reply
from ticket_tools import lookup_ticket, update_ticket

triage_assistant = Agent(
    name="triage-assistant",
    instructions=(
        "Look up the ticket, draft a reply for the support engineer, and set the "
        "ticket status once the draft is ready."
    ),
    tools=[lookup_ticket, draft_reply, update_ticket],
)
