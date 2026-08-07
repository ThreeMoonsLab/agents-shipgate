from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml

from agents_shipgate.core.boundary_registry import is_agent_boundary_path
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.trust_roots import trust_root_class_for
from agents_shipgate.schemas.human_authorization import canonical_https_git_endpoint

_GIT_OBJECT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_WORKTREE_FILTER_CONFIG_LIMIT = 1024 * 1024
_WORKTREE_ATTRIBUTE_LIST_LIMIT = 8 * 1024 * 1024
_DIFF_CONFIG_LIMIT = 1024 * 1024
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
        "The two refs share no reachable merge base, which a shallow clone "
        "causes. Deepen history (`git fetch --deepen=<n>`, or "
        "`git fetch --unshallow` / checkout with `fetch-depth: 0`), then rerun."
    ),
    "objects_missing": (
        "This checkout is a partial clone and the objects the diff needs were "
        "never fetched. Verification runs with GIT_NO_LAZY_FETCH=1 and will "
        "not fetch them implicitly. Hydrate them (for example "
        "`git fetch --refetch origin`, or clone without `--filter`), then "
        "rerun."
    ),
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
        return f"{scope} ({self.reason}).{detail} {self.remediation}"


class DiffInputError(ConfigError):
    """A diff that could not be read in full, with its classified reason."""

    def __init__(self, context: DiffContext) -> None:
        self.context = context
        super().__init__(context.note.strip())


class _UnavailableRevisionError(ConfigError):
    """A revision expression that names refs this checkout does not have."""


