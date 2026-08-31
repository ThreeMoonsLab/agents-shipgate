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
#: the CLI, the step summary, and ``report.md`` cannot word it three ways.
DETERMINISM_BOUNDARY_REFERENCE = (
    "Coverage boundary: what a scan can establish per input and declaration "
    f"shape — {DETERMINISM_BOUNDARY_URL}"
)
