"""The tool-source block ``init`` writes when it has no source to declare.

Both manifest renderers reach this point — ``template.render_auto_manifest``
for the default auto mode, ``artifacts.render_manifest_template`` for
``--minimal`` — and both used to spell the fallback out separately, with the
same defect in each: a ``type`` the tool chose out of the built-in set with no
evidence for any of them, and no mark saying so.

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

**The two renderers may not say the same thing about why.** Auto mode runs full
discovery — Python AST framework detection, artifact globs, the suggested-source
parse probe — so it can say nothing was found. ``--minimal`` runs none of that:
it probes MCP/OpenAPI/Conductor exports and simple OpenAI API artifacts and
stops. Telling a LangChain project that "no framework import was read" is a
claim ``--minimal`` never made the observation to support, so each renderer
carries its own summary and the manifest, ``manifest_message``, and
``control.reason`` all quote the one that belongs to the mode that ran.
"""

from __future__ import annotations

import textwrap
from typing import Literal

from agents_shipgate.schemas.manifest import (
    MANIFEST_PLACEHOLDER_VALUE,
    builtin_tool_source_types_text,
)

#: Where a rendered manifest's tool surface came from.
#:
#: ``detected`` — every source in it was read out of the workspace.
#: ``scaffold`` — none was: the schema requires at least one source block, and
#: the manifest carries a placeholder one for a coding agent or a person to
#: complete. The distinction is the renderer's to make and nobody else's, which
#: is why it travels with the text: ``init`` used to write the scaffold with
#: ``type: openapi`` filled in and report it exactly as it reports a detected
#: manifest, so a reader had no way to tell an inference from a guess (#441).
ToolSurfaceOrigin = Literal["detected", "scaffold"]


class RenderedManifest(str):
    """The rendered ``shipgate.yaml``, and the provenance of its tool surface.

    A ``str`` subclass rather than a wrapper, because both renderers are
    published API — this package's own docstring names
    ``render_manifest_template`` among the imports it keeps stable across
    releases — and ``yaml.safe_load(render_manifest_template(ws))``,
    ``.splitlines()``, and ``path.write_text(...)`` all predate this field.
    Returning a dataclass kept every in-tree call site green (each was updated
    to ``.text``) while breaking every caller outside the tree, which is the
    shape of break that stays invisible until it reaches somebody else's code.

    ``text`` is kept as an explicit accessor for the call sites that want to
    say which half they mean.
    """

    tool_surface_origin: ToolSurfaceOrigin
    #: The one sentence this render's mode is entitled to say about a scaffold,
    #: or ``None`` when the tool surface was detected. Carried rather than
    #: looked up, because the renderer is the only thing that knows both that
    #: the block is a scaffold and what it actually inspected.
    scaffold_summary: str | None

    def __new__(
        cls,
        text: str,
        *,
        tool_surface_origin: ToolSurfaceOrigin,
        scaffold_summary: str | None = None,
    ) -> RenderedManifest:
        if (tool_surface_origin == "scaffold") != (scaffold_summary is not None):
            raise ValueError(
                "a scaffolded render carries the summary its mode is entitled "
                "to say, and a detected one carries none: "
                f"tool_surface_origin={tool_surface_origin!r} with "
                f"scaffold_summary={scaffold_summary!r}"
            )
        instance = super().__new__(cls, text)
        instance.tool_surface_origin = tool_surface_origin
        instance.scaffold_summary = scaffold_summary
        return instance

    @property
    def text(self) -> str:
        """The manifest as a plain ``str``."""

        return str(self)


#: What **auto mode** says about a scaffolded tool surface, wherever it says it:
#: the manifest comment, ``manifest_message``, and ``control.reason``. One
#: sentence, so the YAML a reader opens and the payload an agent parses cannot
#: describe the same block differently.
#:
#: Deliberately one sentence. ``control.reason`` is capped at
#: ``MAX_ENVELOPE_PROSE_BYTES`` and shares that budget with a path, so a longer
#: statement is a statement that gets truncated away on exactly the repositories
#: with deep paths. The detail that does not fit goes in the matching
#: ``*_DETAIL``, which only the manifest comment carries — the artifact has no
#: byte budget.
DISCOVERY_SCAFFOLD_SUMMARY = (
    "Discovery found no tool surface here, so the tool_sources block is a "
    "scaffold, not an inference: every value in it is a placeholder."
)

#: The evidence behind :data:`DISCOVERY_SCAFFOLD_SUMMARY`. Auto mode has
#: actually looked for each of these.
DISCOVERY_SCAFFOLD_DETAIL = (
    "No framework import, tool export, or API spec was read in this workspace."
)

#: What ``--minimal`` says instead. It never runs framework detection, so
#: "discovery found no tool surface" is a claim it has no observation behind:
#: on `samples/simple_langchain_agent` the canonical `detect` reports
#: ``langchain`` while this renderer scaffolds (#441 review).
MINIMAL_SCAFFOLD_SUMMARY = (
    "`--minimal` does not run framework detection, so the tool_sources block "
    "is a scaffold, not an inference: every value in it is a placeholder."
)

#: The evidence behind :data:`MINIMAL_SCAFFOLD_SUMMARY`, and the way out of it.
MINIMAL_SCAFFOLD_DETAIL = (
    "Only MCP/OpenAPI/Conductor exports and simple OpenAI API artifacts were "
    "probed, and none matched. Re-run `init` without `--minimal` to detect "
    "frameworks from source."
)


def scaffold_tool_sources_block(*, summary: str, detail: str) -> list[str]:
    """The ``tool_sources`` block for a render with no source to declare.

    The schema requires at least one source block, so a manifest has to carry
    *something*. What it must not carry is a value the tool chose and did not
    flag. ``id`` and ``path`` were flagged; ``type`` was the one field a reader
    had no reason to question.

    ``path`` loses its ``.yaml`` suffix for the same reason — the extension
    belonged to the guessed type, and an MCP export is JSON.

    ``summary``/``detail`` come from the caller because only the renderer knows
    what it inspected; see this module's docstring.

    The built-in values come from ``builtin_tool_source_types_text`` — the one
    renderer of that list — so a new built-in adapter cannot leave this comment
    describing a set the loader no longer accepts. They are named as built-ins
    rather than as *the* accepted set, because ``ToolSourceConfig.type`` is
    deliberately open to third-party adapters registered through the
    ``agents_shipgate.adapters`` entry-point group — and a repository whose
    artifacts only a custom adapter recognizes is *more* likely to land here,
    not less, since built-in discovery cannot see them (#441 review).
    """

    placeholder = MANIFEST_PLACEHOLDER_VALUE
    # `break_on_hyphens=False`: the default splits "third-party" across lines,
    # which reads as a typo in a comment and makes the phrase ungreppable.
    comment = textwrap.wrap(f"{summary} {detail}", width=74, break_on_hyphens=False)
    comment.extend(
        textwrap.wrap(
            f"type: one of the built-ins ({builtin_tool_source_types_text()}), "
            "or a source type registered by an installed third-party adapter. "
            f"It is {placeholder} because nothing here was inferred, not "
            "because none of them applies.",
            width=72,
            initial_indent="  ",
            subsequent_indent="        ",
            break_on_hyphens=False,
        )
    )
    return [
        *(f"# {line}" for line in comment),
        "tool_sources:",
        f"  - id: {placeholder}",
        f"    type: {placeholder}",
        f"    path: {placeholder}",
    ]
