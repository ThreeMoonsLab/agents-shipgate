OpenAI Agents SDK static extraction fixture with a directory of Python tools.

This fixture exercises the conservative `openai_agents_sdk` adapter path: it
parses local Python files with AST, extracts `@function_tool` declarations, and
does not import user code. `agents/dynamic_tools.py` intentionally contains a
runtime factory that is ignored; provide MCP, OpenAPI, or inventory artifacts
when a real agent builds tools dynamically.

