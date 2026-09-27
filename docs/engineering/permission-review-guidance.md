# Conditional permission-review guidance (#839)

Selected reader: a non-author maintainer reviewing a repository-declared Claude
Code shell permission change. Product owner: @pengfei-threemoonslab, as recorded
in [the program decision](https://github.com/ThreeMoonsLab/agents-shipgate/issues/778#issuecomment-5820723804).
Engineering preparation: Codex. Product wording review and the unaided-reader
exercise in #811 remain separate acceptance work; fixture tests are not human
observations or external adoption evidence.

The maintained `review.changes[].guidance` object is advisory. It changes no
grant, inventory digest, direction, severity, expansion signal, control state,
permission, or release verdict. Enclosing comparison refs, input identity and
coverage apply. There is no invented source line or independent risk score.

## Evidence-to-choice mapping

| Case | Required evidence | Question and eligible choice | Observable consequence |
| --- | --- | --- | --- |
| Allow widened | Engine-proven same-source replacement; supported Bash rules; both inventories | Does this task need the new rule's command matches beyond the previous rule? Retain with rationale, or ask the declaration owner to choose scope. | The retained delta stays visible; a subsequent comparison shows the chosen declaration delta. |
| Allow narrowed | Same evidence, established narrowing | Is this restriction intended? Retain with rationale, or let the owner choose scope. | No automatic expansion; the resulting declaration remains compared. |
| Deny removed | Exact removed deny rule | Should this source remove this deny declaration for the task? Retain the removal or let the owner choose the declaration. | Removal is a declaration fact, not proof that the command is now allowed. |
| Allow/deny added or removed | One raw grant and exact source identity | Should this source add/remove this declaration? | No inferred replacement, global permission gain or loss. |
| Disposition moved | Engine-proven exact rule identity in one source | Is moving this rule between allow and deny intended? | Rerun establishes declared disposition, not runtime permission. |
| Conflicting or unknown context | Unchanged rules and settings from both inventories | Withhold specific choices; identify the contextual limitation. | No least-privilege fix or effective-access conclusion is inferred. |
| Redacted, unsupported or oversized rule | Required literal evidence missing | Explicitly unavailable; no specific question or choice. | No reconstruction from display strings or private text. |
| Legacy artifact | No recorded guidance | Explicitly unavailable. | Rendering does not invent historical reader evidence. |
| Incomparable input | Existing comparison refusal | Existing coverage/recovery remains the answer. | No correction or no-change claim. |
| Covered no change | No changed grant | No guidance and no new task. | Existing coverage-qualified no-change answer. |

Each supported item also offers evidence-preserving dispute triage. None offers
suppression, policy rewriting, a patch, an executable action, or a way around
current control. The previous rule is a reference, not an automatically safe
fix. The advisory declaration owner remains to be assigned; a displayed name
or PR note does not authenticate eligibility. A comparison against the original
base can establish the declaration delta; it does not confirm intent, runtime
access, or the stronger correction-proof claim owned by #840.

The first supported spelling is `Bash(...)` with literal command words, an exact
command, or a terminal ` *` / `:*` prefix. Operators, substitutions, quoting,
interior wildcards, redacted fields and rules over 512 characters withhold
specific choices. The comparator supplies pairings; this projection selects
none. A changed rule can receive an individual declaration question while an
ambiguous replacement remains unpaired. Unchanged overlapping deny/ask rules,
permission-mode settings and incomplete relevant coverage withhold choices.

## Presentation and compatibility

Raw grant identities stay attached to rows through sorting only until the
comparison generates guidance. The result is serialized in the maintained
review model. Reloaded CLI/PR renderers consume it directly and never parse
rendered cells. Guidance also withholds rules changed by the existing credential
redactor, including on routes whose original rows print literal rule text.
Argument-redacted routes publish no raw rule in guidance.

The shared renderer emits at most two whole items within 2,400 characters and
reports the exact omitted item count. It never truncates a condition away from
its choice. JSON retains the full list. PR comments allocate existing facts,
coverage, authority and evidence first; guidance uses only remaining room. A
comment with no room retains its existing evidence pointer. All source-derived
strings use the existing single-line/Markdown literal display protections.

Verifier `0.21`, capability diff `0.4` and contract v41 are unreleased and are
extended in place. `guidance: null` means no supported projection was recorded;
it does not establish absence of risk. Frozen verifier `0.20` is unchanged and
cannot claim the new field. Current readers accept historical artifacts without
guidance and report that specific guidance is unavailable.

## Readiness evidence still required

Source fixture tests must prove JSON round-trip, CLI/PR agreement, unchanged
control permissions, first-five-lines ordering, bounds, unknown context,
redaction, unread input and no-change behavior. A separately built and installed
candidate wheel must repeat the supported example with its hash and import
path recorded. Neither proves behavior in the currently published wheel.

After product review, #811 must record an unaided reader's fact, missing intent,
chosen option, next owner/action and verification target, plus assistance and
harmful misinterpretations. No such outcome is asserted by this implementation.
