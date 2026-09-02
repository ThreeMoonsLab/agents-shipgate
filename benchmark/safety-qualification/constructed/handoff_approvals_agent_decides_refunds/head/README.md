# Support triage workspace

Two OpenAI Agents SDK agents. `triage` looks up the ticket, submits refund
requests, and hands billing questions to `billing`. Each workspace directory
(`support/`, `billing/`) publishes the inventory of the tools its agent binds;
the agents themselves live under `agents/`.

Refund requests are paid out only after a person in billing approves them in
the billing console.
