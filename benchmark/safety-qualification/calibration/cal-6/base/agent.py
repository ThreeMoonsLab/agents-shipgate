"""Fleet-ops assistant.

Answers questions about vehicles in the fleet. Every tool here is declared in
this file, so the surface a reviewer sees is the surface the agent gets.
"""

from langchain.agents import create_agent
from langchain.tools import tool
from pydantic import BaseModel, Field

FLEET_SERVICE = "https://fleet.internal.example/api/v1"


class VehicleInput(BaseModel):
    vehicle_id: str = Field(..., description="Vehicle identifier, e.g. VH-7781.")


@tool(args_schema=VehicleInput)
def vehicle_status(vehicle_id: str) -> dict:
    """Return the current location, fuel level, and driver of one vehicle."""
    import requests

    response = requests.get(f"{FLEET_SERVICE}/vehicles/{vehicle_id}", timeout=10)
    response.raise_for_status()
    return response.json()


@tool(args_schema=VehicleInput)
def maintenance_history(vehicle_id: str) -> dict:
    """Return the completed and scheduled service records for one vehicle."""
    import requests

    response = requests.get(f"{FLEET_SERVICE}/vehicles/{vehicle_id}/service", timeout=10)
    response.raise_for_status()
    return response.json()


agent = create_agent(
    model="gpt-4.1-mini",
    tools=[vehicle_status, maintenance_history],
    system_prompt=(
        "You answer questions about vehicles in the fleet. "
        "You can report a vehicle's status and its maintenance history."
    ),
)
