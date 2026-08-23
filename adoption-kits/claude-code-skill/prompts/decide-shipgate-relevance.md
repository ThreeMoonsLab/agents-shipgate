# Prompt · Decide whether Agents Shipgate is relevant

You are working in a repo or reviewing a PR and need to decide whether
to propose Agents Shipgate as the next step. The other prompts in
[`prompts/`](https://github.com/ThreeMoonsLab/agents-shipgate/tree/main/prompts)
assume relevance is already established — this one runs **before** that
decision and tells you yes or no with a rationale.

The decision is fully data-driven: it does not depend on prose-reading.
[`docs/triggers.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/triggers.json) is the machine-readable
mirror of the AGENTS.md trigger table; you fetch (or read) it and apply
the rules to the changed file list.

## Your task

1. **Identify the changed file set.** Repo-relative, forward slashes:
   - PR context: `git diff --name-only origin/main...HEAD`
   - Working tree: `git status --short` (uncommitted)
   - User-pasted diff: parse `diff --git a/<path> b/<path>` headers

2. **Fetch the trigger catalog.** Either:
   - **Local repo** (already adopted Shipgate): read `docs/triggers.json` directly.
   - **Remote** (target repo without Shipgate): fetch
     `https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/docs/triggers.json`.
   - The catalog has `schema_version: "0.4"`; match `surface_class` instead of maintaining a parallel path list.

3. **Apply the rules.** Two equivalent options:

   **Option A — read the JSON yourself.** Walk `rules[]`. For each rule,
   evaluate `rule.when` against the changed file list **and** the unified
   diff body — several rules use `diff_contains` predicates (e.g.
   `@function_tool`) that a path-only listing cannot satisfy. The
   predicate vocabulary is documented in `triggers.json` under
   `predicate_vocabulary`; the action precedence is in
   `action_precedence`. See the decision tree below.

   **Option B — call the bundled evaluator** (when Shipgate is installed).
   Use the `--git-diff` flag so paths AND diff body come from git in one
   call; piping `git diff --name-only` alone causes `diff_contains` rules
   (decorators, framework tokens, Action URL) to silently never fire:
   ```bash
   agents-shipgate self-check --json    # confirm install
   python -m agents_shipgate.triggers \
       --git-diff origin/main...HEAD --json
   ```
   For uncommitted changes pass `--git-diff` with no revspec — that
   runs `git diff HEAD` (covers BOTH staged and unstaged tracked
   changes) plus `git ls-files --others --exclude-standard` to add
   untracked file paths. Untracked files contribute paths only; their
   content is not in `diff_text`, so `diff_contains` rules won't fire
   on a brand-new file until you `git add` it (or pass `--diff-text`
   manually). If your repo already has a manifest, also pass
   `--manifest-present` so the `force_run` rule can fire.
   The output shape is `{run_shipgate, dry_run_recommended,
   matched_rules, stop_conditions_fired, stop_conditions_terminal,
   rationale, schema_version, input_status, evaluation_status,
   surface_exclusions}`.
   **`run_shipgate` is tri-state.** `true` and `false` are verdicts; `null`
   means no verdict was published, and there are two ways to get there.
   Never coerce `null` to `false`.
   - `evaluation_status: "not_evaluated"` — the diff could not be read.
     Report the missing input and stop; **never** treat it as "this PR is
     not agent-related".
   - `evaluation_status: "unclassified"` — the diff *was* read in full and
     the catalog classified none (or only some) of the changed files, so it
     cannot say the change is irrelevant. `surface_exclusions.entries[]`
     names the files nobody classified. Follow `next_action.command`, which
     routes forward to the scan: run it and let the scan decide.
   An `input_status` other than `complete` alongside
   `evaluation_status: evaluated` is not a contradiction: evidence that did
   not depend on the missing bytes already proved Shipgate should run — a
   rule matched on the change set, or `force_run` from a manifest that is
   present regardless of the diff. Honor that verdict, read `matched_rules`
   before saying what established it, and say the diff still needs
   recovering.

4. **Emit the decision.** Always reply in this exact JSON shape so
   downstream automation can parse you:

   ```json
   {
     "run_shipgate": true,
     "dry_run_recommended": false,
     "matched_rules": [
       {"id": "TRIGGER-MCP-EXPORT-CHANGED", "action": "run_shipgate"}
     ],
     "rationale": "PR adds tools/mcp-export.json; MCP exports are a tool surface."
   }
   ```

5. **Then act.**
   - If `run_shipgate: true`: run verify preview through a **version-pinned,
     zero-install** runner so a stale copy already on `PATH` can't answer —
     `uvx agents-shipgate@{{ shipgate_version }} verify --preview --json` (or
     `pipx run agents-shipgate=={{ shipgate_version }} ...`). Only fall back to a bare
     `agents-shipgate verify --preview --json` once
     `agents-shipgate contract --json` confirms
     `minimum_control_contract_version: {{ minimum_control_contract_version }}` ({{ shipgate_version }} or newer; `--version`
     reports the CLI version, not the contract floor). Then follow
     [`prompts/add-shipgate-to-repo.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/prompts/add-shipgate-to-repo.md)
     for the first-adoption helper flow, or point the user at the GitHub
     Action (`ThreeMoonsLab/agents-shipgate@v0.15.0`) if they prefer CI.
   - If `run_shipgate: false` and `dry_run_recommended: true`: propose
     a non-mutating scan only — never propose `init --write` based on a
     dry-run match alone. Phrase it as "X may have shifted the tool
     surface; want me to run a read-only scan against the existing
     manifest?" If there's no existing manifest, surface the
     `matched_rules` and let the user choose.
   - If `run_shipgate: false` and `dry_run_recommended: false`: **do
     not propose Shipgate.** Recommend whatever the actual review need
     is (lint, type check, unit test, security scan). Mentioning
     Shipgate when the catalog classified the change and said no is noise.
     This branch requires an actual `false` — see the tri-state rule above.

## Decision tree (when reading `triggers.json` by hand)

```
For each changed file path AND the unified diff body:
    For each rule in triggers.rules:
        if rule.when matches → record (rule.id, rule.action)

stop_fired := every clause in triggers.stop_conditions holds
              (requires running detect first; if you haven't, treat as false)

# Action precedence (highest first), see triggers.json:action_precedence:
if stop_fired AND no force_run/run_shipgate matched: → run = false
elif any action == "force_run":                → run = true   (manifest present)
elif any action == "skip_shipgate":            → run = false  (skip beats run)
elif any action == "run_shipgate":             → run = true
elif any action == "dry_run":                  → run = false, dry_run_recommended = true
else:                                          → run = null   (nothing classified)

# Then: a skip may not rest on files no rule classified.
if run is false and some changed file matched no path predicate
   of any matched rule (and the skip was not stop_conditions):
                                               → run = null
```

Why a matched run rule beats `stop_conditions`: the stop block's premise is
"this workspace is not an agent project", read by `detect` from the working
tree. A matched capability rule is evidence from the *diff*, which can carry
what `detect` never saw — an MCP tool schema in a file whose name matches no
glob, for instance. Evidence against the premise defeats the conclusion.

Why an unclassified file withholds a skip: "no rule matched" is a fact about
the catalog, not about the PR. `TRIGGER-DOCS-ONLY-NEGATIVE` is a real skip —
it classifies every changed file and concludes. `no_match` classifies nothing,
and a `dry_run` rule that matched `requirements.txt` has classified nothing
about the opaque file beside it.

Why `skip_shipgate` beats `run_shipgate`: a brittle `diff_contains` match
(e.g. `@tool` mentioned in README prose) should not override the explicit
"this is a docs-only PR with no tool surface impact" signal.

Why `force_run` overrides `skip_shipgate`: an existing `shipgate.yaml` is
the operational opt-in; even a docs-only PR in such a repo gets scanned
because the cost is low (advisory) and tool-adjacent prose changes can
matter.

## What NOT to do

- Do **not** propose Shipgate based on filename guesses ("looks like an
  AI agent"). The trigger catalog is the source of truth — but "no rule
  matched" is not an answer of *no*, it is the absence of one. Route it to
  the scan.
- Do **not** read `run_shipgate: null` as `false`, anywhere. The two
  withheld states exist precisely because a skip nobody can falsify is the
  failure mode this catalog is designed against.
- Do **not** silently fall back to "yes, run it" when you can't fetch
  `triggers.json`. Surface the fetch failure to the user and ask.
- Do **not** invent rule IDs in the output. Every entry in
  `matched_rules` must come from `triggers.json`.
- Do **not** treat the **negative control** ("update docs only") as a
  reason to propose Shipgate. The `TRIGGER-DOCS-ONLY-NEGATIVE` rule
  fires `skip_shipgate` for a reason — and it covers test-only PRs
  too, not just `*.md`.
- Do **not** propose `agents-shipgate init --write` on a `dry_run`-only
  match. `dry_run_recommended: true` justifies a non-mutating `scan`
  against an existing manifest, nothing more.
- Do **not** rely on bare `--git-diff` for brand-new untracked files
  to fire `diff_contains` rules. Bare flag covers tracked changes
  (staged + unstaged) and untracked file *paths*, but not untracked
  file *content*. `git add` first, or pass `--diff-text` explicitly.

## Verification before reporting

- Output is valid JSON with the keys `run_shipgate`,
  `dry_run_recommended`, `matched_rules`, `rationale`.
- Every `matched_rules[].id` exists in the loaded `triggers.json`.
- If `run_shipgate: true`, the next-step command is named.
- If `run_shipgate: null`, the reply says which of the two withheld states
  applies and never claims the PR is unrelated to agent capabilities.
- If `run_shipgate: false` AND `dry_run_recommended: true`, exactly
  one Shipgate command appears (a non-mutating `scan` against an
  existing manifest) — never `init --write`.
- If `run_shipgate: false` AND `dry_run_recommended: false`, no
  Shipgate command appears anywhere in your reply.
