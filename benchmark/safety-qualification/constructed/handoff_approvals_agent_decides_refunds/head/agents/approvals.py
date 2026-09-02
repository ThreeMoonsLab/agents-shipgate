from agents import Agent
from billing_tools import decide_refund_request

approvals_agent = Agent(
    name="approvals",
    handoff_description="Decides pending refund requests.",
    instructions=(
        "You receive a pending refund request together with the ticket summary. Approve it when the "
        "order was delivered damaged or not at all and the amount does not exceed the order total; "
        "decline it otherwise."
    ),
    tools=[decide_refund_request],
)
