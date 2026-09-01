from __future__ import annotations

HITL_RUNTIME_CONTROL_DISCLAIMER = (
    "HITL evidence is local review evidence only. Missing local evidence "
    "does not prove a runtime control is absent, and present local evidence "
    "does not certify runtime enforcement."
)

STATIC_VERDICT_DISCLAIMER = (
    "This verdict covers deterministic static evidence only. Agents Shipgate "
    "did not execute the agent or prove runtime behavior, tool routing, "
    "credential enforcement, or safety."
)

#: The published determinism boundary: what a scan can establish per input and
#: declaration shape.
#:
#: An ``insufficient_evidence`` verdict is only actionable if its reader can
#: tell "this tool cannot analyse my repository" from "this repository declares
#: its tools in a shape nothing static can read". #396 fixed what the headline
#: says; this is where its reader goes for why (#473). The page is generated
#: from the adapter registry and regenerated in CI, so the link cannot outlive
#: the boundary it describes.
DETERMINISM_BOUNDARY_URL = (
    "https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/"
    "determinism-boundary.md"
)

#: The one line the ``insufficient_evidence`` surfaces print, spelled once so
#: the CLI, ``verify``, the step summary, and ``report.md`` cannot word it four
#: ways.
#:
#: Says what the reader gets, in their words. "Coverage boundary: what a scan
#: can establish per input and declaration shape" named the internal axes of
#: the page — a reader who does not already know what a "declaration shape" is
#: cannot tell whether the link is worth a click, and that reader is the whole
#: audience for an abstention. It mirrors the page's own title instead.
DETERMINISM_BOUNDARY_REFERENCE = (
    "What Agents Shipgate can prove, per framework: "
    f"{DETERMINISM_BOUNDARY_URL}"
)
