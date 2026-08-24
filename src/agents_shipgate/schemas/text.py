"""Text primitives shared by the schema layer and the projections above it.

``has_visible_content`` started in ``core.evidence_actions`` because the first
value that needed it was a rendered gap path. Manifest validation needs the
same question answered — an ``override`` reason made only of U+200B passes
``str.strip()`` and renders as nothing to the reviewer it exists for — and the
schema layer must not import from ``core``. It lives here, and
``core.evidence_actions`` re-exports it so its callers are unchanged.
"""

from __future__ import annotations

import unicodedata

# Unicode Default_Ignorable_Code_Point, the code points that render as
# nothing. Used for *visibility*, never for rewriting: a joiner inside
# ``agents/👩‍💻.yaml`` or a Persian identifier's ZWNJ is load-bearing,
# so it stays in the value and only an all-invisible string is rejected.
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


def is_default_ignorable(char: str) -> bool:
    point = ord(char)
    return any(start <= point <= end for start, end in DEFAULT_IGNORABLE_RANGES)


def has_visible_content(value: str) -> bool:
    """True when at least one character renders as something a reader can see.

    Whitespace, controls, unassigned/surrogate/private-use code points, and
    Default_Ignorable code points (ZWSP, ZWJ, VS16, CGJ, bidi controls, …)
    all render as nothing on their own. A value made only of those names no
    surface, however long the string is.
    """

    return any(
        not char.isspace()
        and unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Co", "Cn"}
        and not is_default_ignorable(char)
        for char in value
    )


def _class_escape(point: int) -> str:
    """One character-class atom for a code point.

    BMP points use ``\\uXXXX``, which every regex engine reads the same way.
    Astral points are emitted as the literal character: ``\\u{...}`` is
    ECMA-262-with-``u``-flag only and Python's ``re`` rejects it outright, so a
    pattern using it would make the published schema unusable by the very
    validators that consume it.
    """

    return f"\\u{point:04x}" if point <= 0xFFFF else chr(point)


def _invisible_class_body() -> str:
    """Character-class body for the code points that render as nothing.

    Built from :data:`DEFAULT_IGNORABLE_RANGES` rather than hand-listed, so the
    published JSON Schema and :func:`has_visible_content` cannot drift onto two
    different ideas of "invisible".
    """

    parts = ["\\s", "\\u0000-\\u001f", "\\u007f-\\u009f"]
    for start, end in DEFAULT_IGNORABLE_RANGES:
        parts.append(
            _class_escape(start) if start == end
            else f"{_class_escape(start)}-{_class_escape(end)}"
        )
    return "".join(parts)


#: Matches (by search, not by full match) any string containing at least one
#: character a reader can see. Published on the manifest JSON Schema so an
#: editor validating live rejects the same values the CLI does.
#:
#: A deliberate approximation of :func:`has_visible_content`: surrogate,
#: private-use, and unassigned code points cannot be enumerated portably in an
#: ECMA-262 character class, so the runtime check remains the authority and is
#: strictly the stricter of the two. Everything an editor realistically sees —
#: spaces, tabs, C0/C1 controls, ZWSP, word joiner, bidi controls, variation
#: selectors — is covered by both.
VISIBLE_CONTENT_PATTERN = f"[^{_invisible_class_body()}]"
