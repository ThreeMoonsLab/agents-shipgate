from agents import function_tool as ft


@ft(
    name_override="support.render_reply",
    description_override="Draft a support reply for human review.",
)
async def render_reply(
    case_id: str,
    *,
    include_private_notes: bool = False,
    tone: str = "concise",
) -> str:
    return f"Draft reply for {case_id} in {tone} tone."

