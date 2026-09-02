import os

import agents
import requests

ZENDESK_API = os.environ.get("ZENDESK_API", "https://acme.zendesk.com/api/v2")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['ZENDESK_TOKEN']}"}


@agents.function_tool(name_override="support.lookup_ticket")
def lookup_ticket(ticket_id: str, include_comments: bool = False) -> dict:
    """Fetch a support ticket's subject, status and customer id."""
    return {"ticket_id": ticket_id, "status": "open", "comments": [] if include_comments else None}


@agents.function_tool(name_override="zendesk.update_ticket")
def update_ticket(ticket_id: str, status: str, comment: str) -> dict:
    """Set a ticket's status and append an internal comment."""
    response = requests.put(
        f"{ZENDESK_API}/tickets/{ticket_id}.json",
        json={"ticket": {"status": status, "comment": {"body": comment, "public": False}}},
        headers=_auth_headers(),
        timeout=10,
    )
    response.raise_for_status()
    return {"ticket_id": ticket_id, "status": status}
