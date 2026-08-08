from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.agent_control import AgentControl, normalize_legacy_agent_control
from agents_shipgate.schemas.common import ReleaseDecisionStatus
from agents_shipgate.schemas.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.report import ReleaseDecision
from agents_shipgate.schemas.verification_identity import CONTENT_ID_PATTERN

VerifierBaseStatus = Literal[
    "not_requested",
    "skipped",
    "diff_from_provided",
    "ref_missing",
    "archive_failed",
    "missing_manifest",
    "scan_failed",
    "cache_hit",
    "succeeded",
]
VerifierExecution = Literal["not_run", "succeeded", "skipped", "failed"]
VerifierHeadStatus = VerifierExecution
# How completely the compared change set was read, and — when it was not read
# in full — why. This is an input-acquisition fact, never a verdict: an
# unreadable diff says nothing about what the PR contains, so a consumer must
# not read anything but ``complete`` as evidence that a PR is unrelated to
# agent capabilities.
# ``unknown`` is reachable only through legacy normalization: a pre-v0.7
# artifact recorded no input health at all, and saying so is the one honest
# answer. Current emitters never produce it. Like every value other than
# ``complete`` it withholds permission to read a negative trigger verdict.
DiffCompleteness = Literal["complete", "partial", "unavailable", "unknown"]
DiffInputReason = Literal[
    # Verification stopped before it read any diff (e.g. no manifest to gate
    # against). Nothing failed in Git; nothing about the change set is known.
    "not_attempted",
    "refs_missing",
    # A shallow checkout truncated a merge base that does exist (deepen), as
    # against ``unrelated_histories``, where no common ancestor exists at all
    # and no fetch can create one.
    "merge_base_missing",
    "unrelated_histories",
    "objects_missing",
    "metadata_limit_exceeded",
    "body_limit_exceeded",
    "git_timeout",
    "git_failed",
]
MergeVerdict = Literal[
    "mergeable",
    "human_review_required",
    "insufficient_evidence",
    "blocked",
    "unknown",
]
# Whether Shipgate actually evaluated the change — orthogonal to the verdict.
# Disambiguates a ``mergeable`` verdict: "verified" (Shipgate ran and reached a
# determination) vs "not_applicable" (skipped — nothing to gate) vs "unknown"
# (scan could not complete). Never read "mergeable" alone as "verified safe".
Applicability = Literal["not_evaluated", "verified", "not_applicable", "failed"]
CapabilityChangeBucket = Literal["added", "modified", "removed"]
CapabilityReleaseImpact = Literal[
    "blocks_release",
    "review_required",
    "insufficient_evidence",
    "informational",
    "none",
]

# The projection from the canonical release verdict (``ReleaseDecisionStatus``,
# the ONE thing ``build_release_decision`` computes) onto the agent-facing
# ``MergeVerdict``. Keyed with ``ReleaseDecisionStatus`` so a key that is not a
# real release status is a type error, and covered by a totality test
# (tests/test_verdict_contract.py) so adding a release status without a mapping
# fails CI rather than silently falling back. This dict is the only bridge
# between the two vocabularies.
_DECISION_TO_VERDICT: dict[ReleaseDecisionStatus, MergeVerdict] = {
    "passed": "mergeable",
    "review_required": "human_review_required",
    "insufficient_evidence": "insufficient_evidence",
    "blocked": "blocked",
}


def map_merge_verdict(decision: str | None) -> MergeVerdict:
    """Project ``release_decision.decision`` onto a merge verdict.

    ``None`` (no head scan / no decision) is ``unknown``. A decision string
    outside the canonical vocabulary fails safe to ``human_review_required``
    rather than ``mergeable`` — an unrecognized verdict must never auto-pass.
    """
    if decision is None:
        return "unknown"
    return _DECISION_TO_VERDICT.get(decision, "human_review_required")  # type: ignore[arg-type]


def merge_verdict_for(
    *,
    decision: str | None,
    execution: str | None = None,
    head_status: str | None = None,
) -> MergeVerdict:
    """Single authority for deriving a ``MergeVerdict`` for a verify run.

    When the head scan produced a ``release_decision`` the verdict is a pure
    projection of it (``map_merge_verdict``). With no decision the verdict
    reflects *why*: a skipped head (Shipgate had nothing to gate) is
    ``mergeable``; any other no-decision state (scan failed, or not yet run)
    is ``unknown``. Centralized here so the orchestrator — or any future
    caller — cannot invent a second, inconsistent rule.
    """
    if decision is not None:
        return map_merge_verdict(decision)
    resolved = execution or head_status or "not_run"
    return "mergeable" if resolved == "skipped" else "unknown"


