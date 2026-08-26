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
#: Pinned equal to the ``codeowners`` rows of
#: ``core.trust_roots.TRUST_ROOT_SURFACES`` — a location that decides protection
#: and is not a trust root can be rewritten without the change being classified.
CODEOWNERS_LOCATIONS: tuple[str, ...] = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)

#: GitHub ignores a CODEOWNERS file of 3 MB or more — it loads no rules at all
#: and requests no owner review. Parsing one anyway credited protection from a
#: file the forge never read, which is the same failure as reading a stray file
#: outside a checkout: a claim about review that no pull request will honour.
#:
#: The boundary is taken as 3 MiB and treated as *ignored at the limit*, so the
#: ambiguity in "3 MB" lands on the side that reports uncovered.
CODEOWNERS_SIZE_LIMIT_BYTES = 3 * 1024 * 1024


@dataclass(frozen=True)
class ManifestProtection:
    """What this checkout can prove about who may change the manifest."""

    #: The manifest, relative to the repository root, in posix spelling.
    manifest_path: str
    #: The CODEOWNERS file that was read, or ``None`` when there is none.
    codeowners_path: str | None
    #: True when a rule in that file assigns an owner to the manifest.
    #:
    #: Named for what it establishes and not for what it suggests. "Reviewed"
    #: would be the second half of the property — CODEOWNERS *plus* branch
    #: protection — and this checkout cannot see the second half, so a field
    #: called ``reviewed`` would claim it. Callers that want the stronger
    #: statement have to say so themselves.
    covered: bool
    #: The rule that decided it — the last one to match. ``None`` when none did.
    #: Repository-controlled text, like ``owners``: carried for a caller that
    #: needs to explain the decision, and deliberately not published in any
    #: finding, where source content has no business being.
    matching_pattern: str | None
    #: The owners that rule names, in file order.
    owners: tuple[str, ...]
    #: Whether the CODEOWNERS file assigns an owner to *itself*. Carried
    #: separately from ``covered`` so a finding can say which half is missing:
    #: "no rule covers the manifest" and "anyone can delete the rule that does"
    #: are different problems with different fixes.
    self_owned: bool = False


#: Owner forms GitHub accepts in a CODEOWNERS rule: ``@user``, ``@org/team``,
#: and an email address. Anything else is skipped by GitHub, so a rule whose
#: only token is ``not-an-owner`` assigns nobody — and reading it as ownership
#: reports a manifest as protected that is not.
_OWNER_PATTERN = re.compile(
    r"^(?:@[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:/[A-Za-z0-9._-]+)?"
    r"|[^@\s]+@[^@\s]+\.[^@\s]+)$"
)


def manifest_protection(config_path: Path) -> ManifestProtection:
    """Read this checkout's CODEOWNERS and say whether it covers the manifest.

    Never raises, and fails **closed** on every question it cannot answer: an
    unreadable file, an absent one, or a directory that is not a git checkout
    all report no coverage.

    That last one is not pedantry. CODEOWNERS is a *forge* convention: outside
    a git checkout there is no repository for a rule to govern and no pull
    request for it to gate, so a stray file of that name proves nothing. It
    would still have suppressed the guidance finding and, through the ladder,
    awarded rung 3 — a protection claim resting on a filename.
    """

    manifest = config_path.resolve()
    root = git_root_for(manifest.parent)
    if root is None:
        return ManifestProtection(
            manifest_path=manifest.name,
            codeowners_path=None,
            covered=False,
            matching_pattern=None,
            owners=(),
        )
    try:
        relative = manifest.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - resolve() keeps these under root
        relative = manifest.name

    for location in CODEOWNERS_LOCATIONS:
        candidate = root / location
        if not candidate.is_file():
            continue
        # The first one that exists is the file GitHub reads, and if it cannot
        # read it there is no fallback to the next location — so neither is
        # there one here. Falling through would credit protection from a file
        # the forge ignores.
        try:
            oversized = candidate.stat().st_size >= CODEOWNERS_SIZE_LIMIT_BYTES
        except OSError:
            oversized = True
        text = ""
        if not oversized:
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        pattern, owners = _last_matching_rule(text, relative)
        # And the file has to govern itself. A rule set that names an owner for
        # the manifest but not for CODEOWNERS leaves anyone able to delete that
        # rule in the same pull request, so the protection it describes is one
        # edit deep. `* @team` satisfies both; `/shipgate.yaml @sec` alone does
        # not, and correctly reports uncovered.
        _, self_owners = _last_matching_rule(text, location)
        return ManifestProtection(
            manifest_path=relative,
            codeowners_path=location,
            covered=bool(owners) and bool(self_owners),
            matching_pattern=pattern,
            owners=owners,
            self_owned=bool(self_owners),
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
        # Only tokens GitHub would accept as an owner. A rule with a pattern
        # and a typo assigns nobody, and last-wins means such a rule *removes*
        # ownership a broader rule granted — so counting the typo as an owner
        # gets the answer exactly backwards.
        pattern = parts[0]
        owners = tuple(token for token in parts[1:] if _OWNER_PATTERN.match(token))
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
    tail = "/.*" if directory_only else _descendant_tail(core)
    return re.fullmatch(f"{prefix}{body}{tail}", path) is not None


def _descendant_tail(core: str) -> str:
    """Whether a non-directory pattern also covers what is *under* it.

    GitHub documents ``docs/*`` as matching files directly in ``docs`` and
    **not** further-nested ones, unlike gitignore. Appending a descendant tail
    unconditionally made ``/docs/* @docs`` cover ``docs/sub/shipgate.yaml`` —
    a false positive that sets ``covered``, silences the guidance finding, and
    can award rung 3 for a review no owner was ever asked for.

    The discriminator is the last segment. A wildcard there is a *filename*
    glob and matches one path component; a literal one may name a directory, so
    its contents come with it (``apps`` covering ``apps/api/shipgate.yaml``).
    ``**`` is recursive by definition and :func:`_translate` already expands it.
    """

    last = core.rsplit("/", 1)[-1]
    if last == "**":
        return ""
    if "*" in last or "?" in last:
        return ""
    return "(?:/.*)?"


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
