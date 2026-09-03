"""Assembles the agent's tool surface from the deployment capability profile."""

import os
from pathlib import Path

import yaml
from langchain_community.agent_toolkits.openapi.toolkit import OpenAPIToolkit

PROFILE_PATH = Path(os.environ.get("FLEET_PROFILE", "/etc/fleet/capability-profile.yaml"))


def load_profile() -> dict:
    """Read the capability profile this deployment was given.

    The file is written by the platform team's provisioning job, not by this
    repository, and differs per region.
    """

    return yaml.safe_load(PROFILE_PATH.read_text())


def build_tools(profile: dict):
    """Return the toolkit named by the profile, scoped to the profile's grants."""

    toolkit = OpenAPIToolkit.from_llm(
        spec_url=profile["fleet_api"]["spec_url"],
        allowed_operations=profile["fleet_api"]["operations"],
    )
    return toolkit.get_tools()
