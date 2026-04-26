from agents import Agent, function_tool


@function_tool
def send_email_preview(recipient: str, subject: str, body: str) -> str:
    """Render a customer email preview without sending it."""
    return f"To: {recipient}\nSubject: {subject}\n\n{body}"


refund_agent = Agent(
    name="refund-assistant",
    instructions="Answer refund policy questions and prepare risky actions for review.",
    tools=[send_email_preview],
)
