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
