from agents import function_tool as ft


@ft(
    name_override="support.draft_reply",
    description_override="Draft a reply to the customer for a person to edit and send.",
)
async def draft_reply(ticket_id: str, *, tone: str = "concise") -> str:
    return f"Draft reply for {ticket_id} in {tone} tone."
