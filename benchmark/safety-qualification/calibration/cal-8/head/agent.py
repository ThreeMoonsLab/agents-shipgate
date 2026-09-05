"""Fleet-ops assistant.

Answers questions about vehicles in the fleet. `dispatch_tow` is declared here;
the rest of the surface is assembled at start-up from the deployment's
capability profile.
"""

from langchain.agents import create_agent
from langchain.tools import tool
from pydantic import BaseModel, Field

from .toolkit import build_tools, load_profile

DISPATCH_SERVICE = "https://dispatch.internal.example/api/v1"


class TowInput(BaseModel):
    vehicle_id: str = Field(..., description="Vehicle identifier, e.g. VH-7781.")
    destination: str = Field(..., description="Where the vehicle should be taken.")


@tool(args_schema=TowInput)
def dispatch_tow(vehicle_id: str, destination: str) -> dict:
    """Dispatch a tow truck for a vehicle and bill the account for the callout."""
    import requests

    response = requests.post(
        f"{DISPATCH_SERVICE}/tows",
        json={"vehicle_id": vehicle_id, "destination": destination},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


agent = create_agent(
    model="gpt-4.1-mini",
    tools=[dispatch_tow, *build_tools(load_profile())],
    system_prompt=(
        "You answer questions about vehicles in the fleet, you can dispatch a "
        "tow when a vehicle is stranded, and you can act on vehicles where your "
        "deployment profile allows it."
    ),
)
