import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

const server = new McpServer({ name: "example", version: "0.1.0" });

// Read-only.
server.registerTool("list_records", { description: "List records." }, async () => ({
  content: [],
}));

// Authority-bearing: the reason a first-contact evaluator needs the gate.
server.registerTool("delete_record", { description: "Delete one record." }, async () => ({
  content: [],
}));

export { server };
