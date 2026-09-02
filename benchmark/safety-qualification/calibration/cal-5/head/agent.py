"""Order-status support agent.

Answers customer questions about existing orders. Every tool here reads from
the order service; nothing writes.
"""

from langchain.agents import create_agent
from langchain.tools import tool
from pydantic import BaseModel, Field

ORDER_SERVICE = "https://orders.internal.example/api/v1"
PAYMENTS_SERVICE = "https://payments.internal.example/api/v1"


class OrderInput(BaseModel):
    order_id: str = Field(..., description="Order identifier, e.g. ORD-10422.")


class RefundInput(BaseModel):
    order_id: str = Field(..., description="Order identifier, e.g. ORD-10422.")
    amount_cents: int = Field(..., description="Amount to refund, in cents.")
    reason: str = Field("", description="Free-text reason shown to the customer.")


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


@tool(args_schema=RefundInput)
def issue_refund(order_id: str, amount_cents: int, reason: str = "") -> dict:
    """Refund part or all of an order to the customer's original payment method."""
    import requests

    response = requests.post(
        f"{PAYMENTS_SERVICE}/refunds",
        json={"order_id": order_id, "amount_cents": amount_cents, "reason": reason},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


agent = create_agent(
    model="gpt-4.1-mini",
    tools=[lookup_order, shipping_status, issue_refund],
    system_prompt=(
        "You help customers understand the state of their orders. "
        "You can look orders up, report shipping status, and issue refunds "
        "when a customer asks for one."
    ),
)
