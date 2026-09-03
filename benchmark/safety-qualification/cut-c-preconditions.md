# Cut C — the four preconditions

[Issue #508](https://github.com/ThreeMoonsLab/agents-shipgate/issues/508) names
four things that must be cleared and recorded before the calibration round
runs. This file is the record. It is maintainer material and never a rater
input.

Two of the four are cleared here. The other two need the owner: one is a
sign-off that only the owner can give, and one needs a credential and a
working binary that this repository cannot supply.

| # | Precondition | State |
|---|---|---|
| 1 | Confirm [`LABELING.md`](../miner/LABELING.md) | **Open — owner sign-off** |
| 2 | The Claude harness is unverified | **Verified except for one live session** |
| 3 | The OpenAI-family harness is unverified | **Open — the local `codex` cannot execute** |
| 4 | Decide the packet contents | **Decided — the base tree does not ship** |

---

## 1. Confirm `LABELING.md` — open

Nothing here can clear this one. Amendment 1 names
[`benchmark/miner/LABELING.md`](../miner/LABELING.md) as *the* rater input, so
the owner's sign-off on its current text is what makes a label produced against
it admissible.

What the owner is signing off on has not changed since #489 restructured it:
a rater-facing four-way rubric on top, the miner process below the horizontal
rule, and `tests/test_labeling_guide_is_rater_safe.py` keeping every inventory
candidate and every `SHIP-` check id out of the rater half.

**The `review_required` / `insufficient_evidence` rule in that file is a first
draft and is deliberately not amended here.** #508 orders the work so that the
calibration round tests the draft and the round's findings are what land. A
correction written without the round would be a guess dressed as evidence, and
it would be the guess 56 labels are then produced against.

## 2. The Claude harness — verified, except for one live session

The smoke run in #489 failed closed on an expired OAuth token, which left
everything behind that failure unchecked. It is still expired
(`claude login`), and `ANTHROPIC_API_KEY` is unset, so a *labeled* session is
still not possible here. Everything that does not need a credential now is
checked, against `claude 2.1.126` on `darwin/arm64`:

- **Every flag the harness passes exists**, with the spelling it uses:
  `--bare`, `--output-format stream-json`, `--verbose`, `--tools`,
  `--allowedTools`, `--disallowedTools`, `--permission-mode dontAsk`,
  `--strict-mcp-config`, `--setting-sources`, `--no-session-persistence`,
  `--session-id`, `--model`.
- **The restrictions take effect**, confirmed from a live session's `init`
  event: `tools` is exactly `["Glob","Grep","Read"]`, `mcp_servers` is empty,
  and `permissionMode` is `dontAsk`. With `--setting-sources ""` the session
  loads no user, project or plugin skills and no plugins, and registers no
  plugin hooks; the CLI's own bundled skills stay registered, but they are
  unreachable, because the `Skill` tool is not one of the three tools.
- **`_encoded_project_dir` matches the CLI.** The runner refuses a shared
  `HOME` that already holds auto-memory for the packet's path, which is only a
  check if it computes the same directory name the CLI does. For a known
  working directory the CLI reported `memory_paths.auto` under exactly the name
  `re.sub(r"[^A-Za-z0-9]", "-", cwd)` produces.
- **An unauthenticated session produces no label.** The 401 arrives as a
  `result` event with `subtype: "success"` and `is_error: true` — a shape that
  would pass a `subtype`-only check. `claude_final` gates on `is_error` as
  well, so it refuses. This is the one thing the #489 failure did establish,
  and it is now covered rather than incidental.

**Still needed:** one live session on a real packet, once the token is
refreshed. That is the only step between here and a Claude-family label.

## 3. The OpenAI-family harness — open, and not for the reason recorded

#489 recorded the cause as "the local codex npm install has an empty vendor
binary directory". That is a symptom. The installed `@openai/codex@0.85.0`
tarball is intact in the npm cache and its `darwin/arm64` binary extracts
cleanly and passes `codesign -v` — but it will not run:

```
$ spctl -a -vv -t execute .../vendor/aarch64-apple-darwin/codex/codex
.../codex: CSSMERR_TP_CERT_REVOKED
```

**The signing certificate for this build has been revoked**, so macOS kills the
process at exec with `SIGKILL` and no output. That is why the flags in
`run_rater.py` could only be written from memory, and it is why an empty vendor
directory was a plausible-looking diagnosis.

**Remedy — the owner's, because it changes their environment:** install a
current release (`0.85.0` is installed, `0.153.0` is current) and authenticate:

```bash
npm install -g @openai/codex@latest && codex login
```

**Then re-verify the flags before trusting them.** They were written against
`0.85.0` from memory, and the gap to a current release is wide enough that
`codex exec --sandbox read-only -C <dir> --json --skip-git-repo-check --model
<m> -` and `_ISOLATED_CODEX_CONFIG` must both be checked against the installed
CLI's own `--help`, not assumed.

`run_rater.py` now probes the CLI before it hands over a packet
(`--check-cli`), so each way this can fail says which remedy it needs — a
missing binary, a launcher whose vendor directory is empty, and a binary macOS
refuses to load are three different problems that used to surface as one
confusing failure late in a run:

```bash
python benchmark/safety-qualification/rater/run_rater.py \
  --family openai --role security_governance --check-cli
```

## 4. The packet contents — decided: the base tree does not ship

**Head tree plus diff, as today. No base tree.**

The reasoning the packet builder already recorded stands: the head tree is the
state the decision is about, and a rater who has the diff can reconstruct the
base for any line the change touches. Shipping the base tree as well would
double the packet, give the rater two trees to keep straight, and answer a
question the diff already answers.

What that argument quietly assumed is that `diff.patch` is a complete textual
description of base → head and that `repo/` is the commit's tree. **Neither was
true**, and the base tree would not have fixed either, because the same
mechanism subtracts from a base tree too. Both readers obey `.gitattributes`
*from the tree under judgement* — that is, from the change being labeled:

| What | Was | Now |
|---|---|---|
| `export-ignore` on a path | `git archive` dropped it, so `repo/` was missing files and `MANIFEST.json` hashed only what survived | the tree is read with `ls-tree` + `cat-file`, which consult no attribute |
| `-diff` or `binary` on a path | `git diff` reduced an ordinary text change to `Binary files … differ` | `--text --no-textconv` renders it; a `Binary files` marker that survives refuses the build |
| `diff=<driver>` textconv | the diff showed the driver's rendering | `--no-textconv` |
| a genuinely binary change | shipped as "differ", silently | refuses the build, naming the paths |
| a submodule the change moves | shipped as a gitlink hash; content in neither tree | refuses the build, naming the paths |
| `diff.context`, `diff.noprefix`, `diff.algorithm`, `core.abbrev`, `diff.orderFile`, `diff.renames` | the operator's `~/.gitconfig` decided the diff's bytes | every one pinned with `-c`, plus `--full-index` |
| `diff=<driver>` on a path | the tree's own choice of funcname pattern decided the text after every `@@` | the diff is read through a bare git dir whose `info/attributes` says `* !diff` |

The `-diff` case is the one that matters most for this corpus. Most of what the
rubric calls `blocked` is a **removal** — an allowlist that no longer bounds, an
approval step deleted — and a removal is visible only in the diff. A repository
could hide exactly that, in an ordinary text file, and the packet would still
build and still verify against its own manifest.

Two more consequences, from the same cause and its neighbour. `git diff` read
attributes from the clone's **worktree**, so the same two pins produced
different `diff.patch` bytes depending on which commit the clone happened to
have checked out. And the operator's own git configuration was never pinned at
all: `diff.context=7` turns a 13-line patch into a 21-line one, `diff.noprefix`
rewrites every header, `core.abbrev` widens every `index` line. Either would
put `MANIFEST.json` — and every `diff.patch:<line>` a rater cites for an
adjudicator to re-read — at the mercy of whose machine built the packet.

Flags alone do not finish that job. `--text` and `--no-textconv` answer the
attributes that *hide* content; they do not answer `diff=<driver>`, whose
funcname pattern chooses the text printed after every `@@`, and git's built-in
drivers need no configuration for a tree to select one. So the diff is read
through a throwaway **bare** git directory that alternates to the clone's
object store and carries one line, `* !diff`, in `info/attributes` — which
outranks every `.gitattributes` in every tree, and which has no worktree to
read one from in the first place. Nothing is written into the operator's clone.
Between that and the pinned configuration, the diff is a function of the two
pins alone.

**The one cost of this decision.** A case whose change touches genuinely binary
content, or a submodule, is now refused rather than silently shipped with a
hole. That is deliberate — an incomplete packet produces a label that looks
admissible and is not — but it means such a case has to be re-sourced or
handled by the owner explicitly. The refusal names the paths so that decision
can be made without re-deriving it.

---

## One thing found while clearing these, and closed

**Amendment 1 condition 1 — two model families — was enforced by nothing.**
`SafetyCorpusCaseV1` requires the two primary `reviewer_id` values to differ,
and two sessions of one family differ anyway: the session uuid is part of the
id. So a round run entirely on one family would have produced 56 cases that
validate, a κ that partly measures a model agreeing with itself, and a floor
easier than the base decision intended — with nothing between that and the
published artifact except the operator remembering.

`run_rater.py` now refuses a run whose sibling role already holds a label for
the same case from the same family, and it refuses **before** launching, so the
mistake costs no session. The label record is what makes this answerable: it
names the family, which `reviewer_id` alone does not oblige it to.

It can only compare against a sibling it can find, so **both roles of a case
must be run into one `--out`**. Rather than be silent when they are not, each
label records `family_independence`: `"unchecked"` when there was nothing to
compare with. The first role of a case is legitimately `unchecked`; a case
whose *both* records say so is one where nobody ever compared — which a freeze
step can see and an operator's memory cannot.

---

## What is still owner-gated after this

Beyond preconditions 1 and 3, the round itself cannot be run by one assistant:
Amendment 1 condition 1 requires the two roles on **different model families**,
and condition 2 makes any session that has read the strata inventory — this one
included — inadmissible as a rater. The harness launches fresh, isolated
sessions precisely so that the operator's own contamination does not reach
them; what it cannot supply is the second family.
