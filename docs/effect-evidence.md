# Effect projections and evidence

This describes the current source-tree presentation. Older builds may use the
shorter `Effects` heading; their existing JSON evidence fields still distinguish
the projection from its support.

An action can have a conservative `write` projection while its effect evidence
is `unknown`. The first is the risk envelope Agents Shipgate retains when it
cannot establish a narrower effect; the second says what the static reader
actually established. Neither a name such as `lookup_account` nor the severity
of a policy finding supplies the missing evidence.

Human summaries use **Conservative effect projections** for the risk counts
and **Effect evidence** for their basis. Write/destructive entries carry the
same basis beside the action name; `not pass-eligible` is the existing semantic
assessment's answer, including its identity, binding and authority obligations.
These labels are shared by the CLI, GitHub step summary, report, PR and cold
packet lead. They do not compute a second decision.

| Existing effect status | Human wording | Meaning |
| --- | --- | --- |
| `declared` | reviewed declaration | A reviewed static declaration supports the effect. |
| `structural` | structural evidence | The reader has supporting protocol/provider/scope evidence. |
| `inferred` | provisional: inference | A heuristic suggests the risk; it does not satisfy effect evidence. |
| `protocol_default` | provisional: protocol default | The protocol's conservative default retains risk without proving behavior. |
| `unknown` | provisional: unknown effect | The effect could not be established; the conservative risk remains. |
| `conflicting` | conflicting effect evidence | Claims disagree; the review obligation remains. |
| assessment absent in an older artifact | provisional: evidence unavailable | The old output carries no assessment; absence is not proof. |

**Provisional signals still require attention.** Follow the named evidence
request in the report. Static support for the effect alone does not establish
the agent's identity, deployed wiring or authority. A patch or inventory
suggestion alone does not prove runtime behavior or complete a human review.

## Reading the existing JSON

No new report field or enum is introduced. For each
`action_surface_facts.actions[]` row:

- `effect` is the conservative projection, also carried as
  `semantic_assessment.conservative_effect`.
- `semantic_assessment.effect.status`, `claims[].basis`,
  `claims[].policy_eligible` and `issues[]` describe the supporting evidence.
- `semantic_assessment.pass_eligible` answers the combined semantic question;
  a structural effect can still have an unresolved binding or authority.

Policy severity cannot turn a heuristic claim into typed evidence. Renderers
leave claims, risk tags, severity, pass eligibility and the release decision
unchanged. Runtime behavior is never proven by this static presentation.

The `google_adk_cold_start_agent` sample demonstrates the distinction:
`assemble_case_timeline` retains a `write` projection with `unknown` effect
evidence; `ops.queue_backfill` uses a protocol default; `record_case_outcome`
has a reviewed declaration. Their different labels come directly from the
existing semantic assessments.
