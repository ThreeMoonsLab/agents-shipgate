"""Specialist agents triage can hand a conversation to, chosen per deployment."""

import os
from importlib import import_module

ROUTES = tuple(
    name.strip() for name in os.environ.get("TRIAGE_ROUTES", "billing").split(",") if name.strip()
)


def specialist(name: str):
    return getattr(import_module(name), f"{name}_agent")
