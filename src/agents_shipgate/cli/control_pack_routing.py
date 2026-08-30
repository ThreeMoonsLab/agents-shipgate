"""The route that reconciles a requested control pack with the one on disk.

``init --write`` refuses to overwrite an existing manifest, and then hands the
caller onward. When the invocation asked for a ``--control-pack`` that manifest
does not select, every onward route reads a manifest that loads fine and
advances under the pack that is *there* — so the request is dropped and nothing
says so. This module answers the two questions that route has to settle, once,
for both callers that reach it: ``init`` on its own workspace, and the scoped
candidate commands, where the manifest belongs to a project the root run
refused to adopt.

**Who owns the change is not decided by who typed the flag.** The first
version of this route published a coding-agent ``edit`` on the grounds that the
value came from the command line rather than from inference. That is wrong for
the case that matters: a *governed* coding agent composes its own argv, so
process arguments are not authenticated human provenance, and
``init --write --control-pack read-only-agent`` over a ``financial-strict``
manifest is a policy weakening an agent can request for itself. The direction
decides the owner instead:

* a transition that requires **at least** as much of every effect is
  strengthening, and is a coding-agent edit — following it can only tighten the
  gate the agent is judged by;
* a transition that drops any obligation is a human review, naming the effects
  and the controls it would remove — the same direction ``verify_policy``
  raises ``control_pack_weakened`` for;
* a pack this build cannot resolve is a human review too, because "cannot
  compare" is not "nothing changed".

The comparison itself is :func:`weakened_pack_obligations`, shared with that
check rather than restated here.
"""

from __future__ import annotations

from pathlib import Path

from agents_shipgate.core.control_packs import (
    BUILTIN_CONTROL_PACKS,
    weakened_pack_obligations,
)
from agents_shipgate.schemas.agent_control_envelope import MAX_ENVELOPE_PROSE_BYTES
from agents_shipgate.schemas.diagnostics import NextAction

# Effects named in a weakening route before it says how many it is not naming.
# The same shape ``verify_policy``'s recommendation uses, and for the same
# reason: one changed line can drop obligations across nine effects, and a
# route is read on one screen.
_NAMED_WEAKENED_EFFECTS = 3


def _weakening_why(
    *,
    shown: str,
    on_disk: str,
    requested: str,
    removed: list[tuple[str, list[str]]],
) -> str:
    """The weakening route's sentence, ordered so the cap drops the right end.

    The envelope caps ``why`` at :data:`MAX_ENVELOPE_PROSE_BYTES`, and the
    first draft put the effect list before the reason — so on a repository with
    a deep path the clause that says *why a person owns this* was the clause
    that got cut, leaving a route that looked like a formatting quirk. The
    reason leads; the effects are fitted to what is left and say how many they
    are not naming, the same shape the placeholder review uses.
    """

    head = (
        f"Control pack {on_disk} -> {requested} in {shown} requires less of "
        f"{len(removed)} effect(s). Command-line arguments are not a human "
        "approval — a governed coding agent writes its own — so a person "
        "decides whether this gate is loosened."
    )
    budget = MAX_ENVELOPE_PROSE_BYTES - len(head.encode("utf-8"))
    parts: list[str] = []
    for effect, controls in removed[:_NAMED_WEAKENED_EFFECTS]:
        part = f"{effect} loses {', '.join(controls)}"
        # ``" Dropped: "`` plus the separators and the closing period.
        if len(f" Dropped: {'; '.join([*parts, part])}.".encode()) > budget:
            break
        parts.append(part)
    if not parts:
        return head
    return f"{head} Dropped: {'; '.join(parts)}."


def manifest_control_pack(manifest_bytes: bytes | None) -> str | None:
    """The control pack these manifest bytes select, or ``None``.

    ``None`` covers "no manifest", "these bytes do not decode", and "they do
    not load" alike. No answer is not a different answer: a caller cannot be
    told which pack governs a file that is not there or that the next command
    will reject, and :func:`unapplied_control_pack` refuses to call it a delta.

    One reader, because there were briefly two — one taking bytes for the
    workspace's own manifest and one taking a path for a candidate's — asking
    the same question of the same loader. Two answers to "which pack governs
    this file?" is how one of them starts saying something the other does not.
    """

    if manifest_bytes is None:
        return None
    from agents_shipgate.config.loader import load_manifest_text
    from agents_shipgate.core.control_packs import resolve_control_pack

    try:
        return resolve_control_pack(
            load_manifest_text(manifest_bytes.decode("utf-8"))
        ).id
    except Exception:  # noqa: BLE001 - any objection means "cannot say".
        return None


def manifest_control_pack_at(manifest: Path) -> str | None:
    """:func:`manifest_control_pack`, for a manifest named by path."""

    try:
        return manifest_control_pack(manifest.read_bytes())
    except OSError:
        return None


def unapplied_control_pack(*, requested: str | None, on_disk: str | None) -> bool:
    """Whether this invocation asked for a pack the manifest does not carry.

    ``on_disk`` is ``None`` when there is no manifest, or when the bytes there
    could not be resolved to a pack. Neither is a delta this can speak to: no
    answer is not a different answer, and publishing one produces a route whose
    ``why`` reads "the manifest still selects None".

    ``requested`` is the value the caller actually asked for — ``None`` when
    the option was not given at all. A caller that passes ``--control-pack
    default`` explicitly over a ``financial-strict`` manifest **is** asking for
    a change, and a downgrade at that; collapsing that into "no request"
    because the value happens to be the default is how the one transition that
    always weakens becomes the one that is never routed.
    """

    return (
        requested is not None
        and on_disk is not None
        and on_disk != requested
    )


def control_pack_route(
    *,
    manifest: Path | str,
    requested: str,
    on_disk: str,
    display_path: str | None = None,
) -> NextAction:
    """The one route for a requested pack the manifest does not select.

    ``manifest`` is the file to open; ``display_path`` is what the prose calls
    it, for a candidate route that names a project-relative path while the
    action points at the exact file.
    """

    path = str(manifest)
    shown = display_path or path
    removed = weakened_pack_obligations(on_disk, requested)
    unresolvable = [
        pack_id
        for pack_id in (on_disk, requested)
        if pack_id not in BUILTIN_CONTROL_PACKS
    ]

    if unresolvable:
        return NextAction(
            kind="review",
            why=(
                f"{shown} selects control pack {on_disk} and this run asked for "
                f"{requested}. This build cannot resolve "
                f"{', '.join(sorted(set(unresolvable)))}, so whether the change "
                "would weaken the gate cannot be established either way. A "
                "person decides this one."
            ),
            expects=(
                f"policies.control_pack in {shown} reviewed and set by a person."
            ),
        )

    if removed:
        return NextAction(
            kind="review",
            why=_weakening_why(shown=shown, on_disk=on_disk, requested=requested, removed=removed),
            expects=(
                f"policies.control_pack in {shown} reviewed by a person, and "
                "either left at "
                f"{on_disk} or deliberately changed to {requested}."
            ),
        )

    return NextAction(
        kind="edit",
        path=path,
        why=(
            f"{shown} already exists and was left unchanged, so the "
            f"--control-pack {requested} this run asked for was not applied: "
            f"the manifest still selects {on_disk}. Every effect keeps at "
            "least the controls it has today, so this only tightens the gate."
        ),
        expects=f"policies.control_pack in {shown} is {requested}.",
    )


__all__ = [
    "control_pack_route",
    "manifest_control_pack",
    "manifest_control_pack_at",
    "unapplied_control_pack",
]
