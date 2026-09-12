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
| `github_action` | `action.yml`, `scripts/github_action_outputs.py` | `merge_verdict_vocabulary` | `test_action_input_enumerates_engine_merge_verdicts`, `test_action_output_script_shares_the_engine_merge_verdicts` | The paired `shipgate_wheel`/`shipgate_wheel_sha256` inputs install a caller-supplied local wheel instead of a published version, so that route names no channel and claims no `executable_pin`; it is refused unless both halves are given, and it installs `--no-deps`. `tests/test_action_engine_install.py` proves the refusals. |
| `capability_diff` | `src/agents_shipgate/cli/diff.py`, `src/agents_shipgate/core/capability_diff_rows.py`, `src/agents_shipgate/core/host_comparison.py`, `src/agents_shipgate/report/host_comparison.py` | — | — | Answers no question the engine answers: it emits no verdict, no release decision and no pin. Every field is read from the drift payload the engine already produces — `risk` is the engine's severity and `expansion_signals` is the engine's word on widening — so there is no second implementation to drift. `verify`/PR and `check` reuse the host comparator (#684, `tests/test_manifest_free_pr_rows.py`); check retains argument redaction and its existing local-policy control. Missing comparison evidence never supplies empty comparable rows. Host route only; workflow rows compare effective writes and reusable secret recipients (#685, `tests/test_workflow_capability_diff.py`); artifact-only edits remain separate evidence. Tool-source subjects are #655. |
| `zero_install_detector` | `tools/shipgate-detect.py` | `agent_project_verdict` | `test_detector_verdict_matches_cli` | Emits no `diagnostics[]` and no `next_actions[]`; evidence strings and framework scores are simplified. See the script's own "Intentional simplifications". |
| `emitted_ci_workflow` | `src/agents_shipgate/cli/discovery/ci_workflow.py` | `executable_pin` | `tests/test_adopter_pins_resolve.py::test_the_emitted_workflow_pins_the_release_and_not_the_source_tree`, `tests/test_release_source.py::test_candidate_workflow_uses_immutable_source_before_and_after_publication` | Ordinary/source/preview builds use the published fallback; a stamped candidate pins its verified Action SHA and package version. Before publication its smoke substitutes the exact local wheel inputs. Provenance asserts no qualification. |
| `prompts` | `prompts/` | `contract_floor`, `executable_pin`, `placeholder_ownership`, `release_decision_vocabulary` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_enumerations_match_the_engine_vocabulary`, `test_surface_routes_human_owned_placeholders_to_a_human`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release`, `tests/test_adopter_pins_resolve.py::test_the_shipped_floor_is_decided_against_the_release_the_prompts_pin` | — |
| `skills` | `skills/` | `contract_floor`, `executable_pin`, `placeholder_ownership`, `release_decision_vocabulary`, `report_schema_pin` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_enumerations_match_the_engine_vocabulary`, `test_surface_routes_human_owned_placeholders_to_a_human`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release`, `tests/test_adopter_pins_resolve.py::test_the_shipped_floor_is_decided_against_the_release_the_prompts_pin`, `test_surface_names_the_current_report_schema` | Rendered mirror of `adoption-kits/claude-code-skill`; byte parity is pinned by `tests/test_agent_instructions_renderers.py`. |
| `plugins` | `plugins/` | `contract_floor`, `executable_pin`, `placeholder_ownership`, `release_decision_vocabulary`, `report_schema_pin` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_enumerations_match_the_engine_vocabulary`, `test_surface_routes_human_owned_placeholders_to_a_human`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release`, `tests/test_adopter_pins_resolve.py::test_the_shipped_floor_is_decided_against_the_release_the_prompts_pin`, `test_surface_names_the_current_report_schema` | Same rendered mirror; the plugin adds packaging metadata only. |
| `adoption_kits` | `adoption-kits/` | `contract_floor`, `executable_pin`, `placeholder_ownership`, `release_decision_vocabulary`, `report_schema_pin` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_enumerations_match_the_engine_vocabulary`, `test_surface_routes_human_owned_placeholders_to_a_human`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release`, `tests/test_adopter_pins_resolve.py::test_the_shipped_floor_is_decided_against_the_release_the_prompts_pin`, `test_surface_names_the_current_report_schema` | The renderer's *input*: carries `{{ … }}` templates, so its pins and floors are compared after rendering, never as literals. |
| `examples` | `examples/` | `executable_pin`, `merge_verdict_vocabulary` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_enumerations_match_the_engine_vocabulary`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release` | Illustrative CI wiring. It gates on the engine's merge verdict rather than restating the release-decision set, so only the verdict claim is registered. |
| `policies` | `policies/` | — | — | Manifest fragments only. Answers no question the CLI answers: they are *inputs* the engine evaluates, not restatements of its output. |
| `harness` | `harness/` | `merge_verdict_vocabulary`, `release_decision_vocabulary` | `test_harness_holds_no_drifted_copy_of_the_engine_vocabularies` | Internal adoption-measurement harness, not an adopter-facing surface. It grades artifacts from whichever build a cell ran, so it carries literal vocabularies — each one derived from the engine's and checked against it. |
| `mcp_server` | `src/agents_shipgate/mcp_server/` | — | — | Transport only. Every answer it returns is produced by calling the CLI in-process, so it has nothing of its own to drift. |
| `design_partner_runbook` | `docs/design-partner-verifier-pilot.md` | `contract_floor`, `executable_pin`, `placeholder_ownership` | `test_executable_pin_resolves_in_a_published_channel`, `test_runbook_channel_table_states_the_released_contract_correctly`, `test_surface_routes_human_owned_placeholders_to_a_human`, `tests/test_adopter_pins_resolve.py::test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release` | Version-specific by construction: it names one channel per partner and states what the released build does *not* emit. |
| `human_entry_path` | `README.md`, `docs/quickstart.md` | `contract_floor`, `executable_pin`, `merge_verdict_vocabulary`, `placeholder_ownership`, `release_decision_vocabulary`, `report_schema_pin` | `test_executable_pin_resolves_in_a_published_channel`, `test_the_human_entry_path_states_what_the_published_build_provides`, `test_surface_enumerations_match_the_engine_vocabulary`, `test_surface_routes_human_owned_placeholders_to_a_human`, `test_surface_names_the_current_report_schema` | States its vocabularies as Markdown reference tables rather than braced sets; the vocabulary reader was taught that shape by [#498](https://github.com/ThreeMoonsLab/agents-shipgate/issues/498) rather than exempted from it. |
| `agent_instructions` | `AGENTS.md`, `docs/agent-recipes.md`, `docs/agents/`, `docs/target-repo-agent-snippets.md` | `executable_pin`, `placeholder_ownership`, `report_schema_pin` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_routes_human_owned_placeholders_to_a_human`, `test_surface_names_the_current_report_schema` | States no contract floor of its own — it links [`agent-contract-current.md`](agent-contract-current.md) for the versions. What it does answer is who may fill a manifest placeholder, on the copy a coding agent actually reads, and it carries an Action ref, a `shipgate_version:` input and an install floor a reader runs. |

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
| `emitted-workflow-unpublished-pin` | `emitted_ci_workflow` | [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) | Closed. `init --ci` pins `LATEST_PUBLISHED_VERSION`. |
| `rendered-prompt-unpublished-pin` | `prompts`, `skills`, `plugins`, `adoption_kits` | [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) | Closed. The rendered prompts pin the published release and state the contract gap beside it. |

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
— so `examples/github-actions/10-check-run-annotations.yml` targets `@main`,
which resolves, and says in its own header which input postdates the release and
what to do once one carries it. `DECLARED_UNPINNED_REFS` enumerates these; the
guard checks that the file really uses that ref and really explains itself, so an
unexplained `@main` elsewhere is still the defect it looks like.

## Release channels

"Resolvable" is judged against committed metadata, offline. Discovery and the
default static evaluation gain no network calls, and neither does the test
suite; the live check that the claimed tag exists on origin is the
`release-tag-consistency` job in `.github/workflows/ci.yml`, which runs on
pushes to `main`.

| Channel | Metadata | Reader gets it with | Qualification |
| --- | --- | --- | --- |
| Published release | `agents_shipgate.published_release` → `LATEST_PUBLISHED_VERSION` and `LATEST_PUBLISHED_CONTRACT_VERSION`, bound to the tag and to `.well-known` by `tests/test_adopter_pins_resolve.py` | `pipx install agents-shipgate`, `uses: …@v<tag>` | Qualified |
| Unqualified preview | GitHub pre-release in the `preview-*` namespace, cut by `.github/workflows/release-preview.yml` | `gh release download preview-<version> --pattern '*.whl'` | **None**, by construction — see `docs/release-evidence-policy-decision.md` § Amendment 2 |
| Source checkout | `pyproject.toml` → `[project].version`, mirrored at `.well-known/agents-shipgate.json` → `version` | `./shipgate …` | Not a distributed build |
| Final candidate wheel | Build-only `_meta/release-source.json` binds a clean full source SHA and package version; generated CI uses that SHA | Reviewed local wheel; exact-wheel Action smoke requires a local path plus SHA-256 | The provenance record alone grants **none**; qualification, signing and publication bind those same wheel bytes separately |

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
