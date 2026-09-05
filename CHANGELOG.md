# Changelog

## Unreleased

- **Four lexer defects in the MCP registration reader, fixed in both
  implementations.** (#485 review) Found reviewing the zero-install port; all
  four were in the reader #431 shipped, so the port had copied them rather than
  introduced them. Two invent a tool name, which is the one outcome a reader of
  a *name* cannot afford, and two lose a whole file's surface:

  - A `${…}` holds code, so a brace inside a string, comment, regex or nested
    template is not a structural brace. ``const msg = `brace: ${"{"}`;`` left
    the substitution open and consumed the rest of the file as one unterminated
    template — every registration after that line gone, and a workspace
    declaring an MCP dependency reported as "not an agent project" over a brace
    in a string.
  - A line break ends a JavaScript initializer only when what follows cannot
    continue the expression. `static toolName = "safe"` with `+ "_delete"` on
    the next line published `safe` at `medium` confidence for a tool the server
    registers as `safe_delete`.
  - The regex heuristic now resolves the keyword in front of a slash from the
    *masked* source. Read from the raw text, a comment between `if` and its
    condition hid the keyword, the slash was read as division, and the pattern
    was scanned as code — reporting a tool invented out of a regex body, which
    is precisely what masking exists to make impossible.
  - A backslash before CRLF is one line continuation, not `\r` plus a line
    break. The identical file resolved its registration on a Unix checkout and
    lost it on a Git-for-Windows one.

  Each is an expected-result case in `tests/mcp_idiom_corpus.py`, so both
  readers are pinned to the corrected behaviour rather than to each other's
  agreement, and the CRLF sweep now has a continuation case that actually
  exercises it. The three vendor servers this input exists for are unaffected —
  61, 114 and 114 tools before and after.

- **The zero-install detector reads MCP registration sites, so it stops
  telling vendor MCP server maintainers to stop.** (#485) `tools/shipgate-detect.py`
  is the documented first command run against a repository that has *not*
  adopted Shipgate — which is every repository #431 was about. #431 taught the
  installed CLI to read a tool's name out of a TypeScript or Go registration
  site; the script did not gain it, so the two disagreed on the one question
  the script exists to answer: `mongodb-js/mongodb-mcp-server` (61 tools),
  `github/github-mcp-server` (110) and `grafana/mcp-grafana` (114) were agent
  projects to the CLI and "Stop, not an agent project" to the script. The
  masking lexer, the five idioms, the path predicate, the dependency gate and
  the export-precedence rule are now all in the script too, stdlib-only.

  Porting a load-bearing matcher means a second implementation of it, which is
  this repository's recurring bug class. What makes it affordable is that the
  two are not allowed to become *different* implementations: every case either
  reader has ever been asked about now lives once in `tests/mcp_idiom_corpus.py`
  — every idiom's positive sample, the whole adversarial sweep, the path
  predicate's cases and both escape grammars — and both readers are driven
  through all of it, compared site by site including each site's byte span.
  `samples/mcp_source_only_server` puts the route inside the existing
  `samples/` parity sweep, nine constructed workspaces pin the branches around
  it (covering export, partial export, wildcard export, no dependency, no
  resolved registration, test-only registrations, two registration
  directories), and `test_framework_vocabulary_names_every_cli_omission` now
  passes with an empty `known_omissions`.

  One defect surfaced while porting and is fixed in both: with no MCP export in
  the workspace at all, `_covering_export` returned every resolved name as
  "uncovered", and the caller renders a shortfall as *"An MCP tool export is
  also present and does not name N of these registrations"*. A server whose
  surface exists only as source is the population this input was built for, so
  that claim about a file that does not exist was published into the adoption
  evidence for every one of them.

- **No corpus case is graded against `insufficient_evidence` any more, and four
  `blocked` cells hold one case instead of two.** (#520, #508) A verdict exists
  to route a change somewhere: `passed` merges, `review_required` hands a human
  a named capability, `blocked` stops. `insufficient_evidence` routes nowhere —
  it is what the gate says when *its own* extraction failed, which is a
  statement about shipgate rather than about the change. So it cannot be the
  answer to "what should a correct gate do here?", and both named release
  policies stop demanding cases that expect it: `beta` goes from 28 strata /
  100 cases to 21 / 80, `pre_1_0` from 28 / 56 to 21 / 38. The value stays in
  the enum, the verifier still emits it, and it is scored as a miss against
  whatever the case expected — a coverage failure, which is what it is.

  The first blind labelling round is what settled it. All four cases where both
  independent raters chose `insufficient_evidence` had named a capability the
  diff introduced, so they could have decided; one slot sourced as
  `insufficient_evidence` was placed at `blocked` by both raters; and 12 of the
  15 slots for the outcome had been sourced from the engine's own verdict.

  The same round measured a second thing: of the seven profiles, only four
  produce a real-world `blocked` case at all, and each produces exactly one. So
  `openai_agents_sdk`, `langchain_crewai`, `google_adk` and
  `coding_agent_trust_roots` now ask for one `blocked` case rather than two.
  Filling those cells to two is reachable today only by building four more
  constructions, and a cell filled with constructions measures our imagination
  rather than the world. **No rate moved.** Every exact-match floor is still
  production's rate applied to the population it governs, rounded up —
  `minimum_blocked_exact` falls to 10 because there are 10 `blocked` cases, at
  the same 100% demand — and the origin floor stays 40% of the corpus, which is
  why holding it at a fixed *count* was refused: that would have raised
  production's demand to half the corpus as a side effect of deleting a
  decision. `docs/release-evidence-policy-decision.md` § Amendment 3 records
  the ruling, and `benchmark/safety-qualification/strata-inventory.md` records
  the per-cell evidence for the scarcity.

- **A preview wheel now reports one version, not two.** (#491) The first
  published preview stamped `pyproject.toml` and not
  `src/agents_shipgate/__init__.py`, so its METADATA said
  `0.16.0+preview.20260902.gcc59410` while `--version` said plain `0.16.0` —
  the exact confusion the local version segment exists to prevent. `doctor` was
  collateral: comparing `installed_version` against `imported_version`, it
  reported `installed_version_differs` and told the user two copies were
  shadowing each other, on an environment holding one.

  The build now stamps both sites and proves each with its own round trip over
  a closed file set, but the durable guard is on the artifact: it opens the
  wheel it just built and requires `METADATA: Version` to equal the packaged
  `__version__`. That fails on any future divergence however the stamping is
  written. A companion test pins the committed tree to exactly two version
  sites that already agree, so a third one cannot be added and silently left
  unstamped.


- **Releases run on a cadence, and work that cannot be tagged can still be
  installed.** (#491) 81% of this changelog had never reached a user: 5,039 of
  6,242 lines sat under `## Unreleased`, two milestones were complete and
  untagged, and the newest published build was 56 days old. The cause was not
  the pipeline, which is sound; it was that releases were gated on a single
  expensive artifact (#456), so they happened when that artifact happened.

  Three things change. **The cadence is a policy with a number** —
  `docs/release-runbook.md` § Cadence fixes a 30-day interval and a 45-day
  defect threshold, and `scripts/release_cadence.py` prints days-since-release
  into every CI job summary, warning once the interval lapses. It warns rather
  than fails: a red check would fail whichever unrelated change arrived after
  the interval lapsed, and that author cannot cut a release.

  **An unqualified preview channel exists**, and the finding that admits it is
  recorded in `docs/release-evidence-policy-decision.md` § Amendment 2 —
  including the cases that would have made it inadmissible. `release-preview.yml`
  publishes a GitHub pre-release at `preview-<version>` carrying one wheel,
  built from a commit CI has already accepted using the release's own
  hash-locked toolchain. Five properties keep it from being read as a release,
  each with a negative control: a PEP 440 **local** version segment, which a
  public index must refuse — so an unqualified build can never consume the
  `0.16.0` that PyPI's immutability would then make permanent; a ref outside
  the `v*` trigger namespace; a separate workflow file, so `stage` cannot find
  it when it looks for the mandatory rehearsal; no qualification artifact, so
  `verify_safety_qualification_release.py` rejects it on both the missing
  artifact and `tag == v<version>`; and `--prerelease`, so it is never
  `Latest`. Nothing in the release path was widened to accommodate it —
  `build_manifest` still requires `tag == v<version>`, and a preview simply
  produces no candidate manifest.

  **The release note is separated from the record.** `## Unreleased` had grown
  to 338,932 characters — 2.7x the 125,000-character limit the release body is
  checked against — so no tag could have been cut from it whatever else was
  green. `CHANGELOG.md` now carries one line per change, and the full reviewed
  prose moved verbatim to `docs/changelog/<version>.md`. Nothing was dropped:
  all 165 entries are byte-identical in the record and all 165 have a line in
  the note.

  This does not shorten what `v0.16.0` still needs. The signed qualification
  artifact (#456), the four `SAFETY_QUALIFICATION_*` variables and a real
  `signer_identity` remain preconditions, and a preview is not evidence toward
  any of them.

## 0.16.0

Two milestones of engine work: the evidence-first declaration workflow, the
capability delta as a standalone attestation, a route for MCP servers whose
tool surface exists only as code, the compact agent-control envelope across
every setup command, and the release-integrity pipeline that binds a published
wheel to the commit it came from.

**The full reviewed prose for every entry below is in
[`docs/changelog/0.16.0.md`](docs/changelog/0.16.0.md).** This section is the
release note; that file is the record. Nothing was dropped in the split.

**Migration notes:** 16 entries in [`STABILITY.md`](STABILITY.md) apply to this
release. Read them before upgrading from 0.15.0 — several change a published
schema or a contract version.

### Highlights

- **The capability delta is a standalone, independently verifiable
  attestation.** `verify` writes an in-toto Statement whose subject is the
  reviewed tree, and `tools/verify-capability-delta.py` re-implements its 31
  rules using nothing of ours. (#470, #469)
- **Declarations are evidence-first.** The scanner asks only what it cannot
  prove, pins every answer to the evidence behind it, and a declaration that
  contradicts what was observed is no longer accepted in silence. (#409, #410)
- **MCP servers that ship no tool export now have a route.** The official
  MongoDB and Grafana servers were reported as *not an agent project*; the new
  `mcp_server_source` input reads the tool name at its registration site.
  (#431)
- **One control envelope across the whole adoption walk.** `init`, `detect`,
  `doctor`, `preflight` and `verify` answer "what may I do next?" with one
  compact object instead of four artifacts and a guess. (#322, #323, #333,
  #339)
- **A first adoption no longer blocks itself.** The absent-input class, the
  monorepo manifest routing, the scaffold disclosure and the launcher fixes
  close the loop-breakers found in three adoption walks. (#334, #363, #384,
  #387, #389, #441)
- **Human-facing output names subjects, not identities.** Findings are grouped
  by the thing they are about, every evidence gap labels its tool the way a
  reader can use, and no surface prints a raw digest at a person. (#329, #364,
  #403, #433)
- **The release pipeline proves what it publishes.** Source-to-wheel byte
  binding, separated verification and publication with a recoverable
  transaction, a non-publishing rehearsal path, and a signed SBOM scoped to
  the shipped wheel. (#342, #343, #344, #345, #355, #356)
- **A `0.x` tag has an evidence bar it can meet**, and it is a smaller corpus
  rather than a weaker judgement: zero unsafe auto-passes, the κ floor and the
  holdout fraction are unchanged. (#341)

### Since `0.16.0b7`

- The capability delta is now a standalone attestation any consumer can verify. (#470)
- An MCP server whose tool surface exists only as code now has a route. (#431)
- External PRs can now be evaluated without mutating tracked project files. (#326)
- Verifier and evidence explanations now preserve the fact that produced them. (#436, #396, #414, #420)
- The pre-1.0 qualification corpus now has a committed sourcing plan. (#456)
- The determinism boundary is now a published specification, generated from the code. (#473)
- One capability schema, frozen before either surface that will ship it. (#469)
- Replayable incident fixtures turn first-contact activation into a verifiable product path. (#471)
- Reviewed risk overrides no longer masquerade as scan observations. (#460)
- Capability delta now answers the reviewer’s question in subjects, without changing its machine contract. (#437, #439)
- Changed action declarations have a compact reviewer attestation. (#428)
- Cold-reader artifacts now lead with what the agent can do. (#463)
- The #424 repair loop is now pinned by a committed artifact. (#424)
- MCP clients can now see when a server's reassuring annotation contradicts the evidence beside it. (#462)
- The declaration questionnaire is now pinned by something committed. (#425)
- A reviewed risk tag is the manifest refining its own row, not source evidence contradicting it. (#424)
- A `0.x` tag now has an evidence bar it can actually meet, and it is not a weaker judgement. (#341)
- One control vocabulary reaches both streams, and the adoption walk composes end to end. (#323)
- The declaration continuation: a drafted proposal can now reach the person it was drafted for. (#429)
- The route is reachable on a first adoption, and states the only order the protocol allows. (#429)
- The declaration route is reachable on a first adoption. (#429)
- `init` no longer writes a source type it guessed, and says when the block it wrote is a scaffold. (#441)
- The same MCP server declared in two files is one capability, reconciled. (#403)
- The report's `Root agent:` line names the agent instead of hashing it. (#329)
- The loader-contract failure now offers a way forward.
- A new evidence gap now says which subject left the analysed surface. (#433)
- A published tool surface is one reviewed declaration, not one row per tool. (#432)
- A coding agent can now answer the declaration questions the scanner already knows the answers to. (#410, #409)
- Control packs: the rules layer, chosen once at `init`. (#410, #413)
- Human-facing findings are grouped by subject, and a recommendation names only what is missing. (#364)
- The questionnaire asks the unread questions first. (#1745, #419)
- A confirmed declaration is pinned to the evidence behind it.
- `environment.target: template`, for a repository that ships to be copied.
- `SHIP-TRUST-MANIFEST-UNPROTECTED` reads the file GitHub would read.
- `SHIP-TRUST-MANIFEST-UNPROTECTED` — who may change the gate.
- `doctor` says which rung of the adoption ladder you are on.
- A pin re-opens when authoritative evidence is replaced, not only when a reading appears. (#357)
- Existing `capabilities.lock.json` files keep loading.
- A merged declaration block reads in manifest field order.
- One action, one permission list, with no reviewed authority either.
- Authority follows credentials, not functions: declare it once per source. (#410)
- Ask only what the scanner cannot prove, and say how much is left. (#1745, #410, #357)
- Adopter-facing output stops naming the internal identity model. (#329, #327)
- A declaration cannot discharge a category it does not cover, and a published schema keeps its bytes. (#409, #411)
- A declaration weaker than the evidence inferred for it is no longer silent. (#409, #410, #357)
- Every evidence gap now labels a tool the way a reader can use, in every gap kind. (#403)
- Every stage that narrows the analysed surface now records what it removed, and the release decision can read it. (#403, #3076, #308)
- `verify --preview` of a head that is not checked out now asks for the checkout, instead of stopping. (#397)
- `detect` now publishes the same per-candidate `init` commands `init` does when a workspace holds several agent projects. (#397)
- The first scan of an agent whose tools are imported symbols now scaffolds both layers it needs, instead of emitting nothing. (#361)
- Every `<REVIEW_REQUIRED>` in `suggested-declarations.yaml` now says what a legal answer is. (#388, #268)
- `display_literal` now escapes Unicode noncharacters alongside the invisible code points it already covered. They are the same hazard — nothing reaches the reader, so two repository objects render identically — and two of them are worse: PyYAML rejects U+FFFE and U+FFFF outright, so an agent name carrying one made the generated declaration scaffold unparseable, because the document quoting that name in a comment could not be loaded at all. The encoding stays injective, so `undisplay_literal` still recovers the name.
- `verify --preview` on a monorepo now names the project the pull request actually changed, instead of a repository root that `init` refuses. (#394)
- `detect`'s glob-based source suggestion re-ran the whole git inventory walk once per pattern — fifteen walks for one pass. `_candidate_files_matching` now accepts an inventory the caller already built, which both fixes that and is what lets the preview evidence probe ask the *same* suggestion rule about a single directory rather than keeping a second copy of it.
- Project discovery no longer presents a truncated candidate list as a complete one. (#395)
- Google ADK extraction confidence is now measured on the module, not hardcoded. (#393)
- A tool inventory now completes the source that asked for it, instead of shadowing it. (#386)
- An input that is not there is no longer reported as an input with the wrong shape, and no command creates the workspace it was asked to inspect. (#389, #384)
- A manifest type mismatch is an edit, not a bug report. (#387)
- A Google ADK sub-agent's tools are part of the analyzed surface, and a tool the gate did not look at can no longer go unmentioned. (#385)
- One command runs Agents Shipgate from this checkout, and `doctor` now says which Shipgate answered. (#334, #338, #322)
- The launcher announces a spelling the operating system will actually start.
- Two virtual environments over one base are no longer one interpreter.
- `PATH` lookup follows the shell's rule, not "a file exists there".
- A trampoline target must be a command, not a mention of one.
- A quoted program token is read before it is judged to be ours.
- A `#!/bin/sh` console-script wrapper reports the interpreter it `exec`s.
- An `insufficient_evidence` verdict now leads with the gap you can close, and the three lines that announce it agree. (#362)
- Verifier schema `0.8 → 0.9`.
- The PR comment reports the proven fact, not the routing flag.
- The policy comparator honors the split check-id aliases.
- Accepted debt survives the split.
- The headline is bounded once, at the end.
- Unicode format controls are stripped from headline material.
- The verifier headline leads with the release blockers, not with the governance notice that outranked them. (#1917, #365)
- A first adoption no longer reports a policy weakening that could not have happened.
- The blocker title quoted into the headline is normalized and bounded.
- `init` ranks agent-name candidates instead of taking the first one it trips over. (#320, #1745, #324)
- First adoption inside a monorepo no longer starts by writing the wrong manifest. (#1, #363)
- One control vocabulary across the adoption walk.
- A manifest declaration a person owes is no longer routed to the agent. (#323)
- The `AgentControl` union is unchanged, and the compatibility floor stays at `21`.
- A completion cannot rest on a negative verdict.
- A manifest that is not UTF-8 is refused, not rewritten.
- The envelope only calls a string a command when something can run it.
- `control.next_action.path` names the file byte for byte.
- Every `doctor --json` payload carries the route, including the earliest failure.
- A dry run that wrote the CI workflow no longer says nothing was written.
- `scan` is outside this rollout, and now says so. (#323)
- The recommended next command now runs where it was recommended. (#322)
- A prompt or policy edit outside the repository root — or spelled `Policies/` — no longer reports as "nothing in this PR signals a tool-surface change.".
- The `on-tool-source-changes` CI recipes are retired.
- One compact object now answers "what may I do next?", instead of four artifacts and a guess. (#333, #323, #338)
- The release pipeline now proves the wheel it publishes came from the tagged commit.
- Verification and publication are now separate jobs, and a partial publish is recoverable.
- The qualification signer identity is reviewed code, and a release candidate must have been rehearsed.
- The signed SBOM now describes the shipped wheel instead of the CI machine.
- A release candidate can be rehearsed without any publication authority.
- Release test selection matches CI, so candidates fail on correctness evidence rather than timing noise.
- The release page carries the changelog, and the release runs the environment CI approved. (#345)
- Insufficient-evidence remediation now stays framework-aware from the decision engine through every primary short-form surface. (#318)
- Human review now blocks merge and completion, not publication of the evidence a human needs in order to review. (#335)
- Local verification now evaluates committed and uncommitted edits as one effective worktree diff. (#336)
- A coding agent can no longer enforce a verifier result the workspace has outgrown. (#339)
- An unreadable PR diff is no longer reported as "nothing here is agent-related.".
- Google ADK repositories that share one tool between agents can be scanned again.
- Standalone scans now retire the complete verifier route as one lifecycle set.
- The trigger catalog now recognizes a Google ADK `tools=[...]` list, and stops calling a bare package token a version bump.
- `input_set_id` now covers every input the adapters actually read. (#299)
- `SHIP-VERIFY-POLICY-WEAKENED` can now actually see a weakened CI gate.
- `init` no longer fails on a repository that names two files the same.
- A protected-surface stop now names the route, and the non-route that looks like one.
- A first adoption no longer reads as a policy weakening.
- The manifest a run actually loaded is a trust root.
- `check` detects which agent is running it.
- `check`, `audit`, and `preflight` honor the agent-mode error contract.
- A rerun command that actually reruns.
- Preflight recovery keeps the request it failed on.
- Detached diffs never authorize checkout-dependent verification.
- A failed baseline is never recovered by overwriting it.
- Host-audit filesystem failures follow the catalog.
- The audit id distinguishes the actor.
- Static control inputs now fail closed on identity and resource ambiguity.
- Portable host instructions are protected consistently.
- Mechanical repair authorization is subject-bound.
- Adoption wording stands down when something was genuinely weakened.
- A way out of `insufficient_evidence` (#292). (#292)
- Unfilled scaffold placeholders are rejected by the manifest.
- The authority template was unfillable.
- Evidence gaps say whether this diff caused them.
- Framework-correct low-confidence remedy.

### `0.16.0b7`

- Graded local boundary stop (UX P0, contract v19).
- Stop hook follows `control.state`.
- Own-repo CI verify gates on `blocked,unknown` again. (#274)
- Version advances.

### `0.16.0b6`

- Reproducible verification identity (P0).
- Terminal receipts and portable execution boundary.
- Externally rooted exact-operation authorization (contract v18).
- Identity and authorization contract versions.
- Immutable CI subject.
- Non-forgeable trust decay.
- Agent-authored coverage proposals (contract v18 clarification).
- Codex marketplace coverage and plugin-path containment.

### `0.16.0b5`

- Evidence-basis policy gate (P0).
- Non-waivable policy applicability gaps.
- Evidence contract versions.

### `0.16.0b4`

- Complete zero-config multi-host boundary.
- Host-neutral boundary contract.
- Evidence-bearing host inventory.
- Boundary beta hardening.
- Correction to the original host-governance claim.

### `0.16.0b3`

- Unambiguous agent control contract (P0).
- Control contract versions.
- Execution, applicability, and mergeability are separate.
- Conductor OSS workflow JSON adapter.

### `0.16.0b2`

- Root-reachable agent binding graph (P0).
- Binding contract versions.
- Provider-scoped canonical tool identity (P0).
- Identity-safe policies, diffs, traces, and debt.
- Identity contract versions.

### `0.16.0b1`

- Evidence-backed `passed` verdict.
- Normalized semantic evidence contract.
- Machine-readable static-verdict boundary.
- Capability standard v0.2.
- Qualification trust boundary is explicit.

## 0.15.0 - 2026-07-07

- **First real-history accuracy numbers, published.** The 2026-W26 mined
  corpus (120 merged PRs from stripe/agent-toolkit, block/goose, and
  pydantic-ai) is now labeled (two independent AI labelers, disagreement
  0/10, third-pass adjudicated — pending human spot-check) and scored. On the
  10 PRs the gate engaged, it never wrongly passed an authority-bearing change
  (`needs_human_caught` 1.0, `benign_escalation_rate` 0.0) but also never
  cleanly passed a safe one (`ie_rate_on_safe` 0.5, plus a since-fixed scan
  crash). Full confusion matrix and method in
  [`benchmark/miner/README.md`](benchmark/miner/README.md); the README status
  banner now carries the numbers instead of "none published yet". Real history
  contributes no `must_block` rows, so blocked-recall stays with the
  constructed-adversarial stratum.
- **Config-bound dynamic-toolkit capability detection.** New checks
  `SHIP-CAP-CONFIG-BINDING-REMOVED` (high, suppression-immune) and
  `SHIP-CAP-CONFIG-BINDING-CHANGED` (review item) close the pilot blind spot
  where a diff removed or retargeted a factory's config binding — silently
  expanding the effective tool surface — without any capability delta showing
  in the diff. A conservative same-file config tracer (json/yaml/toml loads,
  `os.environ`, in-file pydantic settings) feeds them; `config → unknown`
  never fires, guarding against false positives.
- **Duplicate `action_surface` action_id collisions degrade instead of
  crashing.** A base reference serialized by a pre-#226 engine could still
  crash `scan`/`verify` at diff time with `Config error: Duplicate
  action_surface action_id`; it now degrades to a source warning
  (review_required), matching the OpenAPI fix in #226. This eliminated the
  four `scan_failed` rows in the W26 corpus.
- **Claude Code plugin marketplace.** The repo now doubles as a Claude Code
  plugin marketplace (`/plugin marketplace add ThreeMoonsLab/agents-shipgate`,
  then `/plugin install agents-shipgate@agents-shipgate`) — the symmetric
  counterpart of the existing Codex marketplace. The plugin is skill-only
  (the auto-triggering skill + the namespaced `/agents-shipgate:shipgate`
  command); the scanner stays in the separately installed CLI and hooks stay
  on the explicit `install-hooks` path. Byte-identity with the canonical
  skill/command sources is test-pinned. Fixed in passing (caught by
  `claude plugin validate`): the canonical `SKILL.md` and `/shipgate`
  command shipped YAML frontmatter with an unquoted `:` in `description`,
  which Claude Code loads as silently-empty metadata — breaking
  description-based skill auto-triggering for every existing install. Both
  are now quoted, all byte-identical copies synced, and a regression test
  parses the frontmatter.

- **Contract v10 (additive): machine-readable `verify_required` on the Codex
  boundary result.** `shipgate check` already escalated to `warn` and routed
  to `verify` when a diff touched a tool surface it cannot gate; that deferral
  now also sets a top-level boolean `verify_required` on
  `shipgate.codex_boundary_result/v1`, and `verify_required` joins
  `agent_result_control_fields` in the runtime contract. Agents switch on the
  field instead of parsing warning prose; the observable pair is
  `decision="warn"` with `verify_required=true` — "no boundary rule fired,
  but capability is not yet gated: run verify before completion" (the
  escalation means a plain `allow` always has `verify_required=false`). The
  field lives on the shared `AgentResultV1` base, so the legacy
  `agent-result-schema.v1.json` carries it too and
  `agent_result_control_fields` validates against both schemas. Additive
  over v9: consumers pinned to `contract_version >= 9` keep working.

## 0.14.0 - 2026-06-30

- **Versioning: the `1.0.0-alpha` line is withdrawn; this work ships as
  `0.14.0`.** An earlier draft of this cycle briefly carried `1.0.0a1`. That
  label was withdrawn: the `report.json` schema (`report_schema_version:
  "0.28"`) is still additive-versioned and not yet frozen, the package is still
  `Development Status :: 4 - Beta`, and no real-world detection-accuracy
  baseline has been published — none of which support a `1.0` line. `0.14.0`
  continues the `0.x` contract line from `0.13.0` and carries the same
  agent-controller cleanup (see
  [STABILITY.md](STABILITY.md#migration-note-0-14-0)). A `1.0` line will begin
  only when the report schema reaches `1.0` and holds without a breaking change.
- **Non-preview `verify` now fails closed on a missing `--config`.**
  `agents-shipgate verify --workspace . --config missing.yaml --json` exits
  `2` with `merge_verdict: "unknown"`, `applicability: "unknown"`, and
  `can_merge_without_human: false`; it writes lightweight verifier/controller
  artifacts but no `report.json` and runs no head scan. This replaces the old
  lenient path where a missing config could trigger-skip and exit `0`.
  `verify --preview --config missing.yaml --json` is unchanged and remains the
  setup/relevance path with exit `0`.
- **Shipgate now has a separate self-dogfood PR workflow.** The root
  `shipgate.yaml` remains the public Codex-plugin marketplace self-scan, while
  `shipgate-self.yaml` and `.github/workflows/agents-shipgate-self.yml` run an
  advisory static-only local-action gate on pull requests with
  `fail_on_merge_verdicts: blocked`, artifact upload enabled, and PR comments
  disabled. This does not scan Shipgate's Python scanner implementation; tests,
  coverage, audit, SBOM, and release signing remain that assurance path.
- **A named high concern now routes to review, not `insufficient_evidence`.**
  When a scan turns up an *active* (not baseline-accepted) high/critical review
  finding, the release decision is now `review_required` even if low-confidence
  extraction would otherwise have produced `insufficient_evidence`. Both
  verdicts are equally non-auto-mergeable, but `review_required` points the
  human at a specific, actionable finding (e.g. the new
  `SHIP-SCOPE-TOOLKIT-UNBOUNDED`) instead of the vaguer "we couldn't see
  enough." `blocked` still outranks everything; IE still fires when the only
  signal is thin extraction. The 2026-06-01 Stripe pilot's silent/IE case now
  surfaces as a routed review. `evidence_gaps` are preserved on the report
  either way, so the extraction-coverage signal is not lost.

## 0.13.0 - 2026-06-12

- **Accepted-debt exception workflow (baseline schema 0.6).** `baseline save`
  gains `--owner`, `--reason`, and `--expires` so the approval metadata the
  v0.5 provenance contract documented as "reviewer-set" is finally settable
  without hand-editing the file (which trips the integrity hash). Metadata is
  stamped on newly-accepted entries; `--apply-to-existing` fills the fields
  into existing entries that lack them — never overwriting a previously-set
  value and preserving each entry's original `recorded_at`/`run_id` history.
  Approval is declared, never inferred, matching the `human_ack` contract.
  New `baseline status` reports accepted-debt aging (owner, age, expiry,
  expiring-soon/expired/unowned summary; `--as-of` pins the date for
  reproducible CI output) and turns into an org governance gate with
  `--require-owner` / `--require-expiry` / `--max-age-days N` — exit 20 on
  violations, advisory exit 0 without gate flags. Expired entries violate
  `--require-expiry`, and entries without provenance fail every active gate
  (unknown history is ungoverned debt, not exempt debt). Legacy 0.2–0.5
  baselines still load; re-saving upgrades them to 0.6.
- **Host-grant drift detection.** `audit --host --save-baseline` records the
  current coding-agent host grants (MCP servers, Claude Code permission rules
  and hooks, workflow scopes, Codex config presence) as the acknowledged state
  in `.agents-shipgate/host-grants.json` (content-only and byte-idempotent —
  no timestamps or machine paths; the directory is already a verify trust-root
  surface, so PR edits to the snapshot stay release-visible). `audit --host
  --drift` deterministically diffs current grants against that baseline with
  per-category added/removed/changed buckets plus `expansion_signals` naming
  the authority-broadening shapes (new or **changed** server, wildcard allow
  added, `deny` or `ask` rule **removed**, hook added or **changed**, workflow
  write scope or `pull_request_target` gained). MCP server and hook entries
  carry a `config_sha256` over their full configuration; inside
  `env`/`headers` only values under secret-looking keys (shared sensitive-key
  vocabulary: token, secret, password, api_key, authorization, …) are redacted
  before hashing, so editing what an existing server or hook can do — args,
  commands, matchers, URL, key sets, or a grant-shaping value like
  `READ_ONLY=false` — is drift while credential rotation is not; the
  baseline's stored `inventory_sha256` is verified at load time and
  hand-edited or malformed baselines fail closed with exit 2. Advisory by default; `--fail-on-drift`
  exits 20 for scheduled CI gates — recipe at
  `examples/github-actions/12-host-grant-drift.yml`. Catches authority changes
  that land outside PR review, where the diff-time `SHIP-HOST-BOUNDARY-*`
  checks cannot see them.
- **`check` defers tool-surface changes to `verify` (coverage boundary).**
  `shipgate check` is boundary-scoped and does not compute the capability
  delta, so a clean boundary result over a diff that changes a
  manifest-declared `tool_sources[].path` no longer returns `allow` — it
  returns `decision="warn"` routing `first_next_action` to `verify`, with a
  `diagnostics[].code="capability_change_requires_verify"` marker and a
  `trace[].step="coverage"` event. Completion is still allowed, but `check`
  no longer green-lights a capability change only `verify` gates, so the local
  loop cannot disagree with `release_decision.decision`. Docs/test/boundary-only
  diffs are unaffected (still `allow`); no `agent_result_v1` schema change.
- **Agent-mode auto-detection.** Agent mode now auto-enables when a known
  coding-agent harness environment is detected (Claude Code exports
  `CLAUDECODE=1`, Cursor `CURSOR_TRACE_ID`), so structured `next_action`
  errors no longer require remembering `AGENTS_SHIPGATE_AGENT_MODE=1`. An
  explicit `AGENTS_SHIPGATE_AGENT_MODE=0` still forces it off.
- **Compact agent stdout for `verify`.** `verify --format agent` (new) prints
  the compact `agent_result_v1` payload (the same artifact written to
  `agents-shipgate-reports/agent-result.json`) on stdout, so one `verify`
  call closes the agent loop without a second file read. Bare `verify --json`
  resolves to this agent surface for verify runs (and to the full verifier
  JSON for `--preview`, whose relevance answer lives in the `trigger`
  block); `verify --format json` is unchanged. Inside a detected
  coding-agent environment, zero-flag `verify` defaults to the agent format.
- **Base auto-detection for `verify`.** When `--base` is omitted, verify
  auto-detects the default branch (`origin/HEAD`, `origin/main`,
  `origin/master`, `main`, `master`) and uses it for diff context — but only
  when the detected ref points at a different commit than the head, so a
  clean checkout of the default branch keeps today's working-tree behavior.
  The detection never fetches. `--no-base` disables it; an explicit `--base`
  always wins. The auto-detected ref is recorded in `base_notes`.
- **`init --claude-code` one-shot setup.** A single flag wires the full
  Claude Code surface: the `CLAUDE.md` managed block, the
  `.claude/skills/agents-shipgate/` skill bundle, the Claude Code hooks, and
  an `agents-shipgate verify --json` alias appended to Makefile /
  `package.json` scripts when those files exist. Idempotent, dry-run without
  `--write`, and reported under the additive `claude_code` key in
  `init --json` output.
- **Pre-commit hooks now run the verifier.** The `agents-shipgate` and
  `agents-shipgate-strict` pre-commit hook entries switch from unconditional
  `scan` to the trigger-gated `verify` flow (the `files:` regex pre-gate is
  unchanged), so local commits get the same merge-verdict surface as CI and
  diff-only trigger rules are evaluated once the hook fires.
- **`fix_task.patches[]`.** When `verify --suggest-patches` routes the repair
  to the coding agent, the fix task now carries the machine-applicable
  suggested patches (`{finding_id, check_id, patch}` with the discriminated
  set/append/remove-pointer payloads) so the agent gets concrete edits, not
  just prose instructions. Manual patches stay excluded and the field is
  additive — repair aid, never a gate input.
- **`fix_task` names low-confidence sources on `insufficient_evidence`.** The
  verify fix task for an `insufficient_evidence` verdict no longer dead-ends
  at the threshold sentence: it names each low-confidence source (count,
  source type, ref) with the explicit-inventory remedy and quotes up to
  three source warnings. Complements the report-layer
  `evidence_coverage.evidence_gaps[]` (schema v0.26); the route stays human
  because declaring an inventory asserts authority a coding agent must not
  invent. Deeper adapter-level config-bound toolkit detection is designed in
  `docs/engineering/config-bound-capability-detection.md`.
- **Claude Code adoption surfaces reworked.** The README gains a
  "Use with Claude Code" section, `docs/agents/use-with-claude-code.md` opens
  with the recommended one-command `init --claude-code` setup, and the
  `agents-shipgate` skill description triggers on change artifacts (MCP
  servers/tools, tool decorators, permission scopes, approval policies, agent
  CI) instead of product-name phrases only.
- **Cold-start dead ends now print an executable next action.** Human-mode
  CLI error paths surface the same ranked recovery step that agent mode
  emits as JSON: `scan`/`doctor`/`verify` config errors print a
  `next: …` / `why: …` hint (e.g. `next: agents-shipgate detect …` on a
  missing manifest), and the `init --write` → `scan` CHANGE_ME placeholder
  failure routes to the manifest edit instead of the generic missing-file
  advice — in both human and agent mode. `verify` also gains agent-mode
  structured errors (`AGENTS_SHIPGATE_AGENT_MODE=1`) and scan-parity
  flag-error vs run-error handling, so flag mistakes are never answered
  with manifest diagnostics. Hints are suppressed in agent mode to keep
  the `docs/errors.json` single-JSON-line contract. Driven by the
  2026-06-10 cold-start funnel test
  (`marketing/cold-start-funnel-test-2026-06-10.md`).

- Add the GTM plan of record (`marketing/gtm-strategy.md`), launch kit,
  design-partner outreach kit, and launch blog draft; README shows the
  verifier PR-comment verdict ("What your PR sees") and links the
  coding-agent install path from the quickstart.

- **Agent-native protocol.** `shipgate check --agent
  {codex,claude-code,cursor} --workspace . --format agent-json` is now the
  canonical one-command agent path. It returns the stable
  `agent_result_v1` contract with explicit completion, stop, repair,
  human-review, policy-provenance, source-artifact, and exit-code fields.
- **`agent_result_v1` policy provenance is required in 0.13.0 producers.**
  The schema name stays `agent_result_v1`; all in-tree producers now emit the
  required `policy` object plus `policy_snapshot_sha256`. Consumers validating
  older v0.12.0 objects should treat this as the 0.13.0 schema publication
  point and update together with the package version.
- **MCP server mode narrowed to `shipgate.check`.** The optional
  `[mcp]` server is now a read-only static adapter that accepts caller-provided
  diff text and returns exact `agent_result_v1`. The v0.12.0 preview tools
  (`shipgate_preview`, `shipgate_verify`, `shipgate_explain_finding`) were
  never listed in `STABILITY.md`; they are removed in favor of the single
  agent protocol command/tool.
- Policy weakening detection now compares parsed before/after policy YAML
  from reconstructed file content when available, so quoted scalars, inline
  comments, and hunks that omit the rule id still block.
- `shipgate check --head <ref>` or `--base <ref>` alone now fails closed with
  a structured CLI error. Provide both refs, or omit both to check local
  uncommitted changes.

## 0.12.0 - 2026-06-09

- **Actionable `insufficient_evidence` (report schema v0.26).**
  `release_decision.evidence_coverage.evidence_gaps[]` now lists one
  structured remediation row per low-confidence tool / source warning
  (`{kind, subject, source_type, source_ref, why, next_action}`), and scan
  writes an advisory `suggested-inventory.json` skeleton next to
  `report.json` whenever low-confidence tools exist — in the same
  MCP-export shape every `tool_inventories` manifest key loads. Pure
  projection of the existing coverage counts; thresholds, decisions, and
  fingerprints are unchanged.
- **Local capability-release ledger (`registry` v0.1).**
  `agents-shipgate registry ingest --attestation <file>` appends a
  normalized, content-addressed row to a JSONL ledger (idempotent);
  `registry query` filters by repo / verdict / capability id /
  trust-root flag. The v0 substrate for the cross-repo attestation
  registry; design boundary for any hosted aggregation documented in
  `docs/hosted-plane-design.md`, and the v1.0 report consolidation
  proposal in `docs/report-v1-consolidation-rc.md`.
- **Host capability governance v0 (`SHIP-HOST-BOUNDARY-*`).** New
  diff-aware, suppression-immune check family covering coding-agent host
  grants: MCP server additions/changes in `.mcp.json` /
  `.cursor/mcp.json` / `.vscode/mcp.json`, Claude Code
  `permissions.allow` expansion (wildcard-shaped rules like `Bash(*)`
  **block**; scoped expansions route to human review), `permissions.deny`
  removal, hook changes, GitHub workflow permission expansion
  (`write-all` blocks; read→write routes to review), and new
  `pull_request_target` triggers. Policy mirror at
  `policies/host-boundary.shipgate.yaml`; concepts and reviewer guidance
  in `docs/mcp-governance.md`. Trust-root classification now also covers
  `.claude/settings.json` / `.claude/settings.local.json` /
  `.cursor/mcp.json` / `.vscode/mcp.json`.
- **`audit --host` zero-config inventory.** One read-only command that
  answers "what is my coding agent currently allowed to do in this
  repo?" — MCP servers (env *keys* only, never values), permission rules
  with wildcard flags, hooks, and workflow write scopes /
  `pull_request_target` — as one page of Markdown or `--json`. Works
  without `shipgate.yaml`.
- **Policy packs v0.2: conditional composition + org distribution.**
  `match` gains `all_of` / `any_of` / `none_of` combinators (flat fields
  stay implicitly ANDed — fully backward compatible) and parameter
  predicates gain declared-bound comparisons (`maximum_above`,
  `minimum_below`), so rules like "financial action with amount unbounded
  or above 1000 must declare approval" are now declarative.
  `checks.policy_packs` entries accept an optional `sha256` content pin
  that fails the scan closed when a shared/org pack is tampered with.
  Schema frozen at `docs/policy-pack-schema.v0.2.json`.
- **MCP server mode (optional `[mcp]` extra).** `agents-shipgate
  mcp-serve` exposes `shipgate_preview`, `shipgate_verify`, and
  `shipgate_explain_finding` over stdio so shell-less agents can query
  the verifier in-loop. Pure projection layer: no network, no mutating
  tools, no second gate (`docs/mcp-server.md`).
- **PreToolUse boundary hook for Claude Code.** `install-hooks --target
  claude-code` now also registers a `PreToolUse` hook: editing a
  protected trust-root surface routes the tool call to the human
  (`permissionDecision: "ask"`, or `deny` via
  `AGENTS_SHIPGATE_PRETOOLUSE_DECISION`) with an explanation — the
  authority boundary surfaces in-session, before the edit, instead of at
  PR time. The protected-surface list is rendered at install time from
  the verify check's `TRUST_ROOT_SURFACES`, so hook and gate cannot
  drift.
- **Native GitHub Check Run support.** New Action inputs `check_run` /
  `check_run_name` publish the merge verdict as a Check Run
  (`mergeable` → success, `blocked` → failure, human-routed verdicts →
  neutral) with up to 50 line-level annotations from `report.sarif`
  (`scripts/github_check_run.py`; requires `checks: write`). New recipes:
  `examples/github-actions/09-risk-labels-and-reviewers.yml` (risk labels
  + trust-root reviewer routing from existing outputs) and
  `10-check-run-annotations.yml`.
- **`agent_weakens_gate` fixture.** One-command trust-root demo
  (`agents-shipgate fixture run agent_weakens_gate`): the head commit
  deletes the repo's Shipgate CI workflow — the cheapest reward-hack —
  and the verifier returns `merge_verdict: blocked` with
  `can_merge_without_human: false` via the suppression-immune
  `SHIP-VERIFY-CI-GATE-REMOVED` / `SHIP-CODEX-BOUNDARY-CI-GATE-REMOVED`
  checks.
- **Privacy hardening.** The redaction passthrough for already-redacted
  values now honors only marker kinds Shipgate itself emits, so scanned
  values formatted like `[REDACTED:...]` can no longer smuggle payloads
  past forced sensitive-key redaction. Added symlink-escape regression
  tests for input loading and `apply-patches` containment.
- Add a GitHub/verify `agent-result.json` artifact that uses the existing
  `agent_result_v1` schema instead of introducing a second agent-result
  contract. The Action exposes `agent_decision`, `risk_level`, `audit_id`,
  `required_reviewers`, and `policy_snapshot_sha256`, and the opt-in
  `fail_on_decisions` input now fails closed when configured but no compact
  agent decision is available.
- Phase 7 makes capability diff the default verifier review primitive when a
  reviewed base lock is committed: `verify` emits head capability locks plus
  semantic diff JSON/Markdown review artifacts when available, and attestation
  output moves from schema `0.1` to `0.2` to bind capability lock/diff hashes.
- SARIF results now prefer stable policy rule IDs when a finding carries one,
  while preserving the built-in Shipgate `check_id` in properties. Existing
  GitHub code-scanning alerts keyed by the previous rule ID may close/reopen
  on the first upgrade run.
- Add the repo's advisory self-dogfood Shipgate workflow, product-hardening
  gap-closure docs, Agent Workflow Evidence schemas, and the AgentPR Governance
  case catalog / acceptance spec.

## 0.11.0 - 2026-05-31

- **Verifier adoption-loop release prep.** Public docs and discovery metadata now
  lead with the verify-first adoption path, pinned `v0.11.0` snippets, verifier
  artifacts, merge verdicts, `fix_task`, and explicit Action merge-policy
  examples. Adds the verify-native `ai_generated_refund_pr` fixture for the
  blocked refund PR demo and introduces the provisional
  `agents-shipgate feedback export` command plus
  `docs/feedback-schema.v0.1.json` for redacted design-partner feedback loops.

- **Verifier PR comment v2 + additive Action outputs.** The GitHub Action now
  defaults to the verifier workflow (`verify_mode: verify`) and the
  capability-review PR comment (`pr_comment_style: capability-review`) for the
  next minor release. The comment starts from
  `release_decision.decision`, renders a top capability-change table, surfaces
  trust-root warnings, separates required human/coding-agent work, and links the
  generated artifacts. The v1 findings-oriented comment remains available for
  one minor release cycle with `pr_comment_style: findings`.
  - New Action outputs are additive:
    `should_run`, `trigger_action`, `trigger_rule_ids`, `verifier_verdict`,
    `trust_root_touched`, `policy_weakened`, `capability_changes_added`,
    `capability_changes_modified`, and `capability_changes_removed`.
  - Existing outputs are preserved; `decision` remains the preferred release
    gating output.
  - `verifier.json` now includes a derived `capability_review` projection
    over `report.capability_change` and `report.verifier_summary`. It is
    reviewer-facing only and cannot disagree with the head scan's
    `release_decision`.

- **New large-scale sample + asserted latency budget.**
  Adds `samples/large_multi_framework_agent/` — a production-shape retail-ops
  AI assistant with ~65 tools across five tool sources (payments OpenAPI,
  fulfillment OpenAPI, CRM MCP, internal warehouse MCP, OpenAI Agents SDK).
  Exercises the pipeline (loaders → checks → release decision → reports +
  packet + privacy redaction) at realistic load, well beyond the 5–15 tool
  range covered by the existing samples. The manifest declares *partial*
  governance coverage on purpose so the scan surfaces a realistic mix of
  blockers, review items, and audit-envelope activity (~10 critical
  approval gaps, ~70 review items, severity overrides, suppressions,
  manual risk hints).
  New `tests/test_large_sample.py` (12 cases) asserts:
  - **Latency budget of 10.0 s wall-clock per scan** (typical: 1–2 s on a
    2024 laptop). The release gate lives on the CI critical path; a
    silent regression that doubles scan time would be felt by every
    adopter. The budget is generous to absorb CI variance — if the
    typical time exceeds half the budget, the sample has grown or the
    pipeline has regressed.
  - **Structural shape**: all 5 sources contribute tools; tool count in
    [50, 100]; findings in [40, 200]; decision blocked; at least one
    critical `SHIP-POLICY-APPROVAL-MISSING`; scope-coverage fires;
    severity-override audit envelope populated; contribution rules
    exhaustive over findings; privacy/reviewer/heuristics audit
    envelopes emitted.
  No committed `expected/report.{md,json}` goldens (intentional — pinning
  50+ findings × 20+ report sections through every schema bump is high
  cost, low signal). Auto-discovered as `agents-shipgate fixture run
  large_multi_framework_agent`; NOT added to `self-check`'s default
  fixture set so install verification stays fast.

- **`init --write` now ensures `agents-shipgate-reports/` is gitignored.**
  Closes a long-standing DX gap: the reports directory created by the first
  `scan` would silently appear in `git status` (and could be committed by an
  agent running `git add -A`). On every `init --write` we now also write a
  managed block to `.gitignore`:
  - File missing → created with just the block.
  - File present without our markers and without an existing
    `agents-shipgate-reports/` line → managed block appended (separated by
    one blank line; user content preserved byte-for-byte).
  - File present with our markers → upserted (unchanged / updated / migrated
    on version bump; refused on a newer version).
  - File present with `agents-shipgate-reports/` (or `/agents-shipgate-reports`
    / `agents-shipgate-reports` / `/agents-shipgate-reports/`) already on its
    own line → no-op (`already_present`). Normalization mirrors what
    gitignore itself does: trailing whitespace is stripped (gitignore
    ignores it on patterns), but **leading whitespace is not** — a line
    like ` agents-shipgate-reports/` (one leading space) is a broken
    pattern that git does not honor, so we fall through and append our
    managed block. Mid-line `#` is *not* treated as a comment introducer
    (gitignore only treats line-leading `#` as a comment, so
    `agents-shipgate-reports/  # legacy line` is a literal pattern that
    matches nothing — we again fall through and append). The same
    leading-whitespace rule applies to `!`-negations:
    ` !agents-shipgate-reports/` is not honored by git, so we don't treat
    it as `skipped_negated` either.
  - File present with `!agents-shipgate-reports/` → no-op
    (`skipped_negated`). Explicit user opt-outs are respected.
  - File present with ambiguous markers (e.g. duplicate blocks) → no-op
    (`skipped_ambiguous`).
  Idempotent on both LF and CRLF hosts (CRLF is preserved when writing,
  and the marker regex tolerates a trailing `\r` so the second `init
  --write` recognizes the existing block rather than appending a
  duplicate). Also runs when the manifest already exists so
  repos that adopted Shipgate before this CLI version get the line on their
  next `init --write`. Failure modes (symlinked `.gitignore` chain, path is
  not a regular file, write error) emit an `error`/`skipped_*` outcome but
  never block `init` — exit code is unchanged from prior versions.

  The outcome is surfaced in `--json` output as a new
  `gitignore: {status, path, message, block_version}` field. A human-readable
  one-line message prints to stdout (or stderr for skip/error statuses);
  `unchanged` and `already_present` are quiet so the success path stays
  scannable. New module: `agents_shipgate.cli.discovery.gitignore_block`.
  New tests: `tests/test_init_gitignore.py` (48 cases covering pure
  parsing, upsert, variant detection, CRLF parse + two-run CRLF
  idempotency, mid-line-`#` no-stripping, leading-whitespace rejection
  (space + tab + on negations), trailing-whitespace acceptance, and
  end-to-end through the CLI).

- **MVP readiness polish.** Check metadata now carries public `mvp_tier`
  triage labels; the OpenAI Agents SDK static extractor can scan a directory of
  immediate `*.py` files; and CLI / GitHub summaries lead with the
  baseline-aware decision, headline, evidence coverage, and next action.
  - `mvp_tier` is metadata only. It does not affect check execution, severity,
    fingerprints, baselines, `release_decision`, or CI exit behavior.
  - OpenAI Agents SDK single-file and directory modes now both emit
    manifest-relative POSIX `source_ref` values. The extractor delegates to the
    shared Python static helper, so runtime/context parameters named `self`,
    `cls`, `ctx`, `context`, `config`, `runtime`, `run_manager`, or `callbacks`
    are omitted from normalized input schemas.
  - CLI top findings now show the highest-impact 3 active findings, prioritized
    by release blockers then review items. `list-checks` plain text includes
    `mvp_tier` as a third tab-separated column; use `--json` for stable
    programmatic consumption.

- **v0.21 — `--no-heuristics` CLI flag closes the round-3 / round-4 E5
  carryover.** `Finding.provenance_kind` has shipped on every report since
  v0.15 as required+non-nullable wire metadata but had no consumer for
  four review cycles. v0.21 lands the consumer the field was always
  designed for: a security/GRC-friendly filter that excludes findings
  whose provenance is `keyword_heuristic` or `regex_heuristic` from the
  active release-gating set.
  - New `--no-heuristics` flag on `agents-shipgate scan` (stable in
    0.x). When set, findings whose `provenance_kind` is in
    `NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS` (today: `keyword_heuristic`
    and `regex_heuristic`) are marked `suppressed=True` with
    `suppression_reason="filtered by --no-heuristics"` BEFORE the
    release decision is built. Filtered findings remain in `findings[]`
    for transparency; they no longer gate release. The KEEP list is
    `static_declaration`, `ast_extraction`, and `policy_pack` —
    declared/parsed-shape findings and explicit external rules stay in
    scope.
  - New top-level `report.heuristics_filter` audit envelope. Required +
    always present on emitted scans regardless of whether the flag was
    set (parallel to `privacy_audit` shape). Fields: `enabled`,
    `excluded_provenance_kinds: list[str]`, `filtered_finding_count`,
    `filtered_by_kind: dict[str, int]`. Earns the contract weight of
    `Finding.provenance_kind` by giving it a first-class consumer.
  - Manifest-driven suppression wins on overlap: a finding the user
    explicitly suppressed via `checks.ignore` keeps the user's reason
    text even when its provenance_kind would have triggered the
    filter. The audit envelope still counts the overlap so reviewers
    see the filter's effective scope.
  - `ReviewerSummary` lens/audit counts already reflect the post-filter
    active set (the filter runs before `build_reviewer_summary`); no
    new field added to `ReviewerSummary` — the dedicated envelope is
    the right audit home.
  - Schema bump: `report_schema_version: "0.20"` → `"0.21"`. v0.20 moves
    to frozen-reference; existing v0.20 consumers ignore the new field.
  - Contract-stamp pin in `docs/architecture.md` bumped to date
    `2026-05-23`, report `v0.21`, packet `v0.6` (unchanged). The
    `test_architecture_doc_contract_stamp_matches_runtime` regression
    test moves in lockstep.
  - 12 new tests in `tests/test_no_heuristics.py` covering: pure-
    function filter semantics (KEEP / FILTER classifications per
    provenance_kind), envelope shape parity across enabled=True/False,
    manifest-suppression preservation, contract-list completeness
    (every value in `NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS` is a
    real `ProvenanceKind`; KEEP+EXCLUDE partition is exact), end-to-
    end `run_scan(no_heuristics=True)`, CLI subprocess smoke test,
    monotone-non-increasing reviewer-summary lens counts under
    filtering.
  - **Decision recorded.** Round-4 review's E5 carryover offered ship-
    or-retire on `provenance_kind`. We ship. Retiring would have forced
    a deprecation cycle on a stable-contract field used by every
    report since v0.15; shipping the flag earns the weight and serves
    a real audience (security/GRC reviewers triaging declared-only
    findings before promotion).

- **v0.21 — CI coverage gate raised from 75% → 85% (E7 from round-4 review).**
  Both `.github/workflows/ci.yml` and `.github/workflows/release.yml` now
  pass `--cov-fail-under=85`. Aggregate coverage on `main` at the time of
  the bump is ~88%, so the gate is +10pp tighter with ~3pp headroom for
  day-to-day movement. The bump catches the next time a refactor lands
  materially less-covered code without corresponding tests. No source
  change required to land — the gate is simply closer to the actual
  signal. Per-file coverage is not enforced; the aggregate floor only
  rises in step with what's already proven on `main`.

- **v0.21 — decompose `inputs/n8n.py` into `inputs/n8n/` package (E8 from
  round-3 review).** The largest input adapter (1493 lines monolithic)
  is now a 6-module package with per-concern boundaries; the public
  surface (`N8nAdapter`, `load_n8n_artifacts`) is unchanged via
  `__init__.py` re-exports. No behavior change — all 30 `tests/test_n8n.py`
  cases pass byte-identical; M3 trust-lint passes; M5 plugin validation
  passes; adapter-discovery contract test (PR #111) passes.
  - `_common.py` (300 LOC) — constants (`N8N_NODE_TYPE_RE`,
    `FROM_AI_RE`, `N8N_SOURCE_TYPES`, `BUILTIN_N8N_PREFIXES`,
    `HTTP_METHODS`), `_NodeItem` and `_Edge` data classes, leaf string
    / path / hash / redaction helpers, node-kind classification.
  - `_secrets.py` (122 LOC) — secret scanning of parameters / notes /
    `pinData` / `staticData` against the v0.19 global `SECRET_PATTERNS`
    layer.
  - `_auth_risk.py` (148 LOC) — credential references, `AuthInfo`
    synthesis, risk-hint heuristics, HTTP path hint.
  - `_tools.py` (492 LOC) — Tool extraction for the 5 flavours (ai,
    workflow, code, http, mcp_client) + projected `mcp`, schema
    extraction (`$fromAI(...)` macro, `inputSchema`, `outputSchema`,
    `parameters.fields`), MCP Client Tool selection mode, tool-artifact
    recording.
  - `_workflows.py` (464 LOC) — workflow file loading, shape detection,
    `_extract_workflow` orchestrator, connection-graph edges, node-record
    builders, dynamic-surface emission.
  - `_adapter.py` (249 LOC) — `N8nAdapter`, `load_n8n_artifacts`, and
    auxiliary loaders (`_load_inventory_ref`, `_load_credential_stubs`,
    `_load_structured_refs`, `_artifact_paths`, `_credential_entries`).
  - Dependency direction is a DAG at module-load time:
    `_common ← _secrets, _auth_risk ← _tools ← _workflows ← _adapter`.
    `_tools` calls back into `_workflows` for record builders and
    dynamic-surface emission via late imports inside the call sites
    that need them — keeps the static import graph one-way.
  - `tests/test_public_surface_contract.py::test_supported_inputs_match_adapter_class_vars_bidirectionally`
    updated from `glob("*.py")` to `rglob("*.py")` so adapter
    sub-packages are scanned (the contract test was written when n8n
    was a single file).
  - Closes round-3 evolution item E8; brings the largest input adapter
    in line with the typical adapter file size (mcp.py 148, openapi.py
    343, langchain.py 305). Largest sub-module now is `_tools.py` at
    492 LOC.

- **Adoption kit rendering externalized.** Codex and Claude Code
  `--agent-instructions` skill bundles now render from packaged
  `adoption-kits/` files instead of Python string constants. Downstream repos
  can provide `.agents-shipgate/adoption-kit.yaml` or
  `--agent-instructions-kit <path>` for local overrides, and generated skill
  directories now carry `.agents-shipgate-kit.json` sidecars for managed
  migrations.

- **v0.20 — third-party adapter entry-point discovery (E4 from round-3 review).**
  Opens the same extension surface for adapters (input loaders) that M5
  already opened for check plugins. Discovery is gated by the existing
  `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` env var and `--no-plugins` CLI flag.
  - New entry-point group: `agents_shipgate.adapters`. A third-party
    package declares an adapter class (or instance) in its
    `pyproject.toml` under
    `[project.entry-points."agents_shipgate.adapters"]`; the class must
    satisfy the `ToolSourceAdapter` Protocol — `source_type` ClassVar,
    `scope` ClassVar (`per_source` or `per_scan`), `artifact_class`
    ClassVar, and a `load(source, base_dir, manifest)` method.
  - New module `src/agents_shipgate/inputs/adapter_validation.py` with
    four load-time gates: `load_failed`, `bad_protocol`, `bad_scope`,
    and **`source_type_collision`** — the load-bearing trust rule
    rejecting any third-party adapter whose `source_type` shadows a
    built-in or another already-registered third-party adapter.
  - New top-level `discover_third_party_adapters(registry, *,
    plugins_enabled, loaded_adapters)` in `inputs/protocol.py` walks
    `entry_points("agents_shipgate.adapters")`, validates each entry,
    and registers the valid ones onto the supplied registry. Both
    valid and invalid records surface in
    `report.loaded_adapters[]` so reviewers can see what was skipped.
  - New report field `loaded_adapters: list[dict[str, Any]]` parallel
    to `loaded_plugins[]`. Items carry `name`, `value`, `distribution`,
    `version`, `source_type`, `validation_status`,
    `validation_errors[]`, `runtime_errors[]`. Required + present on
    every emitted scan (empty list when `--no-plugins` or no
    third-party adapters are installed). The schema generator marks
    each item's eight fields as required.
  - `--strict-plugins` (v0.17+) extended to cover adapter failures.
    Any non-`valid` `loaded_adapters[]` row OR non-empty
    `loaded_adapters[].runtime_errors` now elevates the scan to exit
    code 4 alongside the existing plugin failures.
  - `--no-plugins` flag help text updated to mention third-party
    adapter discovery is also disabled.
  - `run_validated_adapter` (in `adapter_validation.py`) provides a
    runtime safety wrapper for callers that want to capture
    exceptions into `loaded_adapters[].runtime_errors` instead of
    propagating them. The dispatcher's existing `_absorb` artifact-
    class check already fires `TypeError` for artifact smuggling;
    runtime wrapping is opt-in for future adapter-execution paths.
  - 21 new tests in `tests/test_adapter_entry_point_discovery.py`:
    each of the four gates + valid-class + valid-instance + env-var
    gating + `--no-plugins` overrides + collision-with-each-builtin
    parametrize + collision-between-third-parties + `--strict-plugins`
    end-to-end + runtime safety net (exception capture, wrong return
    type, artifact smuggling).
  - STABILITY.md gains a new "Third-party adapter discovery (v0.20+)"
    subsection under "Trust-model invariants" documenting the four
    gates + the `source_type_collision` load-bearing rule.

- **v0.20 — top-level `reviewer_summary` block.** Adds a deterministic
  projection of the reviewer lens surfaces (`tool_surface_diff`,
  capability/intent diff, `action_surface_diff`, evidence matrix) and
  audit envelopes (`policy_audit`, `privacy_audit`, baseline integrity
  findings). Parallels v0.12's `agent_summary` for the reviewer side:
  `agent_summary` answers "what should an agent do next?" and
  `reviewer_summary` answers "what should a reviewer look at first?".
  - Schema: bumped `report_schema_version` 0.19 → 0.20. The new block
    is required + non-nullable on the wire (Pydantic-Optional only for
    legacy test helpers). v0.19 schema is preserved at
    `docs/report-schema.v0.19.json`.
  - Fields: `verdict` (mirrors `release_decision.decision`), `headline`
    (≤200 chars, PR-comment-friendly), per-lens activity counts
    (`tool_surface_changes`, `capability_misalignments`,
    `action_surface_changes`, `evidence_matrix_gaps`), per-audit
    counts (`severity_overrides_applied`,
    `severity_overrides_tier_crossed`, `privacy_redactions`,
    `baseline_integrity_issues`), and `first_recommended_surface`
    (deterministic pointer or `null` on a clean scan).
  - `first_recommended_surface` priority: blocked → release_decision,
    insufficient_evidence → release_decision, then action_surface_diff
    > baseline_integrity > tier-crossed policy_audit >
    capability_intent_diff > tool_surface_diff > privacy_audit >
    evidence_matrix > null. Encoded in `_pick_first_recommended_surface`
    and pinned by `test_reviewer_summary.py`.
  - Projection invariants: pure (no I/O, no LLM calls), deterministic
    (same inputs → byte-identical output, asserted by
    `test_build_reviewer_summary_is_deterministic`), cannot disagree
    with the underlying lens/audit data.
  - STABILITY.md + docs/agent-contract-current.md: new bullets +
    enum-additivity rule mirroring `agent_summary.verdict`.

- **Docs: refresh `docs/architecture.md` to v0.19 reality.** The doc
  was stuck at pre-v0.6 conceptually — it described `core/models.py`
  as the shared model home (deleted in PR #95), framed adapters as
  free-function `load_<name>_artifacts(...)` (pre-v0.11 pattern), and
  did not mention the `schemas/` layer, the five reviewer lenses
  (tool surface / capability-intent / action surface / policy audit
  / evidence matrix), the three audit envelopes (policy audit,
  privacy audit, baseline audit log), the AST trust lint, plugin
  validation gates, severity-override floor, baseline integrity, or
  the privacy redaction layer. Refresh covers the v0.19 pipeline
  end-to-end, names every module, cross-links to `STABILITY.md` for
  each contract, and pins exit code `6` (strict `baseline verify`
  failure). No code change.

- **v0.18 / PR #1 trust-hardening: `dynamic_default` contract in
  `CheckMetadata`.** Formalizes the M1 dynamic-severity contract closed
  in v0.17.
  - `CheckMetadata.dynamic_default: bool = False` opts a check into the
    swing-severity category — its emitted finding severity depends on
    user-declared manifest values rather than the static catalog
    default. The severity-override resolver must receive the
    manifest-effective default via `extra_known_check_defaults`;
    otherwise tier-crossing comparison runs against the static catalog
    default and an aggressive override can silently bypass the gate.
  - A new model validator rejects `dynamic_default=True` without
    `floor_severity` — a swing check without a floor has no safety net.
  - `SHIP-ACTION-POLICY-VIOLATION` now declares `dynamic_default=True`
    and `floor_severity="medium"`. Two distinct contracts apply to
    existing manifests; both produce loud `ConfigError` (exit 2):
    - **Hard floor (no bypass).** Manifests resolving the check below
      `medium` — i.e., to `low` or `info` — are rejected by the
      `floor_severity` validator. `acknowledge_overrides` does NOT
      bypass the floor; the only remedies are to raise the override to
      `medium` or above, or remove the override entirely.
    - **Tier-crossing requires ack.** Downgrading from the catalog
      default `high` to the floor `medium` crosses the high → normal
      tier boundary. This case is allowed only with an
      `acknowledge_overrides` entry that supplies a reason; without one
      it is rejected with a tier-boundary error (not a floor error).
    Manifests currently overriding `SHIP-ACTION-POLICY-VIOLATION` to
    `low`/`info` cannot fix the regression by adding an ack — they must
    raise the override severity. Manifests overriding to `medium`
    without an ack pass once the ack is added.
  - `cli/scan.py:_dynamic_check_defaults` is the new canonical
    aggregator. It seeds every catalog check carrying
    `dynamic_default=True` with its static default (step 1), overlays
    manifest-effective values for action-surface policies (step 2), and
    adds policy-pack rule IDs (step 3). The seed loop guarantees the
    resolver's internal-consistency guard cannot false-positive on user
    input that overrides a swing check without declaring the
    corresponding manifest section.
  - A contract test `test_dynamic_default_aggregator_completeness`
    fails the moment someone adds a new `dynamic_default=True` catalog
    entry without ensuring the aggregator covers it.
  - Future checks emitting at manifest-declared severity must (A) set
    `dynamic_default=True` in `CHECK_METADATA` and (B) add an aggregator
    overlay branch in `cli/scan.py:_dynamic_check_defaults`. The
    contract test enforces both.
- **v0.18 / PR #1 plugin gate: `dynamic_default_not_supported`.**
  - New plugin-validation status rejects plugins declaring
    `AGENTS_SHIPGATE_METADATA.dynamic_default=True`. Plugins have no
    path into the scan dispatcher's aggregator and so could never
    receive the manifest-effective default needed for tier-crossing
    comparison; emitting at that severity directly is the supported
    path (with the floor contract still applying via
    `CheckMetadata.floor_severity`).
  - The gate runs **before** `_coerce_metadata()` so a plugin declaring
    `dynamic_default=True` without `floor_severity` lands in
    `dynamic_default_not_supported` rather than being mis-classified
    as `bad_floor` by the new `CheckMetadata` model validator.
- **v0.18 / PR #2 review follow-up: per-call-site allowlist pinning.**
  PR #91 review caught two structural holes in the v0.18 trust lint
  extension:
  - **P1**: the allowlist matched on `(relative_path, surface)` only,
    so one entry blanket-permitted every occurrence of a surface in
    a file. A future unreviewed `subprocess.run(...)` added to an
    already-allowlisted file would slip past silently.
  - **P2**: `importlib.resources` was globally exempted, so
    `files(name)` calls produced no violation. The current uses
    pass a literal `'agents_shipgate'` anchor, but a future
    user-controlled anchor would bypass the dynamic-import lint.

  Both are closed by tightening the allowlist contract:
  - `AllowedException` now carries `line: int` and `snippet: str`
    (canonical `ast.unparse` of the offending node) in addition to
    `relative_path` and `surface`. `_violation_allowed` matches on
    all four fields. Adding a new `subprocess.run` call to an
    already-allowlisted file now requires a new entry; changing an
    existing call's argv shape changes the `snippet` and fails the
    contract test.
  - `importlib.resources.` joins `FORBIDDEN_ATTR_CALL_PREFIXES`, and
    `importlib.resources` joins `TRACKED_NON_FORBIDDEN_MODULES`. The
    earlier draft of this PR only forbade `importlib.resources.files`,
    which left `read_text`, `read_binary`, `path`, `open_text`,
    `open_binary`, `is_resource`, `contents`, `as_file`, and any
    future addition under the module as a parallel bypass — each
    takes the same anchor-package argument and would have been
    silently allowed. The prefix entry catches the whole family.
    `from importlib.resources import <attr>; <attr>(...)` and
    `import importlib.resources as res; res.<attr>(...)` both
    resolve to canonical `importlib.resources.<attr>` and trip the
    prefix. Both first-party call sites in `triggers.py` and
    `fixtures.py` (currently `files`-only) are individually pinned
    with the literal `'agents_shipgate'` anchor in the snippet — a
    future `files(some_user_anchor)` or `read_text(some_user_anchor,
    ...)` call would change the snippet and fail the test.
  - `Violation` gains `snippet: str` captured via `ast.unparse(node)`.
  - New regression test
    `test_allowed_exceptions_pin_subprocess_run_per_call_site`
    asserts that multi-call files (triggers.py, artifacts.py) have
    distinct entries per call site, so the P1 bypass cannot
    reappear via consolidation.
  - New regression test `test_allowed_exceptions_have_no_duplicates`
    asserts no two entries cover the same call site.
  - Negative-control: injecting a 4th `subprocess.run` into
    `triggers.py` now fails the contract test with the precise
    `(line, surface, snippet)` triple. Injecting
    `files(user_var)` in place of `files('agents_shipgate')` fails
    similarly.

- **v0.18 / PR #2 trust-hardening: static AST lint widened to entire scanner.**
  Previously `tests/test_adapter_static_only.py` AST-scanned only
  `src/agents_shipgate/inputs/`; the public claim in STABILITY.md and
  README is broader ("the scanner does not execute or import user code").
  The lint now structurally enforces the broader claim.
  - Scope widened: scanner now walks every `.py` file under
    `src/agents_shipgate/` via `rglob`. The legacy
    `test_invariant_lint_covers_every_adapter_module` was paranoid for
    the 18-file `inputs/` case and no longer scales to ~80 files — the
    new contract test
    `test_no_unallowlisted_forbidden_surface_in_scanner` is the
    replacement, asserting a definitive PASS/FAIL signal over the whole
    sweep.
  - Four legitimate first-party meta-CLI surfaces are allowlisted via a
    new `ALLOWED_EXCEPTIONS` tuple of `AllowedException` entries, each
    with prose rationale:
    - `cli/bootstrap.py` `subprocess.run(...)` — chains
      `detect → init → scan → apply-patches` against Shipgate's own CLI.
    - `cli/discovery/artifacts.py` `subprocess.run(["git", ...])` —
      probes the user repo for file inventory.
    - `triggers.py` `subprocess.run(["git", "diff", ...])` — trigger
      evaluation reads diff content.
    - `cli/self_check.py` `__import__(name)` — validates that supplied
      modules are installed. Runs only under
      `agents-shipgate self-check`.
  - Two contract tests prevent allowlist rot:
    `test_allowlist_entry_matches_real_surface` (every entry must
    correspond to a real surface) and
    `test_no_unallowlisted_forbidden_surface_in_scanner` (every forbidden
    surface must be allowlisted or eliminated).
  - `importlib.resources` added to `ALLOWED_FORBIDDEN_MODULE_IMPORTS`
    for bundled-package files (e.g. `fixtures.py`, `triggers.py`).
    `importlib.metadata` remains allowed for plugin/adapter discovery.
  - `_scan_source` now returns structured `Violation` objects
    (`line`, `surface`, `message`) instead of preformatted strings, so
    callers can route by `surface` against `ALLOWED_EXCEPTIONS`.
  - STABILITY.md "Trust-model invariants" widened to cite the entire
    scanner package and adds a "Meta-CLI surfaces (allowlisted,
    audited)" subsection documenting each of the four entries.

- **v0.17 / M1 trust-hardening: severity-override floor + audit.**
  - `core.models.CheckMetadata` gains an optional `floor_severity` field
    (Severity | None). 16 release-critical built-in checks now declare a
    hard floor:
    - `SHIP-POLICY-APPROVAL-MISSING` (critical → floor "high")
    - `SHIP-ACTION-{FINANCIAL-WRITE-CONTROL-MISSING, DESTRUCTIVE-ROLLBACK-MISSING,
      WILDCARD-SCOPE, EFFECT-ESCALATED, APPROVAL-REMOVED}` (critical → floor "high")
    - `SHIP-AUTH-{MISSING-SCOPE, MANIFEST-BROAD-SCOPE, TOOL-BROAD-SCOPE,
      SCOPE-COVERAGE-MISSING}` (high → floor "medium")
    - `SHIP-SCOPE-{TOOL-OUTSIDE-PURPOSE, PROHIBITED-TOOL-PRESENT}` (high → floor "medium")
    - `SHIP-INVENTORY-{WILDCARD-TOOLS, LOW-CONFIDENCE-PRODUCTION-SURFACE}` (high → floor "medium")
    - `SHIP-POLICY-CONFIRMATION-MISSING` (high → floor "medium")
    - `SHIP-SIDEFX-IDEMPOTENCY-MISSING` (high → floor "medium")
  - Any `checks.severity_overrides` entry that resolves below the floor
    is rejected as a manifest config error (exit 2). The floor is hard;
    no acknowledgement bypasses it. **Breaking** for manifests that
    previously downgraded these checks below their new floor — fix by
    raising the override to floor-or-above, or removing the override.
  - `checks.severity_overrides` accepts both the legacy scalar form
    (`SHIP-XYZ: medium`) and a new rich form
    (`SHIP-XYZ: { severity, reason, expires }`). Reason flows into the
    new audit row; expires gives reviewers a time-bounded override.
  - New `checks.acknowledge_overrides[]` block. Required for any
    severity override whose application crosses a severity tier
    boundary (critical ↔ high, high ↔ medium/low/info) as a downgrade.
    Tier-crossing **upgrades** never require ack (strictly more
    conservative). Same-tier downgrades (medium → low) don't require ack.
    For checks emitted with manifest-declared severity (action-surface
    policies via `SHIP-ACTION-POLICY-VIOLATION`, policy-pack rules)
    the resolver compares against the strongest declared severity
    across the manifest, not the static catalog default — so a
    `severity: critical` action policy with override `high` is
    correctly tier-crossing and requires ack.
  - Expired `acknowledge_overrides` entry raises a manifest config error
    (exit 2) — no advisory-mode bypass. Same hard contract applies to
    `expires` on rich-form `severity_overrides` entries.
  - New top-level `report.policy_audit` block surfacing every applied
    override:
    `policy_audit.severity_overrides_applied[].{check_id,
    default_severity, applied_severity, manifest_path, reason,
    tier_crossed, direction, expires}`. Always emitted on scans (empty
    envelope when no overrides applied); required + non-nullable on
    the wire (mirrors the v0.12 `agent_summary` pattern). Lands at
    `report_schema_version: "0.17"` alongside M8's
    `release_decision.contribution_rules[]` — both audits are additive
    and share the same schema bump.
  - Markdown report renders a new "Policy Audit" section between
    Release Decision and Summary when overrides exist. GitHub step
    summary adds a one-liner counting overrides + tier-crossed +
    upgrades/downgrades.
  - New module `core/severity_overrides.py` owns floor/tier/ack/expiry
    resolution as a pure function; `core/findings.py::apply_severity_overrides`
    still consumes a flat `dict[str, Severity]` so existing direct
    callers and tests stay byte-compatible.
  - `AgentsShipgateManifest.severity_overrides()` still returns the
    flat scalar projection for back-compat; new
    `severity_override_entries()` returns the rich shape and
    `acknowledge_overrides()` returns the ack list.
- Added `release_decision.contribution_rules[]` — a deterministic
  per-finding audit of how each finding contributed to the release
  decision (M8 of the Trust Hardening Pass). Bumps
  `report_schema_version` to `0.17` (shared with M1's `policy_audit`).
  Exactly one row per `report.findings` entry (including suppressed)
  with `category` ∈ `{blocker, review_item, excluded}` and `rule` ∈
  `{policy_block_new, severity_block_new, policy_baseline_accepted,
  severity_baseline_accepted, review_required, sub_threshold,
  suppressed}`. The new `STABILITY.md` "Release decision truth table"
  documents which `(rule, category)` pair fires for every
  `(blocks_release, severity, baseline_status, fail_on)` combination.
  Additive only: no semantic change to `decision`, `blockers[]`,
  `review_items[]`, `fail_policy.exit_code`, or strict-mode exit codes —
  the audit reflects existing behavior, it does not modify it. The
  field defaults to `[]` for legacy reports loaded via
  `explain-finding` so consumers never need an existence check.
- Replaced the hardcoded `if/elif` source-dispatch in `cli/scan.py` with a
  real `ToolSourceAdapter` Protocol and `AdapterRegistry`. Every loader
  (MCP, OpenAPI, OpenAI Agents SDK, Google ADK, LangChain, CrewAI, n8n,
  Codex plugin, OpenAI API, Anthropic API) is now an adapter class that
  registers with `agents_shipgate.inputs.protocol.REGISTRY`. The scan
  pipeline returns a typed `ArtifactBag` so framework artifacts retain
  their concrete types into `ScanContext`. Framework adapters now fire
  correctly when configured via top-level manifest sections without a
  matching `tool_sources` entry. Internal refactor — no behavior change
  for users.
- Added minimal source provenance to findings. `agents-shipgate scan` now
  emits `report_schema_version: "0.11"` with optional structured location
  keys on `findings[].source`: `path`, `start_line`, `end_line`,
  `start_column`, and `pointer` (RFC 6901). Populated for the common
  tool-source loaders (OpenAPI, MCP, OpenAI tool artifacts, Anthropic
  tool artifacts) when the source file is YAML; JSON inputs carry `path`
  and `pointer` but no line. SARIF emits the position via
  `physicalLocation.region.startLine` (and `endLine` / `startColumn`
  when present), with the JSON pointer under
  `properties.shipgatePointer`. Capability-Intent Diff markdown appends
  `(at path:line)` to misalignment rows when provenance is available.
  `run_id` explicitly excludes the new provenance fields so YAML line
  drift cannot churn the hash. Reports without populated provenance
  remain byte-identical to v0.10 because `report_json_payload` strips
  unset keys.
- Added JSON-first tool-surface diff for PR review. `agents-shipgate scan`
  now emits `report_schema_version: "0.10"` with always-present
  `tool_surface_facts` and `tool_surface_diff` fields. The diff explains
  added/removed/changed tools, high-risk tag changes, scope drift, enforcement
  control changes, policy drift, finding deltas, and accepted debt without
  changing `release_decision.decision`, strict/advisory exit behavior, or SARIF.
- Added `agents-shipgate scan --diff-from <path>` for comparing against a prior
  `report.json` or v0.3 baseline JSON. `--baseline` still controls finding
  baseline status and strict-mode filtering; `--diff-from` controls only
  `tool_surface_diff`.
- Baseline files now save as schema `0.3` with optional `tool_surface_facts`.
  Schema `0.2` baselines continue to load for accepted-debt matching but cannot
  enable surface diff by themselves.
- GitHub Action adds `diff_from`, `diff_base`, and `diff_enabled`. Setting
  `diff_base: target` performs a best-effort target-branch scan with the
  PR-side installed package and falls back to a disabled diff note on fetch,
  config, schema, or scan failures.
- Release Evidence Packet schema bumped to `0.2` with a compact
  `tool_surface_diff` section derived from the report JSON.
- Added optional manifest-level HITL validation evidence mode under
  `validation:`. The scanner now reads local approval traces, override logs,
  high-risk auto-approval exclusions, and promotion criteria to structure
  evidence gaps for reviewers; it does not generate those runtime artifacts or
  certify readiness.
- Tightened HITL evidence wording and provenance. `SHIP-EVIDENCE-*` findings
  now describe missing or incomplete local review evidence without implying
  runtime controls are absent, and include deterministic
  `evidence.source_provenance[]` entries. `source_provenance` is excluded from
  finding fingerprints, so adding provenance does not rotate existing HITL
  baselines or suppressions.
- Release Evidence Packet schema bumped to `0.3` with
  `human_in_the_loop.runtime_control_disclaimer`,
  `human_in_the_loop.source_provenance[]`, and
  `human_in_the_loop.provenance_mode`.
- Added `samples/hitl_evidence_covered_agent`, a refund-domain fixture with
  local approval trace, override log, high-risk exclusion, and promotion
  criteria evidence.
- Added four `SHIP-EVIDENCE-*` checks. Existing baselines may surface these as
  new findings after upgrade when a manifest opts into `validation:`.
- Add `agents-shipgate scenario suggest` (target: `0.9.1`), a YAML export that
  fans out `report.json.suggested_scenarios[]` into concrete
  per-finding/per-tool dynamic validation steps.
- Added ranked next-action diagnostics: `detect --json` and `doctor --json`
  now emit `diagnostics: [...]` and `next_actions: [...]` blocks alongside
  the existing single-string `next_action` field. Coding-agent callers can
  recover from common first-run failures (missing manifest, zero tools,
  unresolved `CHANGE_ME`, missing source files, MCP/OpenAPI artifact-only
  workspaces, dynamic toolsets, production targets without permissions, and
  three negative-control cases) without consulting human-facing docs. Errors
  emitted under `AGENTS_SHIPGATE_AGENT_MODE=1` carry the same `next_actions`
  array. Diagnostic catalog and schema in [docs/diagnostics.md](docs/diagnostics.md).
- Behavior change: when a required `tool_sources[].path` does not
  resolve (file missing OR resolves outside the manifest directory),
  `agents-shipgate doctor --json` exits **0** with
  `unresolved_sources: [...]` and a `SHIP-DIAG-MISSING-SOURCE-FILE`
  diagnostic so an agent gets a routable next action. The non-JSON
  `agents-shipgate doctor` form prints the same diagnostic in
  human-readable form and exits **3** so interactive users still see a
  loud failure. `agents-shipgate scan` is unchanged — it still raises
  `InputParseError(3)` on the same condition regardless of `--json`.
- `DetectResult` gains a `workspace_signals` block (Python file count,
  `pyproject.toml`/`requirements.txt` presence, conventional dir hits) used
  by the new diagnostic resolvers to discriminate negative-control cases.
  The block is additive; existing fields are unchanged.

## 0.8.0 - 2026-05-05

- Report schema bumped to `v0.8`. New top-level required `release_decision` block:
  `{decision, reason, blockers, review_items, evidence_coverage, baseline_delta, fail_policy}`.
  - `decision` is one of `"blocked" | "review_required" | "passed"` and is the
    recommended release-gate signal for v0.8+ consumers.
  - `blockers` and `review_items` are reference-only entries
    (`id, fingerprint, check_id, severity, title, baseline_status`) — full
    Finding payloads stay in `findings[]`.
  - `release_decision` is **baseline-aware**: matched criticals appear in
    `review_items` (accepted debt), not `blockers`. Critical severity is
    **policy-independent** — even advisory CI surfaces a new critical as a
    blocker (with `would_fail_ci=false`).
  - `release_decision.fail_policy.exit_code` matches the process exit code
    one-for-one across all `ci_mode` × `fail_on` × `--baseline` combinations.
- `summary.status` is preserved byte-for-byte for backwards compatibility
  with v0.7 consumers. It stays baseline-blind (a baseline-matched critical
  still flips status to `release_blockers_detected`). The intentional
  divergence from `release_decision.decision` is documented in
  [STABILITY.md](STABILITY.md#release_decisiondecision-vs-summarystatus).
- `docs/report-schema.v0.8.json` added; `v0.7.json` retained as a frozen
  reference. JSON-schema validation catches missing `release_decision` on
  any emitted report.
- Markdown / GitHub Action / CLI summaries now lead with the Release
  Decision block (Decision → Reason → Blockers → Review items → Evidence
  coverage → Baseline delta → Fail policy). SARIF output is unchanged.
- GitHub Action exposes four new outputs: `decision`, `blocker_count`,
  `review_item_count`, `ci_would_fail`. Existing outputs (`status`,
  `critical_count`, `baseline_*`, `adk_*`, `report_*`, `exit_code`)
  unchanged.
- The release verdict path remains deterministic and LLM-free: no agent
  execution, tool call, model call, MCP connection, network access, or
  telemetry is added for v0.8.
- `exit_code_for_report()` refactored to share `effective_fail_on()` and
  `baseline_filtered_active()` helpers with `build_release_decision()`,
  so the standalone exit code and `release_decision.fail_policy.exit_code`
  cannot drift. New regression test pins this across the matrix.

## 0.7.0 - 2026-05-01

Adoption activation: makes the v0.6 features visible to humans and AI
coding agents on real repos, plus exposes per-check remediation
metadata so agents can route findings without re-walking the catalog.

- Agent-facing docs surface:
  - New "Should I run Shipgate on this PR?" trigger table in
    `AGENTS.md` with the soft-stop rule (don't skip MCP/OpenAPI-only
    repos that surface as `is_agent_project: false`).
  - New `docs/agent-recipes.md` — copy-pasteable AI-agent workflows
    for the canonical 4-call flow.
  - New `docs/autofix-policy.md` — four classes (safe / medium /
    manual / never), catalog-vs-Finding contract, strict derivation
    rule, three patch states, unknown-check-id fallback,
    `apply-patches --confidence` table, decision tree.
  - New `docs/minimal-real-configs.md` — per-framework references to
    runnable `samples/*` fixtures (no inline snippets to drift).
  - `docs/INDEX.md` cleanup: stale `report-schema.v0.5.json` link
    removed; current schema link now `report-schema.v0.7.json`.
  - `docs/quickstart.md` adds a "second 60 seconds" real-repo path.
- `CheckMetadata` extensions:
  - New `autofix_safe`, `requires_human_review`, `suggested_patch_kind`
    fields on every check (45 entries). `docs_url` populated for every
    check pointing at a stable `### SHIP-...` anchor in
    `docs/checks.md`. 7 new per-check sections added to `docs/checks.md`
    so every check has a stable anchor.
  - Catalog-level safety bools stay conservative — even checks whose
    generator usually produces a safe non-manual patch (stale-manifest
    removals, scope coverage) keep `autofix_safe: false` /
    `requires_human_review: true` because the generator can fall back
    to `ManualPatch` in edge cases (ambiguous duplicates, etc.).
    `suggested_patch_kind` is informational — describes what the
    generator targets when conditions are clean.
- `Finding` extensions + derivation:
  - Same four optional fields on every Finding, populated by
    `annotate_remediation` during scan. Three patch states handled
    distinctly:
    - `patches: None` (no `--suggest-patches`) → seed from
      CheckMetadata; safe-closed fallback for unknown check IDs
      (policy packs, third-party plugins).
    - `patches: []` (--suggest-patches ran but generator emitted
      nothing) → safe-closed shape with `suggested_patch_kind: "none"`.
      Does NOT fall back to catalog (the report carries no patches).
    - `patches: [...]` (non-empty) → strict derivation rule:
      `autofix_safe: true` ONLY when EVERY emitted patch is non-manual
      AND high-confidence. Mixed states fall to safe-closed.
  - `docs_url` always sourced from CheckMetadata (patches don't carry
    per-instance documentation URLs).
- Report schema bumped to `v0.7` per
  [STABILITY.md](STABILITY.md#stability-contract) ("`report_schema_version`
  bumps minor on additive changes"). `docs/report-schema.v0.7.json`
  added; `v0.6.json` retained as a frozen reference.
- `_run_id` excludes the four new derived fields plus `patches` so
  toggling `--suggest-patches` (or future enrichment fields) doesn't
  shift the hash. New regression test pins this.
- Plugin-loading isolation: every code path that reads the catalog
  during scan honors the scan's `plugins_enabled` setting, including
  the `_attach_patches` recommendation lookup.
  `AGENTS_SHIPGATE_ENABLE_PLUGINS=1 agents-shipgate scan --no-plugins`
  no longer loads plugins.
- Onboarding prompt rewrite: `prompts/add-shipgate-to-repo.md` now
  leads with the canonical 4-call flow (`detect → init --write --ci →
  scan --suggest-patches → apply-patches --json`) and includes the
  decision tree from `docs/autofix-policy.md`. Soft-stop rule
  documented inline. `apply-patches --json` flag added so the
  reporting step has structured data to read.
- Dual-copy prompt parity: byte-identical mirror between
  `prompts/` and `skills/agents-shipgate/prompts/` enforced by
  `tests/test_prompt_parity.py` so the two surfaces can't drift.
- Test coverage: 314 tests pass. New test files:
  `tests/test_remediation_metadata.py`,
  `tests/test_finding_remediation.py`,
  `tests/test_docs_links.py`,
  `tests/test_prompt_parity.py`,
  `tests/test_v07_metadata_roundtrip.py`.

## 0.6.0 - 2026-04-30

Agent-friendly adoption: compresses Shipgate setup into a single
tool-using turn for AI coding agents.

- Added `agents-shipgate detect` — read-only command that classifies a
  workspace as an agent project and reports which framework(s) it uses,
  with confidence and per-framework evidence.
- `agents-shipgate init` now auto-detects by default. Generated
  manifests are schema-valid (validated before write) and include
  framework-specific tool sources and config blocks (LangChain, CrewAI,
  Google ADK, OpenAI Agents SDK, Anthropic, OpenAI API). The legacy
  CHANGE_ME-heavy template is preserved under `--minimal`.
- Added `agents-shipgate init --ci` — opt-in flag that writes
  `.github/workflows/agents-shipgate.yml`. Orthogonal to `--write`:
  each gets its own overwrite-refusal check. Detects cross-workflow
  shipgate references and skips with a distinct message.
- Added `agents-shipgate scan --suggest-patches` — attaches Patch
  objects to every active finding (machine-applicable for the safe
  subset; ManualPatch for everything else). `Finding.patches` is
  absent when the flag is not set; non-opting JSON consumers see no
  contract change.
- Added `agents-shipgate apply-patches` — applies patches from a scan
  JSON report. File-grouped, single SHA per file, dry-run by default,
  containment-checked against the report's new `manifest_dir` field.
- v0.6 patch generators (manifest-target only):
  - High-confidence `RemovePointerPatch` for the 3 stale-manifest
    checks (SUPPRESSION, POLICY, RISK-OVERRIDE).
  - Medium-confidence `AppendPointerPatch` for
    `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (NOT applied at default
    `--confidence high` — adding scopes can encode policy choices).
  - Permanent `ManualPatch` (with anti-pattern instructions) for
    `SHIP-API-TRACE-{APPROVAL,CONFIRMATION}-MISSING` — flipping
    approved/confirmed in a trace patches the evidence, not the agent.
- Bumped report schema to v0.6 (additive: optional `Finding.patches`
  array; new top-level `manifest_dir`). v0.5 schema retained for
  reference.
- Anthropic-specific glob coverage in `init`: tools and policies
  matching `tools/anthropic-tools.json` and
  `policies/anthropic-policy.yaml` now populate the `anthropic:` block
  automatically.
- Added end-to-end agent task `02_three_command_flow` exercising the
  full `detect → init → scan → apply-patches` pipeline.
- Added `ruamel.yaml>=0.18` as a dependency for round-trip-preserving
  YAML edits in `apply-patches`.

## 0.5.1 - 2026-04-29

- Polished launch-facing docs after the v0.5.0 release.
- Updated active examples and discovery metadata to the v0.5.1 release tag.
- Added curated launch marketing and presentation assets while excluding them
  from PyPI source distributions.
- Fixed stale baseline-mode CLI help text.

## 0.5.0 - 2026-04-28

- Added static LangChain/LangGraph and CrewAI Python adapters with manifest
  source types, supplemental inventories, framework report blocks, fixtures,
  and self-check coverage.
- Added framework-specific checks for dynamic LangChain/CrewAI tool surfaces
  and missing function-tool metadata.
- Promoted GitLab CI and CircleCI to first-class integration recipes with
  advisory, strict baseline, artifact, multi-config, and tool-source trigger
  examples.
- Added report schema v0.5 for additive LangChain/CrewAI framework fields.
- Added a framework adapter checklist for future static framework support.
- Deduplicated `source_warnings`; baselines from 0.4.x may report a small
  number of resolved warning entries on first run after upgrade.

## 0.4.0 - 2026-04-27

- Added declarative YAML policy packs with manifest, CLI, report, SARIF, and GitHub Action support.
- Split `SHIP-API-OPERATIONAL-READINESS` into atomic OpenAI API operational readiness check IDs.
- Kept `SHIP-API-OPERATIONAL-READINESS` as a deprecated compatibility alias for suppressions, severity overrides, baseline matching, and check metadata.
- Removed the legacy top-level `check_severity_overrides` alias; use `checks.severity_overrides`.
- Added report schema v0.4 with `loaded_policy_packs` and stabilized Google ADK warnings in the framework surface.
- Added an internal framework adapter seam and documented runtime inventory as design-only.

## 0.3.0 - 2026-04-26

- Added static Google ADK support through `tool_sources[].type: google_adk` and supplemental `google_adk` manifest artifacts.
- Added ADK Python AST and Agent Config YAML extraction for agents, function tools, toolsets, callbacks/plugins, sub-agents, eval references, and explicit local inventories.
- Added six ADK readiness checks covering dynamic toolsets, unfiltered MCP toolsets, missing function metadata, long-running contracts, guardrail evidence, and production eval coverage.
- Added SARIF output via `--format sarif` and GitHub Action SARIF/baseline/ADK outputs.
- Added report schema v0.3 with a generic `frameworks.google_adk` surface summary.
- Added reusable local trace normalization for explicit trace/eval artifacts.

## 0.2.0 - 2026-04-26

- Added manifest-aware checks, deterministic report metadata, check severity overrides, `fail_on`, `init`, `doctor`, `explain`, multi-config scan support, and check entry-point hooks.
- Renamed the project to Agents Shipgate and hardened v0.1 release-readiness behavior.

## 0.1.0

- Initial Agents Shipgate MVP.
- Manifest-first scan over local MCP JSON, OpenAPI specs, and optional OpenAI Agents SDK AST metadata.
- Markdown and JSON reports.
- Advisory and strict CI modes.
- GitHub composite action.
