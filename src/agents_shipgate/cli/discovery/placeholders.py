"""Detect ``CHANGE_ME`` placeholders in a rendered manifest.

Pulled out of ``cli/main.py`` so both ``init`` (which has always reported
placeholders in its JSON output) and the new ``doctor`` diagnostic
(``SHIP-DIAG-CHANGE-ME-PLACEHOLDERS``) share one implementation.

The ``init`` callers historically saw ``[{path, current}]``; the doctor
diagnostic also wants the line number to point an ``edit`` action at
``shipgate.yaml:<line>``. Both are returned in a single richer payload.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# Manifest fields whose value is a *declaration a human makes*, not a fact an
# agent can read out of the repository. Shipgate already refuses to auto-assert
# effect, authority, and binding declarations (contract `do_not_auto_assert`),
# and `baseline save --owner/--reason` already refuses invented values for the
# same reason: they are the record of a person having decided something, so a
# value an agent supplied is not merely a guess, it is a forged approval.
#
# Matched against *every* segment of the reported path, not just its leaf.
# ``collect_placeholders`` names a list item by its own text, so the manifest's
# ``agent.declared_purpose: [CHANGE_ME]`` is reported as
# ``agent.declared_purpose.CHANGE_ME`` — and a leaf-only rule read that as the
# agent's own to fill in, which is exactly the routing #325 exists to stop.
# Whole blocks belong here too: what an agent is permitted to do, and the policy
# governing it, are not the governed agent's call.
HUMAN_OWNED_PLACEHOLDER_FIELDS = frozenset(
    {
        "declared_purpose",
        "prohibited_actions",
        "owner",
        "approval_required",
        "reason",
        "policies",
        "permissions",
    }
)


def placeholder_owner(path: str) -> str:
    """``"human"`` when this placeholder's value must come from a person.

    Everything else is ``"coding_agent"``: a missing tool-source path or a
    project name is ordinary repository reading, and routing it to a human
    stops a turn for work the agent owns.
    """

    segments = [segment for segment in str(path).split(".") if segment]
    if any(segment in HUMAN_OWNED_PLACEHOLDER_FIELDS for segment in segments):
        return "human"
    return "coding_agent"


def human_owned_placeholders(
    placeholders: Sequence[Mapping[str, object]] | None,
) -> list[Mapping[str, object]]:
    """The subset of :func:`collect_placeholders` output a human must resolve."""

    return [
        entry
        for entry in (placeholders or [])
        if placeholder_owner(str(entry.get("path", ""))) == "human"
    ]


def collect_placeholders(template: str) -> list[dict[str, object]]:
    """Find ``CHANGE_ME`` markers in ``template`` and return their
    YAML-pointer-ish location, the original value, and the 1-indexed
    line number on which the placeholder appears."""
    placeholders: list[dict[str, object]] = []
    section_path: list[str] = []
    last_indent = -1
    for index, line in enumerate(template.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while section_path and last_indent >= indent:
            section_path.pop()
            last_indent -= 2
        stripped = line.strip()
        if stripped.endswith(":") and "CHANGE_ME" not in stripped:
            section_path.append(stripped[:-1])
            last_indent = indent
            continue
        if "CHANGE_ME" in line:
            key = stripped.split(":", 1)[0].lstrip("- ").strip()
            placeholders.append(
                {
                    "path": ".".join(
                        [*section_path, key] if key else section_path
                    )
                    or "<root>",
                    "current": "CHANGE_ME",
                    "line": index,
                }
            )
    return placeholders
