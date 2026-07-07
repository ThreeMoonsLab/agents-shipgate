import json

import env
import requests
from agents import Agent, Runner, RunResult, TResponseInputItem, function_tool
from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit

env.ensure("OPENAI_API_KEY")

# The toolkit's authority allowlist is bound from configuration: the
# effective tool surface is defined by config/stripe_actions.json, not by
# code. Static analysis can see the *binding* but not the resulting tools.
_CONFIGURATION = json.load(open("config/stripe_actions.json"))

# Global toolkit instance - initialized in init()
stripe_agent_toolkit = None
support_agent = None


@function_tool
def search_faq(question: str) -> str:
    response = requests.get("https://standupjack.com/faq")
    if response.status_code != 200:
        return "Not sure"
    return f"Given the following context:\n{response.text}\n\nAnswer '{question}' or response with not sure\n"


async def init():
    """Initialize the toolkit and agent. Must be called before run()."""
    global stripe_agent_toolkit, support_agent

    stripe_agent_toolkit = StripeAgentToolkit(
        secret_key=env.ensure("STRIPE_SECRET_KEY"),
        configuration=_CONFIGURATION,
    )

    support_agent = Agent(
        name="Standup Jack Agent",
        instructions=(
            "You are a helpful customer support assistant"
            "Be casual and concise"
            "Use tools to support customers"
            "Sign off with Standup Jack Bot"
        ),
        tools=[search_faq, *stripe_agent_toolkit.get_tools()],
    )


async def run(input: list[TResponseInputItem]) -> RunResult:
    if support_agent is None:
        raise RuntimeError("support_agent not initialized. Call init() first.")
    return await Runner.run(support_agent, input)
