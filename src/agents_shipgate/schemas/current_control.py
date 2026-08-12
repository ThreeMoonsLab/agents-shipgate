"""The one atomic entry point that answers "what is current now?".

``current-control.json`` is a *pointer*, never a second release decision.  It
binds identities and hashes that other authoritative artifacts already
published — the terminal receipt, the agent handoff, the verifier, the report —
so a consumer can decide in one read whether the control state it is holding
still describes this workspace.

Two failure directions motivate it, and both are enforced structurally here
rather than by consumer discipline:

* *Stale denial* — an agent keeps enforcing a cached ``must_stop`` after a
  newer complete run exists.  A published terminal pointer supersedes the
  cached state, and its ``current_control_id`` changes when the run does.
* *Stale authorization* — an agent acts on a cached ``complete`` after the
  workspace moved.  ``complete`` is only representable when the pointer also
  carries the request identity, the decision identity, and a bound terminal
  receipt for exactly that run.

The control projection is deliberately narrower than
:mod:`agents_shipgate.schemas.agent_control`.  It carries no ``next_action``
and no command list, because reproducing a route here would create a second
place where operational authority is decided.  The route stays in the handoff
this pointer references.

Nothing here is assertable by a coding agent.  There is no field in which to
claim human approval — a human authorization can only reach the pointer as a
hash of the signed grant the verifier already accepted — and every field is
covered by ``current_control_id``, so a hand-edited pointer fails to validate
rather than being read as a weaker but still current one.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from agents_shipgate.schemas.agent_control import (
    FullAgentPermissions,
    NoAgentPermissions,
    PublishOnlyPermissions,
)
from agents_shipgate.schemas.verification_identity import (
    CONTENT_ID_PATTERN,
    GIT_OBJECT_PATTERN,
    content_id,
    validate_portable_path,
)

CURRENT_CONTROL_SCHEMA_VERSION = "shipgate.current_control/v1"
CURRENT_CONTROL_SCHEMA_PATH = "docs/current-control-schema.v1.json"
CURRENT_CONTROL_ARTIFACT_NAME = "current-control.json"

# Which command produced this pointer.  Only ``verify`` can reach a state that
# authorizes completion; the others are structurally barred below.
CurrentControlOperation = Literal["verify", "preview", "scan"]
CurrentControlLifecycleState = Literal["in_progress", "terminal"]

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# The artifact key that must be bound before completion is representable.
RECEIPT_ARTIFACT_KEY = "verification_receipt"
# The artifact key carrying the route and execution status a pointer omits by
# design. Named here so producers and readers cannot spell it differently.
VERIFIER_ARTIFACT_KEY = "verifier"
# The artifact key carrying ``release_decision``. A ``scan`` pointer binds this
# and no verifier, so it is the only place that generation's release verdict
# survives — the pointer records no decision, deliberately, so that it cannot
# become a second one.
REPORT_ARTIFACT_KEY = "report"


class CurrentControlArtifactRef(BaseModel):
    """A hash-bound reference to one artifact beside the pointer."""

    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=CONTENT_ID_PATTERN)
    size_bytes: int = Field(ge=0)

    _path_is_portable = field_validator("path")(validate_portable_path)


class CurrentControlWorkspaceIdentity(BaseModel):
    """What the pointer was published against.

    Every field is nullable because the pointer is published by commands that
    resolve different amounts of identity — an in-progress marker knows only
    the repository, a preview outside Git knows nothing.  A consumer treats a
    ``null`` as "not bound", never as "unchanged".
    """

    model_config = ConfigDict(extra="forbid")

    repository: str | None = None
    head_ref: str | None = None
    head_commit_sha: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    head_tree_sha: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    # The other end of the range. A decision about `base...HEAD` is only about
    # that range: advancing the base until the range is empty changes the
    # evidence completely while leaving HEAD and the working tree untouched.
    base_ref: str | None = None
    base_commit_sha: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    merge_base_sha: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    worktree_overlay_sha256: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    policy_snapshot_sha256: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    # Where ``policy_snapshot_sha256`` was taken from, relative to the reports
    # directory. Without it that hash is unfalsifiable: a reader could see that
    # the pointer recorded *some* manifest digest but had no way to recompute it,
    # so a `scan` generation stayed "current" across an edit to the very manifest
    # it scanned. Optional and nullable like every other field here — an older
    # pointer that omits it simply cannot have its policy snapshot confirmed,
    # which is a reason to withhold a verdict rather than to fabricate one.
    policy_snapshot_path: str | None = None
    snapshot_kind: Literal["committed_tree", "worktree_overlay"] | None = None


class UnavailableCurrentControl(BaseModel):
    """A lifecycle run is in flight; no decision in this directory is current."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": ["state", "reason", "completion_allowed", "must_stop", "permissions"]},
    )

    state: Literal["unavailable"]
    reason: NonEmptyText
    completion_allowed: Literal[False] = False
    must_stop: Literal[True] = True
    permissions: NoAgentPermissions = Field(default_factory=NoAgentPermissions)


class CompleteCurrentControl(BaseModel):
    """The referenced run authorizes reporting the task complete."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": ["state", "reason", "completion_allowed", "must_stop", "permissions"]},
    )

    state: Literal["complete"]
    reason: NonEmptyText
    completion_allowed: Literal[True] = True
    must_stop: Literal[False] = False
    permissions: FullAgentPermissions = Field(default_factory=FullAgentPermissions)


class AgentActionRequiredCurrentControl(BaseModel):
    """The referenced run leaves one coding-agent-owned step outstanding."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": ["state", "reason", "completion_allowed", "must_stop", "permissions"]},
    )

    state: Literal["agent_action_required"]
    reason: NonEmptyText
    completion_allowed: Literal[False] = False
    must_stop: Literal[False] = False
    permissions: PublishOnlyPermissions | NoAgentPermissions = Field(
        default_factory=NoAgentPermissions
    )


