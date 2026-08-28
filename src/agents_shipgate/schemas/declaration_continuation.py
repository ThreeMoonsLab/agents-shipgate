"""The receipt that carries a drafted declaration across its own mutation.

``apply-patches --kinds declare_action --apply`` writes into ``shipgate.yaml``,
which is the trust root. That write supersedes the control that authorized it —
``agents-shipgate agent control`` refuses with ``workspace_changed`` the instant
it lands — so the run that follows is a *fresh* decision over a manifest that
now says more. When the declaration is the thing that makes a risk judgeable,
that decision is ``blocked``, and a blocked decision authorizes nothing. The
proposal Shipgate itself drafted could therefore never reach the person who was
supposed to review it (#429 review).

This receipt is what lets the next run tell "the trust root changed, and I
cannot see how" from "the trust root changed by exactly the declarations I
drafted, and here is the pair of digests that proves it". On that proof, and
only on that proof, the blocked run is publish-only: ``edit``, ``commit``,
``push`` and ``update_pr`` so the proposal reaches review — ``merge`` and
``report_complete`` still denied, so the gate is exactly as strong as it was.

**What it proves, and what it does not.** The two digests pin the *delta*: the
manifest at the comparison ref hashed to ``manifest_sha256_before`` and the
manifest now hashes to ``manifest_sha256_after``, so what this run is judging
is precisely what was applied — nothing else edited the file in between, and
nothing else is riding along. It is not a signature: a caller that writes the
manifest by hand can write a matching receipt. That bound is deliberate and it
is small. Publication is not merge; ``capability_review.policy_weakened`` must
still be false, so the gate's own knobs cannot have loosened; and a declaration
weaker than the evidence for it is a published gap (#409), not a quiet pass. The
worst a forged receipt buys is putting a manifest change in front of a human,
which is the thing it is for.

Digests are over **bytes**, matching the applier and the patch generator. Text
hashing normalizes CRLF and produced a digest a CRLF manifest could never match.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Published schema id. Read by ``verify``; written by ``apply-patches``.
DECLARATION_CONTINUATION_SCHEMA_VERSION = "shipgate.declaration_continuation/v1"

#: File name, written beside the report the patches were applied from — which
#: is the directory ``verify --out`` names, so the next run finds it without
#: being told where to look.
DECLARATION_CONTINUATION_ARTIFACT_NAME = "declaration-continuation.json"

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class AppliedDeclaration(BaseModel):
    """One declaration row the applier wrote, in the questionnaire's own terms.

    Carried for the reader of the receipt, not for the rule: the digests decide
    whether the delta is this receipt's, and this says *what* it was in the
    vocabulary the report used to ask. A reviewer opening the PR should not have
    to diff two manifests to learn which questions an agent answered.
    """

    model_config = ConfigDict(extra="forbid")

    #: The three fields of the ``declare_action`` patch that was applied,
    #: copied rather than re-derived: the questionnaire's own spelling of the
    #: answer path lives in the report, and reconstructing it here would be a
    #: second implementation of it that could disagree.
    target_path: str = Field(min_length=1)
    selector: dict[str, str] = Field(min_length=1)
    declaration: dict[str, str] = Field(min_length=1)


class DeclarationContinuationV1(BaseModel):
    """A ``declare_action`` application, bound to the bytes on both sides."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.declaration_continuation/v1"] = (
        DECLARATION_CONTINUATION_SCHEMA_VERSION
    )
    #: Relative to the report's ``manifest_dir``, so the receipt reads the same
    #: on any machine — the containment rule ``declare_action`` patches follow.
    manifest_path: str = Field(min_length=1)
    #: The manifest's bytes before the write. ``None`` where the file the
    #: applier wrote into is not in the comparison ref at all — a first
    #: adoption, where the manifest is itself uncommitted and there is no
    #: earlier version for a digest to name. Recording that as a digest of
    #: whatever happened to be on disk claimed an anchor the reader could not
    #: check, and left the advertised apply/rerun path immediately blocked
    #: (#429 review).
    manifest_sha256_before: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    manifest_sha256_after: str = Field(pattern=_SHA256_PATTERN)
    #: The report whose drafted patches were applied, by content. A receipt
    #: whose source cannot be named is not one a later run should honour.
    source_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    applied: list[AppliedDeclaration] = Field(min_length=1)

    @field_validator("manifest_path")
    @classmethod
    def _relative_and_contained(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or normalized.startswith("../"):
            raise ValueError(
                "manifest_path must be relative to the manifest directory"
            )
        if ".." in normalized.split("/"):
            raise ValueError("manifest_path must not traverse out of the manifest directory")
        return normalized

    @field_validator("manifest_sha256_after")
    @classmethod
    def _the_write_changed_something(cls, value: str, info) -> str:
        before = info.data.get("manifest_sha256_before")
        if before is not None and before == value:
            raise ValueError(
                "a continuation records an applied change, so the digests must differ"
            )
        return value


__all__ = [
    "DECLARATION_CONTINUATION_ARTIFACT_NAME",
    "DECLARATION_CONTINUATION_SCHEMA_VERSION",
    "AppliedDeclaration",
    "DeclarationContinuationV1",
]
