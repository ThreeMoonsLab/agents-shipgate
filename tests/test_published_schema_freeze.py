"""A published schema document keeps its bytes forever (#1044).

A ``docs/*-schema*.json`` file that a release tag carries is published: its
``$id`` is a raw URL consumers fetch and pin by digest. ``scripts/
generate_schemas.py`` holds the whole mechanism -- the pin manifest
(``scripts/published_schema_pins.json``), the published set derived from the
release tags, and the generator's refusal to rewrite a published file. These
tests run it against this tree, and show each guard failing on the defect it
exists for.

The release tags are required, not optional: a freeze that skipped without
them would skip in exactly the runs that must enforce it (PR #413 review 3).
CI's ``suite`` and ``test`` jobs check out with ``fetch-tags: true``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agents_shipgate import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_schemas.py"

#: The documents whose ``tool.version`` default was the running package
#: version, so that every release rewrote them under an unchanged schema
#: version (#847, #907).
_VERSION_STAMPED = (
    "agent-handoff-schema.v9.json",
    "agent-result-schema.v3.json",
    "agent-boundary-result-schema.v3.json",
)

#: The two documents #1088 changed after v1.2.0 published them, and whose
#: current bytes the pin manifest records as a deliberate re-freeze.
_REFROZEN_AFTER_1_2_0 = {
    "agent-boundary-result-schema.v3.json",
    "verifier-schema.v0.21.json",
}


def _load_generator():
    """Import the generator without putting ``scripts/`` on ``sys.path``."""

    spec = importlib.util.spec_from_file_location(
        "agents_shipgate_freeze_generator", GENERATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _load_generator()


@pytest.fixture(scope="module")
def release_tags(generator):
    tags = generator.read_release_tags()
    assert tags is not None, (
        "No release tag can be read, so the published set cannot be derived. "
        "Fetch them (`git fetch --tags`; in CI, `fetch-tags: true` on the "
        "checkout) rather than skipping the freeze."
    )
    return tags


@pytest.fixture(scope="module")
def released(release_tags) -> str:
    """The newest released version, so the controls below hold during release prep too."""

    return release_tags.names[-1][1:]


@pytest.fixture(scope="module")
def generated(generator) -> dict[str, str]:
    """Every generator output at this tree's package version, by basename."""

    return {target.name: content for target, content in (b() for _, b in generator.BUILDERS)}


@pytest.fixture()
def docs_copy(generator, tmp_path) -> Path:
    """The schema documents, as links into this tree, to break one at a time."""

    docs = tmp_path / "docs"
    docs.mkdir()
    for path in (REPO_ROOT / "docs").glob(generator.SCHEMA_DOCUMENT_GLOB):
        (docs / path.name).symlink_to(path)
    return docs


def _kinds(violations) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for violation in violations:
        found.setdefault(violation.name, set()).add(violation.kind)
    return found


# --- This tree --------------------------------------------------------------


def test_every_published_schema_document_keeps_its_bytes(generator, release_tags):
    violations = generator.freeze_violations(tags=release_tags, version=__version__)

    assert not violations, "\n".join(violation.message for violation in violations)


def test_the_published_set_is_every_schema_document_a_release_tag_carries(
    generator, release_tags
):
    """Derived from the tags, not from a hand list; every one is pinned."""

    pins = generator.load_schema_pins()
    on_disk = {path.name for path in (REPO_ROOT / "docs").glob("*-schema*.json")}

    assert set(release_tags.schema_blobs) <= on_disk
    assert set(release_tags.schema_blobs) <= set(pins.sha256)
    assert set(_VERSION_STAMPED) <= set(release_tags.schema_blobs)
    # The two hand tables this replaces covered 33 of these files.
    assert len(release_tags.schema_blobs) >= 177


# --- Each guard fails on its defect -----------------------------------------


def test_a_changed_published_file_fails(generator, release_tags, released, docs_copy):
    target = docs_copy / "agent-handoff-schema.v9.json"
    text = target.read_text(encoding="utf-8")
    target.unlink()
    # The exact rewrite a release bump used to make.
    assert '"default": "1.2.0"' in text
    target.write_text(text.replace('"default": "1.2.0"', '"default": "1.3.0"'), encoding="utf-8")

    violations = generator.freeze_violations(docs=docs_copy, tags=release_tags, version=released)

    assert _kinds(violations) == {
        "agent-handoff-schema.v9.json": {"changed", "differs_from_release"}
    }
    assert "successor schema version" in violations[0].message


def test_a_published_file_without_a_pin_fails(generator, release_tags, released, docs_copy):
    pins = generator.load_schema_pins()
    del pins.sha256["report-schema.v0.43.json"]

    violations = generator.freeze_violations(
        docs=docs_copy, pins=pins, tags=release_tags, version=released
    )

    assert _kinds(violations) == {"report-schema.v0.43.json": {"unpinned"}}
    assert "--pin" in violations[0].message


