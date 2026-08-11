"""The compact operational answer a coding agent routes on.

Every field a coding agent needs in order to decide *what it may do next* is
already published somewhere: the state and permission vector in
``control`` (``shipgate.agent_control`` fields on the verifier, the handoff, and
the boundary result), the currency guarantee in ``current-control.json``, the
release verdict in ``report.json``, and the tool's own exit status in the
process. The problem this envelope solves is not a missing fact — it is that
answering one question costs four reads of four artifacts, and that the two
facts most easily confused (`execution: "succeeded"` and "this may merge") sit
thousands of tokens apart in the same document.

``shipgate.agent_control/v1`` is therefore a **projection, never a decision**.
Nothing here is computed: every field is copied from an authoritative producer,
and the structure below only asserts that the copies cannot contradict each
other. In particular ``permissions`` is carried through verbatim from the
``AgentControl`` union that
:func:`agents_shipgate.core.agent_control.derive_agent_control` fixed, so the
envelope cannot widen authority even by accident.

Like that union, this is a **discriminated union on the state tag rather than a
model with independent fields**, and for the same reason: the tag has to fix the
route, the actor, the permission vector, the human-review shape, and the
execution statuses that state can carry, so that a contradictory payload is
rejected by generated JSON Schema validators and not only by Pydantic. A flat
model with cross-field validators would have published a schema that accepts
``execution: "failed"`` beside ``control_state: "complete"``, or a coding-agent
route on a stopping state — exactly the confusions this envelope exists to make
unrepresentable.

Three separations follow from that structure:

* **Execution status is not release readiness.** ``execution`` says whether the
  tool ran. ``decision`` says what the gate decided. ``control_state`` says what
  the agent may do. The ``complete`` variant cannot carry ``failed`` at all,
  while a *succeeded* one carries no implication whatsoever — that is the
  direction deliberately left open, because it is the direction a reader gets
  wrong.
* **Exit code is not merge authority.** ``exit_code`` reports the CI gate signal,
  which is mode-dependent: in advisory mode a ``blocked`` decision still exits
  0. ``permissions.merge`` is the only field that answers "may I merge".
* **Publication is not completion.** ``permissions`` splits the progress actions
  (``edit``/``commit``/``push``/``update_pr``) from the terminal ones
  (``merge``/``report_complete``), exactly as the ``AgentControl`` union does.

The envelope is emitted on stdout and is never written as an artifact. Adding
one more file to a reports directory that already holds a dozen would deepen the
read-order problem it exists to remove; the forensic artifacts stay where they
are and are reachable from ``artifacts`` by path and hash.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from agents_shipgate.schemas.agent_control import (
    CodingAgentAction,
    FullAgentPermissions,
    HumanControlAction,
    HumanReviewAction,
    NoAgentPermissions,
    NoHumanReview,
    PublishOnlyPermissions,
    RequiredHumanReview,
)
from agents_shipgate.schemas.verification_identity import CONTENT_ID_PATTERN

AGENT_CONTROL_ENVELOPE_SCHEMA_VERSION = "shipgate.agent_control/v1"
AGENT_CONTROL_ENVELOPE_SCHEMA_PATH = "docs/agent-control-schema.v1.json"

# Published size *budget* for one rendered envelope, in bytes of canonical JSON.
# Not a maximum, and deliberately not enforced.
#
# Derived, not guessed. The fixed cost is the artifact map — twelve entries of
# path plus a 71-character content id, about 1.6 KiB — plus roughly 0.7 KiB of
# scalars. The variable cost is three capped prose fields and one exact command,
# about 1.4 KiB more. 4 KiB covers that with headroom and is well under the
# ~9 KiB `verifier.json` plus ~6 KiB `agent-handoff.json` an agent reads today
# to answer the same question.
#
# Two inputs can carry it past this number and are allowed to: a long
# `required_reviewers` list and an unusually long exact command. Truncating
# either to hit a size target would drop something a reader must act on, and a
# control surface that raised because a Git error message ran long would deny an
# answer at the moment the caller most needs one. What *is* enforced is the
# prose cap below; what is measured is representative output, pinned in
# `tests/test_agent_control_envelope.py`.
AGENT_CONTROL_ENVELOPE_BUDGET_BYTES = 4096

# Free-text cap applied by the projection before construction, counted in UTF-8
# bytes rather than characters: the budget is a byte budget, and a character
# limit undercounts both multi-byte codepoints and JSON escape expansion.
# Prose explains; it is never parsed, and the unabridged text is always in the
# bound artifacts. Commands, paths, hashes, and reviewer names are never
# truncated — those are executed or matched, not read.
MAX_ENVELOPE_PROSE_BYTES = 400
PROSE_TRUNCATION_MARKER = " […]"

# Which command produced this answer. ``check`` reaches no release decision and
# publishes no pointer; the other three do both.
AgentControlOperation = Literal["verify", "preview", "scan", "check"]

# Whether this envelope came from the run that decided, or from re-reading the
# published pointer at a later refresh boundary. Both are validated against the
# live workspace before they are emitted.
AgentControlSource = Literal["run", "refresh"]

# Tool execution status, mirroring ``VerifierArtifact.execution`` exactly.
AgentControlExecution = Literal["not_run", "succeeded", "skipped", "failed"]

# The subset a completion-authorizing result may carry. ``failed`` is absent by
# construction, so the separation is enforced by the published schema and not
# only by a model validator a JSON Schema consumer never sees.
CompleteExecution = Literal["not_run", "succeeded", "skipped"]

# Which engine produced ``decision``. ``init``/``doctor``-style commands run
# before any decision exists and report ``none``; naming the source keeps
# ``decision`` from meaning "the gate said" in one command and "setup said" in
# another once the vocabulary rolls out (see #323).
AgentControlDecisionSource = Literal["release_decision", "agent_boundary", "setup", "none"]

# Who owns the next step. Fixed by the state tag on every variant.
AgentControlActor = Literal["coding_agent", "human", "none"]

# ``decision`` and ``decision_source`` are the one pairing no state tag can fix,
# because either engine may be the source in any state. Published as an
# if/then rule so a JSON Schema validator enforces it too.
_DECISION_PAIRING_RULE = [
    {
        "if": {"properties": {"decision_source": {"const": "none"}}, "required": ["decision_source"]},
        "then": {"properties": {"decision": {"type": "null"}}},
        "else": {"properties": {"decision": {"type": "string", "minLength": 1}}},
    }
]

_ENVELOPE_FIELDS = [
    "schema_version",
    "contract_version",
    "operation",
    "source",
    "execution",
    "exit_code",
    "decision",
    "decision_source",
    "control_state",
    "permissions",
    "verify_required",
    "next_actor",
    "next_action",
    "human_review",
    "reason",
    "current_control_id",
    "artifacts",
]

BoundedProse = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class AgentControlArtifactRef(BaseModel):
    """A content-addressed pointer to one forensic artifact.

    Path and hash only. The envelope answers what to do; these answer where to
    look if a human or a deeper tool needs the evidence behind it.

    ``path`` is relative to the directory the command was invoked from, not to
    the reports directory and not to the Git root, because the envelope is
    printed to stdout and carries no other directory context. A reader must be
    able to open it exactly as given.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=CONTENT_ID_PATTERN)


