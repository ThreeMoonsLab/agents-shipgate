import agents


@agents.function_tool(name_override="support.lookup_ticket")
def lookup_ticket(ticket_id: str) -> dict:
    """Fetch a support ticket's subject, status, order id and customer id."""
    return {"ticket_id": ticket_id, "status": "open", "order_id": None}
