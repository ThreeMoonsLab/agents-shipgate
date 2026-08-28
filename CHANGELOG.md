# Changelog

## Unreleased

- **The report's `Root agent:` line names the agent instead of hashing it.**
  (#329) Every shipped sample printed `Root agent: agent_v1:7205d836…` at the
  head of the Agent Binding Surface section — a derived digest that appears in
  no file the adopter has, telling them which agent the whole section is about
  in the one vocabulary they cannot look anything up in. It now reads
  `Root agent: durable_order_agent [conductor_workflows]`, resolved through the
  same agent label index a binding gap uses, so the section header and a
  finding about that agent cannot spell it two different ways. `unresolved`
  is said only where an agent really could not be named — no root at all, or a
  root id no node carries; chaining back to the id would restore the digest on
  exactly the graphs that already read worst, and
  `binding_surface_facts.root_agent_id` still carries the identity in
  `report.json` for a bug report.

  **The sweep that was supposed to catch this could not see it.** The Markdown
  renderer escapes every value it prints, so the line reached
  `test_sample_markdown_speaks_the_adopters_vocabulary` as
  `agent\_v1:7205d836…` — and every derived id shape and internal term in
  `core.adopter_text` contains an underscore, which made that sweep close to
  vacuous over the whole half of the report that goes through
  `_safe_markdown_text`. The sweep now un-escapes each line first, against a
  `_unescape_markdown_text` defined as the escaper's inverse beside it, with a
  negative control asserting the raw escaped spelling passes the matcher while
  the sweep rejects it.

  **`unresolved` is only ever said about an agent.** Two states reach this
  line with a truthy `root_agent_id` and no agent behind it, and reading
  either as an unnameable agent describes a failure that did not happen —
  `Status: structural` and `Pass eligible: true` are printed around it.

  A report carrying no agent graph at all — the `legacy_direct` compatibility
  assessment `core.findings.report_builder` builds, and the schema default for
  a `report.json` written before the field existed — now reads `Root agent:
  none (tools bound directly, no agent graph)`.

  And a repository that declares exactly **one** reviewed
  `tool_sources[].binding` surface and observes no agent: `_select_root`
  returns that sole surface node, so the graph has a root id pointing at a
  `kind: tool_source`. That printed `Root agent: github_mcp` beside
  `Pass eligible: true` — announcing an agent object the repository does not
  have — while the *two*-surface form of the same repository printed the
  correct sentence. Whether a repository has an agent is not a function of how
  many sources it declared, so the answer is read from the node's `kind`, and
  both forms now say `none (graph rooted by declared tool sources)`.

  The `Entry points:` line beneath it (#432) resolves through the same index,
  and its own fallback is now `unresolved` rather than the raw `agent_v1:` id.
  The two lines had disagreed about the same agent one line apart — `Root
  agent: agent_v1:7205d836…` above `Entry points: durable_order_agent
  [conductor_workflows]` — which is the drift one shared index exists to
  prevent. Markdown-only: report, packet and verifier schemas are unchanged,
  and the five sample `report.md` goldens are regenerated.

- **A `codex_config` tool source no longer aborts the scan on any MCP server
  it finds.** A `tool_sources[]` row of `type: codex_config` failed outright
  with `InputParseError` — "A tool source loader reported tool 't_codex' as
  belonging to a different source than the one it was read from" — whenever
  the workspace contained a `.mcp.json` or `.codex/config.toml` declaring a
  server. Since a server with no enumerable tools still mints a `<server>.*`
  wildcard, *any* server at all reached it: the source type worked only on
  workspaces that had no MCP declarations to read, which is the opposite of
  what it is for.

  `inputs.mcp_manifest` minted a per-server id for every tool
  (`mcp_json:<server>`, `codex_config_mcp:<server>`) and then wrapped a whole
  file's servers in one `LoadedToolSource` named for the *file*
  (`codex_config_mcp:<path>`). `core.tool_identity` requires a loaded source
  to name the id its tools carry — a tool arriving under another source's
  name is a loader defect — so the two spellings could never agree.

  **The loader now emits one `LoadedToolSource` per MCP server**, built from
  the server, so there is no second place that spells the id. The minted ids
  themselves are unchanged and stay deliberately path-free: an MCP capability
  is its server and tool, so moving a `.mcp.json` remains a pure rename rather
  than a capability addition (`mcp audit`). One source per *file* was the
  other candidate repair and is wrong: `_native_locator` is the bare tool name
  for MCP-like sources, so grouping a file's servers together made a legal
  `.mcp.json` whose two servers both expose `query` fail with "defines the
  tool 'query' more than once".

  No version moves and no schema changes: `report_schema_version`,
  `contract_version`, and every published schema document are unchanged.
  Nothing that previously produced a report changes shape, because no
  workspace with an MCP server could produce one.

  **Known gap.** Two files of the same kind declaring the same server name —
  `pkg_a/.mcp.json` and `pkg_b/.mcp.json` both declaring `github`, which is
  likely in a monorepo since server names are conventional — still fail:
  `scan` with a duplicate-observation message that names a file the manifest
  never listed twice, and `mcp audit` (already, independently of this change)
  with an unhandled `ConfigError` and no JSON envelope. Path-free capability
  identity and one-observation-per-identity are both deliberate, and
  reconciling them is tracked separately.

- **The loader-contract failure now offers a way forward.** "That is a defect
  in the loader, not in this repository's configuration" was accurate and
  terminal: the reader cannot edit an adapter they do not own, and had just
  been told their own configuration was not at fault. When the dispatcher can
  prove which `tool_sources` entry produced the read, the message now adds
  *Until it is fixed, removing the tool_sources entry 'x' from shipgate.yaml
  lets the scan run without the tools that entry reads* — after the diagnosis,
  and named as the workaround it is rather than as the repair. `details` gains
  `configured_source_id` alongside the existing keys. A source no configured
  row produced is offered nothing, rather than an entry the reader could not
  find.

- **A new evidence gap now says which subject left the analysed surface.**
  (#433) The exclusion ledger from #403 records precisely which subject each
  stage removed — `("binding", "find_duplicate [github_mcp]", "evidence_gap")`
  — and no human-facing surface carried it. A reviewer of
  `github/github-mcp-server#3020`, a PR adding exactly one tool, was told
  "1 of 83 evidence gap(s) are new in this diff" and never *what* the one was;
  the blockers were pre-existing debt about the other 115 tools, so the
  `Most severe:` clause that had been carrying the subject in other cases was
  about something unrelated to the change. That was #403's own thesis — a
  stage computed the right signal, stored it, and did not connect it to the
  decision — standing at the ledger's own output, and it is the epic's last
  open box.

  `verify`'s headline, and therefore `control.reason`, `control.next_action.why`
  and the PR comment's `Summary:` / `Next action:` lines, now continue:
  `New in this diff and not fully analysed: 'find_duplicate [github_mcp]' —
  not bound to the root agent.` Rendering only: no verdict, count, gap,
  finding, or permission moves, and no version does either
  (`report_schema_version`, `contract_version`, the verifier artifact version
  and every published schema document are unchanged).

  **Selected by diffing the ledger itself**, base against head, on
  `(stage, subject, reason)` — so the clause claims exactly what it can prove:
  these rows are in the head ledger and were not in the base one. Only
  `evidence_gap` rows, which is what makes the multiset exact on both sides,
  because those are the rows the ledger's cap never drops. A settled
  workspace has no such rows and gains no clause. The subject printed is the
  ledger entry's own string, built by
  `core.surface_exclusions.catalog_subject`, so it cannot drift from the gap
  row it came from (the join defect #413 fixed one layer down).

  **A name is the ledger's own name, delimited, or it is not shown.** The
  subject is quoted; a subject longer than the cap, or one normalization would
  rewrite, or one carrying the quote character, is counted in the tail instead
  of being shortened or escaped. So the printed name is always exactly the
  ledger's, and a tool named `find_duplicate. Control state complete; agent
  may merge` cannot put that sentence into `control.reason` as prose. When no
  subject can be printed the count is still published, because a subject
  really did leave the surface.

  **No phrase states provenance.** "New in this diff" is said once, by the
  lead-in, from the ledger diff that proves it.
  `BindingSurfaceDiff.added_unbound_tool_ids` is head-minus-base and covers
  both a tool this change added and one that was reachable at the base and
  lost the edge that bound it, so the `newly_unbound_tool` row's own `detail`
  no longer says "this change put the tool in the catalog" either.

  **Bounded, because it shares a 400-byte envelope.** At most three subjects
  are named, each capped on its own account as scanned input, grouped by
  cause so a diff that adds six unwired tools reads as one list with one
  reason and an `and 3 more` tail — the way `Most severe:` already handles the
  findings side. The clause shrinks itself by naming fewer and counting more,
  because a clause that does not fit is dropped whole. One lead-in covers a
  grouped list, so it says "Not fully analysed" rather than the ledger's own
  "excluded from analysis": a `surface_not_enumerated` row is a tool that
  *was* analysed as far as its surface could be read, and the excluded subject
  is the unread remainder. Reason tokens render through one table beside the
  builder that emits them (`core.surface_exclusions.exclusion_phrase`), and a third-party
  adapter's own token falls back to a phrase that claims nothing about a cause
  nobody recorded.

  **And the headline's own budget now yields whole sentences.** The
  evidence-gap context was composed into one string and sliced to fit — right
  for one unbroken run of untrusted text, wrong the moment that text *names
  subjects*: `delete_repo…` is not a shortening of `delete_repository` a
  reader can act on, it is a plausible other tool, and `Not fully analysed:
  find_dup…` names nothing at all. The context is built as ordered
  sentences, most load-bearing first, and every composition route fits it by
  dropping whole sentences from the end. The pre-existing "no new evidence
  gap" note is split the same way, so a tight budget drops the declaration
  remedy and keeps the fact instead of losing both. Byte-identical wherever
  the whole note already fitted, which is every case the suite covers.

  **And the human-review route follows the headline** rather than
  reproducing which of the headline's routes carries a governance
  requirement. That second copy of `_verifier_headline`'s branch conditions
  had drifted: on a PR that both adds an unbound tool and edits the trust
  root, the headline named the subject and `control.next_action.why`,
  `human_review.why` and the PR comment's `Next action:` line did not.
  `_verifier_headline` publishes every governance requirement as a reserved
  suffix, so the headline always states it, and the human-review reason can
  simply be the headline.
- **A published tool surface is one reviewed declaration, not one row per
  tool.** (#432) Binding an MCP server's own surface required naming every
  tool individually under `agent_bindings.declarations[].tools` — 116 selector
  rows for `github/github-mcp-server` to state a fact that is structurally
  true of the source, and the point at which an adopter stops. The two shorter
  spellings a reader reaches for both dead-ended: `agent_bindings.root` naming
  the source reported `ambiguous_root_agent` as though a better selector
  existed, and a `"*"` selector was matched as a literal tool name. Until one
  of them was written `reachable_tools` was `0`, nothing downstream ran, and
  the verdict was `insufficient_evidence` whatever the change did.

  **`tool_sources[].binding`** — `{complete: true, reason}` — states once that
  a source's published surface *is* the surface under review. It sits where
  `tool_sources[].authority` sits and makes the same shape of argument:
  binding, like authority, is a fact about the source rather than about each
  function it exposes. For an agent that is not true — a catalog may hold 63
  OpenAPI operations of which the agent wires 5, and #385 drew that boundary
  deliberately — so the block is opt-in per source and changes nothing where
  it is not written. It is additive and **widening**: it can only move tools
  *into* the analysed surface, where every check then judges them.

  **Both dead ends are routes.** When nothing observed an agent object, the root
  gap no longer prescribes a selector that cannot exist, and a `tools:` selector
  spelled as a pattern (`{tool: "*"}`) is told that selectors name one tool
  exactly and which statement that spelling was reaching for. It says so, routes to
  `shipgate.yaml#tool_sources[].binding`, and the declaration scaffold writes
  one block per configured source carrying the ids read off the surface and
  `<REVIEW_REQUIRED>` for both halves of the judgement. A catalog whose tools
  come from a per-scan adapter has no row to declare on, so it keeps the
  closed-world `agent_bindings.declarations` route instead of being handed a
  remedy the schema rejects. Once a source is declared, `root: {object: <id>,
  source_id: <id>}` resolves to it.

  **It stays a human declaration.** Inferring "this source binds everything"
  from the source's own content is the #268 attack; the block is refused in
  agent-authored `tool_sources` proposals and is a human-owned placeholder, so
  `doctor` will not publish an executable edit for it. A reviewed declaration
  that binds no tool fails closed rather than proving a graph over an empty
  surface, and two reviewed closed-world statements about one node — a
  declared surface selected as the root, plus an `agent: root` declaration
  listing something else — raise `conflicting_binding_evidence` instead of
  being silently unioned into a proven graph.

  **A graph can now be rooted by something that is not an agent, and says so.**
  A repository publishing two servers has two entry points and no root agent,
  which is not the same fact as "the root could not be identified".
  `binding_surface_facts.entry_point_agent_ids` is every node the reachability
  walk started from — empty exactly when nothing rooted the graph, and equal to
  `[root_agent_id]` for every graph a prior release could produce — and
  `agents[].kind` (`agent` | `tool_source`) says what each node in that array
  is. The Markdown report stops calling the deliberate state `unresolved`.

  Report schema `0.41 → 0.42`; both fields are additive, v0.41 is frozen and
  hash-pinned, and every prior version is read forward. The manifest field is
  additive too, and a CLI that predates it rejects the key with a routable
  `ConfigError` (exit 2) rather than ignoring a reviewed claim.

- **A coding agent can now answer the declaration questions the scanner
  already knows the answers to.** (#410 §D) The questionnaire (#410 increment
  2) told a person which blanks were owed; it had no way to say that most of
  them were the scan restating what it had just read, and no way for the agent
  already holding the branch to write those down. `verify` publishes one new
  route — `control.next_action.kind: "confirm_declarations"` — carrying the
  exact `apply-patches` command that writes those answers and the question
  list, each row tagged `authorable_by: "coding_agent" | "human"`. An agent
  applies what it may, commits it to the branch, and stops at the rest **by
  name** instead of at "human review required". Report schema `0.40 → 0.41`,
  packet `0.16 → 0.17`, verifier `0.13 → 0.14`, runtime contract `24 → 25`
  (`minimum_control_contract_version` stays `21`: the shared `AgentControl`
  union is unchanged, exactly as for the setup `edit` route).

  **Authorship is decided by content, never by who is running.** A row is
  `authorable_by: "coding_agent"` only where the scan filled every blank in its
  `declaration_template` — an effect, drawn from the closed `ActionEffect`
  vocabulary, never weaker than any reading it observed — *and* the question is
  not one that asks a person to look again. Every authority block, override and
  inventory keeps its `<REVIEW_REQUIRED>` blank and its `"human"` tag; so does
  a `declaration_drift` row, whose template is complete because it restates a
  confirmed answer beside the pin that moved (#410 §E) and whose whole purpose
  an agent would close by re-stamping it. The rules are enforced on the models
  rather than in the builders that set them, so a gap kind added later cannot
  inherit a licence its author never considered.

  **What the agent gains is a pen, not a decision.** The new `declare_action`
  patch kind is outside the default `apply-patches --kinds`, so no existing
  pipeline (`bootstrap` included) starts writing manifests. The schema binds it
  to the row that published it: the patch is *exactly* the
  `declaration_template`, split into the keys that name the action and the
  fields that are written, at high confidence, from the closed effect
  vocabulary — a row cannot advertise an evidence-derived tag beside a patch
  that writes something else. It writes only into fields the manifest leaves
  silent; a row that already answers one differently, two equally compatible
  rows naming one tool, or a manifest that moved since the scan is refused
  outright — exit 5, nothing written, because an agent that re-ran verify after
  a silent no-op would loop against an unchanged file forever. Same-name tools
  from two providers are supported rather than refused: rows are matched on the
  qualifiers they actually share. `requires_human_review` stays `true` on every
  evidence-gap row and the route's `permissions` are publish-only
  (`edit`/`commit`/`push`/`update_pr`, never `merge` or `report_complete`):
  writing a declaration touches the trust root, so a person still merges it. A
  weakening written by hand is not blocked at the file — it is answered at the
  next scan by `declaration_below_inferred_evidence` (#409).

  The route fires only where a declaration is what the verdict is short of:
  `insufficient_evidence`, no blockers, no proven policy weakening, a
  working-tree run (a ref-bound rerun would re-scan the commit the edit is not
  in yet — the same precondition the mechanical repair route has always
  carried), and a report that actually carries the patches the command would
  apply. That last one is why declaration patches are emitted on every scan
  rather than under `--suggest-patches`: a route may not name a step the report
  it points at does not contain.

  `next_action.patch.target_path` is **relative to `manifest_dir`**, unlike the
  absolute `target_file` the pointer patches carry. The row is embedded by the
  evidence packet, the SARIF file and a cached base scan, all of which travel;
  a `verify --base` run scans an archived checkout, so the absolute form named
  a temporary directory that no longer existed when anyone read the artifact
  and changed on every run, moving digests that are supposed to be
  reproducible. Relative removes the class instead of asking each consumer to
  strip it, and makes containment structural — `apply-patches` resolves it
  under `manifest_dir` and can no longer be handed a path that escapes. Base
  evidence additionally drops the patch outright (a base report describes a
  commit nobody is editing), and `BASE_CACHE_KEY_EPOCH` moves to `4` so no
  cached entry written mid-flight is served back.

  The shipped agent instructions carry the exception, because without it they
  said the opposite: `AGENTS.md` and all four copies of `fix-top-finding.md`
  told an agent that every declaration row is a human's and that no
  evidence-gap row ever reaches `fix_task.actor == "coding_agent"`. They now
  state the one narrow case and keep every prohibition around it, and a test
  pins the three shipped copies byte-identical to each other.

  The control envelope's published size budget is re-derived, `4096 → 6144`
  bytes: a route carrying a list is a third variable component the old number
  was never derived against, and a question row measures about 0.4 KiB. The
  list is capped at six rows — more than the measured worst case produces,
  since per-*source* authority folding turns a 117-tool repository into one
  question — and the printed prefix leads with the human-owned rows, because a
  drafted row is answered by the command whether or not it is printed while a
  human-owned row is what the agent has to hand a person.

  Three fixes fell out of building it. `VerifierArtifact` demanded that an
  `agent_action_required` repair route's command equal the fix task's *rerun*
  command — which forbade the one thing such a route exists to publish, and
  which `test_control_next_action_follows_agent_safe_fix_task` had always
  asserted the opposite of. No verify run had reached the mechanical route and
  built an artifact from it, so nothing caught the contradiction; the invariant
  now says what it always meant, that a route may not name a command the fix
  task does not authorize. `apply-patches` hashes the bytes on disk rather than
  decoded text, because reading as text normalizes CRLF to LF and an untouched
  CRLF manifest therefore reported drift for ever, and it re-emits the newline
  style it found. And it pins YAML sequence indentation to the style `init`
  writes, because round-tripping re-indented every unrelated list in the
  manifest and buried a one-line declaration in whitespace.

- **Control packs: the rules layer, chosen once at `init`.** (#410 §F) The rule
  that a financial write needs approval, an audit log, and idempotency was
  written four times in the engine, was not selectable, was not named, and was
  stated nowhere the adopter reads — so seven findings said "lacks a declared
  approval policy" and none of them said *what* requires one.

  `policies.control_pack` now names the effect → required-controls rule set for
  the whole repository: `default` (today's rules exactly), `financial-strict`,
  or `read-only-agent`. `shipgate init --control-pack <id>` writes the
  selection with a glossary of the alternatives above it, and `init --json`
  reports the choice and every answer it takes under `control_pack`. Omitting
  the key means `default`, so every existing manifest keeps its verdict — the
  sample goldens' findings, severities, `blocks_release`, and release decisions
  are unchanged.

  **Selecting a pack can only tighten the gate.** Every built-in pack requires
  at least what `default` requires — asserted at import, and pinned by a real
  scan across every (pack × effect) pair plus a whole-finding-set comparison,
  because a table read by eye is not the property that matters. A pack also
  decides only which control findings *fire*: the obligation lattice that says
  whether a declared effect covers an inferred one stays the built-in one, so a
  pack requiring identical controls for two effects still cannot let a
  declaration of one discharge the other (#413).

  The engine now reads that one table rather than mirroring it. The three
  dedicated action-control branches, the high-impact approval rule, and the
  tool-level `SHIP-POLICY-APPROVAL-MISSING` / `SHIP-POLICY-CONFIRMATION-MISSING`
  effect sets were four hand-maintained copies of the same rows; they are one
  now. Effects with no control check of their own report a pack obligation
  through `SHIP-ACTION-POLICY-VIOLATION` at `high`, naming the rule in
  `evidence.policy_id` as `control-pack:<pack>:<effect>` — and, like the four
  dedicated families, a `checks.ignore` entry records the exception without
  waiving the blocker.

  `scan` stdout and `report.md` now name the rule that wanted each missing
  control, once per rule instead of once per tool: *"financial write requires
  approval.required, safeguards.audit_log, and safeguards.idempotency — 3
  actions short"*. Both render one projection, so they cannot report different
  counts from one report. `SHIP-ACTION-POLICY-VIOLATION`'s built-in title says
  "without required controls" rather than "without approval", which under a
  stricter pack was telling an action that *has* approval that it does not.

  **A pack move is a policy weakening, and the gate says so.** Every field in
  the effective-policy snapshot answers *does the same finding still block?*;
  a control pack answers *does the same action still produce the finding?*,
  which is the other way a gate gets weaker — and the one a base-vs-head
  comparison could not see. `effective_policy.control_pack` (report schema
  `0.39 → 0.40`, additive, v0.39 frozen) publishes the pack in force, and
  `SHIP-VERIFY-POLICY-WEAKENED` gains `kind: control_pack_weakened`: one
  finding per pack move carrying `removed_controls[] = {effect, controls}`.
  A base snapshot with no pack predates the field and is compared as
  `default`, so the "no pack is weaker than default" invariant is enforced by
  the comparison rather than assumed by it. A snapshot naming a pack the build
  cannot resolve is the other case and is not read as "no weakening": it
  routes to `SHIP-VERIFY-POLICY-BASE-ABSENT` with
  `kind: control_pack_unrecognized`, the reason code that says the comparison
  could not be made.

  **A finding says which rules it is about, and identity follows.** Every
  control finding stamps `evidence.control_pack` and `evidence.control_effects`
  — the effects the rule actually matched, not its whole category, because one
  id serves both high-impact effects and recovering them from it reported a
  code-execution action as also operating on production. Both are excluded
  from the fingerprint, so baselines recorded before the fields keep matching.
  `run_id` now hashes the pack's id, version, and canonical obligations: two
  manifests enforcing different policy are different runs even where neither
  produces a control finding. The pack-only `policy_id` is
  `control-pack:<effects>` with **no** pack name — it is a fingerprint input,
  and two packs asking the same thing state one rule, so naming the pack would
  re-open a baseline entry on a move that changed nothing. That prefix is
  reserved: `action_surface.policies[].id` rejects it at manifest load, and
  non-waivability is decided from `evidence.control_pack` rather than from the
  id string, so a user policy cannot inherit the treatment.

  No new check ids and no new CLI command. `init`'s recovery routes repeat the
  selected pack, so following an emitted `next_action` completes the setup that
  was asked for. Switching to a stricter pack re-opens baseline entries for
  control findings whose requirements grew — an acceptance recorded under
  looser rules should not carry into tighter ones — while a move between packs
  that require the same controls re-opens nothing.

- **Human-facing findings are grouped by subject, and a recommendation names
  only what is missing.** (#364) A scan of four money-moving tools produced
  seventeen findings across five check families. The summary showed three of
  them, and all three were the *same* check on sibling tools — so scopes,
  idempotency, owners and guardrails were never mentioned at all. Severity is
  not the axis a reader acts along: they open one tool, fix what is wrong with
  it, and move on. The subject is now the group key on all three human
  surfaces (`scan` stdout, `report.md`, the PR comment), with severity and
  blocking status as attributes of each row, a location hoisted to the heading
  when every row shares one, and every truncation stating what it hid.

  Separately, the finding a reader would open first told them to declare a
  control the same finding's `evidence.missing` says they had already
  declared: the sentence was a per-check literal naming every control the
  effect obliges, while the evidence named the subset actually absent.
  Following it costs a round and returns the reader to the same finding. The
  three built-in control checks now build the evidence, the sentence, and the
  predicate row from **one** `missing` list at one call site, so they agree by
  construction rather than by review; `SHIP-ACTION-POLICY-VIOLATION` stops
  naming both high-impact effects on an action that has one. Where nothing is
  declared the sentence is unchanged, which is why no shipped sample's
  `recommendation` moved.

  Presentation only. `findings[]`, fingerprints, counts, severities,
  `blocks_release`, SARIF, and the release decision are untouched —
  `report.json` stays the flat per-finding record automation consumes, and the
  sample `report.json` goldens are byte-identical.

  Both PR-comment styles carry the block. `capability-review` is the default
  and `findings` is the legacy style being retired, so wiring it only into the
  latter would have shipped the change to nobody — the comment a reviewer
  actually gets said what *moved* and nothing about what is wrong per tool.

  Three rules keep a row honest about what it is saying. A row only renders
  `missing: …` when the check wrote that list as plain strings; the
  action-policy checks write `{"path", "expected"}` rows for *both* an absent
  path and a present-but-wrong value, so flattening them said
  `missing: safeguards.dry_run` about an action that declares `dry_run` and
  collapsed two policies requiring one path into one indistinguishable row —
  those keep their own title and their adopter-authored recommendation. A
  location falls back to `location` before `ref`, because most adapters
  populate `ref="agent.py"` + `location="agent.py:5"` and leave `path` unset;
  without it four findings on four functions rendered one suffix and then
  shared it. And a path is escaped, never trimmed, so a filename with a
  leading space stays that filename.

  A finding carrying a tool *name* and no id — `SHIP-BASELINE-INTEGRITY-*`,
  copied from a historical baseline entry — is not resolved through the
  current catalog. Uniqueness today cannot establish identity then, and the
  missing `[provider]` qualifier is the signal that the two are not known to
  be the same tool.

  Two joins had to get stricter to make the grouping honest. A group blocks
  when the *release decision* names one of its findings as a blocker, not when
  a finding carries `blocks_release` — a baseline separates those, filing
  accepted debt as a review item while the flag stays true. And a finding is
  matched to a decision item by id, then by fingerprint for an item with no
  id, then by check id and title for an item with neither, each tier holding
  only what the tier above could not: two findings can share a fingerprint,
  and `samples/conductor_agent` ships two that share a check id and a title.
  Within a group, blocking rows sort ahead of severity: a subject whose only
  blocker sorted last by check id showed BLOCKS RELEASE above three rows that
  do not block, with the one that does hidden under "and 2 more findings".

- **The questionnaire asks the unread questions first.** The declaration
  questionnaire promised an order — "by how much answering can move the
  verdict" — and delivered the opposite of it. It ranked each question by the
  effect the scan had *already inferred* for the action, and a pre-filled
  proposal is offered on exactly the same condition, so the two mechanisms ran
  off one signal: **every question that arrived with a draft answer outranked
  every question that arrived blank.** On the fifth `adk-samples#1745` walk
  that put three already-drafted mail tools at Q2–Q4 and the financial write —
  the single question that produces both `critical` blockers once answered — at
  Q6, behind three drafts a reader had to confirm first
  ([#419](https://github.com/ThreeMoonsLab/agents-shipgate/issues/419)).

  *Rank by the ceiling, not by the floor.* Observed risk and "how much can
  answering this move the verdict" are not the same quantity, and the header
  claimed the second. An action nothing has bounded is not a low-risk action;
  it is an unmeasured one, its answer can still turn out to be `destructive`,
  and it is exactly where a human answer carries new information. A question
  about an unbounded action now sorts above every bounded one, and the bounded
  ones keep their old order among themselves — strongest first. On the same
  walk the financial write moves from Q6 to Q3 and all three drafts move to the
  end.

  *Bounded is not the same test as draftable.* A reviewed declaration and
  policy-eligible source evidence bound an action even when what they establish
  is `read`, and the rule that decides whether to pre-fill a value cannot say
  so: it refuses to draft `effect: read` from anything, because a confirmed
  guess of `read` is the one direction that loses safety. Ranking on that rule
  would send an OpenAPI `GET` named `delete_account` to the top of the file
  with its name breaking the tie — the same defect inverted — so ordering asks
  its own question. A heuristic reading of `read` still bounds nothing: this
  resolver may not act on it, so the answer remains open.

  *And a name breaks the tie among blanks.* Where nothing was observed there is
  nothing to rank by, so the questionnaire falls back to the shape of the
  action's name — mutating, neutral, retrieving — using the keyword vocabulary
  the scanner already owns. This needs no trust and is given none: it is
  consulted only among actions the scan measured nothing about, it cannot
  reorder an action the scan did read, and it never reaches a claim, an issue,
  or a verdict. Getting it wrong costs a reader one place in a list they have
  to finish either way.

  The header sentence now states the order the file actually uses — including
  the heuristic-read case, where a block prints a reading and is still
  unbounded — and a test renders the file and checks the two against each
  other. A blank with no reading at all now says so at the block, since the
  header explains that the top of the file is the unbounded half and silence
  read as "nothing to see here".

  *Published contract.* `report.json` /
  `semantic_coverage.declaration_questions.open_questions[]` documented itself
  as "highest-acting action first", which is no longer what it is. The model
  docstring is emitted verbatim into the report, packet, and verifier schemas,
  so it and `docs/agent-contract-current.md` now describe the ranking above and
  say plainly that position is not severity: the action at the top is the one
  *least* is known about. Field shapes are unchanged.
- **A confirmed declaration is pinned to the evidence behind it.** Declarations
  matched by name and nothing ever re-opened one, so a green gate at month
  twelve could rest on a description of a function that no longer does what it
  did. `action_surface.actions[].basis` records which evidence an effect answer
  was given against, as `confirmed:<digest>`; every scan re-derives it and
  compares. Equal is complete silence. Different re-opens the question as a
  `declaration_drift` evidence gap that names what the action reads as now,
  hands over the new pin, and is closed by re-reading and re-confirming.

  The digest is what the questionnaire already showed the reviewer — the
  effects the scan *observed* — so "every answer is pinned to the evidence that
  justified it" is literal rather than approximate. It deliberately does not
  digest the producers: a second heuristic reading an effect somebody already
  answered is not new information about the action, and digesting it would let
  a shipgate release re-open every pinned declaration on every adopter at once.
  It is also stable across the arrival of the answer itself, which is the one
  property that would otherwise make pinning worse than not pinning: confirm a
  proposal, paste the `basis` line the scaffold stamped, rescan, and nothing is
  raised.

  Additive and unpinned-by-default: every manifest written before this field
  existed behaves exactly as it did. `suggested-declarations.yaml` stamps the
  pin on every effect answer it offers, so new answers arrive pinned; to pin an
  existing declaration, write any short placeholder (`basis: confirmed:0`) and
  rescan — the drift row names the value. A pin is a fact about the scan rather
  than a judgement, so it may be pre-filled and can never make an action
  pass-eligible on its own.

  `declaration_drift` is a different statement from
  `declaration_below_inferred_evidence`, and a change that adds a stronger
  reading raises both: one asks whether the declaration is *weaker than*
  today's evidence, the other whether today's evidence is what was answered at
  all. Each is closed by a different edit.

- **`environment.target: template`, for a repository that ships to be copied.**
  A sample has no deployment, so it has no credentials, and asking each of its
  actions which credential it runs with asks a question the repository cannot
  answer in principle — both repositories the adoption walks used were of
  exactly this kind, and `authority: {mode: none}` written twelve times is the
  same claim spelled at cost. Declaring `template` answers the authority
  dimension once, for every action that does not say otherwise.

  A default, never an override, and it applies only where nothing else answers
  the question: an action row's `authority`, a `tool_sources[].authority`
  block, an action's own `scopes:` list, and **anything the source publishes**
  all win over it. That last one is the whole safety argument — the reviewed
  record supplies mode, auth type, credential mode, and permission list
  together, so applying it over a source that published any of the four erases
  what that source proved. And never silent: every action it answers for is one
  semantic review concern, so a template repository can reach `review_required`
  and never `passed`. Stating real authority stays the price of a green gate.

- **`SHIP-TRUST-MANIFEST-UNPROTECTED` reads the file GitHub would read.**
  Protection is credited only from a CODEOWNERS that covers the manifest **and
  covers itself** — a rule set owning `shipgate.yaml` but not `CODEOWNERS`
  describes a protection one edit deep — and it fails closed everywhere the
  forge would not honour the rule: outside a git checkout, on a file of 3 MB or
  more (which GitHub ignores entirely, with no fallback to a lower-precedence
  location), and on tokens GitHub does not accept as owners. `docs/*` no longer
  matches further-nested files, which GitHub documents and gitignore does not.
  The three CODEOWNERS locations are now trust roots themselves, so a change to
  the file that decides protection is classified as one.

- **`SHIP-TRUST-MANIFEST-UNPROTECTED` — who may change the gate.** Every verdict
  rests on the manifest, so a repository that lets it change unreviewed has a
  gate the gated work can turn off. Attestation is the PR review of a protected
  file rather than a separate ceremony, and CODEOWNERS is the half of that a
  checkout can prove. The finding fires only where the manifest is load-bearing
  — the manifest's own `ci.mode: strict` — and it never moves a verdict:
  `low`, never a review item, because branch protection lives in repository
  settings no file here can read and deciding on the visible half alone would
  be the pretending it exists to avoid.

- **`doctor` says which rung of the adoption ladder you are on.** Every
  intermediate state of an adoption reads like a failure: a manifest with no
  declarations reports `insufficient_evidence`, which is accurate and sounds
  broken. `doctor` now names the rung — 0 Audit, 1 Gate the delta, 2 Answer on
  touch, 3 Strict — says what it is worth on its own, and names only the
  conditions that are actually unmet to reach the next one. Published on
  `doctor --json` as `adoption`. A workspace with no manifest is told it is on
  rung 0 rather than only that a file is missing, with the exact
  `init --workspace … --write` invocation that leaves it.

  A rung describes what a repository has **declared**, and says so. It does not
  predict a verdict — a fully structural surface owes no declaration questions
  and can pass from rung 1 — and it does not claim enforcement. `ci.mode:
  strict` is the manifest's own statement, while the workflow that runs
  Shipgate passes its own `ci_mode` (the generated one ships `advisory`), and
  branch protection is a repository setting no file in a checkout can read;
  rung 3 names both limits. Nor is a manifest a workflow: `init` installs one
  only with `--ci`, so rung 1 describes a gate that *can run here* rather than
  one running on every pull request, and points at `--ci` for that. The rungs
  are also not a cumulative chain: a repository whose surface resolves
  structurally may never be asked a declaration question, and would otherwise
  be stranded at rung 1 forever.

- **A pin re-opens when authoritative evidence is replaced, not only when a
  reading appears.** The digest covered the effects a scan observed but not
  their *strength*, so a tool published with `readOnlyHint: true` beside a
  `read_only` keyword hint kept the same pin after the annotation was deleted:
  it still read `read`, from the heuristic alone. `read` is the worst
  classification to lose it on — a heuristic may never establish read-only
  (#357) — so a safety-sensitive answer survived on evidence that could not
  have produced it. The digest now covers `(reading, strongest evidence class)`,
  which keeps corroboration quiet (a second heuristic agreeing with an
  annotation changes nothing) while a replacement moves the pin.
  `evidence_gaps[].next_action.observed_readings[].policy_eligible` publishes
  that half, so a consumer can reproduce the pin from the row it is printed on,
  and the questionnaire marks a heuristic-only reading as such.

- **Existing `capabilities.lock.json` files keep loading.** The lock schema bump
  froze `0.7` without teaching the reader about it, so every committed v0.7 lock
  started failing to load — v0.7 had been readable only because it *was* the
  current constant, and nothing moved it into the compatibility set when the
  next bump took that constant. It is there now, with its historical capability
  standard pinned literally rather than defaulting to the current one. The guard
  walks the published `docs/capability-lock-schema.v*.json` set instead of a
  hardcoded version, so the next bump cannot repeat it: the old test pinned
  `0.6` and kept passing throughout.

- **A merged declaration block reads in manifest field order.** Two evidence-gap
  rows about one action are merged into one block to paste, and the merge kept
  whichever order the rows arrived in — a drift row folded into a
  below-evidence row put `basis` in the middle and the declared `effect`
  underneath it. Blocks now render in the order the manifest itself uses, taken
  from the model rather than restated.

  Report schema 0.38 → 0.39, packet 0.15 → 0.16, verifier 0.12 → 0.13,
  capability lock 0.7 → 0.8, capability-lock diff 0.8 → 0.9; all additive, all
  prior versions frozen, hash-pinned, and read forward.

- **One action, one permission list, with no reviewed authority either.** A
  manifest row that listed `scopes:` and declared no `authority:` block at
  either site turned `verify --base` into `Internal error` (exit 4) on a legal
  manifest. The action's permissions were spelled twice and the two spellings
  disagreed: the action lens took the row's list when it had one and the
  source's auth scopes otherwise, while the authority dimension took the row's
  list only where a reviewed record existed. `CapabilityFactV1` requires the
  two to project one list, so where they disagreed, rebuilding a capability
  fact from a serialized `ActionFact` raised — and that rebuild happens on
  exactly one route, the MCP capability comparison against a base scan. A
  plain `scan` never reaches it, which is why no sample and no scan-level test
  saw this.

  Declaring authority once per source closed the reviewed half of that by
  normalizing both sites into one record. This closes the rest: one resolver
  (`core.semantic_assessment.resolve_action_scopes`) decides an action's
  permission list, read by the action lens and the authority dimension alike,
  rather than teaching the capability builder to paper over a disagreement it
  would then have to keep tolerating. Because the shared rule is the one the
  lens already applied, no shape that resolves today resolves differently —
  every list that moves belongs to a shape that was exit 4. A sweep over
  source authority × declaration row now pins it, and the parity assertion
  added with the source-level block covers the bare-`scopes` shape it had to
  leave out.

  *And a row cannot quietly shrink a grant.* Publishing the row's list on the
  authority dimension is also what would let `scopes: [crm.read]` erase a
  `crm.write` grant the source proves, with a `structural` status and nothing
  raised. The subset rule a reviewed authority is held to now reads the
  *resolved* list, so a bare `scopes:` list is held to it too and reports
  `conflicting_authority_evidence` against `actions[].scopes`. Adding the
  dropped scope back closes it. Broadening is still a broadening, visible to
  the broad-scope policies as before.

  Two consequences for a manifest that used the broken shape, and only for
  those. `SHIP-AUTH-SCOPE-COVERAGE-MISSING` now sees the declared list, so a
  row declaring `scopes: [crm.read]` against a manifest whose
  `permissions.scopes` does not cover it raises the review item it always
  should have — the divergence, not the check, was what kept it quiet. And
  `authority_hash` moves with the scopes, in a capability lock, an
  `action_surface.actions` row, or both.

- **Authority follows credentials, not functions: declare it once per source.**
  Every action a tool source contributes normally runs with the same
  credential, and asking for it once per action asks the same infrastructure
  question N times. That is not merely tedious — it is what breeds the
  copy-paste that breeds wrong answers, and a wrong authority declaration is
  the one that makes an unscoped production credential read as `mode: none`.
  Increment 3 of the evidence-first declaration RFC
  ([#410](https://github.com/ThreeMoonsLab/agents-shipgate/issues/410)) moves
  the claim to where the fact lives.

  *A new `tool_sources[].authority` block.* `{mode, auth_type,
  credential_mode, scopes, reason}`, with exactly the mode co-requirements an
  action row already obeys — they are now one shared rule rather than two
  copies, so a manifest one site rejects and the other accepts is not
  reachable. The only difference is where `scopes` lives: an action row keeps
  its permission list in the sibling `actions[].scopes` field so there is one
  canonical list per action, and a source, having no such sibling, carries its
  scopes inside the block. Whichever site is operative supplies the
  whole record, permission list included, and that one list is what every
  surface reports and judges: the action's `required_scopes`, the authority
  dimension's `scopes`, and the capability fact's — which the capability
  standard requires to agree — and the list the effect evidence reads, so a
  write-verb permission the manifest says an action requires still bounds that
  action's effect whichever site asserted it.

  *Additive, and never a weaker statement.* An `action_surface.actions[]` row
  that declares its own `authority` still wins for that action; a source with
  no `authority` block resolves exactly as before. The resolver normalizes both
  spellings into one record before it judges anything, so the source block is
  held to the same rule as the action row: it may resolve missing metadata and
  may broaden a scope set, but declaring `mode: none` across a source whose
  actions publish an OAuth scope raises `conflicting_authority_evidence` on
  each action that disagrees — naming the block to correct — and it still
  cannot stand in for authority a source publishes *ambiguously*.

  *One blank is one question.* The declaration questionnaire's unit was
  `(action, dimension)`, which counted one edit as N things to do: a source of
  117 actions with no authority evidence read as 117 questions. A question is
  now identified by the manifest block that answers it, published as
  `open_questions[].answer_path`, so those 117 are one question, one numbered
  block in `suggested-declarations.yaml`, and one `evidence_gaps[]` row —
  `subject_kind: tool_source`, subject `crm [tool_source]`, and a `why` that
  says how many actions are waiting on it. Nothing above the published rows
  changed: every action still carries the issue and still fails pass
  eligibility for it. Conflicts stay per action, because each one asks a
  reader of *that* action which of the two claims is wrong.

  *Nothing is prescribed where nothing can be written.* The source route is
  offered only for a `source_id` the manifest actually configures. A per-scan
  adapter stamps a source id that `tool_sources` does not accept, and those
  actions keep being asked on their own row rather than being sent to a
  manifest key the schema rejects.

  Measured on a synthetic 117-tool MCP source, the shape the RFC names: 234
  declaration questions become 118, of which exactly one is the authority
  question, and `suggested-declarations.yaml` carries exactly one
  `tool_sources` block. The 117 identical authority gap rows become one,
  reading "117 actions from tool source 'github' have no explicit or
  structural authority evidence."

  *A source id is not a foreign key.* Review of this change found the join
  itself wrong. `Tool.source_id` is minted by the adapter, and configured ids
  share that namespace: a `tool_sources` row of type `mcp` calling itself
  `openai_api` had its reviewed authority applied to the OpenAI API surface —
  clearing `missing_authority_evidence` for actions nobody declared anything
  about — while a `codex_config` row, whose adapter emits ids derived from the
  file it read, applied to nothing at all. The dispatcher now records which
  configured entry each loaded result was produced *for*, identity resolution
  carries it onto the canonical action, and the declaration joins on that. A
  reviewed `tool_identity` binding that merges observations from two configured
  sources answers for neither: their credentials are separate facts, and the
  question stays on the action row.

  *An omitted optional field is not a claim of absence.* `credential_mode` is
  optional, and a declaration that leaves it out was overwriting a published
  `service_account` with nothing — leaving the dimension `declared` and
  pass-eligible while capability policies matching
  `credential_modes: [service_account]` silently stopped matching. The
  published value is preserved where the declaration states none; a
  *different* stated value is still a conflict.

  *`mode: none` means no credential, including its mode.* Both declaration
  sites accepted `{mode: none, credential_mode: service_account}` — a fact
  about a credential the same block says does not exist, which on a
  structurally complete read action was pass-eligible. Both now reject it.

  *A version bump moves the labels, not only the filenames.* The public
  schema-version statements had drifted: table cells reading `0.37` beside a
  v0.38 link, "The packet schema is `0.14`" above a v0.15 link, and a
  `verifier_schema_version: "0.7"` in README and the Claude Code skill that had
  been stale for several releases. Two parity tests now hold them together — a
  line that links the current schema must also name its version, and a quoted
  `<kind>_schema_version` must equal what the engine emits unless the line or
  its section marks it as history.

  Report schema `0.37 → 0.38` adds `subject_kind` to `evidence_gaps[]` and
  `subject_kind`/`answer_path` to `declaration_questions.open_questions[]`.
  Packet `0.14 → 0.15` and verifier `0.11 → 0.12` follow because they embed the
  same rows; the prior versions keep their published bytes and are read
  forward, defaulting to the action-scoped reading, which is exactly what those
  builds could produce.

- **Ask only what the scanner cannot prove, and say how much is left.**
  Adoption stalls at a wall of blanks: the fourth `adk-samples#1745` walk faced
  `0/12` pass-eligible actions and a report that described the work as "24
  semantic evidence gaps" — a symptom count with no order and no finish line,
  while the same report already held a derived `financial_write` reading for
  the tool that mattered. Increment 2 of the evidence-first declaration RFC
  ([#410](https://github.com/ThreeMoonsLab/agents-shipgate/issues/410)) turns
  that surface into a questionnaire.

  *Effects the scan observed are pre-filled.* `suggested-declarations.yaml`
  now prints the readings behind each effect question — the distinct effects
  the evidence supports, each with the producers that support it — and, where
  they support one conservative answer, offers that answer in the `effect:`
  line instead of a `<REVIEW_REQUIRED>` blank. A proposal is not an assertion:
  nothing consumes the file, the value comes from the closed `ActionEffect`
  vocabulary rather than from source content, and it is never weaker than any
  reading, so confirming one without thinking can only over-declare — the safe
  direction, and one the monotone rule already keeps visible. Nothing is
  proposed from an *absence*: an unannotated MCP tool's protocol default, and a
  heuristic reading of `read`, both keep the blank, because pre-filling either
  would let the scanner establish what only a human may (#357, #268).

  *The file is numbered and counted.* Blocks carry `Question 3 of 5` banners
  ordered by how much answering them can move the verdict — two answers were
  enough to reach one on the walk, and the entry above says which two the
  order now leads with — and both the file header and the CLI print
  `Declaration questions: 1 of 2 answered; 1 open (1 authority).` from one
  rendering, so they cannot describe the same state two ways. An open question
  with no blank to fill (a conflict whose repair is in the source) is still
  numbered and still shown, so the numbering never skips.

  *A question is not the same thing as a declaration.* The denominator counts
  only what both halves can be measured on — the `effect` and `authority` of one
  `action_surface.actions` row — and `answered` is exact: it counts dimensions
  that gap when the same action is re-resolved *without* its declaration. An
  action whose effect an OpenAPI method or an MCP annotation established was
  never asked and never appears in `total`, so a repository cannot improve its
  progress by restating what the scan already knew.

  *A manifest cannot be the source that contradicts itself.* Found by applying
  the proposal exhaustively across structural evidence: declaring
  `risk_tags: [code_execution]` on a tool whose server published
  `readOnlyHint: true` was reported as "high-confidence read and side-effect
  evidence conflict" attributed to `tool_source` — but the side-effect half was
  the reviewer's own line. The read/side-effect conflict is a disagreement
  between *sources*, so it now excludes the manifest's own `risk_tags`,
  `scopes`, and acknowledged `override` (the set `DECLARATION_CLAIM_SOURCES`
  already names). This also repairs the `risk_tags` repair that
  `declaration_below_inferred_evidence` publishes, which could not close the
  row it was printed on whenever the action carried a read-only annotation. Two
  sources disagreeing is still a conflict, and nothing that gated before stops
  gating.

  *Do not ask a question a declaration cannot close.* Review of this change
  found `partial_authority_evidence` counted as a declaration question while
  the resolver preserves it whenever the *source's* authority evidence is
  ambiguous or incomplete — "reviewed authority cannot replace ambiguous or
  incomplete source authority alternatives", a deliberate safety property. An
  MCP tool published with scopes and no auth type asked one authority question,
  and writing the exact scoped block the scaffold requested left the counter at
  `0 of 1 answered` forever. It is now routed to `provide_source` with no
  declaration template and an instruction naming the source shapes that close
  it. The narrower case of the same defect goes too: `conflicting_effect_evidence`
  is raised about either surface, and only the branch the resolver attributes to
  `action_surface_declaration` is a question a declaration answers — a server
  publishing both `readOnlyHint: true` and `destructiveHint: true` contradicts
  itself, and no declaration touches that. Every kind that remains is now pinned
  by a round-trip test: raise it, apply the answer, re-resolve, require the
  question answered.

  *A row that says the manifest cannot fix it does not point at the manifest.*
  `next_action.path` is the machine-readable target coding agents and the
  short-form `Fix at …` line consume, and it fell through to
  `shipgate.yaml#action_surface.actions[...]` for every kind — including the
  two whose repair is in the tool's own published evidence. Those now point at
  the source artifact (`tools.json#/tools/0`), or at nothing when no openable
  reference exists; they stay addressable through their rerun command either
  way. `conflicting_effect_evidence` raised against a self-contradicting source
  also stops publishing the effect vocabulary and the "add a conservative
  reviewed action declaration" instruction, because adding one leaves the
  identical row. One predicate, `is_declaration_answerable`, now decides both
  what the questionnaire counts and what the row publishes — counting a row the
  repair cannot close and publishing a repair for a row the counter knows is
  unanswerable are the same defect from two ends.

  *A reviewed `risk_overrides` tag is the manifest speaking.* The source
  read/side-effect conflict excluded the `action_surface.actions` row but not
  its sibling manifest surface: `risk_overrides.tags` reaches the effect
  dimension as `risk_hint:manual` with basis `reviewed_declaration`. A reviewed
  `code_execution` tag on a tool published with `readOnlyHint: true` was
  reported as the *source* contradicting itself, and declaring the matching
  effect and risk tag could not clear it. Manifest ownership is now decided by
  both routes — the declaration claim sources and the `reviewed_declaration`
  basis, which in this dimension no tool-published content can carry.

  *Reading an old packet no longer rewrites what it decided.* The legacy
  upgrade path gated its `passed → insufficient_evidence` downgrade on "is this
  a version I recognise" rather than on "is this before v0.8", so every
  packet-schema bump quietly added the immediately previous version to the set
  being rewritten — a stored v0.12 `passed` packet already loaded as
  `insufficient_evidence` before this release, explained by a claim about
  history that is false of it. The downgrade is now scoped to v0.1–v0.7, the
  versions that genuinely predate evidence-backed semantic coverage; v0.7 still
  downgrades and two sources disagreeing is still a conflict.

  Report schema `0.36 → 0.37` adds
  `release_decision.evidence_coverage.semantic_coverage.declaration_questions`
  (`{total, answered, open, open_by_dimension, open_questions[]}`) and
  `evidence_gaps[].next_action.observed_readings[]`. Both are additive and
  neither gates. Packet `0.13 → 0.14` and verifier `0.10 → 0.11` follow because
  they embed the same block; the prior versions keep their published bytes and
  are read forward, with an absent counter reported as `0 of 0` rather than as
  a claim that nothing was owed. The safety-qualification gate's
  `required_report_schema_version` moves with them — it compares for exact
  equality, so a gate left behind a bump rejects every receipt for a reason
  that has nothing to do with safety, and it is now pinned equal to what the
  engine emits by a test.

- **Adopter-facing output stops naming the internal identity model.** Running
  the tool on your own repository for the first time could produce
  `Duplicate tool observation identity: source_type='google_adk_function',
  source_id='google_adk:agent.py', native_locator='agent.py#map_account'` —
  three internal concepts, none of them in the manifest you wrote, and the one
  recoverable fact (a file was listed twice) unstated
  ([#329](https://github.com/ThreeMoonsLab/agents-shipgate/issues/329),
  invariant 5 of [#327](https://github.com/ThreeMoonsLab/agents-shipgate/issues/327)).

  Every string whose purpose is to tell a person what to do next — console
  output, the agent-mode `message` / `next_action` / `next_actions[]`,
  `agent-handoff.json` prose, `fix_task.instructions[]`, PR comment text — now
  names a file, a symbol, an agent, or a manifest key. That message reads
  `Tool 'map_account' was read twice from 'agent.py' as one tool source. Check
  shipgate.yaml for an entry naming 'agent.py' more than once…`, and the
  identity triple moves to a new `details` object on the error envelope, where
  a machine consumer or a bug report can still read it. `report.json` evidence
  blocks and the tool catalog are untouched: they are the identity model, and
  they are supposed to be precise.

  *A digest was the subject of a shipped verdict.* A binding gap whose issue
  named no tool fell back to the derived agent id, so `samples/conductor_agent`
  shipped `Insufficient evidence: the agent's tool binding graph is incomplete
  (agent_v1:7205d836…)` as the sentence under its verdict. It now reads
  `(durable_order_agent [conductor_workflows])`. The report's conservation
  invariant — which already refused a raw *tool* id in a gap subject — now
  refuses any derived id, matched by shape: a guard scoped to one kind of
  identifier passes vacuously for every other one.

  *`verify` and `scan` disagreed about the same failure.* Each caught
  `InputParseError` and wrote its own recovery, so a failure with a precise
  route on one command got generic advice on the other. One resolver now serves
  `scan`, `verify`, and the verifier assembly path — and it names the manifest
  the run actually read, since `scan --workspace <repo>` can discover a sole
  nested `services/billing/shipgate.yaml` while the emitted `edit` action said
  `shipgate.yaml`, a different file in the caller's working directory.

  *One failure, two repairs.* A tool read twice is either a repeated manifest
  entry or a duplicate definition inside the artifact, and the structured
  action carries one path — so naming both let a consumer delete a source
  declaration when the file was the problem. The check now reports which cause
  it saw in `details.cause` and the action follows it, reading the answer from
  the manifest because the loaders that aggregate their artifacts cannot say
  which one they read twice. `tool_sources[].id` is also stripped and refused
  when blank, in the published JSON Schema as well as at runtime: it is the key
  `tool_inventories[]` and `tool_identity.bindings[]` join on, both of which
  were already stripped, so `id: " orders "` matched neither and silently
  completed nothing.

  *`next_actions[].path` is the one field a caller opens verbatim, so it is the
  one field that is always resolved.* The manifest an `edit` action names is
  the one the run read — through `--workspace` discovery, through a defaulted
  `--config` on `verify` and `verification prepare`, and through an archived
  `verify --base/--head`, where it used to name a temporary file that had
  already been deleted. Two things follow: a declared artifact is never
  published as a path, because it has no single base, and a failure evaluated
  against a ref that is not checked out publishes none either — the action
  names the commit instead of a working-tree file that may already hold the
  fix. `ArtifactPathConfig.path` is also canonicalized, so declaring both
  `tools.json` and `./tools.json` no longer reads one file twice and produces
  two canonical tools.

  `tests/test_adopter_vocabulary.py` is the guard: it enumerates the
  adopter-facing strings four ways — every evidence-gap kind through the real
  renderers, every published message builder, every hand-written string at an
  emit site in the modules that produce this output, and the shipped sample
  artifacts — and fails on reintroduced internal vocabulary.

- **A declaration cannot discharge a category it does not cover, and a
  published schema keeps its bytes.** Three follow-ups to the monotone
  declaration rule, each a defect that shipped with it
  ([#409](https://github.com/ThreeMoonsLab/agents-shipgate/issues/409),
  [#411](https://github.com/ThreeMoonsLab/agents-shipgate/pull/411)).

  *Effects are risk-ordered; their obligations are not.* The monotone
  comparison read the effect rank alone, so declaring `financial_write` over an
  inferred `external_communication` read as escalation and stayed silent —
  `financial_write` requires approval, audit, and idempotency but **not**
  confirmation, which is exactly what communicating outward requires. The
  action reported pass-eligible with no gap and no
  `SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING` while its external-write
  risk tag sat untouched in the same report. A declaration now accounts for an
  observation only when it ranks at or above it *and* obliges at least that
  observation's built-in controls, from the new `BUILTIN_EFFECT_OBLIGATIONS`
  table — pinned to the branches it mirrors by a test that walks each entry
  through a real scan. Coverage also reads every policy-eligible claim rather
  than the `effect` field alone, so a `risk_tags: [financial_action]` entry
  accounts for an inferred `financial_write` exactly as the control evaluator
  already treats it.

  *The two comparators disagreed.* The declaration rule compared `_EFFECT_RANK`
  while `_non_authoritative_effect_escalation_support` compared
  `ACTION_EFFECT_RANK`, and the two order `write` and `privileged_data_access`
  oppositely — so a declaration could read as covered in one place and raise
  `mixed_policy_evidence` in the other, a verdict no override could reach. Both
  now call `declaration_covers`, which requires *both* orders to agree; nothing
  that gated before stops gating.

  *Four published schema documents were mutated in place.* The new
  `declaration_below_inferred_evidence` value was written into
  `packet-schema.v0.12.json`, `verifier-schema.v0.9.json`,
  `capability-lock-schema.v0.6.json`, and `capability-lock-diff-schema.v0.7.json`
  while they kept their version identifiers — and the two capability-lock
  documents had no successor version at all, so a consumer pinned to any of the
  four rejected artifacts that document is supposed to describe.
  `generate_schemas.py --check` cannot catch this: it proves committed ==
  generated, never that a content change moved the version. All four are
  restored byte-for-byte, the capability lock advances `0.6` → `0.7` and its
  diff `0.7` → `0.8`, and a lock written under `0.6` is advanced on read rather
  than rejected — the normalizer handled only `0.1`–`0.4`, so the bump would
  otherwise have orphaned every committed `capabilities.lock.json`.

  *The published repair now closes the row it is printed on.* The instruction
  named the strongest uncovered observation, so with both a `financial_write`
  and an `external_communication` reading a reviewer could apply the exact edit
  the row asked for and get the same row back — and it fell through to "declare
  the `write` controls" for an effect that obliges none. A raise is advertised
  only when one observed effect covers **every** uncovered observation *and* the
  value already declared; otherwise the row publishes the `risk_tags` route,
  which both accounts for the observation and makes that category's built-in
  controls apply. `accepted_values` and the scaffold template follow the route,
  so the structured action and the prose describe the same repair. An exhaustive
  test applies the published repair to every gapped declared/observed
  combination and asserts the row is gone.

  *Every suppressed observation reaches the reviewer.* An override recorded two
  `overridden_claim_ids` but projected one `inferred_effect`, so the second
  observation it waived vanished from `acknowledged_overrides`, the PR
  projection, and the packet the moment it was acknowledged. There is now one
  reviewer row per suppressed observation, each with its own sources. Un-acknowledged
  rows already named all of them.

- **A declaration weaker than the evidence inferred for it is no longer
  silent.** Declaring `effect: read` on a tool this scanner itself tagged
  `external_write` was accepted with zero findings: the pre-existing
  `inferred_effect_only` gap was closed by the very declaration that
  contradicted the heuristic which raised it, the action went pass-eligible,
  and the contradicting `risk_tags` stayed in the same report with nothing
  joining them
  ([#409](https://github.com/ThreeMoonsLab/agents-shipgate/issues/409),
  Increment 1 of
  [#410](https://github.com/ThreeMoonsLab/agents-shipgate/issues/410)).

  The contradiction check already existed and was correct for the claims it
  could see: `semantic_assessment._assess_effect` admits a claim into
  `contradictory` only when `policy_eligible`, and `domain.py` grants that only
  to typed, high-confidence bases. Heuristic risk hints are deliberately
  excluded — a heuristic must never *drive* policy (#357). But one flag
  governed two different powers. **Driving a verdict** heuristics rightly
  cannot. **Challenging a human assertion** they should: a declaration sitting
  *below* an observation is not the heuristic gating anything, it is a human
  statement contradicting something the scan saw, which is precisely what a
  reviewer needs surfaced.

  Effect declarations are now **monotone**. Adding or escalating relative to
  the evidence stays silent — a reviewer calling an action more dangerous than
  the evidence proves needs no ceremony. De-escalating past a non-policy-
  eligible inference raises `declaration_below_inferred_evidence`
  (report schema `0.35` → `0.36`): a review-level evidence gap naming the
  declared value, the inferred value, and the hint that produced it. The
  declaration remains the operative effect — heuristics still do not drive the
  verdict, and this row never blocks — but the action is not evidence-backed-
  pass until it is answered.

  Two answers close it, and the reviewer owns the choice: raise `effect` to
  what was inferred — the row names the exact value, so `Improve evidence:`
  reads *Raise action_surface.actions[].effect to 'external_communication'* —
  or acknowledge the difference with the new
  `action_surface.actions[].override` block, which names the `evidence` you
  checked and the `reason` it does not apply. An acknowledged override is
  accepted — the action is pass-eligible again — and is reported as one
  semantic review concern, so a run carrying one can never read `passed`. It is
  a human assertion like every other declaration: the gap's template carries
  `suggested_patch_kind: manual`, `auto_apply: false`,
  `requires_human_review: true`, and `apply-patches` never writes it.

  An override never silences `conflicting_effect_evidence`: where
  **policy-eligible** evidence outranks the declaration, the existing blocking
  conflict is unchanged, no acknowledgement attaches, and the row now says the
  override does not reach it — a reviewer blocked there reaches for the field,
  and silently discarding it left them re-running against an unchanged message.

  Source evidence that **agrees** with the declared value does not exempt the
  row. A first draft exempted it — `support.search_kb` declares `read` and
  carries `readOnlyHint: true`, so why make a reviewer defend a protocol
  annotation against a keyword? Because this resolver already refuses to pass
  on that annotation alone: with no declaration the same tool is
  `inferred_effect_only` and not pass-eligible, precisely because a hint
  outranks it. A declaration that merely restates the annotation must not buy
  what the annotation could not, or #409's hole moves rather than closes — and
  the corroboration would be drawn from content the tool source supplies about
  itself, which is not conditioned on `tool_sources[].trust` (an MCP server can
  assert `readOnlyHint: true` about a destructive tool). The agreeing source is
  **named in the row** instead — "source evidence agrees with the declaration
  (mcp_annotation)" — which is what makes the override one line to write. The
  manifest row's own `effect`, `risk_tags`, `scopes`, and `override` never
  count as agreeing evidence for itself.

  `samples/support_refund_agent` carries the two overrides this rule asks it
  for, so the shipped sample is the worked example.

  **The acknowledgement is consumed everywhere the question is asked.** Policy
  applicability asks exactly what the override answers — "does the higher
  heuristic effect apply here?" — so leaving the acknowledged claim unresolved
  there traded `declaration_below_inferred_evidence` for
  `mixed_policy_evidence`: the reviewer followed the row's own instruction and
  landed on a differently-named `insufficient_evidence`. The override claim
  carries `overridden_claim_ids`, and `_non_authoritative_effect_escalation_support`,
  the action-policy predicates, and capability-policy matching all read that one
  authored list rather than re-deriving the comparison. The acknowledged fixture
  now reaches `review_required` with zero policy gaps.

  **Each exception is a row, not a count.** `semantic_coverage.acknowledged_overrides[]`
  (report schema `0.36`, packet `0.12` → `0.13`, verifier `0.9` → `0.10`) names
  the action, both readings, the hint source, any source evidence that agrees,
  and the human's evidence and reason. The packet's §1 and the PR comment
  (`SHIP-ACTION-EFFECT-OVERRIDE-ACKNOWLEDGED`) render one row per override, so
  a reviewer reads the exceptions rather than a number. Frozen `0.12` packets
  and `0.9` verifier artifacts still read forward; the field is absent there and
  an empty list is the honest reading.

  **Blank-looking answers are rejected.** `str.strip()` leaves U+200B and U+2060
  intact, so an override whose `evidence` and `reason` render as nothing to the
  reviewer they exist for validated, suppressed the mismatch, and restored
  pass-eligibility. Both fields now require visible content — the repository's
  own `has_visible_content` semantics, moved to `schemas/text.py` so the schema
  layer can use it without importing `core`, covering whitespace, controls,
  bidi marks, and every Default_Ignorable code point.

  **The published manifest schema says what the CLI enforces.**
  `docs/manifest-v0.1.json` is advertised for live editor validation and
  accepted both an `override` with no `effect` and blank `evidence`/`reason`,
  which `model_validate` rejects — telling a user their manifest is valid and
  then refusing it. The dependency is published as an `if`/`then`, the
  visible-content rule as a `pattern` generated from the same code-point table
  the runtime check reads, and `tests/test_manifest_schema_parity.py` runs
  twelve payloads through both validators and requires them to agree.

  Known limitation: an override whose inferred evidence later stops firing
  stays accepted and unreported. Distinguishing a stale exception from one that
  never applied needs the `basis: confirmed:<derivation_id>` pin from increment
  4 of #410, which is where it belongs.

- **Every evidence gap now labels a tool the way a reader can use, in every gap
  kind.** `EvidenceGap.subject` is a display label — identity lives in
  `subject_id` — but the policy evidence gaps (every row of
  `report.policy_evidence_gaps`, also merged into
  `release_decision.evidence_coverage.evidence_gaps`) put a raw 64-hex
  canonical tool id in that label, in two shapes:
  `tool_v2_2c9ee6ae…` and `support.search_kb [tool_v2_445a25…]`.
  `evidence_gap_headline` prints the label verbatim into the CLI's
  `Improve evidence:` line, the decision reason, and the GitHub step summary,
  so a reader got a digest where a tool name belongs.
  `samples/support_refund_agent` carried `support.search_kb` in one gap list
  under both spellings at once.

  All three emitters — per finding (`cli/scan/decision.py`), per action
  (`core/lenses/action_surface.py`), and per policy-pack rule
  (`inputs/policy_packs.py`) — now resolve the label from the tool catalog by
  `tool_id` and set `subject_id` from the same tool, so the label became
  readable without the identity being dropped; `policy_evidence_gap()`
  previously had no way to carry it and left `subject_id: null` on every row.
  Resolving through the catalog rather than from each emitter's own fields also
  removes a second divergence: `ActionFact.provider` is
  `_normalize_token(provider or source_id or source_type)`, so a source id of
  `my api` used to label one gap `create_refund [my_api]` and another
  `create_refund [my api]` for the very same tool.

  #403 scoped the raw-id rule in `_validate_exclusion_ledger` to
  `LEDGER_JOINED_GAP_KINDS`, on the reasoning that a gap the ledger never joins
  may name its subject however it likes. A guard scoped to a set of kinds
  passes vacuously for every kind outside it, which is how these rows kept
  their digests; the rule now covers every kind, and since review 2 moved the
  join onto `subject_id` it is no longer about joinability at all — it is what
  keeps `subject` a label. `LEDGER_JOINED_GAP_KINDS` existed only to carry the
  carve-out and is removed with it.

  The rule matches an id by *shape*, anywhere in the label, rather than by
  membership in the run's own catalog. A membership test saw neither spelling
  that actually shipped: the policy-pack emitter wrapped the id in a label, and
  a check plugin — validated on its declared `check_id`, not on tool
  membership — can raise a finding carrying a stale or invented id that is in
  no catalog to compare against. An id that cannot be resolved to a catalog row
  now falls through to the check id rather than being printed raw.

  Field shapes are unchanged and `subject_id` already shipped in `0.35`, so
  `report_schema_version` does not move
  ([STABILITY.md](STABILITY.md#migration-note-unreleased-gap-subject-labels)).

- **Every stage that narrows the analysed surface now records what it removed,
  and the release decision can read it.**
  Across two first-time adoption walks the same shape produced five separate
  failures: a stage computed the right signal, stored it, and did not connect
  it to the decision
  ([#403](https://github.com/ThreeMoonsLab/agents-shipgate/issues/403)).

  The sharpest instance is a fail-open in exactly the reward-hacking shape this
  product exists to catch. `github/github-mcp-server#3076` adds
  `delete_repository` — `destructiveHint: true`, `readOnlyHint: false` — to
  GitHub's official MCP server. With the reviewed declaration still listing the
  116-tool surface from the base commit, the run reported `unbound_tools: 1`
  beside `gap_count: 0` and `pass_eligible: true`, and named the new tool
  exactly once in the whole report: as a row in `tool_catalog`. The checks that
  would have blocked it are correct — declaring the tool produces
  `SHIP-POLICY-APPROVAL-MISSING` and
  `SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING` — but the tool left the analysed
  surface before they ran. And it could not be declared without editing
  `shipgate.yaml`, which is a release trust root a coding agent cannot
  self-approve, so the honest options were "invisible capability" and "blocked
  on a trust-root edit".

  Reports now carry `surface_exclusions` (schema `0.35`): one typed
  `{stage, subject, reason, source_ref, detail, accounting}` record per subject
  a stage removed, from the binding graph, adapter parsing, and surface
  completeness. `detect --json` and `trigger --json` emit the same record for
  the stages they own — a capped discovery walk, an unresolved manifest scope, a
  glob-matched source the real adapter rejects, an unclassified change set —
  replacing four ad-hoc spellings of the same event with one. `accounting` is
  what makes the record checkable rather than decorative: `evidence_gap` (a gap
  row names this subject), `route_blocked` (the stage withheld its verdict and
  its `next_action` repairs it), or `not_claimed` (nothing in the repository
  claims the subject as capability).

  A conservation invariant is enforced at emission: `observed == analysed ∪
  excluded`, every excluded subject appears in the ledger, every `evidence_gap`
  record is backed by a gap row carrying the same subject, an excluded tool the
  decision *did* gap is never recorded `not_claimed`, and a subject *this
  change* newly excluded can never be `not_claimed` either. The
  `unbound_tools: 1 / gap_count: 0` state is now unrepresentable rather than
  something each call site has to remember.

  Every evidence gap that names a catalog tool now names it the same way.
  `partial_binding_evidence` and the binding graph-issue rows used to carry the
  raw canonical tool id (`tool_v2_2c9ee6…`) while every other emitter rendered
  `name [provider]`, which both read badly in `Improve evidence:` and made a
  tool unjoinable with itself — the ledger looked up one spelling and found the
  other. The invariant rejects a raw id reaching a joinable gap subject.

  The gate itself moved only where a diff proves it should. `binding_surface_diff`
  gained `added_unbound_tool_ids` — head exclusions minus base exclusions — and
  a tool in that set raises a `missing_binding_evidence` gap naming it. A
  pre-existing unbound catalog entry is unchanged: `samples/large_multi_framework_agent`
  has 58 by design, and gating on those would make declaring an OpenAPI spec or
  an MCP server self-blocking. Catalog membership is still not evidence of
  capability — a capability the diff introduced and nothing judged is a
  different claim. A plain `scan` has no base, so nothing here fires on one.

  **`skip` now requires positive evidence.** `TRIGGER-DOCS-ONLY-NEGATIVE` is a
  legitimate skip: it classifies every changed file and concludes. `no_match`
  classifies nothing, and the same PR is where that mattered — a fully readable
  diff whose only relevant file is
  `pkg/github/__toolsnaps__/delete_repository.snap`, reported as *"nothing in
  this PR signals a tool-surface change"* because `TRIGGER-MCP-EXPORT-CHANGED`
  matches `**/*mcp*.json` and that file is named neither. A non-empty change set
  no rule classified now returns `evaluation_status: "unclassified"` with
  `should_run: null` and a `next_action` routing forward to the scan, rather
  than a skip nobody can falsify; an empty change set keeps `no_match`, because
  there it is a fact about the PR. This is the
  [#308](https://github.com/ThreeMoonsLab/agents-shipgate/issues/308)
  monotonicity rule — already accepted for diff *readability* — applied to diff
  *comprehension*. Trigger catalog `0.3 → 0.4` also adds
  `TRIGGER-MCP-TOOL-SCHEMA-CONTENT`, which recognises an MCP tool definition by
  its content (an MCP input schema beside MCP annotation hints) instead of by a
  naming convention the repository never agreed to; the same glob also missed
  `mcp-server/tools.json`.

  Two more places published a skip nobody could falsify. A matched capability
  rule now overrides `stop_conditions`: the block's premise is "this workspace
  is not an agent project", read by `detect` from the working tree, and a
  matched rule is evidence from the *diff*, which can carry what `detect` never
  saw — the `.snap` file above is invisible to `suggested_sources`, so the stop
  held and discarded the very rule that recognised it. And a skip may no longer
  rest on changed files no rule classified: a dependency bump beside an opaque
  capability file matched a `dry_run` rule that covered only the manifest, and
  published an advisory skip over the sibling. Coverage is now per path.

  The tri-state verdict reaches the consumers that act on it. The Claude Code
  hooks branch on `evaluation_status` instead of coercing `should_run: null` to
  a skip, and say which of the two withheld states applies rather than claiming
  a match; `decide-shipgate-relevance.md` teaches the tri-state and the new
  precedence; and `trigger_catalog_schema_version` moved in step across the
  contract payload, `.well-known`, the rendered local contract, and the docs —
  a drift a new cross-surface equality test now catches.

  Finally, a base comparison that was *requested* and could not be performed no
  longer reads as one nobody asked for. `binding_surface_diff` gains
  `base_comparison_requested`, `VerificationContext` gains
  `base_comparison_unavailable`, and that state raises one gap naming the
  unusable base rather than concluding an unbound tool is pre-existing from a
  comparison that never ran — the weakening
  `docs/engineering/ai-coding-workflow-verifier.md` §2.3 forbids. Ledger rows
  in that state are `unverified`, never `not_claimed`.

  The tri-state reaches the GitHub Action too. `trigger_action` read the raw
  `stop_conditions_fired` bit before the winning verdict, so the Action
  republished a skip the runtime had just refused, and it collapsed both
  withheld states into `none`. It now projects the verdict, returns `withheld`
  rather than a value that reads as a decision, and `action.yml` exports
  `trigger_evaluation_status` so a workflow can tell "run the scan" from
  "repair the input". First adoption stays out of the failed-comparison route:
  a base with no manifest was read successfully and simply has no gate, so
  asking the adopter to regenerate a base report that cannot exist made
  adoption over a partially-wired catalog unfinishable.

  Three integrity gaps in the new evidence closed. `accounting` joins to its
  gap through an explicit `accounted_by` pointer instead of a subject string
  two catalog tools can share; `gated` and the new `gap_backed` are validated
  against the rows they summarize, in Pydantic and in the invariant, because a
  count nothing checks can be forged past both; and the cap's guarantee is now
  the accurate one — every `gap_backed` row survives truncation, while
  `route_blocked` and `unverified` rows may be capped, since their accounting
  is one whole-run fact a single row proves as well as five hundred.

  Adapter omissions are recorded again, from a typed fact rather than from
  prose. `LoadedToolSource.omissions` carries the entries an adapter read and
  refused — the MCP loader records both of its skip branches — so an entry that
  genuinely never entered the catalog reaches the ledger, while the warnings
  about tools that *did* load stay out of it.

- **`verify --preview` of a head that is not checked out now asks for the
  checkout, instead of stopping.** Preview reads project markers from the
  working tree, because that is the tree the `init` it recommends would write
  to, so previewing some other ref establishes no project — on the reported
  pull request the changed directory exists only on the PR branch. That state
  routed to a human with `must_stop: true`, `command: null`, and
  `allowed_next_commands: []`, which ended the loop `allowed_next_commands`
  promises in one move; the remedy, derivable from the very ref preview was
  handed, was never stated
  ([#397](https://github.com/ThreeMoonsLab/agents-shipgate/issues/397)).

  Nothing about the change is in doubt there. What is missing is an input — a
  working tree holding the commit under review — and producing it is one
  mechanical step the caller owns. The route is now the `fetch_base` action
  that already exists for exactly this shape: `agent_action_required`, no
  command (Shipgate never writes to a caller's worktree), and an `expects` that
  names the input. The two causes that no checkout can repair — evidence the
  change deleted, an unreadable inventory — keep their human route. Plain
  `verify` is unaffected: it reads `--head` from the object database, not the
  worktree.

  **`expects` names a commit id, and the rerun it asks for is pinned.** The
  step being requested moves `HEAD`, so a route spelled with the caller's own
  revision expression does not survive it: `--head HEAD~1` names one commit
  before the checkout and its parent after, and following the route walked
  history backwards one commit per iteration instead of resolving. A
  `HEAD`-relative `--base` re-ranges across the same checkout, which is quieter
  and worse — the rerun succeeds against a diff nobody asked for. Both refs are
  resolved to immutable ids before either is published.

  The requested checkout also makes the preview's control pointer stale. It
  bound no HEAD identity, so `agents-shipgate agent control` — the one refresh
  entry point — kept returning the same `current_control_id` and the same
  unmet-looking request after the caller performed it, which is how a
  refresh-driven controller repeats an action forever. The pointer now binds
  the worktree the preview actually read.

  `fetch_base` accordingly means "make this input available" in both of its
  senses. Which sense a route asks for is read from `expects`, by every
  consumer: the adoption scorer recognizes a checkout of *the requested commit*
  — not a fetch, not a path-restoring `checkout --`, not a checkout of some
  other commit — and still requires a fetch for a ref request.

  The whole recovery is published in `expects`, which the envelope never
  truncates, and the instruction leads `why`, which it caps at 400 bytes. A
  sufficiently long branch name had pushed the checkout instruction and both
  pinned refs out of the bounded field, leaving a consumer to re-derive the
  rerun from its own `HEAD`-relative request — rebuilding the walk.

  The pointer binds the *worktree* preview read, not only its HEAD. Preview
  routinely routes on uncommitted evidence — an untracked `agent.py` beside an
  untracked `pyproject.toml` is a project — and deleting one of those files
  moves neither HEAD nor its tree, so the stale route stayed current. The path
  set is derived from the live worktree on both sides, so any path entering or
  leaving it, and any content change within it, refuses the pointer.

- **`detect` now publishes the same per-candidate `init` commands `init` does
  when a workspace holds several agent projects.** Its escalation handed the
  reader a JSON selector inside a shell command —
  `init --workspace <agent_project_candidates[].path> --write` — and no
  runnable command anywhere in `next_actions[]`, so a preview that routed to
  `detect` reached a second dead end (#397). `detect --json` now ranks the
  decision first, exactly as before (`kind: "review"`, `command: null`, because
  naming one candidate would make the arbitrary pick `init --write` refuses to
  make), and carries one exact `init --workspace <candidate> --write --json`
  below it per candidate, with the `executable`/`args` pair. Both commands
  build that list from one helper, so the two an adopter runs in sequence
  cannot publish different recoveries for one workspace; the workspace root
  stays out of it, since that is the scope `init` refuses.

  **Every** candidate gets one. The ten-item cap that keeps a human refusal
  readable had been applied to the routing too, which left candidate 11 onward
  selectable and unrunnable — `detect --workspace samples --json` finds 22
  projects and emitted 10 commands, and the reported pull request's repository
  has 25.

  And a candidate that **already carries a manifest** routes to
  `doctor --config <that manifest> --json`, not to `init --write`. A nested
  `shipgate.yaml` is itself evidence of a project, so adopted directories are
  candidates too: on this repository's own `samples/`, 21 of 22 are, and every
  command emitted for them exited 2 on a manifest `init` will not overwrite
  while `expects` promised a file that already existed. The exception is
  `--agent-instructions`, which makes `init --write` the advertised refresh and
  exits 0 — there the `init` route is kept, flags and all. Both commands'
  *printed* summaries mark those candidates and name the `doctor` route, from
  the one formatter they now share: the human and JSON forms of a single run had
  begun answering the same question two ways.

  Setup the caller asked for survives that route. A refused
  `init --write --ci` writes no workflow, and handing the adopted candidate a
  bare `doctor` dropped the request silently; it now carries `--ci` on an
  `init` with `--write` omitted, which installs the workflow, leaves the
  manifest alone, and exits 0.

  The workspace root gets an answer too. `.` is a real entry in
  `agent_project_candidates` and rank 1 tells the caller to choose from that
  list, but it was the one candidate the routing skipped: `init` there is the
  run that just refused, and `--allow-unresolved-scope` accepts the whole
  workspace as one scope, which is a different decision. It is now an explicit
  human route saying exactly that, and the printed lists mark it — a caption
  reading "re-run init on the one you are changing" over an unmarked `.` is the
  human form of a run contradicting its own routing.

  `detect`'s `control.input_id` now covers the route it publishes, not only the
  classification behind it. Every emitted command is spelled for the entry
  point the process came in through, so the same workspace read as
  `agents-shipgate` and as `/opt/custom/agents-shipgate` published different
  commands under one identity — and that identity is the documented cache
  boundary for the answer.

- **The first scan of an agent whose tools are imported symbols now scaffolds
  both layers it needs, instead of emitting nothing.**
  `suggested-declarations.yaml` was only written once the binding layer was
  already closed, so during the two scans where an adopter is most stuck it
  did not exist, and the binding gap that *was* reported carried
  `declaration_template: null`
  ([#361](https://github.com/ThreeMoonsLab/agents-shipgate/issues/361)).

  Two templates close that. A repository whose agent lists tool symbols static
  analysis cannot resolve extracts nothing at all, so it used to produce only
  source warnings routed to `review_warning` — no path, no command, nothing to
  open. It now raises one `incomplete_surface` row **per source** carrying the
  exact `tool_inventories` entry, joined by `source_id` to the source it
  completes, and the tool-inventory skeleton is written with the symbol names
  the agent's own `tools=[...]` list publishes. Per source, not per symbol: six
  unresolved symbols are one mechanism restated six times, and attaching a
  repair to each row would have put raw loader prose back in the headline that
  grouping removed.

  Once that inventory is declared, the catalog is populated and nothing binds
  it to the root agent. That gap now scaffolds the closed-world
  `agent_bindings.declarations` row with the agent, every catalog tool's exact
  selector, and the observed handoffs pre-filled — while `complete` and
  `reason`, the two values that are a human judgement, stay
  `<REVIEW_REQUIRED>`. Merging the block verbatim after answering those two
  closes `binding_coverage.gap_count` in one iteration. Past a ceiling of 50
  tools the template is withheld rather than truncated: `complete: true` claims
  the listed tools are *all* the agent can reach, so a silently cut list would
  be false exactly where a reviewer cannot see it.

  Both instructions are also withdrawn once followed, and so is the diagnostic
  behind them. A reviewed `tool_inventories` entry naming a source in
  `source_id` is the answer shipgate itself prescribes for that source's
  unresolved-symbol warnings, but those warnings stayed on the report and
  `evidence_below_ie_threshold` gates on their raw count — so a repository that
  did exactly what it was told sat at `insufficient_evidence` forever, with no
  non-warning gap left to act on. They are now withdrawn when the manifest
  declares the source, **per source**, keyed on that reviewed completion
  relationship and never on tool names: a name subtraction cleared an unrelated
  source's warning by coincidence in one direction, and in the other it never
  matched an inventory that had correctly split a toolset symbol into the tools
  it exposes, so that source was prescribed the same inventory forever. Only
  the warning is withdrawn — the loader's `surface_gaps` entry stays, so
  extraction confidence is untouched, and an empty inventory still cannot reach
  a verdict past `SHIP-INVENTORY-NOT-ENUMERABLE`. The inventory skeleton is
  deleted alongside it, the way the declaration scaffold already was.

  A display name two sources share is withdrawn against only once **every**
  source publishing it is complete: while any candidate is still owed an
  inventory the warning could be about that one, and once none is, the
  ambiguity no longer changes the answer. Both sides of the comparison are
  stripped, since the manifest permits surrounding whitespace in an id.

  The closed-world `declarations` row is also scaffolded for the other shape
  that needs it — an agent whose tool list static analysis could only *partly*
  read — and lists the agent's existing edges as well as the unbound catalog
  tools, because a row omitting a tool the repository plainly wires to the
  agent would be false.

- **Every `<REVIEW_REQUIRED>` in `suggested-declarations.yaml` now says what a
  legal answer is.** The one file an adopter is told to edit was the one file
  that did not name the vocabulary: `effect:` and `authority.mode:` were bare
  blanks while `report.json` carried their nine and four accepted values per
  gap, and completing twelve tools meant roughly forty-eight values looked up
  in a different file
  ([#388](https://github.com/ThreeMoonsLab/agents-shipgate/issues/388)).

  Each blank is preceded by a comment carrying either that field's
  `accepted_values` — rendered from the gap's own list, never a second copy, so
  the two artifacts cannot disagree — or, where the answer is not drawn from a
  closed set, the shape it takes and which modes make it required. The
  `agent_bindings.root` block additionally lists the agent objects the scan
  observed, with their source, for a human to confirm: `object` matches the
  agent's *declared* name rather than the Python variable it was assigned to,
  which is what made guessing it a coin flip. Nothing is filled in — a comment
  is not a value, and inferring the trust root from AST evidence remains the
  self-declaration surface [#268](https://github.com/ThreeMoonsLab/agents-shipgate/issues/268)
  closed.

  Pasting an unfinished scaffold now also reports itself as one whatever field
  the placeholder lands in. `agent_bindings.declarations[].complete` accepts
  only `true`, so its own type answered first with "Input should be True",
  which tells a reader nothing about the scaffold they pasted; the placeholder
  is rejected before field validation, so one wording covers every field. That
  check reads raw input, which `yaml.safe_load` can hand back as a *graph*
  rather than a tree, so its traversal visits each container once. A manifest
  containing `&loop {x: *loop}` gets the structured config error and its
  agent-mode recovery payload rather than a `RecursionError`, and an acyclic
  alias DAG — the expensive case, doubling the walk at every level and
  materializing `2**n` path strings for one placeholder — is bounded by the
  size of the document.

- `display_literal` now escapes Unicode noncharacters alongside the invisible
  code points it already covered. They are the same hazard — nothing reaches
  the reader, so two repository objects render identically — and two of them
  are worse: PyYAML rejects U+FFFE and U+FFFF outright, so an agent name
  carrying one made the generated declaration scaffold unparseable, because
  the document quoting that name in a comment could not be loaded at all. The
  encoding stays injective, so `undisplay_literal` still recovers the name.

- **`verify --preview` on a monorepo now names the project the pull request
  actually changed, instead of a repository root that `init` refuses.** The
  change-scope resolver draws a project boundary around a bare
  `requirements.txt` only for directories the caller has already found agent
  evidence in — the whole boundary a `requirements.txt` beside `agent.py` has.
  The preview call site passed no evidence at all, so the walk climbed past
  such a project to the workspace root, resolved no scope, and emitted
  `init --workspace <repo root> --write`, which `init` then refused
  deterministically: "holds 53 self-contained projects that define agents, and
  one manifest describes one agent surface." `detect` had reported the same
  project correctly all along, so the two commands an adopter runs in sequence
  disagreed
  ([#394](https://github.com/ThreeMoonsLab/agents-shipgate/issues/394)).

  Preview now collects that evidence for the directories the diff sits under,
  and asks it only where the answer can change anything: a directory carrying
  a strong marker is already a project root, and one carrying no weak marker
  cannot become one. For the rest it reads what `detect` would find directly
  in that directory, through every rule that can put a file in its evidence
  set — framework-attributed Python, the artifact-glob detectors (Anthropic,
  OpenAI API, n8n, Conductor), suggested OpenAPI/MCP sources, and Codex plugin
  packages — over the same git-aware inventory, so an ignored file cannot make
  preview narrow to a directory `detect` never saw. On the reported pull request the first command
  an adopter sees goes from an eight-step recovery to
  `init --workspace python/agents/smart_closer --write`.

  The probe reports three things as *undetermined* rather than as "no project
  here": the shared `max_python_files` budget running out with a file still
  unread, an unreadable inventory, and a change that deletes the one file
  beside a requirements file that could have been the evidence — the head tree
  cannot say whether what it removed was that project's agent surface.

  Each of those routes to a recovery that can actually advance it, because one
  generic answer could not. A `detect` at the same cap hits the same cap, so
  budget exhaustion emits a concrete higher-cap command; and a head-only
  `detect` cannot see evidence the change deleted — it reports the surviving
  project as the workspace's single scope and its `init` writes a manifest for
  an agent the pull request never touched — so deleted evidence is a human
  route with no command at all. Causes accumulate rather than replacing one
  another, and deleted evidence outranks a cap, because raising a bound cannot
  find a file the change removed. An evaluated head that is not this worktree
  is the third such cause: discovery of the current tree answers about a
  different one, so no discovery command is offered there either — that route
  asks for the checkout instead (first entry above).

  Two blind spots in that probe are closed. It now bounds each directory's
  Python evidence to the files a `detect` *of that directory* would reach —
  the first `max_python_files` paths of its subtree in inventory order — so a
  direct `agent.py` sorting after a thousand inert modules is no longer
  evidence preview can see and the scoped command it recommends cannot. And
  boundaries the change *removes* are derived from the change set before
  anything reads the head tree, since a deleted `pyproject.toml` leaves nothing
  for a head-tree marker filter to find: a pull request deleting a whole
  project was silently attributed to whatever survived.

- `detect`'s glob-based source suggestion re-ran the whole git inventory walk
  once per pattern — fifteen walks for one pass. `_candidate_files_matching`
  now accepts an inventory the caller already built, which both fixes that and
  is what lets the preview evidence probe ask the *same* suggestion rule about
  a single directory rather than keeping a second copy of it.

- **Project discovery no longer presents a truncated candidate list as a
  complete one.** On a repository large enough to hit the Python-file cap,
  `detect` and `init` reported the agent projects found *before* the cap as if
  they were all of them — no cap warning, no `--max-python-files` hint, and the
  project actually under review missing from the list the user was told to
  choose from. The `ambiguous` verdict short-circuited the truncation check, so
  `"unknown"` — the state whose entire purpose is to say the parse was cut
  short — was reachable only when one or fewer candidates were found, and the
  fail-safe was unreachable on exactly the repositories most likely to need it
  ([#395](https://github.com/ThreeMoonsLab/agents-shipgate/issues/395)).

  Truncation is now evaluated independently of ambiguity and reported beside
  it: `detect --json` and `init --json` carry `agent_scope_truncated`, the
  human and refusal messages say the list may be incomplete and name the
  `--max-python-files` remedy, and `workspace_signals.project_root_count`
  bounds the claim with an uncapped, filename-only census of the directories
  that could be a manifest scope — every project-marker directory *plus the
  workspace root*, which is a candidate whether or not it carries a marker,
  because unmarked agent evidence is attributed to it as `.`.

  Nothing publishes a terminal negative from a capped walk any more, and the
  guard for that is a second, wider field: `python_parse_truncated`, the raw
  fact that the parse stopped at its cap. `agent_scope_truncated` additionally
  requires more than one candidate scope — right for a claim about the
  candidate *list*, wrong for a claim about the *workspace*, because a
  single-scope repository whose only agent sorts past the cap leaves it false
  while still hiding an agent. Every whole-workspace negative now gates on the
  raw field: the three negative-control diagnostics
  (`SHIP-DIAG-NO-AGENT-SURFACE`, `-NON-AGENT-LIBRARY`,
  `-PURE-PROMPT-EXPERIMENT`), each of which publishes a `stop` that routing
  turns into `setup_not_applicable`; `bootstrap`'s no-surface stop; the
  `detect` human summary; the first-look classification line; and the trigger
  catalog's stop block, which gained `python_parse_truncated: false`. A
  `detect` payload missing any key that block reads is now reported as
  `stop_conditions_evaluated: false` rather than silently satisfying it, since
  absent is not false.

  `init --write` refuses on a truncated parse too, and takes the same
  `--max-python-files` flag. It runs its own discovery, so a bound `detect`
  settled on did not reach it: following the recommended route landed on an
  `init` that re-ran at the default cap, missed the agent, and wrote a
  `CHANGE_ME` manifest with no tools at exit 0.

  The recovery from a capped walk is now an executable command rather than
  prose inside a human route. Raising `--max-python-files` is a mechanical,
  read-only retry that needs no decision, so `next_actions[0]` is the *same
  command you ran* at a bound covering every Python file in the workspace —
  `detect --max-python-files <n> --json` from `detect`, and
  `init --write --max-python-files <n> --json` (carrying the setup flags the
  run asked for) from `init`. It cannot land back at the same cap, and from
  `init` it settles the scan and completes the setup in one step. That command
  leads the ranked recovery whenever the parse was truncated: asking a human to
  choose from a list the refusal itself calls incomplete is the thing to avoid.
  Human review is reserved for choosing an actual manifest boundary.

  Two more routes that could not succeed are gone:
  `SHIP-DIAG-MCP-OPENAPI-ARTIFACT-ONLY` and
  `SHIP-DIAG-CODEX-PLUGIN-PACKAGE-DETECTED` name a root `init --write`, and
  setup routing ranks a diagnostic ahead of the advance, so on an unsettled
  workspace they published a command over the top of the route that would have
  said so; both now fire only on a settled scope *and* a complete parse. A
  single scope settles the manifest boundary and says nothing about whether the
  surface that manifest would declare was read. And `bootstrap`'s no-surface
  stop ignored `codex_plugin_candidates` entirely, so a Codex-plugin-only
  repository — deliberately `is_agent_project: false` — stopped at `detect`
  and never ran `init`.

  `DetectResult.next_action` carries the same rule. The CLI overwrites it with
  the routed action, which is why its stale branch survived: read as a library
  value — which is what the zero-install detector mirrors — a capped
  single-scope workspace still returned "Workspace does not appear to be an
  agent project. No action." Truncation is now checked ahead of the adoption
  and negative branches in both detectors, and in the script's human output.
  `first_look` routes the same way: its final `Next:` line is the full-count
  retry rather than a `verify --preview` that walks past the recovery printed
  one line above it.

  The zero-install `tools/shipgate-detect.py` carries all the new fields
  (script version `0.4.0`), pinned by the parity test — which now includes a
  workspace that actually truncates, since every sample fixture sits far under
  the cap.
- **Google ADK extraction confidence is now measured on the module, not
  hardcoded.** The Python AST path set `extraction_confidence="medium"` on
  every tool it produced, and the only code that ever set `"high"` applied to
  tools loaded from a `tool_inventory` artifact. Every gate tests `!= "high"`,
  so no ADK repository could reach a pass from source however statically
  analysable it was: `insufficient_evidence` was not a property of a
  repository, it was the framework's default first-run verdict. Reproduced on
  the most trivial case available — one file, twelve annotated module-level
  functions, `12/12` catalog tools reachable, `0` unbound, `0` source warnings —
  which still reported twelve `low_confidence_tool` gaps and abstained. A
  condition that holds for every input carries no information: it could not
  tell a toolkit factory from twelve plain functions, and the remedy it
  prescribed was transcription — copy the twelve tools Shipgate had just
  extracted correctly into `suggested-inventory.json`, adding no fact to the
  system ([#393](https://github.com/ThreeMoonsLab/agents-shipgate/issues/393)).

  A Google ADK Python entrypoint now reports `high` when the adapter can show
  it read the whole surface, and `medium` with a named reason when it cannot.
  The proof is scoped to the file: one unresolved construct anywhere holds
  every tool the file produced, because a fully-resolved agent in a
  half-resolved module can reach tools nobody enumerated. Module-scoped reasons
  are `dynamic_tools_expression`, `unresolved_tool_reference`,
  `unresolved_tool_expression`, `unresolved_tool_wrapper`, `dynamic_toolset`,
  `conflicting_tool_contract`, `unresolved_sub_agent`, `mutable_tool_binding`
  (anything reaching `agent.tools` after construction, including through an
  alias, `setattr`, or `getattr(agent, "tools")`), `dynamic_agent_kwargs`
  (`Agent(**config)`, which hides `tools` entirely), `unresolved_tool_wrapper`
  (a recognised `FunctionTool`/`LongRunningFunctionTool` whose `func` this
  module does not define — an import, an attribute, a lambda, or none at all),
  and `shadowed_tool_definition` (the name-to-definition map is flat and
  scope-blind, so `tools=[helper]` can resolve to a factory's inner function, a
  method lifted out of a class body, one of two conditional definitions, or a
  definition that a parameter, class, import, `except ... as`, `case ... as`,
  `global`, or later assignment rebinds — the tool is still named, its
  signature is not proven). Per-function reasons are `decorated_tool_function`,
  `variadic_parameters`, `untyped_parameter`, and
  `unrepresentable_annotation`. The last two are the same defect twice: the
  JSON-schema fallback types an unannotated parameter `string`, and it types
  `set[str]`, `int | None`, `tuple[...]`, a Pydantic model, and even
  `typing.List[str]` `string` as well. A guess may not ship as a schema, so
  faithfulness is now checked by asking the emitter what it would produce and
  comparing it to what the annotation denotes — including the return
  annotation, which feeds `output_schema` through the same fallback. The
  `low_confidence_tool` evidence gap names the reasons instead of repeating one
  sentence on every AST tool in every repository.

  A recognised constructor is only ADK's while the name still refers to the
  import: `from google.adk.tools import FunctionTool` followed by
  `FunctionTool = replacement` used to have a foreign factory read with
  Google's semantics (`shadowed_framework_symbol`), and a `from x import *`
  can rebind anything the module defines (`star_import_shadowing`). Both are
  refused rather than guessed at.

  Two correctness fixes came out of the same review. Injected context
  parameters are identified the way ADK identifies them — by type, with
  `tool_context` as the name fallback — instead of by dropping every parameter
  spelled `ctx` or `context`, which deleted ordinary model-visible inputs from
  the emitted schema. And `List[...]`/`Dict[...]` now emit `array`/`object`
  rather than `string`, so `from typing import List` is usable without holding
  the tool at `medium` for what was an emitter gap.

  Module-scoped reasons reach every tool the file contributed, not just its
  function tools. A module whose only tools come from a *resolved* OpenAPI or
  MCP toolset still has a tool set the file could not prove — `Agent(**config)`
  beside a resolved `McpToolset` is the case — so those tools are lowered too.
  They are only ever lowered, never raised: this step cannot promote a tool the
  adapter did not extract. They also survive `tool_identity` merging: an
  identity binding proves two observations describe the same operation, which
  says nothing about whether the module one of them came from exposes further
  tools, so a member's unproven tool *set* caps the canonical tool. Reasons
  about a single tool's own interface still resolve in the primary's favour —
  that is what a reviewed inventory is for — and the evidence gap for an
  unproven set now names the construct to fix instead of asking for an
  inventory or spec the repository has already supplied.

  `_surface_is_complete` changed with it: an AST source type used to be
  disqualified outright, which was the same constant one layer down and would
  have kept `incomplete_surface` open on a proven surface. Membership now poses
  the question and the adapter's own attestation answers it. Saying nothing
  still reads as incomplete, so adapters not yet taught to answer —
  LangChain, CrewAI, the OpenAI Agents SDK static path — keep their previous
  verdict, an unclassified new warning demotes its module automatically, and
  a wildcard exposure still outranks any completeness claim.

  Net effect on the reported subject: a fully static ADK project with its
  actions declared reaches `passed` instead of `insufficient_evidence`, and one
  without them is asked for the effect and authority declarations a human
  genuinely owes rather than for a transcription it cannot learn anything from.

- **A tool inventory now completes the source that asked for it, instead of
  shadowing it.** `incomplete_surface` fires for every statically-extracted
  tool on a first ADK/LangChain/CrewAI/n8n scan, and the only remedy the tool
  offered was "save the skeleton, reference it from `<framework>.tool_inventories`".
  Following that instruction exactly made things worse: the inventory was loaded
  as an *independent* source, so its entries were added beside the extracted
  tools rather than joined to them. On the reported subject the catalog went
  from 12 tools to 18 with only 12 distinct names, the reachable ratio fell from
  6/12 to 6/18, the `action_surface` rows that used to resolve became
  `ambiguous_tool_selector`, and the gap that asked for the file was still open.
  The loop had no third step
  ([#386](https://github.com/ThreeMoonsLab/agents-shipgate/issues/386)).

  `<framework>.tool_inventories[]` entries now take `source_id`, naming the tool
  source whose surface the file enumerates. Each entry matching a name that
  source already exposes is joined to that observation, so the catalog keeps its
  size, the merged tool inherits the inventory's high extraction confidence, and
  the gap closes. Entries the source does *not* expose stay standalone — an
  inventory exists precisely to disclose tools static extraction missed, and a
  tool nobody wired is still honestly reported as unbound.

  Nothing is joined by name alone. `source_id` is a manifest declaration
  desugared into the same reviewed-binding engine as `tool_identity.bindings`,
  one binding per matched name; a name a source exposes twice implies no join
  and asks for an explicit binding instead, and a reviewed binding that already
  claims an observation always wins. The prescribed remediation text and the
  `suggested-inventory.json` note now name the field, and an inventory declared
  without it that shadows a low-confidence source says so in `source_warnings`
  rather than degrading in silence. Inventories that genuinely describe a
  separate surface keep working unchanged.

  Completion adds evidence and never removes it. A canonical tool now answers
  to the `source_type`/`source_id` of **any observation bound into it**, so an
  `action_surface` row already written against the completed source — including
  one Shipgate scaffolded itself, which qualifies rows by `source_id` — keeps
  resolving instead of becoming `unresolved_tool_selector` the moment the
  inventory is applied. Both qualifiers, given together, must still be
  satisfied by the same observation. Merging also backfills what only the
  source knew (`output_schema`, `owner`, function signature, auth
  type/mode/credential) wherever the reviewed inventory is silent; previously a
  completed n8n tool came back high-confidence with unknown auth and no owner,
  trading the closed `incomplete_surface` gap for a
  `partial_authority_evidence` one. Disagreements between two populated values
  remain `conflicting_tool_identity` rather than a silent overwrite.

  That erasure was also *suppressing findings*, not only degrading evidence.
  `samples/support_refund_agent` binds a `-> str` SDK function to a reviewed
  inventory that is silent about output, and the merge dropped both the
  AST-derived `{"type": "string"}` schema and the `sdk_function` source type
  that `SHIP-SCHEMA-FREEFORM-OUTPUT` falls back on — so the shipped golden
  recorded no free-form-output finding for a tool that plainly returns
  free-form text. The finding is restored (one new MEDIUM review item; the
  sample's verdict and its five blockers are unchanged).

  The prescribed entry is also YAML-safe: source ids are unconstrained strings
  and generated framework ids embed the configured path, so a comma used to
  split `source_id: google_adk:agent,prod.py` into two keys and the exact text
  the tool printed failed manifest validation. Encoding escapes every non-ASCII
  code point rather than emitting it literally — PyYAML rejects a stream
  carrying C1 controls or a lone surrogate outright, and silently normalizes
  U+0085 NEL to a space, so an id containing NEL round-tripped to a *different*
  id and the remediation named the wrong source.

  Identity aliasing covers the whole selector surface, not just the source
  qualifiers. Completion also rekeys the canonical `tool_id` from an
  observation-derived hash to a binding-derived one, and `_action_selector`
  emits `tool_id` on every row it scaffolds — so the generated declaration
  became `unresolved_tool_selector` against the very inventory the tool had
  just prescribed. A canonical tool now answers to the id each of its
  observations carried while unbound, and the rule reaches every selector
  consumer: `_action_has_policy_control` and `_matching_suppression` compared
  the canonical fields directly, so a source-qualified
  `require_confirmation_for_tools` entry silently stopped applying (reporting a
  missing `confirmation.required` and moving the verdict to `blocked` on an
  untouched manifest) and a source-qualified `checks.ignore` went inert. Alias
  ids resolve selectors only; they never enter the catalog partition that
  `agent_bindings` reads.

  Preserved evidence is now conflict-checked and traceable. Backfilling "the
  first non-empty value" resolved genuine disagreements by observation-id
  order: two members reporting `owner: team-a` and `owner: team-b` produced a
  tool owned by `team-a` with no issues and `pass_eligible=True`. Every
  contributor to `output_schema`, `function_signature`, `owner`, and
  `auth.credential_mode` is compared — the primary included — and more than one
  distinct populated value is `conflicting_tool_identity`, which makes the
  identity non-pass-eligible. (`auth.source` names the extractor that read the
  record, not the credential, so it is preserved without being compared.) Each
  preserved value records the observation that supplied it, and a finding
  raised on one cites that artifact: the restored free-form-output finding now
  points at `agents/refund_agent.py:5`, where the `-> str` actually is, instead
  of an inventory JSON containing no output schema at all.

- **An input that is not there is no longer reported as an input with the
  wrong shape, and no command creates the workspace it was asked to inspect.**
  Three reports, one class. `verify --preview` given a `--workspace` that did
  not exist created the entire four-level path, wrote a full artifact set into
  it, and exited 0 — so a typo produced a confident result about a workspace
  that was never there, and in CI both signals a caller can gate on read
  healthy. The leftover directory then blocked the `git clone` the reporter had
  skipped, turning one missed step into a second, unrelated failure
  ([#389](https://github.com/ThreeMoonsLab/agents-shipgate/issues/389)).

  An absent `--workspace` is now an invocation error — `config_error`, exit 2,
  decided before any directory is resolved or created — on **every** command
  that takes the option, `--preview` included. Preview's documented "always
  exits 0" is a promise about workspaces it evaluated; there is nothing here to
  evaluate. The sweep that enforces this is generated from the live command
  table, so a new `--workspace` command cannot quietly reopen the hole. It also
  closes four defects found while closing the first: `init --write`,
  `audit --host`, and `verification prepare`/`worker` raised bare
  `FileNotFoundError` tracebacks, `install-hooks --write` wrote hooks into the
  mistyped tree, and `mcp audit` answered `decision: allow` about a directory
  that did not exist.

  `doctor` reported an **absent** manifest as a malformed one — "Config file
  must contain a YAML object" — for a file that had never been created. The
  routing knew the difference all along, so the control envelope contradicted
  itself: `control.reason` said *fix the file* while `control.next_action` said
  *bootstrap from scratch*, and an agent reasoning from the reason edited a file
  that was not there. Absent, empty, and present-but-not-a-mapping now produce
  three distinct messages, classified where the read failed rather than after
  the bytes have been flattened to `b""`
  ([#384](https://github.com/ThreeMoonsLab/agents-shipgate/issues/384)). The
  same conversion in the diff-input path — "Workspace is not inside a git
  checkout" for a path that did not exist — is gone with it.

- **A manifest type mismatch is an edit, not a bug report.** A YAML mapping
  where a list belongs — `google_adk.tool_inventories:` with keys under it, the
  prescribed remedy for the first gap most ADK adopters hit — was reported as
  `internal_error` with "this is a bug — please file an issue", naming no field,
  no file, and no line. Pydantic converts `ValueError` and `AssertionError`
  raised inside a validator into a `ValidationError`, but lets `TypeError`
  propagate past the config-loading boundary
  ([#387](https://github.com/ThreeMoonsLab/agents-shipgate/issues/387)).

  Every validator in the manifest schema now raises `ValueError`, so the same
  mistake produces `config_error` (exit 2) naming the manifest path and routing
  to `edit`, exactly as a bad key inside a correctly shaped list already did.
  Messages also name the shape that was written — "must be a list of artifact
  paths, but is a mapping" — and a build-time sweep fails if `raise TypeError`
  reappears anywhere under `schemas/`, which closes the class rather than the
  instance.

- **A Google ADK sub-agent's tools are part of the analyzed surface, and a tool
  the gate did not look at can no longer go unmentioned.** On the canonical ADK
  multi-agent shape — a coordinator with `sub_agents=[salesforce_agent,
  sap_agent]` — every tool the sub-agents owned fell out of the root-reachable
  graph, and none of the 25 evidence gaps named one. On the reported repository
  the excluded half was the half a release gate exists to judge: three financial
  writes, including one that sets opportunities to `Closed Won` and one that
  creates an SAP sales order
  ([#385](https://github.com/ThreeMoonsLab/agents-shipgate/issues/385)).

  ADK routes a handoff by the sub-agent's `name=`, but `sub_agents=[…]` spells
  the Python variable the agent was assigned to. Reading the variable as an
  agent name produced one phantom node per sub-agent, owning no tools, so the
  handoff landed on a node with nothing behind it and the real agent stayed
  unreachable. The two spellings are now reconciled from the module's own
  assignments, resolved innermost-out through the enclosing scopes so that two
  factories reusing one local name cannot cross their sub-agents. This also
  collapses the duplicate nodes the graph used to report.

  An element that cannot be resolved to an agent definition — built inline,
  imported from another module, behind a non-literal `sub_agents` value, or
  rebound ambiguously within one scope — now fails closed as partial evidence
  naming the spelling. It no longer becomes a node of its own: an empty tool
  set on a node named after an import reads as proof the sub-agent has no
  capability, which is the opposite of what is known about it. Naming two of
  three sub-agents likewise no longer reports the two as the whole handoff set.

  `agent_bindings.declarations` was the documented remedy and could not work.
  Declaring an agent seeded a synthetic node for it, and for an agent the
  scanner had already observed that second node made the name ambiguous, so the
  resolver rejected names its own scan had emitted. Declarations now reuse the
  observed node, and a genuinely ambiguous name says how many agents share it
  and which sources they came from instead of reporting the name as unknown.

  Finally, a tool bound to an agent the configured root cannot reach now gets an
  evidence gap naming it. Everything downstream of the binding graph is narrowed
  to root-reachable tools, so such a tool is never judged; before this it was
  not mentioned either, leaving the ratio `6/12 catalog tools reachable` as its
  only trace. This covers tools whose owning agent the scan identified — a hole
  in the graph Agents Shipgate built. A catalog entry that no agent binds at
  all is a different claim and is unchanged: catalog membership is deliberately
  not evidence of capability, so declaring an OpenAPI spec or MCP server does
  not become self-blocking.

- **One command runs Agents Shipgate from this checkout, and `doctor` now says
  which Shipgate answered.** Running the CLI from a source tree meant either a
  bare `agents-shipgate`, which resolves through `PATH` and can silently execute
  a pipx or base-conda copy — a `0.8.0` shadowing a worktree makes new
  subcommands look "missing" — or `PYTHONPATH=src python -m agents_shipgate`,
  which is correct but has to be discovered. A console script promoted from an
  environment that no longer exists is worse than either: it dies with
  `ModuleNotFoundError` before a line of Shipgate runs, so nothing in Shipgate's
  own output can explain it, and the epic's reproduction had a user drop into a
  terminal at exactly that point
  ([#334](https://github.com/ThreeMoonsLab/agents-shipgate/issues/334),
  [#338](https://github.com/ThreeMoonsLab/agents-shipgate/issues/338)).

  A repository launcher, **`./shipgate`**, is now the canonical contributor
  command, and CONTRIBUTING.md, AGENTS.md, and CLAUDE.md all use it. It needs no
  installation, no activated virtualenv, and no `PYTHONPATH`: it puts this
  tree's `src/` ahead of every installed copy (in child processes too), and
  selects an interpreter — `AGENTS_SHIPGATE_PYTHON`, else the project
  virtualenv, looked up in the main checkout as well so a `git worktree` shares
  it — re-executing exactly once, guarded against looping. It announces itself
  through `AGENTS_SHIPGATE_CLI`, the operator override the invocation policy
  already honours, so every command it prints back is runnable as printed;
  without that its `argv[0]` reads as the `shipgate` console script and the
  policy would emit commands a clean checkout cannot run
  ([#322](https://github.com/ThreeMoonsLab/agents-shipgate/issues/322)). An
  operator who set the variable themselves still wins.

  `doctor --json` payloads — and every `doctor` agent-mode error line, including
  the discovery failure that prints no payload — now carry an `environment`
  block: the interpreter and whether it is supported, the launcher and every
  Shipgate console script on `PATH` with the interpreter each shebang names, the
  import source and whether it is a checkout or an install, the installed and
  imported and source-tree versions, and `mismatches[]` — each with a severity
  and, where one exists, a runnable recovery command. Nothing runs an
  interpreter or executes a console script to find out: a stale wrapper is
  identified from its shebang, because a wrapper that cannot start is exactly
  the one that cannot report on itself. A source checkout out-voting an
  installed distribution is *not* reported as a mismatch — that is the intended
  state, and an editable install's metadata lags every version bump by design.

  The recovery is **ranked**, because `pip install` is not always the first
  step: an interpreter created with `venv --without-pip` answers
  `python -m pip install …` with `No module named pip`, so emitting that alone
  would promise a recovery that fails on its first token in exactly the
  environment the recovery exists for. `ensurepip` is proposed ahead of it when
  `pip` is absent, and an interpreter with neither gets the diagnosis and no
  command at all rather than one that cannot run.

  New agent-mode error kind `environment_error` (exit 4), emitted by the
  launcher before Shipgate is running and carrying the same `environment` block;
  it is published in [`docs/errors.json`](docs/errors.json). New public helper
  `agents_shipgate.invocation.render_cli_override`, the host-rules inverse of
  how `AGENTS_SHIPGATE_CLI` is parsed — needed now that something writes the
  variable, so a checkout path containing a space survives the round trip. No
  schema or contract version changes.

- **The launcher announces a spelling the operating system will actually start.**
  A shebang is a POSIX kernel feature, so `.\shipgate` is a file Windows will
  not execute — and announcing that path through `AGENTS_SHIPGATE_CLI` published
  recovery commands that could not run there, which is this launcher's own
  defect relocated to another platform. It now announces
  `<interpreter> <launcher>` wherever the file cannot be started on its own: on
  Windows, and on a copy that lost its executable bit. `python shipgate …` is the
  documented Windows spelling, and it is the one that gets emitted. A new
  Windows CI job covers the entry point, the announcement, and the
  `os.name == "nt"` re-execution branch — including that a non-zero status
  survives the hop, which is exactly what a branch that spawns instead of
  replacing the process can lose.

- **Two virtual environments over one base are no longer one interpreter.**
  `runs_this_interpreter` resolved both paths before comparing them, and a POSIX
  virtualenv's `bin/python` is a symlink to the interpreter it was built from —
  so two unrelated virtualenvs collapsed onto the same binary despite having
  different `sys.prefix` values and different `site-packages`. A console script
  pointing at a *different* environment reported clean. The comparison no longer
  dereferences, matching the rule the launcher already applied when deciding
  whether to switch interpreters; the two copies are now pinned to each other by
  test.

- **`PATH` lookup follows the shell's rule, not "a file exists there".** A
  regular file without the execute bit is skipped by POSIX command lookup, which
  continues to later `PATH` entries. Stopping at it described a wrapper the
  caller's shell would never run and hid the stale-interpreter diagnostic for the
  one it would. Executability is now required on POSIX, and `PATHEXT` decides on
  Windows.

- **A trampoline target must be a command, not a mention of one.** The `exec`
  handoff was found by searching the whole wrapper, so a comment such as
  `# old target: exec "/deleted/python"` above a working `exec` reported a
  healthy wrapper as `console_script_interpreter_missing`. Lines are now read in
  order with `exec` required in command position and comments skipped: a
  diagnostic may not be derived from a string the shell never executes.

- **A quoted program token is read before it is judged to be ours.**
  `retarget_command` located the program by scanning to raw whitespace, on the
  argument that our console-script names contain none — true of the names, and
  irrelevant to the strings they appear in. A quoted interpreter path whose
  directory is named after this project, which cloning it into
  `~/agents-shipgate worktree/` produces, was cut at the space; the remaining
  `'/tmp/agents-shipgate` has `agents-shipgate` as its basename, so a
  `python -m pip install` recovery was rewritten to name the Shipgate entry
  point instead. That is not an unrunnable command but a runnable one that runs
  the wrong program, and the dangling quote also cost the action its
  `executable`/`args` pair. The span is now found with quoting honoured and the
  token's *value* comes from `shlex` — the same grammar the string was rendered
  with — with the two cross-checked, so a disagreement leaves the command
  untouched rather than rewriting it wrongly. A correctly quoted Shipgate path
  (`'/opt/my tools/agents-shipgate'`) is now retargeted where it previously was
  not.

- **A `#!/bin/sh` console-script wrapper reports the interpreter it `exec`s.**
  An interpreter path containing a space cannot go in a shebang, so `pip`
  writes a shell trampoline instead. Reading only the shebang reported
  `/bin/sh` — which exists and is not the running interpreter — so a healthy
  install raised `console_script_runs_other_interpreter` once per alias, while
  the interpreter that could actually go stale stayed invisible. The `exec`
  target is now parsed; an unrecognised handoff reports `null` rather than the
  shell.

- **An `insufficient_evidence` verdict now leads with the gap you can close,
  and the three lines that announce it agree.** The reason counted source
  warnings — the symptom — and demoted the one actionable gap to a secondary
  line, while `agent_summary.first_recommended_action` (the field the agent
  contract routes coding agents to) contradicted that line outright: "applying
  patches does not clear an evidence verdict, so no machine-applicable fix is
  available", printed directly beneath `Improve evidence: … Target:
  shipgate.yaml#agent_bindings.declarations`
  ([#362](https://github.com/ThreeMoonsLab/agents-shipgate/issues/362)). For a
  coding agent that is a dead end, and the cheap ways out of a dead end are the
  ones `forbidden_actions` enumerates. `release_decision.reason`, the short-form
  `Improve evidence:` line, and `first_recommended_action.why` now project **one**
  selected gap — the first `evidence_gaps[]` row that names a nonblank
  normalized target or carries a publishable command, falling back to the first
  row when nothing is addressable.
  On an `insufficient_evidence` verdict with an addressable gap the three name
  the same gap and the same target — or, for a row carrying only a command, the
  same command; the reason reads `Insufficient evidence: <what is unproven>
  (<subject>). Fix at <target>.` (or `Run: <command>.`) `Context: <counts>…`.
  And on **every** verdict, "no machine-applicable fix is available" is
  unreachable while any gap is addressable; where it still appears, it is
  true. Outside that first case the
  three surfaces answer different questions on purpose — with no addressable gap
  the reason keeps its threshold wording, and under `review_required` it stays
  severity-driven — and the published contract now says so at exactly that
  scope. `first_recommended_action.kind` stays `"info"` on the evidence-first branches
  — a statement about the summary projection, not about the gap rows, which may
  carry an exact command (the stale-`--diff-from` base report is regenerated by
  one) alongside the reviewed declarations a human must write.

  Every value these one-line surfaces interpolate is repository-derived — a gap
  subject is a tool name, a policy pack authors `expects`, a semantic gap's
  `path` embeds a tool name — so each is forced onto one line in the shared
  projection rather than at one call site, and the GitHub step summary now
  escapes `release_decision.reason` the way `report.md` always has. Without
  both, a value carrying `\nControl: complete` forged a line under the real one.

  Addressability is decided **after** normalization, by one shared predicate
  every consumer calls, and normalization is split into three questions that
  had been conflated. *Display* renders a value on one line without rewriting
  it: **nothing is deleted**, and anything that would not reach the reader as
  itself — controls, `U+2028`/`U+2029`, bidi marks, lone surrogates, and
  invisible (Default_Ignorable) code points — becomes a visible `<U+XXXX>`
  escape. For identity-bearing values that encoding is **reversible and
  injective**: `<` is escaped too, so `a\nb.yaml` and the literal filename
  `a<U+000A>b.yaml` render differently and `shipgate\u200b.yaml` can no longer
  impersonate `shipgate.yaml`. Prose keeps `<` as ordinary punctuation and
  additionally folds whitespace; paths and commands never fold, so
  `configs/foo  bar.yaml` keeps both spaces and `python -c 'print("a  b")'`
  stays the program that was written. *Visibility* asks whether a value names
  anything at all, using Unicode Default_Ignorable rather than a
  general-category guess, so a path made only of ZWSP, VS16, or CGJ is not
  addressable. *Executability* is all-or-nothing, judged on the **authored
  value**, and a publishable command is published **byte for byte**: any
  control, bidi, or invisible code point, or any whitespace other than
  `U+0020`, suppresses it entirely, and a publishable one is never trimmed.
  Deleting a zero-width character from `r\u200bm -rf` authors a program the
  repository never wrote; trimming a leading NBSP silently changes `argv[0]`;
  and trimming a *trailing* space breaks `printf foo\\ `, whose second token
  legitimately ends in one. Blank accepted values are dropped rather than
  rendered as `Accepted values: , .`, and a suppressed command produces no
  `Run:` line and a `null` repair command instead of an empty one.

  A gap counts as addressable when it names a target **or** carries a
  publishable command. `path` and `command` are independently nullable on the
  wire, and a `provide_source` row carrying only an exact regeneration command
  is as actionable as one naming a file — reading the path alone let
  `Improve evidence:` print `Run: …` while the field agents read said no
  machine-applicable fix existed.

  Two copy rules that made the dead end look larger than it was came with it.
  Warnings that restate one **recognized** mechanism collapse **at render
  time** — six `Google ADK agent 'x' references unresolved tool 'y'.` lines
  become one naming the cause, every affected symbol, and the two surfaces that
  close it — in `report.md`, `packet.md`/`packet.html`, `verify`'s fix-task
  remedies, and the CLI `--verbose` list, which now prints the mechanism count
  beside the raw one. Grouping is structural, not textual: a mechanism declares
  which fields are context (two ADK agents never merge into one row, and a
  symbol they share is never counted twice) and which are subjects (a binding id
  stays attached to its tool, so two sources cannot cross-product). Decoding is
  exact rather than delimiter-guessing — every interpolated value is `repr()` of
  a string, so the decoder reads a *string literal* at each field position and a
  value containing the separator is read whole instead of being cut in half.
  A message that does not decode as a registered mechanism wrote it, including
  a composite one carrying two invalid binding members, keeps its own text
  rather than a merged one — rendered through the same one-line display
  projection as every other group, because opaque loader text still reaches
  surfaces that do not collapse newlines, and replaced by a visible placeholder
  when it has no printable content at all (a blank bullet hid the very thing
  the gate was reporting). `report.json`, `packet.json`,
  `SourceWarningGroup.warnings`, and
  `evidence_coverage.source_warning_count` all keep the loader's bytes: that
  count is a gating input, and folding it would silently recalibrate the
  threshold.

  A `tool_identity.bindings` member that matches nothing now gets the guidance
  that fits its cause. A **configured** source that produced no observations
  states the rule and points at `shipgate.yaml#agent_bindings.declarations`; a
  `source_id` that names no configured source at all — a typo — is told to
  correct the selector, because no binding declaration can repair one. Both used
  to report only that the member "matched 0 observations", the arithmetic that
  sent readers back to declare more bindings over the same empty source.

  `verify`'s fix task keeps typed source-warning repairs. A blanket
  `source_warning` skip discarded the stale-`--diff-from` gap, which carries a
  path, an expectation, and the exact regeneration command, leaving only the raw
  warning prose — so the handoff named a different repair from the one the
  selected gap names. Only pathless, review-only warnings fall through to prose
  now, and every field the typed path interpolates is one-lined before it
  reaches `fix_task.instructions[]` and `allowed_repairs[].target/reason/command`
  — durable machine-facing fields that `agent_result` copies verbatim into
  `repair.instructions`, `suggested_fixes`, and `agent_repair_instructions`.

  Verdict strictness is unchanged. `_MAX_TOLERATED_SOURCE_WARNINGS` and
  `_LOW_CONFIDENCE_TOOL_RATIO` stay frozen, `evidence_below_ie_threshold` reads
  the same counts, and no schema version moves. This is ranking, copy, and
  render-time grouping, plus one consistency invariant with a test.

- **Verifier schema `0.8 → 0.9`.** `capability_review.policy_weakening_proven`
  is an emitted field, and a published schema identifier never gains one: an
  artifact still declaring `0.8` failed validation against the frozen v0.8
  schema every consumer pins to. `0.9` carries the field,
  [`docs/verifier-schema.v0.8.json`](docs/verifier-schema.v0.8.json) keeps its
  published bytes, and artifacts declaring `0.8` and earlier still read — the
  field defaults to `false`, which is exactly what "this artifact recorded no
  base-vs-head comparison" means. The model now also rejects the contradiction
  the docs already forbade: `policy_weakening_proven=true` requires
  `policy_weakened=true`, so a payload cannot route as safe while telling a
  human the policy was weakened.

- **The PR comment reports the proven fact, not the routing flag.** Headline,
  control reason, and fix task were made honest for an unprovable policy
  direction, but the generated `pr-comment.md` still printed
  `Policy weakened: true` off `policy_weakened` — the fail-closed flag that is
  raised precisely when nothing was compared. It now prints
  `Policy changed, weakening unproven: true` with the reason, on every
  first-adoption and no-base run. The route it reports is unchanged.

- **The policy comparator honors the split check-id aliases.** Runtime severity
  resolution already did; the Tier B base-vs-head comparison still read literal
  keys, so it produced both kinds of wrong answer — a head that adds an
  explicit override for the new id lowered the applied severity with no key
  change on the umbrella (missed), and a head that drops a redundant explicit
  override changed no applied severity at all (falsely reported as a
  weakening). Resolution now mirrors the applier exactly: the exact id wins,
  then the umbrella.

- **Accepted debt survives the split.** A fingerprint hashes the check id, so
  every baseline entry recorded against `SHIP-VERIFY-POLICY-WEAKENED` stopped
  matching the renamed no-base finding — moving a `critical` accepted item from
  matched debt to new, and its decision from `review_required` to `blocked`.
  Baseline matching now offers the pre-split fingerprint as an additional
  candidate, scoped to declared split targets and still required to agree on
  `support_hash`, so exactly the renamed row matches and no unrelated debt is
  absorbed.

- **The headline is bounded once, at the end.** The evidence-gap provenance
  note was appended *after* the reserved-budget composition, silently spending
  the room held for the human-review requirement: a long multibyte blocker
  title plus one gap note produced 443 bytes and the 400-byte compact
  projection dropped `a human must review it.` from both `reason` and
  `human_review.why`. Every later addition now goes through one composition,
  the configured-manifest path in the adoption suffix is bounded, and when room
  runs out the parts yield in priority order — the gap note first, never the
  verdict, the named cause, or the requirement.

- **Unicode format controls are stripped from headline material.** C0/C1
  filtering missed the category that matters most: U+202E RIGHT-TO-LEFT
  OVERRIDE and U+2066 LEFT-TO-RIGHT ISOLATE reorder rendered text without
  changing a byte, so a tool name carrying one could visually move the reserved
  governance suffix out of the position the composition guarantees. Unsafe
  Unicode categories (`Cc`, `Cf`, `Cs`, `Co`, `Cn`, `Zl`, `Zp`) are now
  collapsed to spaces before any byte accounting, which also makes a lone
  surrogate — previously a `UnicodeEncodeError` inside the budgeting — a
  non-event.

- **The verifier headline leads with the release blockers, not with the
  governance notice that outranked them.** When a PR both touched the release
  trust root and blocked release on critical or high findings, `headline` —
  the one line that reaches a PR comment, a chat reply, or a triage list —
  reported the trust-root fact and never mentioned the blockers. The two
  findings driving it are `medium`; the blockers they outranked were
  `critical`. Reproduced on
  [google/adk-samples#1917](https://github.com/google/adk-samples/pull/1917),
  where four `SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING` blockers on an
  agent that batch-pays ETH and ERC-20 on Base mainnet went unnamed while the
  headline described Shipgate's own configuration
  ([#365](https://github.com/ThreeMoonsLab/agents-shipgate/issues/365)). The
  ranking now matches the severity: a run carrying a critical or high release
  blocker leads with the scan's own verdict line and *appends* the
  self-approval prohibition, so the human-review requirement survives in the
  same string rather than being replaced by it. That leading line also now
  names the worst blocker rather than only counting it — a count reads the
  same whether the agent is missing a docstring or can move funds with no
  enforced control — chosen deterministically by severity, then check id, then
  title, so two runs of the same tree name the same row. A trust-root or
  policy change with no blockers still leads with the governance notice — it
  is then the whole story. This is ordering only: `control.state`, `must_stop`,
  `merge_verdict`, `can_merge_without_human`, `permissions`, `fix_task`
  routing, and the release decision are untouched, and the reordered headline
  is now what the human-review route carries as its reason, so the control
  envelope cannot name less than the headline does.

- **A first adoption no longer reports a policy weakening that could not have
  happened.** The fail-safe that fires when there is no base policy to compare
  against shared a reason code with a proven base-relative weakening, so
  `verifier.json` reported `SHIP-VERIFY-POLICY-WEAKENED` on a base that
  carried no gate at all — the condition every first adopter meets by
  definition. That fail-safe is now its own reason code,
  **`SHIP-VERIFY-POLICY-BASE-ABSENT`** (medium, floor medium, category
  `verify`), carrying both evidence kinds: `manifest_introduced` (git proves
  the base carries no manifest under any name) and
  `base_snapshot_unavailable` (no base report was obtainable).
  `SHIP-VERIFY-POLICY-WEAKENED` keeps firing, unchanged, for every proven
  base-relative weakening — it is narrowed, not deprecated. Nothing about the
  gate moves with the reason code: same severity, same suppression immunity,
  same `human_ack` requirement on the `policy` surface, same
  `protected_surface_changes` rows, same release decision. In particular
  `verifier_summary.policy_weakened` keeps its fail-safe meaning — only a
  git-proven adoption clears it, so a rename-and-loosen diff still cannot
  clear the gate-bypass alarm by breaking the base scan.

  Three compatibility rules make "same release decision" literally true rather
  than aspirational. **Configuration written against the pre-split id still
  reaches the new one**: a repository that had raised the no-base fail-safe
  with `checks.severity_overrides: {SHIP-VERIFY-POLICY-WEAKENED: critical}`
  still gets `critical` and still blocks, because the pre-split id is an
  umbrella over both halves (`SPLIT_CHECK_ID_ALIASES`) — distinct from the
  legacy-alias map, since the umbrella is not deprecated and a baseline naming
  it must not be flagged as stale. An override written against the new id wins,
  and floor validation is unchanged. **Reports written before the split still
  reproject to what they meant**: every read path — the verifier summary, the
  capability review, `protected_surface_changes`, `human_ack`, the adoption
  fix-task route, and the Action's findings fallback — accepts either id with
  the same evidence kinds, so a stored `report.json` carrying the old id with
  `manifest_introduced` is still an adoption and still reports
  `policy_weakened: false`. Nothing re-emits the old id. **Fail-closed routing
  and the human-facing claim are separated**: `capability_review` gains
  `policy_weakening_proven` (additive, default `false`), the narrower fact that
  a base-vs-head comparison actually ran. `policy_weakened` still routes; only
  `policy_weakening_proven` licenses saying the policy was weakened. A no-base
  run now reads "This PR changes the release policy that evaluates it and no
  base policy was available to prove the change does not weaken the gate"
  instead of asserting a weakening nothing established — in the headline, the
  control reason, and the fix task's repair reason alike.

- **The blocker title quoted into the headline is normalized and bounded.**
  `ReleaseDecisionItem.title` embeds a tool name read out of an OpenAPI spec,
  an MCP export, or a Python source file, so quoting it verbatim made scanned
  input able to reshape the artifact that reports it: newlines survived into a
  field contracted to be one sentence, and length alone was enough to push the
  appended `cannot self-approve` clause past the compact control envelope's
  400-byte prose budget — deleting the human-review requirement from the
  projection a routing consumer reads. Control characters are now collapsed,
  the quoted title is capped, and the governance suffix gets a reserved byte
  budget so the lead is shortened instead of the requirement. If the
  requirement alone fills the budget it is published on its own, which is what
  the headline said before blockers ever led it.

- **`init` ranks agent-name candidates instead of taking the first one it
  trips over.** Candidates were emitted in file-then-AST order with no
  scoring, and all three consumers — the manifest renderer, the `init` JSON
  summary, and the zero-install detector — took the first entry with an
  accepted `source`. "First encountered" was the entire selection policy, and
  it produced two different wrong identities. In
  [usestrix/strix](https://github.com/usestrix/strix) it chose the
  one-character test literal `t` over `Strix`
  ([#320](https://github.com/ThreeMoonsLab/agents-shipgate/issues/320)); in
  [google/adk-samples#1745](https://github.com/google/adk-samples/pull/1745)
  it chose `SalesforceAgent` — a real worker agent — over the
  `App(root_agent=…)` coordinator that declares it
  ([#324](https://github.com/ThreeMoonsLab/agents-shipgate/issues/324)). The
  second is the one that survives review: the manifest was schema-valid and
  named a genuine agent, just not the reviewed one. Selection is now one
  ranking pass over four signals — structural role (an application root
  outranks an unqualified agent, which outranks a declared `sub_agents=[…]` /
  `handoffs=[…]` child), origin (product code outranks test code, which names
  fixtures), corroboration by the project name, and a quality floor that
  rejects values under three significant characters and generic scaffolding
  names. A rejected value is never written: `agent.name` keeps its
  `CHANGE_ME` placeholder and the existing `placeholders[]` review action,
  rather than asserting an identity nothing reliably declares. `name=` given
  as a symbol now resolves statically through **one** hop — a module constant
  or an `os.environ.get("…", "…")` default in the same package, never a
  chain, never a file outside the workspace, and never by importing user
  code. Each `agent_name_candidates[]` entry carries `role`, `path`,
  `rank_score`, `selectable`, and a `rationale[]` explaining its rank, so a
  future ordering regression is visible in `detect --json` instead of
  silently changing what the manifest claims. The rule itself now exists once
  (`select_agent_name`) rather than as a `source in {…}` set literal copied
  into the renderer, the JSON summary, and `detect`'s human-readable line,
  and `tools/shipgate-detect.py` (`script_version` `0.3.0`) is pinned to the
  CLI's ranking byte for byte by the parity suite.

  Because the ranking reads Python name binding, it reads it the way Python
  does or else declines. Every binding is modelled, not just the ones that
  construct agents — a `root_agent` later rebound to `build_root()` retires
  the earlier construction instead of leaving it holding the role. Scopes
  are not flattened: a helper's local `root_agent`, and a helper's local
  import, belong to that helper, and a free name in a nested function
  resolves against the enclosing function before the module rather than
  skipping the captured binding. A reference resolves to the binding that
  actually reaches it — nearest scope, latest line before the reference —
  and when any candidate sits under an `if`/`try`/loop the lookup fails
  closed, because both arms can execute and taking the lexically later one
  is a guess dressed as an answer. A symbol bound more than once anywhere in
  a file is never resolved, which is what makes reading module-level
  constants safe at all: a second write, whether later, conditional,
  computed, or in another scope, means the value Python passes is not the
  one visible statically. `from config import AGENT_NAME as NAME` looks up
  `AGENT_NAME` in the target module, not the alias. An `os.getenv` /
  `os.environ.get` default is only read when the call provably resolves to
  the unshadowed stdlib import, so a module defining its own
  `getenv(key, fallback)` cannot have its fallback lifted out as the agent
  identity. And when an import could resolve to two different in-workspace
  modules — the agent directory's `config.py` and the workspace root's —
  which one Python picks depends on `sys.path`, so the identity stays
  unresolved.

  Provenance is resolved, never assumed. `Agent`/`LlmAgent`/`App` are read
  through the binding that reaches them, so a constructor imported under an
  alias counts and one shadowed by a local `def`/`class` does not — matching
  a terminal spelling let a decoy `def Agent(...)` supply a fabricated
  identity while the real aliased root went unseen. Every binding form is
  modelled, not just assignments: `del`, `class`, `except … as`, `case`, and
  a `global`/`nonlocal` store routed to the scope it actually rebinds all
  retire the agent a name used to hold, and a file carrying
  `from x import *` can prove nothing about any of its names. Lookups into
  an enclosing or module scope no longer compare writes against the nested
  reference's line number: a function body does not execute where it is
  written, so a module-level rebinding *below* a nested reference still
  happens before the call, and only a single unconditional binding there is
  provable. An `App(root_agent=Agent(…))` built inside a branch is
  unresolved rather than whichever arm came first, and `tests.py` / `test.py`
  now count as test code like every other conventional test module.

  Provenance is a question about a *location*, not about a file. The binding
  consulted is the one that reaches the call site, so a framework import at
  the bottom of a module no longer retroactively validates a decoy call
  above it, and a conditional import is not proof at all. Dotted spellings
  are held to the same standard as bare ones — `fake.Agent(...)` cannot
  borrow the terminal name — and a constructor or stdlib lookup replaced
  through an attribute (`adk.Agent = fake`, `os.getenv = fake`) retires the
  provenance its import used to carry, since neither binds a name. A
  wildcard import suspends every spelling it could reach until a later
  explicit binding restores it.

  Scopes now follow Python's. Comprehensions have their own, so
  `[App for App in ()]` no longer shadows a module-level `App`; definition
  headers — defaults, decorators, annotations, class bases and keywords —
  are walked in the enclosing scope, because that is where they are
  evaluated, which stops a parameter from shadowing the constructor its own
  default just used; and a scope introduced inside a branch carries that
  contingency into everything it declares, so two `def build()` arms no
  longer resolve to whichever came first.

  Origin now dominates the score rather than competing with it. The
  documented contract is that product code outranks test code, but additive
  scoring let a test fixture that builds an `App(root_agent=…)` outrank a
  plain agent the shipped code declares. The test penalty is now strictly
  greater than the whole spread of the hierarchy and corroboration signals,
  and a test pins that arithmetic so a future signal cannot silently widen
  the spread past it.

  **A declared application root that cannot be resolved statically now
  blocks selection entirely.** Dropping it and letting the rest of the field
  rank looks conservative but is the #324 failure again: everything
  remaining is by construction *not* the root, so the manifest would declare
  a worker. A dynamic name, a factory call, a symbol no single construction
  defines, a symbol that fails cross-module resolution, a conditionally
  assigned root, and a root rebound to a non-agent value all produce
  `CHANGE_ME` plus the reason.

  The zero-install detector now takes the same workspace inventory as the
  CLI — `git ls-files` when Git can read the workspace, a contained
  filesystem walk otherwise — because that is what makes the parity claim
  true rather than merely tested on tidy fixtures. A `.gitignore`d module is
  invisible to `init`, so walking it anyway let the script name an agent
  `init` would never write; a symlink escaping the workspace both
  contributed a name and leaked its outside absolute path into the output.
  Two further detector fixes: a contained symlink keeps its *logical* path,
  because resolving `agent.py -> source.txt` renamed the entry, dropped the
  `.py` suffix, and reported zero Python files where the CLI reported an
  agent project — the go/no-go verdict, not just the ranking. And the
  non-Git fallback walk now has a documented ceiling that *raises* rather
  than truncating, so a downloaded tree of unrelated assets cannot consume
  unbounded time and memory before detection sees a single source file.

  `DetectResult(agent_name_candidates=[NameCandidate(...)])` keeps working,
  for every sequence form the old field accepted — a tuple of instances used
  to raise and a tuple of legacy dicts used to land silently on
  `selectable: false`. Legacy entries are validated as a `NameCandidate`
  before being enriched, so a payload with missing values, wrong types, or
  keys `extra="forbid"` rejects is no longer upgraded into a well-formed
  lie.
  `NameCandidate` is a public export and was the declared element type
  before ranking existed; narrowing the annotation turned working calls into
  a `ValidationError`, and a legacy dict parsed but silently landed on
  `selectable: false`, changing which name `init` writes. Both are now
  upgraded at the model boundary using the rule that decided selection
  before this change, so old callers keep the behaviour they had.

  Its file bound also moved from the whole inventory onto Python parses, so
  an asset-heavy repository can no longer exhaust the budget before the walk
  reaches any source. Git's output is read incrementally against that bound
  rather than buffered whole and measured afterwards, and overrunning it
  exits non-zero exactly as canonical discovery raises — falling back to a
  walk would do the work the bound exists to refuse.
- **First adoption inside a monorepo no longer starts by writing the wrong
  manifest.** `verify --preview` routed setup to the workspace root, so on a
  repository holding many self-contained agent projects the command it handed
  back produced a manifest covering all of them: on `google/adk-samples` that
  was 252 `tool_sources` and an `agent.name` taken from the first of 160+
  `Agent(name=…)` literals, unrelated to the pull request under review. Nothing
  in the output said so — the JSON reported `manifest_status: "written"`,
  `is_agent_project: true`, `confidence: "high"`, and a *higher* framework
  score than the correct sub-directory — and the alignment layer that compares
  a declared purpose against the observed capability surface has nothing left
  to compare when one declaration covers many agents. The changed paths already
  answer the question, so preview now derives `--workspace` from them: each is
  attributed to the nearest directory at or above it carrying a project marker
  (`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, …), and the scope
  is that project when exactly one is claimed **and** every capability-bearing
  changed path was claimed by it. A capability path no project owns — a root
  `prompts/system.md` travelling with a change to `services/b` — vetoes the
  answer rather than being dropped, because "this change belongs to
  `services/b`" would then be a false statement about the diff. The workspace
  root is likewise left unchanged whenever the paths do not narrow it — a
  root-level change, or one spanning two projects, which narrows to *nothing*
  rather than to their common parent, because the parent of two projects is not
  itself a project. Documentation and tests cannot outvote code: when more than
  one project is claimed, the projects claimed *only* by documentation or test
  paths drop out, and the trigger catalog's own docs-only rule decides which
  paths those are so the two surfaces cannot drift. That leg is load-bearing —
  the reported PR edits `python/agents/README.md` one directory above the
  project it adds, and counting that README as a competing claim sends the
  answer straight back to the repository root. Base detection now runs in
  preview exactly as it does in `verify` (honoring `--no-base`), because the
  promoted adoption command is the bare `verify --preview --json`: without it
  the preview every first adoption runs evaluates an empty change set and
  nothing below it can fire. Markers are read from the working tree — what
  `init` will run against — so a preview of a head that is not the current
  checkout claims no scope at all rather than recommending a directory that
  depends on what happens to be checked out. When the changed project already
  carries its own `shipgate.yaml`, preview routes to `verify` **there**, ahead
  of any root manifest, which governs a different boundary; that also stops the
  next preview from looping against a manifest that now exists. A change
  spanning several projects routes to `detect` instead of to an `init` that
  would deterministically refuse.

  `init --write` closes the same gap from the other side: a workspace whose
  manifest scope is unresolved reports
  `manifest_status: "refused_unresolved_scope"` (exit 2) with
  `auto_detected.agent_scope` and `agent_project_candidates[]`, rather than
  adopting the first agent name it parsed. Two things make a scope unresolved.
  `agent_scope: "ambiguous"` is agents in several self-contained projects —
  evidenced by everything `init` would turn into a manifest, so an
  OpenAPI-only or MCP-only project with no Python at all counts, as does a
  nested `shipgate.yaml` somebody already scoped by hand. `agent_scope:
  "unknown"` is discovery capped before it could tell: Python parsing stops at
  `--max-python-files`, and in a workspace with several project roots a
  "single project" verdict would just be whichever files were read first.
  Truncation alone is not enough to withhold an answer — a repository with one
  project root has nowhere for a second project to hide, so large
  single-project repositories keep their verdict and their working `init`. A
  refused run writes *nothing* — not the manifest, not the CI workflow, not the
  agent-instruction snippets, not the reports `.gitignore` block — so a
  workspace Shipgate declined to adopt carries no Shipgate edits into the diff.
  Rank 1 of the emitted recovery is deliberately not a command: promoting one
  candidate would make the same arbitrary pick the refusal exists to prevent,
  and the refused workspace itself is never offered back. The per-candidate
  commands repeat the setup flags the caller passed, so a recovery cannot
  silently complete with less than `--ci` or an agent-instruction selection
  asked for. `--minimal` adopts no detected name or tool surface and is
  unaffected; a repository that really is one agent surface across several
  projects passes `--allow-unresolved-scope`; and re-running `init --write
  --agent-instructions=…` on an adopted repository is untouched, because its
  scope was settled when its manifest was written.

  Two surfaces around it were wrong in the same direction. `init --ci` scoped
  to a sub-directory wrote `<project>/.github/workflows/agents-shipgate.yml`,
  which GitHub never loads — a gate reported as written that could not run; the
  workflow now lands at the repository root with a `config:` naming the
  manifest relative to that root. And `TRIGGER-SHIPGATE-MANIFEST` matched
  `shipgate.yaml` only at the repository root, so an edit to
  `services/refund/shipgate.yaml` — the file that declares that project's
  agent, purpose, and tool surface — reported `no_match`; it now matches at any
  depth, as do the pre-commit `files:` regex and the Cursor activation globs
  that copy the catalog, and a nested manifest counts as the repo-already-opted-in
  signal. `tools/shipgate-detect.py` (`script_version` `0.3.0`) carries
  `agent_scope` and `agent_project_candidates[]` too, pinned against the CLI by
  the parity test: an agent that consults the zero-install path must not adopt
  a scope the CLI refuses.

  Review found the routing could still be spent on evidence that did not
  support it, so three things changed shape. Preview now evaluates the same
  effective change set the full verifier does — committed range unioned with
  uncommitted and untracked work — because the command it emits runs against
  the working tree: an uncommitted-only capability change previously read as an
  empty diff, and an empty diff read as "nothing narrows the scope". "Scope not
  established" is now a state of its own rather than an absence: a head that is
  not checked out, a capability path no project claims, or a change spanning
  several projects each route to discovery or to human review, and never to
  initializing the repository root, which would turn "Shipgate could not tell"
  into a manifest for whichever agent the current checkout happens to hold.
  When the contested projects are already configured, each is its own gate and
  a human decides — a root manifest is not a substitute for either boundary.

  Three more surfaces followed the same distinction. Reports now default to the
  workspace the caller named, so `verify --workspace apps/a` writes
  `apps/a/agents-shipgate-reports/` — the directory that project's managed
  `.gitignore` block actually covers, and one that two projects cannot
  overwrite for each other; an explicit `--out` still resolves against the
  repository root. `--ci` writes one workflow per gated manifest
  (`agents-shipgate-<project>.yml` beside the root's `agents-shipgate.yml`),
  because the action takes a single `config` scalar and one shared file gated
  whichever project initialized first while reporting a skip for the rest; that
  scalar is now YAML-quoted when the path needs it, so a directory named
  `apps/agent #1` no longer renders a comment. And the `trigger` command reads
  the nested-manifest opt-in through the same resolver preview uses, so
  `apps/a/README.md` beside `apps/a/shipgate.yaml` stops reporting a docs-only
  skip.

  Detection got two corrections in opposite directions. A bare
  `requirements.txt` still is not a project boundary, but one sitting beside an
  agent is the only boundary that layout has — two sibling ADK agents were
  reported as one root scope, and `init` picked one of their names. And an
  `Agent(name=…)` literal only draws a boundary when its file carries a
  supported framework import: an unrelated module defining its own `Agent`
  class made a single-agent repository refuse. Those literals remain name
  suggestions. `tools/shipgate-detect.py` keeps its marker census complete
  rather than truncating it with the general file cap, so heavy filler no
  longer hides the projects it is supposed to find.
  ([#363](https://github.com/ThreeMoonsLab/agents-shipgate/issues/363))
- **One control vocabulary across the adoption walk.** `detect`, `init`, and
  `doctor` each answered "what do I do next" in their own shape, so an agent
  driving a first adoption had to learn four result formats and could not tell
  a setup obligation from a gate verdict. All three `--json` payloads now carry
  a `control` field holding the same `shipgate.agent_control/v1` envelope that
  `verify --format control`, `check --format agent-control-json`, and
  `agents-shipgate agent control` already emit — one `control_state`, one
  six-way `permissions` vector, one typed rank-1 `next_action`. It is a
  projection of the diagnostics those commands already publish, computed in one
  module, so `control.next_action` and `next_actions[0]` name the same work by
  construction; no renderer computes a second verdict. Contract `24`. Every
  existing field, including `next_action` and `next_actions[]`, is unchanged.
  Setup and gate control cannot be confused: setup reports
  `decision_source: "setup"` with a verdict from `setup_complete |
  setup_incomplete | setup_not_applicable`, and the published schema requires
  that source to come from `detect`/`init`/`doctor` *and* requires those
  operations to report no other source. Setup also authorizes nothing — it
  reads no diff, so all six permissions are false, it binds no artifact or
  control identity, and `control_state: "complete"` is unreachable for these
  operations in the schema itself, because a successful `init` is not
  permission to commit, merge, or report a task done.
- **A manifest declaration a person owes is no longer routed to the agent.**
  `init --write` reported unresolved `CHANGE_ME` placeholders and, in the same
  breath, told the caller to scan — inviting the agent to invent an
  `agent.declared_purpose`, which is exactly the class of claim
  `do_not_auto_assert` exists to protect. When a human-owned placeholder is
  unresolved, the setup control state is now `human_review_required`, the action
  names the exact file, line, and field, and **`next_action` / `next_actions[]`
  carry that same route** — publishing the control state beside an unchanged
  executable `scan` command would have left the unsafe answer exactly where a
  pre-#323 consumer reads it. Ownership covers every `do_not_auto_assert`
  surface with a manifest spelling, `agent_bindings` and `action_surface`
  included, not only `declared_purpose`. A placeholder an agent can legitimately
  resolve from the repository — a tool-source path, a project name — stays
  coding-agent work, and once the human-owned values are supplied `doctor`
  advances deterministically to `verify`. `init --write --agent-instructions=…`
  over an existing manifest now inspects *that* manifest rather than the
  template it did not write, so the documented refresh command is not a route
  around the boundary.
- **The `AgentControl` union is unchanged, and the compatibility floor stays at
  `21`.** That union is embedded by the verifier, the handoff, preflight, the
  agent result, the boundary result, and verify-run, so widening it would widen
  six durable published schemas under unchanged identifiers — and five of those
  artifacts record no `contract_version`, so a consumer holding a stored payload
  could not use the floor to tell which shape it has. A setup step that needs a
  file changed is still typed: the envelope publishes `next_action.kind: "edit"`
  with `path` and `expects`, as `SetupEditAction` — declared on the envelope,
  which is emitted on stdout and never stored, and rejected in both layers on
  any operation but `detect`/`init`/`doctor`. Routing such a step as the command
  that merely *checks* the edit was tried and is wrong: an envelope-only
  consumer executing it re-ran `doctor` against an unchanged file forever, with
  the instruction surviving only in `why`.
- **A completion cannot rest on a negative verdict.** Constraining each
  `decision_source` to its own vocabulary left the verdict free of the authority
  it sits beside, so a `complete` envelope with `permissions.merge: true`
  accepted `decision: "blocked"` — a schema-valid negative gate result granting
  terminal authority. A completed release result now admits only `passed`, and a
  completed boundary result only the non-blocking `allow`/`warn`, in both
  layers.
- **A manifest that is not UTF-8 is refused, not rewritten.**
  `errors="replace"` turned one `0xff` byte in `project.name` into U+FFFD, so
  `doctor` loaded a *different*, valid manifest, reported `setup_complete`, and
  recommended verify — while `scan` on the same file exited 4. Setup and the
  gate now validate the same input language.
- **The envelope only calls a string a command when something can run it.** A
  diagnostic's remediation is an instruction for a reader as often as it is an
  invocation: the unknown-adapter routes read
  `AGENTS_SHIPGATE_ENABLE_PLUGINS=1 agents-shipgate scan …`, a shell assignment
  `shlex.split` turns into a program literally named
  `AGENTS_SHIPGATE_ENABLE_PLUGINS=1`, and `pip install
  <third-party-adapter-package>`, a placeholder nobody can install. Both were
  promoted verbatim into `control.next_action.command` under
  `agent_action_required` — a route whose single step cannot be taken. Such a
  remediation now routes to a human, carrying the string as prose. This stops at
  the envelope: `next_actions[]` keeps the diagnostic's own action, because
  `NextAction` already withholds its computed `executable`/`args` pair for a
  string with no faithful argv while letting the rendered string stand, and the
  envelope has no equivalent way to publish an instruction that is not argv.
- **`control.next_action.path` names the file byte for byte.** The envelope's
  text type normalizes what it validates, which is safe for prose and not for a
  path: a filename may legally begin or end with a space, so `' manifest.yaml '`
  in a diagnostic became `'manifest.yaml'` in the envelope and the two rank-1
  projections pointed at two files. The prose cap now covers the setup edit's
  `why` as well, which had routed around it and published 1,134 bytes on a
  contract that documents 400.
- **Every `doctor --json` payload carries the route, including the earliest
  failure.** A `--config` glob that matched nothing raised before the projection
  and returned the legacy error shape alone, with no `control`,
  `decision_source`, or `input_id` — the one counterexample to the promise this
  rollout rests on. That branch now projects a denied setup envelope, and the
  failure identity covers the route it publishes rather than only the defect
  that caused it, so the same rejected workspace read through two entry points
  no longer answers under one `input_id`.
- **A dry run that wrote the CI workflow no longer says nothing was written.**
  `--ci` is orthogonal to `--write`, so `init --ci --json` reported
  `workflow.status: "written"` a few fields above a `why` claiming otherwise.

- **`scan` is outside this rollout, and now says so.** `agent control` after a
  scan reports `decision: null` with a `reason` stating the verdict is
  *withheld*, not absent: the scan reached one — `report.sarif` even carries it
  — but a scan pointer binds no reconfirmable snapshot of the inputs it read, so
  no artifact in that directory can show the verdict still describes the
  workspace. An edit to the manifest, a referenced `tools.json`, a policy pack,
  or a baseline leaves the pointer reading cleanly with the old answer.
  Publishing a verdict from `scan` needs a complete input snapshot threaded
  through report generation and pointer publication; until that lands, `verify`
  is where a checkable verdict comes from, and #323's scan half stays open.

- **The recommended next command now runs where it was recommended.** Every
  emitted command was written as the console script the wheel installs
  (`agents-shipgate …`), so a run started from a source checkout with
  `python -m agents_shipgate` handed the caller a command its environment may
  have had no wrapper for — and agent-mode errors reported the running command
  as `__main__.py`, which is not a program and which any consumer rendering the
  field as Markdown silently corrupts to `**main**.py`. Commands are now
  spelled for the entry point that started the process: a console-script run
  emits exactly what it emitted before, a `python -m agents_shipgate` run emits
  `<sys.executable> -m agents_shipgate …` (by interpreter path, since a bare
  `python` resolves through `PATH` and can land on a different one), and
  `AGENTS_SHIPGATE_CLI` — already the operator override for the Claude Code
  hook command — takes precedence over both, including when the path it names
  happens to end in a console-script name, and parsed with the host's own rules
  so a Windows value keeps its backslashes. One policy covers preview,
  init, doctor, scan, verify, detect, check, preflight, apply-patches, and the
  control and repair commands the verifier and boundary publish; it is applied
  at the emission boundary, so a route built as a plain dict rather than as a
  `NextAction` cannot opt out. Contract `23`. `next_actions[]` entries with
  `kind="command"` additionally carry an `executable[]` / `args[]` pair — the
  authoritative runnable form on every platform, needing no shell. It is
  *computed* from `command` rather than stored, so supplying it is an error and
  no mutation can leave it describing a command the action no longer holds; the
  pair is omitted rather than emitted as `null`, so every action that cannot
  carry an argv is unchanged on the wire, and it is withheld entirely when the
  command needs a shell — an operator, a redirection, a substitution (including
  inside double quotes, which do not make `$VAR` inert), or a `<placeholder>` —
  rather than advertising an argv that would do something else. `command`
  itself is a POSIX rendering on every platform: one renderer and one parser
  must agree, and pairing Windows argv-quoting with a POSIX parse turned
  `C:\repo` into `C:repo` — a runnable command against the wrong workspace.
  Because that rendering is uniform, `shlex.split(command)` recovers the exact
  argv on *every* surface and host, which is the documented recovery for the
  operational control contracts (`control.next_action`,
  `allowed_next_commands`, verifier repairs), where the argv pair is not
  carried. When the rank-1 action is a command, the legacy `next_action` string
  is that command verbatim, so the back-compat field cannot route a caller to a
  different program than `next_actions[0]`. Durable evidence stays canonical: `report.json`,
  `report.md`, and `packet.*` are byte-identical however the process was
  started, because "same inputs, same report" outranks runnability there and
  process entry is not an input. This is the path used to evaluate external
  PRs, where the recovery loop breaking is worst. ([#322](https://github.com/ThreeMoonsLab/agents-shipgate/issues/322))

- **A prompt or policy edit outside the repository root — or spelled
  `Policies/` — no longer reports as "nothing in this PR signals a tool-surface
  change."** `TRIGGER-PROMPTS-OR-POLICIES` matched `prompts/**` and
  `policies/**` only at the root, and the trigger evaluator matched globs
  case-sensitively while the verifier's trust-root classification reads the
  same two surfaces at any depth (`**/prompts/**`, `**/policies/**`) and
  tolerates the case variant a case-insensitive filesystem resolves to the
  canonical name. The two lists disagreed about the same paths: a PR touching
  `services/foo/policies/refund.yaml` reached `skip_reason: "no_match"` for a
  path `SHIP-VERIFY-POLICY-WEAKENED` treats as a policy trust root, and
  `services/foo/Policies/refund.yaml` did the same after the recursive fix
  alone. Prompts failed worse than that — a nested `prompts/*.md` edit
  satisfies the docs-only negative rule's `**/*.md` leg, so it did not merely
  fail to match, it actively *skipped*. Both globs now match at any depth, in
  the positive rule and in `TRIGGER-DOCS-ONLY-NEGATIVE`'s `none_match_glob`
  list, so a nested prompt edit bundled with a docs edit is no longer
  classified as docs-only; and the routing predicates — `glob` and
  `none_match_glob` — now use the same case-tolerant matcher as the
  trust-root classifier and the boundary registry. `every_file_matches`
  deliberately does not: it is the docs-only rule's own classifier, so
  folding it would *subtract* evaluation rather than add it. The catalog's
  `predicate_vocabulary` documents both sides of that split; see the
  directional rule below.

  The surfaces that copy this routing follow, so the fix is end-to-end rather
  than evaluator-only. The pre-commit `files:` regex now matches
  `prompts/`, `policies/`, `.codex-plugin/`, `.agents/plugins/`, `.n8n/` and
  `AGENTS.md`/`CLAUDE.md` at any depth, covers the n8n and Conductor path legs
  it silently omitted, and matches a tracked path named exactly `dir` for a
  `dir/**` glob — its "covers every path-based trigger" claim is now pinned
  exhaustively, so a catalog rule with a path leg must be listed in the hook
  fixture table or named in its exclusion set. Both documented copy-paste hook
  snippets are derived from the canonical regex and pinned clause-by-clause
  rather than by a hand-maintained sample. The `.cursor/rules/agents-shipgate.mdc`
  activation globs gain the recursive forms too: that rule is
  `alwaysApply: false`, so until a glob matched, a lone nested governance edit
  activated no Shipgate instructions at all — and the benchmark setup variant
  and the adoption harness's lint constant, two copies of that list that
  nothing enforced, are now pinned to the renderer.

  A new parity test pins every governance trust-root surface — the manifest,
  `.agents-shipgate/`, `policies/`, `prompts/`, and the Shipgate CI workflow —
  to a representative repo-root path, a nested one, and a case variant, so the
  trust-root list and the trigger catalog cannot drift apart again unnoticed.
  It records that a nested `shipgate.yaml` and a nested `.agents-shipgate/` are
  still routed at the root only, because those two are anchored there by the
  boundary registry that check, verify, preflight and audit share rather than
  by the catalog. The catalog stays at `schema_version` 0.3: this widens an
  existing rule's globs, adds no rule ID and no state, and moves outcomes only
  toward evaluating more, never less.

  Case tolerance is chosen per predicate by which way a wider match moves the
  verdict, not for uniformity. `glob` and `none_match_glob` are folded because
  a wider match can only add a run or make a negative rule fire less.
  `every_file_matches` stays case-sensitive: it is the docs-only rule's own
  classifier and `skip_shipgate` beats `run_shipgate`, so folding it would read
  `src/TEST_agent.py` — a production module on a case-sensitive filesystem — as
  a test file and skip a PR that adds `@function_tool` beside it. The same
  tolerance now reaches the Tier B checks: `_verify_common.touched()` selected
  changed files case-sensitively, so `services/foo/Policies/refund.yaml` was a
  policy trust root that produced no fail-safe finding, and a deleted
  `.github/workflows/Agents-Shipgate.yaml` was a `ci_gate` trust root that
  missed the critical gate-removal finding entirely. Preflight's three inline
  copies of the same retry are folded into the one helper.

  Both pre-commit hooks now declare `types: []` with
  `types_or: [file, symlink]`. pre-commit's default `types: [file]` is an
  AND-filter applied *before* `files:`, and a tracked symlink carries the
  `symlink` tag rather than `file` — so a governance directory symlinked into
  a workspace invoked neither hook no matter what the regex said.

- **The `on-tool-source-changes` CI recipes are retired**, on GitHub Actions,
  GitLab CI and CircleCI alike. They gated Shipgate behind a changed-path
  allowlist, which cannot work for two independent reasons. First,
  `TRIGGER-EXISTING-MANIFEST-PRESENT` is `force_run`: a repo with a
  `shipgate.yaml` is contracted to run on every PR, so the prefilter never
  saved the scan it advertised. Second, every prefilter language involved —
  GitHub `paths`/`paths-ignore`, GitLab `rules.changes`, a CircleCI shell
  diff-gate — matches case-sensitively, while the trigger catalog matches
  governance paths case-insensitively on purpose; an allowlist therefore drops
  `services/foo/Policies/refund.yaml` with no job, no check and no signal at
  all, which is indistinguishable from a repo that never adopted the gate. Run
  the advisory recipe on every PR and let the in-job trigger evaluator decide;
  `verify` short-circuits before the scan on a skip verdict. A contract test
  now rejects any of those prefilter forms in any shipped recipe, across both
  `.yml` and `.yaml`.
- **One compact object now answers "what may I do next?", instead of four
  artifacts and a guess.** A verify run could simultaneously report `execution:
  "succeeded"`, exit code `0`, `release_decision.decision: "review_required"`,
  and `control.state: "human_review_required"` — four facts, three of which
  read like permission to continue, spread across thousands of tokens of
  forensic JSON. New `shipgate.agent_control/v1`
  ([`docs/agent-control-schema.v1.json`](docs/agent-control-schema.v1.json))
  carries tool execution status, the release or boundary decision and which
  engine produced it, the control state, the six-way `permissions` vector, the
  next actor, the exact next action, the identity of the input it was assessed
  against, any review obligations still owed, and the path and sha256 of every
  artifact `current-control.json` binds — in one stdout object under a published `agent_control_budget_bytes`
  budget of 4096 — a measured target, not an enforced cap, since a long
  reviewer list or exact command must never be truncated to fit — roughly a
  fifth of the `verifier.json` plus `agent-handoff.json` an agent reads today
  to answer the same question. Three
  separations that were documentary are now structural: a failed execution can
  never authorize completion (and a *succeeded* one implies nothing); a
  stopping state authorizes nothing; and `permissions.merge`, not `exit_code`,
  answers "may I merge" — the exit code is the CI gate signal and in advisory
  mode a `blocked` decision still exits 0, which is now pinned across all four
  decisions in both modes. The envelope decides nothing: every field is copied
  from a producer that already published it, and where the current-control
  pointer refused a completion its run claimed, the pointer wins and the run's
  route is dropped rather than recovered. Emitted by `agents-shipgate verify
  --format control` (added; `--json` still emits the full verifier artifact),
  `agents-shipgate check --format agent-control-json` (added;
  `agent-boundary-json` unchanged), and `agents-shipgate agent control`, whose
  default output changes from the raw pointer to the envelope — `--format
  pointer` returns the previous output unchanged. `verify --format text` now
  leads with the control state, next actor, and permission vector before the
  existing verdict line. Both entry points run one currency test: `verify
  --format control` validates its own published pointer against the live
  workspace and withholds authority when the workspace moved past what the run
  evaluated, instead of reporting `complete` on a directory `agent control` was
  simultaneously refusing, and it routes from the verifier bytes captured inside
  that read so a pointer can never be paired with another generation's decision.
  Emitted artifact paths are relative to the invoking directory and joined
  structurally, so a root of `/` or a trailing space cannot rename the file
  whose hash was validated. `input_id` binds compact authority to the input it
  assessed — required on `complete`, since two unrelated diffs otherwise
  projected byte-identical envelopes granting merge — and `pending_review[]`
  carries obligations a non-terminal route still owes. Human-readable output
  renders control characters visibly and keeps one field per line, closing a
  spoof where a workspace path containing newlines printed forged `Control:
  complete` and `You may: ... merge` lines; JSON keeps the exact bytes. A
  current-but-routeless generation (a `scan` pointer) is now reported with exit
  0 and merge denied instead of refused, preserving the documented meaning of a
  non-zero exit, and recovery commands are generated from the requested
  workspace and reports directory rather than a hardcoded default. Terminal
  authority is additionally constrained by provenance — `complete` is
  representable only from `verify` (naming its pointer and artifacts) or from
  `check` (naming neither), never from `scan` or `preview` — and a `verify`
  route cannot drop `verify_required`; both are published in the JSON Schema,
  not only enforced in Python. `verify --format control` now reports only this
  invocation's generation instead of whichever is current, and the currency
  comparison re-observes the workspace after confirming the pointer, closing a
  window in which a commit could land mid-read. Separately,
  `.shipgate/agent-contract.json` now upgrades in place from any superseded
  managed version rather than only from renders whose exact hash was recorded —
  repositories on local-contract schema 8 or 9 were stranded. Runtime contract advances `21 → 22` and the downstream
  local contract `9 → 10`; `minimum_control_contract_version` stays at `21`
  because the `AgentControl` union itself is unchanged.
  ([#333](https://github.com/ThreeMoonsLab/agents-shipgate/issues/333),
  [#323](https://github.com/ThreeMoonsLab/agents-shipgate/issues/323),
  [#338](https://github.com/ThreeMoonsLab/agents-shipgate/issues/338))

- **The release pipeline now proves the wheel it publishes came from the
  tagged commit.** The tag workflow established three bindings — tag to
  `pyproject.toml` version, qualification payload to wheel bytes, and tag to
  the wheel's own `METADATA` version — but none tied the shipped bytes back to
  any source tree. It tested the checkout with `ruff`, `compileall`, and
  `pytest`, then published a wheel downloaded from a repository-variable URL,
  with nothing asserting the two corresponded: any wheel declaring
  `Name: agents-shipgate` and the right `Version` satisfied every check.
  Verification now rebuilds the wheel from the tagged checkout and requires
  byte equality with the qualified wheel before publication
  (`scripts/verify_wheel_provenance.py`). Byte equality is achievable because
  the build backend is pinned in `constraints/release-build.txt` — wheels
  record `Generator: hatchling <version>`, so an unpinned backend alone
  changes the archive. A container-metadata-only difference is reported as a
  reproducibility gap and still fails; the weaker unpacked-content bar exists
  behind an explicit `--allow-payload-equivalent` flag so it can never be
  taken silently. The published artifact is still the signed, qualified wheel;
  the rebuilt one is only a comparison reference.
- **Verification and publication are now separate jobs, and a partial publish
  is recoverable.** Expensive verification and immutable publication ran in
  one job, so a failure after a successful PyPI upload could leave an
  immutable version with no finalized GitHub Release and no attached
  provenance — and re-running was not a safe recovery, because the version
  already existed. Verification is now a read-only reusable workflow
  (`contents: read`, no OIDC) that hands off a content-addressed candidate
  bundle; the manifest digest travels through the job-output channel, so
  swapping an artifact — or rewriting the manifest to agree with the swap — is
  detected, and the check is closed-world so an unlisted file cannot ride
  along. Write and OIDC authority are never held by the same job: the PyPI
  publisher holds `id-token: write` alone, checks out no project code, and
  installs only a hash-locked toolchain, so a compromised dependency cannot
  both mint a token and rewrite the repository. A **draft** GitHub Release
  carrying the authoritative assets exists before the upload, and finalization
  happens only after asset validation. The upload is idempotence-aware:
  `scripts/release_publication.py pypi-state` classifies the index as `absent`,
  `published_identical` — requiring *exactly one* unyanked wheel with the
  expected filename and digest, so a divergent sdist or second wheel is not
  mistaken for a completed transaction — or `published_divergent` (always
  fatal). An unreachable index is never read as permission to upload, and an
  already-published release is verified and left untouched rather than
  clobbered. Release
  concurrency is serialized across the PyPI project rather than per tag, with
  `cancel-in-progress: false`. The `pypi` reviewer gate moved to publication,
  so reviewers approve *after* the readiness summary exists instead of
  approving a run whose evidence has not been produced yet.
- **The qualification signer identity is reviewed code, and a release candidate
  must have been rehearsed.** The identity and OIDC issuer that authenticate the
  signed qualification artifact now live in `.github/release-trust-roots.json`
  rather than in variables: an actor able to set variables could otherwise
  substitute fabricated evidence *and* replace the allowlist that vouches for
  it in one unreviewed step — an attack source-to-wheel binding cannot see,
  because it reuses the legitimate wheel and forges only the claims about it.
  Only content-addressed locations stay mutable. Verification also runs against
  the immutable event SHA rather than the symbolic tag ref, and the tag is
  re-peeled against the remote before each irreversible step, so a moved tag
  cannot make the pipeline build one commit while claiming another.
  Publication additionally requires a successful rehearsal at the same source
  SHA whose candidate manifest is byte-identical, and every rehearsal now
  proves the provenance gate fails closed by injecting a tampered wheel and
  asserting it is rejected.
- **The signed SBOM now describes the shipped wheel instead of the CI
  machine.** The workflow installed `.[dev]` and ran `cyclonedx-py
  environment`, inventorying pytest, ruff, twine, Sigstore, and the CycloneDX
  tooling itself — a signed attestation about software the user never
  receives. `scripts/release_sbom.py` inventories an isolated, runtime-only
  install of the qualified wheel, binds the document to that wheel's SHA-256,
  and re-verifies the binding before publication. It also normalizes away the
  `file://` build-machine path CycloneDX records, which otherwise leaked runner
  filesystem layout into a published artifact and made the signed SBOM
  non-deterministic. The dev-only exclusion is derived from the `dev` extra
  rather than hardcoded, so new tooling is covered automatically.
- **A release candidate can be rehearsed without any publication authority.**
  The workflow could only be exercised by pushing a `v*` tag, so its
  verification and failure paths were first-run at the moment publication
  became possible — steps added after v0.15.0 had never executed. A
  `workflow_dispatch` rehearsal now runs the identical build, qualification,
  test, audit, SBOM, and handoff path by calling the same reusable workflow,
  and is structurally incapable of publishing: no publication job exists in the
  file, `permissions: contents: read` caps the token so tag and release
  creation fail, and no `id-token: write` means Trusted Publishing cannot mint
  a token. Rehearsal is a documented prerequisite for a candidate tag.
- **Release test selection matches CI, so candidates fail on correctness
  evidence rather than timing noise.** The release ran the full suite serially,
  including timing-sensitive `perf` tests, inside a 15-minute budget shared
  with qualification, audit, signing, and artifact work. It now uses CI's
  `-n auto` parallelism and excludes `perf`-marked latency budgets, which
  remain enforced at merge time; the adapter static-only trust-model lint keeps
  its own fail-fast step, and the coverage floor stays at CI's 85. The timeout
  is derived from measurement rather than estimate, with the basis recorded in
  `docs/release-runbook.md`.
- **The release page carries the changelog, and the release runs the
  environment CI approved.** Two loose ends from the release-workflow review.
  The GitHub Release body was the placeholder `Agents Shipgate <tag>` while
  `CHANGELOG.md` held the entry describing what actually shipped, so the one
  artifact users read said nothing; `scripts/release_notes.py` now extracts the
  section matching the tag and publishes it through `--notes-file`, verbatim
  and from the checkout pinned to the verified commit rather than retyped at
  tag time. A missing section fails **verification**, which the rehearsal also
  runs, so it is caught while the tag does not yet exist — and `## Unreleased`
  never matches a tag, which is what makes promoting that heading a step the
  pipeline enforces. A body over GitHub's 125,000-character limit is refused
  there too, rather than by a 422 after tagging. Verification, staging and
  finalisation each extract the section, so verification publishes its SHA-256
  and the other two must land on it; finalisation reapplies the body **in the
  same API call that undrafts**, because the window between staging and
  publication — the environment approval included — is time in which a
  release-write actor can edit a draft's text that nothing downstream re-reads.
  Every job writes the file under `$RUNNER_TEMP`, so a candidate cannot decide
  where the write lands. Separately, `pip install -e ".[dev]"` resolved fresh
  at release time, so the run that decided whether to publish could install
  different packages than the CI run that approved the commit, and a
  release-only failure was not reproducible from the same tree. CI and release
  verification now install the identical hash-locked closure in
  `constraints/dev.txt`, add the project with `--no-deps` (an editable install
  cannot be hashed) **and `--no-build-isolation` against the hashed backend
  closure in `constraints/build-backend.txt`** — `--no-deps` does not disable
  PEP 517 build isolation, and current pip does not apply `PIP_CONSTRAINT` to
  an isolated build environment, so the backend and its own dependencies were
  still being resolved from the index on every run — and finish with
  `python -m pip check` proving the closure satisfies what the project
  declares. Regeneration is one command for every lock in the repository
  (`scripts/update_locks.py`, which restores the headers `uv` would
  overwrite), and each lock now records the normalized PEP 508 declarations it
  was compiled from, so a declaration that grows an extra, moves behind a
  marker or becomes a direct URL invalidates it even though every name and
  range still matches. `scripts/verify_dependency_lock.py` — run in CI and
  before publication — checks that binding plus a declared requirement with no
  pin, a pin outside the declared range, a direct requirement the declarations
  no longer contain, a pin without a hash, and locks installed together that
  disagree. Markers are compared by *evaluating* them over the environments the
  project supports (CPython 3.12–3.14 × linux/darwin/win32 × x86-64/aarch64)
  rather than as text, so a conditional declaration that is genuinely missing is
  distinguished from one no supported environment selects, a pin whose own
  marker excludes the platform that needs it is caught, and a valid universal
  fork is not mistaken for a conflict. `[build-system]` is bound to the same
  closure, so a raised backend floor or a switch to another backend can no
  longer leave every file consistent and the wheel built by something nobody
  pinned. CI builds the package with `--no-isolation` for the same reason. It
  never re-resolves against the index, so an unrelated upload cannot turn the
  build red. Two release-only defects found in review are fixed here as well:
  `publish` and `finalize` used a local composite action without checking the
  repository out, which fails while *preparing* the action — the first release
  to reach publication would have broken there, so both now check out that one
  action directory sparsely, at the verified commit, with cone mode off; and the
  publication allowlist matched only `name==version` lines, so a `name @ URL`
  requirement was installed in the token-bearing jobs without ever being
  compared against it — every requirement form it cannot review is now refused,
  and the check runs as its own step against crafted lockfiles in the suite.
  ([#345](https://github.com/ThreeMoonsLab/agents-shipgate/issues/345))
- **Insufficient-evidence remediation now stays framework-aware from the
  decision engine through every primary short-form surface.** Semantic
  `incomplete_surface` gaps for frameworks with explicit inventory support now
  lead with the generated `suggested-inventory.json` artifact and the exact
  `<framework>.tool_inventories` manifest key instead of an unreachable generic
  MCP/OpenAPI route. Console scan output, the GitHub step summary, and text-mode
  `verify` all project that same rank-1 action; unsupported source shapes retain
  the compatibility fallback. Human work now precedes the exact rerun command
  in text output, and verifier fix tasks collapse the duplicate semantic and
  extraction inventory remedies into one instruction. The regression runs a
  real Google ADK workspace through static extraction and semantic assessment,
  so it cannot manufacture a pass-ineligible medium-confidence tool state.
  ([#318](https://github.com/ThreeMoonsLab/agents-shipgate/issues/318))

- **Human review now blocks merge and completion, not publication of the
  evidence a human needs in order to review.** A human route was one universal
  stop: `control.state: "human_review_required"` with `must_stop: true` and
  `allowed_next_commands: []`. For an agent working on a pull request that
  denied commit, push, and PR updates — the exact actions required to produce a
  reviewable diff — so the workflow was circular: review was required, and the
  agent could not publish the state to be reviewed. Two additive changes fix
  it. `control.permissions` is a new object carrying the exact booleans
  `edit`, `commit`, `push`, `update_pr`, `merge`, `report_complete`; it is
  fixed by the state and the route, never set independently, and
  `merge`/`report_complete` always equal `completion_allowed`, so human review
  never becomes self-approvable. A route that runs before any diff was read
  (`fetch_base`, `install`) authorizes none of the six. A fourth state, `review_publishable`, means "a
  human must approve the merge, and the agent may still publish the change for
  that review": `must_stop: false`, a human `next_action`, and at most the one
  exact rerun command that regenerates the same evidence against the committed
  refs. `human_review_required` keeps its exact old meaning and is now reserved
  for results Shipgate cannot vouch for — a blocked release decision, a `block`
  boundary decision, a run whose execution failed, unreadable or unbindable
  diff input, an undeclared capability surface with no discovery route,
  preflight protected-surface touches, and MCP audit blocks. Runtime contract
  advances `20 → 21` and `minimum_control_contract_version` `14 → 21`;
  consumers that switch on `control.state` must add a `review_publishable`
  branch and keep failing closed on unrecognized states, while consumers that
  read only `must_stop` and `completion_allowed` need no change and lose no
  safety. Every schema that carries a control advances its identifier and
  freezes the prior file — verifier `0.7 → 0.8`, handoff `v6 → v7`, verify-run
  `v3 → v4`, shared agent result `agent_result_v2 → v3`, agent boundary result
  `v1 → v2`, preflight `0.3 → 0.4`, downstream local contract `7 → 8` — because
  `permissions` is a new property on variants published as
  `additionalProperties: false`, and leaving the identifier in place would have
  made one version name mean two incompatible shapes. CLI flag spellings are
  unchanged, and `audit_id` does not rotate: the schema token it hashes is
  pinned to the value established ids were issued under.
  `shipgate.codex_boundary_result/v2` stays frozen and now carries its own
  snapshotted control union instead of inheriting the live one.
  Publication additionally requires a replayable subject,
  fully-read input, and a succeeded non-blocked release decision — enforced in
  Pydantic and in generated JSON Schema — so a detached diff, a partially
  unparsed MCP audit, or a failed run can never authorize it. Legacy artifacts and the frozen
  `shipgate.codex_boundary_result/v2` projection are unaffected: pre-v20
  payloads normalize to `human_review_required`, and the frozen format omits
  `control.permissions` and renders the new state as `human_review_required`
  with `must_stop: true`. The installed Claude Code Stop hook now says, on a
  publishable review, that commit/push/PR-update remain authorized and names
  the rerun command. ([#335](https://github.com/ThreeMoonsLab/agents-shipgate/issues/335))
- **Local verification now evaluates committed and uncommitted edits as one
  effective worktree diff.** When a branch change and a review follow-up touch
  the same path, `verify` compares the merge base directly with the current
  worktree instead of concatenating overlapping diff records. The evaluated
  change set is merge-base-relative while the overlay is HEAD-relative, so the
  verification plan binds the exact HEAD-relative overlay path set separately —
  a path canceled by an uncommitted edit leaves policy evaluation but stays
  bound, at its real content, in the receipt. Canceled committed changes are
  called out in `base_notes`; worktree diff collection honors repositories that
  set `core.fileMode=false` rather than forcing Git's mode reads on, which had
  turned every tracked file in such a checkout into a phantom mode change; and a
  plan that predates the bound overlay path set fails with an explicit
  re-prepare action.
  ([#336](https://github.com/ThreeMoonsLab/agents-shipgate/issues/336))

- **A coding agent can no longer enforce a verifier result the workspace has
  outgrown.** The reported failure ran forward: a worktree verify returned
  `human_review_required`, a human committed the reviewed change, a fresh
  committed-ref run produced a `complete` receipt for the same request — and the
  agent kept enforcing the older `must_stop`, asking for the commit that had
  already happened. It runs backward just as easily: a `complete` remembered
  from earlier in a conversation is not evidence about a workspace that has
  since been rebased, checked out, or reconfigured. The content-addressed
  receipt already prevented an old decision from *authorizing a different
  request*; what was missing was one atomic place to ask "what is current now?",
  and any obligation to ask it. Both are now present.
  `agents-shipgate-reports/current-control.json`
  (`shipgate.current_control/v1`) is that entry point. It is a pointer, not a
  second decision engine: it binds identities and hashes of the receipt,
  handoff, verifier, and report those commands already publish. Its lifecycle is
  what makes it trustworthy. `verify`, `verify --preview`, `scan`, and
  `verification prepare` each replace it with a non-terminal `unavailable`
  marker *before* touching any other artifact, so a run that crashes leaves a
  directory that denies cached control instead of one that still advertises the
  previous verdict for a workspace that has moved; the terminal pointer is
  written last, after every artifact it references exists and has been hashed,
  and published by same-directory `os.replace` so no reader can observe a
  half-written one. Readers use the generation-safe protocol in `agents-shipgate
  agent control`: validate the pointer, validate every artifact hash it binds,
  re-read the pointer, and continue only if `current_control_id` is unchanged —
  a run that republishes mid-read makes the read fail rather than return one
  generation's pointer beside another's artifacts. And because byte consistency
  is not generation consistency — every bound artifact still hashes correctly
  one unrelated commit later — the read also compares the pointer's
  `workspace_identity` against the live repository: repository, HEAD commit, and
  HEAD tree, plus the base revision when the decision named one — advancing a
  base until `base...HEAD` is empty changes the evidence completely while
  leaving HEAD and the working tree untouched. Uncommitted work is checked
  against what the decision actually covered: a worktree decision must still
  hash to the overlay it committed to *and* see no live change outside the set
  it recorded, while a committed-tree decision, whose evidence stops at HEAD, is
  invalidated by any uncommitted change that appeared afterwards. Overlay rows
  bind entry kind and the executable bit alongside content, so a `100755` →
  `100644` flip or a regular-file-to-symlink swap with identical bytes cannot
  pass as unchanged. Completion authority is never returned without that
  comparison. Two invariants are structural rather than
  advisory: only an `operation: "verify"` pointer can carry
  `control.state: "complete"`, and only when it also binds a
  `verification_receipt` whose request and decision are the ones the pointer
  records — the assembler accepts any `--out` name under its artifacts root, so
  an older canonical receipt cannot be mistaken for the one a run just closed.
  A scan or a preview cannot represent completion authority at all, and each
  pointer binds only the artifacts its own run wrote: a `scan --format markdown`
  after a verify no longer claims that verifier's `report.json`.
  Supporting scans stay isolated —
  `verify`'s internal head scan does not take over the PR's control identity,
  and `baseline save` already scanned into a temporary directory. Contract
  `19 → 20` adds `current_control_schema_version`, `current_control_artifact`,
  the `agent_refresh_triggers[]` list of boundaries at which a cached control
  state expires, and `current_control_fallback_read_order[]` for consumers built
  before the pointer existed; `agent_read_order[]` now starts at the pointer,
  and the local downstream contract moves `7 → 8`. Generated agent instructions
  and both adoption kits now require the refresh. No report, packet, verifier,
  handoff, or receipt schema changed. ([#339](https://github.com/ThreeMoonsLab/agents-shipgate/issues/339))

- **An unreadable PR diff is no longer reported as "nothing here is
  agent-related."** `verify --preview` collapsed every diff-acquisition failure
  into one message, then evaluated the trigger catalog against the empty inputs
  that failure left behind — publishing `skip_reason: "no_match"` with the
  rationale *"nothing in this PR signals a tool-surface change"* about a PR it
  had never read. The top-level control result stayed fail-closed
  (`merge_verdict: "unknown"`), but the explanation invited exactly the wrong
  conclusion, and on an unconfigured workspace the failure was not reported at
  all: both diff-failure branches were gated on a manifest being present, so a
  shallow or blobless clone of an un-adopted repository — the normal shape of
  first contact — fell through to *"Shipgate is not configured in this
  workspace"* with the Git error visible nowhere but `base_notes`. Three
  things changed. Diff acquisition is now classified rather than flattened:
  `not_attempted`, `refs_missing`, `merge_base_missing`,
  `unrelated_histories`, `objects_missing`, `metadata_limit_exceeded`,
  `body_limit_exceeded`, `git_timeout`, and `git_failed` are read off Git's own
  diagnostic — including the two causes Git reports identically as "no merge
  base", a shallow checkout that deepening repairs versus two roots that no
  fetch can ever join — and travel on the new
  `verifier.json` `diff_status` block together with a bounded, path-redacted
  excerpt and the precise repair — deepen history, hydrate partial-clone
  objects (verification sets `GIT_NO_LAZY_FETCH=1`, so Git will not fetch them
  implicitly), or take it to a human when fetching cannot help. Metadata and
  body are collected separately, so a diff whose body cannot be read no longer
  discards the changed paths that were read successfully; a blobless clone
  answers `--name-status` in full, and those paths are exactly what says a PR
  touches an agent surface. And the trigger evaluator gained the state it was
  missing: `input_status` and `evaluation_status`, with `should_run`,
  `run_shipgate`, `skip`, and `skip_reason` all `null` when the inputs were not
  fully read. The asymmetry is deliberate — rule matching is monotone in the
  evidence, so a *run* verdict reached from partial evidence stays sound and is
  still published, while any *skip* verdict is withheld. Trigger catalog schema
  `0.2 → 0.3` (nullable verdict fields, the two new fields, and the new
  `next_action.kind: "input_required"`); verifier schema `0.6 → 0.7`
  (`diff_status`; v0.6 remains a frozen reference and is still readable).
  `contract_version`, `report_schema_version`, and every other schema counter
  are unchanged.

- **Google ADK repositories that share one tool between agents can be scanned
  again.** Binding the same `FunctionTool` to a coordinator and its sub-agents
  is the canonical ADK multi-agent shape — it is what `google/adk-samples`
  demonstrates — and it aborted the scan with `Duplicate tool observation
  identity` before any finding or `release_decision` existed. That is worse
  than an abstention: `insufficient_evidence` at least routes a human, while a
  hard input failure produces nothing to act on, so on real multi-agent ADK
  repositories the supported adapter returned no gate at all. The extractor
  emitted one `Tool` per *agent binding*, and catalog observation identity is
  `(source_type, source_id, native_locator)` where the locator is the file plus
  function name — so the second binding of one function collided with the
  first. The fix is not to widen that identity with the agent name: one
  function is one action, and minting a capability per binding would inflate
  every count derived from the catalog and quietly change what "unique tools"
  means. The function is now observed once, and the many-to-many binding
  relation travels as framework-owned `AgentBindingObservation` records — the
  same surface the OpenAI Agents SDK adapter already uses — so all three
  bindings survive as first-class edges in the binding graph, in
  root-reachability, and in each tool's `binding_assessment.claims[]`, each
  claim pointing at its own `LlmAgent(...)` call site. Sharing a tool is still
  distinguished from declaring one twice: the observation-identity guard is
  untouched, and a source that genuinely repeats a declaration still fails
  closed. A toolset assigned to a variable and shared between agents is
  likewise loaded once rather than once per binding, and a function bound as
  both `FunctionTool` and `LongRunningFunctionTool` keeps the stricter
  long-running contract and raises a warning instead of letting binding order
  decide. Two consequences worth knowing when upgrading: ADK Python tools no
  longer carry the single-valued `adk_agent_name` annotation (bindings can no
  longer be read off a tool, which was only ever able to name one of N
  agents), so the first scan after upgrade may report a metadata-only
  annotation-hash change for ADK tools — tool identities, fingerprints,
  baselines, and decisions are unaffected; and `frameworks.google_adk` gains
  `tool_binding_count` alongside `function_tool_count`, which now counts tool
  *definitions*, so a function shared by three agents reads as one tool and
  three bindings. `tool_binding_count` is additive and not yet listed in the
  published report schema's `required` set; `report_schema_version` stays at
  `0.34`.

- **Standalone scans now retire the complete verifier route as one lifecycle
  set.** A later `scan` removes `verifier.json`, `agent-handoff.json`, the PR
  comment and run projection, and every verification identity input and output
  before publishing a replacement report, so stale control actions cannot
  survive beside a newer release decision. Cleanup failures now return the
  exact artifact path and recovery action in agent mode. `baseline save` keeps
  its supporting scan in a temporary directory, preserving both the current
  report and forensic verifier evidence.
- **The trigger catalog now recognizes a Google ADK `tools=[...]` list, and
  stops calling a bare package token a version bump.** A PR adding an ADK
  sample whose root agent is `LlmAgent(name="support_agent", tools=[
  lookup_account, delete_account])` — two directly reachable tools, one of
  them destructive — routed as `skip_reason: "dry_run_only"`. The only rule
  that fired was `TRIGGER-FRAMEWORK-VERSION-BUMP`, on a raw `google-adk`
  string somewhere in the diff, and it reported the result as a framework
  upgrade. Two separate defects sat behind that. The first was catalog
  drift, not a missing capability: detection
  (`GOOGLE_ADK_AGENT_CLASSES = {"Agent", "LlmAgent"}`), the ADK adapter, and
  the binding graph all resolve this exact shape, and the same sample
  reports every catalog tool reachable — `docs/triggers.json` was the one
  component carrying no ADK rule at all. Plain functions handed to
  `tools=[...]` carry no decorator, so `@function_tool` / `FunctionTool(`
  never sees them, and the framework's most common agent spelling had no
  positive route. `TRIGGER-GOOGLE-ADK-AGENT-TOOLS-CHANGED` closes that: a
  `tools=[...]` argument alongside either `LlmAgent(` — a class name no other
  supported framework exports, so it identifies ADK by itself — or a
  `google.adk` module path plus an `Agent(` construction. Both legs are
  needed, and for a reason that is easy to get wrong: requiring the import
  covers only whole-file additions, because the ordinary edit that adds one
  tool to an existing agent shows the constructor and the list but leaves the
  import far outside the hunk. `Agent(` stays gated behind the ADK token
  because CrewAI builds `Agent(..., tools=[...])` too, and routing that under
  a rule ID naming Google ADK would repeat the defect below. The residual
  gap is a *modified* list on the `Agent` alias, which diff text alone cannot
  attribute; it is documented in the rule and in AGENTS.md. The second defect
  is the more general one
  — the rule stated a conclusion its evidence could not support. Nothing
  about the string `google-adk` establishes that a dependency version moved;
  it comes just as easily from install prose or a sample import, which is
  why a docs-only change could be classified as a framework upgrade.
  `TRIGGER-FRAMEWORK-VERSION-BUMP` now requires both halves of its claim —
  the package token *and* a changed dependency manifest — and its rationale
  says what it observed (a co-occurrence) rather than what it inferred (an
  upgrade). What is gone is the route where a README mentioning a framework
  was reported as one. The manifest set is **not** a hand-written list in the
  catalog: a first cut of this change was one, and it silently dropped
  advisory coverage for every pip-tools repository, which authors a bump in
  `requirements.in` and compiles it to `requirements.txt` — a real
  `google-adk` bump in a `.in` file went from `dry_run_recommended: true` to
  `no_match`. The set now lives in `DEPENDENCY_MANIFEST_GLOBS`
  (`agents_shipgate.core.dependency_manifests`), covers both halves of the
  pip-tools pair plus the modern lockfiles (`pdm.lock`, PEP 751
  `pylock*.toml`, bun, conda) across Python, Node, and the JVM, and is
  projected into `docs/triggers.json` under a contract test that fails when
  the two drift — the same guard `boundary_adapters` already had. On net,
  real-bump coverage is wider than before this change, not merely preserved.
  The rule ID
  and the catalog `schema_version` (`0.3`) are unchanged: rule IDs are
  stable for `0.x`, and this is rule precision inside the existing schema,
  so an external agent that pre-fetched the catalog keeps working. Both
  rules are diff-only, so the path-based pre-commit `files:` pre-gate still
  cannot decide them; that caveat is now stated for the dependency rule too,
  which had grown a path leg.

- **`input_set_id` now covers every input the adapters actually read.**
  ([#299](https://github.com/ThreeMoonsLab/agents-shipgate/issues/299))
  `input_set_id` is the identity `verification-plan.json`,
  `verification-unit-result.json`, `verify-run.json`, the terminal receipt, and
  attestations all rest on, and its whole claim is that two runs sharing it read
  the same bytes. Three things broke that claim. The manifest-derived branch of
  `build_verification_plan` walked only `tool_sources`, so
  `openai_api.prompt_files` — and every other framework block that names paths:
  `anthropic`, `google_adk`, `langchain`, `crewai`, `n8n`, `codex_plugins`,
  `validation.evidence`, `checks.policy_packs`, `agent.sdk.entrypoint` — never
  became a plan blob. Rewriting a prompt to say refunds need no approval left
  `input_set_id` byte-identical. Worse, the *observed* branch was inert on the
  committed-tree path: `verify --base X --head Y` evaluates an archived copy of
  the head tree, while the static-input snapshot that records adapter reads is
  bound to the worktree, so it captured nothing and the run emitted
  `tool_sources: []`. On the CI path — where the receipt is the artifact anyone
  downstream actually trusts — *no* input reached the request identity at all.
  And enumerating the manifest, however completely, can never reach an input the
  manifest does not name: a Google ADK `McpToolset` inventory or an OpenAPI spec
  constructed inside `agent.py` is discovered while parsing, not declared. Two
  trees whose MCP inventories differed by a trailing newline produced the same
  prepared `input_set_id`.
  Identity is therefore taken at the read boundary, not from declarations. Each
  producer snapshots the tree it evaluates and records what the adapters open: a
  committed-tree run is now snapshotted against the archived tree it scans
  (previously impossible — the snapshot watched the worktree), and
  `verification prepare` loads sources to record their reads. Committed-tree and
  worktree runs of the same tree now bind the same set, asserted as an invariant
  in the suite. Enumerating declared paths survives only as the fallback for a
  plan built with no snapshot at all; that table is derived from the manifest
  models rather than hand-kept, so a new artifact list — or a whole new framework
  block — is covered without editing it.
  Capture also has to supply the *bytes*, not just the path list. Recording what
  was read and then reopening those files to hash them takes the two halves of
  the plan from two different instants: a file rewritten in between is attested
  at its new content while `tool_sources` still lists what the old content
  pointed at, so the receipt describes bytes the scan never evaluated. Plan
  construction now runs under the finalized snapshot on both paths. That makes
  binding an obligation rather than an optimization: under an active snapshot
  both `_blobs` and `_optional_blob` read a path that is *contained but never
  read* as absent, so an input nobody bound does not merely go unhashed — it
  disappears from the plan. Every input the plan hashes is therefore bound
  before the snapshot is sealed: the adapters' own reads, the changed files (a
  README no adapter opens would otherwise vanish from `changed_files`), and the
  explicit `--baseline`, `--diff-from`, and policy packs, with the comparison
  report bound as an external input on a committed-tree preparation because it
  is never mapped into the evaluated tree.
  The manifest itself is now read once, through the snapshot, and parsed from
  those bytes. `load_manifest_with_positions` otherwise reads it twice — a
  direct `Path.read_text` for the model, then the snapshot for positions — so a
  rewrite between the two let the adapters follow one manifest while the plan's
  config blob attested to another: a receipt could name an entrypoint the scan
  never opened. The worktree path always passed its captured text for this
  reason; the committed-tree path and `verification prepare` passed none.
  A committed-tree run therefore has two snapshots alive, and each external
  input must belong to exactly one of them: the worktree snapshot binds the
  baseline, policy packs, and comparison report *before* the archived scan
  starts, so its tamper check still covers them — and covers a wider window than
  before, since it now begins before the scan rather than at the scan's first
  read. Letting both snapshots watch the same external directory instead makes
  the second re-validation fail on a change the first legitimately allowed.
  Two behavior changes worth knowing. `verification prepare` reads inputs now,
  so it fails on a manifest whose inputs cannot be loaded; that is the same
  condition under which `verify` fails, and exactly when a prepared plan could
  not honestly claim an input set. It also routes its errors instead of printing
  a traceback, through the shared diagnostic catalog rather than a local guess:
  an absent manifest gets the setup route (`config_error`, exit 2, the same
  answer `scan` gives), an unparseable one gets the edit route, and an input
  that moved mid-run gets `input_parse_error`, exit 3 — the distinction matters
  because agents branch on it. And the declared-path fallback rejects a path
  resolving outside the verification input root, since it cannot be hashed
  portably.
  No schema changes: `plan.inputs.tool_sources` gains entries, not fields.
  Existing `input_set_id` and `request_id` values do move — which is the point,
  and means a receipt minted before this change cannot be compared by ID against
  one minted after.

- **`SHIP-VERIFY-POLICY-WEAKENED` can now actually see a weakened CI gate.**
  The `effective_policy` snapshot was built from the manifest *after* CLI
  overrides were folded into it, so it described the invocation rather than
  the repository — and `verify` overrides both sides, differently. The base
  tree is scanned with a forced `ci_mode="advisory"` (load-bearing: it keeps
  a base scan from failing the run on the base's own findings), so every
  cached base report recorded `effective_policy.ci_mode == "advisory"` no
  matter what the base declared. Comparing that against the head manifest
  made `head_rank == base_rank` for the one case the check exists to catch:
  a PR downgrading `ci.mode` from `strict` to `advisory` emitted nothing.
  The flagship claim is that an agent cannot quietly weaken its own gate,
  and on this axis the specific finding that names the weakening never
  fired. (Such a PR still routed to a human through
  `SHIP-VERIFY-TRUST-ROOT-TOUCHED`, so this was a missing name on a real
  review, not a silent merge.) The same root cause ran the other way on
  `fail_on`, where only the head carries the override: `verify --fail-on
  high` against a manifest declaring `fail_on: [high, critical]` compared a
  head snapshot of `["high"]` against a base snapshot of `["high",
  "critical"]` and reported a **high**-severity "this PR removes severities
  from the CI fail-on set" against a PR that touched no policy at all.
  The snapshot is now built from the `ci` block as declared on disk, which
  fixes both directions at once — the defect was never "the base is forced
  to advisory", it was "the snapshot describes the invocation". The run's
  own gate is unchanged: top-level `report.ci_mode` / `report.fail_on`, the
  exit code, and `run_id` all still reflect the overrides. Fixing the
  producer alone would have left the bug observable, because a cached base
  report is admitted on a content hash — which proves it was not tampered
  with and says nothing about whether its fields still mean what the current
  CLI expects. `__version__` is in the cache key but does not move for a
  source checkout, an editable install, or between two builds sharing a
  pre-release version string, so a base report written before this change
  was reused verbatim and kept reporting `advisory` for a base that declared
  `strict`. The cache-key epoch is therefore bumped (`BASE_CACHE_KEY_EPOCH`,
  2 -> 3), which strands those entries on a key nothing computes; upgrading
  costs one re-scan per base tree and needs no manual cache clearing.
  Unit coverage had injected
  the base `EffectivePolicy` directly and could not see how the snapshot was
  produced, so the regressions drive real base and head scans through
  `verify --base` in both directions, backed by a structural test that
  replays every CLI override `_prepare_scan` supports and asserts the
  snapshot is byte-identical to the on-disk manifest's.

- **`init` no longer fails on a repository that names two files the same.**
  A generated `tool_sources[].id` was the source type plus the file's
  basename, so `strix/tools/finish/tool.py`, `strix/tools/respond/tool.py`,
  and `strix/tools/load_skill/tool.py` all rendered as `openai_sdk_tool` —
  and the manifest schema, correctly, rejects a manifest whose ids repeat.
  One `tool.py` per tool package is the conventional Python layout, not an
  edge case, so on those repositories the primary adoption path failed
  outright: `init --write` exited 4 with `internal_error` and wrote nothing,
  and the fallback it routed to (`--minimal`) discards the detection work for
  a `CHANGE_ME` template. Worse, `--minimal` had the same rule with no
  validation gate in front of it — two services that each ship
  `openapi.yaml` got an invalid manifest written to disk, and the documented
  next step (`scan`) failed on it with a config error telling the user not to
  re-run `init`. Both renderers now derive the id from the whole
  workspace-relative path (`openai_sdk_strix_tools_finish_tool`), with no
  positional component — a `_2` suffix would have renumbered existing entries
  whenever an unrelated file appeared earlier in the walk. Sanitizing is
  lossy, so paths that still fold to one id (`a-b/` and `a_b/`) each take a
  digest of their own path rather than one side keeping the plain form; a
  collision is the one case where adding a file changes an id that already
  existed, nothing outside it is re-keyed, and `init` refuses to overwrite an
  existing manifest, so ids are assigned once per adoption. A digest prefix
  is not treated as a unique key either — two paths in one sanitized class
  sharing an 8-hex prefix are searchable in seconds — so whatever is still
  tied moves to a wider digest and the rendered set is unique by
  construction. A deep monorepo path keeps its most specific segments plus a
  digest, and the 64-character bound is enforced on the value that ships,
  disambiguated ones included. Verified end to end on the
  repository from the report: 23 sources, 23 unique ids, `init --write` →
  `scan` exits 0.
  Nested sources declared by `--minimal` change id (they had no valid id
  before); ids in an existing `shipgate.yaml` are untouched — `init` refuses
  to overwrite one.

- **A protected-surface stop now names the route, and the non-route that looks
  like one.** A human-routed preflight signal said only that a coding agent must
  not self-approve the edit. That leaves the agent to guess how a human decides,
  and the plausible guess is to ask the operator in conversation — which
  preflight does not read. The agent then either stalls on an answer nothing
  consumes, or treats a spoken "yes" as authority and proceeds past the gate;
  the first wastes a human context switch on a reviewer-requested edit, and the
  second is the gate teaching the behaviour it exists to prevent. The
  recommendation now states both halves: approval goes through the pull request,
  and the agent must not ask the operator to approve the edit in chat.
  This is text on an existing signal — no check id, schema, or routing changed.
  A trust-root edit is still `critical` and still stops the turn.

- **A first adoption no longer reads as a policy weakening.** Adding the
  manifest to a repository that had none is the first verdict every new adopter
  sees, and it said "This PR weakens the release policy that evaluates it",
  carried a finding titled "Policy change cannot be proven safe (no base
  snapshot)", and — because a missing-manifest base was classified as a safe
  recovery — shipped no `fix_task` at all, so nothing named the act that would
  clear it. `verify` now proves adoption from git (the comparison base carries
  no manifest under any name and no YAML that *parses* as one — a text probe
  missed a valid manifest with quoted keys — so neither a *moved* manifest nor
  a base that quietly keeps one can pass itself off as a first adoption) and says so: same check id, same `medium` severity, same
  `human_review_required` state, new evidence kind `manifest_introduced`, and a
  `fix_task` whose leading instruction names the exact configured manifest.
  Only when adoption is the sole gating concern does that instruction say to
  merge the adoption through a human-reviewed PR; blockers, insufficient
  evidence, and additional review items lead with their own stop condition.
  `check` gets the same correction locally, keyed on the diff carrying exactly
  one manifest record and that record being a plain addition. Adoption remains
  a human decision — only the claim about what happened changed.
- **The manifest a run actually loaded is a trust root.** The trust-root table
  only knew `**/shipgate.yaml`, so a repository run with `--config new-gate.yml`
  had no manifest trust root at all: the file defining its gate could be
  introduced or rewritten without a single finding, leaving the release
  substrate empty. With a clean scan that produced `passed` / `mergeable` /
  `complete` — beneath an adoption headline that said a human was required.
  Whatever a run loads as its gate is now classified as one, in both the
  trust-root check and the policy fail-safe — and in `check` and `preflight`,
  which classified it no better: a diff for a custom-named manifest returned
  `allow` with no violations locally and no protected touch in preflight — the
  local check dropped it from the diff entirely before any evaluator saw it —
  and both then recommended a verify command for the default `shipgate.yaml`.
  Identity is compared on normalized, containment-checked paths, so an
  equivalent spelling (`docs/x/../manifest.yaml`) cannot slip past, and
  preflight classifies the *source* side of a rename, which is where the gate
  sits when a diff moves it out from under itself. The
  classification is recorded on the boundary row so a gate-governing surface
  stays out of the graded agent route regardless of its name, and the evidence
  carries the changed path rather than the resolved config path, which for a
  committed-head run is a temporary archive location that would make two
  identical runs produce different fingerprints.
- **`check` detects which agent is running it.** `--agent` defaulted to `codex`
  and never consulted the harness variables Shipgate already reads to switch on
  agent mode, so every Claude Code and Cursor run recorded the wrong actor in
  its result and audit id. Detection now comes from one table that also defines
  those hints, so the two cannot drift; an explicit `--agent` still wins, and a
  plain shell still gets `codex`.
- **`check`, `audit`, and `preflight` honor the agent-mode error contract.**
  The skills and slash command tell agents that with
  `AGENTS_SHIPGATE_AGENT_MODE=1` a failing command emits a structured
  `next_action` line on stderr; these three printed prose only, so an agent that
  mis-invoked them had to parse English. Each error path now emits the line with
  its `exit_code`, and `preflight` no longer lets an unexpected failure escape
  as a bare traceback.
- **A rerun command that actually reruns.** The `fix_task` verification command
  omitted `--config`, substituted `origin/main` for a base the run never used,
  and always appended `--head HEAD`. In a repository with a nested manifest it
  re-ran against a different gate, and for an uncommitted first adoption it
  switched to the committed tree — where the new manifest does not exist — and
  exited 2. It now emits the config always, the base only when one was used,
  and no `--head` for a working-tree run — plus the rest of the evaluated
  request (policy packs, baseline, `--ci-mode`, plugin and heuristic modes, and
  an explicit `--no-base`), because a rerun that drops them evaluates a
  different question than the one whose findings it is meant to reproduce. The
  structured adoption repair now names the resolved config path instead of a
  hardcoded `shipgate.yaml`, and the command carries the resolved `--workspace`
  and a non-default `--out`, so a rerun from another directory evaluates the
  same checkout and writes to the same place. `check`'s recovery command is
  rebuilt from the failing request with only the invalid field corrected —
  a fixed command discarded actor, workspace, config, policy, and diff context
  — and a request whose diff came from stdin gets a review action instead of a
  command that cannot be replayed.
- **Preflight recovery keeps the request it failed on.** Every preflight error
  recommended a bare `agents-shipgate preflight --json`, discarding workspace,
  config, plan, diff, and capability request: following it after a failed
  targeted run evaluated the current repository with an empty plan and returned
  `control.state=complete`. The recovery action now reproduces the actual
  invocation, and offers no command at all when the request came from stdin or
  mixed `--plan` with the per-flag inputs — replaying a request-shape conflict
  can never satisfy its own `expects`. `check`'s recovery is one quoted
  serializer for every path, including diff-input failures, which previously
  joined user-controlled paths and refs unquoted into a published *authorized*
  command; commands the CLI emits for its own targets now name the workspace
  and the manifest, with the config rendered the way `verify` resolves it —
  relative to the repository root — so a nested manifest is no longer verified
  against the root gate.
- **Detached diffs never authorize checkout-dependent verification.** A diff
  supplied by file, stdin, or the read-only MCP adapter can be evaluated for
  diagnostics, but it is not proof of the bytes a later `verify` command would
  read. When such a result owes verification, control now stops with no
  allowed command and the summary says to rerun against the intended worktree
  or a complete ref range. MCP preflight also rejects a `plan` mixed with
  direct request fields instead of silently discarding one input source.
- **A failed baseline is never recovered by overwriting it.** A malformed,
  unknown-schema, or integrity-failed host-grants baseline recommended
  `--save-baseline` against the same path, which replaced the failed artifact
  with the *current* grants — acknowledging them unreviewed and destroying the
  evidence a human needed. Those now route to review. A genuinely absent
  baseline also routes to a human because creating the first baseline
  acknowledges the current grants; a failed read-only drift request never
  authorizes that state-changing decision.
- **Host-audit filesystem failures follow the catalog.** A `--baseline-file`
  naming a directory raised `IsADirectoryError` through typer as a traceback
  and exit 1. Filesystem failures on both `--baseline-file` and `--out` are now
  `other_error` with exit 4, as `docs/errors.json` specifies — they were
  briefly reported as `config_error`/2, which sends an agent back to re-read
  flags that were fine. The baseline *reader* needed the same treatment through
  its `__cause__`, since the loader converts every read `OSError` into
  `ValueError`; a genuinely missing baseline remains `config_error`/2 but now
  carries a review-only route for the first acknowledgement.
- **The audit id distinguishes the actor.** Detecting the calling agent changed
  the label in the result but not the digest — the central one omitted the
  actor and the legacy one hardcoded `codex` — so identical evaluations by
  Claude Code, Cursor, and Codex shared an `audit_id`, which is exactly the
  attribution problem actor detection exists to solve. Legacy replayable
  provided-diff Codex ids keep their established shape. Non-default actors add
  actor identity; worktree, ref-range, and detached evaluations also bind
  input/control replayability, so those ids intentionally rotate. Semantic
  control state is hashed without checkout-specific command paths.
- **Static control inputs now fail closed on identity and resource ambiguity.**
  Local check, preflight, host audit, installed hooks, and verifier Git
  collection bind exact non-symlink, singly-linked regular files; manifest,
  policy, baseline, trust-root, diff, and Git inventories have byte or entry
  ceilings. Executable filters, repository diff drivers, hidden index flags,
  source-like binary diffs, malformed/coherence-breaking diff records, and
  filesystem-portability collisions stop instead of silently dropping source
  text. Prior verifier output is excluded from a worktree request so an
  identical rerun does not hash its own artifacts.
- **Portable host instructions are protected consistently.** Boundary
  matching is case-insensitive and hierarchical for `AGENTS.md`,
  `AGENTS.override.md`, and `CLAUDE.md`; a case variant or nested copy cannot
  acquire authority only after checkout on another host. Symlink directories
  that could conceal a leading-`**/` trust root make host inventory and
  preflight incomplete and route to human review.
- **Mechanical repair authorization is subject-bound.** A coding-agent repair
  route now requires an applicable high-confidence non-manual patch against a
  worktree subject. A ref-bound verifier cannot authorize a patch command that
  would edit the checkout and then rerun the unchanged commit; it routes that
  repair to a human instead.
- **Adoption wording stands down when something was genuinely weakened.**
  Introducing the manifest while editing an existing policy file produced a
  `base_snapshot_unavailable` finding under a headline saying there was no
  prior gate to weaken, and dropped the `review_policy_weakening` repair. The
  pure-adoption wording now requires that nothing else needing review changed.
  The adoption proof itself also stopped resting on two basenames: a base that
  simply keeps an operational manifest under another name deletes nothing and
  matches no name check, so absence is now established by content.

- **A way out of `insufficient_evidence` (#292).** An abstention was
  unactionable in practice: the decision engine generated the exact manifest
  snippet each evidence gap wants, but those snippets were only reachable by
  walking `report.json`, so a three-line, one-time task looked like schema
  archaeology and repositories stayed abstained indefinitely.
  `suggested-declarations.yaml` now assembles them next to the report — merged
  per target, so two gaps on one tool produce one pasteable row instead of two
  invalid ones — and every gap that carries a template names the file in its
  `expects`. Every human-owned value stays `<REVIEW_REQUIRED>`, and the file
  states that a block still containing a sentinel closes nothing. Verified end
  to end: filling the scaffold clears `inferred_effect_only` and
  `missing_authority_evidence` and moves the verdict off abstention.
- **Unfilled scaffold placeholders are rejected by the manifest.** The scaffold
  states that a block still containing `<REVIEW_REQUIRED>` closes nothing, but
  the manifest only checked fields like `authority.auth_type` for
  non-blankness — so a pasted-but-unfinished block loaded and was assessed as
  reviewed evidence, moving a fixture from `insufficient_evidence` to
  `review_required` on placeholders alone. The loader now rejects the sentinel
  wherever it appears and names each unfilled path, so an unfinished scaffold
  cannot change a verdict.
- **The authority template was unfillable.** It offered `authority.mode`
  alone, but the manifest requires `auth_type` for every mode except `none`
  and non-empty `scopes` for `scoped` — a reviewer following it exactly got a
  config error. Surfacing the templates is what exposed it. The template now
  names the co-required fields, and a regression test validates the shipped
  shape against the manifest schema.
- **Evidence gaps say whether this diff caused them.** `verify` already scans
  the base; it now compares the base and head gap sets and reports whether the
  diff introduced a gap or inherited it, so a docs-only turn stops reading as
  an accusation about the current change. The verdict is deliberately
  unchanged: evidence coverage is a property of the whole evaluated surface,
  and a diff that appears to touch nothing is exactly what an unseeable
  capability change looks like, so the diff can never argue an abstention away.
  `docs/engineering/insufficient-evidence-cold-start.md` records why the
  diff-scoped variant was rejected.
- **Framework-correct low-confidence remedy.** The advice named
  `tool_inventories` for every framework, but only four have that key;
  `openai_agents_sdk` — the quickstart framework — has none, so readers were
  sent after a key the schema rejects. The remedy now names the real key when
  one exists and the supported alternative when it does not.

- **Graded local boundary stop (UX P0, contract v19, `0.16.0b7`).** The
  local `shipgate check` previously projected every `require_review`
  boundary violation onto the same `human_review_required` +
  `must_stop: true` control state as a `block` — a CLAUDE.md comment, an
  unknown `.claude/settings.json` key, and a critical grant expansion were
  operationally identical, which routinely hard-stopped coding agents on
  user-requested benign edits. Contract v19 routes a `require_review` set
  that is entirely low/medium risk to
  `control.state: "agent_action_required"` with the exact verify command;
  the review obligation is preserved in the new additive `pending_review[]`
  field on `shipgate.agent_boundary_result/v1` and re-asserted by PR-time
  verify, whose `release_decision` branching is byte-identical. The band is
  fail-closed: `block` actions, `critical` risk, incomplete or unparseable
  input, gate-weakening rules, experimental surfaces, and every
  gate-governing trust-root class (`manifest`, `policy`, `ci_gate`,
  `shipgate_state`) keep the human stop. Root/case-variant
  `AGENTS.md`/`AGENTS.override.md`/`CLAUDE.md` instruction identities do too,
  preserving the composite-diff guarantee from the agent-authored proposal
  work. The deprecated
  `codex-boundary-json` format grades identically; its frozen v2 schema
  does not carry the new field.
- **Stop hook follows `control.state` (`0.16.0b7`).** The installed Claude
  Code Stop hook blocked the agent's stop on any non-`passed` release
  decision — but a Stop-hook block forces the agent to KEEP working, which
  is exactly wrong for `human_review_required` (`must_stop: true` means
  "end the turn and hand off to a human"). The hook now mirrors the
  operational contract: `complete` ends the turn silently,
  `agent_action_required` blocks once and names the one exact remaining
  command, `human_review_required` prints a hand-off notice and lets the
  turn end. Unparseable or unrecognized verifier output warns loudly, is
  never cached by the verified-signature short-circuit, and is never
  treated as passing; the cold-start no-manifest case advises
  `verify --preview` instead of forcing continuation. Reinstall hooks to
  pick up the new behavior.
- **Own-repo CI verify gates on `blocked,unknown` again (`0.16.0b7`).** The
  0.16.0b3-era expansion of `fail_on_merge_verdicts` to include
  `human_review_required` and `insufficient_evidence` (#274) made every
  trust-root-touching PR — including routine release pin sweeps that bump the
  plugin manifests — permanently red: no verifier mechanism can clear a
  `human_review_required` merge verdict, and the verdict's own semantics are
  "release is allowed but the human reviewer should weigh in." Both advisory
  workflows now fail only on `blocked` and `unknown` (fail-closed against
  parse/contract breakage); review routing remains visible in the uploaded
  verifier artifact and the PR reviewer stays the deciding human. The
  GitHub Action's own defaults are unchanged.
- **Version advances (`0.16.0b7`).** Runtime contract `18 → 19`. All other
  schema versions are unchanged; `pending_review[]` is additive on the
  regenerated `agent-boundary-result-schema.v1.json`, and
  `minimum_control_contract_version` stays `14` — the `AgentControl` union,
  its fixed `must_stop`/`completion_allowed` literals, and the release
  gating signal are untouched.
- **Reproducible verification identity (P0, `0.16.0b6`).** Verify now binds
  the resolved Git subject, exact input blobs, evaluation date, behavior
  options, installed engine-content and dependency/adapter/plugin/policy set, normalized task,
  executor, assembled decision, and complete artifact set through
  content-addressed IDs. Exact Git objects are materialized with `ls-tree` and
  `cat-file`; base-cache reuse is invisible to public artifacts and guarded by
  a content hash.
- **Terminal receipts and portable execution boundary.** Successful verify runs emit
  `verification-plan.json`, a decision-free `verification-unit-result.json`,
  `verification-artifacts.json`, and, last, `verification-receipt.json`.
  Workers validate their installed engine and transported inputs but cannot
  assert a verdict; the verifier remains the sole policy engine and the
  assembler re-closes its decision. `verification
  prepare|worker|assemble|reproduce` exposes the portable v1 protocol without
  claiming distributed policy evaluation, arbitrary sharding, or parallel
  speedup.
- **Externally rooted exact-operation authorization (contract v18).** A trusted
  host can derive an unsigned authorization request from a host-attested
  `review_required` receipt, authenticate a human, and return a short-lived
  Ed25519 grant for one exact force-with-lease Git push. Agents Shipgate ships
  no signing or approval command. A second verification recomputes the complete
  request, decision, review set, repository, and tree identities before an
  accepted grant exposes only the guarded `authorization execute` consumer;
  the release decision, merge verdict, and completion authority remain
  unchanged. The executor revalidates current evidence and expiry immediately
  before using an isolated Git object store. Authorization requires an exact
  plugins-disabled engine, rejects third-party plugin loading in the broker,
  and parent-streams the Git pack with bounded stdout, stderr, and time.
  Authorization remains disabled
  without a host-protected trust policy, launcher, interpreter, entire virtual
  environment and `site-packages` tree, dependencies, credentials, and
  separately installed distribution; same-UID modes and editable workspace
  installs are not a trust boundary.
- **Identity and authorization contract versions.** Runtime contract advances
  to v18; report to v0.34; packet to v0.12; verifier to v0.6; verify-run to v3;
  handoff to v6; attestation to v0.5; registry to v0.4; organization evidence
  bundle to v2;
  downstream local contract to v7; and safety qualification formats to v4.
  Verification plan, unit-result, artifact-manifest, and receipt schemas begin
  at v1. The authorization request, signed grant, verifier evaluation, and
  external trust-policy schemas also begin at v1. Prior schemas remain frozen
  readers.
- **Immutable CI subject.** The GitHub Action evaluates `github.sha` by default
  and treats the default pull-request synthetic merge as authorization-
  ineligible. Push authorization requires a separate verification of the exact
  PR head commit. The Action exports receipt, request, decision, and
  artifact-set identities only after validating every terminal artifact hash.
- **Non-forgeable trust decay.** The content-bound commit evaluation date
  remains reproducibility provenance, but cannot extend reviewer-owned trust.
  Baseline, acknowledgement, and severity-override expiry use the later of that
  date and the verifier wall clock, so a forged backdated commit fails closed.
- **Agent-authored coverage proposals (contract v18 clarification).** Preflight
  and local control now distinguish proposal authorship from approval for one
  narrow manifest shape: an exact append-only addition of valid built-in
  `tool_sources` rows may be authored by a coding agent and routed to verify.
  Existing rows, all other manifest values, authority-bearing fields, custom
  adapters, and unsafe paths remain human-routed; the concrete trust-root diff
  still requires reviewer approval. Conventional test/golden fixtures are no
  longer inferred as undeclared deployed surfaces unless the manifest
  explicitly declares them. No schema or runtime-contract version changes.
- **Codex marketplace coverage and plugin-path containment.** Local plugin
  packages reached through a declared Codex marketplace now count as declared
  tool surfaces for local-control routing, and detect/init plus the zero-install
  detector no longer propose redundant direct-package rows for those roots.
  Direct-package loading now hard-rejects a source whose
  `.codex-plugin/plugin.json` symlink target escapes the manifest directory;
  marketplace entries with the same escape are skipped and cannot grant
  coverage or supply verification bytes. Malformed, non-UTF-8, oversized,
  remote, or escaping marketplace inputs stay fail-closed. No schema or
  runtime-contract version changes.

- **Evidence-basis policy gate (P0, `0.16.0b5`).** Semantic claims and risk
  hints now carry a typed evidence basis, stable claim IDs, and derived policy
  eligibility. Policy-pack and action-policy predicates evaluate to
  `matched | not_matched | indeterminate | conflicting`; rule severity,
  confidence, `block: true`, manual tags, and risk overrides cannot upgrade
  heuristic or incomplete evidence into an authoritative finding.
- **Non-waivable policy applicability gaps.** Heuristic-only, mixed, unknown,
  or conflicting applicability is emitted outside Findings and routes to
  `insufficient_evidence`. Baselines, suppressions, severity overrides,
  acknowledgements, and `--no-heuristics` cannot hide it. Supported findings
  expose deterministic predicate support and a `support_hash`; baseline v0.8
  requires that hash to remain equal. Pre-v0.8 baselines cannot supply that
  binding, so supported findings re-gate as new until a human reviews the new
  evidence and re-runs `agents-shipgate baseline save`.
- **Evidence contract versions.** Runtime contract advances to v16; report to
  v0.33; packet to v0.11; verifier to v0.4; handoff to v4; policy pack to
  v0.4; capability standard to v0.5; lock/diff to v0.6/v0.7; action snapshot
  to v0.4; downstream local contract to v5; and safety qualification formats
  to v3. Existing finding fingerprints and all prior schema files remain
  frozen.

- **Complete zero-config multi-host boundary (`0.16.0b4`).** The local
  boundary check now evaluates every recognized changed Codex, Claude Code,
  Cursor, VS Code MCP, shared instruction, and GitHub workflow surface through
  one static assessment. `--agent` identifies the caller and can no longer be
  used as a coverage selector. Untracked, deleted, malformed, unreadable,
  symlinked, and oversized protected inputs fail closed instead of disappearing
  from the result.
- **Host-neutral boundary contract.** Runtime contract advances to v15 and the
  canonical check format becomes `agent-boundary-json` with schema
  `shipgate.agent_boundary_result/v1`. The frozen
  `shipgate.codex_boundary_result/v2` projection remains available through the
  deprecated `codex-boundary-json` spelling for the `0.16.x` line. The shared
  `AgentControl` contract remains v14.
- **Evidence-bearing host inventory.** Host inventory, baseline, and drift
  advance to v0.2 with typed redacted grants, artifact parse status, per-host
  coverage, explicit excluded scopes, and incomparable migration for v0.1
  baselines. Repository scope remains deterministic and default; the explicit
  `local-static` audit scope reads supported local configuration without
  executing hosts, helpers, tools, user code, or network calls.
- **Boundary beta hardening.** Visible permission-mode and sandbox grant values
  now use the same recursive secret redaction as their hashes, so inventories,
  saved baselines, and drift reports cannot persist raw credential-bearing
  strings. Incomparable host baselines route preflight and organization status
  to human review instead of appearing clean. Protected-path classification is
  case-insensitive and retains nested Codex, MCP, and GitHub-workflow copies.
- **Correction to the original host-governance claim.** Earlier documentation
  overstated the first host-audit cut as the effective/current grant set. The
  contract is static and scope-bound: repository results cover
  repository-declared surfaces, while local-static results still exclude
  session approvals, invocation flags, UI state, remote managed settings, and
  runtime enforcement.

- **Unambiguous agent control contract (P0, `0.16.0b3`).** Check, preflight,
  verify, handoff, MCP, verify-run, and GitHub Action projections now share one
  schema-enforced `AgentControl` state: `complete`, `agent_action_required`, or
  `human_review_required`. Pending verification, installation, safe repair, and
  input recovery no longer coexist with completion or a human stop;
  conversation-level acknowledgement cannot clear a control obligation.
- **Control contract versions.** Runtime contract advances to v14; boundary
  result to `shipgate.codex_boundary_result/v2`; verifier to v0.3; handoff to
  `shipgate.agent_handoff/v3`; preflight to v0.3; verify-run to
  `shipgate.verify_run/v2`; and the downstream local contract to schema v3.
  Prior schema files remain frozen. Report v0.32, packet v0.10, capability
  standard v0.4, and capability lock/diff v0.5/v0.6 are unchanged by this
  control-contract milestone.
- **Execution, applicability, and mergeability are separate.** Verifier v0.3
  publishes execution (`not_run | succeeded | skipped | failed`) separately
  from applicability (`not_evaluated | verified | not_applicable | failed`).
  `can_merge_without_human` is true only for a verified `passed` result or a
  completed deterministic non-applicable skip. GitHub Action outputs add
  `agent_control_state` and `agent_control_reason`; legacy booleans remain exact
  derived mirrors for one compatibility cycle.

- **Conductor OSS workflow JSON adapter.** A built-in, per-scan `conductor`
  source statically enumerates literal MCP calls and records MCP discovery,
  LLM tool advertisements, HUMAN checkpoints, nested control-flow tasks, and
  local sub-workflows. Dynamic or unresolved tool surfaces emit
  `SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE`; unsupported worker,
  HTTP, A2A, provider-native, and runtime-generated capabilities remain
  explicit evidence gaps. Report schema advances from the frozen binding
  contract v0.31 to v0.32; manifest v0.1, packet v0.10, and runtime contract
  v13 remain unchanged. `conductor` is now a reserved built-in
  `tool_sources[].type` and may conflict with a third-party adapter that
  previously used the same source type.
- **Root-reachable agent binding graph (P0, `0.16.0b2`).** Tool catalogs no
  longer become an agent's capability surface by extraction alone. Framework
  adapters emit static tool and handoff edges, `agent_bindings` supports exact
  reviewed closed-world declarations, and partial, dynamic, ambiguous, or
  conflicting graphs prevent `passed`. Reports separate `tool_catalog[]` from
  root-reachable `tool_inventory[]` and publish binding facts, diffs, coverage,
  evidence gaps, and human-routed remediation.
- **Binding contract versions.** Runtime contract advances to v13; report to
  v0.31; packet to v0.10; capability standard to v0.4; capability lock/diff to
  v0.5/v0.6; action snapshot to v0.3; and safety qualification formats to v2.

- **Provider-scoped canonical tool identity (P0).** Tool observations now get
  deterministic source-scoped IDs and same-name tools from different
  providers remain distinct. Cross-source evidence joins only through exact,
  reviewed `tool_identity.bindings[]`; invalid bindings and ambiguous
  one-to-one selectors apply nowhere and route to `insufficient_evidence`.
- **Identity-safe policies, diffs, traces, and debt.** Action declarations,
  controls, risk overrides, suppressions, packets, tool/action diffs,
  capability lineage, trace matching, and finding fingerprints consume the
  canonical tool identity. Fingerprint v2 hashes `tool_id`; legacy baseline
  matches are accepted only for an unambiguous current identity. Pre-v0.30
  reports and pre-v0.4 capability locks must be regenerated before diffing.
- **Identity contract versions.** Runtime contract advances to v12; report to
  v0.30; packet to v0.9; capability standard to v0.3; capability lock/diff to
  v0.4/v0.5; policy pack to v0.3; verifier to v0.2; action snapshot to v0.2;
  and agent handoff to `shipgate.agent_handoff/v2`.

- **Evidence-backed `passed` verdict (`0.16.0b1`).** `passed` now requires
  complete, conflict-free static surface, effect, and authority evidence for
  every in-scope action, evaluation of all applicable controls, and no policy
  condition requiring review. Unknown, inferred-only, protocol-defaulted,
  partial, invalid, or conflicting semantics route to
  `insufficient_evidence`; known ambient/unscoped authority routes to review.
  Semantic gaps are not Findings and cannot be suppressed or baselined. This
  remains a static claim, not proof of runtime behavior or enforcement.
- **Normalized semantic evidence contract.** Report schema v0.29 and packet
  schema v0.8 add per-action/per-capability assessments, semantic coverage,
  and typed human-routed gap remediation. Manifest action declarations can
  provide reviewed `effect` and `authority` evidence; Agents Shipgate never
  auto-writes those assertions. Contract v11 exposes this boundary as
  `do_not_auto_assert: [action_effect, action_authority, ...]`.
- **Machine-readable static-verdict boundary.** Report release decisions,
  verifier artifacts, and agent-handoff gates now expose
  `static_analysis_only: true`, `runtime_behavior_verified: false`, and the
  canonical `static_verdict_disclaimer`; packet v0.8 §1 mirrors the report.
- **Capability standard v0.2.** Capability lock schema advances to v0.3 and
  diff schema to v0.4 so static capability facts carry the normalized semantic
  assessment. Runtime contract advances to v11; the source-tree package is
  `0.16.0b1` while install examples remain pinned to the latest published tag,
  `v0.15.0`, until the beta is released.
- **Qualification trust boundary is explicit.** Beta promotion verifies an
  internally consistent production-qualification summary, its configured
  Sigstore identity, and exact wheel/tag binding. Organizational signer
  independence, blind labeling, receipt replay, and the four-week,
  three-design-partner rollout remain governed external controls rather than
  guarantees made by the promotion code. The machine policy enforces a
  combined minimum of 40 real-history, rejected/reverted, or design-partner
  origins.

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
