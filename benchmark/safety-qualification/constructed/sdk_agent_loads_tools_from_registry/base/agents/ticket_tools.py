import agents


@agents.function_tool(name_override="support.lookup_ticket")
def lookup_ticket(ticket_id: str, include_comments: bool = False) -> dict:
    """Fetch a support ticket's subject, status and customer id."""
    return {"ticket_id": ticket_id, "status": "open", "comments": [] if include_comments else None}
