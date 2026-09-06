import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { searchKnowledgeBase } from "./handlers.js";
import { DropTicketArchiveTool } from "./tools/dropTicketArchive.js";

export function createServer(): McpServer {
  const server = new McpServer({ name: "support", version: "0.1.0" });

  // Registered at the call site. The only thing read out of this file is the
  // string literal in first-argument position; the schema below is never
  // evaluated, and the description is not taken from here.
  server.registerTool(
    "support.search_kb",
    {
      description: "Search support knowledge-base articles by free-text query.",
      inputSchema: { query: z.string() },
    },
    searchKnowledgeBase,
  );

  DropTicketArchiveTool.register(server);
  return server;
}
