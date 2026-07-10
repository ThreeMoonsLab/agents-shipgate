from langchain.tools import tool


@tool
def inspect_case(case_id: str) -> dict:
    """Inspect a support case."""
    return {"case_id": case_id}
