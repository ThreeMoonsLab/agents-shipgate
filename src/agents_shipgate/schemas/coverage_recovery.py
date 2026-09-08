"""Optional explanations of who can resolve a measured coverage gap.

These facts neither authorize an edit nor change a release decision. Absence
means the loader did not classify recovery; dynamic evidence can remain
explicitly unresolved even when its source location is known.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class CoverageRecovery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["input_unavailable", "reader_limitation", "unresolved"]
    reason: str


class SourceRecoveryEvidence(BaseModel):
    """A loader's typed observation, joined through its exact warning token.

    The warning is an opaque association token, never parsed for ownership.
    Source identity is retained before sanitization so two explanations that
    collapse to the same public warning cannot silently pick a repair owner.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    warning: str
    source_id: str
    source_type: str
    source_ref: str
    path: str
    recovery: CoverageRecovery
