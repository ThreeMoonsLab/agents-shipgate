"""Ensure ``agents-shipgate-reports/`` is gitignored.

``init --write`` calls :func:`ensure_reports_gitignore` after the manifest
action (whether the manifest was written this run or already existed) so the
reports directory created by the first ``scan`` doesn't silently land in
``git status``.

The implementation parallels :mod:`agent_instructions.managed_block` but uses
``#``-style line comments (``# agents-shipgate:start v=1`` … ``# agents-shipgate:end``)
because ``.gitignore`` has no HTML-comment syntax. Behavior summary:

* No ``.gitignore`` exists → create with just the managed block.
* ``.gitignore`` exists, the user already lists ``agents-shipgate-reports/``
  (or a normalized variant) on its own line → ``already_present``, no-op.
* ``.gitignore`` exists with a negated line (``!agents-shipgate-reports/``)
  → ``skipped_negated``, no-op. The user explicitly opted out.
* ``.gitignore`` exists with our markers → upsert (UNCHANGED / UPDATED /
  MIGRATED / NEWER_VERSION / AMBIGUOUS).
* ``.gitignore`` exists without our markers and without the variant line
  → append a managed block separated by one blank line.

Failing to write the gitignore never raises; the function returns an outcome
that the CLI surfaces in the ``--json`` payload as advisory information.

The module is byte-pure: callers pass ``bytes`` to :func:`parse` and
:func:`upsert` and receive ``bytes`` back. Newline style is preserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Bumped only on incompatible content changes (e.g., we want to ignore an
# additional path). v1 ignores only ``agents-shipgate-reports/``. The v=N
# token lets a future CLI upgrade the block in place — old blocks auto-upgrade
# on the next ``init --write`` run.
GITIGNORE_BLOCK_VERSION: int = 1

# Canonical reports directory name. Configurable via ``output.directory`` in
# the manifest, but the canonical name is what every renderer/sample/doc
# uses; if a user customizes ``output.directory`` they are expected to manage
# their own gitignore (we leave a visible managed block so they can see what
# we tried to add and edit accordingly).
REPORTS_DIR_NAME: str = "agents-shipgate-reports"

# ``\r?`` before ``$`` so the markers parse on a CRLF-style ``.gitignore``
# too. Without it, ``parse()`` returns ``NO_MARKERS`` on a host that was
# (correctly) rendered with CRLF newlines on the first install, and the
# next ``init --write`` appends a duplicate block instead of recognizing
# the existing one. Regression coverage:
# ``test_parse_locates_present_block_with_crlf_newlines`` and
# ``test_ensure_idempotent_on_second_run_with_crlf``.
START_PATTERN = re.compile(rb"^# agents-shipgate:start v=(\d+)\r?$", re.MULTILINE)
END_PATTERN = re.compile(rb"^# agents-shipgate:end\r?$", re.MULTILINE)

# Variants of the reports-dir line we treat as "user already covered this".
# Normalization strips leading/trailing whitespace and the optional leading
# ``/`` anchor + trailing ``/`` (so a power-user line like ``/agents-shipgate-reports/``
# still counts). Globstar prefixes (``**/``) are intentionally NOT normalized
# away because they semantically differ from a root-anchored ignore.
_EQUIVALENT_TOKENS = frozenset(
    {
        REPORTS_DIR_NAME,
        f"{REPORTS_DIR_NAME}/",
        f"/{REPORTS_DIR_NAME}",
        f"/{REPORTS_DIR_NAME}/",
    }
)


class GitignoreBlockState(StrEnum):
    NO_MARKERS = "no_markers"
    PRESENT = "present"
    AMBIGUOUS = "ambiguous"


class GitignoreUpsertStatus(StrEnum):
    """Result statuses for the in-memory upsert pass."""

    APPENDED = "appended"
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    MIGRATED = "migrated"
    NEWER_VERSION = "newer_version"
    AMBIGUOUS = "ambiguous"


class GitignoreOutcomeStatus(StrEnum):
    """Result statuses for :func:`ensure_reports_gitignore`.

    The first six mirror :class:`GitignoreUpsertStatus` plus
    ``created`` for the empty-file case. The last three describe states the
    in-memory upsert can't reach but a real filesystem call can.
    """

    CREATED = "created"
    APPENDED = "appended"
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    MIGRATED = "migrated"
    SKIPPED_NEWER_VERSION = "skipped_newer_version"
    SKIPPED_AMBIGUOUS = "skipped_ambiguous"
    ALREADY_PRESENT = "already_present"
    SKIPPED_NEGATED = "skipped_negated"
    SKIPPED_SYMLINK = "skipped_symlink"
    SKIPPED_NOT_REGULAR_FILE = "skipped_not_regular_file"
    DRY_RUN = "dry_run"
    ERROR = "error"


@dataclass(frozen=True)
class BlockLocation:
    line_start: int
    line_end: int
    version: int


@dataclass(frozen=True)
class ParsedBlock:
    state: GitignoreBlockState
    location: BlockLocation | None = None


@dataclass(frozen=True)
class UpsertResult:
    new_bytes: bytes
    status: GitignoreUpsertStatus
    block_version: int


@dataclass(frozen=True)
class GitignoreOutcome:
    """Result returned by :func:`ensure_reports_gitignore`.

    Always emitted; the CLI surfaces it in the ``--json`` payload as
    informational. ``status`` drives the human-readable line printed to
    stdout/stderr and the JSON ``message``. ``exit_contribution`` is always
    zero — gitignore writes never block ``init --write``.
    """

    status: GitignoreOutcomeStatus
    path: str
    message: str
    block_version: int = GITIGNORE_BLOCK_VERSION

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "path": self.path,
            "message": self.message,
            "block_version": self.block_version,
        }


# --- pure parsing / rendering ------------------------------------------------


def detect_newline(host: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in host else b"\n"


def parse(host: bytes) -> ParsedBlock:
    """Locate the managed block in ``host``."""
    start_matches = list(START_PATTERN.finditer(host))
    end_matches = list(END_PATTERN.finditer(host))

    if not start_matches and not end_matches:
        return ParsedBlock(state=GitignoreBlockState.NO_MARKERS)
    if len(start_matches) != 1 or len(end_matches) != 1:
        return ParsedBlock(state=GitignoreBlockState.AMBIGUOUS)

    start = start_matches[0]
    end = end_matches[0]
    if start.start() >= end.start():
        return ParsedBlock(state=GitignoreBlockState.AMBIGUOUS)

    line_start = host.rfind(b"\n", 0, start.start()) + 1
    newline_after_end = host.find(b"\n", end.end())
    line_end = len(host) if newline_after_end == -1 else newline_after_end + 1

    return ParsedBlock(
        state=GitignoreBlockState.PRESENT,
        location=BlockLocation(
            line_start=line_start,
            line_end=line_end,
            version=int(start.group(1)),
        ),
    )


def render_block(version: int, newline: bytes) -> bytes:
    """Render the managed block.

    v1 ignores exactly one path. Future versions may add more entries; bump
    :data:`GITIGNORE_BLOCK_VERSION` and append them here. Old blocks auto-
    upgrade on the next ``init --write`` because :func:`upsert` detects the
    version mismatch and rewrites.
    """
    if version < 1:
        raise ValueError(f"block version must be >= 1, got {version}")
    if version != GITIGNORE_BLOCK_VERSION:
        # Defensive: avoid silent renders at unknown versions. Today's only
        # supported version is 1.
        raise ValueError(
            f"unknown block version {version}; this CLI ships v{GITIGNORE_BLOCK_VERSION}"
        )
    nl = newline.decode("ascii")
    return (
        f"# agents-shipgate:start v={version}{nl}"
        f"# Added by `agents-shipgate init --write`. Edit between markers or{nl}"
        f"# remove the markers to opt out of automatic upgrades.{nl}"
        f"{REPORTS_DIR_NAME}/{nl}"
        f"# agents-shipgate:end{nl}"
    ).encode()


def upsert(host: bytes, *, version: int = GITIGNORE_BLOCK_VERSION) -> UpsertResult:
    """Compute new gitignore bytes containing the managed block.

    Pure function. ``host`` may be empty. Bytes outside the block region
    are preserved exactly. Callers should pre-screen for the
    ``already_present`` (variant line) and ``skipped_negated`` cases —
    :func:`upsert` only handles the marker-based block.
    """
    parsed = parse(host)
    if parsed.state is GitignoreBlockState.AMBIGUOUS:
        return UpsertResult(
            new_bytes=host,
            status=GitignoreUpsertStatus.AMBIGUOUS,
            block_version=version,
        )

    nl = detect_newline(host)
    block = render_block(version, nl)

    if parsed.state is GitignoreBlockState.NO_MARKERS:
        if not host:
            return UpsertResult(
                new_bytes=block,
                status=GitignoreUpsertStatus.APPENDED,
                block_version=version,
            )
        prefix = host
        if not prefix.endswith(nl):
            prefix += nl
        if not prefix.endswith(nl + nl):
            prefix += nl
        return UpsertResult(
            new_bytes=prefix + block,
            status=GitignoreUpsertStatus.APPENDED,
            block_version=version,
        )

    assert parsed.location is not None
    loc = parsed.location
    if loc.version > version:
        return UpsertResult(
            new_bytes=host,
            status=GitignoreUpsertStatus.NEWER_VERSION,
            block_version=loc.version,
        )

    new_bytes = host[: loc.line_start] + block + host[loc.line_end :]
    if new_bytes == host:
        return UpsertResult(
            new_bytes=host,
            status=GitignoreUpsertStatus.UNCHANGED,
            block_version=version,
        )
    if loc.version < version:
        return UpsertResult(
            new_bytes=new_bytes,
            status=GitignoreUpsertStatus.MIGRATED,
            block_version=version,
        )
    return UpsertResult(
        new_bytes=new_bytes,
        status=GitignoreUpsertStatus.UPDATED,
        block_version=version,
    )


# --- equivalent-line + negation detection -----------------------------------


def _line_matches_reports_dir(line: str) -> bool:
    """True iff ``line`` already ignores ``agents-shipgate-reports/``.

    Accepts the canonical name + leading/trailing slash variants. Three
    things this deliberately does NOT do:

    * Strip an "inline ``#`` comment." Gitignore only treats lines that
      *start* with ``#`` as comments — a mid-line ``#`` is part of the
      pattern. So ``agents-shipgate-reports/  # legacy line`` is a
      literal pattern matching nothing, not "ignore the reports dir
      with a trailing comment." Treating it as already-present would
      leave the reports directory visible to ``git status`` while we
      refused to append our own block.
    * **Strip leading whitespace.** Gitignore does NOT strip leading
      whitespace from patterns: `` agents-shipgate-reports/`` (one
      leading space) matches nothing, as ``git check-ignore`` confirms.
      We must NOT treat such a line as already-present — that would be
      the same silent-leak bug as the inline-``#`` case. We
      ``rstrip()`` instead of ``strip()`` so trailing whitespace IS
      tolerated (gitignore itself ignores trailing whitespace on
      patterns) while a leading-whitespace line falls through to the
      append branch.
    * Normalize globstar forms like ``**/agents-shipgate-reports/`` —
      they semantically differ from a root-anchored ignore and a user
      using them probably knows what they're doing; redundancy is the
      worst case if we don't match.
    """
    token = line.rstrip()
    if not token or token.startswith("#") or token.startswith("!"):
        return False
    return token in _EQUIVALENT_TOKENS


def _line_negates_reports_dir(line: str) -> bool:
    # Same ``rstrip()``-not-``strip()`` rule as ``_line_matches_reports_dir``:
    # gitignore does not strip leading whitespace, so a line like
    # `` !agents-shipgate-reports/`` is NOT honored as a negation
    # (``git check-ignore`` confirms). Treating it as ``skipped_negated``
    # would refuse to add our managed block under the false belief that
    # the user opted out — leave it to fall through instead.
    token = line.rstrip()
    if not token.startswith("!"):
        return False
    return token[1:] in _EQUIVALENT_TOKENS


def detect_existing_state(host: bytes) -> tuple[bool, bool]:
    """Return ``(already_present, negated)``.

    Scans non-marker lines for the equivalent-line check. Lines INSIDE a
    managed block are excluded so a prior managed-block install isn't
    misclassified as a manual mention.
    """
    parsed = parse(host)
    text = host.decode("utf-8", errors="replace")
    lines = text.splitlines()
    inside_block = False
    has_present = False
    has_negation = False
    for line in lines:
        if START_PATTERN.match(line.encode("utf-8")):
            inside_block = True
            continue
        if END_PATTERN.match(line.encode("utf-8")):
            inside_block = False
            continue
        if inside_block:
            continue
        if _line_matches_reports_dir(line):
            has_present = True
        if _line_negates_reports_dir(line):
            has_negation = True
    # Marker-present + manual line: prefer the marker path (upsert). The
    # ambiguous state still routes through upsert which returns AMBIGUOUS;
    # the caller surfaces SKIPPED_AMBIGUOUS in that case.
    if parsed.state is GitignoreBlockState.PRESENT:
        return False, has_negation
    return has_present, has_negation


# --- filesystem-facing helper -----------------------------------------------


def _first_symlink_in_chain(path: Path, workspace: Path) -> Path | None:
    """Return the first symlink between ``workspace`` and ``path`` (inclusive).

    Same pattern as :func:`agent_instructions.apply._first_symlink_in_chain`.
    Prevents a symlinked directory above ``.gitignore`` from routing the
    write outside the workspace.
    """
    workspace_real = workspace.resolve()
    try:
        relative_parts = path.relative_to(workspace_real).parts
    except ValueError:
        return path
    cur = workspace_real
    for part in relative_parts:
        cur = cur / part
        if cur.is_symlink():
            return cur
        if not cur.exists():
            return None
    return None


def ensure_reports_gitignore(
    workspace: Path,
    *,
    write: bool,
) -> GitignoreOutcome:
    """Ensure ``workspace/.gitignore`` ignores ``agents-shipgate-reports/``.

    ``write=False`` returns a :data:`GitignoreOutcomeStatus.DRY_RUN` outcome
    without touching the filesystem (the JSON-only `init` path uses this).
    """
    workspace = workspace.resolve()
    path = workspace / ".gitignore"
    path_str = str(path)

    if not write:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.DRY_RUN,
            path=path_str,
            message=(
                f"Would ensure {REPORTS_DIR_NAME}/ is ignored in {path} "
                "(re-run with --write to commit)."
            ),
        )

    symlink = _first_symlink_in_chain(path, workspace)
    if symlink is not None:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.SKIPPED_SYMLINK,
            path=path_str,
            message=(
                f"{symlink} is a symlink; refusing to follow it. "
                f"Add {REPORTS_DIR_NAME}/ to your .gitignore manually."
            ),
        )

    if not path.exists():
        # New file: render just the block from an empty host.
        block = render_block(GITIGNORE_BLOCK_VERSION, b"\n")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(block)
        except OSError as exc:
            return GitignoreOutcome(
                status=GitignoreOutcomeStatus.ERROR,
                path=path_str,
                message=(
                    f"Could not create {path}: {exc}. "
                    f"Add {REPORTS_DIR_NAME}/ to your .gitignore manually."
                ),
            )
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.CREATED,
            path=path_str,
            message=f"Created {path} with {REPORTS_DIR_NAME}/ ignored.",
        )

    if not path.is_file():
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.SKIPPED_NOT_REGULAR_FILE,
            path=path_str,
            message=(
                f"{path} exists but is not a regular file; refusing to write. "
                f"Add {REPORTS_DIR_NAME}/ to your .gitignore manually."
            ),
        )

    try:
        host = path.read_bytes()
    except OSError as exc:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.ERROR,
            path=path_str,
            message=(
                f"Could not read {path}: {exc}. "
                f"Add {REPORTS_DIR_NAME}/ to your .gitignore manually."
            ),
        )

    already_present, negated = detect_existing_state(host)
    if negated:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.SKIPPED_NEGATED,
            path=path_str,
            message=(
                f"{path} contains a negation for {REPORTS_DIR_NAME}/; "
                "respecting the explicit opt-out."
            ),
        )
    if already_present:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.ALREADY_PRESENT,
            path=path_str,
            message=f"{path} already ignores {REPORTS_DIR_NAME}/; no change.",
        )

    result = upsert(host)
    if result.status is GitignoreUpsertStatus.AMBIGUOUS:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.SKIPPED_AMBIGUOUS,
            path=path_str,
            message=(
                f"{path} contains ambiguous agents-shipgate markers. "
                f"Resolve manually before re-running."
            ),
        )
    if result.status is GitignoreUpsertStatus.NEWER_VERSION:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.SKIPPED_NEWER_VERSION,
            path=path_str,
            message=(
                f"{path} contains a newer managed block (v{result.block_version}); "
                f"this CLI ships v{GITIGNORE_BLOCK_VERSION}. Upgrade the CLI."
            ),
            block_version=result.block_version,
        )
    if result.status is GitignoreUpsertStatus.UNCHANGED:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.UNCHANGED,
            path=path_str,
            message=f"{path} managed block already up to date (v{result.block_version}).",
            block_version=result.block_version,
        )

    try:
        path.write_bytes(result.new_bytes)
    except OSError as exc:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.ERROR,
            path=path_str,
            message=(
                f"Could not write {path}: {exc}. "
                f"Add {REPORTS_DIR_NAME}/ to your .gitignore manually."
            ),
        )
    if result.status is GitignoreUpsertStatus.APPENDED:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.APPENDED,
            path=path_str,
            message=f"Appended managed block (v{result.block_version}) to {path}.",
            block_version=result.block_version,
        )
    if result.status is GitignoreUpsertStatus.MIGRATED:
        return GitignoreOutcome(
            status=GitignoreOutcomeStatus.MIGRATED,
            path=path_str,
            message=f"Migrated {path} managed block to v{result.block_version}.",
            block_version=result.block_version,
        )
    return GitignoreOutcome(
        status=GitignoreOutcomeStatus.UPDATED,
        path=path_str,
        message=f"Updated managed block (v{result.block_version}) in {path}.",
        block_version=result.block_version,
    )
