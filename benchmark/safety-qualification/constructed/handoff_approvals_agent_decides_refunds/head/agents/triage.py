from agents import Agent
from approvals import approvals_agent
from billing import billing_agent
from billing_tools import request_refund
from ticket_tools import lookup_ticket

triage_agent = Agent(
    name="triage",
    instructions=(
        "Look up the ticket first. Hand billing questions to the billing agent. "
        "Submit a refund request when the customer asks for one and the order qualifies, "
        "then hand the pending request to the approvals agent so the customer gets an answer "
        "in the same conversation."
    ),
    tools=[lookup_ticket, request_refund],
    handoffs=[billing_agent, approvals_agent],
)
