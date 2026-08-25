"""The co-requirement rules a reviewed authority declaration has to satisfy.

Authority is declarable in two places (#410 increment 3): on one
``action_surface.actions[]`` row, and once on the ``tool_sources[]`` entry every
action of that source inherits. The *rules* are identical — a ``scoped`` grant
without scopes is unfillable wherever it is written — so they are stated once
here rather than twice, once per site.

Written as a plain function over values rather than as a mixin or a shared base
model because the two sites disagree about where ``scopes`` lives: an action row
carries it as a sibling field (``action_surface.actions[].scopes``, kept there so
the manifest has one canonical permission list per action), while a source block
carries it inside the authority mapping. Only the *label* differs; the rule does
not, so the label is a parameter.
"""

from __future__ import annotations

from collections.abc import Sequence

#: The reviewed modes. Deliberately excludes ``unknown``, which is a resolver
#: state — the answer "I do not know" is a blank, not a declaration.
AUTHORITY_MODE_VALUES: tuple[str, ...] = ("none", "scoped", "unscoped", "ambient")


def validate_authority_scopes(scopes: Sequence[str], *, label: str) -> list[str]:
    """Normalize a declared scope list, rejecting blanks.

    A scope that is empty or all whitespace names no permission. Accepting it
    would let ``mode: scoped`` satisfy its "non-empty scopes" co-requirement
    with a list that grants nothing, which reads as a reviewed answer and is
    not one.
    """

    normalized: list[str] = []
    for scope in scopes:
        value = scope.strip()
        if not value:
            raise ValueError(f"{label} must contain concrete, non-blank scope strings")
        normalized.append(value)
    return normalized


def validate_authority_co_requirements(
    *,
    mode: str,
    auth_type: str | None,
    scopes: Sequence[str],
    reason: str | None,
    mode_label: str,
) -> None:
    """Raise when a declared mode is missing what that mode requires.

    ``mode_label`` names the field being judged (``tool_sources[].authority.mode``
    …) so the message points at the line the reviewer has to edit. The rules:

    * ``none`` — no credential at all, so no ``auth_type`` and no scopes;
    * ``scoped`` — an ``auth_type`` and a concrete, non-empty scope list;
    * ``unscoped`` — an ``auth_type`` and a ``reason``, and no scopes (a scope
      list would contradict the claim that the grant is unscoped);
    * ``ambient`` — a ``reason``, and no scopes.
    """

    normalized_auth_type = (auth_type or "").strip()
    normalized_reason = (reason or "").strip()
    has_scopes = bool(list(scopes))

    if mode == "none":
        if has_scopes:
            raise ValueError(f"{mode_label} 'none' requires empty scopes")
        if normalized_auth_type:
            raise ValueError(f"{mode_label} 'none' requires no auth_type")
    elif mode == "scoped":
        if not normalized_auth_type:
            raise ValueError(f"{mode_label} 'scoped' requires auth_type")
        if not has_scopes:
            raise ValueError(f"{mode_label} 'scoped' requires non-empty scopes")
    elif mode == "unscoped":
        if not normalized_auth_type:
            raise ValueError(f"{mode_label} 'unscoped' requires auth_type")
        if has_scopes:
            raise ValueError(f"{mode_label} 'unscoped' requires empty scopes")
        if not normalized_reason:
            raise ValueError(f"{mode_label} 'unscoped' requires reason")
    elif mode == "ambient":
        if has_scopes:
            raise ValueError(f"{mode_label} 'ambient' requires empty scopes")
        if not normalized_reason:
            raise ValueError(f"{mode_label} 'ambient' requires reason")


__all__ = [
    "AUTHORITY_MODE_VALUES",
    "validate_authority_co_requirements",
    "validate_authority_scopes",
]
