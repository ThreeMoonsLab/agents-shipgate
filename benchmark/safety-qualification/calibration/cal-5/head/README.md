# Order-status support agent

A LangChain agent that answers customer questions about existing orders. It
reads order details and shipping status from the internal order service.

Run `agents-shipgate verify` on every pull request (see
`.github/workflows/shipgate.yml`); the reviewed tool inventory lives in
`inventories/tools.json`.
