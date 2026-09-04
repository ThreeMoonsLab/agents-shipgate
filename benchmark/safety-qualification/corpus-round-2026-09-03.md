# Cut C — the corpus round, 2026-09-03

The first attempt at the 56 × 2 blind primary labels. **It did not reach the
bar**, in two independent ways, and both are recorded here rather than worked
around. Labels and transcripts are on the owner's machine at
`/private/tmp/cutc-host/runs/`; nothing about them is committed.

## How it ran

| | |
|---|---|
| `security_governance` | `claude` — `claude-opus-5`, CLI 2.1.259 |
| `framework_tooling` | `openai` — `gpt-5.6-sol`, `codex-cli` 0.153.0 |
| Guide | the corrected `LABELING.md`, all five rulings, byte-identical in every packet |
| Host | a `deploy.py` deployment carrying no answer-stating file; every label records `host_isolation: no answer key on host` |
| Mode | `--home-mode shared`, **no** `--working-material` — these are corpus labels |
| Sharding | by **case**, four shards, so both roles of a case share one `--out` and `claim_family` can compare |

96 admissible labels over 48 cases, each with a content-addressed transcript.

One session was refused and re-run: the shared-mode memory guard found
`~/.claude/projects/<packet path>` already present, left by an earlier smoke
run at that same path. That is the guard working; the case was re-run from a
fresh packet path, same manifest, same `case_id`.

## Blocker 1 — only 48 of the 60 slots can be a packet at all

| kind | slots | buildable |
|---|---|---|
| external PR, both pins resolve | 33 | yes |
| construction with `base/` and `head/` | 15 | yes |
| **shipped sample** | **12** | **no** |

A shipped sample under `samples/` is a **single tree**. It has no `base/` and
no `head/`, so it is not a change, and the rater packet is defined as head plus
the diff that produced it. `LABELING.md` opens "You are labeling one change";
for these twelve there is none.

That is not a tooling gap that more code closes. These slots are **cold-start**
cases — the gate runs `scan` on a state, not `verify --base` on a change — and
making them ratable needs two things this round did not have the authority to
invent: a packet form with no `diff.patch`, and a rubric section that asks
"what should a correct gate do with this repository?" rather than "with this
change". Both change what the corpus measures, so both are the owner's.

**48 < 56**, and the per-decision floors (13 `passed`, 14 each of the other
three) cannot be met either. The corpus cannot be completed until this is
settled.

## Blocker 2 — κ = 0.6111, against a floor of 0.80

```
raw agreement 0.708   expected 0.250   Cohen's kappa = 0.6111
```

| decision | claude (`sg`) | codex (`ft`) |
|---|---|---|
| `passed` | 14 | 14 |
| `review_required` | 18 | 12 |
| `insufficient_evidence` | 5 | 13 |
| `blocked` | 11 | 9 |

Adjudication does **not** repair this. κ is a property of the two blind primary
labels, and Amendment 1's third identity resolves disagreements into final
labels without changing what the primaries were.

### The disagreement is one ambiguity, and it is one this round created

Fourteen cases split. Six are purely `review_required` ↔
`insufficient_evidence`; collapsing that one line into the miner's
`needs_human` takes agreement to 0.833 and κ to 0.7322 — closer, still short,
so the line is most of the problem but not all of it.

Codex reached for `insufficient_evidence` 13 times to claude's 5, and on six of
those claude said `review_required`. The rationales agree on the facts and
divide on one question:

> A tool that the diff **registers by name**, whose endpoint and credential you
> can cite, but whose *advertised operations* live outside the packet — is that
> a capability you can name, or a surface that cannot be established?

Three examples, all the same shape: four Apigee API Hub toolsets plus a Secret
Manager key; an `McpToolset` pointed at `https://mapstools.googleapis.com/mcp`
plus a new `GOOGLE_MAPS_API_KEY`; CrewAI flows whose Gmail and Trello tools are
imported from dependencies. In each, claude named the capability and said
`review_required`; codex said the operations could not be established and named
what would resolve it — which is exactly what ruling 3 asks of an
`insufficient_evidence`.

**Both are following the guide, because the guide says both.** Ruling 1 rewrote
`review_required` as "adds, widens, or unguards **a capability you can name**".
The `insufficient_evidence` list was not revisited, and it still reads:

> - an integration is **mounted by name** and its capabilities live somewhere
>   the repository does not include;

Those two rules now overlap on the single most common shape in real history.
This is the same structural defect as the round-1 finding that produced ruling
1 — a rule rewritten on one side of a line without the other side being brought
with it — and it was introduced when the rulings landed, not found by them.

### The ruling this needs

> When the diff registers a tool by name and its endpoint or credential is
> citable, but its advertised operations are not in the packet — which label?

Under ruling 1's own principle — the gate's deliverable is the capability
delta, and a `review_required` must hand the person a **named** capability —
the answer looks like `review_required`, with `insufficient_evidence` reserved
for the case where there is no name to give at all, only the place the surface
left view (`tools=build_tools(load_profile())`, `cal-6`). That would mean
striking or narrowing the "mounted by name" bullet.

It is not this document's call. It decides what 48 labels mean, and the labels
have to be produced again against whatever it settles.

## What is not proposed

Moving the κ floor, or adjudicating toward `strata-inventory.csv`'s
`target_decision`. The target was chosen with the engine's verdict in view for
every `miner_label` row and is a sourcing guess, not the answer; the blind
primaries are the ground truth, and #508 is explicit that a corpus which cannot
meet the bar is a corpus problem.
