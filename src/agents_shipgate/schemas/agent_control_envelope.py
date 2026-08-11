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
read-order problem it exists to remove; the artifacts stay where they are and
are reachable from ``artifacts`` by path and hash.

``artifacts`` is exactly what ``current-control.json`` binds — deliberately not
"everything the run wrote". The pointer's binding is the set whose hashes were
validated, so anything outside it would be a path this envelope cannot vouch
for. ``check`` publishes no pointer and therefore binds nothing; its authority
is bound by ``input_id`` instead.
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
# publishes no pointer; ``verify``/``preview``/``scan`` do both; the three setup
# operations run before a release decision can exist at all (see #323).
AgentControlOperation = Literal[
    "verify", "preview", "scan", "check", "detect", "init", "doctor"
]

# The operations whose control state is derived from *setup facts* rather than
# from a gate verdict. Named once, because three separate rules below depend on
# the same membership and a second spelling of this set is how they drift apart.
SETUP_OPERATIONS: tuple[str, ...] = ("detect", "init", "doctor")

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

# Which engine produced ``decision``. ``detect``/``init``/``doctor`` run before
# any release decision exists, so they report ``setup``; naming the source keeps
# ``decision`` from meaning "the gate said" in one command and "setup said" in
# another (see #323). The pairing is enforced in both directions by
# ``_SETUP_PROVENANCE_RULE``: a setup source can only come from a setup
# operation, and a setup operation can report no other source.
AgentControlDecisionSource = Literal["release_decision", "agent_boundary", "setup", "none"]

# The closed vocabulary ``decision`` uses when ``decision_source`` is ``setup``,
# exactly as ``release_decision.decision`` is the vocabulary under
# ``release_decision``. It describes how far *configuration* has got, and
# deliberately says nothing about a release: no value here ever authorizes
# merge, because the ``complete`` variant cannot carry a setup operation at all.
#
#   ``setup_complete``       — configuration is in place; the outstanding step
#                              is the gate, not more setup.
#   ``setup_incomplete``     — at least one setup obligation remains.
#   ``setup_not_applicable`` — the workspace is not a Shipgate target.
SETUP_DECISIONS: tuple[str, ...] = (
    "setup_complete",
    "setup_incomplete",
    "setup_not_applicable",
)

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

# Terminal authority is only reachable two ways, and each leaves a different
# trace. A verify run publishes a pointer and binds artifacts; a boundary check
# writes nothing and binds neither. Without this, both Pydantic and the
# published schema accepted a `complete` envelope with `operation: "scan"`, no
# decision, no identity, no artifacts, and full merge authority — a shape no
# producer can emit and no reader should ever honour.
_COMPLETE_PROVENANCE_RULE = [
    {
        "if": {"properties": {"operation": {"const": "check"}}, "required": ["operation"]},
        "then": {
            "properties": {
                "source": {"const": "run"},
                "decision_source": {"const": "agent_boundary"},
                "current_control_id": {"type": "null"},
                "artifacts": {"maxProperties": 0},
            }
        },
        "else": {
            "properties": {
                "decision_source": {"const": "release_decision"},
                "current_control_id": {"type": "string"},
                "artifacts": {"minProperties": 1},
            },
            "required": ["current_control_id"],
        },
    }
]

# Setup-derived and release-decision-derived control states must not be
# confusable, in either direction — the whole point of rolling one vocabulary
# across six commands is that `control_state` cannot mean "the gate says" on one
# command and "setup says" on another.
#
# Published as if/then rules rather than left to the ``operation`` field alone,
# because a reader that switches on `decision_source` (which is what it is for)
# would otherwise have to know the operation table by heart to tell the two
# apart. The second half also fixes the provenance: setup evaluates no diff,
# publishes no pointer, and binds no artifact, so an envelope claiming both a
# setup operation and a bound artifact set is describing something no producer
# can be.
_SETUP_PROVENANCE_RULE = [
    {
        "if": {
            "properties": {"decision_source": {"const": "setup"}},
            "required": ["decision_source"],
        },
        "then": {"properties": {"operation": {"enum": list(SETUP_OPERATIONS)}}},
    },
    {
        "if": {
            "properties": {"operation": {"enum": list(SETUP_OPERATIONS)}},
            "required": ["operation"],
        },
        "then": {
            "properties": {
                "decision_source": {"const": "setup"},
                "decision": {"enum": list(SETUP_DECISIONS)},
                "source": {"const": "run"},
                "current_control_id": {"type": "null"},
                "artifacts": {"maxProperties": 0},
            }
        },
    },
]

