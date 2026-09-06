# Distribution-parity corpus

Minimized workspaces that the engine and the surfaces which restate it are both
asked about, driven by `tests/test_distribution_surface_parity.py` (#497).

Only what nothing else covers. #485 shipped `samples/mcp_source_only_server/`
(TypeScript) and `tests/mcp_idiom_corpus.py` (source text, both readers), so the
TypeScript positive that used to live here was deleted rather than kept beside
them — a second corpus is the same defect as a second implementation. What
remains is a **Go** workspace, which `samples/` has none of, and a negative that
exercises the dependency gate at whole-workspace level, which the source-text
corpus does not reach.

The shape they hold is why this matters. Before #485, the parity test compared
`tools/shipgate-detect.py` with the CLI on `samples/`, and no sample held an MCP
server whose tool surface lives only in registration sites — so when #431 taught
the CLI to read those, the two implementations diverged on the one question the
script exists to answer and CI stayed green. A corpus is only as strong as the
shapes in it, and `samples/` still holds no Go one.

| Workspace | Shape | Both implementations should answer |
| --- | --- | --- |
| `go_tool_struct_positive` | `github.com/modelcontextprotocol/go-sdk` required, `mcp.Tool{Name: "…"}` composite literals | `is_agent_project: true`, one `mcp_server_source` at `internal/tools` |
| `ts_no_mcp_dependency_negative` | The TypeScript idiom spelled exactly, and **no** declared MCP dependency | `is_agent_project: false` |

The negative case is the load-bearing one. Without it a reader could pass this
corpus by claiming a tool surface from the spelling alone, which is the
fail-open shape #393 named: a proof resting on a spelling is not a proof. The
dependency gate is what makes `.registerTool(` on an Express router a
coincidence rather than evidence, and any port of the reader has to keep
answering `false` here.

Each workspace is the smallest thing that exercises its route. They are not
realistic servers and are not meant to be — a larger fixture would make a
divergence harder to read, not easier.
