import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

type OperationType = "create" | "read" | "update" | "delete";

/**
 * The class shape the official MongoDB server uses: the tool's identity is a
 * static field, and the sibling `description` and `operationType` literals are
 * read from the same class body.
 *
 * The registration written in this comment — `static toolName = "example"` —
 * is invisible to the reader. Comments and string bodies are masked before
 * anything is matched, so a documented example can never enter the catalog.
 */
export class DropTicketArchiveTool {
  public static readonly toolName: string = "support.drop_ticket_archive";
  public static operationType: OperationType = "delete";
  public description = "Delete the archived-ticket collection for a workspace.";

  static register(server: McpServer): void {
    // Deliberately *not* a literal: the reader reports this registration as
    // unenumerated rather than dropping it, which is what makes the tool count
    // in `detect`'s evidence honest about what it could not name.
    server.registerTool(DropTicketArchiveTool.toolName, {}, () => undefined);
  }
}
