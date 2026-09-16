"""Issue #638: base-cache access stays inside its Git metadata namespace.

The cache lives at ``<git metadata>/agents-shipgate/base-scans/<key>/``. Git
chooses the metadata directory (a worktree's own Git dir, a linked ``.git``);
everything below it is the cache's lexical namespace. A link or other
non-directory planted at any namespace component must make the cache
unavailable for that run: the base is regenerated from Git, nothing is read or
written through the link, and verify says which path to repair.

The end-to-end tests drive ``verify`` through the CLI and depend on no private
helper, so they run unchanged against a tree without the fix.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest
from test_nonregular_evidence_readers import _run_bounded
from test_verify_weakening import _weakened_repo
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify import orchestrator

CONFIG = "samples/support_refund_agent/shipgate.yaml"
SAMPLE = Path("samples/clean_read_only_agent")
NAMESPACE = ("agents-shipgate", "base-scans")

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="settled links, FIFOs and modes are exercised on POSIX"
)
needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
not_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory modes"
)


@pytest.fixture
def pinned_umask():
    """Creation modes are masked by the process umask; pin it so they are exact."""

    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _base_scans(monkeypatch) -> list[Path]:
    calls: list[Path] = []
    original = orchestrator.run_scan

    def observe(*args, **kwargs):
        config = kwargs["config_path"]
        if any(
            parent.name == "base" and parent.parent.name.startswith("agents-shipgate-verify-")
            for parent in config.parents
        ):
            calls.append(config)
        return original(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "run_scan", observe)
    return calls


def _verify(workspace: Path, *, external: Path | None = None, before=None) -> dict:
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--workspace",
            str(workspace),
            "--config",
            CONFIG,
            "--base",
            "HEAD~1",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )
    if external is not None:
        # Checked before the exit code, so a failure names the escape itself.
        assert _tree_state(external) == before, "files outside the cache namespace changed"
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _policy_kinds(workspace: Path) -> set[str]:
    report = json.loads((workspace / "agents-shipgate-reports/report.json").read_text())
    return {
        finding["evidence"].get("kind")
        for finding in report["findings"]
        if finding["check_id"] == "SHIP-VERIFY-POLICY-WEAKENED"
    }


def _tree_state(root: Path) -> dict[str, tuple]:
    """Every entry below ``root`` by kind, inode, and bytes, without following links."""

    state: dict[str, tuple] = {".": ("dir", root.lstat().st_ino)}
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in sorted([*directories, *files]):
            path = Path(directory) / name
            metadata = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(metadata.st_mode):
                state[relative] = ("link", os.readlink(path))
            elif stat.S_ISDIR(metadata.st_mode):
                state[relative] = ("dir", metadata.st_ino)
            else:
                state[relative] = (
                    "file",
                    metadata.st_ino,
                    metadata.st_nlink,
                    metadata.st_mtime_ns,
                    path.read_bytes(),
                )
    return state


def _metadata(repo: Path) -> Path:
    return (repo / ".git").resolve()


def _seed_foreign_entries(directory: Path, count: int) -> None:
    """Old, cache-shaped user data a prune through the link would delete."""

    for index in range(count):
        entry = directory / f"foreign-{index:02d}"
        entry.mkdir(parents=True)
        (entry / "report.json").write_text(f"user data {index}\n")
        os.utime(entry, (index + 1, index + 1))


def _only_entry(repo: Path) -> Path:
    (entry,) = _metadata(repo).joinpath(*NAMESPACE).iterdir()
    return entry


# --- end to end: a linked namespace component ---------------------------------

LINKED = [
    "entry_with_bad_checksum",
    "entry_with_valid_report",
    "base_scans_warm",
    "base_scans_cold",
    "agents_shipgate_cold",
]


@pytest.mark.parametrize(
    ("boundary", "linked"),
    [
        *(("descriptor", linked) for linked in LINKED),
        # The pathname fallback used where descriptor operations are missing.
        ("lexical", "entry_with_bad_checksum"),
        ("lexical", "base_scans_cold"),
    ],
)
def test_linked_cache_component_is_never_read_or_written_through(
    tmp_path, monkeypatch, boundary, linked
):
    if boundary == "lexical":
        monkeypatch.setattr(
            orchestrator, "_DESCRIPTOR_RELATIVE_BASE_CACHE", False, raising=False
        )
    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    scans = _base_scans(monkeypatch)
    namespace = _metadata(repo) / "agents-shipgate"
    external = tmp_path / "external"
    external.mkdir()

    if linked.startswith("entry") or linked == "base_scans_warm":
        _verify(repo)
        assert len(scans) == 1
        entry = _only_entry(repo)
        if linked == "base_scans_warm":
            link = namespace / "base-scans"
            target = external / "base-scans"
        else:
            link = entry
            target = external / "entry"
        link.rename(target)
        link.symlink_to(target, target_is_directory=True)
        if linked == "entry_with_bad_checksum":
            (target / "report.sha256").write_text("0" * 64 + "\n")
    else:
        if linked == "base_scans_cold":
            namespace.mkdir()
            link = namespace / "base-scans"
        else:
            link = namespace
        target = external / "cache"
        _seed_foreign_entries(target, orchestrator.BASE_CACHE_KEEP_ENTRIES)
        link.symlink_to(target, target_is_directory=True)

    before = _tree_state(external)
    scans_before = len(scans)

    payload = _verify(repo, external=external, before=before)

    # Nothing outside the namespace was created, replaced, or pruned ...
    assert link.is_symlink()
    # ... and the base comparison still ran, from Git, and found the weakening.
    assert payload["base_status"] == "succeeded", payload["base_notes"]
    assert "ci_mode_weakened" in _policy_kinds(repo)
    assert len(scans) == scans_before + 1, "a linked cache must not be reused"
    notes = " ".join(payload["base_notes"])
    assert "Base-scan cache unavailable" in notes, notes
    assert f"{link} is a symbolic link" in notes
    assert "rerun verification" in notes

    # A second run neither heals the link by writing through it nor reuses it.
    payload = _verify(repo, external=external, before=before)
    assert payload["base_status"] == "succeeded"
    assert len(scans) == scans_before + 2


@pytest.mark.parametrize("leaf", ["report.json", "report.sha256", "capabilities.lock.json"])
def test_linked_cache_file_is_replaced_without_touching_its_target(tmp_path, monkeypatch, leaf):
    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    scans = _base_scans(monkeypatch)
    _verify(repo)
    entry = _only_entry(repo)
    external = tmp_path / "external"
    external.mkdir()
    target = external / leaf
    target.write_bytes((entry / leaf).read_bytes())
    (entry / leaf).unlink()
    (entry / leaf).symlink_to(target)
    if leaf == "capabilities.lock.json":
        # A linked report or checksum is refused on read and regenerates by
        # itself; the lock is only republished when the report is.
        (entry / "report.sha256").unlink()
    before = _tree_state(external)

    payload = _verify(repo, external=external, before=before)

    assert payload["base_status"] == "succeeded"
    assert len(scans) == 2
    assert not (entry / leaf).is_symlink()
    assert (entry / leaf).is_file()
    _verify(repo)
    assert len(scans) == 2, "the repaired entry is reused"


def test_hard_linked_cache_report_is_an_alias_not_a_hit(tmp_path, monkeypatch):
    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    scans = _base_scans(monkeypatch)
    _verify(repo)
    report = _only_entry(repo) / "report.json"
    external = tmp_path / "external"
    external.mkdir()
    alias = external / "report.json"
    os.link(report, alias)
    before = _tree_state(external)

    payload = _verify(repo)

    assert payload["base_status"] == "succeeded"
    assert len(scans) == 2, "a hard-linked report is not reused"
    assert report.lstat().st_nlink == 1
    # The alias keeps its bytes; only its link count dropped when the cache
    # entry was replaced beside it.
    after = _tree_state(external)
    assert after["report.json"][4] == before["report.json"][4]
    assert after["report.json"][1] == before["report.json"][1]
    _verify(repo)
    assert len(scans) == 2


# --- end to end: wrong kinds and unreadable parents ------------------------------


@pytest.mark.parametrize("component", ["agents-shipgate", "base-scans", "entry"])
def test_non_directory_namespace_component_makes_the_cache_unavailable(
    tmp_path, monkeypatch, component
):
    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    scans = _base_scans(monkeypatch)
    metadata = _metadata(repo)
    if component == "entry":
        _verify(repo)
        blocked = _only_entry(repo)
        for child in blocked.iterdir():
            child.unlink()
        blocked.rmdir()
    elif component == "base-scans":
        (metadata / "agents-shipgate").mkdir()
        blocked = metadata / "agents-shipgate" / "base-scans"
    else:
        blocked = metadata / "agents-shipgate"
    blocked.write_text("not a cache directory\n")
    scans_before = len(scans)

    payload = _verify(repo)

    assert payload["base_status"] == "succeeded", payload["base_notes"]
    assert len(scans) == scans_before + 1
    assert blocked.read_text() == "not a cache directory\n"
    notes = " ".join(payload["base_notes"])
    assert f"Base-scan cache unavailable: {blocked} is not a directory" in notes, notes


@not_root
def test_unreadable_namespace_parent_makes_the_cache_unavailable(tmp_path, monkeypatch):
    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    scans = _base_scans(monkeypatch)
    _verify(repo)
    base_scans = _metadata(repo).joinpath(*NAMESPACE)
    before = {path.name: path.read_bytes() for path in _only_entry(repo).iterdir()}
    base_scans.chmod(0)
    try:
        payload = _verify(repo)
    finally:
        base_scans.chmod(0o755)

    assert payload["base_status"] == "succeeded", payload["base_notes"]
    assert len(scans) == 2
    notes = " ".join(payload["base_notes"])
    assert f"Base-scan cache unavailable: {base_scans} could not be opened" in notes, notes
    assert {path.name: path.read_bytes() for path in _only_entry(repo).iterdir()} == before
    _verify(repo)
    assert len(scans) == 2, "the entry is reused once the directory is readable again"


_FIFO_CHILD = """
    import json, sys, time
    from pathlib import Path
    from agents_shipgate.cli.verify.orchestrator import _base_cache_directory
    from agents_shipgate.core.verification_identity import UnsafeDirectoryComponent

    started = time.monotonic()
    outcomes = []
    for create in (False, True):
        try:
            with _base_cache_directory(
                Path(sys.argv[1]), ("agents-shipgate", "base-scans", "entry"), create=create
            ):
                outcomes.append("opened")
        except UnsafeDirectoryComponent as exc:
            outcomes.append(exc.reason)
    print(json.dumps({"outcomes": outcomes, "elapsed": time.monotonic() - started}))
