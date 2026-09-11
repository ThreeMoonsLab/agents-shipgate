# Instruction structure and review routing

Agents Shipgate compares the structure a supported host reads from an instruction
file. It does not treat the words “shell”, “must”, “approval” or a fenced shell
example as a permission grant. Guidance can influence an agent's behavior; an
unchanged structural comparison is neither a prompt-safety judgment nor proof of
runtime enforcement.

## What is compared

| Surface | Supported structural evidence |
| --- | --- |
| `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md` | Plain guidance. Body edits do not declare host permissions. |
| `SKILL.md` | Bounded, valid frontmatter, including identity, allowed tools, invocation settings and command hooks; exact inline bang-backtick preprocessing commands. |
| `.claude/commands/*.md` | Supported command frontmatter and exact inline bang-backtick preprocessing commands. |
| `.cursor/rules/*.mdc` | Description, globs and `alwaysApply` frontmatter. |

Every supported frontmatter field participates in comparison, including descriptive
metadata. A changed frontmatter value therefore remains reviewable. Structured
registration moves remain reviewable even when file contents are identical.
Directory role takes precedence over the basename: a Claude command named
`AGENTS.md` is a command. Unmodeled subagent/rule roles, unknown fields, invalid
types, duplicate keys, YAML aliases/tags, unterminated preprocessing, and executable
bang fences remain unresolved. The parser accepts at most 256 KiB of instruction
text, 32 KiB of frontmatter and 4,096 frontmatter tokens.

Configured manifests and policy sources retain precedence. A policy file called
`AGENTS.md` cannot become guidance because of its name. Broad `.claude/**` and
`.codex/**` registrations remain in the trust-root catalog: their settings,
permissions and unrecognized files receive their existing evaluation/review.

## One complete comparison across workflows

The final verifier, local boundary check and preflight share one classifier.
Legacy word-based requirement/command emitters are retired, with their published
IDs retained for the minor-cycle migration. An unresolved comparison produces
explicit input/structure evidence, including on the legacy Codex API; it never
infers an authority change from words in the body.
A positive comparison requires complete base/head text, an exact applicable diff,
unique endpoints and no unresolved structure. Partial hunks, unresolved renames,
Git mode/copy changes, path identity problems and path-only plans cannot establish
unchanged structure. An empty or malformed diff is not evidence of safety. The existing diff parser
can drop header-like hunk rows, including a removed Markdown horizontal rule
(#611); those comparisons remain unresolved rather than receiving a prose skip.

Preflight v0.5 publishes `instruction_structure_unchanged: true` only on the exact
instruction touch that meets this proof. It still asks for verification after the
edit. Verifier v0.17 and handoff v9 separate `conditional_file_edits` from the
unconditional `forbidden_file_edits`. Each conditional rule gives the original
patterns, the required comparison, a preflight route and a human-review fallback.
`grants_authority: false` is literal: this standing rule is not a permission or a
remembered approval. Read current control for operational authority.

Host inventory v0.3 and trust-root graph v0.2 preserve raw captured hashes alongside
structural comparison evidence. A prose edit still changes the raw identity and
invalidates an old verification receipt. Host drift and graph review projections
can omit the corresponding permission-change warning only when the supported
structure is positively unchanged. Malformed/unsupported instructions remain
visible as incomplete inventory coverage and cannot produce a complete baseline.

## Generated Claude Code hook

Reinstall the hook to receive the new runner. In the default `ask` mode, one
complete, bounded, contained `Edit` or `Write` is previewed through the installed
CLI's preflight parser. The proposed content is never executed. The runner requires
a v0.5 positive proof for exactly that path and reconfirms the original file's
identity after parsing. Unique replacements and explicit `replace_all` are
supported. Unknown edit shapes, non-LF or unterminated text, files over 128 KiB,
an older CLI, a timeout, malformed output or changed identity retain the prompt.
`deny` remains the operator's hard block.

Conditional instruction paths neither create nor consume per-path approval
memory. In particular, a silently accepted prose edit cannot authorize a later
`allowed-tools`, hook or registration change. PostToolUse still requests final
verification. This hook does not intercept arbitrary shell writes or replace CI.

## Compatibility and migration

Contract v32 advertises preflight v0.5, verifier v0.17, handoff v9, and host
inventory/baseline/drift v0.3. Published predecessor schemas remain frozen. Legacy
verifier/handoff readers preserve their existing deny-lists and do not synthesize
a conditional rule. Legacy host baselines remain readable but are incomparable
with v0.3; a person must inspect the old evidence and deliberately review any
replacement. No automatic baseline upgrade, acknowledgment or declaration is
created. Older preflight graphs cannot assert structural evidence they lack.

The frozen file named `preflight-schema.v0.4.json` describes a v0.3 payload. The
successor uses v0.5 without rewriting that historical URL; #609 tracks its discovery
cleanup. Use the actual payload discriminator, not a version inferred from a URL.

An empty plan retains the existing completed-planning response. It has no
verifier-bound current-control identity and cannot stand in for final verification.
The successor schema matches that existing response; #610 tracks the separate
compatibility decision about its shared permission vector. The edit hook accepts
only an exact verify-required preview with every permission false.

Refs #545, #516.
