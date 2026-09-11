import { Router } from "express";

// Spelled exactly like the TypeScript SDK idiom, but this repository declares
// no MCP framework dependency, so the registration-site reader must not claim
// a tool surface from it. The provenance gate is the whole point of the case.
const router = Router();

router.registerTool("list_records", { description: "List records." });
router.registerTool("delete_record", { description: "Delete one record." });

export { router };
