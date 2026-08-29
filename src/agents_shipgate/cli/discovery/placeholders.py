"""Detect ``CHANGE_ME`` placeholders in a rendered manifest, and say who owns them.

Pulled out of ``cli/main.py`` so both ``init`` (which has always reported
placeholders in its JSON output) and the ``doctor`` diagnostic
(``SHIP-DIAG-CHANGE-ME-PLACEHOLDERS``) share one implementation.

Locations come from the **parsed YAML node tree**, not from scanning lines. The
line scanner it replaced tracked indentation, so it could only see block style:
the flow spelling ``agent: {name: bot, declared_purpose: [CHANGE_ME]}`` — which
the loader accepts and the schema validates — was reported at path ``agent``,
and ``agent`` is not a human-owned field, so ``doctor`` published an executable
edit for a declaration only a person may make. Ownership must not depend on how
someone chose to spell their YAML.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence

import yaml

from agents_shipgate.schemas.manifest import MANIFEST_PLACEHOLDER_VALUE

PLACEHOLDER_VALUE = MANIFEST_PLACEHOLDER_VALUE

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
    # Recorded runtime behaviour — approval and agent traces. Evidence of what a
    # deployed system actually did is the one thing a static tool cannot check
    # and an agent must never supply; `runtime-trace` is in `do_not_auto_assert`
    # for exactly that reason.
    "validation": "runtime-trace",
}

# Leaf field names that are human-owned wherever they appear, including inside
# blocks that are otherwise ordinary. ``owner``/``reason``/``expires`` are the
# accepted-debt record that ``baseline save`` already refuses to invent, and the
# trace lists are the per-framework spellings of the same runtime evidence
# ``validation`` carries (``google_adk.trace_samples``,
# ``openai_api.trace_samples``).
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
        # ``tool_sources[].binding``: the reviewed claim that a source's
        # published surface is the surface under review. Same closed-world
        # claim the ``agent_bindings`` block carries, stated from the source
        # side, and `agent_binding` is in `do_not_auto_assert` for it (#432).
        "binding",
        "effect",
        "safeguards",
        "confirmation",
        "idempotency",
        "trace_samples",
        "approval_traces",
        "agent_traces",
        "evidence",
    }
)

# The union, matched against *every* segment of the reported path rather than
# just its leaf: a placeholder anywhere below a human-owned block is human-owned.
HUMAN_OWNED_PLACEHOLDER_FIELDS = frozenset(
    HUMAN_OWNED_PLACEHOLDER_LEAVES | set(HUMAN_OWNED_MANIFEST_BLOCKS)
)


def placeholder_owner(path: str) -> str:
    """``"human"`` when this placeholder's value must come from a person.

    Everything else is ``"coding_agent"``: a missing tool-source path or a
    project name is ordinary repository reading, and routing it to a human
    stops a turn for work the agent owns.

    Sequence indices are stripped before matching, so ``agent.declared_purpose``
    and ``agent.declared_purpose[0]`` classify alike.
    """

    return (
        "human"
        if any(segment in HUMAN_OWNED_PLACEHOLDER_FIELDS for segment in _segments(path))
        else "coding_agent"
    )


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
    """Every ``CHANGE_ME`` in ``template``, with its field path and 1-indexed line.

    Paths are built from the parsed document, so they name real fields whatever
    spelling the author used: block, flow, or a mix. A sequence element is
    reported as ``<field>[<index>]`` rather than by its own text — the previous
    ``agent.declared_purpose.CHANGE_ME`` named the *value* as if it were a field,
    which read as an ordinary key to any rule matching on names.

    An unparseable document falls back to the line scan. That path only matters
    for text the loader is going to reject anyway — ``doctor`` raises
    ``SHIP-DIAG-INVALID-MANIFEST``, whose blocking route outranks any placeholder
    obligation — but reporting nothing at all would be worse than reporting
    approximate locations.
    """

    try:
        root = yaml.compose(template)
    except yaml.YAMLError:
        return _scan_lines(template)
    if root is None:
        return []
    return list(_walk(root, path=""))


def _walk(node: yaml.Node, *, path: str) -> Iterator[dict[str, object]]:
    if isinstance(node, yaml.ScalarNode):
        if PLACEHOLDER_VALUE in str(node.value):
            yield {
                "path": path or "<root>",
                "current": str(node.value),
                "line": node.start_mark.line + 1,
            }
        return
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            name = str(key.value) if isinstance(key, yaml.ScalarNode) else "?"
            # A placeholder used as a *key* is still a placeholder, and it sits
            # under the same parent, so it inherits the parent's ownership.
            if isinstance(key, yaml.ScalarNode) and PLACEHOLDER_VALUE in name:
                yield {
                    "path": path or "<root>",
                    "current": name,
                    "line": key.start_mark.line + 1,
                }
            yield from _walk(value, path=f"{path}.{name}" if path else name)
        return
    if isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            yield from _walk(item, path=f"{path}[{index}]")


def _segments(path: str) -> list[str]:
    """Field names in ``path``, with sequence indices removed."""

    segments: list[str] = []
    for raw in str(path).split("."):
        name = raw.split("[", 1)[0].strip()
        if name:
            segments.append(name)
    return segments


def _scan_lines(template: str) -> list[dict[str, object]]:
    """Indentation-based fallback for a document YAML cannot parse.

    Block style only, and approximate: it is the previous implementation, kept
    for the one case where there is no tree to walk.
    """

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
        if stripped.endswith(":") and PLACEHOLDER_VALUE not in stripped:
            section_path.append(stripped[:-1])
            last_indent = indent
            continue
        if PLACEHOLDER_VALUE in line:
            key = stripped.split(":", 1)[0].lstrip("- ").strip()
            placeholders.append(
                {
                    "path": ".".join([*section_path, key] if key else section_path) or "<root>",
                    "current": PLACEHOLDER_VALUE,
                    "line": index,
                }
            )
    return placeholders


#: A value the template actually wrote: the sentinel alone, or the sentinel as
#: a filename with an extension (``path: CHANGE_ME.yaml``), optionally under a
#: directory the author already filled in. Deliberately stricter than
#: :func:`collect_placeholders`, which reports any value *containing* the
#: sentinel because listing one too many is harmless — a routing decision is
#: not: ``CHANGE_ME-tools.json`` is a legal filename, and a manifest that is
#: fully filled in and merely points at a missing file was being told it still
#: held template placeholders (#329 review 3). Where the two disagree the
#: reader gets the generic route, which says something true.
_PLACEHOLDER_TOKEN = re.compile(
    rf"(?:^|[/\\]){re.escape(PLACEHOLDER_VALUE)}(?:\.[A-Za-z0-9]+)?$"
)


def manifest_placeholder_fields(manifest_text: str) -> list[str]:
    """The manifest fields still holding a ``CHANGE_ME``, as field paths.

    Typed state for the recovery router, which used to decide by searching the
    whole exception text for the sentinel. The placeholder is a property of the
    manifest, so this reads it from the manifest — and reads it exactly.
    """

    return [
        str(entry["path"])
        for entry in collect_placeholders(manifest_text)
        if _PLACEHOLDER_TOKEN.search(str(entry.get("current") or ""))
    ]