# A `verify` route carries an independent verification obligation. The
# authoritative union rejects a verify action beside `verify_required: false`;
# without this the envelope and its schema did not.
_VERIFY_OBLIGATION_RULE = [
    {
        "if": {
            "properties": {"next_action": {"properties": {"kind": {"const": "verify"}}}},
            "required": ["next_action"],
        },
        "then": {"properties": {"verify_required": {"const": True}}},
    }
]

_ENVELOPE_FIELDS = [
    "schema_version",
    "contract_version",
    "operation",
    "source",
    "execution",
    "exit_code",
    "input_id",
    "decision",
    "decision_source",
    "control_state",
    "permissions",
    "verify_required",
    "next_actor",
    "next_action",
    "human_review",
    "pending_review",
    "reason",
    "current_control_id",
    "artifacts",
]

# `strip_whitespace` has no JSON Schema representation, so a schema-only
# consumer accepted `input_id=" "` on a merge-authorizing envelope that Pydantic
# rejected. A pattern requiring one non-whitespace character *is* published, so
# both layers agree on what counts as present.
_NON_BLANK = r"\S"

BoundedProse = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, pattern=_NON_BLANK),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, pattern=_NON_BLANK),
]


class AgentControlPendingReview(BaseModel):
    """One review obligation the agent carries while it keeps working.

    A graded ``require_review`` boundary row routes the agent onward instead of
    ending the turn, but the obligation is not cleared. The full row lives on
    the boundary result; this is the compact restatement, because a projection
    that keeps the route and drops the obligation tells the agent it is finished
    with something it still owes a human.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    risk_level: str = Field(min_length=1)
    path: str | None = None
    reviewers: list[str] = Field(default_factory=list)


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
        json_schema_extra={
            "required": _ENVELOPE_FIELDS,
            "allOf": [*_DECISION_PAIRING_RULE, *_SETUP_PROVENANCE_RULE],
        },
    )

    schema_version: Literal["shipgate.agent_control/v1"] = AGENT_CONTROL_ENVELOPE_SCHEMA_VERSION
    contract_version: str = Field(min_length=1)
    operation: AgentControlOperation
    source: AgentControlSource

    # --- did the tool run, and on what? ------------------------------------
    execution: AgentControlExecution
    # ``None`` when the producing run did not record one. Never inferred.
    exit_code: int | None
    # The identity of the input this control was assessed against: the boundary
    # ``audit_id``, or the verifier ``request_id``. Without it the compact form
    # is authority detached from its subject — two unrelated diffs produced
    # byte-identical `complete` envelopes granting merge, because everything
    # that distinguished them (`audit_id`, `subject`, `changed_files`) lives
    # only on the full result. The ``complete`` variant requires it.
    input_id: NonEmptyText | None

    # --- what did the gate decide? ----------------------------------------
    decision: BoundedProse | None
    decision_source: AgentControlDecisionSource

    # --- where is the evidence? -------------------------------------------
    # Obligations that survive a non-terminal route. Empty on ``complete``,
    # which cannot coexist with an outstanding review.
    pending_review: list[AgentControlPendingReview] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def _setup_provenance_matches_the_producer(self) -> _AgentControlEnvelopeBase:
        """Mirror ``_SETUP_PROVENANCE_RULE`` so both layers agree.

        Same reasoning as the completion rule below: a payload one layer accepts
        and the other rejects is a contract that means two different things
        depending on who reads it. The direction that matters here is a
        release-decision-derived state wearing a setup operation, or the
        reverse — either makes ``control_state`` ambiguous about what decided
        it, which is the confusion this envelope was rolled out to remove.
        """

        is_setup_operation = self.operation in SETUP_OPERATIONS
        if (self.decision_source == "setup") != is_setup_operation:
            raise ValueError(
                "decision_source='setup' and a setup operation "
                f"({', '.join(SETUP_OPERATIONS)}) imply each other"
            )
        if not is_setup_operation:
            return self
        if self.decision not in SETUP_DECISIONS:
            raise ValueError(f"a setup decision must be one of {SETUP_DECISIONS}")
        if self.source != "run":
            raise ValueError("setup control is only ever read from the run that produced it")
        if self.current_control_id is not None or self.artifacts:
            raise ValueError("setup publishes no control pointer and binds no artifact")
        return self


class CompleteControlEnvelope(_AgentControlEnvelopeBase):
    """The coding agent may merge and report the task complete.

    Every authority-bearing field is pinned by the tag. ``execution`` narrows to
    the statuses a completed evaluation can have, which is what makes
    "a failed run can never authorize completion" a schema rule rather than a
    validator a JSON Schema consumer never runs.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "required": _ENVELOPE_FIELDS,
            "allOf": [
                *_DECISION_PAIRING_RULE,
                *_SETUP_PROVENANCE_RULE,
                *_COMPLETE_PROVENANCE_RULE,
            ],
        },
    )

    control_state: Literal["complete"]
    execution: CompleteExecution
    # `scan` and `preview` reach no release decision, so neither can complete.
    operation: Literal["verify", "check"]
    # A completion always has a verdict behind it, from one of the two engines
    # that can produce one.
    decision: NonEmptyText
    decision_source: Literal["release_decision", "agent_boundary"]
    # Required, not optional: terminal authority that cannot name the input it
    # was assessed against is authority no reader can check.
    input_id: NonEmptyText
    pending_review: list[AgentControlPendingReview] = Field(default_factory=list, max_length=0)
    permissions: FullAgentPermissions = Field(default_factory=FullAgentPermissions)
    verify_required: Literal[False] = False
    next_actor: Literal["none"] = "none"
    next_action: None = None
    human_review: NoHumanReview = Field(default_factory=NoHumanReview)

    @model_validator(mode="after")
    def _provenance_matches_the_producer(self) -> CompleteControlEnvelope:
        """Mirror ``_COMPLETE_PROVENANCE_RULE`` so both layers agree.

        The published rule is what an external consumer enforces; this is what
        an in-process caller hits. Neither is redundant, because a shape
        accepted by one and rejected by the other is a contract that means two
        different things depending on who reads it.
        """

        if self.operation == "check":
            if self.source != "run" or self.decision_source != "agent_boundary":
                raise ValueError("a completed boundary check is a local run of that engine")
            if self.current_control_id is not None or self.artifacts:
                raise ValueError("a boundary check publishes no pointer and binds no artifact")
        else:
            if self.decision_source != "release_decision":
                raise ValueError("a completed verification is decided by the release engine")
            if self.current_control_id is None or not self.artifacts:
                raise ValueError(
                    "a completed verification must name the control identity and the "
                    "artifacts it bound"
                )
        return self


