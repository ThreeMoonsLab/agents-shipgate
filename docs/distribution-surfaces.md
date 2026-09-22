# Distribution surfaces

One engine is published through many surfaces. This is the list of them, what
each one *claims*, and which test proves the claim.

A surface that is not on this list is a surface nobody is checking. That was the
state most of them were in when [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497)
was filed, and [#485](https://github.com/ThreeMoonsLab/agents-shipgate/issues/485)
is what it costs: after [#431](https://github.com/ThreeMoonsLab/agents-shipgate/issues/431)
taught the CLI to read an MCP server's tool surface out of its source,
`tools/shipgate-detect.py` — the documented zero-install front door — went on
answering `is_agent_project: false` for the vendor MCP servers the CLI now
reports as agent projects, and CI stayed green. This is the "second
implementation" class ([#322](https://github.com/ThreeMoonsLab/agents-shipgate/issues/322))
at the distribution layer.

## The invariant

> **Every surface that answers a question the engine also answers must give the
> engine's answer, or say here what it does not answer.**

Two families of question, and both are enforced:

- **Verdict parity.** "Is this an agent project?", "what is the merge verdict?",
  "who owns this declaration?" A surface that restates one of these must restate
  the engine's answer, not re-derive it.
- **Executability.** "Run this command", "use this ref", "you need contract N."
  A surface that tells a reader to execute something must name something that
  resolves *in the build it names*. An ahead-of-release source tree emits a
  resolvable supported path or an explicit incompatibility — never a nonexistent
  tag, an unmarked preview, or a command the named build does not have.

Deriving beats duplicating. A surface that *can* read the answer out of the
package should not carry a second implementation at all. The standalone detector
is the one real exception — being importable-free is its entire value — so it
keeps its own implementation and takes the parity test as its contract.

Host applicability is part of `agent_project_verdict`: both discovery paths
publish the same `host_boundary_candidates` and
`host_discovery_incomplete_paths`. Candidates mean recognized config filenames,
including ignored settings; they do not mean parsed grants. Paths the census
could not see through inhibit a complete negative without proving a host
exists, and without refusing the rest of the classification. The dedicated
`tests/test_host_discovery.py` corpus also exercises root/nested registry
predicates, invalid input types, no-write setup and bounded recovery. The
committed parity corpus cannot hold a host-only workspace — a `.mcp.json`
under `tests/` would be a candidate of *this* repository — so
`test_detector_verdict_matches_cli_on_host_only_shapes` builds the config,
config-directory and unreadable-directory shapes in a temporary tree and runs
them through the same comparator, which keeps the two host rows from agreeing
by absence. The zero-install script remains metadata-only and emits no control
authority.

## Claims vocabulary

Every claim in the registry is one of these. The vocabulary is closed; the code
and this document are checked against each other by
`tests/test_distribution_surface_parity.py`.

| Claim | Means | Engine source of truth |
| --- | --- | --- |
| `agent_project_verdict` | Answers "is this an agent project", and with which sources | `agents_shipgate.cli.discovery.detect_workspace` |
| `merge_verdict_vocabulary` | Enumerates the merge verdicts a caller can gate on, or compares against one | `agents_shipgate.schemas.contract.MERGE_VERDICTS` |
| `release_decision_vocabulary` | Enumerates the release-gate decisions | `agents_shipgate.schemas.contract.RELEASE_DECISIONS` |
| `placeholder_ownership` | Tells a reader who may fill a manifest placeholder | `agents_shipgate.cli.discovery.placeholders.placeholder_owner` |
| `executable_pin` | Names a version, tag or ref a reader will install or run — the Action ref, a `pip`/`uvx` pin, a `>=` install floor, and the Action's own `shipgate_version:` input, which `action.yml` turns into `pip install agents-shipgate==<value>` | `agents_shipgate.published_release.LATEST_PUBLISHED_VERSION`; a stamped candidate's emitted CI uses `agents_shipgate.release_source.candidate_action_ref` |
| `contract_floor` | Names a runtime contract version a reader must reach | `agents_shipgate.published_release.LATEST_PUBLISHED_CONTRACT_VERSION` |
| `report_schema_pin` | Points a reader at the `report-schema.v<X>.json` to validate `report.json` against | `agents_shipgate.schemas.report.ReadinessReport.report_schema_version`, published by `contract --json` |

## The registry

| Surface | Root | Claims | Proven by | Narrower than the CLI in |
| --- | --- | --- | --- | --- |
| `human_review_request` | `docs/human-review-request.md` | `release_decision_vocabulary` | `test_surface_enumerations_match_the_engine_vocabulary` | One complete-evidence documentation-quality class only; no authority or decision ingestion. |
| `human_review_decision` | `docs/human-review-decision.md` | `release_decision_vocabulary` | `test_surface_enumerations_match_the_engine_vocabulary` | Host-neutral read-only evaluator; no GitHub acquisition, persistence or operation authority. |
| `github_action` | `action.yml`, `scripts/github_action_outputs.py` | `merge_verdict_vocabulary` | `test_action_input_enumerates_engine_merge_verdicts`, `test_action_output_script_shares_the_engine_merge_verdicts` | The paired `shipgate_wheel`/`shipgate_wheel_sha256` inputs install a caller-supplied local wheel instead of a published version, so that route names no channel and claims no `executable_pin`; it is refused unless both halves are given, and it installs `--no-deps`. `tests/test_action_engine_install.py` proves the refusals. Every `python` the Action starts in the workspace runs with `-P` or as a script path, so a pull request's `pip/` or `agents_shipgate/` package cannot stand in for pip or the engine; the same file executes the install and merge-verdict steps against such a checkout. The `v1.0.0` tag predates that fix; the published `v1.1.0` carries it. |
| `capability_diff` | `src/agents_shipgate/cli/diff.py`, `src/agents_shipgate/core/capability_diff_rows.py`, `src/agents_shipgate/core/host_comparison.py`, `src/agents_shipgate/report/host_comparison.py`, `src/agents_shipgate/core/unread_inputs.py`, `src/agents_shipgate/cli/verify/changed_inputs.py` | — | — | Answers no question the engine answers: it emits no verdict, no release decision and no pin. Every field is read from the drift payload the engine already produces — `risk` is the engine's severity and `expansion_signals` is the engine's word on widening — so there is no second implementation to drift. A `permission_mode` or `sandbox` row names the setting and its value as the file spells it (`enableAllProjectMcpServers: true`, `defaultMode: dontAsk`), recovered from the grant's published value and digest, and a Claude Code setting's `why` is the basis the engine's one setting table (`core/host_settings.py`) records for the value; that table also rates the grant and `check`'s violation, so a row's severity and the violation's risk give one answer (#827, `tests/test_prompt_disabling_settings.py`). `verify`/PR and `check` reuse the host comparator (#684, `tests/test_manifest_free_pr_rows.py`), and the source name each named reusable-workflow secret refers to, also non-widening, with a redacting name or target refused rather than compared, and an unreadable value neither compared nor named on this surface — only the host inventory and `audit --host` name its `job/destination`, as on `1.0.0` (#693, `tests/test_reusable_workflow_secret_mappings.py`); check retains argument redaction and its existing local-policy control. Missing comparison evidence never supplies empty comparable rows. Host route only; workflow rows compare effective writes and reusable secret recipients (#685, `tests/test_workflow_capability_diff.py`) and each job's remote step action references, as a non-widening change (#771, `tests/test_workflow_step_action_references.py`); every job id, step label, trigger and scope name those rows print is the label the engine published once where it built the grant, redacted, never re-derived here; `check`'s workflow evidence is derived from the raw declarations, which it still compares, and redacts job and scope names by the same rule; two distinct job ids or triggers in one workflow, or scope names in one `permissions` mapping, that publish alike are refused rather than compared, so while such a workflow exists `check` refuses on every run even when it is unchanged (#802, `tests/test_workflow_label_redaction.py`); artifact-only edits remain separate evidence. Tool-source subjects are #655. Where a partial or experimental surface is byte-identical on both sides, `diff` and `verify` compare the rest and name it in `unchanged_limits`; `check` keeps refusing, because its boundary result cannot carry a limit yet (#721). A hook row's `why` states the grant's loading basis, read from its published `source`, `access` and `risk` by the engine's `hook_loading_basis`; only a hook the host loads for this project earns an expansion signal — one a settings layer declares, or one a plugin selects that the repository's project settings enable from an in-repository marketplace — so a declared-only hook, or one a plugin selects without that enablement, is a row and never an expansion, and a removal names no basis (#714). `check` compares without a plugin-reference limit both sides share on an untouched source, which it cannot name and does not route; a limit only one side carries makes its comparison incomparable. A partial clone that never fetched the base's objects is refused as `objects_missing`, exit `2`, never compared and never fetched; the refusal ends with the remediation sentence `verify` reports for the same reason, produced by the same function (#817, `tests/test_capability_diff_partial_clone.py`). The text of `diff`, `verify`, the PR comment and `check` reads the rows through one function, `review_changes`, and adds no row and changes no row value in any JSON projection (#795, `tests/test_host_diff_review_changes.py`): a permission rule is named with its disposition; an MCP server with the command name (never its path) or redacted URL and the env and header key names its grant already publishes, redacted and bounded, a URL printing only in the engine's sanitized scheme-and-host form and otherwise as `url not shown`, or, when none of those differ, a sentence naming what was compared and that the change is in a detail not shown, such as the command's path or arguments; an allow rule the permission lattice decided another replaced (`widened` or `narrowed`), or the exact rule text that moved between dispositions in one host and source (`moved`), is one entry, never on the routes that redact rule arguments; and `diff` counts entries `from N rows` when one joins rows. Comparable results with entries end with one review question, naming the row count when an entry joins rows, and every result whose comparison names a base commit and a commit or working-tree head — a zero-row result and a refusal included (#812 follow-up, `tests/test_host_comparison_coverage.py`) — ends with the compared commits, the tool version and an `agents-shipgate diff --base <sha>` reproduction, labelled `Inputs:` rather than `Compared:` where the comparison was refused, since that run compared nothing — and a refused comparison publishes no `review` object at all, so those two lines are the only place that run states its provenance, built from the `base_commit` it publishes beside the refusal; `check` and a provided diff print the question alone, and no result without a change asks a question. Every one of those facts is published beside the rows, so a machine consumer reads what a human reads (#795 slice 2, same test file): a row adds `disposition`, the `allow`/`ask`/`deny` list a permission rule is declared under and `null` for any other kind, on every route that publishes rows; and `review` in `diff --json` (capability diff `0.3`) and `host_comparison.review` in `verifier.json` (verifier `0.20`) — one object for one comparison — carry the presented changes, each naming the `row_indexes` it stands for, the `direction` the text uses (`widened`, `narrowed` and `moved` included, which no single row can carry), its cells, its `why` and one `expands`, plus a `summary` of `{rows, changes, widenings}` equal to `diff`'s summary line, the review question and the reproduction command. The block is refused unless its changes stand for every published row exactly once, its counters match and no joined change's two sides read alike, so the routes that redact rule arguments publish their rows alone and never a pair that reads `X → X`; `check`'s boundary result carries rows, with their dispositions, and no block. It is presentation, not a second opinion: it is the one `review_changes` projection the text prints, so the rows, their values, their count and every control answer are what they were. A comparison read back from JSON prints the changes it published, and one whose rows a caller sliced falls back to those rows. Each comparison also says what it established (#812, `tests/test_host_comparison_coverage.py`): `coverage` in `diff --json` (capability diff `0.3`) and `host_comparison.coverage` in `verifier.json` (verifier `0.20`) are the same object, printed as `What this run established` by `diff`, `verify` text and the PR comment. It is read off the grant changes, artifact changes, observed sources and blocking issues the comparator already computed: a file's rows, counting a source inside it (`<file>#profiles.<name>`, `<file>#plugins.<name>`); a file with no row and no artifact change called unchanged (`compared`, `0` rows) only when Git proves its blob identical, as the check `unchanged_limits` uses does, asked privately in one bounded batch and never published, because the artifact digest redacts `env` values and `apiKeyHelper`; a file that changed with no compared grant moving (`changed_without_grant_change`), whose artifact differs only in its digest or whose content Git shows differs while its artifact did not (never a difference a checkout line-ending conversion or a converting attribute explains, and no filter is run), never a plugin manifest or marketplace, a retargeted link or project settings while a hook's loading basis moved, worded as no compared grant changing and never as which fields changed; any other changed file with no row (`changed_without_rows`); a file Git neither proves identical nor shows differs — a provided diff, a link read, a redacted path, a working-tree file a checkout wrote with `CRLF` that Git reports unchanged — as `unchanged_not_proven`, never no change and never a change (#812 review cycle 3); the side that published a source, worded `published by` rather than `read in` for a plugin manifest or marketplace, which is published only while it declares hooks; and on a refused comparison each blocking source and its kind. Outside the bounded candidate rules below, a file no inventory observed is never an item and its absence is no claim, which the block states where it is read — one line under the heading and `read_sources_only` in the JSON — so a true list cannot be taken for the account of the change (#812 follow-up); a source already in `unchanged_limits` is not repeated; the list is capped at ten with `omitted_items`, ordered so what no row shows precedes a file's rows and, among blocking limits, by kind (`unreadable`, `parse_failed`, `unresolved_precedence`, then `unsupported`, `dynamic_source_excluded`, `remote_source_excluded`) — order, not severity, and a ranking of kinds rather than of items, since `unsupported` carries both a file this entry merely does not accept and one whose own text would not parse, so an item behind the count may still be one to repair; total down to every field an item is keyed by, the source name and then its side, limit and status — and counted in text as items not listed, ranked below those listed, and the PR comment lists only what fits in the room its entries, review question, reproduction, advisory, next action and evidence leave, at most 2000 characters, so the block never pushes out a line the comment prints without it (a row list that fills the comment by itself still truncates it, as on `1.0.0`); an instruction file's line carries no redacted-values note; sources are the inventory's redacted paths; `null` means not recorded, which is how a `0.19` verifier reads. It moves no row, reason, digest, baseline, control state or next action, and `check`'s boundary result and text carry none, so neither `check` nor a provided diff asks Git anything for it. The same list names the changed inputs this entry does not read (#821, `tests/test_unread_changed_inputs.py`): capability diff `0.4` and verifier `0.21` add a `changed_not_read` item, with the `candidate` rule that named it, for each path in the comparison's own changed-file set — the committed change, or the working tree's tracked and untracked changes — that a bounded, documented rule set recognises as plausibly agent configuration (`mcp.json` in a plugin directory, a plugin manifest's `mcpServers`, a Codex, Cursor or Copilot manifest's `hooks` and the hook files it names, a manifest or marketplace that does not parse, `.cursor/hooks.json`, host settings below the repository root, a marketplace entry's external `source`) and that no inventory published; a member is named whatever read its file, because no reader reads it. It is named from the path and, for a manifest or marketplace member, its text: nothing is fetched, run or read as a grant, so it is never a row, a widening, a `check` violation or a loading claim, and an external source is described redacted and never fetched. It ranks right after the blocking limits, inside the same cap; `read_sources_only` is `false` while one is named, and the first line says so instead; `unread_candidates` and `unread_candidates_not_examined` say whether the change set was examined and how many candidates past the bound of 32 were not. A manifest-free `verify` whose only host-relevant change is such an input publishes the comparison instead of the setup route; a `0.20` verifier reads with the search not recorded. |
| `zero_install_detector` | `tools/shipgate-detect.py` | `agent_project_verdict` | `test_detector_verdict_matches_cli` | Emits no `diagnostics[]` and no `next_actions[]`; evidence strings and framework scores are simplified. See the script's own "Intentional simplifications". |
| `emitted_ci_workflow` | `src/agents_shipgate/cli/discovery/ci_workflow.py` | `executable_pin` | `tests/test_adopter_pins_resolve.py::test_the_emitted_workflow_pins_the_release_and_not_the_source_tree`, `tests/test_release_source.py::test_candidate_workflow_uses_immutable_source_before_and_after_publication` | Ordinary/source/preview builds use the published fallback; a stamped candidate pins its verified Action SHA and package version. Before publication its smoke substitutes the exact local wheel inputs. Provenance asserts no qualification. |
| `prompts` | `prompts/` | `contract_floor`, `executable_pin`, `placeholder_ownership`, `release_decision_vocabulary` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_enumerations_match_the_engine_vocabulary`, `test_surface_routes_human_owned_placeholders_to_a_human`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release`, `tests/test_adopter_pins_resolve.py::test_the_shipped_floor_is_decided_against_the_release_the_prompts_pin` | — |
| `skills` | `skills/` | `contract_floor`, `executable_pin`, `placeholder_ownership`, `release_decision_vocabulary`, `report_schema_pin` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_enumerations_match_the_engine_vocabulary`, `test_surface_routes_human_owned_placeholders_to_a_human`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release`, `tests/test_adopter_pins_resolve.py::test_the_shipped_floor_is_decided_against_the_release_the_prompts_pin`, `test_surface_names_the_current_report_schema` | Rendered mirror of `adoption-kits/claude-code-skill`; byte parity is pinned by `tests/test_agent_instructions_renderers.py`. |
| `plugins` | `plugins/` | `contract_floor`, `executable_pin`, `placeholder_ownership`, `release_decision_vocabulary`, `report_schema_pin` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_enumerations_match_the_engine_vocabulary`, `test_surface_routes_human_owned_placeholders_to_a_human`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release`, `tests/test_adopter_pins_resolve.py::test_the_shipped_floor_is_decided_against_the_release_the_prompts_pin`, `test_surface_names_the_current_report_schema` | Same rendered mirror; the plugin adds packaging metadata only. |
| `adoption_kits` | `adoption-kits/` | `contract_floor`, `executable_pin`, `placeholder_ownership`, `release_decision_vocabulary`, `report_schema_pin` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_enumerations_match_the_engine_vocabulary`, `test_surface_routes_human_owned_placeholders_to_a_human`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release`, `tests/test_adopter_pins_resolve.py::test_the_shipped_floor_is_decided_against_the_release_the_prompts_pin`, `test_surface_names_the_current_report_schema`, `tests/test_release_source.py::test_a_stamped_wheel_kit_pins_its_own_release_not_the_previous_one`, `tests/test_packaging.py::test_a_stamped_installed_wheel_kit_does_not_downgrade_or_stop_at_the_floor`, `tests/test_release_source.py::test_a_stamped_engine_below_the_floor_states_its_own_gap` | The renderer's *input*: carries `{{ … }}` templates, so its pins and floors are compared after rendering, never as literals. Every executable pin is a template: a release-stamped wheel renders its own version, Action source and contract, and other builds render the published fallback (#781). The stamped proofs are registered on this row only, because the checked-in mirrors are source renderings. |
| `examples` | `examples/` | `executable_pin`, `merge_verdict_vocabulary` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_enumerations_match_the_engine_vocabulary`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release` | Illustrative CI wiring. It gates on the engine's merge verdict rather than restating the release-decision set, so only the verdict claim is registered. |
| `policies` | `policies/` | — | — | Manifest fragments only. Answers no question the CLI answers: they are *inputs* the engine evaluates, not restatements of its output. |
| `harness` | `harness/` | `merge_verdict_vocabulary`, `release_decision_vocabulary` | `test_harness_holds_no_drifted_copy_of_the_engine_vocabularies` | Internal adoption-measurement harness, not an adopter-facing surface. It grades artifacts from whichever build a cell ran, so it carries literal vocabularies — each one derived from the engine's and checked against it. |
| `mcp_server` | `src/agents_shipgate/mcp_server/` | — | — | Transport only. Every answer it returns is produced by calling the CLI in-process, so it has nothing of its own to drift. |
| `design_partner_runbook` | `docs/design-partner-verifier-pilot.md` | `contract_floor`, `executable_pin`, `placeholder_ownership` | `test_executable_pin_resolves_in_a_published_channel`, `test_runbook_channel_table_states_the_released_contract_correctly`, `test_surface_routes_human_owned_placeholders_to_a_human`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release` | Version-specific by construction: it names one channel per partner and states what the released build does *not* emit. |
| `human_entry_path` | `README.md`, `docs/quickstart.md` | `contract_floor`, `executable_pin`, `merge_verdict_vocabulary`, `placeholder_ownership`, `release_decision_vocabulary`, `report_schema_pin` | `test_executable_pin_resolves_in_a_published_channel`, `test_the_human_entry_path_states_what_the_published_build_provides`, `test_surface_enumerations_match_the_engine_vocabulary`, `test_surface_routes_human_owned_placeholders_to_a_human`, `test_surface_names_the_current_report_schema` | States its vocabularies as Markdown reference tables rather than braced sets; the vocabulary reader was taught that shape by [#498](https://github.com/ThreeMoonsLab/agents-shipgate/issues/498) rather than exempted from it. The quoted `diff` answers, the auto-detected `origin/HEAD` base, the documented missing-base recovery and the channel statements are held to this tree's output and to `.github/release-channels.json` by `tests/test_host_diff_entry_docs.py` (#779), which the claims vocabulary has no row for. The same module holds the build the pages say their quotes came from to a record of the answers the newest published release prints, taken from that build at release-runbook step 8 (#778). The quickstart's detector continue rule names `suggested_sources`, `codex_plugin_candidates` and `host_boundary_candidates`, so a host-only repository is not stopped where `detect` routes it forward; `tests/test_public_surface_contract.py::test_detect_continue_rules_do_not_stop_a_host_only_repository` holds it and `llms.txt`'s rule to those fields (#792). |
| `agent_instructions` | `AGENTS.md`, `docs/agent-recipes.md`, `docs/agents/`, `docs/target-repo-agent-snippets.md` | `executable_pin`, `placeholder_ownership`, `report_schema_pin` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_routes_human_owned_placeholders_to_a_human`, `test_surface_names_the_current_report_schema` | States no contract floor of its own — it links [`agent-contract-current.md`](agent-contract-current.md) for the versions. What it does answer is who may fill a manifest placeholder, on the copy a coding agent actually reads, and it carries an Action ref, a `shipgate_version:` input and an install floor a reader runs. The `AGENTS.md` host-configuration review bullets name the manifest-free `diff` route and quote none of its output, so they add no claim; the quoted `diff` answers belong to the `human_entry_path` row (#792). |

`policies/` and `src/agents_shipgate/mcp_server/` carry no claim. That is a
finding, not an omission: neither restates an engine answer, so parity has
nothing to say about them and inventing a test for them would be theatre. They
stay on the list so the *next* reader does not have to re-derive that.

`README.md` and `docs/quickstart.md` were on the *other* list until
[#498](https://github.com/ThreeMoonsLab/agents-shipgate/issues/498) — recorded
as "repository documentation" and therefore checked by nobody for the two
things they do answer. They answer both, and both were wrong: the quickstart's
first command was `check --format agent-boundary-json`, which the release the
same page told a reader to install rejects outright, and its placeholder step
sent a coding agent to the README for `agent.declared_purpose` — a declaration
only a person may make. Neither is an unusual failure; they are this document's
two claim families, on the surface a stranger reaches first. Being unregistered
is what let them sit there.

## Known parity gaps

**None today**, and the way that happened is the point.

A gap is a surface that answers a question differently from the engine, allowed
to keep doing so only because it is written down here with an owner. Each one is
a row in the parity test marked `xfail(strict=True)`: it fails today, and the day
the owning fix lands it starts *passing*, which makes the strict marker fail and
forces the row to be retired. A gap cannot rot here unnoticed.

### Closed

Three were registered when this document was written, and all three closed
before it first landed:

| Gap | Surface | Owner | Outcome |
| --- | --- | --- | --- |
| `detector-mcp-server-source` | `zero_install_detector` | [#485](https://github.com/ThreeMoonsLab/agents-shipgate/issues/485) | Closed. The detector reads MCP registration sites; `known_omissions` is empty again. |
| `emitted-workflow-unpublished-pin` | `emitted_ci_workflow` | [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) | Closed. Ordinary builds' `init --ci` pins `LATEST_PUBLISHED_VERSION`; a release-stamped wheel pins its own source SHA (#570, #781). |
| `rendered-prompt-unpublished-pin` | `prompts`, `skills`, `plugins`, `adoption_kits` | [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) | Closed. Source-rendered prompts pin the published release and state the contract gap beside it; a release-stamped wheel's kit pins its own release and contract (#781). |

Every row flipped to `XPASS`, every strict marker failed, and the exemptions had
to be removed to get back to green. That is what a self-cleaning exemption is
for; nobody had to remember.

To register the next one: add it to `KNOWN_GAPS` in
`tests/test_distribution_surface_parity.py`, add a row above with its owning
issue, and mark the affected parity rows `xfail(strict=True)` naming the gap. A
gap without an owner is not a gap, it is a defect.

### Declared exceptions

Different from a gap, and not a divergence at all: a surface that cannot pin a
release because the capability it demonstrates postdates one. #497's rule allows
"a resolvable supported path **or an explicit version/contract incompatibility"**
— so such a file may target `@main`, which resolves, provided its own header
says which input postdates the release and what to do once one carries it.
`DECLARED_UNPINNED_REFS` enumerates these, and is empty today:
`examples/github-actions/10-check-run-annotations.yml` targeted `@main` until
`v1.0.0` carried `check_run_policy`, and has pinned the newest published
release since — `v1.1.0` today. The guard checks that the file really uses that ref and really explains itself, so an
unexplained `@main` elsewhere is still the defect it looks like.

## Release channels

"Resolvable" is judged against committed metadata, offline. Discovery and the
default static evaluation gain no network calls, and neither does the test
suite; the live check that the claimed tag exists on origin is the
`release-tag-consistency` job in `.github/workflows/ci.yml`, which runs on
pushes to `main`.

| Channel | Metadata | Reader gets it with | Qualification |
| --- | --- | --- | --- |
| Published release | `agents_shipgate.published_release` → `LATEST_PUBLISHED_VERSION` and `LATEST_PUBLISHED_CONTRACT_VERSION`, bound to the tag and to `.well-known` by `tests/test_adopter_pins_resolve.py` | `pipx install agents-shipgate`, `uses: …@v<tag>` | The declared channel's. **Qualified** for a version `.github/release-channels.json` declares `qualified`, and for every release before #648. **None** for a version it declares `advisory`, which ships a signed `advisory-statement.json` saying so — see `docs/release-evidence-policy-decision.md` § Amendment 5 |
| Unqualified preview | GitHub pre-release in the `preview-*` namespace, cut by `.github/workflows/release-preview.yml` | `gh release download preview-<version> --pattern '*.whl'` | **None**, by construction — see `docs/release-evidence-policy-decision.md` § Amendment 2 |
| Source checkout | `pyproject.toml` → `[project].version`, mirrored at `.well-known/agents-shipgate.json` → `version` | `./shipgate …` | Not a distributed build |
| Final candidate wheel | Build-only `_meta/release-source.json` binds a clean full source SHA and package version; generated CI uses that SHA, and the bundled skill kits render that SHA, that version and the contract the wheel emits into every pin and contract-floor statement (#781) | Reviewed local wheel; exact-wheel Action smoke requires a local path plus SHA-256 | The provenance record alone grants **none**; qualification, signing and publication bind those same wheel bytes separately |

The published and source values differ whenever the tree is ahead of the newest
tag, which is the normal state between releases. A surface may name the source
build only when it also says which channel that is; naming it as though it were
published was the `rendered-prompt-unpublished-pin` gap, closed by #506.

`hatch_build.py` belongs to the build toolchain, alongside `pyproject.toml`,
rather than an adopter-facing command. It produces the candidate's source
provenance and makes no claim about a runtime verdict or qualification.
`tests/test_wheel_candidate_build.py` checks that producer against real wheel builds;
the `emitted_ci_workflow` row above covers how the installed engine uses its
record. The top-level classifier records this distinction explicitly.

The main-tree `.well-known/agents-shipgate.json` integration enumeration is
checked against `contract --json` from the same source build, including missing
and unsupported entries. The site's discovery copy stays pinned to its released
tag and is compared only with that tag's contract, never with unreleased main.
An enumeration entry describes an existing integration format; it does not
add a CLI command or confer release authority.

Where a surface demonstrates a capability no published release carries, the
honest output is neither an unresolvable pin nor silence: it is to say so. #506
renders that sentence into the adoption prompts beside the pin, and the
`### Declared exceptions` rule above covers the committed examples.

## Adding or changing a surface

1. If it answers a question the engine answers, add its claims to `SURFACES` in
   `tests/test_distribution_surface_parity.py` and add its row here. The two are
   checked against each other, so neither can be updated alone.
2. If it answers nothing the engine answers, still add the row, with no claims
   and a note saying why — that is what keeps the next reader from re-deriving
   it.
3. If it cannot be brought to parity, it is a gap: give it a row in **Known
   parity gaps** with an owning issue, and an `xfail(strict=True)` row in the
   parity test. A gap without an owner is not a gap, it is a defect.
4. A new top-level directory in the repository must be classified as a surface
   root, a container root, or not distributed. Until it is, the parity test
   fails — which is the point.

See also [`CONTRIBUTING.md` § Surface discipline](../CONTRIBUTING.md#surface-discipline),
which governs whether a *new* surface should exist at all. This document governs
what an existing one is allowed to say.
