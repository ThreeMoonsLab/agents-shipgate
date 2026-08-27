"""The tool-source block ``init`` writes when discovery read nothing.

Both manifest renderers reach this point — ``template.render_auto_manifest``
for the default auto mode, ``artifacts.render_manifest_template`` for
``--minimal`` — and both used to spell the fallback out separately, with the
same defect in each: a ``type`` the tool chose out of nine possibilities with
no evidence for any of them, and no mark saying so.

That is what #441 reported. A FastMCP Python server got

.. code-block:: yaml

    tool_sources:
      - id: CHANGE_ME
        type: openapi
        path: CHANGE_ME.yaml

whose two ``CHANGE_ME`` fields appeared in ``placeholders[]`` while ``type``
appeared in neither ``placeholders[]`` nor any comment. Filling in the two
flagged blanks yielded a schema-valid manifest declaring an OpenAPI spec that
does not exist in the repository — "the manifest validates but downstream
consumers see meaningless defaults", which is precisely what the quickstart
warns adopters about.

One module, so the block and the provenance that travels with it are written
once. It imports only from ``schemas``, which is what keeps it importable from
both renderers without a cycle.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Literal

from agents_shipgate.schemas.manifest import (
    MANIFEST_PLACEHOLDER_VALUE,
    builtin_tool_source_types_text,
)

#: Where a rendered manifest's tool surface came from.
#:
#: ``detected`` — every source in it was read out of the workspace.
#: ``scaffold`` — nothing was: the schema requires at least one source block,
#: and the manifest carries a placeholder one for a coding agent or a person to
#: complete. The distinction is the renderer's to make and nobody else's, which
#: is why it travels with the text: ``init`` used to write the scaffold with
#: ``type: openapi`` filled in and report it exactly as it reports a detected
#: manifest, so a reader had no way to tell an inference from a guess (#441).
ToolSurfaceOrigin = Literal["detected", "scaffold"]


@dataclass(frozen=True)
class RenderedManifest:
    """A rendered ``shipgate.yaml`` and the provenance of its tool surface."""

    text: str
    tool_surface_origin: ToolSurfaceOrigin

    @property
    def is_scaffold(self) -> bool:
        return self.tool_surface_origin == "scaffold"


#: What ``init`` says about a scaffolded tool surface, wherever it says it:
#: the manifest comment, ``manifest_message``, and ``control.reason``. One
#: sentence, so the YAML a reader opens and the payload an agent parses cannot
#: describe the same block differently.
#:
#: Deliberately one sentence. ``control.reason`` is capped at
#: ``MAX_ENVELOPE_PROSE_BYTES`` and shares that budget with a path, so a longer
#: statement is a statement that gets truncated away on exactly the repositories
#: with deep paths. The detail that does not fit goes in
#: :data:`SCAFFOLD_DETAIL`, which only the manifest comment carries — the
#: artifact has no byte budget.
SCAFFOLD_SUMMARY = (
    "Discovery found no tool surface here, so the tool_sources block is a "
    "scaffold, not an inference: every value in it is a placeholder."
)

#: The evidence behind :data:`SCAFFOLD_SUMMARY`, for the one surface that can
#: afford it.
SCAFFOLD_DETAIL = (
    "No framework import, tool export, or API spec was read in this workspace."
)


def scaffold_tool_sources_block() -> list[str]:
    """The ``tool_sources`` block for a workspace discovery could not read.

    The schema requires at least one of ``tool_sources`` / ``openai_api`` /
    ``anthropic`` / ``google_adk`` / ``langchain`` / ``crewai``, so a manifest
    has to carry *something*. What it must not carry is a value the tool chose
    and did not flag. ``id`` and ``path`` were flagged; ``type`` was the one
    field a reader had no reason to question.

    ``path`` loses its ``.yaml`` suffix for the same reason — the extension
    belonged to the guessed type, and an MCP export is JSON.

    The accepted values come from ``builtin_tool_source_types_text`` — the one
    renderer of that list — so a new built-in adapter cannot leave this comment
    describing a set the loader no longer accepts.
    """

    placeholder = MANIFEST_PLACEHOLDER_VALUE
    accepted = builtin_tool_source_types_text()
    comment = textwrap.wrap(f"{SCAFFOLD_SUMMARY} {SCAFFOLD_DETAIL}", width=74)
    comment.extend(
        textwrap.wrap(
            f"type: one of {accepted}. It is {placeholder} because discovery "
            "had no evidence for any of them, not because none of them "
            "applies.",
            width=72,
            initial_indent="  ",
            subsequent_indent="        ",
        )
    )
    return [
        *(f"# {line}" for line in comment),
        "tool_sources:",
        f"  - id: {placeholder}",
        f"    type: {placeholder}",
        f"    path: {placeholder}",
    ]