class AgentActionControlEnvelope(_AgentControlEnvelopeBase):
    """One exact coding-agent-owned step remains.

    ``permissions`` admits two vectors because the route decides: a step that
    runs before any diff was read authorizes nothing beyond itself, while a step
    taken on an evaluated change keeps publication authority. Both deny merge.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "required": _ENVELOPE_FIELDS,
            "allOf": [
                *_DECISION_PAIRING_RULE,
                *_SETUP_PROVENANCE_RULE,
                *_VERIFY_OBLIGATION_RULE,
            ],
        },
    )

    control_state: Literal["agent_action_required"]
    permissions: PublishOnlyPermissions | NoAgentPermissions
    verify_required: bool
    next_actor: Literal["coding_agent"] = "coding_agent"
    next_action: CodingAgentAction
    human_review: NoHumanReview = Field(default_factory=NoHumanReview)

    @model_validator(mode="after")
    def _a_verify_route_keeps_its_obligation(self) -> AgentActionControlEnvelope:
        """Mirror ``_VERIFY_OBLIGATION_RULE``; the authoritative union has it too."""

        if self.next_action.kind == "verify" and not self.verify_required:
            raise ValueError("a verify action must preserve verify_required=true")
        return self


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
    "SETUP_DECISIONS",
    "SETUP_OPERATIONS",
    "AgentActionControlEnvelope",
    "AgentControlActor",
    "AgentControlArtifactRef",
    "AgentControlDecisionSource",
    "AgentControlEnvelope",
    "AgentControlExecution",
    "AgentControlOperation",
    "AgentControlPendingReview",
    "AgentControlSource",
    "CompleteControlEnvelope",
    "HumanReviewRequiredControlEnvelope",
    "ReviewPublishableControlEnvelope",
    "truncate_prose",
    "validate_agent_control_envelope",
]
