"""Retail-ops AI assistant: OpenAI Agents SDK static surface.

Five SDK function tools layered on top of the OpenAPI + MCP surfaces. The
agent uses these to prepare actions for human review (render previews,
calculate totals, format labels) plus two internal-only workflow tools
(callback scheduling and human escalation).

Static-only: this file is parsed via ``ast.parse`` and never imported.
"""

from __future__ import annotations

from agents import Agent, function_tool


@function_tool
def render_customer_email_preview(
    recipient: str,
    subject: str,
    body: str,
) -> str:
    """Render a customer email preview WITHOUT sending it.

    Returns a formatted preview string for the human reviewer to inspect
    before approving a real send through ``crm.send_customer_email``.
    """
    return f"To: {recipient}\nSubject: {subject}\n\n{body}"


@function_tool
def calculate_refund_total(
    line_items: list,
    tax_rate: float,
    restocking_fee: float,
) -> dict:
    """Compute the net refund amount for a set of order line items.

    Pure computation. Read-only; does not contact the payment processor.
    """
    subtotal = sum(item.get("price", 0) * item.get("quantity", 1) for item in line_items)
    tax = subtotal * tax_rate
    return {
        "subtotal": subtotal,
        "tax": tax,
        "restocking_fee": restocking_fee,
        "total": subtotal + tax - restocking_fee,
    }


@function_tool
def generate_shipping_label_pdf(
    shipment_id: str,
    carrier: str,
) -> str:
    """Render a shipping label PDF for visual confirmation.

    Local rendering only; does not call the carrier API. Returns a path
    to the generated PDF artifact.
    """
    return f"/tmp/shipping-labels/{carrier}-{shipment_id}.pdf"


@function_tool
def schedule_internal_callback(
    queue: str,
    delay_seconds: int,
    payload: dict,
) -> str:
    """Schedule an internal job to retry a step after a delay.

    Internal-only side effect (writes to the internal job queue). Idempotent
    when ``payload`` includes a deduplication key.
    """
    return f"job-{queue}-{delay_seconds}"


@function_tool
def escalate_to_human(
    reason: str,
    severity: str,
    context: dict,
) -> str:
    """Hand off the current conversation to a human operator.

    Internal-only: writes a row to the human-escalation queue and marks the
    current conversation as paused.
    """
    return f"escalation-{severity}"


ops_assistant = Agent(
    name="retail-ops-assistant",
    instructions=(
        "You help the retail-ops team prepare refunds, shipment changes, and "
        "customer communications. Always render previews before sending. Always "
        "escalate destructive or high-value actions to a human reviewer."
    ),
    tools=[
        render_customer_email_preview,
        calculate_refund_total,
        generate_shipping_label_pdf,
        schedule_internal_callback,
        escalate_to_human,
    ],
)