def test_a_deleted_published_file_fails(generator, release_tags, released, docs_copy):
    (docs_copy / "verifier-schema.v0.16.json").unlink()

    violations = generator.freeze_violations(docs=docs_copy, tags=release_tags, version=released)

    assert _kinds(violations) == {"verifier-schema.v0.16.json": {"missing"}}


def test_bytes_that_differ_from_a_release_need_a_recorded_refreeze(
    generator, release_tags, released, docs_copy
):
    """Re-pinning changed bytes is not enough: the release's bytes are the truth.

    Without the manifest's record of #1088, exactly the two files that change
    touched fail, so every other published file still has the bytes the newest
    release carrying it published. Once a release after 1.2.0 publishes both as
    they are, the record is history and removing it changes nothing.
    """

    pins = generator.load_schema_pins()
    live = {name for name, tag in pins.refrozen.items() if release_tags.newest(name)[0] == tag}

    violations = generator.freeze_violations(
        docs=docs_copy, pins=pins._replace(refrozen={}), tags=release_tags, version=released
    )

    assert _kinds(violations) == {name: {"differs_from_release"} for name in live}
    if release_tags.names[-1] == "v1.2.0":
        assert live == _REFROZEN_AFTER_1_2_0


def test_while_the_version_has_no_tag_every_schema_document_must_be_pinned(
    generator, release_tags, released, docs_copy
):
    """Release preparation: the tag will publish every schema document on disk."""

    (docs_copy / "brand-new-schema.v1.json").write_text("{}\n", encoding="utf-8")
    pins = generator.load_schema_pins()
    unpinned_on_disk = {
        path.name for path in docs_copy.glob("*-schema*.json") if path.name not in pins.sha256
    }
    assert "brand-new-schema.v1.json" in unpinned_on_disk

    tagged = generator.freeze_violations(docs=docs_copy, tags=release_tags, version=released)
    pending = generator.freeze_violations(docs=docs_copy, tags=release_tags, version="99.0.0")

    assert release_tags.pending(released) is None
    assert not tagged
    assert _kinds(pending) == {name: {"unpinned"} for name in unpinned_on_disk}
    assert all("about to be published at v99.0.0" in violation.message for violation in pending)


# --- The generator never rewrites a published file --------------------------


def _with_handoff_builder(generator, *, bumped: bool):
    """Two builders: the published agent-handoff v9 and the unpublished checks.json.

    The v9 builder emits the committed bytes, or them with the rewrite every
    release bump used to make, so the controls outlive v9's own builder.
    """

    name = "agent-handoff-schema.v9.json"
    committed = (REPO_ROOT / "docs" / name).read_text(encoding="utf-8")
    assert '"default": "1.2.0"' in committed
    content = committed.replace('"default": "1.2.0"', '"default": "1.3.0"') if bumped else committed

    def build() -> tuple[Path, str]:
        return generator.DOCS / name, content

    return (("checks_catalog", dict(generator.BUILDERS)["checks_catalog"]), ("agent_handoff", build))


