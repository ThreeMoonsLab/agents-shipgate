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

``declare_action`` (report v0.39) is the one kind that does not hang off a
finding. It answers a declaration question — an ``evidence_gaps[]`` row the
scan could fill from its own evidence — and is published on that row rather
than on ``findings[].patches`` (#410 §D).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.common import Confidence

SuggestedPatchKind = Literal[
    "manual",
    "remove_pointer",
    "append_pointer",
    "set_pointer",
    "declare_action",
    "none",
]

#: The keys of a ``declaration_template`` that *identify* the action rather
#: than declare anything about it. One spelling, shared by the generator that
#: splits a template into :class:`DeclareActionPatch` and by anything that has
#: to ask which half of a row is the subject.
#:
#: Kept here beside the patch that consumes it rather than beside the template
#: that produces it: the split is the patch's contract — ``selector`` names the
#: row, ``declaration`` is what gets written into it — and a second list of
#: these names is how the two halves start disagreeing about which is which.
ACTION_SELECTOR_KEYS: tuple[str, ...] = (
    "tool",
    "tool_id",
    "source_id",
    "source_type",
)


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


class DeclareActionPatch(_PatchBase):
    """Write one action declaration the scan derived from its own evidence.

    Every other patch kind repairs something the manifest already says. This
    one *adds* a reviewed-shape claim to the trust root, so it is deliberately
    a distinct kind rather than a ``set_pointer`` at a computed index:

    * **It is not in the default ``--kinds``.** Every ``apply-patches`` and
      ``bootstrap`` invocation in the wild passes the default set, and a
      declaration arriving under ``append_pointer`` would start writing the
      manifest for callers who never asked for it. Answering a declaration
      question is an explicit act, and the route that proposes it
      (``next_action.kind: confirm_declarations``) spells the flag.
    * **It names its subject.** A reviewer reading the dry-run diff — or the
      PR the agent pushed — sees which action is being declared and as what,
      not a JSON pointer whose meaning depends on the list it indexes into.
    * **It refuses to overwrite.** ``declaration`` is written only into fields
      the row leaves silent. A row that already answers one of them differently
      is a human's answer, and no evidence-derived proposal may replace it —
      only a human may assert against the evidence (#410 §D).

    What may be proposed is decided upstream, by
    ``EvidenceGapAction.authorable_by``: the scan filled every blank in the
    template, from the closed effect vocabulary, never weaker than any reading
    it observed. This model carries the result; it does not re-decide it.
    """

    kind: Literal["declare_action"] = "declare_action"
    #: The manifest, **relative to** ``report.manifest_dir`` — not the absolute
    #: ``target_file`` the pointer patches carry, and the difference is
    #: deliberate.
    #:
    #: An absolute path is a fact about the machine that produced the report,
    #: and this row travels: it is embedded by the evidence packet, the SARIF
    #: file, and a cached base scan. A ``verify --base`` run scans an
    #: *archived* checkout, so the absolute form named a temporary directory
    #: that no longer exists by the time anyone reads the artifact — and one
    #: that changes every run, which moved artifact digests that are supposed
    #: to be reproducible. Relative removes the class rather than asking each
    #: consumer to remember to strip it, and it makes containment structural:
    #: ``apply-patches`` resolves it under ``manifest_dir`` and can no longer
    #: be handed a path that escapes.
    target_path: str
    #: The manifest is YAML. Kept as a field for symmetry with the pointer
    #: patches — and so a future JSON manifest widens one Literal rather than
    #: growing a second patch kind.
    target_format: Literal["yaml"] = "yaml"
    #: The ``action_surface.actions`` row this answers for, as
    #: ``declaration_template`` names it. ``tool`` is always present.
    selector: dict[str, Any]
    #: The fields to write — ``effect``, and ``risk_tags`` where no single
    #: effect covers every reading. Never a selector key.
    declaration: dict[str, Any]
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
    SetPointerPatch
    | AppendPointerPatch
    | RemovePointerPatch
    | DeclareActionPatch
    | ManualPatch,
    Field(discriminator="kind"),
]
