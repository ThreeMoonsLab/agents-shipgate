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

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from agents_shipgate.published_release import latest_published_action_ref


def _action_ref() -> str:
    """Return the action ref the generated workflow should pin to.

    The newest *published* release tag, not ``v<__version__>``. ``@main`` is
    unpinned and breaks reproducibility, which is why this pins at all — but
    "pin to a ref that resolves" and "pin to the version of the tree that
    happens to be emitting" are different requirements, and only the first was
    ever needed. This file is written into a stranger's repository and then
    executed by their CI, so a ref that does not exist yet fails at
    action-resolution time, before any step runs: for 56 days every workflow
    `init --ci` wrote named a tag that had never been cut, and a first-time
    adopter's first Shipgate run was a red check about our repository rather
    than theirs (#506).

    ``AGENTS_SHIPGATE_WORKFLOW_REF`` overrides it for tracking ``main`` or
    testing against another release. The override is the operator's claim that
    the ref resolves; nothing here can check it.
    """
    import os

    override = os.environ.get("AGENTS_SHIPGATE_WORKFLOW_REF")
    if override:
        return override
    return latest_published_action_ref()


# Inputs/outputs mirror ``action.yml``; update both when adding inputs.
# A snapshot test guards against drift.
_WORKFLOW_TEMPLATE = """\
name: Agents Shipgate

on:
  pull_request:
    branches: [main]

permissions:
  contents: read
  pull-requests: write   # required for pr_comment: "true"
  # checks: write         # uncomment when enabling check_run: "true"

jobs:
  shipgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Run Agents Shipgate
        uses: ThreeMoonsLab/agents-shipgate@{ref}
        with:
          config: {config}
          ci_mode: advisory       # change to "strict" once findings are clean
          diff_base: target
          check_annotations: "true"
          pr_comment: "true"
          # fail_on: critical,high
          # baseline: .agents-shipgate/baseline.json
          # check_run: "true"
          # check_run_policy: require-mergeable
"""


DEFAULT_MANIFEST_PATH = "shipgate.yaml"

# Path characters that need no YAML quoting in a plain scalar. Anything else —
# `#` starts a comment, `: ` ends a key, quotes and braces start other node
# types — is emitted as a double-quoted scalar instead. A project directory
# named `apps/agent #1` otherwise renders `config: apps/agent #1/shipgate.yaml`,
# which YAML reads as `apps/agent` and the action then cannot find (#363
# review).
_PLAIN_YAML_SCALAR = re.compile(r"\A[A-Za-z0-9._][A-Za-z0-9._/+-]*\Z")


def _yaml_scalar(value: str) -> str:
    """Serialize ``value`` as a YAML scalar that reads back unchanged."""

    if _PLAIN_YAML_SCALAR.fullmatch(value):
        return value
    # JSON string syntax is a subset of YAML's double-quoted scalar, and its
    # escaping rules for quotes and backslashes are the same.
    return json.dumps(value)


def _render_workflow_template(config: str = DEFAULT_MANIFEST_PATH) -> str:
    return _WORKFLOW_TEMPLATE.format(ref=_action_ref(), config=_yaml_scalar(config))


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


def workflow_relative_path(config_value: str) -> str:
    """Workflow file for the manifest at ``config_value``.

    One file per gated manifest. A monorepo with one manifest per project
    needs one workflow per project: the action takes a single ``config``
    scalar, so a shared ``agents-shipgate.yml`` gates whichever project
    initialized first and silently leaves the rest ungated — while `--ci`
    reports `skipped_existing_target` as though the work were done (#363
    review). The repository-root manifest keeps the historical name.
    """

    if config_value == DEFAULT_MANIFEST_PATH:
        return WORKFLOW_RELATIVE_PATH
    slug = _workflow_slug(str(Path(config_value).parent))
    return f".github/workflows/agents-shipgate-{slug}.yml"


def _workflow_slug(relative_dir: str) -> str:
    """A file-name-safe, collision-free slug for a project directory."""

    slug = re.sub(r"[^A-Za-z0-9]+", "-", relative_dir).strip("-").lower()
    # Two directories can normalize to the same slug (``apps/a`` and
    # ``apps.a``), and two workflows with the same name is the very failure
    # this function exists to prevent. A short digest of the exact path keeps
    # them apart without making the common name unreadable.
    digest = hashlib.sha256(relative_dir.encode("utf-8")).hexdigest()[:8]
    return f"{slug or 'project'}-{digest}"


def write_ci_workflow(
    workspace: Path, *, repository_root: Path | None = None
) -> CiWorkflowResult:
    """Write the Shipgate workflow for ``workspace``'s manifest if absent.

    Refuses to overwrite. Also refuses if an existing workflow already gates
    this same manifest — surfacing the cross-reference so users don't
    accidentally double-wire CI.

    ``repository_root`` is where the workflow goes when the manifest lives
    in a sub-directory. GitHub Actions loads workflows from the repository
    root and nowhere else, so a workflow written beside a nested manifest
    is a file that never runs — reported as success while no gate exists
    (#363). The workflow's ``config:`` then names the manifest relative to
    that root, because the action runs with ``--workspace "."`` there.
    """
    workspace = workspace.resolve()
    root = (repository_root or workspace).resolve()
    try:
        manifest_relative = (workspace / DEFAULT_MANIFEST_PATH).relative_to(root)
    except ValueError:
        # The manifest is not under the named root; keep the workflow and its
        # config beside the manifest rather than pointing CI at a path that
        # does not exist from the root.
        root = workspace
        manifest_relative = Path(DEFAULT_MANIFEST_PATH)
    config_value = manifest_relative.as_posix()
    relative_target = workflow_relative_path(config_value)
    workflows_dir = root / ".github" / "workflows"
    target = root / relative_target

    cross_ref = _detect_cross_reference(
        workflows_dir, exclude=target, config_value=config_value
    )
    if cross_ref is not None:
        return CiWorkflowResult(
            status="skipped_cross_reference",
            path=str(target),
            message=(
                f"Shipgate already gates {config_value} in {cross_ref}; not "
                f"creating {Path(relative_target).name}. Edit the existing "
                "workflow if needed."
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
    target.write_text(_render_workflow_template(config_value), encoding="utf-8")
    return CiWorkflowResult(
        status="written",
        path=str(target),
        message=f"Wrote {target}",
    )


def _detect_cross_reference(
    workflows_dir: Path, *, exclude: Path, config_value: str
) -> Path | None:
    """Find a workflow that already gates *this* manifest.

    Skips the target file itself — only flags a *different* workflow.
    Matching on the manifest as well as on the action is what lets a second
    project be gated at all: a repository-wide "Shipgate is already wired"
    check would refuse every project after the first, leaving them ungated
    while reporting a skip.

    Parser scope: regex match on ``uses:`` keys plus the rendered ``config:``
    value. Mentions in comments, ``if:`` conditions, or YAML strings would
    also match; this is the documented parser boundary.
    """
    if not workflows_dir.is_dir():
        return None
    exclude_resolved = exclude.resolve()
    config_pattern = re.compile(
        rf"^\s*config:\s*[\"']?{re.escape(config_value)}[\"']?\s*$",
        re.MULTILINE,
    )
    for path in sorted(workflows_dir.iterdir()):
        if path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if path.resolve() == exclude_resolved:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _USES_PATTERN.search(text):
            continue
        # The historical behavior for a root manifest: any Shipgate workflow
        # counts, because `config:` defaults to `shipgate.yaml` when omitted.
        if config_value == DEFAULT_MANIFEST_PATH or config_pattern.search(text):
            return path
    return None