def test_the_generator_writes_nothing_when_a_published_file_would_change(
    generator, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(generator, "DOCS", tmp_path)
    monkeypatch.setattr(generator, "BUILDERS", _with_handoff_builder(generator, bumped=True))

    assert generator.main([]) == 1
    err = capsys.readouterr().err

    assert "docs/agent-handoff-schema.v9.json: published at v" in err
    assert "successor schema version" in err
    assert "constraints/dev.txt" in err
    assert "Nothing was written." in err
    # Not even the unpublished checks.json, which an unrefused run writes.
    assert list(tmp_path.iterdir()) == []

    assert generator.main(["--check"]) == 1
    assert "docs/agent-handoff-schema.v9.json: published at v" in capsys.readouterr().err


def test_the_generator_writes_when_no_published_file_would_change(
    generator, monkeypatch, tmp_path
):
    """The positive control: the same run without the change writes both files."""

    monkeypatch.setattr(generator, "DOCS", tmp_path)
    monkeypatch.setattr(generator, "BUILDERS", _with_handoff_builder(generator, bumped=False))

    assert generator.main([]) == 0

    written = (tmp_path / "agent-handoff-schema.v9.json").read_bytes()
    assert (tmp_path / "checks.json").is_file()
    assert hashlib.sha256(written).hexdigest() == (
        generator.load_schema_pins().sha256["agent-handoff-schema.v9.json"]
    )


def test_without_release_tags_the_pins_still_refuse_a_rewrite(
    generator, monkeypatch, tmp_path, capsys
):
    """A shallow clone falls back to the pin manifest instead of failing closed."""

    monkeypatch.setattr(generator, "read_release_tags", lambda repo_root=None: None)
    monkeypatch.setattr(generator, "DOCS", tmp_path)
    monkeypatch.setattr(generator, "BUILDERS", _with_handoff_builder(generator, bumped=True))

    assert generator.main([]) == 1
    err = capsys.readouterr().err

    assert "no release tag can be read here" in err
    assert (
        "docs/agent-handoff-schema.v9.json: pinned in scripts/published_schema_pins.json" in err
    )
    assert list(tmp_path.iterdir()) == []


def test_pin_adds_the_missing_pin_and_never_moves_one(generator, monkeypatch, tmp_path, capsys):
    original = generator.PINS_PATH.read_bytes()
    manifest = tmp_path / "published_schema_pins.json"
    payload = json.loads(original)
    del payload["sha256"]["report-schema.v0.43.json"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(generator, "PINS_PATH", manifest)
    monkeypatch.setattr(generator, "BUILDERS", ())

    assert generator.main(["--pin"]) == 0
    assert "Pinned docs/report-schema.v0.43.json" in capsys.readouterr().out
    # The seeded manifest is exactly what --pin derives.
    assert manifest.read_bytes() == original

    payload["sha256"]["verifier-schema.v0.16.json"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    before = manifest.read_bytes()

    assert generator.main(["--pin"]) == 1
    err = capsys.readouterr().err
    assert "docs/verifier-schema.v0.16.json: published at v" in err
    assert "Nothing was pinned." in err
    assert manifest.read_bytes() == before


# --- A package version bump leaves every schema document byte-identical -----

_BUMP_PROBE = """
import importlib.util, json, sys

import agents_shipgate

agents_shipgate.__version__ = sys.argv[2]
spec = importlib.util.spec_from_file_location("bumped_generator", sys.argv[1])
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)

from agents_shipgate.schemas.agent_handoff import AgentHandoffTool
from agents_shipgate.schemas.agent_result_v1 import AgentResultTool

outputs = [builder() for _name, builder in generator.BUILDERS]
refusals = generator._frozen_rewrites(
    outputs, pins=generator.load_schema_pins(), tags=generator.read_release_tags()
)
print(json.dumps({
    "runtime": [AgentHandoffTool().version, AgentResultTool().version],
    "outputs": {target.name: content for target, content in outputs},
    "refusals": sorted(refusals),
}))
"""


def test_a_version_bump_leaves_every_schema_document_byte_identical(generator, generated):
    """Simulate the release bump: the same generator under another version.

    Before #1044 this changed the three version-stamped documents, so every
    release-prep commit rewrote published bytes under unchanged versions.
    """

    path = [str(REPO_ROOT / "src"), *filter(None, [os.environ.get("PYTHONPATH")])]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(path)}
    completed = subprocess.run(
        [sys.executable, "-c", _BUMP_PROBE, str(GENERATOR_PATH), "9.9.9"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    bumped = json.loads(completed.stdout)

    # The bump took effect: the runtime default still follows the running
    # version, and a generated file that does record it moved.
    assert bumped["runtime"] == ["9.9.9", "9.9.9"]
    assert bumped["outputs"]["determinism-boundary.json"] != generated["determinism-boundary.json"]

    schemas = {name for name in generated if generator.is_schema_document(name)}
    assert {name for name in bumped["outputs"] if generator.is_schema_document(name)} == schemas
    changed = sorted(name for name in schemas if bumped["outputs"][name] != generated[name])
    assert changed == []
    assert bumped["refusals"] == []


def test_no_unpublished_schema_carries_a_release_specific_default(
    generator, release_tags, generated
):
    """A successor schema version must not inherit the frozen version default.

    The three version-stamped documents keep the ``"default": "1.2.0"`` they
    were published with. Any schema document not yet published that carries
    a release version as a default is the #1044 defect starting again.
    """

    releases = {tag[1:] for tag in release_tags.names} | {__version__}
    published = set(release_tags.schema_blobs) | set(generator.load_schema_pins().sha256)

    def release_defaults(node) -> list[str]:
        if isinstance(node, dict):
            default = node.get("default")
            found = [default] if isinstance(default, str) and default in releases else []
            return found + [value for child in node.values() for value in release_defaults(child)]
        if isinstance(node, list):
            return [value for child in node for value in release_defaults(child)]
        return []

    offenders = {
        name: release_defaults(json.loads(content))
        for name, content in generated.items()
        if generator.is_schema_document(name) and name not in published
    }

    assert {name: found for name, found in offenders.items() if found} == {}
    for name in set(_VERSION_STAMPED) & set(generated):
        assert release_defaults(json.loads(generated[name])) == ["1.2.0"]
