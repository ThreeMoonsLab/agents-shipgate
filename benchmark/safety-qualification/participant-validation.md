# Participant Validation (Gate 2)

The corpus's real-history cases were written and reviewed by real people, and
those people are better judges of *"should this change have been blocked"* than
any rater we can supply. This gate sends each reachable author and reviewer a
one-page case card and two questions, and records what comes back.

Governed by [Amendment 1 of the release evidence policy
decision](../../docs/release-evidence-policy-decision.md#amendment-1--the-pre-10-labeling-protocol-and-the-participant-validation-gate),
which fixes the five pre-registered rules. The short form:

- **This gate does not gate the tag.** It runs after labels are frozen and
  receipts exist; the tag does not wait for responses.
- **Validation, not relabeling.** Frozen labels do not move. A material
  disagreement triggers a case review whose corrections land in the `beta`
  corpus — except a material error found before the tag ships, which may force
  a re-freeze.
- **Reviewers outrank authors.** Both are asked; recorded separately.
- **Two questions, recorded apart.** Label validation and product signal have
  different consumers.
- **Naming needs consent.** Asked inside the outreach message; absent a yes,
  the response is aggregate-only.

## Who is contacted, in what order

For each `real_history` / `rejected_or_reverted` case: first the reviewer whose
decision resolved the PR (approved, requested changes, rejected, or reverted),
then the author. Design-partner cases go through the partner contact.
No bulk mail, no bot comments, no more than one follow-up. A non-response is a
data point, not an invitation to persist.

## The case card (one page, generated per case)

```
Case <id> — <repo>#<PR>  (base <sha7> → head <sha7>)
Profile: <profile>            Origin: <origin>

What the change does (2–3 lines, from the diff, no speculation)

Frozen label: <passed|review_required|insufficient_evidence|blocked>
  security_governance:  <label>  — <one-line rationale>
  framework_tooling:    <label>  — <one-line rationale>
  adjudication:         <only if the two disagreed>

What the verifier concluded, with its top evidence rows
  <decision> — <headline>
  - <subject>: <finding / capability delta row>  (<file:line>)
  - <subject>: <finding / capability delta row>  (<file:line>)

Reproduce it yourself:
  <one pinned, copy-pasteable command>
```

Everything on the card is generated from the frozen corpus entry and the
case's receipt-bound artifacts — nothing is written by hand except the 2–3
line change description, which quotes no one and claims nothing the diff does
not show.

## The message template

Subject: `Your call on <repo>#<PR> — did we judge it right?`

```
Hi <name>,

I maintain agents-shipgate, an open-source, deterministic gate that reviews
what an AI agent is able to do after a code change. We are qualifying a
release against a benchmark of real capability-change PRs, and <repo>#<PR> —
which you <reviewed|wrote> — is one of the 56 cases, at pinned commits
<base>..<head>.

You were there and we were not, so your judgment outranks our labels. One
page attached; two separate questions:

1. The case says this change should have been "<frozen label>". From what
   you knew as its <reviewer|author>, is that the right call? If not, what
   should it have been, and what did we miss?

2. Separately from whether we got it right: would output like this have been
   useful to you when the PR was open?

One consent question: if you reply, may we name you and <repo> in the
published validation results, or should your response stay aggregate-only?

You can re-run the whole thing yourself with the command on the card.
Thank you — a one-line answer to either question is already valuable.

<sender>
```

Two rules the template encodes on purpose: question 1 and question 2 are
never merged, and the consent ask is in the first message, not a follow-up.

## The response log

One row per contact in `participant-validation-log.csv`, committed with
responses redacted to what consent allows:

| column | meaning |
|---|---|
| `case_id` | corpus case |
| `contact_role` | `reviewer` / `author` / `design_partner` |
| `contacted_at` | date of first message |
| `responded` | `yes` / `no` (blank until closed) |
| `label_agreement` | `agrees` / `disagrees` / `qualified` / `no_answer` |
| `disagreement_note` | their alternative label + reason, if any |
| `value_signal` | `useful` / `not_useful` / `mixed` / `no_answer` |
| `naming_consent` | `named` / `aggregate_only` |
| `case_review_opened` | issue ref, when rule 1 triggered |

Aggregate results are reported as *agreement among responders, with n* — never
as a rate over the contacted population, and never with a pre-registered
threshold this sample size cannot support.
