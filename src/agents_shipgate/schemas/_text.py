"""Character-class predicates shared by the schema and rendering layers.

``has_visible_content`` was written for the display layer in
:mod:`agents_shipgate.core.evidence_actions`, which answers "does this string
name anything a reader can open?". A reviewed manifest justification asks the
same question at the input boundary — ``str.strip()`` accepts a reason made
only of U+200B, and that reason then satisfies a requirement whose entire
purpose is for a human to read it (PR #412 review).

The predicates live here rather than in ``core`` because ``schemas`` is the
lower layer: nothing under ``schemas`` imports ``core``, and a manifest
validator reaching upward for a text helper would invert that.
``evidence_actions`` re-exports ``has_visible_content`` so its callers are
unchanged.
"""

from __future__ import annotations

import unicodedata

# Rewriting the text after them is the whole point of these, so they are the
# one class that is escaped rather than passed through: left intact, a forged
# suffix can be made to display as if it were the real target.
BIDI_CONTROLS = frozenset(
    "؜‎‏‪‫‬‭‮⁦⁧⁨⁩"
)

# Unicode Default_Ignorable_Code_Point, the code points that render as
# nothing. Used for *visibility*, never for rewriting: a joiner inside
# ``agents/👩‍💻.yaml`` or a Persian identifier's ZWNJ is load-bearing,
# so it stays in the display and only an all-invisible value is rejected.
DEFAULT_IGNORABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)

#: Control characters a human legitimately writes inside a multi-line
#: justification. Everything else in category ``Cc`` is a terminal or protocol
#: control that has no business in reviewed prose.
_ALLOWED_CONTROLS = frozenset("\n\r\t")


def is_default_ignorable(char: str) -> bool:
    point = ord(char)
    return any(start <= point <= end for start, end in DEFAULT_IGNORABLE_RANGES)


def has_visible_content(value: str) -> bool:
    """True when at least one character renders as something a reader can see.

    Whitespace, controls, unassigned/surrogate/private-use code points, and
    Default_Ignorable code points (ZWSP, ZWJ, VS16, CGJ, bidi controls, …)
    all render as nothing on their own. A "path" made only of those names no
    surface, however long the string is.
    """

    return any(
        not char.isspace()
        and unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Co", "Cn"}
        and not is_default_ignorable(char)
        for char in value
    )


def unsafe_prose_characters(value: str) -> list[str]:
    """Code points that must not survive into reviewed, rendered prose.

    Bidi controls can make text after them display as something else, and
    invisible or unassigned code points let two different manifests render
    identically to the reviewer comparing them. Newline, carriage return, and
    tab are allowed through: a justification may legitimately be several
    lines, and the renderer folds them rather than the schema forbidding them.

    Returns the offending code points as ``U+XXXX`` labels, in order of first
    appearance, so a validation error can name what to remove.
    """

    seen: set[str] = set()
    offenders: list[str] = []
    for char in value:
        if char in _ALLOWED_CONTROLS:
            continue
        category = unicodedata.category(char)
        if (
            char in BIDI_CONTROLS
            or is_default_ignorable(char)
            or category in {"Cc", "Cf", "Cs", "Co", "Cn"}
        ):
            label = f"U+{ord(char):04X}"
            if label not in seen:
                seen.add(label)
                offenders.append(label)
    return offenders


__all__ = [
    "BIDI_CONTROLS",
    "DEFAULT_IGNORABLE_RANGES",
    "has_visible_content",
    "is_default_ignorable",
    "unsafe_prose_characters",
]
