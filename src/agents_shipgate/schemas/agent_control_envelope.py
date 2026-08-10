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
and the validators below only assert that the copies cannot contradict each
other. In particular ``permissions`` is carried through verbatim from the
``AgentControl`` union that
:func:`agents_shipgate.core.agent_control.derive_agent_control` fixed, so the
envelope cannot widen authority even by accident.

Three separations are structural rather than documentary:

* **Execution status is not release readiness.** ``execution`` says whether the
  tool ran. ``decision`` says what the gate decided. ``control_state`` says what
  the agent may do. A failed run can never be ``complete``, but a *succeeded*
  one carries no implication at all — that is the direction the enforced
  implication deliberately leaves open, because it is the direction a reader
  gets wrong.
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
    model_validator,
)

from agents_shipgate.schemas.agent_control import (
    AgentControlAction,
    AgentControlState,
    FullAgentPermissions,
    NoAgentPermissions,
    NoHumanReview,
    PublishOnlyPermissions,
    RequiredHumanReview,
)
from agents_shipgate.schemas.verification_identity import CONTENT_ID_PATTERN

AGENT_CONTROL_ENVELOPE_SCHEMA_VERSION = "shipgate.agent_control/v1"
AGENT_CONTROL_ENVELOPE_SCHEMA_PATH = "docs/agent-control-schema.v1.json"

# Published size budget for one rendered envelope, in bytes of canonical JSON.
#
# Derived, not guessed. The fixed cost is the artifact map — twelve entries of
# path plus a 71-character content id, about 1.6 KiB — plus roughly 0.7 KiB of
# scalars. The variable cost is three capped prose fields and one exact command,
# about 1.4 KiB more. 4 KiB covers that with headroom and is well under the
# ~9 KiB `verifier.json` plus ~6 KiB `agent-handoff.json` an agent reads today
# to answer the same question.
#
# It is a *budget*, pinned by `tests/test_agent_control_envelope.py`, and
# deliberately not a validator. Two fields can exceed it in principle — a very
# long reviewer list, or an unusually long exact command — and in both cases
# dropping content to meet a size target would remove something safety-relevant.
# A control surface that raised because a Git error message ran long would deny
# an answer at the moment the caller most needs one.
MAX_AGENT_CONTROL_ENVELOPE_BYTES = 4096

# Free-text cap applied by the projection before construction. Prose explains;
# it is never parsed, and the unabridged text is always in the bound artifacts.
# Commands, paths, hashes, and reviewer names are never truncated — those are
# executed or matched, not read.
MAX_ENVELOPE_PROSE_CHARS = 400
PROSE_TRUNCATION_MARKER = " […]"

# Which command produced this answer. ``check`` reaches no release decision and
# publishes no pointer; the other three do both.
AgentControlOperation = Literal["verify", "preview", "scan", "check"]

# Whether this envelope came from the run that decided, or from re-reading the
# published pointer at a later refresh boundary. Both are current at emit time;
# a refresh additionally proves the decision still describes the live workspace.
AgentControlSource = Literal["run", "refresh"]

# Tool execution status, mirroring ``VerifierArtifact.execution`` exactly.
AgentControlExecution = Literal["not_run", "succeeded", "skipped", "failed"]

# Which engine produced ``decision``. ``init``/``doctor``-style commands run
# before any decision exists and report ``none``; naming the source keeps
# ``decision`` from meaning "the gate said" in one command and "setup said" in
# another once the vocabulary rolls out (see #323).
AgentControlDecisionSource = Literal["release_decision", "agent_boundary", "setup", "none"]

# Who owns the next step. ``none`` is only correct when there is no next step.
AgentControlActor = Literal["coding_agent", "human", "none"]

BoundedProse = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ENVELOPE_PROSE_CHARS),
]


