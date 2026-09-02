from agents import Agent
from billing_tools import return_quote

billing_agent = Agent(
    name="billing",
    handoff_description="Answers questions about what the customer was billed and quotes returns.",
    instructions="Explain what the customer was billed. Quote returns when asked; triage submits the request.",
    tools=[return_quote],
)
