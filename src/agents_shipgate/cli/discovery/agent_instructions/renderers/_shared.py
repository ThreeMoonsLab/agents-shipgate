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
control`; a non-zero exit means nothing is current here and you hold no
authority. Re-read it after any human or external-tool action, after commit,
rebase, checkout, pull, or any worktree change, after any agents-shipgate
command returns, before enforcing a cached `must_stop`, before commit/push/PR
update, before merge or release, and before declaring the task complete. If
`current_control_id` changed, discard every cached control state and restart
from the new identity. A result you remember from earlier in this conversation
never outranks the current pointer — in either direction."""
