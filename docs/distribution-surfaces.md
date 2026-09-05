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

## Claims vocabulary

Every claim in the registry is one of these. The vocabulary is closed; the code
and this document are checked against each other by
`tests/test_distribution_surface_parity.py`.

| Claim | Means | Engine source of truth |
| --- | --- | --- |
| `agent_project_verdict` | Answers "is this an agent project", and with which sources | `agents_shipgate.cli.discovery.detect_workspace` |
| `merge_verdict_vocabulary` | Enumerates the merge verdicts a caller can gate on | `agents_shipgate.schemas.contract.MERGE_VERDICTS` |
| `release_decision_vocabulary` | Enumerates the release-gate decisions | `agents_shipgate.schemas.contract.RELEASE_DECISIONS` |
| `placeholder_ownership` | Tells a reader who may fill a manifest placeholder | `agents_shipgate.cli.discovery.placeholders.placeholder_owner` |
| `executable_pin` | Names a version, tag or ref a reader will install or run | `.well-known/agents-shipgate.json` → `release_status.latest_release` |
| `contract_floor` | Names a runtime contract version a reader must reach | `agents_shipgate.schemas.contract.CONTRACT_VERSION`, per build |

## The registry

| Surface | Root | Claims | Proven by | Narrower than the CLI in |
| --- | --- | --- | --- | --- |
| `github_action` | `action.yml` | `merge_verdict_vocabulary` | `test_action_input_enumerates_engine_merge_verdicts`, `test_action_output_script_shares_the_engine_merge_verdicts` | — |
| `zero_install_detector` | `tools/shipgate-detect.py` | `agent_project_verdict` | `test_detector_verdict_matches_cli` | Emits no `diagnostics[]` and no `next_actions[]`; evidence strings and framework scores are simplified. See the script's own "Intentional simplifications". |
| `emitted_ci_workflow` | `src/agents_shipgate/cli/discovery/ci_workflow.py` | `executable_pin` | `test_emitted_ci_workflow_pins_a_published_ref` | — |
| `prompts` | `prompts/` | `executable_pin`, `contract_floor`, `merge_verdict_vocabulary`, `placeholder_ownership` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_routes_human_owned_placeholders_to_a_human` | — |
| `skills` | `skills/` | `executable_pin`, `contract_floor`, `merge_verdict_vocabulary`, `placeholder_ownership` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_routes_human_owned_placeholders_to_a_human` | Rendered mirror of `adoption-kits/claude-code-skill`; byte parity is pinned by `tests/test_agent_instructions_renderers.py`. |
| `plugins` | `plugins/` | `executable_pin`, `contract_floor`, `merge_verdict_vocabulary`, `placeholder_ownership` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_routes_human_owned_placeholders_to_a_human` | Same rendered mirror; the plugin adds packaging metadata only. |
| `adoption_kits` | `adoption-kits/` | `executable_pin`, `contract_floor`, `merge_verdict_vocabulary`, `placeholder_ownership` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_routes_human_owned_placeholders_to_a_human` | The renderer's *input*: carries `{{ … }}` templates, so its pins are compared after rendering, never as literals. |
| `examples` | `examples/` | `executable_pin`, `merge_verdict_vocabulary` | `test_executable_pin_resolves_in_a_published_channel` | Illustrative CI wiring. States no verdict of its own — it shows how to gate on the engine's. |
| `policies` | `policies/` | — | — | Manifest fragments only. Answers no question the CLI answers: they are *inputs* the engine evaluates, not restatements of its output. |
| `harness` | `harness/` | `merge_verdict_vocabulary` | `test_surface_states_only_engine_merge_verdicts` | Internal adoption-measurement harness, not an adopter-facing surface; it imports the package rather than re-deriving. |
| `mcp_server` | `src/agents_shipgate/mcp_server/` | — | — | Transport only. Every answer it returns is produced by calling the CLI in-process, so it has nothing of its own to drift. |
| `design_partner_runbook` | `docs/design-partner-verifier-pilot.md` | `executable_pin`, `contract_floor`, `placeholder_ownership` | `test_executable_pin_resolves_in_a_published_channel`, `test_surface_routes_human_owned_placeholders_to_a_human`, `test_contract_floor_is_reachable_in_the_build_the_surface_names` | Version-specific by construction: it names one channel per partner and states what the released build does *not* emit. |

`policies/` and `src/agents_shipgate/mcp_server/` carry no claim. That is a
finding, not an omission: neither restates an engine answer, so parity has
nothing to say about them and inventing a test for them would be theatre. They
stay on the list so the *next* reader does not have to re-derive that.

## Known parity gaps

A gap is a surface that answers a question differently from the engine, and is
allowed to keep doing so only because it is written down here with an owner.
Each one is a row in the parity test marked `xfail(strict=True)`: it fails
today, and the day the owning fix lands it starts *passing*, which makes the
strict marker fail and forces this row to be retired. A gap cannot rot here
unnoticed.

| Gap | Surface | What diverges | Owner |
| --- | --- | --- | --- |
| `detector-mcp-server-source` | `zero_install_detector` | The CLI reads MCP tool registrations out of TypeScript and Go source (`mcp_server_source`, #431); the standalone script does not, so it answers `is_agent_project: false` for every vendor MCP server the CLI now accepts. | [#485](https://github.com/ThreeMoonsLab/agents-shipgate/issues/485) |
| `emitted-workflow-unpublished-pin` | `emitted_ci_workflow` | `init --write --ci` writes `uses: ThreeMoonsLab/agents-shipgate@v<__version__>`. While the tree is ahead of the newest tag that ref does not resolve, and GitHub fails the adopter's job before any step runs. | [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) |
| `rendered-prompt-unpublished-pin` | `prompts`, `skills`, `plugins`, `adoption_kits` | The rendered prompts pin `uvx agents-shipgate@<emitting build>` so the runner and the contract floor beside it come from one build. While the tree is ahead of the newest release that pin names a version PyPI does not have. | [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) |

## Release channels

"Resolvable" is judged against committed metadata, offline. Discovery and the
default static evaluation gain no network calls, and neither does the test
suite; the live check that the claimed tag exists on origin is the
`release-tag-consistency` job in `.github/workflows/ci.yml`, which runs on
pushes to `main`.

| Channel | Metadata | Reader gets it with | Qualification |
| --- | --- | --- | --- |
| Published release | `.well-known/agents-shipgate.json` → `release_status.latest_release` | `pipx install agents-shipgate`, `uses: …@v<tag>` | Qualified |
| Unqualified preview | GitHub pre-release in the `preview-*` namespace, cut by `.github/workflows/release-preview.yml` | `gh release download preview-<version> --pattern '*.whl'` | **None**, by construction — see `docs/release-evidence-policy-decision.md` § Amendment 2 |
| Source checkout | `pyproject.toml` → `[project].version`, mirrored at `.well-known/agents-shipgate.json` → `version` | `./shipgate …` | Not a distributed build |

The published and source values differ whenever the tree is ahead of the newest
tag, which is the normal state between releases. A surface may name the source
build only when it also says which channel that is; naming it as though it were
published is the `rendered-prompt-unpublished-pin` gap above.

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
