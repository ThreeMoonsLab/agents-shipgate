from agents import Agent
from billing_tools import lookup_receipt, return_quote

billing_agent = Agent(
    name="billing",
    handoff_description="Answers questions about what the customer was billed, receipts and return quotes.",
    instructions="Explain what the customer was billed and their receipts. Quote returns when asked; triage submits the request.",
    tools=[return_quote, lookup_receipt],
)