def applicability_for(
    *,
    decision: str | None,
    execution: str | None = None,
    head_status: str | None = None,
) -> Applicability:
    """Whether Shipgate actually evaluated this change — orthogonal to the verdict.

    A produced ``decision`` means Shipgate was applicable and reached a
    determination (``"verified"`` — regardless of pass/block). A *skipped* head
    means there was nothing to gate (``"not_applicable"``). Anything else — scan
    failed, or not yet run — is ``"unknown"``. This is the field that keeps a
    ``merge_verdict`` of ``"mergeable"`` from being read as "verified safe" when
    Shipgate in fact did not need to run. Mirrors ``merge_verdict_for`` so the
    two stay in lock-step.
    """
    if decision is not None:
        return "verified"
    resolved = execution or head_status or "not_run"
    if resolved == "skipped":
        return "not_applicable"
    if resolved == "failed":
        return "failed"
    return "not_evaluated"


class VerifierNextAction(BaseModel):
    """Deprecated v0.1/v0.2 reader model; current artifacts use AgentControl."""

    model_config = ConfigDict(extra="forbid")

    actor: Literal["coding_agent", "human"] = "human"
    kind: str = "review"
    command: str | None = None
    why: str = ""


class VerifierHumanReview(BaseModel):
    """Deprecated v0.1/v0.2 reader model; current artifacts use AgentControl."""

    model_config = ConfigDict(extra="forbid")

    required: bool = False
    why: str | None = None


class VerifierRepair(BaseModel):
    """One deterministic repair affordance or prohibition.

    The verifier owns the actor and safety boundary. These rows are not model
    suggestions: they are a structured projection of remediation metadata and
    trust-root rules so coding agents can distinguish mechanical fixes from
    human-only authority decisions.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    actor: Literal["coding_agent", "human"]
    kind: str
    target: str | None = None
    finding_id: str | None = None
    check_id: str | None = None
    command: str | None = None
    reason: str


class VerifierFixTaskPatch(BaseModel):
    """A machine-applicable patch projected into the fix task.

    Repair aid only — never a gate input. ``patch`` carries the
    discriminated Patch payload (``set_pointer`` / ``append_pointer`` /
    ``remove_pointer``) exactly as the head scan emitted it; ``manual``
    patches are intentionally excluded because their guidance already
    appears in ``instructions``.
    """

    model_config = ConfigDict(extra="forbid")

    finding_id: str | None = None
    check_id: str = ""
    patch: dict[str, Any] = Field(default_factory=dict)


class VerifierFixTask(BaseModel):
    """The single repair task a verify run hands to whoever acts next.

    Routing is deterministic and projected from the head scan — never an LLM
    judgment. ``coding_agent`` + ``safe_to_attempt=True`` means the gating
    gaps are mechanical (every gating finding is ``autofix_safe``): the agent
    may fix them and re-run ``verification_command``. ``human`` +
    ``safe_to_attempt=False`` means an authority gap a coding agent must not
    invent its way past — missing approval/idempotency evidence, a weakened
    policy, or a touched trust root. ``forbidden_shortcuts`` are the
    reward-hacking moves that are never acceptable for either actor.
    ``patches`` (v0.12+) carries the machine-applicable suggested patches for
    the gating findings when verify ran with ``--suggest-patches`` and the
    task routes to the coding agent.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"actor": {"const": "human"}},
                        "required": ["actor"],
                    },
                    "then": {"properties": {"safe_to_attempt": {"const": False}}},
                },
                {
                    "if": {
                        "properties": {
                            "actor": {"const": "coding_agent"},
                            "safe_to_attempt": {"const": True},
                        },
                        "required": ["actor", "safe_to_attempt"],
                    },
                    "then": {
                        "properties": {
                            "verification_command": {
                                "type": "string",
                                "minLength": 1,
                                "pattern": "\\S",
                            }
                        },
                        "required": ["verification_command"],
                    },
                },
            ]
        },
    )

    actor: Literal["coding_agent", "human"]
    safe_to_attempt: bool
    instructions: list[str] = Field(default_factory=list)
    allowed_repairs: list[VerifierRepair] = Field(default_factory=list)
    forbidden_repairs: list[VerifierRepair] = Field(default_factory=list)
    forbidden_shortcuts: list[str] = Field(default_factory=list)
    verification_command: str | None = None
    patches: list[VerifierFixTaskPatch] = Field(default_factory=list)

    @model_validator(mode="after")
    def _routing_is_consistent(self) -> VerifierFixTask:
        # The anti-reward-hacking guarantee: an authority gap routed to a
        # human can never be marked safe for a coding agent to attempt.
        if self.actor == "human" and self.safe_to_attempt:
            raise ValueError(
                "VerifierFixTask with actor='human' must have "
                "safe_to_attempt=False (authority gaps are not agent-safe)."
            )
        if self.actor == "coding_agent" and self.safe_to_attempt:
            if not self.verification_command or not self.verification_command.strip():
                raise ValueError(
                    "An agent-safe VerifierFixTask must provide an exact verification_command."
                )
        return self


