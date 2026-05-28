import agents


@agents.function_tool(name_override="support.lookup_case")
def lookup_case(config, case_id: str, tags: list[str] | None = None) -> dict:
    """Look up read-only support case metadata."""
    return {"case_id": case_id, "tags": tags or []}

