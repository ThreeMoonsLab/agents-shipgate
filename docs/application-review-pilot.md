# Application-diff reviewer pilot

Protocol drafted on 2026-11-16. The product maintainer owns the enrollment and
checkpoint decision. The study coordinator records the frozen inputs
and assigns review tasks. Recruit up to four consenting, non-author reviewers
for two 20-minute review tasks each. The observation window is 28 days from the
first participant's first review. Record any shortfall when that window ends.
See the [maintainer responsibilities](../MAINTAINERS.md).

## Enrollment and frozen inputs

The application-diff route starts on a real PR that changes a Python
application agent built with the OpenAI Agents SDK or Google ADK. Select only
bindings and coverage limits that the released build documents for this
route. A second eligible PR meets the same rule in the participant's ordinary
work during the observation window. Record the eligibility decision before
seeing whether a participant runs the tool.

Before assigning tasks, record the installed wheel's version and SHA-256,
the CLI contract version, the operating environment, exact merge base and
head, and the PR discussion snapshot. Record application scope and CLI
arguments, save the text and JSON from the release, and bind their hashes to
the task. Freeze task assignment, order, eligibility rules, rubric, and
checkpoint thresholds in the private tracker. The operator fetches the exact
refs and sets `BASE_SHA` and `HEAD_SHA` to those full commit IDs.

```sh
agents-shipgate diff --application --base "$BASE_SHA" --head "$HEAD_SHA"
agents-shipgate diff --application --base "$BASE_SHA" --head "$HEAD_SHA" --json
```

A first valid complete comparison produces parseable JSON for the pinned refs
with `comparison_status: compared` and readable reviewer output. Keep
`partial`, `not_established`, CLI refusals, setup failures, and tool errors in
the attempted denominator. Record `not_established` when neither side
establishes a supported application agent. A CLI refusal reports unread refs
or inputs. Score useful recovery separately when a reviewer can identify the
named limitation and take the appropriate next step. Record setup and fetch
time, engine time, and reviewer reading and decision time independently.

Before any external enrollment or delivery, record the owner's dated ruling
on how the released-build value gate applies to the application route.
Follow the applicable gate and obtain participant consent for collection and
publication. Record approval separately for names, source links, and raw
artifacts. [Released-build value gate #830](https://github.com/ThreeMoonsLab/agents-shipgate/issues/830),
[enrollment and consent #653](https://github.com/ThreeMoonsLab/agents-shipgate/issues/653).

## Review task and scoring

Prepare two matched tasks with independent human source adjudication before
review. Each reviewer gets one GitHub diff alone and a different GitHub diff
plus the actual application-diff report. Alternate task, arm, and order across
the four reviewers so each task appears in each arm and each position. Keep
the same time budget in both arms. The adjudicators record the material
changes and limits, the source locations, and the range of defensible
decisions. Save disagreements and their resolution. Use separate development
cases for practice. [Independent evaluation #1158](https://github.com/ThreeMoonsLab/agents-shipgate/issues/1158),
[observed-row decision #1147](https://github.com/ThreeMoonsLab/agents-shipgate/issues/1147).

Score the reviewer's first independent response on four items.

1. Identify the affected agent and the direction of the material change.
2. Cite source evidence sufficient to locate and check the change.
3. State the relevant coverage limitation and treat candidate rows as provisional.
4. Record a concrete decision or fix and the next step that follows from it.

Award one point per item that matches the adjudicated evidence. A four-point
response with a recorded decision or fix counts as first reviewer value in
the report arm. Keep wrong decisions, omissions, abstentions, and missed
deadlines in the results. Record assistance as none, setup help, or
interpretation help, with a timestamp and description. Collect and score the
independent answer before offering interpretation help. Score a subsequent
assisted answer separately.

At the next eligible application PR, record the opportunity date, whether the
reviewer voluntarily selected the report, and whether any prompt, requirement,
or integration produced it. Record whether an existing integration was still
enabled. Keep prompted and enforced uses in their own counts.

## Tracker and checkpoint

| Evidence | Measure and denominator |
| --- | --- |
| Development replay | Count `compared`, `partial`, `not_established`, CLI refusals, and failed runs out of all pinned development attempts. Record release or source checkout and the answer hash. |
| Independent source adjudication | Count completed and disputed labels out of the cases assigned to adjudication. Record labeler roles, independence, scope, and resolution. |
| Application route | Count valid complete results out of route attempts and useful recoveries out of `partial`, `not_established`, or CLI-refused attempts. Record the status and exact limits for each. |
| Actual reviewer value | Count correct decisions out of all assigned tasks in each arm. Report the four-part rubric, wrong decisions, and reading time per arm. Split first value by aid category and include every report-arm task in its denominator. |
| Agent selection | Count selections out of the eligible observed opportunities defined by [#799](https://github.com/ThreeMoonsLab/agents-shipgate/issues/799). Record the route and whether it was selected or prescribed. |
| External reuse | Count unaided voluntary second uses out of reviewers with a verified next eligible PR. Report how many had no second opportunity or unknown observation. Record prompts, requirements, and integration status separately. |

At day 28, **stop** expansion if an adjudicated report made a confident material
misstatement or concealed a relevant coverage gap. If neither stop condition
applies, **continue** with a narrow next cohort only when all four paired reviews
completed, the report arm earned first value in at least three of its four
tasks, its correct-decision count met or exceeded the diff-only arm, and at
least two reviewers with verified second opportunities voluntarily reused the
route. **Narrow** in other cases.
Record the scope that needs a repair or another observable opportunity and
the evidence that would reopen the decision. Publish aggregate denominators,
outcomes, timing, and remaining limitations in the
[results ledger](design-partner-pilot-results.md).
