"""Whether a human review is required to change the trust root (#410 §G).

Everything Agents Shipgate decides rests on ``shipgate.yaml``: a repository
that lets anything edit it unreviewed has a gate that can be turned off by
whatever the gate exists to constrain. The pincer the coding-agent walks hit
is the same fact from the other side — the agent must not edit the manifest,
and its new tool is invisible until someone does.

The property that resolves it is not a new ceremony. It is the one the
repository already has: **attestation is the PR review of a protected file**.
CODEOWNERS plus branch protection means a change to the manifest is a change a
named human approved, in the place review already happens, and that is what
makes an agent-authored declaration safe to accept — a human still merges it.

This module reads the first half of that, which is statically knowable, and is
deliberately silent about the second. Branch protection lives in repository
settings no file in the checkout can prove, so nothing here claims it; the
finding says what it checked and what it could not.

The matcher is a gitignore-style subset of the CODEOWNERS pattern language:
``*`` within a path segment, ``**`` across segments, ``?``, a leading ``/``
or an embedded ``/`` anchoring to the repository root, a trailing ``/``
restricting to a directory's contents, and last-rule-wins with an ownerless
last rule removing ownership. Enough for every real rule that would cover a
manifest, and it errs toward *not* finding coverage — the direction that
raises a guidance finding rather than silently claiming protection nobody has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agents_shipgate.core.agent_controls import git_root_for

#: Where GitHub looks for CODEOWNERS, in its own precedence order: a file in
#: ``.github`` wins over one at the root, which wins over one in ``docs``.
CODEOWNERS_LOCATIONS: tuple[str, ...] = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)


@dataclass(frozen=True)
class ManifestProtection:
    """What this checkout can prove about who may change the manifest."""

    #: The manifest, relative to the repository root, in posix spelling.
    manifest_path: str
    #: The CODEOWNERS file that was read, or ``None`` when there is none.
    codeowners_path: str | None
    #: True when a rule in that file assigns an owner to the manifest.
    covered: bool
    #: The rule that decided it — the last one to match. ``None`` when none did.
    matching_pattern: str | None
    #: The owners that rule names, in file order.
    owners: tuple[str, ...]

    @property
    def reviewed(self) -> bool:
        """True when changing the manifest requires a named owner's review."""

        return self.covered


def manifest_protection(config_path: Path) -> ManifestProtection:
    """Read this checkout's CODEOWNERS and say whether it covers the manifest.

    Never raises: an unreadable or absent file is reported as no coverage,
    which is what it is. A repository that is not a git checkout has no
    CODEOWNERS semantics at all and is reported the same way.
    """

    manifest = config_path.resolve()
    root = git_root_for(manifest.parent) or manifest.parent
    try:
        relative = manifest.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - resolve() keeps these under root
        relative = manifest.name

    for location in CODEOWNERS_LOCATIONS:
        candidate = root / location
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        pattern, owners = _last_matching_rule(text, relative)
        return ManifestProtection(
            manifest_path=relative,
            codeowners_path=location,
            covered=bool(owners),
            matching_pattern=pattern,
            owners=owners,
        )
    return ManifestProtection(
        manifest_path=relative,
        codeowners_path=None,
        covered=False,
        matching_pattern=None,
        owners=(),
    )


def _last_matching_rule(text: str, path: str) -> tuple[str | None, tuple[str, ...]]:
    """The last rule in ``text`` matching ``path``, with the owners it names.

    Last-wins is the CODEOWNERS rule, and it matters: a broad ``*`` at the top
    followed by a narrower ownerless rule for the manifest leaves the manifest
    unowned, which is the case a first-match reading would report backwards.
    """

    matched: tuple[str | None, tuple[str, ...]] = (None, ())
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("["):
            # ``[section]`` headers are a GitLab extension; the rules under one
            # are ordinary rules, so skipping the header alone is right.
            continue
        parts = line.split()
        pattern, owners = parts[0], tuple(parts[1:])
        if _matches(pattern, path):
            matched = (pattern, owners)
    return matched


def _matches(pattern: str, path: str) -> bool:
    """True when a CODEOWNERS ``pattern`` covers the repo-relative ``path``."""

    if pattern in {"*", "**", "/*", "/**"}:
        return True
    directory_only = pattern.endswith("/")
    core = pattern.strip("/")
    if not core:
        return False
    # A pattern with no separator matches at any depth, exactly as gitignore
    # does: ``shipgate.yaml`` covers ``apps/api/shipgate.yaml``.
    anchored = "/" in pattern.rstrip("/")
    body = _translate(core)
    prefix = "" if anchored else r"(?:.*/)?"
    tail = "/.*" if directory_only else "(?:/.*)?"
    return re.fullmatch(f"{prefix}{body}{tail}", path) is not None


def _translate(core: str) -> str:
    """One CODEOWNERS path pattern as a regular expression body.

    ``**`` is the only segment that is not a segment: it stands for any number
    of them, including none, so it carries its own separator rather than
    taking the one between its neighbours. Emitting a literal ``/`` around it
    is what made ``apps/**/shipgate.yaml`` fail to match ``apps/shipgate.yaml``
    — and, worse, ``apps/api/shipgate.yaml`` too.
    """

    segments = core.split("/")
    last = len(segments) - 1
    out: list[str] = []
    for index, segment in enumerate(segments):
        if segment == "**":
            out.append(".*" if index == last else "(?:[^/]+/)*")
            continue
        out.append(_translate_segment(segment))
        if index != last:
            out.append("/")
    return "".join(out)


def _translate_segment(segment: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "*":
            if segment.startswith("**", index):
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


__all__ = [
    "CODEOWNERS_LOCATIONS",
    "ManifestProtection",
    "manifest_protection",
]
