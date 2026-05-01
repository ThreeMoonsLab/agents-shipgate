"""Generate ``.github/workflows/agents-shipgate.yml`` for ``shipgate init --ci``.

Per the v0.6 plan §2:
- ``--ci`` is orthogonal to ``--write`` — workflow file existence is
  independent of manifest existence; each gets its own overwrite-refusal.
- Refuses to overwrite an existing ``agents-shipgate.yml``.
- Detects cross-workflow shipgate references (any other ``.yml``/``.yaml``
  in ``.github/workflows/`` that ``uses: ThreeMoonsLab/agents-shipgate``)
  and skips with a distinct message — avoids creating a duplicate
  workflow when shipgate is already wired in.

Status enum returned by :func:`write_ci_workflow`:
- ``"written"``  — workflow created.
- ``"skipped_existing_target"``  — agents-shipgate.yml already exists.
- ``"skipped_cross_reference"``  — another workflow already calls the
  action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agents_shipgate import __version__

# The generated workflow pins to the current package version so users
# get a reproducible action reference. Override via the
# ``AGENTS_SHIPGATE_WORKFLOW_REF`` env var if you need to track main or
# pin to a different release for testing.


def _action_ref() -> str:
    """Return the action ref the generated workflow should pin to.

    Defaults to ``v<__version__>`` so newly-onboarded repos pin to the
    Shipgate release that wrote their workflow. ``@main`` is unpinned and
    breaks reproducibility.
    """
    import os

    override = os.environ.get("AGENTS_SHIPGATE_WORKFLOW_REF")
    if override:
        return override
    return f"v{__version__}"


# Inputs/outputs mirror ``action.yml``; update both when adding inputs.
# A snapshot test guards against drift.
_WORKFLOW_TEMPLATE = """\
name: Agents Shipgate

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write   # only used when pr_comment: true; harmless otherwise

jobs:
  shipgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Agents Shipgate
        uses: ThreeMoonsLab/agents-shipgate@{ref}
        with:
          config: shipgate.yaml
          ci_mode: advisory       # change to "strict" once findings are clean
          # fail_on: critical,high
          # baseline: .agents-shipgate/baseline.json
          # pr_comment: "true"
"""


def _render_workflow_template() -> str:
    return _WORKFLOW_TEMPLATE.format(ref=_action_ref())


# Backwards-compat: tests and external callers may import the constant.
WORKFLOW_TEMPLATE = _render_workflow_template()

WORKFLOW_RELATIVE_PATH = ".github/workflows/agents-shipgate.yml"

_USES_PATTERN = re.compile(
    r"^\s*-?\s*uses:\s*[\"']?ThreeMoonsLab/agents-shipgate(?:@[^\s\"']+)?[\"']?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass
class CiWorkflowResult:
    status: str  # "written" | "skipped_existing_target" | "skipped_cross_reference"
    path: str
    message: str
    cross_reference_path: str | None = None


def write_ci_workflow(workspace: Path) -> CiWorkflowResult:
    """Write ``.github/workflows/agents-shipgate.yml`` if absent.

    Refuses to overwrite. Also refuses if any existing workflow already
    calls ``ThreeMoonsLab/agents-shipgate`` — surfacing the cross-reference
    so users don't accidentally double-wire CI.
    """
    workspace = workspace.resolve()
    workflows_dir = workspace / ".github" / "workflows"
    target = workspace / WORKFLOW_RELATIVE_PATH

    cross_ref = _detect_cross_reference(workflows_dir, exclude=target)
    if cross_ref is not None:
        return CiWorkflowResult(
            status="skipped_cross_reference",
            path=str(target),
            message=(
                f"Shipgate is already wired in {cross_ref}; not creating "
                f"agents-shipgate.yml. Edit the existing workflow if needed."
            ),
            cross_reference_path=str(cross_ref),
        )

    if target.exists():
        return CiWorkflowResult(
            status="skipped_existing_target",
            path=str(target),
            message=(
                f"Workflow already exists at {target}; not overwriting. "
                f"Edit it directly or delete it before re-running --ci."
            ),
        )

    workflows_dir.mkdir(parents=True, exist_ok=True)
    # Re-render at write time so the ref reflects the current package
    # version (or the AGENTS_SHIPGATE_WORKFLOW_REF override).
    target.write_text(_render_workflow_template(), encoding="utf-8")
    return CiWorkflowResult(
        status="written",
        path=str(target),
        message=f"Wrote {target}",
    )


def _detect_cross_reference(workflows_dir: Path, *, exclude: Path) -> Path | None:
    """Scan workflows for any ``uses: ThreeMoonsLab/agents-shipgate*``.

    Skips the target file itself — only flags a *different* workflow.
    Parser scope: regex match on ``uses:`` keys. Mentions in comments,
    ``if:`` conditions, or YAML strings would also match; this is the
    documented parser boundary.
    """
    if not workflows_dir.is_dir():
        return None
    exclude_resolved = exclude.resolve()
    for path in sorted(workflows_dir.iterdir()):
        if path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if path.resolve() == exclude_resolved:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _USES_PATTERN.search(text):
            return path
    return None
