"""Order-status support agent.

Answers customer questions about existing orders. Every tool here reads from
the order service; nothing writes.
"""

from langchain.agents import create_agent
from langchain.tools import tool
from pydantic import BaseModel, Field

ORDER_SERVICE = "https://orders.internal.example/api/v1"


class OrderInput(BaseModel):
    order_id: str = Field(..., description="Order identifier, e.g. ORD-10422.")


@tool(args_schema=OrderInput)
def lookup_order(order_id: str) -> dict:
    """Look up the line items, total, and current state of an order."""
    import requests

    response = requests.get(f"{ORDER_SERVICE}/orders/{order_id}", timeout=10)
    response.raise_for_status()
    return response.json()


@tool(args_schema=OrderInput)
def shipping_status(order_id: str) -> dict:
    """Return the carrier, tracking number, and last scan for an order."""
    import requests

    response = requests.get(f"{ORDER_SERVICE}/orders/{order_id}/shipment", timeout=10)
    response.raise_for_status()
    return response.json()


agent = create_agent(
    model="gpt-4.1-mini",
    tools=[lookup_order, shipping_status],
    system_prompt=(
        "You help customers understand the state of their orders. "
        "You can look orders up and report shipping status."
    ),
)
