"""Tool profiles, so operations can change what an assistant may call without a deploy."""

import tomllib
from importlib import import_module
from pathlib import Path

PROFILES = Path(__file__).with_name("tools.toml")


def load_tools(profile: str) -> list:
    config = tomllib.loads(PROFILES.read_text(encoding="utf-8"))
    tools = []
    for ref in config["profiles"][profile]:
        module_name, _, attr = ref.rpartition(".")
        tools.append(getattr(import_module(module_name), attr))
    return tools