class VerifierCapabilityChange(BaseModel):
    """One reviewer-facing capability change projected for verifier output."""

    model_config = ConfigDict(extra="forbid")

    id: str
    change_type: str
    change_bucket: CapabilityChangeBucket
    subject_kind: str
    subject: str
    impact: CapabilityReleaseImpact = "informational"
    rationale: str
    source_path: str | None = None
    source_start_line: int | None = None
    related_finding_ids: list[str] = Field(default_factory=list)


class VerifierCapabilityReview(BaseModel):
    """Derived capability-review rollup for PR comments and Action outputs.

    This is a projection only. It never gates independently of
    ``report.json.release_decision.decision``.
    """

    model_config = ConfigDict(extra="forbid")

    added: int = 0
    modified: int = 0
    removed: int = 0
    trust_root_touched: bool = False
    policy_weakened: bool = False
    top_changes: list[VerifierCapabilityChange] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# Only these three describe history or objects that a fetch can make local.
# The rest are deterministic failures that another fetch cannot touch, so a
# ``fetch_repairable`` claim about them is rejected at construction rather than
# published as an instruction that loops.
_FETCH_REPAIRABLE_REASONS = frozenset(
    {"refs_missing", "merge_base_missing", "objects_missing"}
)


class VerifierDiffStatus(BaseModel):
    """Whether the compared change set was actually read, and why not.

    Emitted on every verifier artifact so automation never has to infer input
    health from a verdict. ``completeness: "complete"`` is the only value that
    licenses reading a negative trigger result — anything else means the
    evidence the verdict would rest on was missing, and the artifact says so
    instead of reporting "nothing in this PR signals a tool-surface change".
    """

    model_config = ConfigDict(extra="forbid")

    completeness: DiffCompleteness = "complete"
    # Present exactly when the diff was read neither completely nor not-at-all:
    # ``complete`` has nothing to explain, and ``unknown`` has no record to
    # explain it with.
    reason: DiffInputReason | None = None
    # Bounded, path-redacted excerpt of Git's own diagnostic. Diagnostics only.
    detail: str | None = None
    # The precise repair, e.g. deepen history or hydrate partial-clone objects.
    remediation: str | None = None
    # Whether making refs/objects available locally can repair the failure.
    # ``False`` routes to a human instead of another fetch attempt.
    fetch_repairable: bool = False

    @model_validator(mode="after")
    def _reason_tracks_completeness(self) -> VerifierDiffStatus:
        explainable = self.completeness in {"partial", "unavailable"}
        if explainable != (self.reason is not None):
            raise ValueError(
                "VerifierDiffStatus.reason must be present exactly when the "
                "diff was partially read or unavailable"
            )
        if self.completeness != "complete" and self.fetch_repairable and (
            self.reason not in _FETCH_REPAIRABLE_REASONS
        ):
            raise ValueError(
                f"VerifierDiffStatus.fetch_repairable is not true for "
                f"{self.reason!r}: fetching cannot repair it"
            )
        return self

    @classmethod
    def unknown(cls) -> VerifierDiffStatus:
        """The input health of an artifact that predates v0.7 reporting."""

        return cls(
            completeness="unknown",
            detail="This artifact predates verifier v0.7 input-health reporting.",
        )


AgentStopReason = Literal[
    "self_approval_prohibited",
    "blocked_findings",
    "insufficient_evidence",
    "human_review_required",
    "scan_incomplete",
]


