"""Install target and stdio smoke for CI's `mcp-extra` job (#713).

`python scripts/mcp_extra_requirement.py floor` prints `mcp==<floor>` from the
`[mcp]` extra in pyproject.toml; `newest` prints the declared range itself, so
pip resolves the newest release it admits. Deriving both from pyproject.toml
keeps the job testing the range the package actually declares.

`--stdio-smoke` starts the installed `agents-shipgate mcp-serve` as a real stdio
MCP server, lists its tools through the SDK client, and exits non-zero unless
exactly the five read-only, closed-world tools answer.
"""

from __future__ import annotations

import asyncio
import re
import sys
import tomllib
from pathlib import Path

EXPECTED_TOOLS = {
    "shipgate.capabilities",
    "shipgate.check",
    "shipgate.explain",
    "shipgate.handoff",
    "shipgate.preflight",
}


def requirement(which: str) -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extra = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]["mcp"]
    if len(extra) != 1:
        raise SystemExit(f"expected one requirement in the [mcp] extra, found {extra!r}")
    (declared,) = extra
    match = re.fullmatch(r"mcp>=([0-9][0-9.]*),<[0-9][0-9.]*", declared)
    if match is None:
        raise SystemExit(f"cannot read a floor from the [mcp] extra {declared!r}")
    if which == "floor":
        return f"mcp=={match.group(1)}"
    if which == "newest":
        return declared
    raise SystemExit(f"expected floor or newest, got {which!r}")


async def _list_tools():
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    parameters = StdioServerParameters(command="agents-shipgate", args=["mcp-serve"])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return (await session.list_tools()).tools


def stdio_smoke() -> None:
    tools = asyncio.run(asyncio.wait_for(_list_tools(), timeout=60))
    names = {tool.name for tool in tools}
    if names != EXPECTED_TOOLS:
        raise SystemExit(f"mcp-serve listed {sorted(names)}, expected {sorted(EXPECTED_TOOLS)}")
    for tool in tools:
        annotations = tool.annotations
        if (
            annotations is None
            or annotations.read_only_hint is not True
            or annotations.open_world_hint is not False
        ):
            raise SystemExit(f"{tool.name} is not advertised read-only and closed-world")
    print(f"mcp-serve answered over stdio with {len(tools)} read-only tools")


def main(argv: list[str]) -> None:
    if argv == ["--stdio-smoke"]:
        stdio_smoke()
        return
    if len(argv) != 1:
        raise SystemExit("usage: mcp_extra_requirement.py floor|newest|--stdio-smoke")
    print(requirement(argv[0]))


if __name__ == "__main__":
    main(sys.argv[1:])
