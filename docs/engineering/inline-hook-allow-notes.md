# Inline hook allow notes

An added or changed Claude Code `PreToolUse` hook row can identify a literal
allow decision (#826). The note states the documented effect: auto-approves
matched tool calls without a prompt. Host exceptions and deny/ask rules still
apply; see [Claude Code's hook decision documentation](https://code.claude.com/docs/en/hooks#pretooluse-decision-control).
This is a reading of declared configuration, not evidence that a hook ran.

The reader accepts only a single `echo`, `echo -n`, `printf`, or `cat`
here-document (`<<` or `<<-`) emitting a JSON object. Echo and printf operands
must be fully quoted, and an echo or printf may be followed by `; exit 0` or
`&& exit 0`, which leave its output and exit status unchanged. `printf` accepts
a literal without format directives or escapes other than one trailing `\n`,
or `%s` / `%s\n` followed by one quoted JSON operand. Here-document delimiters
are word identifiers of at most 40 characters, optionally quoted; with `<<-`,
leading tabs are removed from each body line and the delimiter line, as the
shell does. Unquoted here-document bodies cannot contain backslashes. Commands
are limited to 8192 characters. Expansion syntax, command substitution,
carriage returns, line continuations, pipes, conditions, extra commands
(including `exit` on its own line, or with any status but `0`), redirects and
script invocations are outside this grammar, as are `echo -e`, `jq -n` and
interpreter one-liners such as `python -c`. No shell command is executed and no
script is opened.

The JSON has only `hookSpecificOutput`, containing `hookEventName: PreToolUse`,
`permissionDecision: allow`, and optionally a string `permissionDecisionReason`.
Duplicate keys and other fields are not accepted, including the common
`suppressOutput`, `continue` and `systemMessage` fields and the older top-level
`{"decision": "approve"}` form, whose current effect this grammar does not
settle. Literal `deny` and `ask` decisions produce no allow note. The reason is
never published.

The matcher must be omitted, empty, `*`, `.*`, or a plain tool-name alternation
containing the exact name `Bash`, optionally enclosed in parentheses and/or
anchored with `^` and `$`. For example, `Bash|Read` qualifies, but `Read`,
`NotBash`, and arbitrary regular expressions do not. Handler conditions such
as `if`, asynchronous options, and unknown group or handler options prevent
recognition. The supported handler fields are `type`, `command`, `timeout`,
and `statusMessage`; supported group fields are `matcher` and `hooks`.

Only hooks read from host configuration or an enabled project plugin receive
the note. A removed hook is not described as currently granting approval. The
note follows the existing hook loading basis (#714), which does not read
`disableAllHooks`: a settings file that also sets `disableAllHooks: true`
still gets the note.
Published matchers use the existing bounded label and redaction rules. The
command and JSON body are never added to the note.

The inventory's handler metadata carries `inline_allow` and `decision_limit`,
only on a Claude Code `PreToolUse` handler, the one this reader examines.
Unsupported PreToolUse commands, including scripts, name
`script_or_command_behavior_not_read` as their limit; a handler whose literal
decision was read carries `inline_allow` alone. This is metadata, not a new
coverage failure or auto-approval finding. A false `inline_allow` does not
establish that the command cannot approve a call. Handlers of other events and
hosts, such as a `PermissionRequest` hook that emits an allow, carry neither
field: their absence means not examined, never read without a limit. Existing
handler truncation remains in effect.

These fields are display evidence, excluded from saved baselines and grant
comparison identity. Only the row's explanation changes: direction, widening,
severity, check decisions and control permissions are unchanged. Diff,
verifier host comparison and PR comments consume the same row. No check ID is
introduced. The unreleased inventory schema `0.7` and runtime contract `41`
are extended in place; released schemas remain unchanged.

Regression fixtures establish recognition and abstention within this grammar.
They do not establish a public-PR yield or false-positive rate. The #830
released-build evaluation remains separate.
