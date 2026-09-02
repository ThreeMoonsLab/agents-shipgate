from agents import Agent
from billing_tools import request_refund
from specialists import ROUTES, specialist
from ticket_tools import lookup_ticket

triage_agent = Agent(
    name="triage",
    instructions=(
        "Look up the ticket first. Hand billing questions to the billing agent. "
        "Submit a refund request when the customer asks for one and the order qualifies."
    ),
    tools=[lookup_ticket, request_refund],
    handoffs=[specialist(name) for name in ROUTES],
)