_SAFE_DIFF_CONFIG = [
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.safecrlf=false",
    "-c",
    "core.eol=lf",
    "-c",
    "core.bigFileThreshold=32m",
    "-c",
    "core.fileMode=false",
    "-c",
    "core.precomposeUnicode=false",
    "-c",
    "submodule.recurse=false",
    "-c",
    "core.quotePath=false",
    "-c",
    f"core.attributesFile={os.devnull}",
    "-c",
    f"diff.orderFile={os.devnull}",
    "-c",
    "diff.suppressBlankEmpty=false",
    "-c",
    "diff.renameLimit=32767",
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
    """

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


def detect_default_base(workspace: Path, head: str = "HEAD") -> str | None:
    """Best-effort default base ref for PR-style diff enrichment.

    Tries the remote default branch (``origin/HEAD``) first, then remote
    conventional candidates (``origin/main``, ``origin/master``). A
    candidate qualifies only when it exists locally and points at a
    different commit than ``head`` — diffing a branch against itself adds
    scan cost without diff signal. Local ``main``/``master`` are never
    selected implicitly because they are often stale in CI and worktrees;
    pass ``--base main`` explicitly when that is intended. Never fetches;
    this only reads refs that already exist in the checkout.
    """

    return detect_default_base_with_notes(workspace, head).base


def detect_default_base_with_notes(workspace: Path, head: str = "HEAD") -> DefaultBaseDetection:
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
        if sha is not None and sha != head_sha:
            selected_base = candidate
            selected_base_sha = sha
            break
    notes = _skipped_local_base_notes(
        workspace,
        head_sha,
        selected_base_sha=selected_base_sha,
    )
    return DefaultBaseDetection(base=selected_base, notes=notes)


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
) -> None:
    """Fail closed when a source-like path is hidden behind a binary marker."""

    result = _run_git_bounded_result(
        workspace,
        [
            *_SAFE_DIFF_CONFIG,
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
        raise ConfigError(
            "Git tree path identity could not be established within static "
            "resource bounds."
        )
    try:
        paths = [
            raw.decode("utf-8", errors="strict")
            for raw in listing.split(b"\0")
            if raw
        ]
    except UnicodeDecodeError as exc:
        raise ConfigError("Git tree contains a non-UTF-8 path") from exc
    requested_key = _portable_tree_path_key(requested_text)
    matches = [path for path in paths if _portable_tree_path_key(path) == requested_key]
    if len(matches) > 1:
        raise ConfigError(
            "Git tree contains filesystem-colliding paths for configured "
            f"manifest {requested_text!r}: {matches!r}"
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


def working_tree_context(
    workspace: Path,
    *,
    exclude: Path | None = None,
    reject_index_hidden: bool = False,
) -> tuple[list[str], str]:
    """Return uncommitted changed paths and tracked-file diff text.

    ``git diff HEAD`` includes staged and unstaged tracked changes. Untracked
    file paths are included for trigger/check context, but their contents are
    intentionally not read into the diff body.
    """

    _reject_unbound_diff_configuration(workspace)
    _reject_executable_worktree_filters(workspace)
    pathspec = _worktree_pathspec(workspace, exclude)
    if reject_index_hidden:
        _reject_index_hidden_capability_paths(workspace, pathspec=pathspec)
    names = _run_git_bounded_result(
        workspace,
        [
            *_SAFE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            "HEAD",
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
    body = _run_git_bounded_result(
        workspace,
        [
            *_SAFE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            "HEAD",
            "--",
            *pathspec,
        ],
        max_output_bytes=_DIFF_BODY_LIMIT,
    )
    paths = sorted(_paths_from_name_status(names.payload))
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
        _reject_binary_capability_paths(workspace, "HEAD", pathspec=pathspec)
    except BinaryCapabilityDiffError as exc:
        exc.changed_paths = tuple(paths)
        exc.diff_text = diff_text
        raise
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
    return paths, diff_text


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
        shown = ", ".join(sorted(active)[:3])
        raise ConfigError(
            "Static worktree collection refuses executable Git "
            f"filters ({shown}). Commit the intended changes and verify refs, "
            "or provide an explicit inert diff artifact."
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
        shown = ", ".join(sorted(attributed_paths)[:3])
        raise ConfigError(
            "Static worktree collection refuses Git filter attributes because "
            f"their normalization driver is not receipt-bound ({shown}). "
            "Commit the intended changes and verify refs instead."
        )


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
    if any(record.partition(b"\n")[2].strip() for record in configured.split(b"\0")):
        raise ConfigError(
            "Deterministic diff collection refuses repository-local diff.* "
            "configuration because it is not receipt-bound."
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
        raise ConfigError(
            "Deterministic diff collection refuses non-empty or aliased "
            ".git/info/attributes because it is not receipt-bound."
        )


def archive_tree(workspace: Path, ref: str, destination: Path) -> None:
    """Materialize exact Git blobs without export-ignore or substitutions."""

    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ConfigError("Git archive destination must be empty")
    commit = commit_sha(workspace, ref)
    if commit is None:
        raise ConfigError(f"Git archive ref is unavailable: {ref}")
    with tempfile.TemporaryDirectory(prefix="agents-shipgate-git-snapshot-") as raw:
        git_dir = Path(raw) / "git"
        _copy_verified_commit_graph(workspace, commit=commit, git_dir=git_dir)
        _materialize_isolated_tree(git_dir, commit=commit, destination=destination)


def _copy_verified_commit_graph(workspace: Path, *, commit: str, git_dir: Path) -> None:
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
            input=f"{commit}\n".encode("ascii"),
            stdout=output,
            stderr=subprocess.PIPE,
            check=False,
            env=env,
            timeout=120,
        )
    if packed.returncode != 0:
        detail = packed.stderr.decode("utf-8", errors="replace").strip()
        raise ConfigError(f"Could not copy verified Git objects: {detail}")
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
    checked = _run_git_dir(
        git_dir,
        ["fsck", "--strict", "--no-reflogs", "--no-dangling", commit],
        check=False,
    )
    if checked.returncode != 0:
        detail = checked.stderr.strip() or checked.stdout.strip()
        raise ConfigError(f"Copied Git object graph failed integrity validation: {detail}")


def _materialize_isolated_tree(git_dir: Path, *, commit: str, destination: Path) -> None:
    listing = _run_git_dir(git_dir, ["ls-tree", "-r", "-z", commit], text=False).stdout
    root = destination.resolve()
    entries: list[tuple[str, str, str]] = []
    portable_paths: dict[str, str] = {}
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        metadata, raw_path = raw.split(b"\t", 1)
        mode, object_type, oid = metadata.decode("ascii").split(" ", 2)
        path_text = raw_path.decode("utf-8", errors="strict")
        if "\\" in path_text:
            raise ConfigError(f"Git tree path is not portable: {path_text}")
        portable_key = _portable_tree_path_key(path_text)
        prior = portable_paths.setdefault(portable_key, path_text)
        if prior != path_text:
            raise ConfigError(
                "Git tree contains filesystem-colliding paths: "
                f"{prior!r} and {path_text!r}"
            )
        if object_type != "blob" or mode in {"120000", "160000"}:
            raise ConfigError(
                f"Git tree contains unsupported external binding at {path_text} "
                f"(mode {mode}, type {object_type})."
            )
        entries.append((mode, oid, path_text))

    object_format = _run_git_dir(git_dir, ["rev-parse", "--show-object-format"]).stdout.strip()
    expected_digests: dict[str, str] = {}
    for mode, oid, path_text in entries:
        target = (root / path_text).resolve()
        if target == root or root not in target.parents:
            raise ConfigError(f"Git tree path escapes destination: {path_text}")
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = _run_git_dir(git_dir, ["cat-file", "blob", oid], text=False).stdout
        if _git_object_id("blob", blob, algorithm=object_format) != oid:
            raise ConfigError(f"Git blob failed object-ID validation: {path_text}")
        target.write_bytes(blob)
        expected_digests[path_text] = hashlib.sha256(blob).hexdigest()
        if mode == "100755":
            os.chmod(target, 0o755)

    materialized = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if materialized != expected_digests:
        raise ConfigError("Materialized Git tree differs from the verified object graph")


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
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    env.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def _run_git_dir(
    git_dir: Path,
    args: list[str],
    *,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    return _run_process(
        ["git", "--no-replace-objects", f"--git-dir={git_dir}", *args],
        capture_output=True,
        check=check,
        env=_git_object_environment(),
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

    text = bytes(payload).decode("utf-8", errors="replace")
    collapsed = " ".join(
        part
        for part in "".join(
            character if character.isprintable() else " " for character in text
        ).split()
    )
    if len(collapsed) > _GIT_STDERR_EXCERPT_CHARS:
        collapsed = collapsed[:_GIT_STDERR_EXCERPT_CHARS].rstrip() + "…"
    return collapsed


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
        return "merge_base_missing", detail
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

    Defined below ``_run_git`` so the line-pinned static-only allowlist entry
    for that subprocess call-site (``tests/test_adapter_static_only.py``)
    stays stable.
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


__all__ = [
    "active_replace_refs",
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
    "git_path",
    "GitPushEndpoint",
    "merge_base_sha",
    "read_file_at_ref",
    "repository_identity",
    "resolve_tree_path_identity",
    "resolve_git_push_endpoint",
    "resolve_source_head_identity",
    "ref_exists",
    "SourceHeadIdentity",
    "staged_paths_under",
    "tree_sha",
    "validate_source_head_identity",
    "working_tree_context",
]
