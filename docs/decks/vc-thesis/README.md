# Three Moons Lab — VC thesis discussion deck

A working thesis on release readiness for agentic systems. **Not a fundraising deck** — an
artifact for sparring sessions with founders, operators, and design partners. **15 slides.**

## Files

| File                                | What it is                                                         |
| ----------------------------------- | ------------------------------------------------------------------ |
| `build/deck-editable.pptx`          | **Editable PowerPoint** — text natively reconstructed. Edit copy here. |
| `build/deck.pptx`                   | Image-based PowerPoint with speaker notes (pixel-perfect, copy not editable) |
| `build/deck.pdf`                    | PDF version. Share this if you want zero edit affordance.          |
| `build/slide-01.png` … `15.png`     | Rendered slides at 1920×1080 @2x, for screen / Loom / web          |
| `build/contact-sheet.png`           | One-image overview of all 15 rendered slides                       |
| `build/contact-sheet-editable.png`  | Same overview for the editable .pptx (LibreOffice render)          |
| `build/fragments/`                  | Cropped image fragments embedded in `deck-editable.pptx`           |
| `build_deck.py`                     | Generator for rendered PNGs                                        |
| `assemble.py`                       | Post-build: regenerates contact sheet + PDF                        |
| `build_pptx.py`                     | Wraps PNGs into `deck.pptx` (image-based version)                  |
| `build_pptx_native.py`              | Builds `deck-editable.pptx` (native reconstruction)                |
| `crop_fragments.py`                 | Crops Gödel/FEP diagrams + Slide 8 from rendered PNGs              |
| `slide-08-options/`                 | Three drafts of Slide 8 — V2 won, V1/V3 kept for reference         |

## Two PowerPoint versions — pick the one that matches your edit need

| Edit you want to make                         | `deck-editable.pptx` | `deck.pptx`        |
| --------------------------------------------- | -------------------- | ------------------ |
| Tweak a headline / body copy / kicker         | ✅ Native text       | ❌ Edit Python      |
| Reorder slides, hide, duplicate, add slides   | ✅                   | ✅                  |
| Add / edit speaker notes                      | ✅                   | ✅ (pre-filled)     |
| Resize / move existing shapes                 | ✅                   | ❌                  |
| Restyle colors / fonts globally               | ✅                   | ❌                  |
| Get pixel-perfect to the rendered design      | mostly — slight font drift | ✅ exact          |

**Native reconstruction trade-offs.** Slides 1, 2, 3, 6, 7, 9, 10, 11, 12, 13 are fully
native — every word is a real PowerPoint text run, every card is a real shape. Slides 4, 5,
and 8 embed cropped image fragments where rebuilding them with PowerPoint shapes would lose
intent: the Gödel self-reference loop (slide 4), the Free Energy boundary diagram (slide 5),
and the syntax-highlighted editor + report (slide 8). The headline and body text on those
slides are still native and editable — only the diagram region is an image.

**Font note.** The deck specifies Aptos Display / Aptos / Consolas. PowerPoint Mac/365 has
these by default; older Office may substitute (the substitution is usually fine). If the
fonts are missing, PowerPoint will pick a clean sans fallback automatically.

## Reading guide — how each slide is meant to land

Five-act narrative arc. Acts 2 (Gödel + FEP) use a dark theme as the intellectual core; the rest is brand cream.

### Act 1 — The shift (slides 1–3)

**01 Cover.**
Establishes posture in the subtitle: *"A working thesis — not a pitch."* The viewer should
read this and adjust expectations down from "they're going to ask for money" to "they want
to think alongside me."

**02 The inflection.**
The single highest-leverage slide for category creation. Side-by-side: yesterday's LLM call
vs today's agent loop. The bottom callout — *"every tool change becomes a release event"* —
is the entire wedge in one line.

**03 The new release problem.**
Three negative-space cards (`it is not — software testing / LLM eval / runtime SRE`)
followed by a dark-callout positive definition. Defends the category claim by what it isn't.

### Act 2 — The intellectual core (slides 4–5, dark)

**04 Gödel.**
*Why* this category exists. A sufficiently capable agent cannot self-certify. External
assurance is structural, not optional. Closes off two would-be competitors: foundation
model labs ("we'll add this") and agent self-certification ("the agent can check itself").

**05 Free Energy Principle.**
*Where* the wedge intervenes. Tool calls are the only channel through which internal
uncertainty becomes external side effect. Readiness = bounding free energy at the action
boundary. Pivots naturally into Slide 7's tool-use argument.

> If the audience reads slides 4–5 and doesn't lean in, they're not in this thesis. That's a
> useful filter, not a bug.

