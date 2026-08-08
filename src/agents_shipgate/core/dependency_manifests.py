"""Canonical dependency-manifest path set.

One list, so a rule that wants to say "a dependency actually changed here"
cannot quietly disagree with the next consumer that asks the same question.
The trigger catalog's ``TRIGGER-FRAMEWORK-VERSION-BUMP`` projects this set
into ``docs/triggers.json``; the public-surface contract test pins the two
against each other so an addition here without a catalog update fails CI.

Scope is deliberately the ecosystems the catalog's framework tokens live in
— Python (``openai-agents``, ``langchain``, ``crewai``, ``google-adk``),
Node (the JS ports of the same SDKs), and the JVM (``conductor-oss``).
Adding a token for a new ecosystem means adding its manifests here too.

Membership test: is this file where a dependency *version* is declared or
locked? Declaration inputs count (``requirements.in`` is where a pip-tools
bump is authored, even though the pin lands in ``requirements.txt``), and
so do lockfiles. A file that merely *mentions* a package — a README, a
Dockerfile ``pip install`` line, a sample import — does not: a rule reading
this set is asserting that a dependency changed, and those files cannot
support that claim.
"""

from __future__ import annotations

from agents_shipgate.core.globbing import glob_match

# Python. Both halves of the pip-tools pair are here on purpose: `.in` is
# where the bump is authored and `.txt` is where it is compiled to, and a PR
# may carry either or both.
_PYTHON_MANIFEST_GLOBS: tuple[str, ...] = (
    "**/pyproject.toml",
    "**/setup.py",
    "**/setup.cfg",
    "**/requirements*.txt",
    "**/requirements*.in",
    "**/requirements/*.txt",
    "**/requirements/*.in",
    "**/constraints*.txt",
    "**/constraints*.in",
    "**/Pipfile",
    "**/Pipfile.lock",
    "**/poetry.lock",
    "**/uv.lock",
    "**/pdm.lock",
    # PEP 751 permits both `pylock.toml` and `pylock.<name>.toml`.
    "**/pylock.toml",
    "**/pylock.*.toml",
    "**/environment.yml",
    "**/environment.yaml",
    "**/conda-lock.yml",
    "**/conda-lock.yaml",
)

_NODE_MANIFEST_GLOBS: tuple[str, ...] = (
    "**/package.json",
    "**/package-lock.json",
    "**/npm-shrinkwrap.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/bun.lock",
    "**/bun.lockb",
)

_JVM_MANIFEST_GLOBS: tuple[str, ...] = (
    "**/pom.xml",
    "**/build.gradle",
    "**/build.gradle.kts",
    "**/gradle/libs.versions.toml",
)

DEPENDENCY_MANIFEST_GLOBS: tuple[str, ...] = (
    *_PYTHON_MANIFEST_GLOBS,
    *_NODE_MANIFEST_GLOBS,
    *_JVM_MANIFEST_GLOBS,
)


def is_dependency_manifest(path: str) -> bool:
    """Return whether ``path`` is a file that declares or locks a dependency."""

    normalized = path.replace("\\", "/").removeprefix("./")
    return any(glob_match(pattern, normalized) for pattern in DEPENDENCY_MANIFEST_GLOBS)
