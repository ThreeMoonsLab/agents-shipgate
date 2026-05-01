"""Patch types attached to findings when ``scan --suggest-patches`` is set.

Per the v0.6 plan §3:
- Discriminated union by ``kind`` (Pydantic ``Field(discriminator="kind")``).
- ``target_file`` is an absolute path (per C13). ``apply-patches``
  enforces a containment check against ``report.manifest_dir`` before
  any write.
- ``ManualPatch`` carries no target — it makes no machine-applicable
  claim; agents and humans use ``instructions`` to decide what to do.

v0.6 ships generators that emit only manifest-target patches. All other
findings get a ``ManualPatch`` populated from ``CheckMetadata.recommendation``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["low", "medium", "high"]


class _PatchBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetPointerPatch(_PatchBase):
    """Set the value at a JSON pointer inside a YAML or JSON file."""

    kind: Literal["set_pointer"] = "set_pointer"
    target_file: str
    pointer: str
    value: Any
    target_format: Literal["yaml", "json"]
    confidence: Confidence
    rationale: str
    target_sha256: str


class AppendPointerPatch(_PatchBase):
    """Append a value to the list at a JSON pointer."""

    kind: Literal["append_pointer"] = "append_pointer"
    target_file: str
    pointer: str
    value: Any
    target_format: Literal["yaml", "json"]
    confidence: Confidence
    rationale: str
    target_sha256: str


class RemovePointerPatch(_PatchBase):
    """Remove the node at a JSON pointer."""

    kind: Literal["remove_pointer"] = "remove_pointer"
    target_file: str
    pointer: str
    target_format: Literal["yaml", "json"]
    confidence: Confidence
    rationale: str
    target_sha256: str


class ManualPatch(_PatchBase):
    """No machine-applicable change. Carries human-readable instructions.

    Used for every finding whose check ID has no v0.6 non-manual generator
    and for findings (like trace flips, per C6) that are intentionally
    never auto-patched.
    """

    kind: Literal["manual"] = "manual"
    instructions: str


Patch = Annotated[
    SetPointerPatch | AppendPointerPatch | RemovePointerPatch | ManualPatch,
    Field(discriminator="kind"),
]
