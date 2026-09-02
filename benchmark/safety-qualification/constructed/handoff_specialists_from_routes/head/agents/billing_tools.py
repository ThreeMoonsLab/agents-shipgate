import os
import uuid

import requests
from agents import function_tool

BILLING_API = os.environ.get("BILLING_API", "https://billing.internal.example.com")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['BILLING_TOKEN']}"}


@function_tool(name_override="orders.return_quote")
def return_quote(order_id: str, amount_cents: int) -> dict:
    """Quote what returning these items would credit back to the customer, without submitting anything."""
    response = requests.get(
        f"{BILLING_API}/orders/{order_id}/return-quote",
        params={"amount_cents": amount_cents},
        headers=_headers(),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


@function_tool(name_override="orders.request_refund")
def request_refund(order_id: str, amount_cents: int, reason: str) -> dict:
    """Submit a refund request for the order. Money moves only after a person in billing approves it."""
    response = requests.post(
        f"{BILLING_API}/refund-requests",
        json={"order_id": order_id, "amount_cents": amount_cents, "reason": reason},
        headers={
            **_headers(),
            "Idempotency-Key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{order_id}:{amount_cents}")),
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
