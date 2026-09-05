"""Every ref and version ``init`` writes into someone else's repository resolves.

Issue #506. ``init --write --ci`` wrote ``uses: ThreeMoonsLab/agents-shipgate@v0.16.0``
into an adopter's repository for 56 days, and no such tag had ever been cut.
GitHub fails that job at action-resolution time, before any step executes, so a
first-time adopter's very first Shipgate run was a red check carrying an error
about *our* repository rather than theirs. The bundled onboarding prompt had the
same defect one layer up: ``uvx agents-shipgate@0.16.0``, a version the index
does not carry.

Two things kept it alive, and both are addressed here rather than in prose:

- The rule was written twice. Documentation surfaces tracked the latest
  published tag; the one artifact that gets *executed* by a stranger's CI
  tracked ``__version__``. ``LATEST_PUBLISHED_VERSION`` is now the single
  constant, and this module sweeps everything ``init`` emits against it.
- The test asserted the defect. ``test_init_ci.py`` required the emitted pin to
  equal ``__version__``, so the divergence was not merely unguarded — it was
  enforced.

The sweep is deliberately non-vacuous: it asserts each pin *shape* was actually
found. A template that stops interpolating its pin would otherwise turn this
file green by emitting nothing to check.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from agents_shipgate import __version__
from agents_shipgate.cli.discovery.agent_instructions import apply as apply_module
from agents_shipgate.cli.discovery.agent_instructions.apply import render_targets
from agents_shipgate.cli.discovery.agent_instructions.renderers.claude_code_skill import (
    render_files as render_claude_code_skill_files,
)
from agents_shipgate.cli.discovery.agent_instructions.targets import SPECS
from agents_shipgate.cli.discovery.ci_workflow import (
    WORKFLOW_RELATIVE_PATH,
    write_ci_workflow,
)
from agents_shipgate.published_release import (
    LATEST_PUBLISHED_CONTRACT_VERSION,
    LATEST_PUBLISHED_VERSION,
    ContractFloorProse,
    contract_floor_prose,
    latest_published_action_ref,
    published_release_meets_contract_floor,
)
from agents_shipgate.schemas.contract import MINIMUM_CONTROL_CONTRACT_VERSION
from scripts.release_cadence import is_release_tag

REPO_ROOT = Path(__file__).resolve().parent.parent

_VERSION = r"\d+\.\d+\.\d+(?:[A-Za-z]+\d*)?"

#: The four shapes an adopter's machine has to resolve, and what each one is.
#: The Action ref deliberately captures *any* ref rather than a version — the
#: failure this file exists for was a ref that did not exist, and ``@main`` or a
#: stale SHA would be the same failure wearing different syntax.
PIN_SHAPES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "action ref",
        re.compile(r"ThreeMoonsLab/agents-shipgate@([^\s\"'`)\],;{}]+)"),
        "GitHub resolves this before any step runs; a missing ref is a red check.",
    ),
    (
        "zero-install runner pin",
        # No runner prefix. `uvx` is what the prompts write today, but the
        # property under test is "this names a version the index carries", and
        # `uv tool run` or `pipx run` spell the same pin. The `@v` Action form
        # cannot collide: a digit must follow `@`. Same reasoning, same shape,
        # as `UVX_PIN_PATTERN` in `test_public_surface_contract.py`.
        re.compile(rf"(?<![\w-])agents-shipgate@({_VERSION})"),
        "The runner fetches this from the index on every invocation.",
    ),
    (
        "pip/pipx pin",
        re.compile(rf"agents-shipgate==({_VERSION})"),
        "pip resolves this against the index.",
    ),
    (
        "shipgate_version input",
        re.compile(rf"shipgate_version:\s*['\"]({_VERSION})['\"]"),
        "The Action installs this version from PyPI inside the job.",
    ),
)


#: Refs the bundled prompts print as a *blank for the reader to fill in* while
#: teaching an adopter to bump a pin. Enumerated, never pattern-matched: an
#: allowlist that guessed at "looks like a placeholder" is one bad guess away
#: from excusing `@main`. `test_the_reader_blanks_are_blanks` holds both ends —
#: every entry is visibly unfillable, and every entry is still in use.
READER_BLANK_REFS = frozenset({"v<NEW>", "v…"})


def _expected(shape: str) -> str:
    return latest_published_action_ref() if shape == "action ref" else LATEST_PUBLISHED_VERSION


def _emitted_files(tmp_path: Path) -> dict[str, str]:
    """Everything ``init`` writes into an adopter's repository, by label.

    Rendered rather than read off disk: a checked-in mirror can be hand-edited
    into agreement with this test while the renderer keeps emitting something
    else, which is precisely how the workflow and the bundled CI recipe came to
    name different releases.

    The ``--agent-instructions`` half comes from ``SPECS`` — the registry the
    CLI itself selects targets from — and goes through ``render_targets``, the
    function ``init`` renders with. A hand-written list of renderers is the
    version of this that goes stale: `AGENTS.md`, `CLAUDE.md`, the slash
    command, the Cursor rule, the PR template and the local contract carry no
    pin *today*, and a sweep that named only the two skill bundles would keep
    passing on the day one of them grew an install snippet.
    """

    workspace = tmp_path / "adopter"
    workspace.mkdir()
    result = write_ci_workflow(workspace)
    assert result.status == "written", result

    emitted = {
        WORKFLOW_RELATIVE_PATH: (workspace / WORKFLOW_RELATIVE_PATH).read_text(encoding="utf-8")
    }
    for outcome in render_targets(workspace, sorted(SPECS)):
        # The managed block or whole-file body, labelled by the target that
        # writes it; file-tree targets additionally carry their own paths.
        emitted[f"<{outcome.name}> {SPECS[outcome.name].relative_path}"] = outcome.rendered
        for entry in outcome.files or ():
            emitted[entry["path"]] = entry["content"]
    return emitted


def test_the_sweep_covers_every_agent_instruction_target(tmp_path):
    """Every registered target reaches the sweep, and the skill bundles' files with it.

    ``SPECS`` is the registry `--agent-instructions` selects from, so a target
    added there is swept without anyone remembering to add it here. This states
    the coupling rather than leaving it implicit, and fails if `render_targets`
    ever stops returning a target's rendered body.
    """

    emitted = _emitted_files(tmp_path)
    for name in SPECS:
        label = f"<{name}> {SPECS[name].relative_path}"
        assert label in emitted, f"{name} is registered but never reached the sweep"
        assert emitted[label].strip(), f"{name} rendered empty; the sweep over it is vacuous"

    # File-tree targets contribute their individual files too, not just the
    # dry-run bundle text: the bundled CI recipe is one of them, and it is a
    # file an adopter's CI executes.
    assert any(
        key.endswith("ci-recipes/advisory-pr-comment.yml") for key in emitted
    ), "the claude-code-skill file tree did not reach the sweep"


# --- the published release is real ------------------------------------------


def _git(*argv: str) -> subprocess.CompletedProcess[str]:
    """Run git against this repository, or skip the test that needed it.

    One precondition, stated once. Both checks below ask the same question of
    the same checkout, and a missing git binary must not surface as a skip from
    one and an unhandled ``FileNotFoundError`` from the other — that reports an
    environment limitation as a code failure. Note what is *not* skipped: a
    working git that answers with no tags. That is a checkout which cannot
    answer the question this module exists to ask, and returning "no violations
    found" from an empty set is the fail-open shape that let #506 ship.
    """

    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - no git binary at all
        pytest.skip(f"git is not available to read this repository: {exc}")


def _tag_names() -> list[str]:
    """Every release tag in this checkout."""

    result = _git("for-each-ref", "--format=%(refname:strip=2)", "refs/tags")
    if result.returncode != 0:  # pragma: no cover - not a git checkout
        pytest.skip(f"not a git checkout: {result.stderr.strip()}")

    tags = [name for name in result.stdout.split() if is_release_tag(name)]
    assert tags, (
        "This checkout has no release tags, so no pin can be checked against "
        "release reality. Run `git fetch --tags`; in CI, the job needs "
        "`fetch-tags: true` on actions/checkout."
    )
    return tags


def test_latest_published_version_names_a_tag_in_this_repositorys_history():
    """The constant every emitted pin derives from must be a tag that exists.

    This is the assertion whose absence cost 56 days. Note what it is *not*:
    it does not require the constant to equal the newest tag. Cutting a release
    must not be what fixes an unresolvable pin — if it were, the defect would
    return on the first commit after the tag, when the tree moves ahead again.
    """

    assert latest_published_action_ref() in _tag_names(), (
        f"{latest_published_action_ref()} is not a tag in this repository. "
        "`init` writes it into an adopter's repository, where GitHub resolves "
        "it before running anything. Set LATEST_PUBLISHED_VERSION to a release "
        "that exists; do not point it at the source tree."
    )


def test_the_published_release_constant_is_not_the_source_tree_version_by_accident():
    """A tree ahead of its newest tag must still emit the tag.

    The regression is not "these two strings differ" — they are equal on a
    release commit, legitimately. It is `LATEST_PUBLISHED_VERSION` being
    *defined* as `__version__`, which passes on a release commit and breaks on
    every commit after it.
    """

    module = REPO_ROOT / "src/agents_shipgate/published_release.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    # Code only. The module's own prose explains the trap at length, and a
    # substring check over the file would fail on the explanation.
    references = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "__version__")
        or (isinstance(node, ast.Attribute) and node.attr == "__version__")
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "__version__" for alias in node.names)
        )
    ]
    assert not references, (
        "published_release.py must not read __version__: the source tree runs "
        "ahead of the newest tag for the whole interval between releases, and "
        "binding the two is the defect in #506."
    )


def test_the_published_release_constant_matches_the_well_known_claim():
    """One published-release answer, not two.

    `.well-known`'s `release_status.latest_release` is the claim `ci.yml`'s
    `release-tag-consistency` job re-checks against *origin* on every push to
    main. Binding the package constant to it puts every emitted pin behind that
    job as well, without a second network check.
    """

    published = json.loads(
        (REPO_ROOT / ".well-known/agents-shipgate.json").read_text(encoding="utf-8")
    )
    assert published["release_status"]["latest_release"] == latest_published_action_ref()


def test_latest_published_contract_version_is_what_that_tag_actually_emits():
    """Read out of the tag, not asserted about it.

    This constant decides whether the adoption prompts claim the release they
    pin reports the contract floor they demand. Getting it too high re-creates
    the contradiction the whole exercise is about — a prompt promising that the
    build it pins is new enough when it demonstrably is not — so it is checked
    against the release's own source rather than trusted.
    """

    tag = latest_published_action_ref()
    relpath = "src/agents_shipgate/schemas/contract.py"
    result = _git("show", f"{tag}:{relpath}")
    assert result.returncode == 0, (
        f"Could not read {relpath} at {tag}: {result.stderr.strip()}\n"
        "If the file moved after that release, update the path here. If the "
        "object is missing, this checkout is too shallow — the job needs "
        "`fetch-depth: 0` rather than `fetch-tags: true` alone."
    )
    match = re.search(rf'^CONTRACT_VERSION[^=\n]*=\s*"({_VERSION}|\d+)"', result.stdout, re.M)
    assert match, f"{tag}:{relpath} declares no top-level CONTRACT_VERSION."
    assert LATEST_PUBLISHED_CONTRACT_VERSION == match.group(1), (
        f"LATEST_PUBLISHED_CONTRACT_VERSION is "
        f"{LATEST_PUBLISHED_CONTRACT_VERSION!r}, but {tag} emits "
        f"{match.group(1)!r}. Bump both published-release constants together."
    )


# --- everything init emits resolves -----------------------------------------


def test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release(tmp_path):
    violations: list[str] = []
    for relpath, text in sorted(_emitted_files(tmp_path).items()):
        for shape, pattern, why in PIN_SHAPES:
            expected = _expected(shape)
            for number, line in enumerate(text.splitlines(), start=1):
                for match in pattern.finditer(line):
                    if match.group(1) in READER_BLANK_REFS:
                        continue
                    if match.group(1) != expected:
                        violations.append(
                            f"{relpath}:{number} {shape} names {match.group(1)!r}; "
                            f"the published release is {expected!r}. {why}\n"
                            f"    line: {line.strip()!r}"
                        )
    assert not violations, "init emits pins that do not resolve:\n" + "\n".join(violations)


def test_the_pin_sweep_actually_sees_every_shape_it_checks(tmp_path):
    """A sweep that matches nothing reports no violations.

    Each shape below is emitted by a different template. If one stops
    interpolating its pin — or the file carrying it leaves the bundle — the
    sweep above goes quiet rather than red, and that silence is what this
    turns back into a failure.
    """

    emitted = _emitted_files(tmp_path)
    for shape, pattern, _ in PIN_SHAPES:
        found = {
            relpath for relpath, text in emitted.items() if pattern.search(text)
        }
        assert found, (
            f"no emitted file carries a {shape}; the sweep over it is vacuous. "
            "Either the template stopped rendering the pin, or PIN_SHAPES is "
            "out of date with what init writes."
        )


def test_the_reader_blanks_are_blanks_and_are_still_in_use(tmp_path):
    """Both ends of the one exemption in the sweep.

    ``upgrade-shipgate-version.md`` shows an adopter the line to edit, so it
    prints a ref with the version left out. That is the only reason a
    non-published ref may appear at all, and it holds only while the ref is
    visibly a blank — a bracket or an ellipsis, which no `uses:` line resolves.
    The second half deletes the exemption when the prose that needed it goes:
    an entry nothing emits any more is a hole waiting for a real ref.
    """

    emitted = "\n".join(_emitted_files(tmp_path).values())
    for ref in sorted(READER_BLANK_REFS):
        assert re.search(r"[<>…]", ref), (
            f"{ref!r} is not a visible blank, so exempting it from the pin "
            "sweep exempts a ref something could try to resolve."
        )
        assert f"ThreeMoonsLab/agents-shipgate@{ref}" in emitted, (
            f"nothing emits {ref!r} any more; drop it from READER_BLANK_REFS "
            "rather than leaving an unused exemption in the sweep."
        )


def test_the_sweep_catches_an_unpublished_pin(tmp_path, monkeypatch):
    """The negative control: the exact defect, re-introduced, must fail.

    `AGENTS_SHIPGATE_WORKFLOW_REF` is the documented override, which makes it
    the cheapest way to put an unresolvable ref into the emitted workflow and
    prove the sweep is load-bearing rather than decorative.
    """

    monkeypatch.setenv("AGENTS_SHIPGATE_WORKFLOW_REF", f"v{__version__}.does-not-exist")
    with pytest.raises(AssertionError, match="do not resolve"):
        test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release(tmp_path)


def test_the_sweep_catches_an_unpublished_pin_in_a_managed_block(tmp_path, monkeypatch):
    """The second negative control, for the half that carries no pin today.

    `AGENTS.md`, `CLAUDE.md`, the slash command, the Cursor rule, the PR
    template and the local contract are all written into an adopter's
    repository and none of them names a version right now — which is exactly
    why a sweep that skipped them would look correct indefinitely and fail on
    the day one of them grew an install snippet. Injecting a pin into one
    proves the coverage is real rather than nominal.
    """

    original = apply_module.render_agents_md
    monkeypatch.setattr(
        apply_module,
        "render_agents_md",
        lambda: original() + "\n    uses: ThreeMoonsLab/agents-shipgate@v9.9.9\n",
    )
    with pytest.raises(AssertionError, match="do not resolve"):
        test_every_pin_init_writes_into_an_adopter_repo_names_the_published_release(tmp_path)


def test_the_emitted_workflow_pins_the_release_and_not_the_source_tree(tmp_path):
    workspace = tmp_path / "adopter"
    workspace.mkdir()
    write_ci_workflow(workspace)
    content = (workspace / WORKFLOW_RELATIVE_PATH).read_text(encoding="utf-8")

    assert f"ThreeMoonsLab/agents-shipgate@{latest_published_action_ref()}" in content
    if __version__ != LATEST_PUBLISHED_VERSION:
        assert f"agents-shipgate@v{__version__}" not in content


# --- the floor the prompts demand, stated honestly ---------------------------


def test_the_contract_floor_prose_states_the_gap_when_there_is_one():
    """A published release older than the floor is said out loud, not papered over.

    The alternative the issue rejects on principle is a pin that silently
    degrades — `__version__` when its tag exists, the published tag otherwise —
    because then two adopters get different artifacts depending on the day they
    ran `init`, and nothing tells either of them which they got.
    """

    gap = contract_floor_prose(str(int(LATEST_PUBLISHED_CONTRACT_VERSION) + 1))
    assert not gap.satisfied
    assert "No published release reports that contract yet" in gap.notice
    assert LATEST_PUBLISHED_VERSION in gap.notice
    assert LATEST_PUBLISHED_CONTRACT_VERSION in gap.notice
    # It must not promise the pinned build satisfies the floor.
    assert "or newer" not in gap.source

    met = contract_floor_prose(LATEST_PUBLISHED_CONTRACT_VERSION)
    assert met.satisfied
    assert f"`{LATEST_PUBLISHED_VERSION}` or newer" == met.source
    assert "No published release" not in met.notice


def test_an_unreadable_contract_floor_is_treated_as_unmet():
    """"I cannot tell" renders as the gap, never as a promise."""

    assert not published_release_meets_contract_floor("not-a-number")
    assert not contract_floor_prose("not-a-number").satisfied


def test_the_shipped_floor_is_decided_against_the_release_the_prompts_pin():
    """The two constants the prompts render from must be compared, not assumed.

    Today they disagree — `v0.15.0` predates the control contract entirely, so
    its `contract --json` carries no floor field at all — and the prompts say
    so. This asserts the comparison is live: whichever way it resolves, the
    rendered prose is the one that matches.
    """

    prose = contract_floor_prose(MINIMUM_CONTROL_CONTRACT_VERSION)
    assert isinstance(prose, ContractFloorProse)
    assert prose.satisfied is published_release_meets_contract_floor(
        MINIMUM_CONTROL_CONTRACT_VERSION
    )

    rendered = render_claude_code_skill_files()[
        ".claude/skills/agents-shipgate/prompts/add-shipgate-to-repo.md"
    ]
    assert prose.notice in rendered
    assert "{{" not in rendered, "an uninterpolated placeholder reached the adopter"
