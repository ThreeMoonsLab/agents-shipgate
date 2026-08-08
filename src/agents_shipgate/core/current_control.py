"""Lifecycle and safe-read protocol for the current-control pointer.

The pointer is the only artifact in the reports directory whose meaning is
"this is current *now*".  Everything else in that directory means "this is what
some run produced".  Three rules keep that distinction true across crashes,
concurrent readers, and coding-agent conversation boundaries:

1. **Invalidate first.**  A run replaces the pointer with a non-terminal
   ``unavailable`` marker before it touches any other artifact.  A process that
   dies mid-run therefore leaves a directory that denies every cached decision
   instead of one that still advertises the previous terminal verdict for a
   workspace that has since moved.
2. **Publish last.**  The terminal pointer is written only after every artifact
   it references exists and has been hashed, and it is the last file made
   visible for the run.
3. **Publish atomically.**  Same-directory temporary file, ``fsync``,
   ``os.replace``, then ``fsync`` of the directory.  A reader never observes a
   half-written pointer.

Readers use :func:`read_current_control`, which re-reads the pointer after
validating the artifacts it binds.  If the identity moved underneath the read,
the whole read is rejected rather than returning a mix of two generations.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import ParamSpec, TypeVar

from agents_shipgate.core.errors import AgentsShipgateError
from agents_shipgate.core.verification_identity import (
    read_regular_file_beneath,
    worktree_overlay,
)
from agents_shipgate.schemas.agent_control import AgentControl
from agents_shipgate.schemas.current_control import (
    CURRENT_CONTROL_ARTIFACT_NAME,
    RECEIPT_ARTIFACT_KEY,
    AgentActionRequiredCurrentControl,
    CompleteCurrentControl,
    CurrentControlArtifactRef,
    CurrentControlOperation,
    CurrentControlPointer,
    CurrentControlProjection,
    CurrentControlWorkspaceIdentity,
    HumanReviewRequiredCurrentControl,
    UnavailableCurrentControl,
    current_control_identity_payload,
)
from agents_shipgate.schemas.verification_identity import (
    VerificationPlan,
    VerificationReceipt,
    content_id,
    validate_portable_path,
)

MAX_CURRENT_CONTROL_BYTES = 1024 * 1024
MAX_BOUND_ARTIFACT_BYTES = 256 * 1024 * 1024

# Stable key -> filename map for everything the pointer may bind.  Keys are the
# contract-visible names; only files that actually exist are bound, so the
# artifact set itself tells a reader which evidence this run produced.
CURRENT_CONTROL_ARTIFACT_FILENAMES: dict[str, str] = {
    RECEIPT_ARTIFACT_KEY: "verification-receipt.json",
    "verification_artifact_manifest": "verification-artifacts.json",
    "verification_plan": "verification-plan.json",
    "agent_handoff": "agent-handoff.json",
    "verifier": "verifier.json",
    "verify_run": "verify-run.json",
    "human_authorization": "human-authorization.json",
    "report": "report.json",
    "report_markdown": "report.md",
    "report_sarif": "report.sarif",
    "packet": "packet.json",
    "pr_comment": "pr-comment.md",
}

# Artifacts a scan produces, and only in the formats that scan was configured to
# emit.  A run must never bind one of these unless it wrote it: `scan --format
# markdown` after a verify leaves the verifier's `report.json` untouched, and
# binding it would advertise two generations as one current set.
SCAN_CONTROL_ARTIFACT_KEYS: frozenset[str] = frozenset(
    {"report", "report_markdown", "report_sarif", "packet"}
)
# The complement: what a verifier route writes.  `preview` runs no scan at all,
# so it binds only these.
VERIFIER_ROUTE_CONTROL_ARTIFACT_KEYS: frozenset[str] = frozenset(
    key for key in CURRENT_CONTROL_ARTIFACT_FILENAMES if key not in SCAN_CONTROL_ARTIFACT_KEYS
)
# Scan output format name (``_OutputPlan.generated_paths`` key) -> artifact key.
SCAN_FORMAT_ARTIFACT_KEYS: dict[str, str] = {
    "markdown": "report_markdown",
    "json": "report",
    "sarif": "report_sarif",
    "packet_json": "packet",
}

_LIFECYCLE_OWNER: ContextVar[str | None] = ContextVar(
    "agents_shipgate_current_control_owner", default=None
)


class CurrentControlPublishError(AgentsShipgateError):
    """The pointer could not be moved to its next lifecycle state."""

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        super().__init__(f"Could not publish the current control pointer {path}: {detail}")


class CurrentControlUnavailable(AgentsShipgateError):
    """No validated control identity is current in this directory.

    ``reason`` is a stable machine code.  Every value means the same thing
    operationally: the caller holds no current authority and must not act on a
    control state it cached earlier.
    """

    def __init__(self, reason: str, detail: str, *, path: Path | None = None) -> None:
        self.reason = reason
        self.path = path
        super().__init__(detail)


@contextmanager
def current_control_lifecycle(owner: str) -> Iterator[None]:
    """Claim the pointer for one command for the duration of the block.

    ``verify`` runs a full scan into the same directory to produce its head
    report.  That supporting scan must not publish a scan-scoped pointer over
    the verification the caller actually asked for, so publication from nested
    scans is suppressed while an owner is active.
    """

    token = _LIFECYCLE_OWNER.set(owner)
    try:
        yield
    finally:
        _LIFECYCLE_OWNER.reset(token)


def current_control_lifecycle_owner() -> str | None:
    return _LIFECYCLE_OWNER.get()


_P = ParamSpec("_P")
_R = TypeVar("_R")


def owns_current_control(operation: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Mark a command entry point as the owner of the pointer it publishes."""

    def decorate(function: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(function)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with current_control_lifecycle(operation):
                return function(*args, **kwargs)

        return wrapper

    return decorate


def current_control_path(out_dir: Path) -> Path:
    return out_dir / CURRENT_CONTROL_ARTIFACT_NAME


def begin_current_control(
    out_dir: Path,
    *,
    operation: CurrentControlOperation,
    reason: str,
    repository: str | None = None,
) -> CurrentControlPointer:
    """Atomically replace whatever was current with an in-progress marker."""

    pointer = _finalize(
        operation=operation,
        lifecycle_state="in_progress",
        request_id=None,
        decision_id=None,
        workspace_identity=CurrentControlWorkspaceIdentity(repository=repository),
        control=UnavailableCurrentControl(state="unavailable", reason=reason),
        artifacts={},
        supersedes=_previous_control_id(out_dir),
    )
    _publish(out_dir, pointer)
    return pointer


def publish_current_control(
    out_dir: Path,
    *,
    operation: CurrentControlOperation,
    control: CurrentControlProjection,
    request_id: str | None = None,
    decision_id: str | None = None,
    workspace_identity: CurrentControlWorkspaceIdentity | None = None,
    artifact_keys: Collection[str] | None = None,
    artifact_paths: Mapping[str, str] | None = None,
) -> CurrentControlPointer:
    """Bind the artifacts now on disk and publish the terminal pointer.

    ``artifact_keys`` restricts the binding to what this operation can actually
    have produced.  Leaving it open would let a command bind a file an earlier,
    different run wrote, which is the mixed artifact set the pointer exists to
    make detectable.

    ``artifact_paths`` overrides where a key lives, for producers that do not
    write to the canonical filename — ``verification assemble --out`` accepts any
    name beneath its artifacts root, and the pointer has to bind the receipt
    that run actually emitted rather than whatever occupies the canonical path.
    """

    artifacts = bind_current_control_artifacts(
        out_dir, artifact_keys=artifact_keys, artifact_paths=artifact_paths
    )
    if control.state == "complete":
        # Defence in depth: the schema rejects a receipt-less completion too,
        # but downgrading here keeps a caller with an inconsistent view from
        # losing the run. The identity check matters independently — a producer
        # may write its receipt somewhere other than the canonical path (the
        # assembler's `--out` accepts any name under its artifacts root), which
        # leaves an older canonical receipt to be bound beside a newer decision.
        refusal = _receipt_binding_refusal(
            out_dir,
            artifacts,
            request_id=request_id,
            decision_id=decision_id,
        )
        if refusal is not None:
            control = HumanReviewRequiredCurrentControl(
                state="human_review_required", reason=refusal
            )
    pointer = _finalize(
        operation=operation,
        lifecycle_state="terminal",
        request_id=request_id,
        decision_id=decision_id,
        workspace_identity=workspace_identity or CurrentControlWorkspaceIdentity(),
        control=control,
        artifacts=artifacts,
        supersedes=_previous_control_id(out_dir),
    )
    _publish(out_dir, pointer)
    return pointer


def _receipt_binding_refusal(
    out_dir: Path,
    artifacts: dict[str, CurrentControlArtifactRef],
    *,
    request_id: str | None,
    decision_id: str | None,
) -> str | None:
    """Return why completion must be refused, or ``None`` when it may stand."""

    ref = artifacts.get(RECEIPT_ARTIFACT_KEY)
    if ref is None:
        return (
            "The run reported completion but published no terminal receipt, "
            "so completion authority cannot be established."
        )
    try:
        receipt = _load_receipt(out_dir, ref)
    except ValueError as exc:
        return f"The bound terminal receipt could not be read: {exc}"
    if receipt.request_id != request_id or receipt.decision_id != decision_id:
        return (
            "The bound terminal receipt closes a different request than the one "
            "this run decided, so completion authority cannot be established."
        )
    return None


def _load_receipt(out_dir: Path, ref: CurrentControlArtifactRef) -> VerificationReceipt:
    data = read_regular_file_beneath(
        out_dir,
        ref.path,
        max_size=MAX_BOUND_ARTIFACT_BYTES,
        label="current control receipt",
    )
    try:
        return VerificationReceipt.model_validate_json(data)
    except Exception as exc:  # pydantic ValidationError and friends
        raise ValueError(str(exc)) from exc


def bind_current_control_artifacts(
    out_dir: Path,
    *,
    artifact_keys: Collection[str] | None = None,
    artifact_paths: Mapping[str, str] | None = None,
) -> dict[str, CurrentControlArtifactRef]:
    """Hash every selected artifact that exists as a regular file in ``out_dir``."""

    filenames = dict(CURRENT_CONTROL_ARTIFACT_FILENAMES)
    for key, filename in (artifact_paths or {}).items():
        # A producer may relocate an artifact, never invent a new kind of one,
        # and never point outside the directory the pointer describes.
        if key not in filenames:
            raise CurrentControlPublishError(
                out_dir, f"unknown current control artifact key: {key!r}"
            )
        try:
            filenames[key] = validate_portable_path(filename)
        except ValueError as exc:
            raise CurrentControlPublishError(out_dir / filename, str(exc)) from exc
    selected = (
        filenames
        if artifact_keys is None
        else {key: filename for key, filename in filenames.items() if key in artifact_keys}
    )
    refs: dict[str, CurrentControlArtifactRef] = {}
    for key, filename in selected.items():
        path = out_dir / filename
        if path.is_symlink() or not path.is_file():
            continue
        try:
            data = read_regular_file_beneath(
                out_dir,
                filename,
                max_size=MAX_BOUND_ARTIFACT_BYTES,
                label="current control artifact",
            )
        except ValueError as exc:
            raise CurrentControlPublishError(out_dir / filename, str(exc)) from exc
        refs[key] = CurrentControlArtifactRef(
            path=filename,
            sha256=_sha256_bytes(data),
            size_bytes=len(data),
        )
    return refs


def project_agent_control(
    control: AgentControl,
    *,
    operation: CurrentControlOperation,
    receipt_bound: bool,
) -> CurrentControlProjection:
    """Narrow an authoritative :class:`AgentControl` onto the pointer.

    This is a projection, never a decision.  The one place it deviates is
    conservative: completion authority is refused unless this run is a verify
    that also published a terminal receipt, because ``complete`` is the single
    state a stale reader could act on destructively.
    """

    if control.state == "complete":
        if operation != "verify" or not receipt_bound:
            return HumanReviewRequiredCurrentControl(
                state="human_review_required",
                reason=(
                    "A completion-authorizing control was projected without a "
                    "verify receipt for this exact request, so it is refused."
                ),
            )
        return CompleteCurrentControl(state="complete", reason=control.reason)
    if control.state == "human_review_required":
        return HumanReviewRequiredCurrentControl(
            state="human_review_required", reason=control.reason
        )
    return AgentActionRequiredCurrentControl(state="agent_action_required", reason=control.reason)


def workspace_identity_from_plan(plan: VerificationPlan) -> CurrentControlWorkspaceIdentity:
    """Derive the pointer's workspace identity from a verification plan.

    The plan's subject is the same one the terminal receipt closes over, so a
    pointer built from it names exactly the workspace the decision was made
    against — not the workspace the reader happens to be sitting in.
    """

    git = plan.subject.git
    return CurrentControlWorkspaceIdentity(
        repository=git.repository_id,
        head_ref=git.head_ref,
        head_commit_sha=git.head_commit_sha,
        head_tree_sha=git.head_tree_sha,
        base_ref=git.base_ref,
        base_commit_sha=git.base_commit_sha,
        merge_base_sha=git.merge_base_sha,
        worktree_overlay_sha256=git.worktree_overlay_sha256,
        policy_snapshot_sha256=content_id(
            {
                "config": plan.inputs.config.sha256,
                "policy_packs": [blob.sha256 for blob in plan.inputs.policy_packs],
                "policy_catalog_sha256": plan.engine.policy_catalog_sha256,
            }
        ),
        snapshot_kind=git.snapshot_kind,
    )


@dataclass(frozen=True)
class LiveWorkspace:
    """The repository as it stands right now, for comparison with the pointer.

    Resolved by the caller because the Git helpers live in the CLI layer.
    ``root`` is the repository root, used to recompute a worktree overlay.
    """

    root: Path
    repository: str | None = None
    head_commit_sha: str | None = None
    head_tree_sha: str | None = None
    # The paths that differ from HEAD right now. ``None`` means the caller could
    # not determine them, which is treated as unverified rather than unchanged.
    changed_paths: tuple[str, ...] | None = None
    # Ref resolvers, supplied by the CLI because the Git helpers live there. The
    # pointer names its own base, so it cannot be resolved before the read.
    resolve_commit: Callable[[str], str | None] | None = None
    resolve_merge_base: Callable[[str, str], str | None] | None = None


@dataclass(frozen=True)
class CurrentControlRead:
    """A pointer that was validated against the artifacts it binds."""

    pointer: CurrentControlPointer
    path: Path


def read_current_control(
    out_dir: Path,
    *,
    live: LiveWorkspace | None = None,
    attempts: int = 3,
) -> CurrentControlRead:
    """Read the current control identity, or refuse.

    Implements the generation-safe protocol: read and validate the pointer,
    validate every artifact it binds, then re-read the pointer and continue
    only when ``current_control_id`` is unchanged.  A run that republishes
    while this read is in flight makes the read fail rather than return a
    pointer describing one generation and artifacts from another.

    Byte consistency is not generation consistency.  A pointer whose artifacts
    all still hash correctly can still describe a workspace that has since
    moved — one commit is enough — so ``live`` is compared against the bound
    ``workspace_identity`` and any drift refuses the read.  Completion
    authority is never returned without that comparison: passing ``live=None``
    means "not verified", which downgrades to a refusal rather than a pass.
    """

    path = current_control_path(out_dir)
    moved = CurrentControlUnavailable(
        "generation_changed",
        (
            "The current control identity changed while it was being read; "
            "no coherent generation could be observed."
        ),
        path=path,
    )
    last: CurrentControlUnavailable | None = None
    for _ in range(max(1, attempts)):
        pointer = _load_pointer(out_dir, path)
        try:
            _validate_bound_artifacts(out_dir, pointer)
        except CurrentControlUnavailable as mismatch:
            # A run that republished mid-read moves the pointer too. Retry that
            # case; a mismatch under a pointer that did not move is a real
            # inconsistency and must surface.
            if _current_control_id(path) == pointer.current_control_id:
                raise
            last = mismatch
            continue
        _validate_control_currency(out_dir, pointer, live)
        confirmation = _load_pointer(out_dir, path)
        if confirmation.current_control_id == pointer.current_control_id:
            return CurrentControlRead(pointer=pointer, path=path)
        last = moved
    raise last or CurrentControlUnavailable(
        "generation_changed",
        "The current control identity could not be read coherently.",
        path=path,
    )


def _load_pointer(out_dir: Path, path: Path) -> CurrentControlPointer:
    if path.is_symlink():
        raise CurrentControlUnavailable(
            "unsafe_pointer",
            f"{CURRENT_CONTROL_ARTIFACT_NAME} must be a regular file, not a symlink.",
            path=path,
        )
    if not path.is_file():
        raise CurrentControlUnavailable(
            "missing",
            (
                f"No {CURRENT_CONTROL_ARTIFACT_NAME} is present in {out_dir}; run a "
                "Shipgate command that publishes one before acting on any cached "
                "control state."
            ),
            path=path,
        )
    try:
        data = read_regular_file_beneath(
            out_dir,
            CURRENT_CONTROL_ARTIFACT_NAME,
            max_size=MAX_CURRENT_CONTROL_BYTES,
            label="current control pointer",
        )
    except ValueError as exc:
        raise CurrentControlUnavailable("unreadable", str(exc), path=path) from exc
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CurrentControlUnavailable(
            "invalid_schema",
            f"{CURRENT_CONTROL_ARTIFACT_NAME} is not one UTF-8 JSON object.",
            path=path,
        ) from exc
    if not isinstance(payload, dict):
        raise CurrentControlUnavailable(
            "invalid_schema",
            f"{CURRENT_CONTROL_ARTIFACT_NAME} must contain one JSON object.",
            path=path,
        )
    try:
        return CurrentControlPointer.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError and friends
        raise CurrentControlUnavailable(
            "invalid_schema",
            f"{CURRENT_CONTROL_ARTIFACT_NAME} is not a valid control pointer: {exc}",
            path=path,
        ) from exc


def _validate_control_currency(
    out_dir: Path,
    pointer: CurrentControlPointer,
    live: LiveWorkspace | None,
) -> None:
    """Refuse a pointer whose evidence no longer describes this workspace."""

    grants_authority = pointer.control.state == "complete"
    if grants_authority:
        refusal = _receipt_binding_refusal(
            out_dir,
            pointer.artifacts,
            request_id=pointer.request_id,
            decision_id=pointer.decision_id,
        )
        if refusal is not None:
            raise CurrentControlUnavailable("receipt_mismatch", refusal, path=out_dir)

    identity = pointer.workspace_identity
    if live is None:
        if grants_authority:
            raise CurrentControlUnavailable(
                "workspace_unverified",
                (
                    "This pointer authorizes completion, but the live workspace "
                    "was not supplied, so it could not be confirmed that the "
                    "decision still describes it."
                ),
                path=out_dir,
            )
        return

    for field, observed in (
        ("repository", live.repository),
        ("head_commit_sha", live.head_commit_sha),
        ("head_tree_sha", live.head_tree_sha),
    ):
        expected = getattr(identity, field)
        if expected is not None and observed != expected:
            raise CurrentControlUnavailable(
                "workspace_changed",
                (
                    f"The workspace moved since this decision was made: "
                    f"{field} is {observed!r}, but the pointer was published "
                    f"against {expected!r}. Re-run verification."
                ),
                path=out_dir,
            )
    if grants_authority and (
        identity.head_commit_sha is None or identity.head_tree_sha is None
    ):
        raise CurrentControlUnavailable(
            "workspace_unverifiable",
            (
                "This pointer authorizes completion but binds no HEAD identity, "
                "so it cannot be confirmed against the live workspace."
            ),
            path=out_dir,
        )
    _validate_base_currency(out_dir, identity, live, required=grants_authority)
    if identity.snapshot_kind == "worktree_overlay":
        _validate_worktree_currency(out_dir, pointer, live, required=grants_authority)
    elif identity.snapshot_kind == "committed_tree":
        _require_clean_worktree(out_dir, live, required=grants_authority)


def _validate_worktree_currency(
    out_dir: Path,
    pointer: CurrentControlPointer,
    live: LiveWorkspace,
    *,
    required: bool,
) -> None:
    """Confirm the working tree is still the one a worktree decision saw.

    Two things have to hold, and neither implies the other:

    * every path the decision covered still has the content it had — recomputed
      with the same normalization the plan used, because editing one of those
      files leaves HEAD and its tree byte-identical; and
    * nothing *outside* that set differs from HEAD now.  Everything outside it
      was identical to HEAD when the decision was made — that is why it was not
      in the change set — so a live path the plan never recorded is new
      evidence the decision never saw.

    The second is a subset test, not equality.  ``plan.inputs.changed_paths`` is
    not the uncommitted set: a local run with a base carries the union of
    ``base...HEAD`` and the worktree, so requiring equality would refuse a clean
    workspace the instant the run that produced it finished.
    """

    ref = pointer.artifacts.get("verification_plan")
    if ref is None:
        if required:
            raise CurrentControlUnavailable(
                "workspace_unverifiable",
                (
                    "This pointer authorizes completion of a worktree "
                    "verification but binds no plan, so the overlay it decided "
                    "on cannot be recomputed."
                ),
                path=out_dir,
            )
        return
    try:
        data = read_regular_file_beneath(
            out_dir,
            ref.path,
            max_size=MAX_BOUND_ARTIFACT_BYTES,
            label="current control plan",
        )
        plan = VerificationPlan.model_validate_json(data)
        decided_paths = list(plan.inputs.changed_paths)
        rows = worktree_overlay(live.root, decided_paths)
    except (ValueError, OSError) as exc:
        raise CurrentControlUnavailable(
            "workspace_unverifiable",
            f"The worktree overlay this decision committed to could not be recomputed: {exc}",
            path=out_dir,
        ) from exc
    observed = content_id(rows) if rows else None
    if observed != pointer.workspace_identity.worktree_overlay_sha256:
        raise CurrentControlUnavailable(
            "workspace_changed",
            (
                "The working tree changed since this decision was made: the "
                "recomputed overlay does not match the one the pointer was "
                "published against. Re-run verification."
            ),
            path=out_dir,
        )
    if live.changed_paths is None:
        if required:
            raise CurrentControlUnavailable(
                "workspace_unverifiable",
                (
                    "This pointer authorizes completion of a worktree "
                    "verification, but the current set of uncommitted changes "
                    "could not be determined."
                ),
                path=out_dir,
            )
        return
    unseen = sorted(set(live.changed_paths) - set(decided_paths))
    if unseen:
        raise CurrentControlUnavailable(
            "workspace_changed",
            _unseen_change_detail(unseen, "this decision never saw"),
            path=out_dir,
        )


def _require_clean_worktree(out_dir: Path, live: LiveWorkspace, *, required: bool) -> None:
    """A committed-tree decision says nothing about uncommitted work.

    ``verify --head <ref>`` evaluates an archived commit, so its evidence stops
    at HEAD.  Uncommitted changes appearing afterwards invalidate the pointer in
    *both* directions, so this is not scoped to completion: a stale
    ``human_review_required`` kept enforcing a pre-change stop against a
    workspace a human had since edited, and the archived run has no route that
    can clear it — the clearing route is a local worktree verification, which is
    what the refusal has to say.

    ``required`` narrows only the *undeterminable* case: a change set we could
    not read is unknown rather than known-drifted, so it blocks authority but
    does not deny a caller its route.
    """

    if live.changed_paths is None:
        if required:
            raise CurrentControlUnavailable(
                "workspace_unverifiable",
                (
                    "This pointer authorizes completion of a committed-tree "
                    "verification, but the current set of uncommitted changes "
                    "could not be determined."
                ),
                path=out_dir,
            )
        return
    if live.changed_paths:
        raise CurrentControlUnavailable(
            "workspace_changed",
            _unseen_change_detail(
                sorted(live.changed_paths),
                "this committed-tree decision could not have covered",
            )
            + " Re-run verification over the working tree (omit --head) to"
            " decide on the workspace as it stands.",
            path=out_dir,
        )


def _validate_base_currency(
    out_dir: Path,
    identity: CurrentControlWorkspaceIdentity,
    live: LiveWorkspace,
    *,
    required: bool,
) -> None:
    """Confirm the other end of the range still resolves where it did.

    A decision about ``base...HEAD`` is a decision about that range.  Advancing
    the base — a merge, or a fetch moving ``origin/main`` — can empty the range
    entirely without touching HEAD or the working tree, which leaves every
    HEAD-based check satisfied while the evidence underneath has gone.
    """

    if identity.base_ref is None or identity.base_commit_sha is None:
        return
    if live.resolve_commit is None:
        if required:
            raise CurrentControlUnavailable(
                "workspace_unverifiable",
                (
                    "This pointer authorizes completion of a verification "
                    f"against {identity.base_ref!r}, but that ref could not be "
                    "resolved to compare against."
                ),
                path=out_dir,
            )
        return
    observed = live.resolve_commit(identity.base_ref)
    if observed != identity.base_commit_sha:
        raise CurrentControlUnavailable(
            "workspace_changed",
            (
                f"The base this decision was made against moved: {identity.base_ref!r} "
                f"is now {observed!r}, but the pointer was published against "
                f"{identity.base_commit_sha!r}. Re-run verification."
            ),
            path=out_dir,
        )
    if identity.merge_base_sha is not None and live.resolve_merge_base is not None:
        observed_merge_base = live.resolve_merge_base(
            identity.base_ref, identity.head_ref or "HEAD"
        )
        if observed_merge_base != identity.merge_base_sha:
            raise CurrentControlUnavailable(
                "workspace_changed",
                (
                    "The merge base this decision was made against moved: it is "
                    f"now {observed_merge_base!r}, but the pointer was published "
                    f"against {identity.merge_base_sha!r}. Re-run verification."
                ),
                path=out_dir,
            )


def _unseen_change_detail(paths: list[str], clause: str) -> str:
    shown = ", ".join(paths[:3])
    if len(paths) > 3:
        shown += f", and {len(paths) - 3} more"
    return (
        f"The working tree carries {len(paths)} uncommitted change(s) {clause} "
        f"({shown}). Re-run verification."
    )


def _validate_bound_artifacts(out_dir: Path, pointer: CurrentControlPointer) -> None:
    for name, ref in sorted(pointer.artifacts.items()):
        try:
            data = read_regular_file_beneath(
                out_dir,
                ref.path,
                max_size=MAX_BOUND_ARTIFACT_BYTES,
                label="current control artifact",
            )
        except ValueError as exc:
            raise CurrentControlUnavailable(
                "artifact_unreadable",
                f"Bound artifact {name!r} could not be read safely: {exc}",
                path=out_dir / ref.path,
            ) from exc
        if len(data) != ref.size_bytes or _sha256_bytes(data) != ref.sha256:
            raise CurrentControlUnavailable(
                "artifact_mismatch",
                (
                    f"Bound artifact {name!r} does not match the current control "
                    "pointer; the artifact set is mixed or was edited after "
                    "publication."
                ),
                path=out_dir / ref.path,
            )


def _finalize(
    *,
    operation: CurrentControlOperation,
    lifecycle_state: str,
    request_id: str | None,
    decision_id: str | None,
    workspace_identity: CurrentControlWorkspaceIdentity,
    control: CurrentControlProjection,
    artifacts: dict[str, CurrentControlArtifactRef],
    supersedes: str | None,
) -> CurrentControlPointer:
    draft = CurrentControlPointer.model_construct(
        current_control_id="sha256:" + "0" * 64,
        operation=operation,
        lifecycle_state=lifecycle_state,  # type: ignore[arg-type]
        request_id=request_id,
        decision_id=decision_id,
        workspace_identity=workspace_identity,
        control=control,
        artifacts=artifacts,
        supersedes=supersedes,
    )
    identity = content_id(current_control_identity_payload(draft))
    # Re-validate through the full model so every structural invariant runs
    # against the exact payload that will be written.
    return CurrentControlPointer.model_validate(
        {**draft.model_dump(mode="json"), "current_control_id": identity}
    )


def _previous_control_id(out_dir: Path) -> str | None:
    """Best-effort read of the identity being replaced, for audit only."""

    return _current_control_id(current_control_path(out_dir))


def _current_control_id(path: Path) -> str | None:
    """Cheap identity probe that never raises; ``None`` means "not readable"."""

    if path.is_symlink() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_bytes()[:MAX_CURRENT_CONTROL_BYTES].decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    value = payload.get("current_control_id") if isinstance(payload, dict) else None
    if isinstance(value, str) and value.startswith("sha256:") and len(value) == 71:
        return value
    return None


def _publish(out_dir: Path, pointer: CurrentControlPointer) -> None:
    path = current_control_path(out_dir)
    payload = (json.dumps(pointer.model_dump(mode="json"), indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if path.is_symlink():
        raise CurrentControlPublishError(
            path, "the existing pointer is a symlink and will not be followed"
        )
    temporary: Path | None = None
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(
            dir=out_dir, prefix=f".{CURRENT_CONTROL_ARTIFACT_NAME}.", suffix=".tmp"
        )
        temporary = Path(name)
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(out_dir)
    except OSError as exc:
        raise CurrentControlPublishError(path, str(exc)) from exc
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()


def _fsync_directory(directory: Path) -> None:
    # Durability of the rename itself.  Not every platform supports opening a
    # directory for fsync; where it does not, the rename is still atomic.
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            os.close(descriptor)


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


__all__ = [
    "CURRENT_CONTROL_ARTIFACT_FILENAMES",
    "MAX_BOUND_ARTIFACT_BYTES",
    "SCAN_CONTROL_ARTIFACT_KEYS",
    "SCAN_FORMAT_ARTIFACT_KEYS",
    "VERIFIER_ROUTE_CONTROL_ARTIFACT_KEYS",
    "MAX_CURRENT_CONTROL_BYTES",
    "CurrentControlPublishError",
    "CurrentControlRead",
    "LiveWorkspace",
    "CurrentControlUnavailable",
    "begin_current_control",
    "bind_current_control_artifacts",
    "current_control_lifecycle",
    "current_control_lifecycle_owner",
    "current_control_path",
    "owns_current_control",
    "project_agent_control",
    "publish_current_control",
    "read_current_control",
    "workspace_identity_from_plan",
]