class AgentController(BaseModel):
    """Deprecated v0.1/v0.2 reader model; never emitted by verifier v0.3.

    Historically this re-shaped ``merge_verdict``,
    ``can_merge_without_human``, ``fix_task``, ``capability_review`` — into the
    four questions an agent must answer without human interpretation: may I claim
    the task done (``completion_allowed``), must I stop for a human
    (``must_stop`` / ``stop_reason``), what may I run next
    (``allowed_next_commands``), and what must I never edit or do to get past the
    gate (``forbidden_file_edits`` / ``forbidden_actions``).

    It introduces NO new decision: ``completion_allowed`` is locked to
    ``can_merge_without_human`` by ``VerifierArtifact``, and every other field is
    a deterministic projection of the head scan. ``forbidden_file_edits`` and
    ``forbidden_actions`` are a STANDING negative affordance — present on every
    verdict, including ``mergeable`` — so a passing run never reads as "anything
    goes".
    """

    model_config = ConfigDict(extra="forbid")

    completion_allowed: bool = False
    must_stop: bool = True
    stop_reason: AgentStopReason | None = None
    allowed_next_commands: list[str] = Field(default_factory=list)
    forbidden_file_edits: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    user_message_template: str | None = None


class VerifierArtifact(BaseModel):
    """Machine-readable artifact emitted by ``agents-shipgate verify``.

    This is an orchestration record only. The release gate remains
    ``report.json.release_decision.decision`` from the head scan.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"decision": {"const": "passed"}},
                        "required": ["decision"],
                    },
                    "then": {
                        "properties": {
                            "execution": {"const": "succeeded"},
                            "head_status": {"const": "succeeded"},
                            "merge_verdict": {"const": "mergeable"},
                            "applicability": {"const": "verified"},
                            "can_merge_without_human": {"const": True},
                            "control": {
                                "properties": {"state": {"const": "complete"}},
                                "required": ["state"],
                            },
                            "fix_task": {"type": "null"},
                            "capability_review": {
                                "properties": {
                                    "trust_root_touched": {"const": False},
                                    "policy_weakened": {"const": False},
                                }
                            },
                            "release_decision": {
                                "type": "object",
                                "properties": {
                                    "decision": {"const": "passed"},
                                    "blockers": {"maxItems": 0},
                                    "review_items": {"maxItems": 0},
                                    "evidence_coverage": {
                                        "properties": {
                                            "human_review_recommended": {"const": False},
                                            "evidence_gaps": {"maxItems": 0},
                                        }
                                    },
                                }
                            },
                        }
                    },
                },
                {
                    "if": {
                        "properties": {"can_merge_without_human": {"const": True}},
                        "required": ["can_merge_without_human"],
                    },
                    "then": {
                        "oneOf": [
                            {
                                "properties": {
                                    "execution": {"const": "succeeded"},
                                    "decision": {"const": "passed"},
                                    "applicability": {"const": "verified"},
                                }
                            },
                            {
                                "properties": {
                                    "execution": {"const": "skipped"},
                                    "decision": {"type": "null"},
                                    "applicability": {"const": "not_applicable"},
                                }
                            },
                        ],
                        "properties": {
                            "control": {
                                "properties": {"state": {"const": "complete"}},
                                "required": ["state"],
                            }
                        },
                    },
                    "else": {
                        "properties": {
                            "control": {
                                "properties": {
                                    "state": {
                                        "enum": [
                                            "agent_action_required",
                                            "review_publishable",
                                            "human_review_required",
                                        ]
                                    }
                                },
                                "required": ["state"],
                            }
                        }
                    },
                },
                {
                    # Keyed on the permission vector, not on the state: an
                    # agent repair route asserts exactly the same thing about
                    # the change as a publishable review does. The four
                    # progress booleans are Literal-pinned to move together, so
                    # testing one is testing all four.
                    "if": {
                        "properties": {
                            "control": {
                                "properties": {
                                    "completion_allowed": {"const": False},
                                    "permissions": {
                                        "properties": {"update_pr": {"const": True}},
                                        "required": ["update_pr"],
                                    },
                                },
                                "required": ["completion_allowed", "permissions"],
                            }
                        },
                        "required": ["control"],
                    },
                    "then": {
                        "required": ["execution", "diff_status", "release_decision"],
                        "properties": {
                            "execution": {"const": "succeeded"},
                            "diff_status": {
                                "properties": {"completeness": {"const": "complete"}},
                                "required": ["completeness"],
                            },
                            "release_decision": {
                                "type": "object",
                                "properties": {
                                    "decision": {"type": "string", "not": {"const": "blocked"}}
                                },
                                "required": ["decision"],
                            },
                        },
                    },
                },
                {
                    "if": {
                        "properties": {
                            "authorization": {
                                "properties": {"status": {"const": "accepted"}},
                                "required": ["status"],
                            }
                        },
                        "required": ["authorization"],
                    },
                    "then": {
                        "required": [
                            "execution",
                            "head_status",
                            "release_decision",
                            "decision",
                            "merge_verdict",
                            "applicability",
                            "can_merge_without_human",
                            "control",
                            "fix_task",
                        ],
                        "properties": {
                            "execution": {"const": "succeeded"},
                            "head_status": {"const": "succeeded"},
                            "decision": {"const": "review_required"},
                            "merge_verdict": {"const": "human_review_required"},
                            "applicability": {"const": "verified"},
                            "can_merge_without_human": {"const": False},
                            "control": {
                                "properties": {
                                    "state": {"const": "agent_action_required"},
                                    "completion_allowed": {"const": False},
                                    "next_action": {
                                        "properties": {"kind": {"const": "repair"}},
                                        "required": ["kind"],
                                    },
                                    "allowed_next_commands": {
                                        "minItems": 1,
                                        "maxItems": 1,
                                    },
                                },
                                "required": [
                                    "state",
                                    "completion_allowed",
                                    "next_action",
                                    "allowed_next_commands",
                                ],
                            },
                            "fix_task": {"type": "null"},
                            "release_decision": {
                                "type": "object",
                                "properties": {
                                    "decision": {"const": "review_required"}
                                },
                                "required": ["decision"],
                            },
                        }
                    },
                },
            ]
        },
    )

    verifier_schema_version: Literal["0.7"] = "0.7"
    static_analysis_only: Literal[True] = True
    runtime_behavior_verified: Literal[False] = False
    static_verdict_disclaimer: str = STATIC_VERDICT_DISCLAIMER
    workspace: str
    request_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    subject_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    input_set_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    engine_requirement_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    executor_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    decision_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    config: str
    base_ref: str | None = None
    head_ref: str = "HEAD"
    changed_files: list[str] = Field(default_factory=list)
    diff_text_available: bool = False
    # Required, so a current artifact cannot omit the input-health contract:
    # a payload with no ``diff_status`` would be indistinguishable from one
    # that read its diff cleanly. Pre-v0.7 artifacts are normalized to
    # ``VerifierDiffStatus.unknown()`` on the legacy path instead.
    diff_status: VerifierDiffStatus
    trigger: dict[str, Any] = Field(default_factory=dict)
    base_status: VerifierBaseStatus = "not_requested"
    base_tree_sha: str | None = None
    head_tree_sha: str | None = None
    base_report_json: str | None = None
    base_notes: list[str] = Field(default_factory=list)
    execution: VerifierExecution = "not_run"
    # One-cycle compatibility mirror.  It is locked byte-for-byte to
    # ``execution`` and is not an independent state machine.
    head_status: VerifierHeadStatus = "not_run"
    head_report_json: str | None = None
    head_exit_code: int = 0
    release_decision: ReleaseDecision | None = None
    agent_summary: dict[str, Any] | None = None
    reviewer_summary: dict[str, Any] | None = None
    capability_review: VerifierCapabilityReview = Field(default_factory=VerifierCapabilityReview)
    mode: str = "advisory"
    decision: str | None = None
    merge_verdict: MergeVerdict = "unknown"
    applicability: Applicability = "not_evaluated"
    can_merge_without_human: bool = False
    control: AgentControl
    authorization: AuthorizationEvaluationV1
    headline: str | None = None
    fix_task: VerifierFixTask | None = None
    forbidden_file_edits: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_control(cls, data: Any) -> Any:
        """Read v0.2 artifacts fail-closed while emitting only the v0.3 shape."""

        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        legacy_version = normalized.get("verifier_schema_version")
        legacy = legacy_version in {"0.1", "0.2", "0.3", "0.4", "0.5", "0.6"}
        if not legacy:
            # Current v0.7 artifacts must already carry the authoritative
            # control union.  Silently synthesizing a missing or malformed
            # current control would turn an internal consistency failure into
            # a trusted handoff.  Only frozen prior readers are normalized.
            return normalized
        normalized["verifier_schema_version"] = "0.7"
        # A pre-v0.7 artifact recorded nothing about whether its diff was
        # readable. Defaulting that to ``complete`` would manufacture the one
        # claim the whole field exists to stop.
        normalized.setdefault(
            "diff_status", VerifierDiffStatus.unknown().model_dump(mode="json")
        )
        normalized.setdefault(
            "authorization",
            AuthorizationEvaluationV1.not_requested().model_dump(mode="json"),
        )

        execution = normalized.get("execution") or normalized.get("head_status")
        execution = execution or "not_run"
        if legacy and normalized.get("mode") == "preview":
            execution = "not_run"
            normalized["merge_verdict"] = "unknown"
            normalized["can_merge_without_human"] = False
        normalized.setdefault("execution", execution)
        if legacy and normalized.get("mode") == "preview":
            normalized["execution"] = execution
            normalized["head_status"] = execution
        else:
            normalized.setdefault("head_status", execution)
        release = normalized.get("release_decision")
        substrate_decision = release.get("decision") if isinstance(release, dict) else None
        normalized.setdefault("decision", substrate_decision)
        normalized.setdefault(
            "merge_verdict",
            merge_verdict_for(
                decision=substrate_decision,
                execution=str(execution),
            ),
        )
        normalized.setdefault(
            "can_merge_without_human",
            bool(
                substrate_decision == "passed"
                or (substrate_decision is None and execution == "skipped")
            ),
        )
        expected_applicability = applicability_for(
            decision=substrate_decision,
            execution=str(execution),
        )
        if legacy and normalized.get("applicability") == "unknown":
            normalized["applicability"] = expected_applicability
        else:
            normalized.setdefault("applicability", expected_applicability)

        legacy_controller = normalized.get("agent_controller")
        legacy_payload: dict[str, Any] = (
            dict(legacy_controller) if isinstance(legacy_controller, dict) else {}
        )
        if "completion_allowed" not in legacy_payload:
            legacy_payload["completion_allowed"] = bool(normalized.get("can_merge_without_human"))
        for key in ("first_next_action", "human_review"):
            if key in normalized:
                legacy_payload[key] = normalized[key]
        fix_task = normalized.get("fix_task")
        verification_command = (
            fix_task.get("verification_command") if isinstance(fix_task, dict) else None
        )
        if (
            isinstance(fix_task, dict)
            and fix_task.get("actor") == "coding_agent"
            and fix_task.get("safe_to_attempt") is True
            and verification_command
        ):
            legacy_payload["first_next_action"] = {
                "actor": "coding_agent",
                "kind": "repair",
                "command": verification_command,
                "why": (
                    (fix_task.get("instructions") or [None])[0]
                    or "Apply the mechanical repair and rerun verification."
                ),
            }
            legacy_payload["verify_required"] = True
        if "control" not in normalized:
            normalized["control"] = normalize_legacy_agent_control(
                legacy_payload,
                verification_command=verification_command,
            )
        if isinstance(legacy_controller, dict):
            normalized.setdefault(
                "forbidden_file_edits",
                list(legacy_controller.get("forbidden_file_edits") or []),
            )
            normalized.setdefault(
                "forbidden_actions",
                list(legacy_controller.get("forbidden_actions") or []),
            )
        for legacy_key in ("agent_controller", "first_next_action", "human_review"):
            normalized.pop(legacy_key, None)
        return normalized

    @model_validator(mode="after")
    def _verdict_projects_release_decision(self) -> VerifierArtifact:
        """Lock the one-decision-engine contract structurally.

        Whenever a head ``release_decision`` is present, the agent-facing
        ``merge_verdict`` and the convenience ``decision`` copy MUST be exact
        projections of it — never an independently computed second opinion.
        Construction-time enforcement makes an inconsistent artifact
        impossible to emit. (No release_decision — skipped / failed / preview
        — is left unconstrained: there is no substrate to project.)
        """
        if self.static_verdict_disclaimer != STATIC_VERDICT_DISCLAIMER:
            raise ValueError("VerifierArtifact must preserve the static-verdict disclaimer")
        if self.release_decision is None:
            if self.decision is not None:
                raise ValueError("decision requires a release_decision substrate")
            expected = merge_verdict_for(decision=None, execution=self.execution)
            if self.merge_verdict != expected:
                raise ValueError(
                    "merge_verdict must project from execution when no release "
                    f"decision exists (expected {expected!r})"
                )
            return self
        if self.release_decision.static_analysis_only is not True:
            raise ValueError("VerifierArtifact release_decision must be static-analysis-only")
        if self.release_decision.runtime_behavior_verified is not False:
            raise ValueError("VerifierArtifact cannot claim runtime behavior was verified")
        release_disclaimer = self.release_decision.static_verdict_disclaimer
        if release_disclaimer != self.static_verdict_disclaimer:
            raise ValueError(
                "VerifierArtifact static-verdict disclaimer must match release_decision"
            )
        substrate = self.release_decision.decision
        if self.decision != substrate:
            raise ValueError(
                "VerifierArtifact.decision must equal "
                "release_decision['decision'] (one decision engine): "
                f"{self.decision!r} != {substrate!r}"
            )
        expected = map_merge_verdict(substrate)
        if self.merge_verdict != expected:
            raise ValueError(
                "VerifierArtifact.merge_verdict must be the projection of "
                f"release_decision['decision']={substrate!r} via "
                f"map_merge_verdict (expected {expected!r}, got "
                f"{self.merge_verdict!r})"
            )
        return self

    @model_validator(mode="after")
    def _applicability_projects_release_decision(self) -> VerifierArtifact:
        """Lock applicability to the substrate, mirroring the verdict lock.

        A present head ``release_decision`` means Shipgate evaluated the change
        and produced a determination, so ``applicability`` MUST be
        ``"verified"``. An *absent* value was already backfilled by
        ``_derive_absent_applicability``; this lock therefore only rejects an
        *explicit* contradiction (e.g. ``"not_applicable"`` passed alongside a
        release decision). Skipped / failed / preview runs have no
        ``release_decision`` substrate and are left unconstrained, exactly like
        ``merge_verdict``.
        """
        if self.execution != self.head_status:
            raise ValueError("head_status must exactly mirror execution")
        expected = applicability_for(
            decision=self.decision,
            execution=self.execution,
        )
        if self.applicability != expected:
            raise ValueError(
                "VerifierArtifact.applicability must project from execution and "
                f"release decision (expected {expected!r}, got {self.applicability!r})"
            )
        if self.release_decision is not None and self.execution != "succeeded":
            raise ValueError("a release decision requires execution='succeeded'")
        return self

    @model_validator(mode="after")
    def _control_projects_gate(self) -> VerifierArtifact:
        expected_can_merge = bool(
            (self.execution == "skipped" and self.release_decision is None)
            or (
                self.execution == "succeeded"
                and self.decision == "passed"
                and self.release_decision is not None
            )
        )
        if self.can_merge_without_human != expected_can_merge:
            raise ValueError(
                "can_merge_without_human must be the pure passed/not-applicable "
                f"projection (expected {expected_can_merge!r})"
            )
        if self.control.completion_allowed != expected_can_merge:
            raise ValueError("control.completion_allowed must equal can_merge_without_human")
        if expected_can_merge and self.control.state != "complete":
            raise ValueError("mergeable artifacts require control.state='complete'")
        if not expected_can_merge and self.control.state == "complete":
            raise ValueError("non-mergeable artifacts cannot authorize completion")
        if self.control.state == "complete" and self.fix_task is not None:
            raise ValueError("complete control cannot carry a pending fix task")
        authorization_accepted = self.authorization.status == "accepted"
        if authorization_accepted:
            if self.execution != "succeeded" or self.decision != "review_required":
                raise ValueError(
                    "accepted human authorization requires a succeeded review_required result"
                )
            if self.merge_verdict != "human_review_required" or self.can_merge_without_human:
                raise ValueError(
                    "human authorization is operational evidence and cannot change merge authority"
                )
            if self.control.state != "agent_action_required":
                raise ValueError(
                    "accepted human authorization must route one exact coding-agent action"
                )
            action = self.control.next_action
            if action.kind != "repair" or getattr(action, "command", None) != self.authorization.command:
                raise ValueError(
                    "authorized control must expose the exact signed operation command"
                )
            if self.control.allowed_next_commands != [self.authorization.command]:
                raise ValueError(
                    "authorized control may expose only the exact signed operation command"
                )
            if self.fix_task is not None:
                raise ValueError(
                    "authorized operational control must not relabel a human review task as agent-safe"
                )
        if self.control.state == "agent_action_required":
            action = self.control.next_action
            if action.kind == "repair":
                if self.fix_task is None and not authorization_accepted:
                    raise ValueError("agent repair control requires a verifier fix task")
                if (
                    self.fix_task is not None
                    and (self.fix_task.actor != "coding_agent" or not self.fix_task.safe_to_attempt)
                ):
                    raise ValueError("agent repair control requires an agent-safe fix task")
                if (
                    self.fix_task is not None
                    and getattr(action, "command", None) != self.fix_task.verification_command
                ):
                    raise ValueError(
                        "agent repair control command must equal the exact fix-task rerun command"
                    )
            elif self.fix_task is not None:
                raise ValueError("non-repair agent control cannot carry a pending fix task")
            if self.release_decision is not None and action.kind != "repair":
                raise ValueError(
                    "a non-passing release decision can route to an agent only through "
                    "an evidence-backed repair task"
                )
        elif self.control.state in {"human_review_required", "review_publishable"}:
            if self.fix_task is not None and (
                self.fix_task.actor != "human" or self.fix_task.safe_to_attempt
            ):
                raise ValueError("human control requires a human-owned, non-safe fix task")
            if self.control.state == "review_publishable":
                # Publishing evidence is authority over the pull request, not
                # over Shipgate: the only command a review route may authorize
                # is the exact rerun that regenerates this same evidence.
                rerun = self.fix_task.verification_command if self.fix_task is not None else None
                permitted = {rerun} if rerun else set()
                if not set(self.control.allowed_next_commands) <= permitted:
                    raise ValueError(
                        "a publishable review may authorize only the exact fix-task "
                        "rerun command"
                    )
        self._assert_publication_rests_on_an_evaluated_change()
        self._assert_passed_substrate_is_consistent()
        return self

    def _assert_publication_rests_on_an_evaluated_change(self) -> None:
        """Bind progress authority to the substrate, on every state.

        The control variant cannot see ``execution``, ``diff_status``, or the
        release decision, so this is the only layer that can tell whether the
        change being published was read at all. Keyed on ``permissions``, not
        on ``state``: an ``agent_action_required`` repair route asserts exactly
        the same thing about the change as a publishable review does.

        ``complete`` is excluded because it is governed by the stricter
        ``can_merge_without_human`` projection above — that is the one state
        where a deterministic *not-applicable* skip legitimately authorizes
        everything without a release decision.
        """

        if self.control.completion_allowed or not self.control.permissions.publishes:
            return
        if self.execution != "succeeded":
            raise ValueError("publication authority requires execution='succeeded'")
        if self.diff_status.completeness != "complete":
            raise ValueError(
                "publication authority requires a completely read diff "
                f"(diff_status.completeness={self.diff_status.completeness!r})"
            )
        if self.release_decision is None:
            raise ValueError("publication authority requires a release decision substrate")
        if self.release_decision.decision == "blocked":
            raise ValueError("a blocked release decision cannot authorize publication")

    def _assert_passed_substrate_is_consistent(self) -> None:
        if self.decision != "passed" or self.release_decision is None:
            return
        if self.release_decision.blockers or self.release_decision.review_items:
            raise ValueError("passed cannot carry blockers or review items")
        coverage = self.release_decision.evidence_coverage
        if coverage.human_review_recommended:
            raise ValueError("passed cannot recommend human review")
        if coverage.evidence_gaps:
            raise ValueError("passed cannot carry evidence gaps")
        if self.capability_review.trust_root_touched:
            raise ValueError("passed cannot carry a touched release trust root")
        if self.capability_review.policy_weakened:
            raise ValueError("passed cannot carry a weakened release policy")

    @property
    def human_review(self):
        """Compatibility accessor; the serialized authority is ``control``."""

        return self.control.human_review

    @property
    def first_next_action(self):
        """Compatibility accessor; the serialized authority is ``control``."""

        return self.control.next_action


__all__ = [
    "AgentController",
    "AgentStopReason",
    "Applicability",
    "CapabilityChangeBucket",
    "CapabilityReleaseImpact",
    "MergeVerdict",
    "VerifierArtifact",
    "VerifierBaseStatus",
    "VerifierCapabilityChange",
    "VerifierCapabilityReview",
    "VerifierFixTask",
    "VerifierFixTaskPatch",
    "VerifierHeadStatus",
    "VerifierHumanReview",
    "VerifierNextAction",
    "VerifierRepair",
    "applicability_for",
    "map_merge_verdict",
    "merge_verdict_for",
]