### Act 3 — Our thesis (slides 6–7)

**06 The thesis.**
Stack diagram positions Three Moons Lab between agent frameworks (above) and tool surfaces
(below), distinct from adjacent infra (eval, observability, MCP gateways). Right side: the
thesis stated plainly.

**07 Why tool-use is the right wedge.**
Three cards — action boundary, most formalizable, highest-leverage risk surface — backed by
the academic landscape (AppWorld, ToolEmu, AgentDojo, τ-bench). Dark callout closes with
the "wedge logic" criteria: any one of those misses, and tool-use isn't the right cut.

### Act 4 — The product path (slides 8–12)

**08 + 09 — DECLARED → DETECTED diptych.**
The most concrete pair. Slide 8 shows the team's actual source files: `refund_agent.py`
(the OpenAI Agents SDK wiring) on the left, `shipgate.yaml` (the release contract) on the
right. Slide 9 shows what shipgate found on those files: BLOCKED, 2 critical, 14 high, real
check IDs. The narrative spine sits between the two slides: the manifest's
`prohibited_actions` literally says *"issue refund without approval"* (slide 8), but
`stripe.create_refund` ships with no approval policy declared (slide 9). The audience
connects the two slides themselves — that's the whole point.

**10 How the release contract gets written.**
Anticipates "wait, where does shipgate.yaml come from?" — a question that always comes up
after slides 8–9. Four steps: SCAFFOLD (auto via `agents-shipgate init`) → AUTHOR (human
fills declared_purpose, prohibited_actions, policies, scopes) → SCAN → ITERATE in PR / CI.
Bottom row makes the auto-vs-human split explicit: scanner can detect tools, only humans
can write intent. Optional Terraform analogy if they push: "prose policy → policy as code."

**11 Phase 2 & 3 — Sandbox → Trace.**
Phase 2 turns Phase 1 unknowns into experimental evidence (sandbox, failure injection,
prompt-injection harness). Phase 3 turns one-time reports into continuous state (trace
ingestion, replay, regression detection). The footer line is deliberate humility:
"Phase 1 ships now. Phase 2 and 3 are deliberate land-and-expand."

**12 The compounding asset.**
The single most important slide for category vs feature framing. Three phases are the same
evidence corpus unfolding across timescales — failure taxonomy, policy library, trace
schema all compound with every user. Anti-feature defense: a GitHub Action lint can't
compound; this can.

### Act 5 — Where I am, what I want (slides 13–15)

**13 What I'm validating now.**
Three open hypotheses, each paired with the proof being sought. Closes with a deliberate
disclaimer of what's *not* claimed (PMF, runtime safety certification, foundation-model-lab
proof). This slide signals intellectual honesty more than any other.

**14 10-year north star — medical record for every agent.**
Healthcare-for-agents metaphor placed late so it doesn't dominate framing. Today =
pre-release exam → Year 2 = stress tests → Year 3–4 = vital signs → 10-year = medical
record across the lifetime of every agent. The bottom callout grounds it: "Today we build
the first instrument."

**15 What I'm looking for.**
Not "what we want from you," but "what would help right now, in order." Sparring partners
(thinking partners), design partners (the most valuable ask), capital optionality (later).
The closing italic line — *"infrastructure to make their entry into the world
accountable"* — is the only place the deck declares its philosophy directly.

## Notes for delivery

- **For Prateek-style first conversation**: walk slides 1, 2, 3, 6, 7, 8, 9, 10, 13, 15 (~10 slides).
  Skip 4–5 unless they lean philosophical. Skip 11–12 unless they push on category-vs-feature.
  Skip 14 unless they ask about long-term vision.
  Slide 10 (workflow) is the answer to "where does shipgate.yaml come from?" — usually the next question after Slide 9.

- **For a written share** (email, doc): send the full PDF. Slide 8 carries enough specificity
  to ground the abstract argument; slides 4–5 give the deck intellectual identity.

- **For Loom / async**: walk every slide. Watch for which slides someone replays or pauses
  on — those are signal.

## Re-run

```bash
cd docs/decks/vc-thesis
python3 build_deck.py          # renders 12 slides + copies slide-08 from v2
python3 assemble.py            # contact sheet + PDF
python3 crop_fragments.py      # cuts diagram fragments for the editable pptx
python3 build_pptx.py          # image-based deck.pptx with speaker notes
python3 build_pptx_native.py   # editable deck-editable.pptx (recommended)
```

Requires Python 3, `playwright` + `chromium`, `markdown`, `Pillow`, `Pygments`, `python-pptx`.
