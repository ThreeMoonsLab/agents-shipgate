# Conductor OSS MCP-core agent

Original, local-only fixture for the built-in Conductor OSS workflow JSON
adapter. It contains one literal MCP call, one LLM-selected dynamic MCP call,
a structural `HUMAN` checkpoint, nested control flow, and an intentionally
unsupported HTTP task.

Run it with:

```bash
agents-shipgate scan -c samples/conductor_agent/shipgate.yaml
```

The adapter never starts Conductor or connects to the example endpoints.
