"""Shared snippets reused across multiple renderers.

Centralizing the wording here keeps surfaces consistent and makes the Rule 3
guard (``ci_mode: strict`` only appears in the CI-pointer paragraph) easy to
enforce with a snapshot test.
"""

from __future__ import annotations

CI_POINTER_PARAGRAPH = (
    "CI runs via `.github/workflows/agents-shipgate.yml`. Generate it with "
    "`agents-shipgate init --ci`. The default mode is `ci_mode: advisory`. "
    "Promotion to `ci_mode: strict` is a human decision after baseline review."
)

# The refresh obligation. It exists because a control state is only true of the
# workspace it was computed from, and a coding agent's conversation outlives
# that workspace: a human commits, a rebase lands, another tool runs. Both
# directions are failures — enforcing a stale stop after a newer complete run
# exists, and acting on a stale complete after the workspace moved. The machine
# form of this list is `agent_refresh_triggers` in the runtime contract.
CURRENT_CONTROL_PARAGRAPH = """`agents-shipgate-reports/current-control.json` is the one entry point that
says which control identity is current. Read it with `agents-shipgate agent
control --workspace .`, which checks the pointer against the repository as it
stands right now — a moved HEAD, a changed tree, or an edited working file
refuses the read. A non-zero exit means nothing is current here and you hold no
authority. Re-read it after any human or external-tool action, after commit,
rebase, checkout, pull, or any worktree change, after any agents-shipgate
command returns, before enforcing a cached `must_stop`, before commit/push/PR
update, before merge or release, and before declaring the task complete. If
`current_control_id` changed, discard every cached control state and restart
from the new identity. A result you remember from earlier in this conversation
never outranks the current pointer — in either direction."""

#: Name the change before routing on control (#662). The Cursor rule carries the
#: same words in its own layout; `test_agent_instructions_renderers` pins all three.
DIFF_FIRST_PARAGRAPH = """First, name what the change did to the agent's authority. This needs no
manifest and no committed baseline:

```bash
shipgate diff --workspace .
```

One row per host grant, each carrying subject, before, after, direction,
severity and why it matters; `⚠` marks a row the engine read as an
expansion of authority. Quote those rows to the user, and put them in the
pull request body. A covered comparison with no rows is a real answer, not
a missing one. `--json` emits the same rows under `rows`.

A row is a description, never a permission: showing one, or seeing an empty
table, grants no authority to edit, commit, push, merge or report the work
complete."""
