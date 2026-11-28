from __future__ import annotations

import errno
import hashlib
import os
import posixpath
import re
import subprocess
import tempfile
import threading
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml

from agents_shipgate import _perf
from agents_shipgate.core.adopter_text import (
    AGENTS_SHIPGATE_ORIGIN,
    GIT_TREE_LISTING_UNREADABLE,
    MANIFEST_SPELLING_COLLISION,
    UNCONTAINED_GIT_TREE_LINK,
    UNSUPPORTED_GIT_TREE_ENTRY,
)
from agents_shipgate.core.boundary_registry import is_agent_boundary_path
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.git_env import hardened_git_env
from agents_shipgate.core.privacy import redact_text, truncate_redacted
from agents_shipgate.core.trust_roots import trust_root_class_for
from agents_shipgate.core.workspace_input import workspace_input_error
from agents_shipgate.schemas.human_authorization import canonical_https_git_endpoint

_GIT_OBJECT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_WORKTREE_FILTER_CONFIG_LIMIT = 1024 * 1024
_WORKTREE_ATTRIBUTE_LIST_LIMIT = 8 * 1024 * 1024
_DIFF_CONFIG_LIMIT = 1024 * 1024
_PROMISOR_CONFIG_LIMIT = 1024 * 1024
_DIFF_METADATA_LIMIT = 8 * 1024 * 1024
_DIFF_BODY_LIMIT = 32 * 1024 * 1024
_GIT_STDERR_LIMIT = 8 * 1024
_GIT_STDERR_EXCERPT_CHARS = 240
_TEXT_CAPABILITY_SUFFIXES = frozenset(
    {
        ".json",
        ".jsonl",
        ".md",
        ".mdc",
        ".mjs",
        ".cjs",
        ".js",
        ".jsx",
        ".py",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)


class BinaryCapabilityDiffError(ConfigError):
    """A diff whose capability-like binary paths cannot be read statically."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = tuple(sorted(paths))
        self.changed_paths: tuple[str, ...] = ()
        self.diff_text = ""
        super().__init__(
            "Git classified source-like changed paths as binary, so their "
            "capability text cannot be evaluated statically: "
            + ", ".join(self.paths[:3])
        )


# Why a requested diff could not be read in full. These are input-acquisition
# states, not verdicts: none of them says anything about what the PR contains.
# ``refs_missing``/``merge_base_missing``/``objects_missing`` are repairable by
# making history or objects locally available; the rest are not.
DiffInputReason = Literal[
    "not_attempted",
    "refs_missing",
    "merge_base_missing",
    "unrelated_histories",
    "objects_missing",
    "metadata_limit_exceeded",
    "body_limit_exceeded",
    "git_timeout",
    "git_failed",
]

# Mirrors ``BoundaryChangeSet.completeness`` so ``check`` and ``verify`` speak
# one vocabulary for partially-read inputs.
DiffCompleteness = Literal["complete", "partial", "unavailable"]

_FETCHABLE_DIFF_REASONS: frozenset[str] = frozenset(
    {"refs_missing", "merge_base_missing", "objects_missing"}
)

#: The hydration example ``objects_missing`` names when no workspace is bound.
DEFAULT_HYDRATE_COMMAND = "git fetch --refetch --no-filter origin"


def objects_missing_remediation(hydrate: str = DEFAULT_HYDRATE_COMMAND) -> str:
    """The one way ``objects_missing`` is repaired, worded once (#817).

    ``verify``'s diff status and ``diff``'s refusal both say this, so the
    repair cannot drift into two spellings. ``hydrate`` is the example command:
    ``diff`` passes one bound to its workspace and the clone's promisor remote.

    ``--no-filter`` is what makes the example work. A bare
    ``git fetch --refetch origin`` applies the clone's configured
    ``remote.<name>.partialclonefilter`` again, so it downloads every commit
    and tree a second time and still no blob, and the same read fails after it.
    """

    return (
        "This checkout is a partial clone and the objects the diff needs were "
        "never fetched. Shipgate runs Git with GIT_NO_LAZY_FETCH=1 and will not "
        f"fetch them implicitly. Hydrate them (for example `{hydrate}`, or "
        "clone without `--filter`), then rerun."
    )


_DIFF_REASON_REMEDIATION: dict[str, str] = {
    "not_attempted": (
        "Verification stopped before it read any diff, so nothing is known "
        "about the change set. Clear the reported blocker and rerun."
    ),
    "refs_missing": (
        "Fetch the missing ref locally (for example "
        "`git fetch --no-tags origin <ref>`), then rerun."
    ),
    "merge_base_missing": (
        "This checkout is shallow, so the merge base the two refs share was "
        "truncated away. Deepen history (`git fetch --deepen=<n>`, or "
        "`git fetch --unshallow` / checkout with `fetch-depth: 0`), then rerun."
    ),
    "unrelated_histories": (
        "The two refs share no common ancestor and this checkout is not "
        "shallow, so no fetch can create one. Confirm the base names the right "
        "comparison point — a force-push or a rewritten branch produces this — "
        "then rerun."
    ),
    "objects_missing": objects_missing_remediation(),
    "metadata_limit_exceeded": (
        "The change set exceeds Shipgate's static diff-metadata bound. Split "
        "the change, or exclude generated output from the compared range."
    ),
    "body_limit_exceeded": (
        "The unified diff exceeds Shipgate's static diff-body bound. Changed "
        "paths were still collected; split the change or exclude generated "
        "output to recover the textual evidence."
    ),
    "git_timeout": (
        "Git did not finish within the static timeout. Inspect repository "
        "size and local Git health before rerunning; fetching refs will not "
        "repair it."
    ),
    "git_failed": (
        "Inspect the reported Git failure before rerunning; fetching refs "
        "cannot repair a deterministic input failure."
    ),
}


@dataclass(frozen=True)
class DiffContext:
    """One diff-acquisition attempt and exactly how complete its result is.

    ``completeness`` is the contract. ``complete`` means every changed path and
    the full unified-diff body were read; ``partial`` means the changed paths
    are authoritative but the textual body is missing or unproven; and
    ``unavailable`` means nothing about the change set was established. A caller
    must never treat ``partial`` or ``unavailable`` evidence as proof that a PR
    is unrelated to agent capabilities.
    """

    changed_files: tuple[str, ...] = ()
    diff_text: str = ""
    completeness: DiffCompleteness = "complete"
    reason: DiffInputReason | None = None
    detail: str = ""

    @property
    def remediation(self) -> str:
        if self.reason is None:
            return ""
        return _DIFF_REASON_REMEDIATION[self.reason]

    @property
    def fetch_repairable(self) -> bool:
        """Whether making refs/objects available locally can repair this."""

        return self.reason in _FETCHABLE_DIFF_REASONS

    @property
    def note(self) -> str:
        """One safe operator-facing line for ``base_notes``."""

        if self.completeness == "complete":
            return ""
        return f"{self.finding} {self.remediation}"

    @property
    def finding(self) -> str:
        """:attr:`note` without its remediation: what failed, and nothing more.

        For a reader whose own way out differs from the one the remediation
        names: the currency check reads the working tree, where "split the
        change" or "fetch the ref" may not be the step that clears it (#813).
        """

        if self.completeness == "complete":
            return ""
        scope = (
            "Changed paths were collected but the diff body could not be read"
            if self.completeness == "partial"
            else "The diff could not be read"
        )
        detail = ""
        if self.detail:
            terminated = self.detail.rstrip()
            if terminated and terminated[-1] not in ".!?":
                terminated += "."
            detail = f" Git reported: {terminated}"
        return f"{scope} ({self.reason}).{detail}"


class DiffInputError(ConfigError):
    """A diff that could not be read in full, with its classified reason."""

    def __init__(self, context: DiffContext) -> None:
        self.context = context
        super().__init__(context.note.strip())


class UnboundGitConfigurationError(ConfigError):
    """Repository Git configuration a static worktree read refuses to run under.

    Executable ``filter.*`` drivers, ``filter=`` attributes on tracked paths,
    repository-local ``diff.*`` configuration and a non-empty
    ``.git/info/attributes`` are not bound into any receipt, so a worktree read
    refuses them rather than letting them rewrite what it reads. They describe
    the repository, not the change: every worktree read of this repository
    refuses the same way until the configuration itself changes, whatever is
    re-run.

    ``finding`` names what was refused — the configuration keys or the paths —
    without the remediation the message appends. The currency check reads the
    working tree for a committed-tree pointer too, so "verify refs" does not
    clear it there, and repeating that advice sent a caller round the same
    refusal (#813).
    """

    def __init__(self, finding: str, *, remediation: str | None = None) -> None:
        self.finding = finding
        super().__init__(f"{finding} {remediation}" if remediation else finding)


class _UnavailableRevisionError(ConfigError):
    """A revision expression that names refs this checkout does not have."""


_SAFE_DIFF_SETTINGS = [
    "core.fsmonitor=false",
    "core.autocrlf=false",
    "core.safecrlf=false",
    "core.eol=lf",
    "core.bigFileThreshold=32m",
    "core.fileMode=false",
    "core.precomposeUnicode=false",
    "submodule.recurse=false",
    "core.quotePath=false",
    f"core.attributesFile={os.devnull}",
    f"diff.orderFile={os.devnull}",
    "diff.suppressBlankEmpty=false",
    "diff.renameLimit=32767",
]
_SAFE_DIFF_CONFIG = [
    argument for setting in _SAFE_DIFF_SETTINGS for argument in ("-c", setting)
]
# Worktree comparisons honor the repository's effective core.fileMode setting.
# Git writes it as false on filesystems that cannot preserve executable bits;
# forcing true there turns ordinary checkouts into whole-repository mode diffs.
_SAFE_WORKTREE_DIFF_CONFIG = [
    argument
    for setting in _SAFE_DIFF_SETTINGS
    if not setting.casefold().startswith("core.filemode=")
    for argument in ("-c", setting)
]
_DETERMINISTIC_DIFF_OPTIONS = [
    "--no-ext-diff",
    "--no-textconv",
    "--ignore-submodules=dirty",
    "--no-color",
    "--diff-algorithm=myers",
    "--no-indent-heuristic",
    "--unified=3",
    "--inter-hunk-context=0",
    "-O",
    os.devnull,
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--find-renames=50%",
    "--submodule=short",
    "--full-index",
]


def ensure_git_workspace(workspace: Path) -> Path:
    """Return the git root containing ``workspace``.

    ``verify`` is a PR-diff workflow, so git is required for base/head
    orchestration. The command remains local-only: all calls are fixed argv
    reads against the existing checkout.

    A path that is not there is classified before git is consulted. Running
    ``git rev-parse`` in a directory that does not exist fails the same way as
    running it in one that is not a repository, and reporting both as "not
    inside a git checkout" asserts the directory exists — which sent a
    first-time user to diagnose their git installation when the real state was
    a workspace they had not cloned yet (#389, and the second instance
    recorded on #384).
    """

    workspace_error = workspace_input_error(workspace)
    if workspace_error is not None:
        raise workspace_error
    result = _run_git(workspace, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        raise ConfigError(f"Workspace is not inside a git checkout: {workspace}")
    root = result.stdout.strip()
    if not root:
        raise ConfigError(f"Workspace is not inside a git checkout: {workspace}")
    return Path(root).resolve()


REMOTE_BASE_CANDIDATES = ("origin/main", "origin/master")
LOCAL_BASE_CANDIDATES = ("main", "master")


@dataclass(frozen=True)
class DefaultBaseDetection:
    base: str | None
    notes: list[str]


@dataclass(frozen=True)
class GitPushEndpoint:
    """One remote selector resolved to an immutable authorization endpoint."""

    selector: str
    push_url: str
    repository_id: str


def commit_sha(workspace: Path, ref: str) -> str | None:
    _validate_ref_token(ref)
    result = _run_git(
        workspace,
        [
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{ref}^{{commit}}",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def active_replace_refs(workspace: Path) -> list[str]:
    """Return active Git object-replacement refs.

    Verification always disables replacement-object resolution, but an
    authorization route also rejects a checkout that contains any replace
    refs.  That keeps the reviewed identity and the later executable source
    unambiguous even for callers that inspect the repository independently.
    """

    result = _run_git(
        workspace,
        ["for-each-ref", "--format=%(refname)", "refs/replace/"],
        check=False,
    )
    if result.returncode != 0:
        raise ConfigError("Could not inspect Git replacement refs")
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


SourceHeadRelation = Literal["evaluated_head", "authorization_ineligible"]


@dataclass(frozen=True)
class SourceHeadIdentity:
    """Resolved evaluated/source commits and their validated relationship."""

    evaluated_head_commit_sha: str
    source_head_commit_sha: str | None
    relation: SourceHeadRelation


def validate_source_head_identity(
    workspace: Path,
    *,
    evaluated_head_commit_sha: str,
    source_head_commit_sha: str | None,
) -> SourceHeadRelation:
    """Require executable source authority to equal the evaluated commit.

    A missing source is a valid receipt state but is authorization-ineligible.
    No parent, ancestry, or host assertion can authorize a distinct commit:
    Git permits a merge commit to use an arbitrary tree unrelated to a parent.
    """

    if not _GIT_OBJECT_RE.fullmatch(evaluated_head_commit_sha):
        raise ValueError("evaluated head must be one full lowercase Git object ID")
    if commit_sha(workspace, evaluated_head_commit_sha) != evaluated_head_commit_sha:
        raise ValueError("the exact evaluated head commit is unavailable locally")
    if source_head_commit_sha is None:
        return "authorization_ineligible"
    if not _GIT_OBJECT_RE.fullmatch(source_head_commit_sha):
        raise ValueError("source head must be one full lowercase Git object ID")
    if commit_sha(workspace, source_head_commit_sha) != source_head_commit_sha:
        raise ValueError("the exact source head commit is unavailable locally")
    if source_head_commit_sha != evaluated_head_commit_sha:
        raise ValueError("authorization source head must equal the evaluated head commit")
    return "evaluated_head"


def resolve_source_head_identity(
    workspace: Path,
    *,
    head_ref: str,
    github_actions: bool = False,
    event_name: str | None = None,
    evaluated_head_sha: str | None = None,
) -> SourceHeadIdentity:
    """Resolve direct authority or mark a synthetic PR merge ineligible.

    Local and explicitly overridden heads authorize only themselves.  The
    default GitHub ``pull_request`` merge commit is useful verification
    evidence, but cannot authorize pushing its distinct PR source; callers
    must explicitly verify that source commit in a separate run.
    """

    evaluated = commit_sha(workspace, head_ref)
    if evaluated is None:
        raise ValueError(f"evaluated head ref is unavailable locally: {head_ref}")
    evaluated_hint = _normalized_sha_hint(
        evaluated_head_sha,
        label="action evaluated head",
    )
    is_default_pr_merge = (
        github_actions
        and (event_name or "").strip() == "pull_request"
        and evaluated_hint == evaluated
    )
    source = None if is_default_pr_merge else evaluated
    relation = validate_source_head_identity(
        workspace,
        evaluated_head_commit_sha=evaluated,
        source_head_commit_sha=source,
    )
    return SourceHeadIdentity(evaluated, source, relation)


def _normalized_sha_hint(value: str | None, *, label: str) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if not _GIT_OBJECT_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be one full lowercase Git object ID")
    return normalized


def detect_default_base(
    workspace: Path,
    head: str = "HEAD",
    *,
    allow_local_when_no_remote: bool = False,
    allow_equal_head: bool = False,
) -> str | None:
    """Best-effort default base ref for PR-style diff enrichment.

    Tries the remote default branch (``origin/HEAD``) first, then remote
    conventional candidates (``origin/main``, ``origin/master``). A
    candidate qualifies only when it exists locally and points at a
    different commit than ``head`` — diffing a branch against itself adds
    scan cost without diff signal. Local ``main``/``master`` are never
    selected implicitly because they are often stale in CI and worktrees;
    pass ``--base main`` explicitly when that is intended. Never fetches;
    this only reads refs that already exist in the checkout.

    ``allow_local_when_no_remote`` narrows that last rule to the case it
    was written for. A local ``main`` is refused because a remote is the
    authority it might be stale against; where the repository has **no
    remote at all**, there is no such authority and the local branch is
    the only base in existence. Refusing there would leave `check` with
    nothing to compare on a repository that was simply never pushed
    (#649). Off by default; `verify` reaches it through
    :func:`resolve_verify_base` only after the remote-only detection found
    nothing (#1023).

    ``allow_equal_head`` keeps an authoritative default at ``head`` eligible
    for callers comparing the working tree or an explicitly requested head.
    This distinguishes a valid empty comparison from missing base evidence.
    """

    return detect_default_base_with_notes(
        workspace,
        head,
        allow_local_when_no_remote=allow_local_when_no_remote,
        allow_equal_head=allow_equal_head,
    ).base


def detect_default_base_with_notes(
    workspace: Path,
    head: str = "HEAD",
    *,
    allow_local_when_no_remote: bool = False,
    allow_equal_head: bool = False,
) -> DefaultBaseDetection:
    """Return the implicit base plus warnings for skipped local defaults."""

    head_sha = commit_sha(workspace, head)
    if head_sha is None:
        return DefaultBaseDetection(base=None, notes=[])
    candidates: list[str] = []
    origin_head = _run_git(workspace, ["rev-parse", "--abbrev-ref", "origin/HEAD"], check=False)
    if origin_head.returncode == 0:
        name = origin_head.stdout.strip()
        if name and name != "origin/HEAD":
            candidates.append(name)
    candidates.extend(c for c in REMOTE_BASE_CANDIDATES if c not in candidates)
    selected_base: str | None = None
    selected_base_sha: str | None = None
    for candidate in candidates:
        sha = commit_sha(workspace, candidate)
        if sha is not None and (allow_equal_head or sha != head_sha):
            selected_base = candidate
            selected_base_sha = sha
            break
    if (
        selected_base is None
        and allow_local_when_no_remote
        and not _has_configured_remote(workspace)
    ):
        for candidate in LOCAL_BASE_CANDIDATES:
            sha = commit_sha(workspace, candidate)
            if sha is not None and (allow_equal_head or sha != head_sha):
                selected_base = candidate
                selected_base_sha = sha
                break
    notes = _skipped_local_base_notes(
        workspace,
        head_sha,
        selected_base_sha=selected_base_sha,
    )
    return DefaultBaseDetection(base=selected_base, notes=notes)


@dataclass(frozen=True)
class VerifyBaseResolution:
    """What ``verify`` compares against when ``--base`` is omitted (#1023).

    ``base`` is the ref to diff against. ``at_head`` names a default branch
    that already points at the head: the comparison resolved, and there is
    nothing committed to compare, so no base scan is spent on it. Both
    ``None`` means no base exists to compare against at all, which ``verify``
    refuses rather than publishing an empty diff as a complete one.
    """

    base: str | None
    notes: list[str]
    at_head: str | None = None

    @property
    def resolved(self) -> bool:
        return self.base is not None or self.at_head is not None


def resolve_verify_base(workspace: Path, head: str = "HEAD") -> VerifyBaseResolution:
    """``check``'s base ladder (#649), keeping every base ``verify`` already chose.

    The remote-only detection runs first and wins whenever it finds a base, so
    a run that had one keeps exactly that one. Only when it finds none is the
    ladder ``check`` uses consulted: a default branch at the head is a
    resolved, empty comparison, and a repository with no remote at all falls
    back to its local ``main`` or ``master``. Anything else is unresolved.
    """

    detection = detect_default_base_with_notes(workspace, head)
    if detection.base is not None:
        return VerifyBaseResolution(base=detection.base, notes=detection.notes)
    ladder = detect_default_base_with_notes(
        workspace,
        head,
        allow_local_when_no_remote=True,
        allow_equal_head=True,
    )
    if ladder.base is None:
        return VerifyBaseResolution(base=None, notes=ladder.notes)
    if commit_sha(workspace, ladder.base) == commit_sha(workspace, head):
        return VerifyBaseResolution(base=None, notes=detection.notes, at_head=ladder.base)
    return VerifyBaseResolution(base=ladder.base, notes=ladder.notes)


def _has_configured_remote(workspace: Path) -> bool:
    """Whether this checkout has any remote to be stale against."""

    result = _run_git(workspace, ["remote"], check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def _skipped_local_base_notes(
    workspace: Path,
    head_sha: str,
    *,
    selected_base_sha: str | None,
) -> list[str]:
    notes: list[str] = []
    for local in LOCAL_BASE_CANDIDATES:
        local_sha = commit_sha(workspace, local)
        if local_sha is None or local_sha == head_sha:
            continue
        if selected_base_sha is not None and local_sha == selected_base_sha:
            continue
        remote = f"origin/{local}"
        remote_sha = commit_sha(workspace, remote)
        if remote_sha is not None and remote_sha == local_sha:
            continue
        if remote_sha is not None and remote_sha != local_sha:
            notes.append(
                f"Skipped local base {local!r} for implicit auto-base because "
                "only remote refs are auto-detected; "
                f"{local!r} points at {_short_sha(local_sha)} while {remote!r} "
                f"points at {_short_sha(remote_sha)}. Pass --base {local} "
                "explicitly if that local branch is intended."
            )
            continue
        notes.append(
            f"Skipped local base {local!r} for implicit auto-base because only "
            "remote refs are auto-detected. Pass --base "
            f"{local} explicitly if that local branch is intended."
        )
    return notes


def _short_sha(sha: str) -> str:
    return sha[:12]


def ref_exists(workspace: Path, ref: str) -> bool:
    return commit_sha(workspace, ref) is not None


def tree_sha(workspace: Path, ref: str) -> str:
    commit = commit_sha(workspace, ref)
    if commit is None:
        raise ConfigError(f"Git ref is unavailable: {ref}")
    result = _run_git(
        workspace,
        ["rev-parse", "--verify", "--end-of-options", f"{commit}^{{tree}}"],
    )
    return result.stdout.strip()


def merge_base_sha(workspace: Path, base: str, head: str) -> str | None:
    base_commit = commit_sha(workspace, base)
    head_commit = commit_sha(workspace, head)
    if base_commit is None or head_commit is None:
        return None
    result = _run_git(
        workspace,
        ["merge-base", "--", base_commit, head_commit],
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def require_merge_base_sha(workspace: Path, base: str, head: str) -> str:
    """Resolve one merge base or raise a typed diff-input failure.

    Worktree verification needs only this ancestry identity before comparing
    the merge-base tree directly with the effective worktree. Reading the
    intermediate committed diff would make canceled or superseded branch
    content an accidental input again.
    """

    base_commit = commit_sha(workspace, base)
    head_commit = commit_sha(workspace, head)
    if base_commit is None or head_commit is None:
        missing = base if base_commit is None else head
        raise DiffInputError(
            DiffContext(
                completeness="unavailable",
                reason="refs_missing",
                detail=f"Git ref {missing!r} is not available locally.",
            )
        )
    result = _run_git(
        workspace,
        ["merge-base", "--", base_commit, head_commit],
        check=False,
    )
    resolved = result.stdout.strip()
    if result.returncode == 0 and _GIT_OBJECT_RE.fullmatch(resolved):
        return resolved
    detail = _redact_local_paths(result.stderr.strip(), workspace)
    if result.returncode == 1:
        truncated = _history_is_truncated(workspace)
        reason: DiffInputReason = (
            "merge_base_missing"
            if truncated is True
            else "unrelated_histories"
            if truncated is False
            else "git_failed"
        )
    else:
        reason, detail = _classify_diff_failure(
            _BoundedGitResult(payload=None, stderr=detail),
            limit_reason="metadata_limit_exceeded",
            workspace=workspace,
        )
    raise DiffInputError(
        DiffContext(
            completeness="unavailable",
            reason=reason,
            detail=detail or "Git could not resolve one merge base.",
        )
    )


def commit_date(workspace: Path, ref: str) -> str:
    commit = commit_sha(workspace, ref)
    if commit is None:
        raise ConfigError(f"Git ref is unavailable: {ref}")
    return _run_git(
        workspace,
        ["show", "-s", "--format=%cs", "--end-of-options", commit],
    ).stdout.strip()


def repository_identity(workspace: Path) -> str:
    """Return a credential-free stable repository locator."""

    remote = _run_git(workspace, ["remote", "get-url", "origin"], check=False)
    value = remote.stdout.strip() if remote.returncode == 0 else ""
    normalized = _normalize_repository_url(value)
    return normalized or f"local:{workspace.name}"


def resolve_git_push_endpoint(workspace: Path, selector: str) -> GitPushEndpoint:
    """Resolve a local remote name once to a canonical HTTPS push endpoint.

    ``selector`` is deliberately not returned as an executable destination.
    Authorization signs the concrete URL and repository identity so later
    mutation of ``remote.<name>`` cannot retarget the operation.
    """

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", selector):
        raise ConfigError("Git remote selector contains an unsafe token")
    result = _run_git(
        workspace,
        ["remote", "get-url", "--push", selector],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ConfigError(f"Git remote {selector!r} has no resolvable push URL")
    raw_url = result.stdout.strip()
    try:
        push_url, repository_id = canonical_https_git_endpoint(raw_url)
    except ValueError as exc:
        raise ConfigError(f"Git remote {selector!r} has an unsafe push URL: {exc}") from exc
    return GitPushEndpoint(
        selector=selector,
        push_url=push_url,
        repository_id=repository_id,
    )


def _normalize_repository_url(value: str) -> str | None:
    """Normalize common HTTPS/SSH Git locators without credentials."""

    if not value:
        return None
    host = ""
    path = ""
    if "://" in value:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        path = parsed.path
    else:
        scp = re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", value)
        if scp:
            host = scp.group(1).lower()
            path = scp.group(2)
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not host or not normalized_path:
        return None
    return f"{host}/{normalized_path}"


def git_path(workspace: Path, path: str) -> Path:
    result = _run_git(workspace, ["rev-parse", "--git-path", path])
    resolved = Path(result.stdout.strip())
    if resolved.is_absolute():
        return resolved.resolve()
    return (workspace / resolved).resolve()


def git_directories(workspace: Path) -> tuple[Path, Path]:
    """Return the checkout-specific Git dir and the repository common dir."""

    values: list[Path] = []
    for flag in ("--git-dir", "--git-common-dir"):
        result = _run_git(workspace, ["rev-parse", flag])
        raw = result.stdout.strip()
        if not raw:
            raise ConfigError(f"Git did not report {flag} for {workspace}")
        value = Path(raw)
        values.append(value.resolve() if value.is_absolute() else (workspace / value).resolve())
    return values[0], values[1]


def diff_context(workspace: Path, base: str, head: str) -> tuple[list[str], str]:
    """Return committed-ref diff paths and body, or raise on any shortfall.

    Callers that can act on a partially-read diff should use
    :func:`collect_diff_context` instead. This wrapper stays strict so a caller
    that cannot represent partial evidence never silently reasons over it.
    """

    return _require_complete(collect_diff_context(workspace, base, head))


def diff_revspec_context(workspace: Path, revspec: str) -> tuple[list[str], str]:
    """Return deterministic committed-ref diff paths and body, or raise."""

    return _require_complete(collect_revspec_diff_context(workspace, revspec))


def _require_complete(context: DiffContext) -> tuple[list[str], str]:
    if context.completeness != "complete":
        raise DiffInputError(context)
    return list(context.changed_files), context.diff_text


def collect_diff_context(workspace: Path, base: str, head: str) -> DiffContext:
    """Collect the ``base...head`` diff and report exactly how complete it is."""

    base_commit = commit_sha(workspace, base)
    head_commit = commit_sha(workspace, head)
    if base_commit is None or head_commit is None:
        missing = base if base_commit is None else head
        return DiffContext(
            completeness="unavailable",
            reason="refs_missing",
            detail=f"Git ref {missing!r} is not available locally.",
        )
    return collect_revspec_diff_context(workspace, f"{base_commit}...{head_commit}")


def collect_revspec_diff_context(workspace: Path, revspec: str) -> DiffContext:
    """Collect a deterministic committed-ref diff without discarding evidence.

    Metadata and body are read separately and reported separately. A body that
    cannot be read no longer throws away the changed-path evidence that was
    successfully collected — a blobless clone, for instance, answers
    ``--name-status`` fully while failing the textual diff, and those paths are
    exactly what tells a caller the PR touches an agent surface.
    """

    _reject_unbound_diff_configuration(workspace)
    try:
        revspec = _resolved_diff_revspec(workspace, revspec)
    except _UnavailableRevisionError as exc:
        return DiffContext(
            completeness="unavailable",
            reason="refs_missing",
            detail=str(exc),
        )
    names = _run_git_bounded_result(
        workspace,
        [
            *_SAFE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            "--name-status",
            "-z",
            revspec,
        ],
        max_output_bytes=_DIFF_METADATA_LIMIT,
    )
    if names.payload is None:
        reason, detail = _classify_diff_failure(
            names, limit_reason="metadata_limit_exceeded", workspace=workspace
        )
        return DiffContext(
            completeness="unavailable", reason=reason, detail=detail
        )
    paths = tuple(sorted(_paths_from_name_status(names.payload)))
    body = _run_git_bounded_result(
        workspace,
        [
            *_SAFE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            revspec,
        ],
        max_output_bytes=_DIFF_BODY_LIMIT,
    )
    if body.payload is None:
        reason, detail = _classify_diff_failure(
            body, limit_reason="body_limit_exceeded", workspace=workspace
        )
        return DiffContext(
            changed_files=paths,
            completeness="partial",
            reason=reason,
            detail=detail,
        )
    diff_text = _decode_diff_body(body.payload)
    try:
        _reject_binary_capability_paths(workspace, revspec)
    except BinaryCapabilityDiffError as exc:
        exc.changed_paths = paths
        exc.diff_text = diff_text
        raise
    except DiffInputError as exc:
        # The binary-hiding guard could not run, so the body is not proven to
        # contain every capability path's text. Keep it, but never as complete.
        return DiffContext(
            changed_files=paths,
            diff_text=diff_text,
            completeness="partial",
            reason=exc.context.reason,
            detail=exc.context.detail,
        )
    return DiffContext(changed_files=paths, diff_text=diff_text)


def _paths_from_name_status(payload: bytes) -> list[str]:
    """Return every path named by a NUL-delimited ``git diff --name-status``.

    Rename and copy records carry two paths.  Both are part of the evaluated
    change: keeping only the destination lets a protected source disappear
    behind an unprotected new name before the verifier classifies it.
    """

    fields = payload.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    paths: list[str] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii", errors="strict")
        index += 1
        path_count = 2 if status[:1] in {"R", "C"} else 1
        if index + path_count > len(fields):
            raise ConfigError("Git returned malformed NUL-delimited diff metadata.")
        for raw_path in fields[index : index + path_count]:
            path = os.fsdecode(raw_path)
            if path and path not in paths:
                paths.append(path)
        index += path_count
    return paths


def _decode_diff_body(payload: bytes) -> str:
    """Decode Git text hunks and ASCII binary markers without ambiguity."""
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ConfigError("Git diff body is not valid UTF-8.") from exc


def _reject_binary_capability_paths(
    workspace: Path,
    revspec: str,
    *,
    pathspec: list[str] | None = None,
    diff_config: list[str] | None = None,
) -> None:
    """Fail closed when a source-like path is hidden behind a binary marker."""

    effective_config = _SAFE_DIFF_CONFIG if diff_config is None else diff_config
    result = _run_git_bounded_result(
        workspace,
        [
            *effective_config,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            "--no-renames",
            "--no-patch",
            "--numstat",
            "-z",
            revspec,
            *(["--", *pathspec] if pathspec else []),
        ],
        max_output_bytes=_DIFF_METADATA_LIMIT,
    )
    payload = result.payload
    if payload is None:
        reason, detail = _classify_diff_failure(
            result, limit_reason="metadata_limit_exceeded", workspace=workspace
        )
        raise DiffInputError(
            DiffContext(completeness="unavailable", reason=reason, detail=detail)
        )
    hidden: list[str] = []
    for record in payload.split(b"\0"):
        if not record:
            continue
        added, separator, remainder = record.partition(b"\t")
        removed, separator_two, raw_path = remainder.partition(b"\t")
        if not separator or not separator_two:
            raise ConfigError("Git returned malformed NUL-delimited numstat metadata.")
        if added != b"-" or removed != b"-":
            continue
        path = os.fsdecode(raw_path)
        if (
            Path(path).suffix.casefold() in _TEXT_CAPABILITY_SUFFIXES
            or is_agent_boundary_path(path)
            or trust_root_class_for(path) is not None
        ):
            hidden.append(path)
    if hidden:
        raise BinaryCapabilityDiffError(hidden)


def read_bytes_at_ref(workspace: Path, ref: str, path: Path) -> bytes | None:
    """One file's exact bytes at ``ref``, or ``None`` if it could not be read.

    Bytes rather than text, because the only caller hashes them: decoding
    normalizes CRLF, and a digest taken after that can never match the one the
    applier wrote from the file on disk — the bug class already fixed once
    inside ``apply-patches`` itself.
    """

    commit = commit_sha(workspace, ref)
    if commit is None:
        return None
    return _run_git_bounded_output(
        workspace,
        ["show", f"{commit}:{path.as_posix()}"],
        max_output_bytes=_MAX_MANIFEST_BYTES,
    )


def read_file_at_ref(workspace: Path, ref: str, path: Path) -> str | None:
    """Return one file's text at ``ref`` without materializing the tree."""

    commit = commit_sha(workspace, ref)
    if commit is None:
        return None
    payload = _run_git_bounded_output(
        workspace,
        ["show", f"{commit}:{path.as_posix()}"],
        max_output_bytes=_MAX_MANIFEST_BYTES,
    )
    if payload is None:
        return None
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def path_present_at_ref(workspace: Path, ref: str, path: Path) -> bool | None:
    """Whether exactly this path exists as a blob at ``ref``.

    ``None`` when git could not answer, so every caller reads it fail-closed.
    Deliberately *not* expressed as ``read_file_at_ref(...) is None``: that
    helper also returns ``None`` for a blob past its read bound, which would
    report an oversize committed gate as absent — the exact confusion #429 was
    filed about, one layer down.

    One bounded ``ls-tree`` on the single path, so a repository's size does not
    enter into it.
    """

    commit = commit_sha(workspace, ref)
    if commit is None:
        return None
    result = _run_git(
        workspace,
        ["ls-tree", "-z", "--full-tree", commit, "--", path.as_posix()],
        check=False,
        text=False,
    )
    if result.returncode != 0:
        return None
    records = [record for record in result.stdout.split(b"\0") if record]
    for record in records:
        header, separator, _encoded_path = record.partition(b"\t")
        if not separator:
            return None
        fields = header.split()
        if len(fields) < 2:
            return None
        if fields[1] == b"blob":
            return True
    return False


@dataclass(frozen=True)
class FileAtRef:
    """One repository file as a commit holds it (#1024).

    ``state`` is ``present`` with ``data`` set, ``absent`` when the commit has
    no entry at exactly this path, or ``unreadable`` with ``reason`` naming why
    (not a regular file, past the read bound, or Git could not answer).
    """

    state: Literal["present", "absent", "unreadable"]
    data: bytes | None = None
    reason: str | None = None


_REGULAR_FILE_MODES = frozenset({b"100644", b"100755"})


def regular_file_at_ref(
    workspace: Path, ref: str, path: Path, *, max_bytes: int
) -> FileAtRef:
    """Read exactly ``path`` at ``ref`` as a regular file, following nothing.

    ``read_bytes_at_ref`` answers ``None`` alike for an absent path, an
    oversize blob and a Git failure, and ``git show`` of a symbolic link
    returns its target text as though it were the file. A comparison reference
    has to tell those apart: an absent one can be replaced by a scan of the
    same commit, while a link or an unreadable one cannot be read at all.
    """

    commit = commit_sha(workspace, ref)
    if commit is None:
        return FileAtRef("unreadable", reason=f"{ref!r} does not resolve to a commit")
    wanted = path.as_posix()
    listing = _run_git_bounded_output(
        workspace,
        ["--literal-pathspecs", "ls-tree", "-z", "--full-tree", commit, "--", wanted],
        max_output_bytes=_MAX_MANIFEST_LISTING_BYTES,
    )
    if listing is None:
        return FileAtRef("unreadable", reason="Git could not list the path")
    for record in (item for item in listing.split(b"\0") if item):
        header, separator, encoded_path = record.partition(b"\t")
        if not separator or encoded_path != wanted.encode("utf-8"):
            continue
        fields = header.split()
        if len(fields) != 3 or fields[1] != b"blob" or fields[0] not in _REGULAR_FILE_MODES:
            return FileAtRef("unreadable", reason="it is not a regular file")
        data = _run_git_bounded_output(
            workspace,
            ["cat-file", "blob", fields[2].decode("ascii")],
            max_output_bytes=max_bytes,
        )
        if data is None:
            return FileAtRef(
                "unreadable",
                reason=f"it could not be read within {max_bytes} bytes",
            )
        return FileAtRef("present", data=data)
    return FileAtRef("absent")


def path_ever_present_from(workspace: Path, ref: str, path: Path) -> bool | None:
    """Whether any commit reachable from ``ref`` ever held exactly ``path``.

    ``False`` only when complete history proves it never did. A shallow
    checkout cannot see what came before its boundary: ``rev-list`` there
    answers with empty output and exit 0, because the boundary is a root, so
    the guard is what keeps that silence from reading as "never". A
    ``$GIT_DIR/info/grafts`` file cuts history the same way while
    ``--is-shallow-repository`` says ``false``, and Git honours it even with
    replace objects disabled, so its presence answers ``None`` too, as does a
    ref or a Git that cannot answer. Every commit that changed the path is
    considered (``--full-history``), so a side branch that added and later
    removed it is not simplified away behind a merge.
    """

    if _history_is_truncated(workspace) is not False:
        return None
    if _graft_file_present(workspace) is not False:
        return None
    commit = commit_sha(workspace, ref)
    if commit is None:
        return None
    result = _run_git(
        workspace,
        [
            "--literal-pathspecs",
            "rev-list",
            "-1",
            "--full-history",
            commit,
            "--",
            path.as_posix(),
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def _graft_file_present(workspace: Path) -> bool | None:
    """Whether the repository carries a ``$GIT_DIR/info/grafts`` file.

    ``None`` when its location cannot be read, which no caller may take as
    "absent". ``GIT_GRAFT_FILE`` cannot move it: every Git run here drops the
    caller's ``GIT_*`` environment.
    """

    result = _run_git(workspace, ["rev-parse", "--git-path", "info/grafts"], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    graft = Path(result.stdout.strip())
    if not graft.is_absolute():
        graft = workspace / graft
    try:
        graft.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return True


#: The longest link text a tree entry is read for. No platform accepts a
#: longer path, so a longer text is never followed.
_MAX_LINK_TEXT_BYTES = 4096

#: How the host reader reaches a file: each link it follows, with that link's
#: own text, and the in-tree path of the regular file it opens (#822).
_ReaderPath = tuple[tuple[tuple[str, str], ...], str]


def _reader_path(
    path: str,
    kind: Callable[[str], str | None],
    link_text: Callable[[str], str | None],
) -> _ReaderPath | None:
    """How the host reader reaches the file at ``path`` on one side, or ``None`` (#822).

    ``kind`` names the entry at an in-tree path without following it:
    ``"tree"``, ``"blob"`` (a regular file) or ``"link"``, and ``None`` for
    anything else or nothing. ``link_text`` is a link entry's own text.

    A link is followed only by rules the reader also applies (#700, the
    reader's ``_resolve_in_tree_link``) and the archive would also resolve it
    by (#711, :func:`_resolve_tree_link`): its text is relative and lands
    inside the tree after normalization, every directory above where it lands
    is a directory and not a link, and the chain ends at a directory or regular
    file within :data:`_MAX_TREE_LINK_HOPS` links. A path holds links at one
    component only, because a directory the reader reads through holds no
    link, and every other component must be a directory. ``None`` means the
    reader would not reach a regular file at ``path`` that way, so nothing
    about it can be proven.

    These are the rules for the links on the way to ``path``, not every
    condition the reader puts on reading a whole linked directory (the
    reader's ``_reads_through_directory_link``: no link anywhere beneath the
    target, no skipped name on the way, a boundary location). So an answer
    other than ``None`` is necessary for the reader to open the file there,
    not sufficient: see :func:`blob_path_unchanged`.
    """

    parts = path.split("/")
    real: list[str] = []
    links: list[tuple[str, str]] = []
    for index, part in enumerate(parts):
        current = "/".join((*real, part))
        current_kind = kind(current)
        if current_kind == "link":
            if links:
                return None
            for _ in range(_MAX_TREE_LINK_HOPS):
                text = link_text(current)
                if not text or "\0" in text or os.path.isabs(text) or text.startswith(("/", "\\")):
                    return None
                joined = posixpath.normpath(posixpath.join(posixpath.dirname(current), text))
                if joined in {".", ".."} or joined.startswith("../"):
                    return None
                links.append((current, text))
                landed = joined.split("/")
                if any(kind("/".join(landed[:depth])) != "tree" for depth in range(1, len(landed))):
                    return None
                current, current_kind = joined, kind(joined)
                if current_kind != "link":
                    break
            else:
                return None
            real = current.split("/")
        else:
            real.append(part)
        if index == len(parts) - 1:
            return (tuple(links), current) if current_kind == "blob" else None
        if current_kind != "tree":
            return None
    return None


class _CommitEntries:
    """One commit's tree entries, each read exactly, by its own `ls-tree`."""

    def __init__(
        self, workspace: Path, commit: str, *, allow_lookup: Callable[[], bool] | None = None,
    ) -> None:
        self._workspace = workspace
        self._commit = commit
        self._allow_lookup = allow_lookup
        self._entries: dict[str, tuple[str, str, str] | None] = {}
        self._link_texts: dict[str, str | None] = {}

    def entry(self, path: str) -> tuple[str, str, str] | None:
        """``(mode, type, oid)`` of exactly this path; ``None`` for none, or a Git failure."""

        if path not in self._entries:
            if self._allow_lookup is not None and not self._allow_lookup():
                return None
            self._entries[path] = self._read(path)
        return self._entries[path]

    def _read(self, path: str) -> tuple[str, str, str] | None:
        # One path per listing: a pathspec that names a directory another
        # pathspec is under lists that directory's children, not the directory.
        result = _run_git(
            self._workspace,
            ["--literal-pathspecs", "ls-tree", "-z", "--full-tree", self._commit, "--", path],
            check=False,
            text=False,
        )
        if result.returncode != 0:
            return None
        records = [record for record in result.stdout.split(b"\0") if record]
        if len(records) != 1:
            return None
        header, separator, name = records[0].partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3 or name != path.encode("utf-8"):
            return None
        mode, object_type, oid = (field.decode("ascii", errors="replace") for field in fields)
        if not _GIT_OBJECT_RE.fullmatch(oid):
            return None
        return mode, object_type, oid

    def kind(self, path: str) -> str | None:
        entry = self.entry(path)
        if entry is None:
            return None
        mode, object_type, _oid = entry
        if object_type == "tree" and mode == "040000":
            return "tree"
        if object_type == "blob" and mode in {"100644", "100755"}:
            return "blob"
        if object_type == "blob" and mode == "120000":
            return "link"
        return None

    def link_text(self, path: str) -> str | None:
        entry = self.entry(path)
        if entry is None or entry[:2] != ("120000", "blob"):
            return None
        oid = entry[2]
        if oid not in self._link_texts:
            if self._allow_lookup is not None and not self._allow_lookup():
                return None
            self._link_texts[oid] = self._read_link_text(oid)
        return self._link_texts[oid]

    def _read_link_text(self, oid: str) -> str | None:
        data = _run_git_bounded_output(
            self._workspace, ["cat-file", "blob", oid], max_output_bytes=_MAX_LINK_TEXT_BYTES
        )
        if data is None:
            return None
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None

    def reader_path(self, path: str) -> _ReaderPath | None:
        # A regular blob listed under the path's own name has no link above
        # it, because a listing never descends through one. That is the one
        # listing a path with no link took before #822, and still takes.
        if self.kind(path) == "blob":
            return (), path
        return _reader_path(path, self.kind, self.link_text)


def _worktree_reader_path(workspace: Path, path: str) -> _ReaderPath | None:
    """How the reader reaches ``path`` in the working tree, read without following a link."""

    from agents_shipgate.core.trust_roots import _directory_member_kind

    kinds = {"directory": "tree", "file": "blob", "symlink": "link"}

    def kind(relative: str) -> str | None:
        try:
            return kinds.get(_directory_member_kind(workspace / relative))
        except OSError:
            return None

    def link_text(relative: str) -> str | None:
        try:
            return os.readlink(workspace / relative)
        except (OSError, ValueError):
            return None

    return _reader_path(path, kind, link_text)


def blob_path_unchanged(workspace: Path, base: str, head: str | None, path: str) -> bool:
    """Whether the file the host reader opens at ``path`` is the same at ``base`` and ``head``.

    ``head=None`` means the working tree. Both sides must reach that file the
    same way. With no link on the path, it is the same regular file at
    ``path``, as before. Through an in-tree link (#822), such as
    ``.claude/skills -> ../.agents/skills``, every link on the way must be a
    link on both sides with the same text, every other component a
    directory, and the file it lands on the same regular file at the same
    in-tree path. Links are followed only by the rules :func:`_reader_path`
    states, which the reader and the archive also apply to the links on the
    way.

    ``True`` is necessary for naming a limit on ``path`` as unchanged, not
    sufficient: this does not model every condition the reader puts on reading
    a whole linked directory, so it can answer ``True`` where one side's reader
    does not read ``path`` at all, such as a head that adds a link inside
    ``.agents/skills``. Its callers ask only about a limit both sides carry on
    ``path``, and a side whose reader does not read ``path`` carries none
    there: in that example it carries an ``unreadable`` limit on the link
    instead, which is never named as unchanged (only that side carries it,
    and a link at `.claude/skills` is where a host reads configuration, so
    :func:`unread_link_unchanged` never proves it, #1004), so the comparison
    still refuses whatever this answers.

    The answer is ``False`` whenever identity cannot be proven: a path absent
    on either side, a link added, removed or given another text (even one that
    lands on the same file), a link that leaves the repository, dangles, loops
    or passes the hop bound, a tree or submodule, an unreadable file, or any
    Git failure. The base is always read from its Git tree entries, and so is a
    commit head. A working tree is read without following any link, each link
    by its own text and the file by its unfiltered hash, against the base's
    entries. Blob object IDs are compared rather than `git diff` output, so a
    ``.gitattributes`` filter or textconv cannot make two different byte
    sequences read as equal (#721).
    """

    from pathlib import PurePosixPath

    relative = PurePosixPath(path)
    if (
        not path
        or relative.is_absolute()
        or relative.as_posix() != path
        or ".." in relative.parts
        or "\\" in path
        or "\0" in path
    ):
        return False
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        return False

    base_commit = commit_sha(workspace, base)
    if base_commit is None:
        return False
    base_tree = _CommitEntries(workspace, base_commit)
    before = base_tree.reader_path(path)
    base_entry = base_tree.entry(before[1]) if before is not None else None
    if before is None or base_entry is None:
        return False
    if head is not None:
        head_commit = commit_sha(workspace, head)
        if head_commit is None:
            return False
        head_tree = _CommitEntries(workspace, head_commit)
        return head_tree.reader_path(path) == before and head_tree.entry(before[1]) == base_entry
    if _worktree_reader_path(workspace, path) != before:
        return False
    hashed = _run_git(workspace, ["hash-object", "--no-filters", "--", before[1]], check=False)
    return hashed.returncode == 0 and hashed.stdout.strip() == base_entry[2]


def tree_path_absent(workspace: Path, base: str, head: str | None, path: str) -> bool:
    """Whether nothing is at ``path`` at ``base`` and at ``head`` (#702).

    ``head=None`` means the working tree. On each side every component above
    the first missing one must be a directory: a gitlink, link or file on the
    way is not absence, since the file may live behind it, and neither is a
    Git failure. In the working tree no component may be a link or other
    non-directory either, and the index may stage nothing at the path or at
    a component above it, such as a submodule not checked out. ``False``
    whenever absence is not proven, so a selected hook script missing on both
    sides is an unchanged limit only when this proves it (the #822 rule for
    limits identical on both sides).
    """

    from pathlib import PurePosixPath

    relative = PurePosixPath(path)
    if (
        not path
        or relative.is_absolute()
        or relative.as_posix() != path
        or ".." in relative.parts
        or "\\" in path
        or "\0" in path
    ):
        return False
    try:
        encoded = path.encode("utf-8")
    except UnicodeEncodeError:
        return False
    prefixes = ["/".join(relative.parts[: index + 1]) for index in range(len(relative.parts))]

    def commit_absent(ref: str) -> bool:
        commit = commit_sha(workspace, ref)
        if commit is None:
            return False
        for prefix in prefixes:
            listed = _run_git(
                workspace,
                ["--literal-pathspecs", "ls-tree", "-z", "--full-tree", commit, "--", prefix],
                check=False,
                text=False,
            )
            if listed.returncode != 0:
                return False
            records = [record for record in listed.stdout.split(b"\0") if record]
            if not records:
                return True
            header, separator, name = records[0].partition(b"\t")
            if (
                len(records) != 1
                or not separator
                or name != prefix.encode("utf-8")
                or header.split()[:2] != [b"040000", b"tree"]
            ):
                return False
        return False

    if not commit_absent(base):
        return False
    if head is not None:
        return commit_absent(head)
    import stat

    for prefix in prefixes:
        try:
            mode = os.lstat(workspace / prefix).st_mode
        except FileNotFoundError:
            break
        except OSError:
            return False
        if not stat.S_ISDIR(mode):
            return False
    else:
        return False
    staged = _run_git(
        workspace,
        ["--literal-pathspecs", "ls-files", "--stage", "-z", "--", *prefixes],
        check=False,
        text=False,
    )
    if staged.returncode != 0:
        return False
    names = {prefix.encode("utf-8") for prefix in prefixes}
    for record in staged.stdout.split(b"\0"):
        if not record:
            continue
        _header, separator, name = record.partition(b"\t")
        if not separator or name in names or name.startswith(encoded + b"/"):
            return False
    return True


#: Bounds on the directory an unchanged link names (#1004): the files its Git
#: tree may hold, the bytes of their listing, and the bytes a working tree's
#: copy may hold for hashing. Past any of them the link is not proven
#: unchanged and keeps its limit.
_LINKED_DIRECTORY_FILES = 10_000
_LINKED_DIRECTORY_LISTING_BYTES = 8 * 1024 * 1024
_LINKED_DIRECTORY_BYTES = 64 * 1024 * 1024
#: What one comparison examines beneath all its unread directory links
#: together (#1004 review): the files their directories hold, and the
#: working-tree bytes it hashes. A directory several links name counts once.
#: Past either, every link not yet proven keeps its limit, and its coverage
#: detail says why.
_LINKED_DIRECTORIES_FILE_BUDGET = 20_000
_LINKED_DIRECTORIES_BYTE_BUDGET = 128 * 1024 * 1024
#: Bounds per-entry Git work even when many aliases name the same directory.
_LINKED_DIRECTORIES_LOOKUP_BUDGET = 1024
_LINKED_DIRECTORY_LOCATION_ENTRIES = 60_000

#: Why a link was not proven unchanged, where a bound decided it (#1004).
#: Appended to the refused comparison's coverage detail for that link.
LINKED_DIRECTORY_PAST_BOUND = (
    "It was not proven unchanged because reading the directory exceeded "
    "the file, entry, or byte limit for one link."
)
LINKED_DIRECTORIES_BUDGET_SPENT = (
    "It was not proven unchanged: this comparison already examined "
    f"{_LINKED_DIRECTORIES_FILE_BUDGET:,} files or "
    f"{_LINKED_DIRECTORIES_BYTE_BUDGET // (1024 * 1024)} MiB beneath other unread "
    "directory links, its bound, so this link was not examined."
)
LINKED_DIRECTORIES_LOOKUP_BUDGET_SPENT = (
    "It was not proven unchanged because the directory-link proof reached "
    f"its limit of {_LINKED_DIRECTORIES_LOOKUP_BUDGET:,} Git lookups."
)
_UNEXAMINED_LINK_LIMIT = (
    "The directory-link proof did not examine this path after reaching its comparison budget."
)


def _linked_directory(
    link: str, text: str | None, kind: Callable[[str], str | None]
) -> str | None:
    """The in-tree directory a link's own text names, walked as a filesystem walks it (#1004).

    ``kind`` names an in-tree entry without following it, as for
    :func:`_reader_path`: ``"tree"``, ``"blob"``, ``"link"`` or ``None``. One
    hop only. The text must be relative, ``..`` may not rise above the
    repository, and every component it passes through, and the one it ends
    on, must be a directory: never a link (a chain), a file or nothing. Each
    component is walked from where the last one landed, so ``missing/../dir``
    fails at ``missing`` as the filesystem fails it, although it normalizes to
    ``dir``. ``None`` as well for a text that is empty, absolute or holds an
    empty component (one trailing ``/`` is allowed: it asks for the directory
    that is required anyway), for the repository root, and for a directory
    that holds the link itself (a loop).
    """

    if not text or "\0" in text or "\\" in text or text.startswith("/") or os.path.isabs(text):
        return None
    components = text.split("/")
    if len(components) > 1 and components[-1] == "":
        components.pop()
    parts = link.split("/")[:-1]
    for component in components:
        if component == "":
            return None
        if component == ".":
            continue
        if component == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(component)
        if kind("/".join(parts)) != "tree":
            return None
    if not parts:
        return None
    target = "/".join(parts)
    if link == target or link.startswith(f"{target}/"):
        return None
    return target


#: :func:`_linked_directory_files` and :func:`_worktree_directory_holds`
#: answer this when a bound, not the content, decided (#1004 review).
_PAST_BOUND = "past_bound"


def _linked_directory_files(
    workspace: Path, tree: str
) -> dict[str, tuple[str, str]] | Literal["past_bound"] | None:
    """Every file one Git tree holds, recursively: ``{path: (mode, blob)}`` (#1004).

    ``None`` when the tree holds a link or a submodule, whose content no tree
    identity binds, or on any Git failure. ``"past_bound"`` when it holds more
    than :data:`_LINKED_DIRECTORY_FILES` files, or a listing past its byte
    bound.
    """

    if not _GIT_OBJECT_RE.fullmatch(tree):
        return None
    result = _run_git_bounded_result(
        workspace,
        ["ls-tree", "-r", "-z", "--full-tree", tree],
        max_output_bytes=_LINKED_DIRECTORY_LISTING_BYTES,
    )
    if result.payload is None:
        return _PAST_BOUND if result.exceeded else None
    files: dict[str, tuple[str, str]] = {}
    for record in result.payload.split(b"\0"):
        if not record:
            continue
        header, separator, name = record.partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3:
            return None
        mode, object_type, oid = (field.decode("ascii", errors="replace") for field in fields)
        if object_type != "blob" or mode not in {"100644", "100755"} or not _GIT_OBJECT_RE.fullmatch(oid):
            return None
        try:
            files[name.decode("utf-8")] = (mode, oid)
        except UnicodeDecodeError:
            return None
        if len(files) > _LINKED_DIRECTORY_FILES:
            return _PAST_BOUND
    return files


def _worktree_directory_holds(
    workspace: Path, directory: str, files: dict[str, tuple[str, str]], *, max_bytes: int
) -> tuple[bool | Literal["past_bound"], int]:
    """Whether a working-tree directory holds exactly ``files``, read without following a link (#1004).

    Returns the answer and the bytes it read, or a count over the byte cap
    when it hit that cap. Every entry beneath the
    directory is typed by its own entry: a link, a special file or anything
    that cannot be typed fails, and so does a file Git's tree does not list,
    untracked or ignored alike. A directory holding no file is not compared,
    as Git records none. Each file must have the tree's mode (executable or
    not) and, by its unfiltered hash, the tree's blob.

    One exception (#1004 review): a directory or link named as one the
    repository walk never enters (`_WALK_SKIPPED_DIRECTORIES`: `.git`,
    `node_modules`, `.venv`, `__pycache__`, `.pytest_cache` and the like) is
    not walked, so build output and caches a local run leaves there do not
    count as a change. That walk is the one every host's reader enumerates
    its sources from, so no reader reads a configuration file beneath such
    a name, at its real path or through the link. A file another reader opens
    by a reference (a hook script, an OpenShell policy) is read and compared
    at its own path, or carries its own limit through the link, either way
    apart from this one. A file Git tracks beneath such a name still counts:
    it is checked by its own entries, each directory above it included.

    ``"past_bound"`` when the directory holds more entries, or more bytes
    than ``max_bytes``, than this compares.
    """

    import stat

    from agents_shipgate.core.host_grants import _WALK_SKIPPED_DIRECTORIES
    from agents_shipgate.core.trust_roots import (
        IdentityBoundReadSession,
        IdentityReadBudgetExceeded,
    )

    root = Path(directory)
    if root.drive:
        return False, 0
    entry_limit = 2 * _LINKED_DIRECTORY_FILES
    # The session also inventories lexical ancestors and directories containing
    # tracked files beneath a skipped name, and revalidates each inventory.
    session_entry_limit = 3 * entry_limit
    found: set[str] = set()
    skipped: set[Path] = set()
    visited = 0
    pending = [root]
    try:
        reader = IdentityBoundReadSession(
            workspace, max_entries=session_entry_limit, max_total_bytes=max_bytes
        )
        while pending:
            parent = pending.pop()
            names = reader.directory_entries(parent, max_entries=entry_limit - visited)
            visited += len(names)
            for name in names:
                child = parent / name
                kind = reader.directory_entry_kind(child)
                if name in _WALK_SKIPPED_DIRECTORIES and kind in {"directory", "symlink"}:
                    skipped.add(child)
                elif kind == "directory":
                    pending.append(child)
                elif kind == "file":
                    relative = child.relative_to(root).as_posix()
                    if relative not in files:
                        return False, 0
                    found.add(relative)
                else:
                    return False, 0
    except IdentityReadBudgetExceeded:
        return _PAST_BOUND, 0
    except (OSError, ValueError, NotImplementedError):
        return False, 0

    try:
        for name, (mode, oid) in sorted(files.items()):
            path = Path(name)
            if path.drive or path.is_absolute() or ".." in path.parts:
                return False, reader.bytes_read
            child = root / path
            if name not in found and not any(parent in skipped for parent in child.parents):
                return False, reader.bytes_read
            content, file_mode = reader.read_bytes_and_mode(child)
            executable = bool(file_mode & stat.S_IXUSR)
            if executable != (mode == "100755"):
                return False, reader.bytes_read
            # Git's unfiltered blob identity prefixes the exact bytes with
            # their type and length, using the repository's object format.
            algorithm = "sha1" if len(oid) == 40 else "sha256"
            digest = hashlib.new(algorithm, usedforsecurity=False)
            digest.update(f"blob {len(content)}\0".encode("ascii"))
            digest.update(content)
            if digest.hexdigest() != oid:
                return False, reader.bytes_read
        reader.finish()
    except IdentityReadBudgetExceeded:
        # Keep the caller's distinction between an entry bound and a byte
        # bound even when the reader could reject the file before opening it.
        if reader.entries_scanned > session_entry_limit:
            return _PAST_BOUND, reader.bytes_read
        return _PAST_BOUND, max_bytes + 1
    except (OSError, ValueError, NotImplementedError):
        return False, reader.bytes_read
    return True, reader.bytes_read


@dataclass
class _LinkedTarget:
    """What one comparison learned about one directory its links name (#1004 review).

    Keyed by the directory's path and its base tree object, so every link
    naming it shares one listing, one registry match and one working-tree
    check, and the budget counts it once.
    """

    files: dict[str, tuple[str, str]] | None
    #: Why it is not proven, when a bound decided: one of the two sentences.
    bound: str | None = None
    #: Whether a path beneath it, as a link spells it, is configuration.
    holds_configuration: bool = False
    #: The working-tree check, once asked.
    worktree: bool | None = None


class UnreadLinkProver:
    """:func:`unread_link_unchanged` for one comparison (#1004).

    A link the reader leaves unread is a limit under every host whose `**/`
    glob it may conceal, and several links may name one directory, so each
    link is answered once and each directory examined once, by its path and
    tree object. All of it shares one budget,
    :data:`_LINKED_DIRECTORIES_FILE_BUDGET` files and
    :data:`_LINKED_DIRECTORIES_BYTE_BUDGET` working-tree bytes: past it,
    every link not yet proven keeps its limit, and :meth:`reason` says so for
    the comparison's coverage.
    """

    def __init__(self, workspace: Path, base: str, head: str | None) -> None:
        from agents_shipgate.core.trust_roots import IdentityReadBudget

        self._workspace = workspace
        self._base = base
        self._head = head
        self._answers: dict[str, bool] = {}
        self._reasons: dict[str, str] = {}
        self._targets: dict[tuple[str, str], _LinkedTarget] = {}
        self._commits: dict[str, _CommitEntries | None] = {}
        self._files_left = _LINKED_DIRECTORIES_FILE_BUDGET
        self._bytes_left = _LINKED_DIRECTORIES_BYTE_BUDGET
        self._lookups_left = _LINKED_DIRECTORIES_LOOKUP_BUDGET
        self._lookup_limit_reached = False
        self._spent = False
        self._location_budget = IdentityReadBudget(
            max_entries=_LINKED_DIRECTORY_LOCATION_ENTRIES, max_total_bytes=0,
        )

    def __call__(self, path: str) -> bool:
        if path not in self._answers:
            proven = not self._lookup_limit_reached and self._prove(path)
            if not proven and self._lookup_limit_reached:
                self._reasons.setdefault(path, LINKED_DIRECTORIES_LOOKUP_BUDGET_SPENT)
            self._answers[path] = proven
        return self._answers[path]

    def reason(self, path: str) -> str | None:
        """Why ``path`` was not proven unchanged, where a bound decided it.

        Also for a link never asked once the budget was spent: a comparison
        stops at the first limit it cannot name, so the ones after it were not
        examined.
        """

        from agents_shipgate.core.boundary_registry import is_host_configuration_location

        if path in self._reasons:
            return self._reasons[path]
        if path in self._answers or is_host_configuration_location(path):
            return None
        if self._lookup_limit_reached:
            return LINKED_DIRECTORIES_LOOKUP_BUDGET_SPENT
        return _UNEXAMINED_LINK_LIMIT if self._spent else None

    def _allow_lookup(self) -> bool:
        if self._lookups_left == 0:
            self._lookup_limit_reached = True
            return False
        self._lookups_left -= 1
        return True

    def _entries(self, ref: str) -> _CommitEntries | None:
        if ref not in self._commits:
            commit = commit_sha(self._workspace, ref)
            self._commits[ref] = (
                _CommitEntries(self._workspace, commit, allow_lookup=self._allow_lookup)
                if commit else None
            )
        return self._commits[ref]

    def _target(self, link: str, target: str, tree: str) -> _LinkedTarget:
        from agents_shipgate.core.boundary_registry import is_host_configuration_location

        key = (target, tree)
        if key in self._targets:
            return self._targets[key]
        if not self._allow_lookup():
            return _LinkedTarget(files=None, bound=LINKED_DIRECTORIES_LOOKUP_BUDGET_SPENT)
        listed = _linked_directory_files(self._workspace, tree)
        if listed == _PAST_BOUND:
            facts = _LinkedTarget(files=None, bound=LINKED_DIRECTORY_PAST_BOUND)
        elif listed is None:
            facts = _LinkedTarget(files=None)
        elif len(listed) > self._files_left:
            self._spent = True
            facts = _LinkedTarget(files=None, bound=LINKED_DIRECTORIES_BUDGET_SPENT)
        else:
            self._files_left -= len(listed)
            # Asked of the first link that names this directory, and true of
            # every other: no link reaches here at a configuration location,
            # and beneath a link that is not one, whether a path is
            # configuration depends on its part below the link alone
            # (`tests/test_unchanged_link_limits.py` pins it).
            facts = _LinkedTarget(
                files=listed,
                holds_configuration=any(
                    is_host_configuration_location(f"{link}/{name}") for name in listed
                ),
            )
        self._targets[key] = facts
        return facts

    def _worktree_holds(self, facts: _LinkedTarget, target: str) -> bool:
        if facts.files is None:
            return False
        if facts.worktree is None:
            per_link = _LINKED_DIRECTORY_BYTES
            cap = min(per_link, self._bytes_left)
            held, hashed = _worktree_directory_holds(
                self._workspace, target, facts.files, max_bytes=cap
            )
            self._bytes_left = max(self._bytes_left - hashed, 0)
            if held == _PAST_BOUND:
                if cap < per_link and hashed > cap:
                    self._spent = True
                    facts.bound = LINKED_DIRECTORIES_BUDGET_SPENT
                else:
                    facts.bound = LINKED_DIRECTORY_PAST_BOUND
            facts.worktree = held is True
        return facts.worktree

    def _prove(self, path: str) -> bool:
        from pathlib import PurePosixPath

        from agents_shipgate.core.boundary_registry import is_host_configuration_location

        relative = PurePosixPath(path)
        if (
            not path
            or relative.is_absolute()
            or relative.as_posix() != path
            or ".." in relative.parts
            or "\\" in path
            or "\0" in path
        ):
            return False
        try:
            path.encode("utf-8")
        except UnicodeEncodeError:
            return False
        if is_host_configuration_location(path):
            return False
        base_tree = self._entries(self._base)
        if base_tree is None:
            return False
        above = ["/".join(relative.parts[:index]) for index in range(1, len(relative.parts))]
        if any(base_tree.kind(prefix) != "tree" for prefix in above) or base_tree.kind(path) != "link":
            return False
        text = base_tree.link_text(path)
        target = _linked_directory(path, text, base_tree.kind)
        target_entry = base_tree.entry(target) if target is not None else None
        if target is None or target_entry is None:
            return False
        if self._spent and (target, target_entry[2]) not in self._targets:
            self._reasons[path] = LINKED_DIRECTORIES_BUDGET_SPENT
            return False
        facts = self._target(path, target, target_entry[2])
        if facts.files is None or facts.holds_configuration:
            if facts.bound is not None:
                self._reasons[path] = facts.bound
            return False
        if self._head is not None:
            head_tree = self._entries(self._head)
            return (
                head_tree is not None
                and all(head_tree.kind(prefix) == "tree" for prefix in above)
                and head_tree.entry(path) == base_tree.entry(path)
                and _linked_directory(path, head_tree.link_text(path), head_tree.kind) == target
                and head_tree.entry(target) == target_entry
            )

        from agents_shipgate.core.trust_roots import (
            IdentityBoundReadSession,
            IdentityReadBudgetExceeded,
        )

        kinds = {"directory": "tree", "file": "blob", "symlink": "link"}
        try:
            reader = IdentityBoundReadSession(self._workspace, budget=self._location_budget)

            def kind(entry: str) -> str | None:
                return kinds.get(reader.directory_entry_kind(Path(entry)))

            if any(kind(prefix) != "tree" for prefix in above) or kind(path) != "link":
                return False
            worktree_text = reader.link_target(Path(path))
            if worktree_text != text or _linked_directory(path, worktree_text, kind) != target:
                return False
            held = self._worktree_holds(facts, target)
            if held:
                reader.finish()
        except IdentityReadBudgetExceeded:
            self._reasons[path] = "The comparison reached its working-tree link location entry limit."
            return False
        except (OSError, ValueError, NotImplementedError):
            return False
        if not held and facts.bound is not None:
            self._reasons[path] = facts.bound
        return held


def unread_link_unchanged(workspace: Path, base: str, head: str | None, path: str) -> bool:
    """Whether ``path`` is one unchanged in-tree directory link, left unread (#1004).

    The reader reads through a directory link only at a boundary location
    (#700); anywhere else, such as `.happy/terraform/envs/dev/modules ->
    ../../modules`, it may conceal a `**/` match, so the reader names it as an
    ``unreadable`` limit. This proves, from Git objects and never by following
    the link, that such a limit hides no change and no configuration. All of
    these must hold:

    - **The link.** On both sides an entry of mode ``120000`` at ``path``
      with the same blob, so the same text, and a directory at every
      component above it.
    - **Where it lands.** Its text names, in one hop, a directory inside the
      repository that does not hold the link (:func:`_linked_directory`), the
      same directory on both sides, with the same Git tree object, so the same
      files, modes and bytes beneath it.
    - **What it holds.** No link or submodule anywhere beneath it, which no
      tree identity binds, and at most :data:`_LINKED_DIRECTORY_FILES` files.
    - **Not configuration.** Neither ``path`` nor any path beneath it, as the
      link spells it, is one a host reads for agent configuration
      (:func:`~agents_shipgate.core.boundary_registry.is_host_configuration_location`),
      so configuration cannot be moved behind an unchanged link.

    ``head=None`` means the working tree, whose head is read without following
    any link: each component by its own entry, the link by its own text, and
    every file beneath the directory it names by its own entry and unfiltered
    hash, which must match the base's tree exactly, untracked and ignored files
    included, except beneath a name the repository walk never enters
    (:func:`_worktree_directory_holds`). The base and a commit head are read
    from their tree entries. The answer is ``False`` whenever any of this
    cannot be proven: a link added, removed, retargeted or rewritten, a
    changed directory, a dangling, looping, absolute or escaping link, a
    chain, a bound reached, or any Git failure.

    One question on its own; a comparison asks through
    :class:`UnreadLinkProver`, which shares work and a budget across links.
    """

    return UnreadLinkProver(workspace, base, head)(path)


def unread_link_prover(workspace: Path, base: str, head: str | None) -> UnreadLinkProver:
    """The :class:`UnreadLinkProver` one comparison asks (#1004)."""

    return UnreadLinkProver(workspace, base, head)


#: Bounds on what :func:`blob_path_identities` reads (#812): one tree listing,
#: each base blob and working-tree file it compares (the host reader's own
#: per-file bound), and those base blobs together.
_IDENTITY_LISTING_BYTES = 8 * 1024 * 1024
_IDENTITY_FILE_BYTES = 1024 * 1024
_IDENTITY_BATCH_BYTES = 64 * 1024 * 1024
#: The pathspec bytes one `ls-tree` command line carries, well inside the
#: shortest platform command-line limit (Windows, 32,767 characters).
_IDENTITY_ARGUMENT_BYTES = 8 * 1024
#: Checkout conversions `.gitattributes` can apply besides line endings. A
#: working-tree difference on a path that carries one is never called a
#: change: it may be the conversion alone (#812).
_CONVERSION_ATTRIBUTES = ("filter", "ident", "working-tree-encoding")


@dataclass(frozen=True)
class _TreeBlob:
    mode: str
    oid: str
    size: int


def _regular_tree_blobs(
    workspace: Path, commit: str, paths: Sequence[str]
) -> dict[str, _TreeBlob] | None:
    """The regular-file blob at each path in one commit, from one listing.

    A path that is absent, a symlink, a tree or a submodule has no entry, and
    so does one whose listing is not exactly one record under its own name.
    The paths are passed as literal pathspecs, split only so no command line
    grows past a size every platform accepts. ``None`` means a listing failed.
    """

    wanted = {path.encode("utf-8"): path for path in paths}
    records: dict[str, list[list[bytes]]] = {}
    chunk: list[str] = []
    length = 0
    for index, path in enumerate(paths):
        chunk.append(path)
        length += len(path.encode("utf-8")) + 3
        if index + 1 < len(paths) and length < _IDENTITY_ARGUMENT_BYTES:
            continue
        listing = _run_git_bounded_output(
            workspace,
            ["--literal-pathspecs", "ls-tree", "-l", "-z", "--full-tree", commit, "--", *chunk],
            max_output_bytes=_IDENTITY_LISTING_BYTES,
        )
        if listing is None:
            return None
        for record in listing.split(b"\0"):
            header, separator, name = record.partition(b"\t")
            if separator and name in wanted:
                records.setdefault(wanted[name], []).append(header.split())
        chunk, length = [], 0
    entries: dict[str, _TreeBlob] = {}
    for path, found in records.items():
        if len(found) != 1 or len(found[0]) != 4:
            continue
        mode, kind, oid, size = (field.decode("ascii", errors="replace") for field in found[0])
        regular = kind == "blob" and mode in {"100644", "100755"}
        if regular and _GIT_OBJECT_RE.fullmatch(oid) and size.isdigit():
            entries[path] = _TreeBlob(mode=mode, oid=oid, size=int(size))
    return entries


def _c_quoted_path(path: str) -> bytes:
    """One line of `hash-object --stdin-paths` input that Git unquotes to exactly ``path``."""

    quoted = bytearray(b'"')
    for byte in path.encode("utf-8"):
        if byte in (0x22, 0x5C):
            quoted += b"\\" + bytes([byte])
        elif byte < 0x20 or byte == 0x7F:
            quoted += b"\\%03o" % byte
        else:
            quoted.append(byte)
    return bytes(quoted) + b'"\n'


def _unfiltered_worktree_hashes(workspace: Path, paths: Sequence[str]) -> dict[str, str]:
    """Each working-tree file's blob id as its bytes are, from one `hash-object`.

    Empty when Git fails on any of them: its output then cannot be matched to
    the paths with certainty.
    """

    if not paths:
        return {}
    output = _run_git_bounded_output(
        workspace,
        ["hash-object", "--no-filters", "--stdin-paths"],
        max_output_bytes=(len(paths) + 1) * 72,
        input=b"".join(_c_quoted_path(path) for path in paths),
    )
    if output is None:
        return {}
    hashes = output.decode("ascii", errors="replace").split()
    if len(hashes) != len(paths) or not all(_GIT_OBJECT_RE.fullmatch(oid) for oid in hashes):
        return {}
    return dict(zip(paths, hashes, strict=True))


def _paths_with_conversion_attributes(workspace: Path, paths: Sequence[str]) -> set[str] | None:
    """The paths a `filter`, `ident` or `working-tree-encoding` attribute converts on checkout.

    One `check-attr`. ``None`` when it could not be read.
    """

    output = _run_git_bounded_output(
        workspace,
        ["-c", "core.fsmonitor=false", "check-attr", "--stdin", "-z", *_CONVERSION_ATTRIBUTES],
        max_output_bytes=_IDENTITY_LISTING_BYTES,
        input=b"".join(path.encode("utf-8") + b"\0" for path in paths),
    )
    if output is None:
        return None
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) != 3 * len(_CONVERSION_ATTRIBUTES) * len(paths):
        return None
    wanted = {path.encode("utf-8"): path for path in paths}
    converted: set[str] = set()
    for index in range(0, len(fields), 3):
        name, _attribute, value = fields[index : index + 3]
        if name not in wanted:
            return None
        if value not in {b"unspecified", b"unset"}:
            converted.add(wanted[name])
    return converted


def _blob_contents(workspace: Path, blobs: Sequence[_TreeBlob]) -> dict[str, bytes]:
    """The bytes of each blob, from one bounded `cat-file --batch`; empty on any failure."""

    if not blobs:
        return {}
    unique = list({blob.oid: blob for blob in blobs}.values())
    output = _run_git_bounded_output(
        workspace,
        ["cat-file", "--batch"],
        max_output_bytes=sum(blob.size + 128 for blob in unique),
        input=b"".join(blob.oid.encode("ascii") + b"\n" for blob in unique),
    )
    if output is None:
        return {}
    contents: dict[str, bytes] = {}
    offset = 0
    for blob in unique:
        header_end = output.find(b"\n", offset)
        if header_end < 0:
            return {}
        header = output[offset:header_end].split()
        start, end = header_end + 1, header_end + 1 + blob.size
        if (
            header != [blob.oid.encode("ascii"), b"blob", str(blob.size).encode("ascii")]
            or output[end : end + 1] != b"\n"
        ):
            return {}
        contents[blob.oid] = bytes(output[start:end])
        offset = end + 1
    return contents


def _read_regular_file(target: Path) -> bytes | None:
    """A working-tree file's bytes within the identity bound, never through a final link."""

    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(target, flags)
    except OSError:
        return None
    try:
        with os.fdopen(descriptor, "rb") as handle:
            data = handle.read(_IDENTITY_FILE_BYTES + 1)
    except OSError:
        return None
    return data if len(data) <= _IDENTITY_FILE_BYTES else None


def blob_path_identities(
    workspace: Path, base: str, head: str | None, paths: Iterable[str]
) -> dict[str, bool | None]:
    """Whether each path's bytes are the same at ``base`` and ``head``, three ways (#812).

    ``head=None`` means the working tree. Every path requested has an answer:

    - ``True``: identical. The same regular-file blob on both sides, exactly
      as :func:`blob_path_unchanged` proves it for a path no link reaches.
    - ``False``: differs. Between commits, two regular-file blobs with
      different object IDs. In the working tree, a regular file whose
      unfiltered hash differs from the base blob, on a path no `filter`,
      `ident` or `working-tree-encoding` attribute converts, and whose bytes
      still differ from the base blob's once `CRLF` is read as `LF` on both
      sides.
    - ``None``: neither is shown. A difference a checkout conversion can make
      (`eol=crlf`, `core.autocrlf`, which Git itself reports as no change), a
      mode-only change, a path absent or not a regular file on either side, a
      symlink or a symlinked parent, a file past the read bound, or any Git
      failure. Callers must never read it as either answer.

    No clean or smudge filter runs: a configured filter driver is a command
    line, so a converted path is answered ``None`` rather than converted.
    ``blob_path_unchanged`` is left as it is for ``unchanged_limits``, where
    anything short of identity refuses; only it follows an in-tree link
    (#822), so a path through one is ``None`` here.

    The cost is bounded per call, not per path: one tree listing per commit,
    one `hash-object --stdin-paths` and, only for paths whose hashes differ,
    one `check-attr` and one `cat-file --batch`.
    """

    from pathlib import PurePosixPath

    answers: dict[str, bool | None] = {}
    wanted: list[str] = []
    for path in dict.fromkeys(paths):
        answers[path] = None
        relative = PurePosixPath(path)
        if (
            not path
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in path
            or "\0" in path
        ):
            continue
        try:
            path.encode("utf-8")
        except UnicodeEncodeError:
            continue
        wanted.append(path)
    if not wanted:
        return answers
    base_commit = commit_sha(workspace, base)
    base_blobs = _regular_tree_blobs(workspace, base_commit, wanted) if base_commit else None
    if base_blobs is None:
        return answers
    if head is not None:
        head_commit = commit_sha(workspace, head)
        head_blobs = _regular_tree_blobs(workspace, head_commit, wanted) if head_commit else None
        if head_blobs is None:
            return answers
        for path in wanted:
            before, after = base_blobs.get(path), head_blobs.get(path)
            if before is None or after is None:
                continue
            if before == after:
                answers[path] = True
            elif before.oid != after.oid:
                answers[path] = False
        return answers

    readable: list[str] = []
    for path in wanted:
        if path not in base_blobs:
            continue
        parts = PurePosixPath(path).parts
        target = workspace / path
        try:
            parents = [workspace / Path(*parts[:index]) for index in range(1, len(parts))]
            if (
                any(parent.is_symlink() for parent in parents)
                or target.is_symlink()
                or not target.is_file()
            ):
                continue
        except OSError:
            continue
        readable.append(path)
    hashes = _unfiltered_worktree_hashes(workspace, readable)
    differing: list[str] = []
    for path in readable:
        if path not in hashes:
            continue
        if hashes[path] == base_blobs[path].oid:
            answers[path] = True
        else:
            differing.append(path)
    if not differing:
        return answers
    converted = _paths_with_conversion_attributes(workspace, differing)
    if converted is None:
        return answers
    compared: list[str] = []
    total = 0
    for path in differing:
        size = base_blobs[path].size
        if path in converted or size > _IDENTITY_FILE_BYTES or total + size > _IDENTITY_BATCH_BYTES:
            continue
        total += size
        compared.append(path)
    contents = _blob_contents(workspace, [base_blobs[path] for path in compared])
    for path in compared:
        before = contents.get(base_blobs[path].oid)
        after = _read_regular_file(workspace / path)
        if before is None or after is None:
            continue
        if before.replace(b"\r\n", b"\n") != after.replace(b"\r\n", b"\n"):
            answers[path] = False
    return answers


def resolve_tree_path_identity(
    workspace: Path,
    ref: str,
    requested: Path,
) -> Path | None:
    """Return the exact Git-tree spelling corresponding to ``requested``.

    Filesystems such as default APFS can materialize an NFC Git pathname with
    an NFD directory-entry spelling. Verification must scan the local entry,
    but receipts and archived-tree lookups must retain the exact Git identity
    so they reproduce on case-sensitive, normalization-sensitive hosts.

    A portable-key collision is rejected rather than guessed. The archive
    materializer enforces the same collision rule before writing any tree.
    """

    commit = commit_sha(workspace, ref)
    if commit is None:
        return None
    requested_text = requested.as_posix()
    listing = _run_git_bounded_output(
        workspace,
        ["ls-tree", "-r", "--name-only", "-z", commit],
        max_output_bytes=_MAX_MANIFEST_LISTING_BYTES,
    )
    if listing is None:
        raise _tree_limitation(
            "Git tree path identity could not be established within static "
            "resource bounds.",
            failure=GIT_TREE_LISTING_UNREADABLE,
        )
    requested_key = _portable_tree_path_key(requested_text)
    matches: list[str] = []
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        try:
            path = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            # The diff and the readers decode every path as UTF-8 too, so this
            # refusal stands on every route; it is classified, not lifted.
            raise _tree_limitation(
                "Git tree contains a non-UTF-8 path",
                failure=GIT_TREE_LISTING_UNREADABLE,
                path=raw.decode("utf-8", errors="backslashreplace"),
            ) from None
        # A name the portable rule refuses is not compared: the path asked for
        # passed that rule, and such a name is refused where it would be read,
        # by the --head archive before any reader runs. Refusing it here refused
        # every verify of the repository, with or without --head, over an entry
        # nothing opens (#328).
        try:
            key = _portable_tree_path_key(path)
        except ConfigError:
            continue
        if key == requested_key:
            matches.append(path)
    if len(matches) > 1:
        # Every route refuses this one, so it is the repository's to settle by
        # keeping one spelling, not a limitation to wait out.
        raise ConfigError(
            "Git tree contains filesystem-colliding paths for configured "
            f"manifest {requested_text!r}: {matches!r}",
            details={"failure": MANIFEST_SPELLING_COLLISION, "paths": list(matches)},
        )
    if not matches:
        return None
    matched = Path(matches[0])
    try:
        requested_stat = (workspace / requested).stat(follow_symlinks=False)
        matched_stat = (workspace / matched).stat(follow_symlinks=False)
    except OSError:
        return None
    # A portable-key match is only an alias hint. On case-sensitive hosts,
    # distinct files can legitimately differ only by case, normalization, or
    # trailing spaces; rebinding them would hash one file under another path.
    return matched if os.path.samestat(requested_stat, matched_stat) else None

# Suffixes retained for the independent rename/deletion guard.  Manifest
# discovery itself is deliberately suffix-agnostic because ``load_manifest``
# accepts YAML content from any filename.
_MANIFEST_SUFFIXES = (".yaml", ".yml")


# Bounds on the retained-manifest probe. A tree with more tracked files than
# this, or a candidate larger than this, is not worth reading to decide a
# wording question — the probe reports "cannot prove" and the plainer copy wins.
# The process cost is now constant (one tree listing plus one cat-file batch),
# so ordinary repositories should not lose adoption detection merely because
# they contain a few hundred small tracked files. These are resource-safety
# bounds, not expected repository sizes.
_MAX_MANIFEST_CANDIDATES = 10_000
_MAX_MANIFEST_BYTES = 512 * 1024
_MAX_MANIFEST_BATCH_BYTES = 64 * 1024 * 1024
_MAX_MANIFEST_LISTING_BYTES = 8 * 1024 * 1024

# The keys every Shipgate manifest must carry. Matching on parsed structure
# rather than on raw text is what makes quoted keys, differing indentation, and
# flow mappings all read the same.
_MANIFEST_REQUIRED_KEYS = frozenset({"version", "project", "agent"})


def carries_manifest_like_yaml(
    workspace: Path,
    ref: str,
    *,
    protected_names: frozenset[str] = frozenset(),
) -> bool | None:
    """Whether ``ref`` contains any file that parses as a Shipgate manifest.

    A basename check cannot prove a base carries no gate: a manifest may be
    called anything, so a base that keeps an operational ``old-gate.json`` while
    the head adds ``new-gate.yml`` passes every name test.

    Every tracked filename is eligible because :func:`load_manifest` selects
    its parser by content, not suffix. The candidates are *parsed*, not
    grepped. A text probe for ``^project:`` misses a valid manifest whose keys
    are quoted, indented, or written in flow style — and a manifest that loads
    fine while the probe says "absent" is the fail-open this exists to prevent.
    Over-matching is deliberate: an unrelated document carrying both keys
    merely costs the adoption wording. ``protected_names`` provides the
    independent canonical-name guard from this same bounded tree listing, so
    callers never need a second unbounded ``ls-tree`` process.

    ``None`` means the answer could not be established — an unreadable tree or
    a tree/candidate beyond the bounds above. Files that are not UTF-8 YAML
    objects are safely skipped because ``load_manifest`` cannot accept them.
    Callers must treat ``None`` as "cannot prove", never as absence.
    """

    commit = commit_sha(workspace, ref)
    if commit is None:
        return None
    listing = _run_git_bounded_output(
        workspace,
        ["ls-tree", "-r", "-l", "-z", commit],
        max_output_bytes=_MAX_MANIFEST_LISTING_BYTES,
    )
    if listing is None:
        return None
    records = [record for record in listing.split(b"\0") if record]
    if len(records) > _MAX_MANIFEST_CANDIDATES:
        return None
    candidates: list[tuple[bytes, str, int]] = []
    total_bytes = 0
    for record in records:
        try:
            header, encoded_path = record.split(b"\t", 1)
            _mode, object_type, object_id, encoded_size = header.split()
            candidate = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
        # Recursive ls-tree can still contain a submodule commit. It is not a
        # file the manifest loader could open, so it is outside this probe.
        if object_type != b"blob":
            continue
        if candidate.rsplit("/", maxsplit=1)[-1] in protected_names:
            return True
        try:
            byte_count = int(encoded_size)
        except (TypeError, ValueError):
            return None
        if byte_count > _MAX_MANIFEST_BYTES:
            return None
        total_bytes += byte_count
        if total_bytes > _MAX_MANIFEST_BATCH_BYTES:
            return None
        candidates.append((object_id, candidate, byte_count))

    if not candidates:
        return False

    # Object IDs from ls-tree, rather than ref:path expressions, keep the
    # batch protocol unambiguous even for unusual Git filenames. One bounded
    # cat-file process replaces the former size+show pair per tracked file.
    try:
        batch = _run_git(
            workspace,
            ["cat-file", "--batch"],
            check=False,
            text=False,
            input=b"".join(
                object_id + b"\n" for object_id, _path, _size in candidates
            ),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if batch.returncode != 0 or not isinstance(batch.stdout, bytes):
        return None

    output = batch.stdout
    offset = 0
    for expected_id, candidate, expected_size in candidates:
        header_end = output.find(b"\n", offset)
        if header_end < 0:
            return None
        try:
            object_id, object_type, encoded_size = output[offset:header_end].split()
            byte_count = int(encoded_size)
        except (TypeError, ValueError):
            return None
        if (
            object_id != expected_id
            or object_type != b"blob"
            or byte_count != expected_size
        ):
            return None
        content_start = header_end + 1
        content_end = content_start + byte_count
        if content_end >= len(output) or output[content_end : content_end + 1] != b"\n":
            return None
        blob = output[content_start:content_end]
        offset = content_end + 1
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            if candidate.lower().endswith(_MANIFEST_SUFFIXES):
                return None
            continue
        try:
            document = yaml.safe_load(text)
        except (RecursionError, ValueError, OverflowError, yaml.YAMLError):
            if candidate.lower().endswith(_MANIFEST_SUFFIXES):
                return None
            # The real manifest loader rejects the same input. Preserve the
            # long-standing fail-closed contract for declared YAML files while
            # allowing unrelated source/binary files to be ruled out.
            continue
        if isinstance(document, dict) and _MANIFEST_REQUIRED_KEYS <= {
            str(key) for key in document
        }:
            return True
    if offset != len(output):
        return None
    return False


def removes_a_yaml_file(workspace: Path, base: str | None, head: str) -> bool | None:
    """Whether the evaluated diff deletes or renames away any YAML file.

    A repository is free to name its manifest anything, so "the base has no
    file called shipgate.yaml" cannot by itself prove the base had no gate: a
    PR that renames ``old-gate.yml`` to ``new-gate.yml`` while loosening it
    passes that test. Git's own rename/delete detection answers the question
    the name check cannot, without having to guess names.

    ``None`` means the diff could not be read — cannot prove, so callers must
    not claim an adoption.
    """

    try:
        _reject_unbound_diff_configuration(workspace)
        if base is None:
            _reject_executable_worktree_filters(workspace)
    except ConfigError:
        return None
    args = [
        *_SAFE_DIFF_CONFIG,
        "diff",
        *_DETERMINISTIC_DIFF_OPTIONS,
        "--name-status",
        "--diff-filter=DR",
        "-z",
    ]
    head_commit = commit_sha(workspace, head)
    if head_commit is None:
        return None
    if base:
        base_commit = commit_sha(workspace, base)
        if base_commit is None:
            return None
        args.append(f"{base_commit}...{head_commit}")
    else:
        args.append(head_commit)
    output = _run_git_bounded_output(
        workspace,
        args,
        max_output_bytes=_DIFF_METADATA_LIMIT,
    )
    if output is None:
        return None
    try:
        paths = _paths_from_name_status(output)
    except (UnicodeDecodeError, ConfigError):
        return None
    # Checking both rename sides is deliberately conservative: a destination
    # YAML path can only suppress friendly adoption wording, never grant it.
    return any(path.lower().endswith(_MANIFEST_SUFFIXES) for path in paths)


def removes_any_tracked_path(
    workspace: Path,
    *,
    comparison_ref: str,
    head: str,
    worktree: bool,
) -> bool | None:
    """Whether the *effective* evaluated diff deletes or renames away anything.

    Suffix-agnostic and content-agnostic, unlike :func:`removes_a_yaml_file`,
    and that is the point rather than laziness. That helper answers a wording
    question and may safely look only at ``.yaml``/``.yml``; this one answers
    "could this diff have moved an existing gate onto the configured path",
    where the gate may be ``old-gate.json`` or have no suffix at all, and where
    a missed rename grants a coding agent write access to the trust root.
    Refusing on *any* removal costs an adoption PR that also deletes a file its
    drafting route; guessing which removals could have been a manifest costs
    correctness (#429 review).

    ``worktree`` selects the comparison the run actually evaluated. A run whose
    head is the working tree stages and edits files that ``base...head`` cannot
    see — a staged ``R086 old-gate.yml -> shipgate.yaml`` under ``--base main``
    is invisible to the committed range and is exactly the shape this exists to
    refuse.

    ``None`` means the diff could not be read: cannot prove, so callers must
    not claim an introduction.
    """

    try:
        _reject_unbound_diff_configuration(workspace)
        if worktree:
            _reject_executable_worktree_filters(workspace)
    except ConfigError:
        return None
    comparison_commit = commit_sha(workspace, comparison_ref)
    if comparison_commit is None:
        return None
    args = [
        *_SAFE_DIFF_CONFIG,
        "diff",
        *_DETERMINISTIC_DIFF_OPTIONS,
        "--name-status",
        "--diff-filter=DR",
        "-z",
        comparison_commit,
    ]
    if not worktree:
        head_commit = commit_sha(workspace, head)
        if head_commit is None:
            return None
        args[-1] = f"{comparison_commit}...{head_commit}"
    output = _run_git_bounded_output(
        workspace,
        args,
        max_output_bytes=_DIFF_METADATA_LIMIT,
    )
    if output is None:
        return None
    try:
        paths = _paths_from_name_status(output)
    except (UnicodeDecodeError, ConfigError):
        return None
    return bool(paths)


def working_tree_context(
    workspace: Path,
    *,
    comparison_ref: str = "HEAD",
    exclude: Path | None = None,
    reject_index_hidden: bool = False,
) -> tuple[list[str], str]:
    """Return effective changed paths and tracked-file diff text.

    ``git diff <comparison_ref>`` compares one committed tree with the current
    index/worktree, so staged and unstaged tracked changes are represented in
    one record per effective path. Untracked file paths are included for
    trigger/check context, but their contents are intentionally not read into
    the diff body.

    The verifier passes the merge-base commit as ``comparison_ref`` when a
    worktree sits on top of committed branch changes. This is deliberately one
    comparison, rather than concatenating ``base...HEAD`` and ``HEAD`` diffs:
    concatenation makes a valid overlapping edit look structurally ambiguous.
    """

    _reject_unbound_diff_configuration(workspace)
    _reject_executable_worktree_filters(workspace)
    comparison_commit = commit_sha(workspace, comparison_ref)
    if comparison_commit is None:
        raise DiffInputError(
            DiffContext(
                completeness="unavailable",
                reason="refs_missing",
                detail=f"Git ref {comparison_ref!r} is not available locally.",
            )
        )
    pathspec = _worktree_pathspec(workspace, exclude)
    if reject_index_hidden:
        _reject_index_hidden_capability_paths(workspace, pathspec=pathspec)
    paths = _working_tree_paths(
        workspace,
        comparison_commit=comparison_commit,
        pathspec=pathspec,
    )
    body = _run_git_bounded_result(
        workspace,
        [
            *_SAFE_WORKTREE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            comparison_commit,
            "--",
            *pathspec,
        ],
        max_output_bytes=_DIFF_BODY_LIMIT,
    )
    if body.payload is None:
        reason, detail = _classify_diff_failure(
            body, limit_reason="body_limit_exceeded", workspace=workspace
        )
        raise DiffInputError(
            DiffContext(
                changed_files=tuple(paths),
                completeness="partial",
                reason=reason,
                detail=detail,
            )
        )
    diff_text = _decode_diff_body(body.payload)
    try:
        _reject_binary_capability_paths(
            workspace,
            comparison_commit,
            pathspec=pathspec,
            diff_config=_SAFE_WORKTREE_DIFF_CONFIG,
        )
    except BinaryCapabilityDiffError as exc:
        exc.changed_paths = tuple(paths)
        exc.diff_text = diff_text
        raise
    except DiffInputError as exc:
        # The binary-hiding guard could not run, so the body is not proven to
        # cover every capability path. Carry what was read: a caller that can
        # act on partial evidence should not have to re-collect it.
        raise DiffInputError(
            DiffContext(
                changed_files=tuple(paths),
                diff_text=diff_text,
                completeness="partial",
                reason=exc.context.reason,
                detail=exc.context.detail,
            )
        ) from exc
    return paths, diff_text


def working_tree_paths(
    workspace: Path,
    *,
    comparison_ref: str = "HEAD",
    exclude: Path | None = None,
    reject_index_hidden: bool = False,
) -> list[str]:
    """Return the bounded path inventory for one worktree comparison.

    This metadata-only form is used to bind the HEAD-relative overlay identity
    independently from the merge-base-relative diff evaluated by policy.
    """

    _reject_unbound_diff_configuration(workspace)
    _reject_executable_worktree_filters(workspace)
    comparison_commit = commit_sha(workspace, comparison_ref)
    if comparison_commit is None:
        raise DiffInputError(
            DiffContext(
                completeness="unavailable",
                reason="refs_missing",
                detail=f"Git ref {comparison_ref!r} is not available locally.",
            )
        )
    pathspec = _worktree_pathspec(workspace, exclude)
    if reject_index_hidden:
        _reject_index_hidden_capability_paths(workspace, pathspec=pathspec)
    return _working_tree_paths(
        workspace,
        comparison_commit=comparison_commit,
        pathspec=pathspec,
    )


def _working_tree_paths(
    workspace: Path,
    *,
    comparison_commit: str,
    pathspec: list[str],
) -> list[str]:
    names = _run_git_bounded_result(
        workspace,
        [
            *_SAFE_WORKTREE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            comparison_commit,
            "--name-status",
            "-z",
            "--",
            *pathspec,
        ],
        max_output_bytes=_DIFF_METADATA_LIMIT,
    )
    if names.payload is None:
        reason, detail = _classify_diff_failure(
            names, limit_reason="metadata_limit_exceeded", workspace=workspace
        )
        raise DiffInputError(
            DiffContext(completeness="unavailable", reason=reason, detail=detail)
        )
    paths = sorted(_paths_from_name_status(names.payload))
    # The untracked inventory is cheap path metadata, independent of the diff
    # body. Collecting it here rather than after the body read means a body
    # that cannot be read still hands back the complete set of changed paths —
    # a brand-new capability file appears in no `git diff` at all.
    untracked = _run_git_bounded_output(
        workspace,
        [
            *_SAFE_DIFF_CONFIG,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *pathspec,
        ],
        max_output_bytes=_DIFF_METADATA_LIMIT,
    )
    if untracked is None:
        raise ConfigError(
            "Git untracked-path inventory exceeded static output bounds."
        )
    for raw_path in untracked.split(b"\0"):
        if not raw_path:
            continue
        path = os.fsdecode(raw_path)
        if path not in paths:
            paths.append(path)
    return sorted(paths)


def _worktree_pathspec(workspace: Path, exclude: Path | None) -> list[str]:
    pathspec = [":(top)**"]
    if exclude is None:
        return pathspec
    try:
        relative = exclude.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError as exc:
        raise ConfigError("Verifier output directory must remain inside workspace") from exc
    if relative in {"", "."}:
        raise ConfigError("Verifier output directory cannot be the workspace root")
    # ``--out`` is user-controlled. Literal magic prevents names such as
    # ``*`` or ``[x]`` from becoming exclusion patterns that hide unrelated
    # worktree changes. Matching the directory path excludes its descendants
    # under Git's ordinary pathspec directory-prefix semantics.
    pathspec.append(f":(top,literal,exclude){relative}")
    return pathspec


def _reject_index_hidden_capability_paths(
    workspace: Path,
    *,
    pathspec: list[str],
) -> None:
    """Reject index flags that can conceal worktree inputs.

    Declared tool sources may use arbitrary extensions, so path heuristics
    cannot prove a hidden entry irrelevant before the manifest is evaluated.
    Reject every hidden entry in the bounded pathspec.
    """

    payload = _run_git_bounded_output(
        workspace,
        ["ls-files", "-v", "-z", "--", *pathspec],
        max_output_bytes=_DIFF_METADATA_LIMIT,
    )
    if payload is None:
        raise ConfigError(
            "Git index-visibility metadata exceeded static output bounds."
        )
    hidden: list[str] = []
    for record in payload.split(b"\0"):
        if not record:
            continue
        marker, separator, raw_path = record.partition(b" ")
        if not separator or len(marker) != 1:
            raise ConfigError("Git returned malformed index-visibility metadata.")
        code = chr(marker[0])
        if code != "S" and not code.islower():
            continue
        hidden.append(os.fsdecode(raw_path))
    if hidden:
        raise ConfigError(
            "Git index flags hide paths from worktree collection: "
            + ", ".join(sorted(hidden)[:3])
        )


def _reject_executable_worktree_filters(workspace: Path) -> None:
    """Fail before Git can execute a configured clean/process filter."""

    payload = _run_git_bounded_output(
        workspace,
        [
            "config",
            "--includes",
            "-z",
            "--get-regexp",
            r"^filter\..*\.(clean|process|smudge)$",
        ],
        max_output_bytes=_WORKTREE_FILTER_CONFIG_LIMIT,
        allowed_returncodes=(0, 1),
    )
    if payload is None:
        raise ConfigError(
            "Could not establish whether repository Git clean/process filters "
            "are safe for static worktree collection."
        )
    active: list[str] = []
    for record in payload.split(b"\0"):
        if not record:
            continue
        raw_key, separator, raw_value = record.partition(b"\n")
        if not separator:
            raise ConfigError("Git returned malformed filter configuration.")
        if raw_value.strip():
            active.append(raw_key.decode("utf-8", errors="replace"))
    if active:
        shown = _named_in_refusal(active)
        raise UnboundGitConfigurationError(
            f"Static worktree collection refuses executable Git filters ({shown}).",
            remediation=(
                "Commit the intended changes and verify refs, or provide an "
                "explicit inert diff artifact."
            ),
        )
    attributed = _run_git_bounded_output(
        workspace,
        [
            *_SAFE_DIFF_CONFIG,
            "ls-files",
            "-z",
            "--",
            ":(top)**",
            ":(exclude,attr:!filter)",
            ":(exclude,attr:-filter)",
        ],
        max_output_bytes=_WORKTREE_ATTRIBUTE_LIST_LIMIT,
    )
    if attributed is None:
        raise ConfigError(
            "Git filter-attribute metadata exceeded static resource bounds or "
            "could not be inspected safely."
        )
    attributed_paths = [
        os.fsdecode(raw) for raw in attributed.split(b"\0") if raw
    ]
    if attributed_paths:
        shown = _named_in_refusal(attributed_paths)
        raise UnboundGitConfigurationError(
            "Static worktree collection refuses Git filter attributes because "
            f"their normalization driver is not receipt-bound ({shown}).",
            remediation="Commit the intended changes and verify refs instead.",
        )


def _named_in_refusal(names: list[str]) -> str:
    """The first three configuration keys or paths a refusal names, redacted.

    Both come from the repository, and either can be shaped like a credential
    — a tracked ``assets/ghp_….bin`` — while the refusal is published in
    ``verifier.json``, the PR comment and every currency refusal (#813).
    """

    return redact_text(", ".join(sorted(set(names))[:3])) or ""


def _reject_unbound_diff_configuration(workspace: Path) -> None:
    """Reject repository-local presentation state that can rewrite Git diffs."""

    configured = _run_git_bounded_output(
        workspace,
        [
            "config",
            "--includes",
            "-z",
            "--get-regexp",
            r"^diff\.",
        ],
        max_output_bytes=_DIFF_CONFIG_LIMIT,
        allowed_returncodes=(0, 1),
    )
    if configured is None:
        raise ConfigError(
            "Could not establish whether repository-local Git diff drivers are "
            "safe for deterministic collection."
        )
    # The keys are named so the refusal says what it refused; values never
    # are, because a driver's value is a command line.
    keys = [
        key.decode("utf-8", errors="replace")
        for key, _, value in (record.partition(b"\n") for record in configured.split(b"\0"))
        if value.strip()
    ]
    if keys:
        raise UnboundGitConfigurationError(
            "Deterministic diff collection refuses repository-local diff.* "
            f"configuration because it is not receipt-bound ({_named_in_refusal(keys)})."
        )

    raw_info_path = _run_git(
        workspace,
        ["rev-parse", "--git-path", "info/attributes"],
        check=False,
    )
    if raw_info_path.returncode != 0 or not raw_info_path.stdout.strip():
        raise ConfigError("Could not inspect repository-local Git attributes.")
    info_path = Path(raw_info_path.stdout.strip())
    if not info_path.is_absolute():
        info_path = workspace / info_path
    try:
        metadata = info_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ConfigError(
            "Could not inspect repository-local Git attributes safely."
        ) from exc
    if not info_path.is_file() or info_path.is_symlink() or metadata.st_size:
        raise UnboundGitConfigurationError(
            "Deterministic diff collection refuses non-empty or aliased "
            ".git/info/attributes because it is not receipt-bound."
        )


def archive_tree(
    workspace: Path,
    ref: str,
    destination: Path,
    *,
    scope: Callable[[str], bool] | None = None,
    record_gitlinks: bool = False,
    rescope: Callable[[Path], tuple[Path, Callable[[str], bool]] | None] | None = None,
    _admit_contained_links: bool = False,
) -> dict[str, str]:
    """Materialize exact Git blobs without export-ignore or substitutions.

    ``rescope``, with a ``scope``, is called with the materialized tree and
    may name a second, empty destination and a wider scope: the same
    verified object store then materializes that one too, so a reader that
    learns from the first tree which further paths it needs (#702) does not
    copy and verify the object graph twice. The gitlinks returned are the
    last tree's.

    ``scope`` narrows the materialized tree to the paths a reader will
    actually open, plus every symlink — a linked directory is what can
    conceal a scoped path from the reader, so the two sides must see the
    same links. Without a scope every blob is written; that once cost one
    ``git cat-file`` per file, 1,168 subprocesses and 25 seconds on a 26 MB
    repository whose host surface is four files (#686), and blobs are now
    read in a few bounded ``cat-file --batch`` processes instead. Every
    archive packs the tree rather than the commit, so no history is walked:
    the commit's parents are not part of the snapshot, and a depth-1
    checkout, which lacks them, archives its head like any other (#1023).

    Scoping changes what is *materialized*, never what is *verified*: every
    blob written is still checked against its object ID, and the isolated
    store is still fsck'd, on the same terms as an unscoped archive.

    A gitlink (a submodule's commit pointer) in scope is refused unless
    ``record_gitlinks`` is set. Its content lives in another repository, so
    no archive of this one can hold it. A caller whose reader treats an
    unpopulated submodule as the empty directory a checkout leaves may opt in:
    the gitlink is then materialized as that empty directory and returned
    as ``{path: commit}``, so the caller can name what it did not read.
    """

    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ConfigError("Git archive destination must be empty")
    commit = commit_sha(workspace, ref)
    if commit is None:
        raise ConfigError(f"Git archive ref is unavailable: {ref}")
    with tempfile.TemporaryDirectory(prefix="agents-shipgate-git-snapshot-") as raw:
        git_dir = Path(raw) / "git"
        # Peeled here, in the repository that certainly holds the commit: a
        # tree pack does not carry it, so `<commit>^{tree}` cannot be
        # resolved again inside the isolated store.
        with _perf.phase("archive.rev_parse_tree"):
            tree = _run_git(workspace, ["rev-parse", f"{commit}^{{tree}}"]).stdout.strip()
        with _perf.phase("archive.copy_verified_graph"):
            _copy_verified_commit_graph(
                workspace,
                commit=commit,
                git_dir=git_dir,
                tree=tree,
            )
        with _perf.phase("archive.materialize_scoped"):
            gitlinks = _materialize_isolated_tree(
                git_dir,
                tree=tree,
                destination=destination,
                scope=scope,
                record_gitlinks=record_gitlinks,
                admit_contained_links=_admit_contained_links,
            )
        with _perf.phase("archive.rescope"):
            wider = rescope(destination) if rescope is not None and scope is not None else None
        if wider is None:
            return gitlinks
        again, wider_scope = wider
        again.mkdir(parents=True, exist_ok=True)
        if any(again.iterdir()):
            raise ConfigError("Git archive destination must be empty")
        with _perf.phase("archive.materialize_wider"):
            return _materialize_isolated_tree(
                git_dir,
                tree=tree,
                destination=again,
                scope=wider_scope,
                record_gitlinks=record_gitlinks,
            )


def archive_verification_tree(workspace: Path, ref: str, destination: Path) -> dict[str, str]:
    """Full verification snapshot, holding the links a checkout of ``ref`` holds.

    A link that opens inside the tree, such as ``CLAUDE.md -> AGENTS.md`` or
    ``.claude/skills -> ../.agents/skills``, is recreated as the link a
    checkout of the commit holds, instead of failing the whole tree: refusing
    every link made ``verify --head`` exit 2, and the base comparison fail, on
    any repository that commits one. Each reader then meets that link, not a
    refusal: a reader that refuses links refuses it, and a read that follows
    it reaches only this commit's own files. A link that could reach anything
    this tree does not hold is still refused: see :func:`_contained_tree_links`.
    Admission is read from this immutable tree's verified object store; the
    worktree cannot authorize a link. Generic unscoped archives retain their
    refusal, and on Windows, where a link is not recreated as Git for Windows
    would write it, so does this one.
    """

    return archive_tree(workspace, ref, destination, _admit_contained_links=True)


def _contained_tree_links(
    git_dir: Path,
    listed: list[tuple[str, str, str, str]],
    tree_types: dict[str, str],
) -> dict[str, tuple[str, bool]]:
    """Links a full verification archive recreates, each mapped to what it opens.

    The value is the tree path the link opens (``""`` for the root) and
    whether that is a directory.

    A link is admitted only when opening it, as the filesystem opens it,
    reaches a blob or a directory of this tree (see :func:`_tree_link_target`).
    One that is absolute, leaves the tree, dangles, loops, passes more than
    :data:`_MAX_TREE_LINK_HOPS` links or has text that is not UTF-8 or holds
    NUL is not, and the archive refuses it as before. Nothing a link admitted
    here can reach is outside the verified tree, so no read through one can
    bring in bytes the commit does not hold.
    """

    links = [(oid, path) for mode, _, oid, path in listed if mode == "120000"]
    if not links:
        return {}
    object_format = _run_git_dir(git_dir, ["rev-parse", "--show-object-format"]).stdout.strip()
    link_texts: dict[str, str] = {}
    for (oid, path), blob in zip(links, _isolated_blobs(git_dir, links), strict=True):
        if _git_object_id("blob", blob, algorithm=object_format) != oid:
            raise ConfigError(f"Git blob failed object-ID validation: {path}")
        try:
            text = blob.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        if "\0" not in text:
            link_texts[path] = text
    # An unscoped listing names no trees, so a directory is any proper prefix
    # of a listed path. A link is a leaf, so it is never one of them.
    directories: set[str] = set()
    for path in tree_types:
        parts = path.split("/")
        directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
    admitted: dict[str, tuple[str, bool]] = {}
    for path in link_texts:
        opened = _tree_link_target(path, link_texts, tree_types, directories)
        if opened is not None:
            target, kind = opened
            admitted[path] = (target, kind == "tree")
    return admitted


#: Why an unscoped verification archive refuses a committed link it does not
#: recreate.
_LINK_NOT_CONTAINED = "only a link that opens a file or directory inside the repository is recreated"
_LINK_COLLIDES = "it collides with another path on this filesystem"
_LINKS_NOT_RECREATED_HERE = "committed links are not recreated on Windows"


def _recreates_committed_links() -> bool:
    """Whether this platform recreates committed links in a verification archive.

    Windows does not: ``os.symlink`` neither converts ``/`` in a relative
    target as Git for Windows does nor creates links without a privilege, so a
    recreated link would not be the link a checkout there holds.
    """

    return os.name != "nt"


def _copy_verified_commit_graph(
    workspace: Path, *, commit: str, git_dir: Path, tree: str | None = None
) -> None:
    """Copy one reachable object graph and verify it independently."""

    object_format = _run_git(workspace, ["rev-parse", "--show-object-format"]).stdout.strip()
    if object_format not in {"sha1", "sha256"}:
        raise ConfigError(f"Unsupported Git object format: {object_format!r}")
    init_args = ["git", "--no-replace-objects", "init", "--quiet", "--bare"]
    if object_format == "sha256":
        init_args.append("--object-format=sha256")
    init_args.append(str(git_dir))
    env = _git_object_environment()
    initialized = _run_process(
        init_args,
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=60,
    )
    if initialized.returncode != 0:
        raise ConfigError(f"Could not initialize isolated Git store: {initialized.stderr.strip()}")

    pack_path = git_dir.parent / "reachable.pack"
    with _perf.phase("archive.copy.pack_objects"):
        with pack_path.open("wb") as output:
            packed = _run_process(
                [
                    "git",
                    "--no-replace-objects",
                    "-C",
                    str(workspace),
                    "pack-objects",
                    "--stdout",
                    "--revs",
                ],
                input=f"{tree or commit}\n".encode("ascii"),
                stdout=output,
                stderr=subprocess.PIPE,
                check=False,
                env=env,
                timeout=120,
            )
    if packed.returncode != 0:
        detail = packed.stderr.decode("utf-8", errors="replace").strip()
        raise ConfigError(f"Could not copy verified Git objects: {detail}")
    with _perf.phase("archive.copy.index_pack"):
        with pack_path.open("rb") as source:
            indexed = _run_process(
                [
                    "git",
                    "--no-replace-objects",
                    f"--git-dir={git_dir}",
                    "index-pack",
                    "--stdin",
                ],
                stdin=source,
                capture_output=True,
                check=False,
                env=env,
                timeout=120,
            )
    if indexed.returncode != 0:
        detail = indexed.stderr.decode("utf-8", errors="replace").strip()
        raise ConfigError(f"Copied Git objects failed index validation: {detail}")
    # fsck the object the pack actually carries. A tree pack has no commit,
    # and asking for one that is not there fails the check for the wrong
    # reason; a tree also has no parents, which is why an archive works on a
    # shallow clone where a commit walk crosses the graft (#683, #1023).
    with _perf.phase("archive.copy.fsck"):
        checked = _run_git_dir(
            git_dir,
            ["fsck", "--strict", "--no-reflogs", "--no-dangling", tree or commit],
            check=False,
        )
    if checked.returncode != 0:
        detail = checked.stderr.strip() or checked.stdout.strip()
        raise ConfigError(f"Copied Git object graph failed integrity validation: {detail}")


def _materialize_isolated_tree(
    git_dir: Path,
    *,
    tree: str,
    destination: Path,
    scope: Callable[[str], bool] | None = None,
    record_gitlinks: bool = False,
    admit_contained_links: bool = False,
) -> dict[str, str]:
    listing_args = ["ls-tree", "-r", "-z"]
    if scope is not None:
        listing_args.append("-t")
    with _perf.phase("archive.tree.list"):
        listing = _run_git_dir(git_dir, [*listing_args, tree], text=False).stdout
    root = destination.resolve()
    entries: list[tuple[str, str, str]] = []
    links: list[tuple[str, str]] = []
    portable_paths: dict[str, str] = {}
    gitlinks: dict[str, str] = {}
    #: Every entry's object type, in or out of scope, so a link's target type
    #: can be read from the tree rather than from what was materialized.
    tree_types: dict[str, str] = {}
    listed: list[tuple[str, str, str, str]] = []
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        metadata, raw_path = raw.split(b"\t", 1)
        mode, object_type, oid = metadata.decode("ascii").split(" ", 2)
        path_text = raw_path.decode("utf-8", errors="strict")
        tree_types[path_text] = "link" if mode == "120000" else object_type
        listed.append((mode, object_type, oid, path_text))
    #: Unscoped, a link is refused unless the caller admits the links that
    #: open inside this tree, each mapped to whether it names a directory.
    admitted_links = (
        _contained_tree_links(git_dir, listed, tree_types)
        if scope is None and admit_contained_links and _recreates_committed_links()
        else {}
    )
    #: Unscoped and admitting links, every directory a tree entry sits in,
    #: by its portable key, so a link can be held apart from any name a case-
    #: or normalization-insensitive filesystem would merge with it.
    directory_keys: dict[str, str] = {}
    seen_directories: set[str] = set()
    with _perf.phase("archive.tree.scope_through_links"):
        in_scope = (
            _scope_through_boundary_links(git_dir, listed, tree_types, scope)
            if scope is not None
            else None
        )
    with _perf.phase("archive.tree.classify"):
        for mode, object_type, oid, path_text in listed:
            if in_scope is not None and mode != "120000" and not in_scope(path_text):
                # Out of scope is not merely unmaterialized, it is unexamined:
                # the portability and collision checks below describe the tree
                # this archive writes, and a name that never lands on the
                # filesystem cannot collide there. A repository whose
                # `src/templates/` holds `.AwS__CrEdEnTiAlS.html` beside
                # `.aws__credentials.html` is comparable for its host surface.
                # Symlinks are always examined: they are what conceals a
                # boundary path from the reader.
                continue
            if "\\" in path_text:
                raise _unsupported_tree_entry(
                    f"Git tree path is not portable: {path_text}", path=path_text
                )
            portable_key = _committed_tree_path_key(path_text)
            prior = portable_paths.setdefault(portable_key, path_text)
            if prior != path_text:
                raise _unsupported_tree_entry(
                    "Git tree contains filesystem-colliding paths: "
                    f"{prior!r} and {path_text!r}",
                    path=path_text,
                )
            if admitted_links:
                parts = path_text.split("/")
                for index in range(1, len(parts)):
                    directory = "/".join(parts[:index])
                    if directory not in seen_directories:
                        seen_directories.add(directory)
                        directory_keys.setdefault(
                            _portable_tree_path_key(directory), directory
                        )
            if object_type == "tree" and scope is not None:
                # A directory at a recognized configuration path is an invalid
                # input, not an absent file. Preserve its kind even when none of
                # its children are in scope so both inventories can refuse it.
                target = (root / path_text).resolve()
                if target == root or root not in target.parents:
                    raise ConfigError(f"Git tree path escapes destination: {path_text}")
                target.mkdir(parents=True, exist_ok=True)
                continue
            if record_gitlinks and mode == "160000" and object_type == "commit":
                # An unpopulated submodule: the directory a checkout leaves, with
                # nothing written into it. What it would hold is not in this
                # repository's object graph, and the caller names it as unread.
                target = (root / path_text).resolve()
                if target == root or root not in target.parents:
                    raise ConfigError(f"Git tree path escapes destination: {path_text}")
                target.mkdir(parents=True, exist_ok=True)
                gitlinks[path_text] = oid
                continue
            if object_type != "blob" or mode == "160000" or (
                mode == "120000" and scope is None and path_text not in admitted_links
            ):
                raise _unsupported_binding(
                    path_text,
                    mode,
                    object_type,
                    reason=(
                        (
                            _LINK_NOT_CONTAINED
                            if _recreates_committed_links()
                            else _LINKS_NOT_RECREATED_HERE
                        )
                        if mode == "120000" and admit_contained_links
                        else None
                    ),
                )
            if mode == "120000":
                # Recreated as a link rather than refused, so the base tree
                # presents the reader the same object the worktree does: a link,
                # opened with O_NOFOLLOW, recorded as unreadable if it is a
                # boundary path. Refusing instead made a third of a
                # 12-repository sample uncomparable, several of them for a
                # symlink no reader would ever open (#688).
                #
                # Nothing is written *through* a link. The escape this refusal
                # used to cover needs a blob under a symlinked ancestor, which a
                # valid tree cannot express — the name would be both a blob and
                # a tree, a duplicate entry `fsck --strict` rejects before
                # anything is written. Blobs are materialized before links
                # regardless, so no write passes through one even if that check
                # ever loosens.
                links.append((oid, path_text))
                continue
            entries.append((mode, oid, path_text))
        # A committed link must not share a name, on a case- or
        # normalization-insensitive filesystem, with a directory of the tree,
        # nor sit in a directory that shares one with a file. Refused before
        # anything is written. Without a link, names that merge keep the
        # archive's behaviour for any tree: two directories merge into one at
        # one depth, as on a checkout, and a file that cannot be written beside
        # a directory is refused when it is written.
        if admitted_links:
            link_directory_keys = {
                _portable_tree_path_key("/".join(parts[:index]))
                for parts in (path.split("/") for path in admitted_links)
                for index in range(1, len(parts))
            }
            for portable_key, path_text in portable_paths.items():
                directory = directory_keys.get(portable_key)
                if directory is None or directory == path_text:
                    continue
                if path_text in admitted_links or portable_key in link_directory_keys:
                    raise _unsupported_tree_entry(
                        "Git tree contains filesystem-colliding paths: "
                        f"{directory!r} and {path_text!r}",
                        path=path_text,
                    )

    object_format = _run_git_dir(git_dir, ["rev-parse", "--show-object-format"]).stdout.strip()
    expected_digests: dict[str, str] = {}
    entry_blobs = _isolated_blobs(git_dir, [(oid, path_text) for _mode, oid, path_text in entries])
    with _perf.phase("archive.tree.blobs_and_write"):
        for (mode, oid, path_text), blob in zip(entries, entry_blobs, strict=True):
            target = (root / path_text).resolve()
            if target == root or root not in target.parents:
                raise ConfigError(f"Git tree path escapes destination: {path_text}")
            if _git_object_id("blob", blob, algorithm=object_format) != oid:
                raise ConfigError(f"Git blob failed object-ID validation: {path_text}")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
            except (FileExistsError, NotADirectoryError, IsADirectoryError) as exc:
                # In a fresh destination, only two tree names this filesystem
                # merges can put a file where a directory goes, or the reverse.
                if scope is not None or not admit_contained_links:
                    raise
                raise _unsupported_tree_entry(
                    f"Git tree path {path_text} collides with another path on this filesystem.",
                    path=path_text,
                ) from exc
            expected_digests[path_text] = hashlib.sha256(blob).hexdigest()
            if mode == "100755":
                os.chmod(target, 0o755)

    link_texts: dict[str, str] = {}
    with _perf.phase("archive.tree.links"):
        for (oid, path_text), blob in zip(links, _isolated_blobs(git_dir, links), strict=True):
            target = root / path_text
            if scope is not None and not _within(root, target.parent):
                raise ConfigError(f"Git tree path escapes destination: {path_text}")
            if _git_object_id("blob", blob, algorithm=object_format) != oid:
                raise ConfigError(f"Git blob failed object-ID validation: {path_text}")
            # The link's own text is the blob. Scoped, it may point anywhere,
            # including out of the tree — the reader opens it with O_NOFOLLOW
            # and records the failure, which is exactly what it does on the
            # live worktree. Unscoped, only an admitted link gets here.
            link_text = blob.decode("utf-8", errors="strict")
            if scope is None:
                _create_admitted_link(
                    root, path_text, link_text, directory=admitted_links[path_text][1]
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(link_text, target)
            link_texts[path_text] = link_text
        if scope is None:
            # Each recreated link must open exactly what the tree model said.
            for path_text, (opened, _directory) in admitted_links.items():
                if os.path.realpath(root / path_text) != os.path.realpath(
                    root.joinpath(*PurePosixPath(opened).parts)
                ):
                    raise _unsupported_binding(
                        path_text, "120000", "blob", reason=_LINK_COLLIDES
                    )

    with _perf.phase("archive.tree.link_target_types"):
        placeholders = (
            _materialize_link_target_types(root, link_texts, tree_types)
            if scope is not None
            else set()
        )

    with _perf.phase("archive.tree.verify_rglob"):
        materialized = {
            relative: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and (relative := path.relative_to(root).as_posix()) not in placeholders
        }
    if materialized != expected_digests:
        raise ConfigError("Materialized Git tree differs from the verified object graph")
    return gitlinks


def _tree_limitation(message: str, *, failure: str, path: str | None = None) -> ConfigError:
    """A refusal of committed tree content that is Agents Shipgate's, not the repository's (#328).

    Git accepts the tree; what refuses it is the archive Agents Shipgate reads
    the commit through, the portable path rule that archive keeps, or the
    reading of the commit's path listing. ``details`` carries that classification
    -- ``origin`` and ``failure``, and ``path`` when one entry is refused -- so
    a caller routes on it rather than on the message, which keeps the wording
    every refusal has always had. It stays a plain :class:`ConfigError`: every
    handler, exit code and the class name some comparison reasons record are
    unchanged.
    """

    details: dict[str, str] = {"failure": failure, "origin": AGENTS_SHIPGATE_ORIGIN}
    if path is not None:
        details["path"] = path
    return ConfigError(message, details=details)


def _unsupported_tree_entry(message: str, *, path: str) -> ConfigError:
    """A committed entry the verification archive does not support yet."""

    return _tree_limitation(message, failure=UNSUPPORTED_GIT_TREE_ENTRY, path=path)


def _binding_refusal_text(
    path_text: str, mode: str, object_type: str, *, reason: str | None = None
) -> str:
    """The refusal of a tree entry an archive will not materialize.

    It names the entry and why it is refused, and nothing about the manifest:
    the entry is a property of the committed tree, not of the configuration.
    """

    message = (
        f"Git tree contains unsupported external binding at {path_text} "
        f"(mode {mode}, type {object_type})"
    )
    return f"{message}: {reason}." if reason else f"{message}."


def _unsupported_binding(
    path_text: str, mode: str, object_type: str, *, reason: str | None = None
) -> ConfigError:
    """:func:`_binding_refusal_text`, classified.

    A link that does not open inside the tree is refused by design, so that no
    read reaches bytes the commit does not hold; every other entry here is one
    the archive does not support yet.
    """

    return _tree_limitation(
        _binding_refusal_text(path_text, mode, object_type, reason=reason),
        failure=(
            UNCONTAINED_GIT_TREE_LINK
            if reason == _LINK_NOT_CONTAINED
            else UNSUPPORTED_GIT_TREE_ENTRY
        ),
        path=path_text,
    )


def _committed_tree_path_key(path_text: str) -> str:
    """:func:`_portable_tree_path_key` of an entry of a committed tree.

    The same rule refuses a path the caller asked for, which is the caller's to
    fix, so a committed entry's refusal is typed here rather than in the rule.
    """

    try:
        return _portable_tree_path_key(path_text)
    except ConfigError as exc:
        raise _unsupported_tree_entry(str(exc), path=path_text) from None


def _create_admitted_link(root: Path, path_text: str, link_text: str, *, directory: bool) -> None:
    """Recreate one admitted link of an unscoped archive at its exact tree path.

    The parent must resolve to the very directory the tree names, through no
    link, before and after it is created; anything else, any filesystem error
    and a resolution that cannot finish are a typed refusal of that link
    rather than an internal error.
    """

    target = root / path_text
    expected_parent = root.joinpath(*PurePosixPath(path_text).parent.parts)
    refusal = _unsupported_binding(path_text, "120000", "blob", reason=_LINK_COLLIDES)
    try:
        if target.parent.resolve() != expected_parent:
            raise refusal
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.resolve() != expected_parent:
            raise refusal
        os.symlink(link_text, target, target_is_directory=directory)
    except (OSError, RuntimeError) as exc:
        # RuntimeError is how Python reports a resolution that loops.
        collides = isinstance(exc, (FileExistsError, NotADirectoryError, RuntimeError)) or (
            isinstance(exc, OSError) and exc.errno == errno.ELOOP
        )
        if collides:
            raise _unsupported_binding(
                path_text, "120000", "blob", reason=_LINK_COLLIDES
            ) from exc
        # Not a property of the tree: the filesystem refused the write.
        raise ConfigError(
            _binding_refusal_text(
                path_text, "120000", "blob", reason="this system could not create the link"
            )
        ) from exc


#: The most blob bytes one `cat-file --batch` read of an isolated store holds.
#: A batch is buffered whole, so this bounds memory, not the tree: a larger
#: tree takes more batches, and a single larger blob is read alone, as it was
#: when every blob had a process of its own.
_MAX_ISOLATED_BATCH_BYTES = 64 * 1024 * 1024


def _isolated_blobs(git_dir: Path, wanted: Sequence[tuple[str, str]]) -> Iterator[bytes]:
    """Each ``(object ID, path)`` blob's bytes from ``git_dir``, in order (#686 cost class).

    One `cat-file` process per blob made a whole-tree archive cost a process
    per file: 3,485 of them and 181 seconds for one side of a 3,485-blob
    repository. Here one `cat-file --batch-check` types and sizes every object
    first, so a missing object, or one that is not a blob, refuses before any
    content is read; the content then comes from as few `cat-file --batch`
    reads as :data:`_MAX_ISOLATED_BATCH_BYTES` allows.

    Only the framing is checked here: every record must name the requested
    object, as a blob of the size the check reported. The caller still hashes
    each blob against its tree entry's object ID before writing it. ``path``
    only names the entry in a refusal.
    """

    if not wanted:
        return
    paths: dict[str, str] = {}
    for oid, path_text in wanted:
        # The batch protocol reads one object name per line and resolves any
        # revision syntax in it, so only a full object ID may be sent.
        if not _GIT_OBJECT_RE.fullmatch(oid):
            raise ConfigError(f"Git tree entry has a malformed object ID: {path_text}")
        paths.setdefault(oid, path_text)
    checked = _run_git_dir(
        git_dir,
        ["cat-file", "--batch-check"],
        text=False,
        input=b"".join(f"{oid}\n".encode("ascii") for oid in paths),
    ).stdout
    records = checked.split(b"\n")
    if records.pop() != b"" or len(records) != len(paths):
        raise ConfigError("Git object check returned a malformed response")
    sizes: dict[str, int] = {}
    for (oid, path_text), record in zip(paths.items(), records, strict=True):
        fields = record.split(b" ")
        if fields == [oid.encode("ascii"), b"missing"]:
            raise ConfigError(f"Git object is missing from the verified object graph: {path_text}")
        if len(fields) != 3 or fields[0] != oid.encode("ascii") or not fields[2].isdigit():
            raise ConfigError(f"Git object check returned a malformed record: {path_text}")
        if fields[1] != b"blob":
            raise ConfigError(
                f"Git tree entry is not a blob: {path_text} "
                f"(type {fields[1].decode('ascii', errors='replace')})."
            )
        sizes[oid] = int(fields[2])

    # Consecutive runs of the requested order, each within the byte bound;
    # an object repeated inside a run is read once.
    runs: list[list[str]] = [[]]
    distinct: set[str] = set()
    run_bytes = 0
    for oid, _path_text in wanted:
        if oid not in distinct:
            if distinct and run_bytes + sizes[oid] > _MAX_ISOLATED_BATCH_BYTES:
                runs.append([])
                distinct, run_bytes = set(), 0
            distinct.add(oid)
            run_bytes += sizes[oid]
        runs[-1].append(oid)
    for run in runs:
        requested = list(dict.fromkeys(run))
        output = _run_git_dir(
            git_dir,
            ["cat-file", "--batch"],
            text=False,
            input=b"".join(f"{oid}\n".encode("ascii") for oid in requested),
        ).stdout
        contents: dict[str, bytes] = {}
        offset = 0
        for oid in requested:
            size = sizes[oid]
            header_end = output.find(b"\n", offset)
            start = header_end + 1
            end = start + size
            if (
                header_end < 0
                or output[offset:header_end] != f"{oid} blob {size}".encode("ascii")
                or output[end : end + 1] != b"\n"
            ):
                raise ConfigError(f"Git object read returned a malformed record: {paths[oid]}")
            contents[oid] = output[start:end]
            offset = end + 1
        if offset != len(output):
            raise ConfigError("Git object read returned more than was requested")
        for oid in run:
            yield contents[oid]


#: How many links one resolution may pass through before it counts as unresolved.
_MAX_TREE_LINK_HOPS = 8


def _scope_through_boundary_links(
    git_dir: Path,
    listed: list[tuple[str, str, str, str]],
    tree_types: dict[str, str],
    scope: Callable[[str], bool],
) -> Callable[[str], bool]:
    """Widen a scope to the in-tree targets of links at in-scope paths (#700).

    The host reader reads a boundary path that is an in-tree link through to
    its target. A base tree that held only a typed placeholder there would
    compare the worktree's file against an empty one. So a link whose own path
    is in scope brings its target's bytes, and a link to a directory brings
    every blob beneath it whose path under the link is in scope.

    Targets are resolved by the tree's own names, exactly as
    :func:`_resolve_tree_link` does. Every link text read here comes from the
    fsck'd isolated store and is checked against its object ID, and every blob
    this adds is still validated when it is materialized.
    """

    object_format = _run_git_dir(git_dir, ["rev-parse", "--show-object-format"]).stdout.strip()
    link_texts: dict[str, str] = {}
    links = [(oid, path_text) for mode, _type, oid, path_text in listed if mode == "120000"]
    for (oid, path_text), blob in zip(links, _isolated_blobs(git_dir, links), strict=True):
        if _git_object_id("blob", blob, algorithm=object_format) != oid:
            raise ConfigError(f"Git blob failed object-ID validation: {path_text}")
        link_texts[path_text] = blob.decode("utf-8", errors="strict")
    files: set[str] = set()
    directories: list[tuple[str, str]] = []
    for link in sorted(link_texts):
        resolved = _resolve_tree_link(link, link_texts)
        if resolved is None or resolved == link:
            continue
        target_type = tree_types.get(resolved)
        if target_type == "blob" and scope(link):
            files.add(resolved)
        elif target_type == "tree":
            directories.append((f"{resolved}/", f"{link}/"))
    if not files and not directories:
        return scope

    def widened(path_text: str) -> bool:
        if scope(path_text) or path_text in files:
            return True
        return any(
            path_text.startswith(target) and scope(link + path_text[len(target):])
            for target, link in directories
        )

    return widened


def _resolve_tree_link(path_text: str, link_texts: dict[str, str]) -> str | None:
    """Where an in-tree link lands, by the tree's own names, or ``None``.

    Lexical only, and deliberately narrow: an absolute target, a target that
    leaves the tree, a chain longer than the hop bound, or any path with a link
    as an *intermediate* component is unresolved. Unresolved is the answer the
    reader already gives such a link, so nothing below is ever written through
    one.
    """

    current = path_text
    for _ in range(_MAX_TREE_LINK_HOPS):
        text = link_texts.get(current)
        if text is None:
            return current
        if not text or text.startswith("/"):
            return None
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(current), text))
        if joined in {".", ".."} or joined.startswith("../"):
            return None
        parts = joined.split("/")
        if any("/".join(parts[:index]) in link_texts for index in range(1, len(parts))):
            return None
        current = joined
    return current if current not in link_texts else None


def _tree_link_target(
    path_text: str,
    link_texts: dict[str, str],
    tree_types: dict[str, str],
    directories: set[str],
) -> tuple[str, Literal["blob", "tree"]] | None:
    """The tree path opening the link at ``path_text`` reaches, and its kind, or ``None``.

    Only full-archive admission asks this. It resolves the path as the
    filesystem does, one component at a time against the tree's own listing,
    rather than normalizing it as :func:`_resolve_tree_link` does:
    ``missing/../one`` fails at ``missing``, ``afile/../one`` at a regular file
    used as a directory and ``one/`` by asking a file for a directory, although
    each normalizes to ``one``. A link met at any component is followed from
    the directory that holds it, at most :data:`_MAX_TREE_LINK_HOPS` links in
    all; an empty or absolute link text, an undecodable one (absent from
    ``link_texts``), ``..`` above the root, or a name the tree does not hold
    (a gitlink included) resolves to nothing. The kind is ``"blob"`` for a
    regular file and ``"tree"`` for a directory, the root (``""``) included.
    """

    resolved: list[str] = []
    pending = list(reversed(path_text.split("/")))
    hops = 0
    while pending:
        component = pending.pop()
        if component in {"", "."}:
            continue
        if component == "..":
            if not resolved:
                return None
            resolved.pop()
            continue
        candidate = "/".join([*resolved, component])
        kind = tree_types.get(candidate)
        if kind == "link":
            hops += 1
            text = link_texts.get(candidate)
            if hops > _MAX_TREE_LINK_HOPS or not text or text.startswith("/"):
                return None
            pending.extend(reversed(text.split("/")))
        elif candidate in directories:
            resolved.append(component)
        elif kind == "blob" and not pending:
            return candidate, "blob"
        else:
            return None
    return "/".join(resolved), "tree"


def _materialize_link_target_types(
    root: Path, link_texts: dict[str, str], tree_types: dict[str, str]
) -> set[str]:
    """Give each recreated link a target of the type the tree says it has (#700).

    A scoped archive writes only the paths a reader opens, so a link to an
    out-of-scope file dangled here while it resolved in the worktree. The reader
    types a link's target to decide whether it may conceal a recursive match,
    and a dangling link does, so `changelog.md -> README.md` made the base side
    incomplete and every comparison in the repository refused.

    The placeholder carries the target's type, never its content: an empty file
    or an empty directory, at a path the scope already excluded because no
    reader opens it. Returns the placeholder files, which the digest check must
    not count as materialized blobs.
    """

    placeholders: set[str] = set()
    for path_text in sorted(link_texts):
        resolved = _resolve_tree_link(path_text, link_texts)
        if resolved is None:
            continue
        object_type = tree_types.get(resolved)
        target = root / resolved
        if object_type not in {"blob", "tree"} or os.path.lexists(target):
            continue
        if not _within(root, target.parent):
            continue
        if object_type == "tree":
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")
            placeholders.add(resolved)
    return placeholders


def _within(root: Path, candidate: Path) -> bool:
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        # A path that cannot be resolved, such as one through a link loop
        # (Python raises RuntimeError for that), is not known to be within.
        return False
    return resolved == root or root in resolved.parents


def _portable_tree_path_key(path_text: str) -> str:
    if (
        "\\" in path_text
        or ":" in path_text
        or path_text.startswith("/")
        or any(ord(character) < 32 for character in path_text)
    ):
        raise ConfigError(f"Git tree path is not portable: {path_text}")
    raw_parts = path_text.split("/")
    portable_parts: list[str] = []
    reserved = {"con", "prn", "aux", "nul"}
    reserved.update(f"com{number}" for number in range(1, 10))
    reserved.update(f"lpt{number}" for number in range(1, 10))
    for raw_part in raw_parts:
        part = unicodedata.normalize("NFKC", raw_part).casefold()
        portable = part.rstrip(" .")
        basename = portable.split(".", 1)[0]
        if (
            not portable
            or raw_part in {".", ".."}
            or raw_part != raw_part.rstrip(" .")
            or basename in reserved
        ):
            raise ConfigError(f"Git tree path is not portable: {path_text}")
        portable_parts.append(portable)
    if not portable_parts:
        raise ConfigError(f"Git tree path is not portable: {path_text}")
    return "/".join(portable_parts)


def _git_object_id(kind: str, data: bytes, *, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    digest.update(f"{kind} {len(data)}\0".encode("ascii"))
    digest.update(data)
    return digest.hexdigest()


def _validate_ref_token(ref: str) -> None:
    if not ref or ref.startswith("-") or any(char in ref for char in "\0\r\n"):
        raise ConfigError(
            "Git refs must be non-empty, must not begin with '-', and must not "
            "contain control delimiters."
        )


def _resolved_diff_revspec(workspace: Path, revspec: str) -> str:
    """Resolve a user rev expression to option-safe commit IDs."""

    _validate_ref_token(revspec)
    if "..." in revspec:
        parts = revspec.split("...")
        separator = "..."
    elif ".." in revspec:
        parts = revspec.split("..")
        separator = ".."
    else:
        parts = [revspec]
        separator = ""
        # ``git diff <one-ref>`` compares that commit with the index/worktree,
        # so it carries the same repository-configured execution hazards as
        # the explicit worktree collector.
        _reject_executable_worktree_filters(workspace)
    if len(parts) not in {1, 2} or any(not part for part in parts):
        raise ConfigError(f"Unsupported Git diff revision expression: {revspec!r}")
    commits = [commit_sha(workspace, part) for part in parts]
    if any(commit is None for commit in commits):
        raise _UnavailableRevisionError(
            f"Git diff revision is unavailable: {revspec!r}"
        )
    return separator.join(commit for commit in commits if commit is not None)


def _git_object_environment() -> dict[str, str]:
    """The environment every Git child of ``verify`` runs with.

    Kept as this module's own name so a test can drop one protection and prove
    the others still hold (``tests/test_capability_diff_partial_clone.py``);
    the variables themselves live in :mod:`agents_shipgate.core.git_env`.
    """

    return hardened_git_env()


def _run_git_dir(
    git_dir: Path,
    args: list[str],
    *,
    check: bool = True,
    text: bool = True,
    input: bytes | None = None,
) -> subprocess.CompletedProcess:
    return _run_process(
        ["git", "--no-replace-objects", f"--git-dir={git_dir}", *args],
        capture_output=True,
        check=check,
        env=_git_object_environment(),
        input=input,
        text=text,
        timeout=120,
    )


@dataclass(frozen=True)
class _BoundedGitResult:
    """One bounded Git read, keeping why it failed instead of only that it did."""

    payload: bytes | None
    exceeded: bool = False
    timed_out: bool = False
    stderr: str = ""


def _run_git_bounded_output(
    workspace: Path,
    args: list[str],
    *,
    max_output_bytes: int,
    timeout: int = 60,
    allowed_returncodes: tuple[int, ...] = (0,),
    input: bytes | None = None,
) -> bytes | None:
    """Run read-only Git plumbing without buffering unbounded stdout."""

    return _run_git_bounded_result(
        workspace,
        args,
        max_output_bytes=max_output_bytes,
        timeout=timeout,
        allowed_returncodes=allowed_returncodes,
        input=input,
    ).payload


def _run_git_bounded_result(
    workspace: Path,
    args: list[str],
    *,
    max_output_bytes: int,
    timeout: int = 60,
    allowed_returncodes: tuple[int, ...] = (0,),
    input: bytes | None = None,
) -> _BoundedGitResult:
    """Run bounded Git plumbing and retain a bounded stderr excerpt.

    stderr is drained on its own thread under a small cap for the same reason
    stdout is: an unread pipe deadlocks the child, and an unbounded one is a
    memory hazard. The excerpt exists only so an input failure can be
    classified and explained; it never becomes evidence.
    """

    cmd = ["git", "--no-replace-objects", "-C", str(workspace), *args]
    env = _git_object_environment()
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed local Git argv, no shell.
            cmd,
            env=env,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        )
    except OSError as exc:
        return _BoundedGitResult(payload=None, stderr=str(exc))

    output = bytearray()
    errors = bytearray()
    exceeded = False
    read_failed = False

    def _drain_stdout() -> None:
        nonlocal exceeded, read_failed
        assert process.stdout is not None
        try:
            while chunk := process.stdout.read(64 * 1024):
                remaining = max_output_bytes + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(output) > max_output_bytes:
                    exceeded = True
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
        except OSError:
            read_failed = True

    def _drain_stderr() -> None:
        assert process.stderr is not None
        try:
            while chunk := process.stderr.read(4096):
                remaining = _GIT_STDERR_LIMIT - len(errors)
                if remaining > 0:
                    errors.extend(chunk[:remaining])
        except OSError:
            pass

    reader = threading.Thread(target=_drain_stdout, daemon=True)
    reader.start()
    error_reader = threading.Thread(target=_drain_stderr, daemon=True)
    error_reader.start()
    write_failed = False

    def _write_stdin() -> None:
        nonlocal write_failed
        assert process.stdin is not None
        try:
            process.stdin.write(input or b"")
            process.stdin.close()
        except (BrokenPipeError, OSError):
            write_failed = True

    writer = (
        threading.Thread(target=_write_stdin, daemon=True)
        if input is not None
        else None
    )
    if writer is not None:
        writer.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join()
        error_reader.join()
        if writer is not None:
            writer.join()
        return _BoundedGitResult(
            payload=None,
            timed_out=True,
            stderr=_decode_git_stderr(errors),
        )
    reader.join()
    error_reader.join()
    if writer is not None:
        writer.join()
    stderr_text = _decode_git_stderr(errors)
    if (
        returncode not in allowed_returncodes
        or exceeded
        or read_failed
        or write_failed
    ):
        return _BoundedGitResult(
            payload=None,
            exceeded=exceeded,
            stderr=stderr_text,
        )
    return _BoundedGitResult(payload=bytes(output), stderr=stderr_text)


def _decode_git_stderr(payload: bytes | bytearray) -> str:
    """Return Git's diagnostic text as one safe, bounded, single-line string."""

    raw = bytes(payload)
    if len(raw) >= _GIT_STDERR_LIMIT:
        # The capture stopped at its limit, so its last word may be the head
        # of a longer one: a token cut there is too short for any rule to
        # recognise. Drop everything after the last whitespace (#1079 review).
        whole = re.match(rb"(?s)(.*\s)\S*\Z", raw)
        raw = whole.group(1) if whole else b""
    text = raw.decode("utf-8", errors="replace")
    collapsed = " ".join(
        part
        for part in "".join(
            character if character.isprintable() else " " for character in text
        ).split()
    )
    # Redacted, then bounded (#1079): Git can echo a remote URL or a header,
    # and this text is published as a diagnostic's detail. It used to be
    # bounded and never redacted.
    collapsed = redact_text(collapsed) or ""
    if len(collapsed) > _GIT_STDERR_EXCERPT_CHARS:
        collapsed = truncate_redacted(collapsed, _GIT_STDERR_EXCERPT_CHARS).rstrip() + "…"
    return collapsed


def promisor_remotes(workspace: Path) -> tuple[str, ...]:
    """The remotes this partial clone was promised objects by, as a command can name them.

    Read from repository configuration only, in the order Git lists it: every
    ``remote.<name>.promisor`` Git reads as true, then the older
    ``extensions.partialClone`` when it names another remote. A name is kept
    only when ``git fetch <name>`` means that configured remote: it has a
    ``remote.<name>.url``, Git accepts it as a remote name (the
    ``refs/remotes/<name>/`` ref-format test ``git remote add`` applies, so
    ``up/stream`` is kept), it is UTF-8, and it does not start with ``-``,
    which ``git fetch`` would read as an option. Empty when none is left; no
    other name, ``origin`` included, is supplied in its place. Nothing is
    fetched (#817).
    """

    promisor_flags = _git_config_records(
        workspace, r"^remote\..+\.promisor$", as_bool=True
    )
    other_keys = _git_config_records(
        workspace, r"^(remote\..+\.url|extensions\.partialclone)$"
    )
    if promisor_flags is None or other_keys is None:
        return ()
    candidates = [
        _remote_subsection(key, "promisor")
        for key, value in promisor_flags
        if value == "true"
    ]
    candidates.extend(
        value for key, value in other_keys if key == "extensions.partialclone"
    )
    with_url = {
        _remote_subsection(key, "url")
        for key, _ in other_keys
        if key != "extensions.partialclone"
    }
    names: list[str] = []
    for name in dict.fromkeys(candidates):
        if not name or name.startswith("-") or name not in with_url:
            continue
        accepted = _run_git(
            workspace,
            ["check-ref-format", f"refs/remotes/{name}/test"],
            check=False,
        )
        if accepted.returncode == 0:
            names.append(name)
    return tuple(names)


def _git_config_records(
    workspace: Path, pattern: str, *, as_bool: bool = False
) -> list[tuple[str, str]] | None:
    """``git config -z --get-regexp`` as ``(key, value)`` pairs, in file order.

    ``as_bool`` lets Git parse the values, so ``yes``, ``1`` and a bare key
    read as ``true`` exactly as Git itself reads them. ``[]`` when nothing
    matches, ``None`` when Git refuses the read (a value it cannot parse as a
    boolean, say) or the output passes its bound. A record that is not UTF-8
    is dropped rather than decoded into a name that differs from the
    configured one.
    """

    payload = _run_git_bounded_output(
        workspace,
        [
            "config",
            "--includes",
            "-z",
            *(["--type=bool"] if as_bool else []),
            "--get-regexp",
            pattern,
        ],
        max_output_bytes=_PROMISOR_CONFIG_LIMIT,
        allowed_returncodes=(0, 1),
    )
    if payload is None:
        return None
    records: list[tuple[str, str]] = []
    for raw in payload.split(b"\0"):
        if not raw:
            continue
        try:
            record = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        key, _, value = record.partition("\n")
        records.append((key, value))
    return records


def _remote_subsection(key: str, variable: str) -> str:
    """``<name>`` from ``remote.<name>.<variable>``, as configured (Git lowercases only the outer parts)."""

    return key[len("remote.") : -len(f".{variable}")]


class PromisedObjectsMissingError(ConfigError):
    """``commit``'s tree needs objects a partial clone never fetched (``objects_missing``)."""

    def __init__(self, commit: str) -> None:
        self.commit = commit
        super().__init__(
            f"Commit {commit} needs Git objects this partial clone never fetched."
        )


def archive_fetched_tree(
    workspace: Path,
    commit: str,
    destination: Path,
    *,
    scope: Callable[[str], bool] | None = None,
    record_gitlinks: bool = False,
    rescope: Callable[[Path], tuple[Path, Callable[[str], bool]] | None] | None = None,
) -> dict[str, str]:
    """:func:`archive_tree`, naming a partial clone's unfetched objects as such.

    A blobless clone fails the object copy with a :class:`ConfigError`, and a
    treeless one fails the tree lookup before it with a
    :class:`subprocess.CalledProcessError`. Both become
    :class:`PromisedObjectsMissingError` when :func:`promised_objects_missing`
    proves the clone lacks promised objects, so a caller can name the
    hydration instead of a traceback (#817). Other Git
    process failures are normalized to ConfigError at this subprocess boundary,
    so CLI callers need no process-execution import just to catch an error.
    """

    try:
        return archive_tree(
            workspace, commit, destination, scope=scope, record_gitlinks=record_gitlinks,
            rescope=rescope,
        )
    except (ConfigError, subprocess.CalledProcessError) as exc:
        if promised_objects_missing(workspace, commit):
            raise PromisedObjectsMissingError(commit) from exc
        if isinstance(exc, subprocess.CalledProcessError):
            raise ConfigError(f"Could not materialize Git tree for {commit}: {exc}") from exc
        raise


def promised_objects_missing(workspace: Path, commit: str) -> bool:
    """Whether ``commit``'s tree needs objects a partial clone never fetched.

    Asked of the repository rather than read from a failure's wording: a walk
    of the commit's objects fails, and the same walk succeeds once objects a
    promisor remote still owes are allowed to be missing. A shallow boundary
    is not crossed (``--no-walk``), and an object missing without a promise,
    as in a corrupt repository, is not reported as one. Lazy fetching is
    disabled for both walks, so asking fetches nothing (#817).
    """

    walk = ["rev-list", "--objects", "--no-walk", "--quiet"]
    try:
        complete = _run_git(workspace, [*walk, commit], check=False)
        if complete.returncode == 0:
            return False
        promised = _run_git(
            workspace, [*walk, "--missing=allow-promisor", commit], check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return promised.returncode == 0


def _history_is_truncated(workspace: Path) -> bool | None:
    """Whether this checkout's commit history is shallow.

    ``None`` when Git could not answer. That is not "no": it is the one case
    where the caller has no basis to claim the histories are unrelated, so it
    must not synthesize either repair.
    """

    result = _run_git(
        workspace, ["rev-parse", "--is-shallow-repository"], check=False
    )
    if result.returncode != 0:
        return None
    answer = result.stdout.strip().casefold()
    if answer == "true":
        return True
    if answer == "false":
        return False
    return None


def _redact_local_paths(text: str, workspace: Path) -> str:
    """Keep local filesystem layout out of a diagnostic that ships in JSON."""

    if not text:
        return text
    for absolute in (str(workspace.resolve()), str(workspace), str(Path.home())):
        if absolute and absolute != os.sep:
            text = text.replace(absolute, "<redacted-path>")
    return text


def _classify_diff_failure(
    result: _BoundedGitResult,
    *,
    limit_reason: DiffInputReason,
    workspace: Path | None = None,
) -> tuple[DiffInputReason, str]:
    """Map one failed bounded Git read to a stable reason and a safe detail.

    Classification reads Git's own diagnostic rather than guessing from the
    repository shape, so a shallow clone with no merge base and a partial clone
    with unfetched blobs stop being reported as the same failure.
    """

    if result.exceeded:
        return limit_reason, ""
    detail = (
        _redact_local_paths(result.stderr, workspace)
        if workspace is not None
        else result.stderr
    )
    if result.timed_out:
        return "git_timeout", detail
    lowered = result.stderr.casefold()
    if "no merge base" in lowered:
        # "No merge base" has two causes with opposite repairs, and Git reports
        # them identically. A shallow checkout truncated a merge base that does
        # exist — deepening restores it. Two genuinely unrelated roots have no
        # common ancestor at all, and routing that to another fetch loops an
        # agent forever, so it goes to whoever chose the base ref.
        truncated = (
            _history_is_truncated(workspace) if workspace is not None else None
        )
        if truncated is True:
            return "merge_base_missing", detail
        if truncated is False:
            return "unrelated_histories", detail
        return "git_failed", detail
    if any(
        marker in lowered
        for marker in (
            "promisor remote",
            "lazy fetching disabled",
            "missing blob",
            "unable to read",
            "cannot read object",
            "object file is empty",
        )
    ):
        return "objects_missing", detail
    if any(
        marker in lowered
        for marker in (
            "unknown revision",
            "ambiguous argument",
            "not a valid object name",
            "bad revision",
            "bad object",
        )
    ):
        return "refs_missing", detail
    return "git_failed", detail


def _run_git(
    workspace: Path,
    args: list[str],
    *,
    check: bool = True,
    text: bool = True,
    input: bytes | None = None,
) -> subprocess.CompletedProcess:
    cmd = ["git", "--no-replace-objects", "-C", str(workspace), *args]
    env = _git_object_environment()
    return _run_process(
        cmd,
        capture_output=True,
        check=check,
        env=env,
        input=input,
        text=text,
        timeout=60,
    )


def _run_process(
    cmd: list[str],
    *,
    capture_output: bool = False,
    check: bool = False,
    env: dict[str, str],
    text: bool = False,
    timeout: int,
    input: bytes | None = None,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
) -> subprocess.CompletedProcess:
    """Single audited no-shell process boundary for local Git plumbing."""

    return subprocess.run(
        cmd,
        capture_output=capture_output,
        check=check,
        env=env,
        input=input,
        stderr=stderr,
        stdin=stdin,
        stdout=stdout,
        text=text,
        timeout=timeout,
    )


def staged_paths_under(workspace: Path, subdir: str) -> list[str]:
    """Return staged (index) paths under ``subdir``, relative to ``workspace``.

    Reads the git index only (``git diff --cached --name-only --relative``);
    never fetches or writes. Used to warn when generated Agents Shipgate
    reports have been staged for commit — they are generated artifacts that
    ``init`` already gitignores. Returns ``[]`` outside a git checkout.
    """

    prefix = subdir.rstrip("/") + "/"
    result = _run_git(
        workspace,
        [
            *_SAFE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            "--cached",
            "--name-only",
            "--relative",
        ],
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip().startswith(prefix)]


def _commit_present_at_ref(workspace: Path, ref: str) -> bool | None:
    """Whether ``ref`` resolves to a commit, preserving Git's error state.

    Unlike :func:`commit_sha`, an unborn ref is ``False`` while an operational
    Git failure is ``None``.  Human-artifact ordering needs that distinction:
    a repository with no first commit is cold by construction, but an
    unreadable repository must fail closed to the established verdict-first
    order.
    """

    _validate_ref_token(ref)
    result = _run_git(
        workspace,
        [
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{ref}^{{commit}}",
        ],
        check=False,
    )
    if result.returncode == 0:
        return bool(result.stdout.strip()) or None
    if result.returncode == 1:
        return False
    return None


def path_committed_at_head(workspace: Path, path: Path) -> bool | None:
    """Whether ``path`` is a blob at ``HEAD``, with errors kept unknown."""

    try:
        head_present = _commit_present_at_ref(workspace, "HEAD")
        if head_present is not True:
            return head_present
        return path_present_at_ref(workspace, "HEAD", path)
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass(frozen=True)
class OutputDirectoryInventory:
    """What Git holds beneath one directory, split by how Git holds it."""

    #: Paths committed at ``HEAD``.
    head: tuple[str, ...]
    #: Paths committed in another compared commit and absent at ``HEAD``: the
    #: change being evaluated removes them.
    removed: tuple[str, ...]
    #: Index entries not committed at ``HEAD``: staged additions.
    staged: tuple[str, ...]
    #: Paths committed at ``HEAD`` that the index no longer holds: staged
    #: deletions.
    unstaged: tuple[str, ...]
    #: Untracked paths that survive the standard exclude rules.
    untracked: tuple[str, ...]
    #: Those of the caller's ``probe_ignored`` paths the standard exclude rules
    #: ignore. A probed path need not exist: the rules match names, so this is
    #: whether a file written there later would be ignored.
    ignored: tuple[str, ...] = ()


def output_directory_inventory(
    workspace: Path,
    directory: str,
    *,
    compared: Sequence[str] = (),
    probe_ignored: Sequence[str] = (),
) -> OutputDirectoryInventory:
    """Every path Git could report beneath ``directory``, if it were not excluded.

    ``directory`` is spelled relative to the repository root as Git stores it.
    A change set leaves out everything beneath an excluded directory: content
    committed at ``HEAD`` (and every later edit to it), content a compared
    commit held that the change deletes, staged additions, and untracked files
    the standard exclude rules keep. That is the question an output directory
    is judged by — what leaving it out would hide (#804). ``compared`` names
    the other commits the caller's change set is taken against, such as a
    merge base; one that does not resolve contributes nothing, because no
    change set is taken against it.

    ``probe_ignored`` names root-relative paths, typically files a run has not
    written yet, to test against the same exclude rules: whether a file a run
    would write there is one Git reports at all.

    Every read is bounded like every other metadata read here. A directory too
    large to list, or a Git failure, raises rather than reading as empty.
    """

    pathspec = [f":(top,literal){directory}"]

    def listed(args: list[str]) -> set[str]:
        payload = _run_git_bounded_output(
            workspace,
            [*_SAFE_DIFF_CONFIG, *args, "-z", "--", *pathspec],
            max_output_bytes=_DIFF_METADATA_LIMIT,
        )
        if payload is None:
            raise ConfigError(
                f"Git could not list the paths beneath {directory} within its "
                "static output bounds."
            )
        return {os.fsdecode(raw) for raw in payload.split(b"\0") if raw}

    def committed(ref: str) -> set[str]:
        commit = commit_sha(workspace, ref)
        if commit is None:
            return set()
        return listed(["ls-tree", "-r", "--name-only", commit])

    at_head = committed("HEAD")
    elsewhere: set[str] = set()
    for ref in compared:
        elsewhere |= committed(ref)
    index = listed(["ls-files", "--cached"])
    untracked = listed(["ls-files", "--others", "--exclude-standard"])
    ignored: set[str] = set()
    if probe_ignored:
        # Exit 1 is "none of them is ignored", not a failure.
        payload = _run_git_bounded_output(
            workspace,
            [*_SAFE_DIFF_CONFIG, "check-ignore", "--stdin", "-z"],
            max_output_bytes=_DIFF_METADATA_LIMIT,
            allowed_returncodes=(0, 1),
            input=b"".join(os.fsencode(path) + b"\0" for path in probe_ignored),
        )
        if payload is None:
            raise ConfigError(
                f"Git could not read the ignore rules beneath {directory} within "
                "its static output bounds."
            )
        ignored = {os.fsdecode(raw) for raw in payload.split(b"\0") if raw}
    return OutputDirectoryInventory(
        head=tuple(sorted(at_head)),
        removed=tuple(sorted(elsewhere - at_head)),
        staged=tuple(sorted(index - at_head)),
        unstaged=tuple(sorted(at_head - index)),
        untracked=tuple(sorted(untracked)),
        ignored=tuple(sorted(ignored.intersection(probe_ignored))),
    )


__all__ = [
    "active_replace_refs",
    "archive_fetched_tree",
    "archive_tree",
    "BinaryCapabilityDiffError",
    "collect_diff_context",
    "collect_revspec_diff_context",
    "commit_date",
    "commit_sha",
    "DefaultBaseDetection",
    "detect_default_base",
    "detect_default_base_with_notes",
    "DiffContext",
    "DiffInputError",
    "diff_context",
    "diff_revspec_context",
    "ensure_git_workspace",
    "git_directories",
    "git_path",
    "GitPushEndpoint",
    "merge_base_sha",
    "objects_missing_remediation",
    "output_directory_inventory",
    "OutputDirectoryInventory",
    "path_committed_at_head",
    "path_present_at_ref",
    "PromisedObjectsMissingError",
    "promised_objects_missing",
    "promisor_remotes",
    "read_bytes_at_ref",
    "read_file_at_ref",
    "removes_any_tracked_path",
    "repository_identity",
    "require_merge_base_sha",
    "resolve_tree_path_identity",
    "resolve_git_push_endpoint",
    "resolve_source_head_identity",
    "ref_exists",
    "SourceHeadIdentity",
    "staged_paths_under",
    "tree_sha",
    "UnboundGitConfigurationError",
    "validate_source_head_identity",
    "working_tree_context",
    "working_tree_paths",
]


def shallow_merge_base_is_proven(workspace: Path, base: str, head: str, resolved: str) -> bool:
    """Reject hidden better ancestors without requiring unrelated old history.

    Inputs are resolved commit IDs. A returned common ancestor need not be the
    best one when a merge exposes older ancestry around a shallow graft.
    """
    if resolved in {base, head}:
        return True
    shallow = _history_is_truncated(workspace)
    if shallow is False:
        return True
    if shallow is None:
        return False
    roots = _run_git(
        workspace,
        ["rev-list", "--max-parents=0", "--max-count=1", base, head, "--not", resolved],
        check=False,
    )
    # Removing the candidate's ancestry must leave no root (including grafts)
    # reachable from either tip. Otherwise a hidden edge can conceal a newer
    # common ancestor. This is conservative for unrelated shallow side branches.
    return roots.returncode == 0 and not roots.stdout.strip()
