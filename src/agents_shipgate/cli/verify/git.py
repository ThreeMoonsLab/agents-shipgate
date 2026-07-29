from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.schemas.human_authorization import canonical_https_git_endpoint

_GIT_OBJECT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


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
    result = _run_git(
        workspace,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
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
    result = _run_git(
        workspace,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        check=False,
    )
    return result.returncode == 0


def tree_sha(workspace: Path, ref: str) -> str:
    result = _run_git(workspace, ["rev-parse", f"{ref}^{{tree}}"])
    return result.stdout.strip()


def merge_base_sha(workspace: Path, base: str, head: str) -> str | None:
    result = _run_git(workspace, ["merge-base", base, head], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def commit_date(workspace: Path, ref: str) -> str:
    return _run_git(workspace, ["show", "-s", "--format=%cs", ref]).stdout.strip()


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
    revspec = f"{base}...{head}"
    names = _run_git(workspace, ["diff", "--name-only", revspec])
    body = _run_git(workspace, ["diff", revspec])
    paths = [line for line in names.stdout.splitlines() if line.strip()]
    return paths, body.stdout


def read_file_at_ref(workspace: Path, ref: str, path: Path) -> str | None:
    """Return one file's text at ``ref`` without materializing the tree."""

    result = _run_git(
        workspace,
        ["show", f"{ref}:{path.as_posix()}"],
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout

def paths_named_at_ref(
    workspace: Path, ref: str, names: frozenset[str]
) -> list[str] | None:
    """Tracked paths at ``ref`` whose file name is one of ``names``.

    Used to prove that a ref carries no Shipgate manifest *anywhere*, not just
    at the configured path — otherwise moving the manifest to a new path would
    make a modified gate look like a first adoption.

    ``None`` means the listing failed, which is not the same as an empty
    result: callers must treat it as "cannot prove", never as proven absence.
    """

    result = _run_git(workspace, ["ls-tree", "-r", "--name-only", ref], check=False)
    if result.returncode != 0:
        return None
    return [
        line
        for line in result.stdout.splitlines()
        if line.strip() and line.rsplit("/", maxsplit=1)[-1] in names
    ]


# Suffixes a Shipgate manifest can plausibly carry. A rename away from one of
# these is the shape the adoption claim must not survive.
_MANIFEST_SUFFIXES = (".yaml", ".yml")


def carries_manifest_like_yaml(workspace: Path, ref: str) -> bool | None:
    """Whether ``ref`` contains any YAML that looks like a Shipgate manifest.

    A basename check cannot prove a base carries no gate: a manifest may be
    called anything, so a base that keeps an operational ``old-gate.yml`` while
    the head adds ``new-gate.yml`` passes every name test. This asks a content
    question instead — any tracked YAML carrying the manifest's required
    top-level keys. It over-matches by design: an unrelated YAML with
    ``project:`` and ``agent:`` merely costs the adoption wording, which is the
    safe direction.

    ``None`` means the search could not run; callers must treat that as "cannot
    prove", never as absence.
    """

    result = _run_git(
        workspace,
        [
            "grep",
            "--all-match",
            "-l",
            "-e",
            "^project:",
            "-e",
            "^agent:",
            ref,
            "--",
            "*.yaml",
            "*.yml",
        ],
        check=False,
    )
    if result.returncode == 1:  # documented: nothing matched
        return False
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


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

    args = ["diff", "--name-status", "--find-renames", "--diff-filter=DR"]
    args.extend([f"{base}...{head}"] if base else ["HEAD"])
    result = _run_git(workspace, args, check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        fields = [field for field in line.split("\t") if field.strip()]
        if len(fields) < 2:
            continue
        source = fields[1].replace("\\", "/")
        if source.lower().endswith(_MANIFEST_SUFFIXES):
            return True
    return False


def working_tree_context(workspace: Path) -> tuple[list[str], str]:
    """Return uncommitted changed paths and tracked-file diff text.

    ``git diff HEAD`` includes staged and unstaged tracked changes. Untracked
    file paths are included for trigger/check context, but their contents are
    intentionally not read into the diff body.
    """

    names = _run_git(workspace, ["diff", "HEAD", "--name-only"])
    body = _run_git(workspace, ["diff", "HEAD"])
    paths = [line for line in names.stdout.splitlines() if line.strip()]
    untracked = _run_git(workspace, ["ls-files", "--others", "--exclude-standard"])
    for line in untracked.stdout.splitlines():
        stripped = line.strip()
        if stripped and stripped not in paths:
            paths.append(stripped)
    return paths, body.stdout


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
        portable_parts = [
            unicodedata.normalize("NFKC", part).casefold().rstrip(" .")
            for part in path_text.split("/")
        ]
        if any(not part for part in portable_parts):
            raise ConfigError(f"Git tree path is not portable: {path_text}")
        portable_key = "/".join(portable_parts)
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


def _git_object_id(kind: str, data: bytes, *, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    digest.update(f"{kind} {len(data)}\0".encode("ascii"))
    digest.update(data)
    return digest.hexdigest()


def _git_object_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_PROTOCOL_FROM_USER": "0",
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


def _run_git(
    workspace: Path,
    args: list[str],
    *,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    cmd = ["git", "--no-replace-objects", "-C", str(workspace), *args]
    env = os.environ.copy()
    env.update(
        {
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_PROTOCOL_FROM_USER": "0",
        }
    )
    return _run_process(
        cmd,
        capture_output=True,
        check=check,
        env=env,
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
    result = _run_git(workspace, ["diff", "--cached", "--name-only", "--relative"], check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip().startswith(prefix)]


__all__ = [
    "active_replace_refs",
    "archive_tree",
    "commit_date",
    "commit_sha",
    "DefaultBaseDetection",
    "detect_default_base",
    "detect_default_base_with_notes",
    "diff_context",
    "ensure_git_workspace",
    "git_path",
    "GitPushEndpoint",
    "merge_base_sha",
    "read_file_at_ref",
    "repository_identity",
    "resolve_git_push_endpoint",
    "resolve_source_head_identity",
    "ref_exists",
    "SourceHeadIdentity",
    "staged_paths_under",
    "tree_sha",
    "validate_source_head_identity",
    "working_tree_context",
]
