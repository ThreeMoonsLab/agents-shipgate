# HITL Evidence Covered Agent

This fixture uses the same refund-support domain as `hitl_evidence_agent`,
but includes the local human-in-the-loop evidence files required for a
`limited_auto_approval` review posture.

The sample demonstrates reviewer-visible provenance for approval traces,
override reasons, high-risk auto-approval exclusions, and promotion criteria.
Agents Shipgate reads these local artifacts and structures them for review; it
does not certify runtime enforcement.

