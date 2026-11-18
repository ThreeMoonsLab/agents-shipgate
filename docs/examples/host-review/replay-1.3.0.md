# Replay with Agents Shipgate 1.3.0

On 2026-11-17 UTC, a fresh installation of the 1.3.0 wheel replayed the
three constructed histories on macOS arm64 with CPython 3.12.13. The wheel
emitted contract 42 and matched the SHA-256 in the release metadata,
`40d7cc2bd869c1a180f77ab60ce1e820d01ff0d84a572ad135a25fbe1ad92e43`.
The original 1.1.0 captures remain available from each card.
[PyPI release metadata](https://pypi.org/pypi/agents-shipgate/1.3.0/json)

Restore `main` and `change` with the [history instructions](README.md#restore-the-exact-example-history),
check out `change`, then run:

```sh
uvx --from agents-shipgate==1.3.0 agents-shipgate diff --base main --json
```

The base and head of each restored history matched [the recorded commits](provenance.json).

| Scenario | Comparison | Captured result |
| --- | --- | --- |
| [Claude shell rule](shell/card.md) | `comparable` | Two medium rows. The new `Bash(npm *)` row widens access. The removed `Bash(npm test *)` row is a removal. |
| [Remote MCP declaration](mcp/card.md) | `comparable` | One high widening adds the remote `docs` declaration in `.mcp.json`. |
| [Workflow token permissions](workflow/card.md) | `comparable` | One high widening. The `inspect` job changes from `contents: read` to `contents: write`. Version 1.3.0 labels the workflow-level values `access: read` and `access: write`. |
| [Malformed shell configuration](shell/malformed-input.txt) | `incomparable` | Advisory exit `0` and `head_inventory_incomplete`. The changed settings could not be read. |

Each comparison covers the declared configuration supported by the installed
reader. The replay did not execute a hook, workflow or MCP server. Distribution,
participant attempts, and review outcomes remain open.
