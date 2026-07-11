from agents import Agent
from case_tools import lookup_case
from reply_tools import render_reply

support_assistant = Agent(
    name="support-sdk-assistant",
    tools=[lookup_case, render_reply],
)
