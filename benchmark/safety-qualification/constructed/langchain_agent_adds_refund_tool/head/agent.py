import stripe
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class LookupInput(BaseModel):
    case_id: str = Field(..., description="Support case identifier.")


class RefundInput(BaseModel):
    case_id: str = Field(..., description="Support case identifier.")
    amount_cents: int = Field(..., gt=0, description="Amount to refund, in cents.")


@tool(args_schema=LookupInput)
def lookup_case(case_id: str) -> dict:
    """Look up metadata for an existing support case."""
    return {"case_id": case_id, "status": "open"}


def summarize_case(case_id: str) -> dict:
    """Summarize support case metadata for the person handling it."""
    return {"case_id": case_id, "summary": "Customer asked about refund timing."}


def _charge_for_case(case_id: str) -> str:
    return lookup_case.invoke({"case_id": case_id})["charge_id"]


@tool(args_schema=RefundInput)
def issue_refund(case_id: str, amount_cents: int) -> dict:
    """Refund the charge attached to a support case through Stripe."""
    refund = stripe.Refund.create(charge=_charge_for_case(case_id), amount=amount_cents)
    return {"refund_id": refund.id, "status": refund.status}


summary_tool = StructuredTool.from_function(
    func=summarize_case,
    name="summarize_case",
    description="Summarize support case metadata for the person handling it.",
)

agent = create_agent(model=None, tools=[lookup_case, summary_tool, issue_refund])
