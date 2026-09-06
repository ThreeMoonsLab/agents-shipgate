# Adopters

Who has said, publicly and on their own initiative, that they run Agents
Shipgate — and exactly what each of those statements is worth.

Agents Shipgate collects nothing. Local-first and static by default: no
telemetry, no analytics, no phone-home, no account, no crash reports
([trust model](docs/trust-model.md)). That stance is deliberate and it has a
price: this project cannot count its own users. This file is that price, paid
honestly. It is the **only** source for any adoption number this project
publishes, and every row in it was put there by the party the row names.

**What an entry proves.** That a consenting party stated, on a date, that they
used Agents Shipgate the way the row says. That is the whole claim. It is not
evidence of continued use, review quality, team size, or willingness to pay,
and the `Use` column is self-reported: nobody verifies it and nobody upgrades
it. Adoption evidence is held to the same bar as capability evidence here —
explicit, attributable and verifiable, or absent.

## Counts

**Counts as of: 2026-09-06.**

- **External adopter entries: 0.**
- **Maintainer dogfooding entries: 1.**

The two counts are reported separately and never added together. A dogfooding
entry cannot satisfy an external adoption claim or an external repeat-use
claim — see [Claims policy](#claims-policy). The zero is published rather than
omitted, for the same reason the pilot publishes its zeros in
[`docs/design-partner-pilot-results.md`](docs/design-partner-pilot-results.md):
an absent number reads as a modest one, and it is not.

## External adopters

| Adopter | Repository | What it gates | Use | Since | Entry |
| --- | --- | --- | --- | --- | --- |

**No external entries yet.** Nobody outside Three Moons Lab has asked to be
listed. That is the count as of the date above, not a rounding of something
larger.

## Maintainer dogfooding

Entries operated by the maintainers. They are listed for completeness and are
never counted as external adoption.

| Adopter | Repository | What it gates | Use | Since | Entry |
| --- | --- | --- | --- | --- | --- |
| Three Moons Lab (maintainer) | [ThreeMoonsLab/agents-shipgate](https://github.com/ThreeMoonsLab/agents-shipgate) | Its own Codex plugin package and marketplace entry, on every pull request — `agents-shipgate.yml` and `agents-shipgate-self.yml`. The Python scanner itself is covered by the ordinary test suite, not by this gate. | advisory CI | 2026-09-06 | [#475](https://github.com/ThreeMoonsLab/agents-shipgate/issues/475) |

That row says `advisory CI`, not `blocking CI`, and the difference is worth
reading. Both workflows do fail the run on `blocked` and `unknown` — but
`main` carries no required status check, so a red run stops no merge. Anyone
can re-check that claim the way it was made: the branch ruleset on `main`
(`gh api repos/ThreeMoonsLab/agents-shipgate/rulesets`) protects against
deletion and non-linear history and requires a pull request, and carries no
`required_status_checks` rule. Under the definitions below that is advisory,
and writing anything stronger in our own row would be the exact failure this
file exists to prevent.

## What the Use column means

Three values, and no others. Each describes what the gate can do at the moment
the entry is written, not how much anyone likes it.

- **`local evaluation`** — run by hand, or from a coding-agent session. No CI
  job runs it.
- **`advisory CI`** — runs in CI on changes and publishes its result, but the
  result does not decide whether the change may merge: the check is not
  required, or the job never fails on a verdict.
- **`blocking CI`** — runs in CI *and* can stop a merge: the job fails on at
  least one merge verdict, and that check is required on the protected branch.

A row states one of these because its author says so. Nothing in this
repository checks that the CI it describes exists, and no maintainer will
raise a row to a stronger tier on inference — not from a badge, not from a
workflow file spotted in the wild, not from a conversation.

## Add yourself

Being counted is an act you perform, never one performed on you. Two paths;
both leave a public, attributable record, which is what makes the count
worth anything.

**The short path — open an issue.** Use the
[Adopter entry](https://github.com/ThreeMoonsLab/agents-shipgate/issues/new?template=adopter_entry.yml)
form. It asks for the four fields that are yours to state; the date is when
the row lands and the `Entry` link is that issue. A maintainer opens the pull
request.

**The direct path — open a pull request** adding one row to the table above.
The `Entry` column needs a link to a public act in this repository, so open
the PR as a draft, then push the row with your own PR's URL and mark it ready.
Move the external count and the as-of date in [Counts](#counts) in the same
PR: a guard fails the build if the counts disagree with the rows, or if the
as-of date is older than the newest entry.

A valid entry has all six fields:

| Field | What goes in it |
| --- | --- |
| `Adopter` | The organization, project or person being counted. |
| `Repository` | `owner/repo` as a link, or the single word `private`. A private-repo entry names the organization and nothing else — no repository name, no counts, no internal detail. |
| `What it gates` | One clause: which capability surface the gate reviews. |
| `Use` | One of the three values above, as they are defined above. |
| `Since` | `YYYY-MM-DD`, the date the entry is added. It is not a start date and not a renewal — an entry is never automatically re-confirmed. |
| `Entry` | A link to the issue, discussion or pull request in this repository where you asked to be listed. |

**Only you may add you.** An entry submitted by someone who does not speak for
the adopter is removed on sight, and a maintainer who cannot tell will ask
before merging. Before merging any row a maintainer checks four things: that
the requester speaks for the adopter, that the `Use` value matches what the
requester actually wrote, that the repository link resolves or the row says
`private`, and that the counts and as-of date moved with the row.

**Removing yourself** takes an issue, a comment, or an email to
`help@threemoonslab.com` saying so. No reason is required, none will be asked
for, and the next published number is restated at the next as-of date. Please
also update the row when your use changes tier or stops — nothing here expires
on its own, which is why every published number is quoted with its date.

## Badge

Optional, for adopters who want to point at the registry from their own
README:

```markdown
[![agent capability review: Agents Shipgate](https://img.shields.io/badge/agent%20capability%20review-Agents%20Shipgate-2f6feb)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/ADOPTERS.md)
```

One badge, deliberately tier-neutral. There is no blocking-CI variant and
there will not be one: a badge is a link, not evidence, and a badge that
implied a tier would be an adoption claim nobody could check. **A badge is not
an entry** — displaying it adds nobody to this file, removing it removes
nobody from it, and no number published by this project will ever be derived
from counting badges.

## Privacy

- **Nothing is collected, ever.** Agents Shipgate does not phone home, and
  this registry adds no exception to that. There is no automatic collection of
  any kind behind this file — no scraping of dependents, forks, stars,
  download counts or workflow files into a row.
- **Every public entry is user-initiated and consenting.** Appearing here
  requires a public act by the adopter, linked from the row.
- **Private repositories are welcome, at organization granularity.** Write
  `private` in the `Repository` column; the row then says who and what tier,
  and nothing about where.
- **Private pilot observations are a separate ledger with separate consent.**
  The design-partner pilot
  ([runbook](docs/design-partner-verifier-pilot.md),
  [results](docs/design-partner-pilot-results.md)) takes three separately
  granted consents — public naming, source links, raw bundles — and none of
  them makes a public adopter entry. A pilot participant who wants to be
  listed here adds themselves, by one of the paths above, as a distinct
  decision.
- **Removal is unconditional**, as described above.

## Claims policy

Rules for any adoption number this project publishes — in the README, the
site, a release note, a talk, or a sales conversation.

1. **No entry, no claim.** Every published adoption number traces to named
   rows in this file. If it cannot be traced to rows here, it is not published.
2. **Every number carries its as-of date.** Counts are quoted with the date in
   [Counts](#counts), never bare.
3. **External and dogfooding are never summed.** They are published as two
   numbers. A dogfooding entry cannot satisfy an external adoption claim or an
   external repeat-use claim, and a claim about "adopters" without a qualifier
   means the external count.
4. **An entry is a dated statement, not a measurement.** It says a party made
   a claim on a day. It does not say they still run it, that a review went
   well, or how much they run it.
5. **The tier is self-reported and never upgraded.** Publish the tier the row
   states, or publish the breakdown; never aggregate three tiers into a
   stronger word like "in production" or "gating".
6. **A badge is not an entry.** Nothing is inferred from a badge — not
   adoption, not a tier.
7. **The pilot ledger stays separate.** The design-partner denominators in
   [`docs/design-partner-pilot-results.md`](docs/design-partner-pilot-results.md)
   answer a different question under different consents. The two are never
   combined, and neither is added to stars, downloads or dependents, which
   measure caches and sentiment rather than use. Neither is participation in
   the historical-case corpus
   ([#511](https://github.com/ThreeMoonsLab/agents-shipgate/issues/511)) an
   adoption denominator: reviewing whether a past change should have been
   stopped is a judgement about the corpus, not a statement about running the
   gate.
8. **Removal is unconditional.** An entry leaves on request; numbers published
   before it left are not retracted, they are restated at the next as-of date.

## How this file is checked

`tests/test_adopters_registry.py` runs in CI on every change. It parses both
tables, fails on a row missing a field, an unknown `Use` value, a
non-ISO or future `Since`, an `Entry` link that does not point into this
repository, this repository appearing as an external adopter, counts that
disagree with the rows, an as-of date older than the newest entry, a claims
rule quietly dropped, a second badge variant, an issue form that has drifted
from the tier vocabulary or stopped requiring consent, and an adoption number
stated elsewhere in the repository that these rows cannot source. The registry
is small enough to check by eye, which is exactly why it is worth checking
mechanically: the
failure mode is not a typo, it is a number that drifts upward one edit at a
time.
