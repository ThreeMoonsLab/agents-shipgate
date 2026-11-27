from __future__ import annotations

from typing import Literal, get_args

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.patches import SuggestedPatchKind

MvpTier = Literal["core", "adapter", "evidence", "lifecycle", "hygiene"]
# Where a catalog ID stands (#1047). ``active`` checks emit findings.
# ``deprecated`` IDs stay published and configurable for at least one minor
# cycle (STABILITY.md § Check IDs) but new runs emit no findings for them.
# ``projection`` IDs are never findings: they label rows a projection prints —
# a PR annotation or a SARIF result for a semantic evidence gap — and are
# catalogued so ``explain`` answers for every ID the tool prints. No manifest
# setting can name one.
CheckLifecycle = Literal["active", "deprecated", "projection"]


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
    lifecycle: CheckLifecycle = "active"
    # The release whose notes deprecated the ID, and the IDs that now carry
    # its meaning (empty when nothing replaces it). Set only on deprecated IDs.
    deprecated_in: str | None = None
    replaced_by: list[str] = Field(default_factory=list)
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
    # Lexical heuristics never move the release verdict (#1055, Option A).
    # A check whose only evidence for an instance is lexical — a keyword in a
    # description, a word shared with ``prohibited_actions`` — sets this so the
    # instance is published as a visible, non-blocking advisory finding and
    # *no* evidence gap is raised for it. Without the flag a policy-ineligible
    # instance becomes an evidence gap, which moves ``passed`` to
    # ``insufficient_evidence`` on wording alone, and nothing a reviewer can
    # declare closes it (#1035). Instances with policy-eligible support are
    # untouched: they gate exactly as before. In-process routing metadata,
    # not catalog wire surface.
    advisory_when_policy_ineligible: bool = Field(default=False, exclude=True)
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
    def _check_deprecation_fields_need_deprecated(self) -> CheckMetadata:
        if self.lifecycle != "deprecated" and (self.deprecated_in is not None or self.replaced_by):
            raise ValueError(
                f"check {self.id!r} sets deprecated_in or replaced_by but its "
                f"lifecycle is {self.lifecycle!r}; only a deprecated ID has them."
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
