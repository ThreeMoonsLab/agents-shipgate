OpenAI Agents SDK static extraction fixture with a directory of Python tools.

This fixture exercises the conservative `openai_agents_sdk` adapter path: it
parses local Python files with AST, extracts `@function_tool` declarations and
the literal `support_assistant = Agent(..., tools=[...])` binding, and
does not import user code. Directory scanning is non-recursive: only immediate
`*.py` files under `agents/` are scanned in sorted order.
`agents/dynamic_tools.py` intentionally contains a runtime factory that is
ignored. [`inventories/tools.json`](inventories/tools.json) is the reviewed
inventory for the two bound static tools: it lets the fixture exercise AST
extraction without pretending AST alone proves a complete framework binding
graph. Real agents that build tools dynamically must provide a reviewed MCP,
OpenAPI, or inventory artifact before they can qualify for `passed`.