"""


@needs_fifo
@pytest.mark.parametrize("component", ["agents-shipgate", "agents-shipgate/base-scans"])
def test_fifo_namespace_component_is_refused_promptly(tmp_path, component):
    if "/" in component:
        (tmp_path / "agents-shipgate").mkdir()
    os.mkfifo(tmp_path / component)

    outcome = _run_bounded(_FIFO_CHILD, str(tmp_path))

    assert outcome["outcomes"] == ["is not a directory", "is not a directory"]
    assert stat.S_ISFIFO((tmp_path / component).lstat().st_mode)


# --- the boundary itself -------------------------------------------------------


@pytest.mark.parametrize("boundary", ["descriptor", "lexical"])
def test_prune_never_traverses_a_linked_entry_or_namespace(tmp_path, monkeypatch, boundary):
    if boundary == "lexical":
        monkeypatch.setattr(orchestrator, "_DESCRIPTOR_RELATIVE_BASE_CACHE", False)
    anchor = tmp_path / "metadata"
    base_scans = anchor.joinpath(*NAMESPACE)
    base_scans.mkdir(parents=True)
    external = tmp_path / "external"
    _seed_foreign_entries(external / "linked-entry", 2)
    for index, name in enumerate(("old", "new", "newest"), start=10):
        (base_scans / name).mkdir()
        (base_scans / name / "report.json").write_text(name)
        os.utime(base_scans / name, (index, index))
    # The oldest name of all, so a prune that counted it would delete through it.
    (base_scans / "aaa-linked").symlink_to(external / "linked-entry", target_is_directory=True)
    before = _tree_state(external)

    orchestrator._prune_base_scan_cache(anchor, keep=1)

    assert sorted(path.name for path in base_scans.iterdir()) == ["aaa-linked", "newest"]
    assert _tree_state(external) == before

    # A linked namespace directory is not pruned at all.
    moved = tmp_path / "external-base-scans"
    base_scans.rename(moved)
    base_scans.symlink_to(moved, target_is_directory=True)
    for index in range(3):
        (moved / f"extra-{index}").mkdir()
        os.utime(moved / f"extra-{index}", (index + 1, index + 1))
    before = _tree_state(moved)

    orchestrator._prune_base_scan_cache(anchor, keep=1)

    assert _tree_state(moved) == before


@pytest.mark.parametrize("boundary", ["descriptor", "lexical"])
def test_boundary_publishes_reads_and_refuses_inside_one_directory(
    tmp_path, monkeypatch, boundary, pinned_umask
):
    if boundary == "lexical":
        monkeypatch.setattr(orchestrator, "_DESCRIPTOR_RELATIVE_BASE_CACHE", False)
    anchor = tmp_path / "metadata"
    anchor.mkdir()
    components = (*NAMESPACE, "entry")

    with pytest.raises(FileNotFoundError):
        with orchestrator._base_cache_directory(anchor, components, create=False):
            pass
    with orchestrator._base_cache_directory(anchor, components, create=True) as cache:
        cache.replace("report.json", b"payload", temporary_prefix="report-", mode=0o644)
        cache.replace("report.json", b"payload-2", temporary_prefix="report-", mode=0o644)
        cache.replace("owner.json", b"secret", temporary_prefix="owner-", mode=0o600)
        assert cache.read("report.json", max_bytes=64, label="probe") == b"payload-2"
        assert cache.present("report.json")
        assert not cache.present("report.sha256")
        with pytest.raises(ValueError):
            cache.read("report.json", max_bytes=3, label="probe")
    entry = anchor.joinpath(*components)
    assert sorted(path.name for path in entry.iterdir()) == ["owner.json", "report.json"]
    # The mode a publication asks for is the mode it gets, on both boundaries.
    assert stat.S_IMODE((entry / "report.json").lstat().st_mode) == 0o644
    assert stat.S_IMODE((entry / "owner.json").lstat().st_mode) == 0o600
    (entry / "owner.json").unlink()

    (entry / "linked.json").symlink_to(entry / "report.json")
    with orchestrator._base_cache_directory(anchor, components, create=False) as cache:
        with pytest.raises((OSError, ValueError)):
            cache.read("linked.json", max_bytes=64, label="probe")

    external = tmp_path / "external"
    external.mkdir()
    entry.rename(external / "entry")
    entry.symlink_to(external / "entry", target_is_directory=True)
    before = _tree_state(external)
    for create in (False, True):
        with pytest.raises(orchestrator.UnsafeDirectoryComponent) as refused:
            with orchestrator._base_cache_directory(anchor, components, create=create):
                pass
        assert refused.value.path == entry
        assert refused.value.reason == "is a symbolic link"
    assert _tree_state(external) == before


# --- legitimate Git layouts keep caching ----------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_warm_hit_answers_exactly_like_the_cold_run(tmp_path, monkeypatch):
    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    scans = _base_scans(monkeypatch)
    out = repo / "agents-shipgate-reports"

    cold = _verify(repo)
    cold_base = (out / "verification-base-report.json").read_bytes()
    warm = _verify(repo)

    assert len(scans) == 1
    assert (out / "verification-base-report.json").read_bytes() == cold_base
    for field in ("request_id", "input_set_id", "decision_id", "base_tree_sha", "merge_verdict"):
        assert warm[field] == cold[field], field
    entry = _only_entry(repo)
    assert sorted(path.name for path in entry.iterdir()) == [
        "capabilities.lock.json",
        "report.json",
        "report.sha256",
    ]
    digest = hashlib.sha256((entry / "report.json").read_bytes()).hexdigest()
    assert (entry / "report.sha256").read_text() == f"{digest}\n"


def test_linked_worktree_caches_under_its_own_git_dir(tmp_path, monkeypatch):
    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    worktree = tmp_path / "linked-worktree"
    _git(repo, "worktree", "add", "--detach", str(worktree), "HEAD")
    git_dir = Path(_git(worktree, "rev-parse", "--absolute-git-dir")).resolve()
    common_dir = (worktree / _git(worktree, "rev-parse", "--git-common-dir")).resolve()
    assert git_dir != common_dir
    scans = _base_scans(monkeypatch)

    first = _verify(worktree)
    second = _verify(worktree)

    assert first["base_status"] == second["base_status"] == "succeeded"
    assert len(scans) == 1, "the warm run reuses the worktree's cache entry"
    (report,) = git_dir.joinpath(*NAMESPACE).glob("*/report.json")
    assert report.is_file()
    assert not (common_dir / "agents-shipgate").exists()
    assert "ci_mode_weakened" in _policy_kinds(worktree)


def test_git_selected_metadata_directory_may_itself_be_a_link(tmp_path, monkeypatch):
    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    moved = tmp_path / "relocated-git-dir"
    (repo / ".git").rename(moved)
    (repo / ".git").symlink_to(moved, target_is_directory=True)
    scans = _base_scans(monkeypatch)

    first = _verify(repo)
    second = _verify(repo)

    assert first["base_status"] == second["base_status"] == "succeeded"
    assert len(scans) == 1
    (report,) = moved.resolve().joinpath(*NAMESPACE).glob("*/report.json")
    assert report.is_file()


# --- published modes ------------------------------------------------------------


@pytest.mark.parametrize("boundary", ["descriptor", "lexical"])
def test_published_cache_files_keep_their_modes(
    tmp_path, monkeypatch, boundary, pinned_umask
):
    """The checksum and the lock are owner-only; the report is an ordinary file."""

    if boundary == "lexical":
        monkeypatch.setattr(orchestrator, "_DESCRIPTOR_RELATIVE_BASE_CACHE", False)
    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)

    _verify(repo)

    entry = _only_entry(repo)
    assert {
        path.name: stat.S_IMODE(path.lstat().st_mode) for path in entry.iterdir()
    } == {
        "report.json": 0o644,
        "report.sha256": 0o600,
        "capabilities.lock.json": 0o600,
    }


# --- a namespace component replaced *during* the run ------------------------------
#
# The settled-link guarantee above is about a link already in place when a cache
# directory is opened. These are about the other half: the validated bytes are
# kept for the run, so a component swapped after validation — or after a cold
# store — cannot be read back. A `git worktree add` layout is covered as well as
# an ordinary checkout, because its Git directory lies outside the workspace
# snapshot and so has no second line of defence (#803).


def _workspace(tmp_path: Path, layout: str) -> Path:
    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    if layout == "ordinary":
        return repo
    worktree = tmp_path / "linked-worktree"
    _git(repo, "worktree", "add", "--detach", str(worktree), "HEAD")
    return worktree


def _base_scans_dir(workspace: Path) -> Path:
    """The cache namespace below the metadata directory Git selects."""

    git_dir = Path(_git(workspace, "rev-parse", "--absolute-git-dir")).resolve()
    return git_dir.joinpath(*NAMESPACE)


def _forged(source: Path, destination: Path) -> Path:
    """A copy of the cache whose base says the gate was always advisory.

    The checksum is recomputed, so the forgery is indistinguishable from a
    genuine entry to everything except where it was read from.
    """

    shutil.copytree(source, destination)
    for report in destination.glob("*/report.json"):
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["effective_policy"]["ci_mode"] == "strict"
        payload["effective_policy"]["ci_mode"] = "advisory"
        raw = json.dumps(payload, indent=2).encode("utf-8")
        report.write_bytes(raw)
        report.with_name("report.sha256").write_text(
            f"{hashlib.sha256(raw).hexdigest()}\n", encoding="ascii"
        )
    return destination


def _swap_after(monkeypatch, name: str, *, base_scans: Path, forged: Path, kept: Path):
    """Replace ``base-scans`` with a link to ``forged`` once, after ``name`` runs."""

    original = getattr(orchestrator, name)
    state = {"swapped": False}

    def hooked(*args, **kwargs):
        result = original(*args, **kwargs)
        if not state["swapped"]:
            base_scans.rename(kept)
            base_scans.symlink_to(forged, target_is_directory=True)
            state["swapped"] = True
        return result

    monkeypatch.setattr(orchestrator, name, hooked)
    return state


def _assert_unraced(raced: dict, control: dict, workspace: Path, forged: Path) -> None:
    out = workspace / "agents-shipgate-reports"
    assert raced["base_status"] == "succeeded", raced["base_notes"]
    assert "ci_mode_weakened" in _policy_kinds(workspace), "the forged base was consumed"
    for field in (
        "request_id",
        "input_set_id",
        "decision_id",
        "base_tree_sha",
        "merge_verdict",
    ):
        assert raced[field] == control[field], field
    published = (out / "report.json").read_text(encoding="utf-8")
    assert str(forged) not in published, "the forged tree was published"
    assert "agents-shipgate-verify-base-" not in published, "a temporary path was published"


@pytest.mark.parametrize("layout", ["ordinary", "worktree"])
def test_namespace_swapped_after_validation_is_never_consumed(
    tmp_path, monkeypatch, layout
):
    workspace = _workspace(tmp_path, layout)
    scans = _base_scans(monkeypatch)
    _verify(workspace)
    control = _verify(workspace)
    control_base = (
        workspace / "agents-shipgate-reports/verification-base-report.json"
    ).read_bytes()
    assert len(scans) == 1
    assert "ci_mode_weakened" in _policy_kinds(workspace)

    base_scans = _base_scans_dir(workspace)
    forged = _forged(base_scans, tmp_path / "forged-base-scans")
    # After the whole cache step, so validation has happened and the head scan,
    # the artifact export, gap provenance and capability review are still ahead.
    swap = _swap_after(
        monkeypatch,
        "_prepare_base_report",
        base_scans=base_scans,
        forged=forged,
        kept=tmp_path / "genuine-base-scans",
    )

    raced = _verify(workspace)

    assert swap["swapped"]
    assert len(scans) == 1, "the warm entry was still a hit"
    _assert_unraced(raced, control, workspace, forged)
    assert (
        workspace / "agents-shipgate-reports/verification-base-report.json"
    ).read_bytes() == control_base


@pytest.mark.parametrize("layout", ["ordinary", "worktree"])
def test_namespace_swapped_after_the_cold_store_is_never_consumed(
    tmp_path, monkeypatch, layout
):
    workspace = _workspace(tmp_path, layout)
    scans = _base_scans(monkeypatch)
    _verify(workspace)
    control = _verify(workspace)
    control_base = (
        workspace / "agents-shipgate-reports/verification-base-report.json"
    ).read_bytes()

    base_scans = _base_scans_dir(workspace)
    forged = _forged(base_scans, tmp_path / "forged-base-scans")
    shutil.rmtree(base_scans)  # the next run is cold and stores its own report
    # Pruning is the first thing after publication, so the swap lands between
    # the store and everything that would read the stored report back.
    swap = _swap_after(
        monkeypatch,
        "_prune_base_scan_cache",
        base_scans=base_scans,
        forged=forged,
        kept=tmp_path / "genuine-base-scans",
    )

    raced = _verify(workspace)

    assert swap["swapped"]
    assert len(scans) == 2, "the cold run rescanned the base"
    _assert_unraced(raced, control, workspace, forged)
    assert (
        workspace / "agents-shipgate-reports/verification-base-report.json"
    ).read_bytes() == control_base


# --- an unavailable cache publishes a stable reference ---------------------------


def test_an_unavailable_cache_publishes_the_same_report_every_run(tmp_path, monkeypatch):
    """No temporary directory reaches ``report.json``: two runs agree byte for byte."""

    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    _base_scans(monkeypatch)
    _verify(repo)
    base_scans = _base_scans_dir(repo)
    external = tmp_path / "external"
    base_scans.rename(external)
    base_scans.symlink_to(external, target_is_directory=True)
    out = repo / "agents-shipgate-reports"

    first = _verify(repo)
    first_bytes = (out / "report.json").read_bytes()
    second = _verify(repo)
    second_bytes = (out / "report.json").read_bytes()

    assert "Base-scan cache unavailable" in " ".join(first["base_notes"])
    assert first_bytes == second_bytes
    payload = json.loads(first_bytes)
    for surface in ("tool_surface_diff", "action_surface_diff"):
        published = payload[surface]["base"]["path"]
        assert published.endswith("report.json"), published
        assert "base-scans" in published, published
        assert str(external) not in published, published
        assert "agents-shipgate-verify-base-" not in published, published
    for field in ("request_id", "input_set_id", "decision_id"):
        assert first[field] == second[field], field


def test_a_link_planted_in_the_run_directory_is_replaced_not_followed(tmp_path, monkeypatch):
    """The run's own copy is published without following a link either (#638)."""

    repo = _weakened_repo(tmp_path, sample_dir=SAMPLE)
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"untouched\n")
    planted: list[Path] = []
    original = tempfile.TemporaryDirectory

    class PlantALink(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if str(kwargs.get("prefix", "")).endswith("-base-"):
                link = Path(self.name) / "report.json"
                link.symlink_to(victim)
                planted.append(link)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", PlantALink)

    _verify(repo)

    assert planted, "the run-scoped base directory was never created"
    assert victim.read_bytes() == b"untouched\n"
    assert "ci_mode_weakened" in _policy_kinds(repo)