class _AgentControlEnvelopeBase(BaseModel):
    """Fields every state carries. The state tag fixes the rest."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": _ENVELOPE_FIELDS, "allOf": _DECISION_PAIRING_RULE},
    )

    schema_version: Literal["shipgate.agent_control/v1"] = AGENT_CONTROL_ENVELOPE_SCHEMA_VERSION
    contract_version: str = Field(min_length=1)
    operation: AgentControlOperation
    source: AgentControlSource

    # --- did the tool run? -------------------------------------------------
    execution: AgentControlExecution
    # ``None`` when the producing run did not record one. Never inferred.
    exit_code: int | None

    # --- what did the gate decide? ----------------------------------------
    decision: BoundedProse | None
    decision_source: AgentControlDecisionSource

    # --- where is the evidence? -------------------------------------------
    reason: BoundedProse
    current_control_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    artifacts: dict[str, AgentControlArtifactRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _decision_and_source_move_together(self) -> _AgentControlEnvelopeBase:
        """The one rule the state tag cannot fix, enforced on both layers.

        Either engine may be the source in any state, so this cannot be pinned
        per variant. ``_DECISION_PAIRING_RULE`` publishes it to JSON Schema
        consumers; this keeps Pydantic from accepting a payload the published
        document rejects, which would put the two layers out of step in the
        direction that matters — a decision present with no engine named.
        """

        if (self.decision is None) != (self.decision_source == "none"):
            raise ValueError(
                "decision and decision_source must both be present or both absent"
            )
        return self


class CompleteControlEnvelope(_AgentControlEnvelopeBase):
    """The coding agent may merge and report the task complete.

    Every authority-bearing field is pinned by the tag. ``execution`` narrows to
    the statuses a completed evaluation can have, which is what makes
    "a failed run can never authorize completion" a schema rule rather than a
    validator a JSON Schema consumer never runs.
    """

    control_state: Literal["complete"]
    execution: CompleteExecution
    permissions: FullAgentPermissions = Field(default_factory=FullAgentPermissions)
    verify_required: Literal[False] = False
    next_actor: Literal["none"] = "none"
    next_action: None = None
    human_review: NoHumanReview = Field(default_factory=NoHumanReview)


class AgentActionControlEnvelope(_AgentControlEnvelopeBase):
    """One exact coding-agent-owned step remains.

    ``permissions`` admits two vectors because the route decides: a step that
    runs before any diff was read authorizes nothing beyond itself, while a step
    taken on an evaluated change keeps publication authority. Both deny merge.
    """

    control_state: Literal["agent_action_required"]
    permissions: PublishOnlyPermissions | NoAgentPermissions
    verify_required: bool
    next_actor: Literal["coding_agent"] = "coding_agent"
    next_action: CodingAgentAction
    human_review: NoHumanReview = Field(default_factory=NoHumanReview)


class ReviewPublishableControlEnvelope(_AgentControlEnvelopeBase):
    """A human gates the merge; publishing the evidence for that review does not.

    ``next_action`` is typed as the review-only action rather than the general
    human action, so a stop route cannot be published under a state that grants
    publication authority — in generated JSON Schema as well as in Pydantic.
    """

    control_state: Literal["review_publishable"]
    permissions: PublishOnlyPermissions
    verify_required: bool
    next_actor: Literal["human"] = "human"
    next_action: HumanReviewAction
    human_review: RequiredHumanReview


class HumanReviewRequiredControlEnvelope(_AgentControlEnvelopeBase):
    """Nothing is authorized: Shipgate has no assessment it can stand behind."""

    control_state: Literal["human_review_required"]
    permissions: NoAgentPermissions = Field(default_factory=NoAgentPermissions)
    verify_required: bool
    next_actor: Literal["human"] = "human"
    next_action: HumanControlAction
    human_review: RequiredHumanReview


type AgentControlEnvelope = Annotated[
    CompleteControlEnvelope
    | AgentActionControlEnvelope
    | ReviewPublishableControlEnvelope
    | HumanReviewRequiredControlEnvelope,
    Field(discriminator="control_state"),
]

AGENT_CONTROL_ENVELOPE_ADAPTER = TypeAdapter(AgentControlEnvelope)


def validate_agent_control_envelope(value: object) -> AgentControlEnvelope:
    """Validate an arbitrary value against the authoritative union."""

    return AGENT_CONTROL_ENVELOPE_ADAPTER.validate_python(value)


def truncate_prose(value: str) -> str:
    """Cap one free-text field deterministically, in UTF-8 bytes.

    Truncation happens on a codepoint boundary, so the result is always valid
    text; the marker makes an abridged field obvious rather than silently short.
    """

    text = value.strip()
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_ENVELOPE_PROSE_BYTES:
        return text
    keep = MAX_ENVELOPE_PROSE_BYTES - len(PROSE_TRUNCATION_MARKER.encode("utf-8"))
    return encoded[:keep].decode("utf-8", errors="ignore").rstrip() + PROSE_TRUNCATION_MARKER


__all__ = [
    "AGENT_CONTROL_ENVELOPE_ADAPTER",
    "AGENT_CONTROL_ENVELOPE_BUDGET_BYTES",
    "AGENT_CONTROL_ENVELOPE_SCHEMA_PATH",
    "AGENT_CONTROL_ENVELOPE_SCHEMA_VERSION",
    "MAX_ENVELOPE_PROSE_BYTES",
    "PROSE_TRUNCATION_MARKER",
    "AgentActionControlEnvelope",
    "AgentControlActor",
    "AgentControlArtifactRef",
    "AgentControlDecisionSource",
    "AgentControlEnvelope",
    "AgentControlExecution",
    "AgentControlOperation",
    "AgentControlSource",
    "CompleteControlEnvelope",
    "HumanReviewRequiredControlEnvelope",
    "ReviewPublishableControlEnvelope",
    "truncate_prose",
    "validate_agent_control_envelope",
]
