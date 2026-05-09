# Prompt · Explain a single Agents Shipgate finding to a user

You need to translate one `report.json` finding into prose for a human
who has never read the Shipgate docs. Companion to `agents-shipgate
explain-finding <fingerprint>`, which gives you the structured payload
you'll quote.

This is for the moment when an agent has run a scan, identified the top
finding (via `agent_summary.first_recommended_action.why` or by walking
`findings[]`), and now has to summarize it for a PR comment, chat
reply, or commit message. The user shouldn't have to follow a doc link
to understand what's going on.

## Your task

1. **Get the fingerprint.** Read it from `agent_summary.first_recommended_action.why` if that names a `check_id` and tool, then look up the matching `findings[].fingerprint` in `report.json`. Otherwise pick the highest-severity active finding (`critical > high > medium > low`) and read `fingerprint` directly from that entry.

2. **Run `explain-finding` to get the structured payload.**
   ```bash
   agents-shipgate explain-finding <FINGERPRINT> \
       --from agents-shipgate-reports/report.json --json
   ```
   The output carries:
   - `check_id`, `title`, `severity`, `category` — what the check is.
   - `tool_name`, `tool_id` — the affected tool (may be null for manifest-level checks).
   - `evidence` — the structured evidence the check captured.
   - `recommendation` — the check author's verbatim suggested fix.
   - `agent_action` — `auto_apply | propose_patch_for_review | escalate_to_human | suppress_with_reason | informational`.
   - `metadata` — full `CheckMetadata` (rationale, fires_when, evidence_fields, docs_url) when the check is in the catalog.
   - `explanation` — a deterministic 3–5-sentence prose summary you can quote verbatim or rewrite.

3. **Write the prose for the user.** Three to five sentences, in this order:
   1. **What.** Name the check (`check_id` is fine), the affected tool (`tool_name`), and the severity in one sentence. If the check has no `tool_name`, name what the check examined (e.g. "the manifest", "permissions").
   2. **Why it matters.** Pull from `metadata.rationale` or `metadata.fires_when`. If neither exists, paraphrase the `recommendation`. Avoid verbatim verbose catalog text — translate "limited automation review" into plain English.
   3. **What you'll do (or want).** Map `agent_action` to a concrete next step:
      - `auto_apply`: "I can apply the fix automatically — say yes and I'll run `apply-patches --confidence high --apply`."
      - `propose_patch_for_review`: "There's a suggested patch but the confidence is medium/low (or there's a manual sibling). Want me to show the diff before applying?"
      - `escalate_to_human`: "There's no automatic fix. Here's the recommended remediation: [paraphrase recommendation]. Want me to draft the change for you to review?"
      - `suppress_with_reason`: "If you want to accept this risk, I can add a suppression with reason. What should the reason say?"
      - `informational`: "No action needed; flagging for awareness."
   4. *(Optional)* **Where to learn more.** If `metadata.docs_url` exists, link it.
   5. *(Optional)* **Suppression status.** If `suppressed` is true, mention that — otherwise omit.

4. **Cite evidence sparingly.** Only quote a specific evidence value when it makes the explanation concrete (e.g. naming the broken parameter, the file path from `source.ref`). Do not dump the whole `evidence` dict.

5. **Format for the surface.** PR comments and chat support markdown — use a code span for `check_id` and `tool_name`. Plain text emails should drop the backticks but keep the structure.

## Example

Input (from `explain-finding fp_f092940f62fbb012 --from ... --json`):
```json
{
  "check_id": "SHIP-POLICY-APPROVAL-MISSING",
  "severity": "critical",
  "tool_name": "stripe.create_refund",
  "agent_action": "escalate_to_human",
  "recommendation": "Declare an approval policy or remove the tool.",
  "metadata": {
    "rationale": "High-risk actions need explicit approval before promotion.",
    "fires_when": "Financial/destructive risk exists without approval policy."
  }
}
```

Good prose for a PR comment:

> The release-readiness scan flagged a critical issue: `stripe.create_refund` doesn't declare an approval policy in `shipgate.yaml`. High-risk actions like refunds need an explicit human approval gate before they can ship — without one, an agent could trigger a refund on its own without review. There's no automatic fix here. The right remediation is to either add `policies.require_approval_for_tools: [stripe.create_refund]` (with a reviewer-visible approval trace) or remove the tool from this release surface. Want me to draft the manifest change for you?

Bad prose for the same input:

> Finding `fp_f092940f62fbb012`: `SHIP-POLICY-APPROVAL-MISSING` fired with severity `critical` on `stripe.create_refund`. autofix_safe=false, requires_human_review=true. evidence: risk_tags=[financial_action, destructive]. recommendation: "Declare an approval policy or remove the tool."

The bad version is true but unreadable — it dumps the JSON instead of translating it.

## What NOT to do

- Do **not** quote the structured `explanation` field verbatim if it's robotic. It's a deterministic baseline; rewrite for tone when needed.
- Do **not** fabricate consequences. If the check's `rationale` doesn't say "could trigger a refund," don't say it. Stay grounded in catalog text.
- Do **not** propose `apply-patches` for `escalate_to_human` findings — the user has to decide on the fix manually.
- Do **not** propose adding a `checks.ignore` entry as the default response. Suppression is a real choice, but it's the last resort and needs an audit-trail-quality reason. Use the [`triage-false-positive.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/prompts/triage-false-positive.md) prompt for that workflow.
- Do **not** include the fingerprint string in the user-facing prose unless they specifically asked for it. Fingerprints are agent-to-agent identifiers, not human-friendly labels.

## Verification before sending the message

- The user-facing prose names the affected tool (or what the check examined) at least once.
- The severity is mentioned somewhere (a word like "critical" or "medium-severity" — not just the JSON token).
- The action sentence matches the finding's `agent_action`. If the message says "I'll apply this automatically," `agent_action` must be `auto_apply`.
- No raw JSON dumps in the prose — translate, don't quote.
- If `metadata.docs_url` exists, include it (or link text equivalent).
