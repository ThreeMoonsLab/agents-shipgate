# HITL Evidence Agent

This fixture has local manifest evidence a reviewer would expect for
recommendation-only operation: narrow scope, declared approval policy,
idempotency evidence, and an owner for the high-risk tool.

It intentionally targets `limited_auto_approval` without the local validation
evidence files. The HITL evidence findings list what is not on disk:
approval traces, override reasons, high-risk auto-approval exclusions, and
promotion criteria. Agents Shipgate does not decide whether the agent is ready
for auto-approval; it structures those evidence gaps for review.
