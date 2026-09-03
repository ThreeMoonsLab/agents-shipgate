"""Fleet-ops assistant.

Answers questions about vehicles in the fleet. The tool surface is assembled at
start-up from the deployment's capability profile, so one build serves every
region without a code change.
"""

from langchain.agents import create_agent

from .toolkit import build_tools, load_profile

agent = create_agent(
    model="gpt-4.1-mini",
    tools=build_tools(load_profile()),
    system_prompt=(
        "You answer questions about vehicles in the fleet, and you can act on "
        "them where your deployment profile allows it."
    ),
)
