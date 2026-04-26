# Task 01 · Install Agents Shipgate and run a scan

The current working directory contains an MCP-shaped tool list at `tools.json`. Your job:

1. Install `agents-shipgate` (use `pipx`, or fall back to `python -m pip`).
2. Generate a starter manifest with `agents-shipgate init --workspace . --write`.
3. Replace any `CHANGE_ME` values in `shipgate.yaml`. Read `tools.json` to inform the agent name and declared purpose.
4. Run `agents-shipgate scan -c shipgate.yaml --ci-mode advisory`.
5. Report back: status, critical/high/medium counts, and the top 3 finding check IDs.

Do not commit anything. Do not modify `tools.json`.