class ReviewPublishableCurrentControl(BaseModel):
    """A human gates the merge; the agent may still publish for that review.

    The pointer carries ``permissions`` because this is the one read a consumer
    is obliged to make before acting, and ``must_stop`` alone cannot answer the
    question it is asked here: `false` distinguishes this state from a total
    stop but says nothing about which actions the run authorized. Every variant
    carries the vector so that answer never depends on the state tag.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "required": ["state", "reason", "completion_allowed", "must_stop", "permissions"]
        },
    )

    state: Literal["review_publishable"]
    reason: NonEmptyText
    completion_allowed: Literal[False] = False
    must_stop: Literal[False] = False
    permissions: PublishOnlyPermissions


class HumanReviewRequiredCurrentControl(BaseModel):
    """The referenced run stops the coding agent pending human review."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": ["state", "reason", "completion_allowed", "must_stop", "permissions"]},
    )

    state: Literal["human_review_required"]
    reason: NonEmptyText
    completion_allowed: Literal[False] = False
    must_stop: Literal[True] = True
    permissions: NoAgentPermissions = Field(default_factory=NoAgentPermissions)


type CurrentControlProjection = Annotated[
    UnavailableCurrentControl
    | CompleteCurrentControl
    | AgentActionRequiredCurrentControl
    | ReviewPublishableCurrentControl
    | HumanReviewRequiredCurrentControl,
    Field(discriminator="state"),
]


class CurrentControlPointer(BaseModel):
    """``agents-shipgate-reports/current-control.json``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.current_control/v1"] = CURRENT_CONTROL_SCHEMA_VERSION
    current_control_id: str = Field(pattern=CONTENT_ID_PATTERN)
    operation: CurrentControlOperation
    lifecycle_state: CurrentControlLifecycleState
    request_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    decision_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    workspace_identity: CurrentControlWorkspaceIdentity
    control: CurrentControlProjection
    artifacts: dict[str, CurrentControlArtifactRef] = Field(default_factory=dict)
    # The identity this pointer replaced, for audit only.  Deliberately outside
    # the identity hash: "what is current" is a statement about now, not about
    # how the directory got here, and two runs that produce the same control
    # must produce the same ``current_control_id``.
    supersedes: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)

    @model_validator(mode="after")
    def _pointer_is_coherent(self) -> CurrentControlPointer:
        if self.lifecycle_state == "in_progress":
            if self.control.state != "unavailable":
                raise ValueError("an in-progress pointer must deny every cached decision")
            if self.artifacts:
                raise ValueError("an in-progress pointer cannot bind a terminal artifact set")
            if self.request_id is not None or self.decision_id is not None:
                raise ValueError("an in-progress pointer has no settled request or decision")
        else:
            if self.control.state == "unavailable":
                raise ValueError("a terminal pointer must project a settled control state")
            if not self.artifacts and self.operation != "scan":
                # Verify and preview always write at least a verifier artifact,
                # so an empty binding there means the run lost its evidence. A
                # scan configured to emit no report format genuinely produces
                # nothing, and saying so is more honest than binding a file
                # some earlier run left behind.
                raise ValueError("a terminal pointer must bind at least one artifact")
        if self.operation != "verify" and self.control.state == "complete":
            # Only the verifier decides merge authority.  A scan or a preview
            # that could reach ``complete`` would be exactly the stale
            # authorization this pointer exists to prevent.
            raise ValueError("only verify can publish a completion-authorizing control")
        if self.control.state == "complete":
            if self.request_id is None or self.decision_id is None:
                raise ValueError("completion authority requires a bound request and decision")
            if RECEIPT_ARTIFACT_KEY not in self.artifacts:
                raise ValueError("completion authority requires a bound terminal receipt")
        paths = [ref.path for ref in self.artifacts.values()]
        if len(set(paths)) != len(paths):
            raise ValueError("current control artifacts must not bind one path twice")
        expected = content_id(current_control_identity_payload(self))
        if self.current_control_id != expected:
            raise ValueError("current_control_id must hash the complete control pointer")
        return self


def current_control_identity_payload(pointer: CurrentControlPointer) -> dict[str, Any]:
    """Return the payload ``current_control_id`` hashes."""

    return pointer.model_dump(
        mode="json",
        exclude={"current_control_id", "schema_version", "supersedes"},
        exclude_none=False,
    )


__all__ = [
    "CURRENT_CONTROL_ARTIFACT_NAME",
    "CURRENT_CONTROL_SCHEMA_PATH",
    "CURRENT_CONTROL_SCHEMA_VERSION",
    "RECEIPT_ARTIFACT_KEY",
    "REPORT_ARTIFACT_KEY",
    "VERIFIER_ARTIFACT_KEY",
    "AgentActionRequiredCurrentControl",
    "CompleteCurrentControl",
    "CurrentControlArtifactRef",
    "CurrentControlLifecycleState",
    "CurrentControlOperation",
    "CurrentControlPointer",
    "CurrentControlProjection",
    "CurrentControlWorkspaceIdentity",
    "HumanReviewRequiredCurrentControl",
    "UnavailableCurrentControl",
    "current_control_identity_payload",
]
