# Support orders workflow

An n8n workflow behind the `support-orders` webhook. The Support Agent node
answers questions about an order using the tools attached to it; refund
requests it summarizes go to `#support-refunds`, where a person approves or
declines them before the Stripe call runs.

The exported workflow is [`workflows/support-orders.json`](workflows/support-orders.json);
[`inventories/tools.json`](inventories/tools.json) lists the tools the agent
node can call.
