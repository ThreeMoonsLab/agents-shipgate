from __future__ import annotations

from typing import Literal, get_args

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.patches import SuggestedPatchKind

MvpTier = Literal["core", "adapter", "evidence", "lifecycle", "hygiene"]


class CheckMetadata(BaseModel):
    # Plugins may supply ``AGENTS_SHIPGATE_METADATA`` with either ``id`` or
    # ``check_id`` as the identifier key. Built-ins continue to use ``id``;
    # ``check_id`` is accepted for symmetry with the field name used in
    # ``Finding.check_id`` so newer plugins can use a single spelling
    # across metadata + emitted findings.
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(validation_alias=AliasChoices("id", "check_id"))
    category: str
    default_severity: Severity
    # Public triage/display tier for the MVP Tool-Use Readiness story.
    # This is catalog metadata only: it does not affect severity,
    # fingerprints, baselines, release decisions, or CI exit behavior.
    mvp_tier: MvpTier = "hygiene"
    description: str
    rationale: str | None = None
    fires_when: str | None = None
    evidence_fields: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    docs_url: str | None = None
    # v0.7 remediation policy: describes per-check policy independent of
    # any scan run. The mirror Finding-level fields land in PR 3 and are
    # derived from `Finding.patches` when present, falling back to these
    # check-level values otherwise.
    autofix_safe: bool = False
    requires_human_review: bool = True
    suggested_patch_kind: SuggestedPatchKind = "manual"
    # Reviewer-grade escalation: when ``True``, ``annotate_remediation``
    # forces ``autofix_safe=False`` and ``requires_human_review=True``
    # regardless of the per-patch derivation. Used for the high-risk
    # categories — approval/confirmation, idempotency, broad-scope,
    # prohibited-action, runtime-trace — where even a high-confidence
    # non-manual patch must not auto-apply without explicit human review.
    # In-process only (not serialised in ``report.json``).
    requires_human_review_regardless_of_patch: bool = False
    # Advisory observer findings can be worth publishing even when their
    # predicate support is intentionally too weak for release policy. The scan
    # still emits an evidence gap and release_decision still excludes the
    # finding through ``support.policy_eligible=False``; this flag only keeps
    # the detailed finding in ``report.findings`` so its non-gating audience
    # does not lose the evidence. In-process routing metadata, not catalog
    # wire surface.
    publish_when_policy_ineligible: bool = Field(default=False, exclude=True)
    # v0.17 (M1 + M5): hard severity floor used by two callers.
    # M1 (manifest-side): ``checks.severity_overrides`` cannot resolve
    # to a weaker severity than ``floor_severity``; the resolver raises
    # ConfigError (exit 2) and no acknowledgement bypasses it.
    # M5 (plugin-side): plugin self-consistency check rejects plugins
    # whose declared ``floor_severity`` exceeds their own
    # ``default_severity``.
    # ``None`` (default) means no floor — preserves v0.x behavior for
    # every check that doesn't opt in. Only release-critical trust-spine
    # checks declare a floor. Severity ranking (weakest → strongest):
    # ``info < low < medium < high < critical``.
    floor_severity: Severity | None = None
    # v0.18 (PR #1): formalizes the M1 dynamic-severity contract.
    # True iff this check's emitted finding severity depends on
    # user-declared manifest values (e.g.,
    # ``SHIP-ACTION-POLICY-VIOLATION`` emits at
    # ``action_surface.policies[].severity``). The severity-override
    # resolver MUST receive the manifest-effective default via
    # ``extra_known_check_defaults``; otherwise tier-crossing comparison
    # runs against the static catalog default and an aggressive override
    # can silently bypass the gate.
    #
    # Built-in checks marking ``dynamic_default=True`` MUST also declare
    # ``floor_severity`` (enforced by the model validator below). Plugins
    # cannot set ``dynamic_default=True``: the plugin validation pipeline
    # rejects such plugins with status ``dynamic_default_not_supported``.
    dynamic_default: bool = False

    @model_validator(mode="after")
    def _check_floor_not_above_default(self) -> CheckMetadata:
        if self.floor_severity is None:
            return self
        order = list(get_args(Severity))
        if order.index(self.floor_severity) > order.index(self.default_severity):
            raise ValueError(
                f"floor_severity={self.floor_severity!r} must not exceed "
                f"default_severity={self.default_severity!r} for {self.id!r}"
            )
        return self

    @model_validator(mode="after")
    def _check_dynamic_default_requires_floor(self) -> CheckMetadata:
        if self.dynamic_default and self.floor_severity is None:
            raise ValueError(
                f"check {self.id!r} has dynamic_default=True but no "
                f"floor_severity; a swing check requires a floor to "
                f"prevent silent downgrade bypass."
            )
        return self