class AgentControlArtifactRef(BaseModel):
    """A content-addressed pointer to one forensic artifact.

    Path and hash only. The envelope answers what to do; these answer where to
    look if a human or a deeper tool needs the evidence behind it.

    ``path`` is relative to the directory the command was run from, not to the
    reports directory, because the envelope is printed to stdout and carries no
    other directory context. A reader must be able to open it as given.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=CONTENT_ID_PATTERN)


class AgentControlEnvelope(BaseModel):
    """``shipgate.agent_control/v1`` — the one compact control answer."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "required": [
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
        },
    )

    schema_version: Literal["shipgate.agent_control/v1"] = AGENT_CONTROL_ENVELOPE_SCHEMA_VERSION
    contract_version: str = Field(min_length=1)
    operation: AgentControlOperation
    source: AgentControlSource

    # --- did the tool run? -------------------------------------------------
    execution: AgentControlExecution
    # ``None`` when the producing run did not record one — a refresh read of a
    # pointer whose run published no verifier, for instance. Never inferred.
    exit_code: int | None

    # --- what did the gate decide? ----------------------------------------
    decision: str | None
    decision_source: AgentControlDecisionSource

    # --- what may the agent do? -------------------------------------------
    control_state: AgentControlState
    permissions: FullAgentPermissions | PublishOnlyPermissions | NoAgentPermissions
    # An obligation that outlives ``next_action``: a `repair` route can be
    # completed and still owe a verification. Dropping it would leave an agent
    # believing the named step was the last one.
    verify_required: bool
    next_actor: AgentControlActor
    next_action: AgentControlAction | None
    human_review: NoHumanReview | RequiredHumanReview
    reason: BoundedProse

    # --- where is the evidence? -------------------------------------------
    current_control_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    artifacts: dict[str, AgentControlArtifactRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _projection_is_self_consistent(self) -> AgentControlEnvelope:
        """Reject copies that contradict the producers they were copied from.

        Each rule below restates an invariant the authoritative producer already
        guarantees. Restating them here is what makes this a projection with
        teeth: a future emitter that assembles the envelope by hand cannot
        publish a shape the union would have refused.
        """

        completion = self.control_state == "complete"
        if self.permissions.merge != completion or self.permissions.report_complete != completion:
            raise ValueError(
                "merge and report_complete are authorized exactly when control_state is complete"
            )
        if completion and self.execution == "failed":
            # The converse is intentionally *not* enforced: a succeeded run is
            # routinely non-complete, and pretending otherwise is the confusion
            # this envelope exists to prevent.
            raise ValueError("a failed execution cannot authorize completion")
        if completion and self.verify_required:
            raise ValueError("a complete result cannot still owe a verification")
        if self.control_state == "human_review_required" and self.permissions.authorizes_anything:
            raise ValueError("a stopping state cannot authorize any action")
        if (self.human_review.required is True) != (
            self.control_state in {"review_publishable", "human_review_required"}
        ):
            raise ValueError("human_review.required must agree with the human-owned control states")
        if (self.decision is None) != (self.decision_source == "none"):
            raise ValueError("decision and decision_source must both be present or both absent")

        expected_actor: AgentControlActor
        if self.next_action is None:
            expected_actor = "none"
        else:
            expected_actor = "human" if self.next_action.actor == "human" else "coding_agent"
        if self.next_actor != expected_actor:
            raise ValueError("next_actor must name the actor of next_action")
        if self.next_actor == "none" and not completion:
            # A non-terminal answer with nobody named is the dead end #338 is
            # about. A refresh that cannot recover the route is the one honest
            # exception, and it names the human rather than nobody.
            raise ValueError("a non-complete envelope must name the actor who acts next")
        return self


def truncate_prose(value: str) -> str:
    """Cap one free-text field deterministically for the size budget."""

    text = value.strip()
    if len(text) <= MAX_ENVELOPE_PROSE_CHARS:
        return text
    keep = MAX_ENVELOPE_PROSE_CHARS - len(PROSE_TRUNCATION_MARKER)
    return text[:keep].rstrip() + PROSE_TRUNCATION_MARKER


__all__ = [
    "AGENT_CONTROL_ENVELOPE_SCHEMA_PATH",
    "AGENT_CONTROL_ENVELOPE_SCHEMA_VERSION",
    "MAX_AGENT_CONTROL_ENVELOPE_BYTES",
    "MAX_ENVELOPE_PROSE_CHARS",
    "PROSE_TRUNCATION_MARKER",
    "AgentControlActor",
    "AgentControlArtifactRef",
    "AgentControlDecisionSource",
    "AgentControlEnvelope",
    "AgentControlExecution",
    "AgentControlOperation",
    "AgentControlSource",
    "truncate_prose",
]
