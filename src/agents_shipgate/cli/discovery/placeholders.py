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
# Manifest blocks that are *entirely* human-owned, and the contract entry each
# one carries. The contract already publishes this boundary in
# ``do_not_auto_assert``; the mapping from those names to manifest surfaces is
# spelled out here because it is not mechanical, and pinned by
# ``tests/test_setup_control.py`` so a new ``do_not_auto_assert`` entry with a
# manifest surface cannot be added without landing here too.
#
# These are reviewed closed-world claims about deployed wiring, or records of a
# person having decided something. A value a coding agent supplied is not a
# guess to be corrected later — it is a declaration nobody made, and Shipgate
# will treat it as evidence.
HUMAN_OWNED_MANIFEST_BLOCKS: dict[str, str] = {
    # Which agent is wired to which tools, and which agent is the root.
    "agent_bindings": "agent_binding",
    # Which extracted tools are one tool. Same closed-world claim, stated from
    # the tool side.
    "tool_identity": "agent_binding",
    # What each action does and on whose authority, plus the approval and
    # confirmation safeguards around it.
    "action_surface": "action_effect",
    # What the agent is permitted to do, and the policy governing it.
    "permissions": "action_authority",
    "policies": "action_authority",
    # Accepted debt and its owner: suppressions, waivers, acknowledgements.
    "checks": "suppression",
    "baseline": "baseline",
    "human_ack": "human-ack",
    # Reclassifying a risk downward is a policy-weakening decision.
    "risk_overrides": "policy-weakening",
    # Organization governance is not the governed agent's to set.
    "organization": "action_authority",
}

# Leaf field names that are human-owned wherever they appear, including inside
# blocks that are otherwise ordinary. ``owner``/``reason``/``expires`` are the
# accepted-debt record that ``baseline save`` already refuses to invent.
HUMAN_OWNED_PLACEHOLDER_LEAVES = frozenset(
    {
        "declared_purpose",
        "prohibited_actions",
        "owner",
        "reason",
        "expires",
        "approval",
        "approval_required",
        "authority",
        "effect",
        "safeguards",
        "confirmation",
        "idempotency",
    }
)

# The union, matched against *every* segment of the reported path rather than
# just its leaf. ``collect_placeholders`` names a list item by its own text, so
# the manifest's ``agent.declared_purpose: [CHANGE_ME]`` is reported as
# ``agent.declared_purpose.CHANGE_ME`` — and a leaf-only rule read that as the
# agent's own to fill in, which is exactly the routing #325 exists to stop.
HUMAN_OWNED_PLACEHOLDER_FIELDS = frozenset(
    HUMAN_OWNED_PLACEHOLDER_LEAVES | set(HUMAN_OWNED_MANIFEST_BLOCKS)
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
