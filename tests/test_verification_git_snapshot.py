from __future__ import annotations

import os
import subprocess
import zlib
from pathlib import Path

import pytest

from agents_shipgate.cli.verify.git import (
    archive_tree,
    archive_verification_tree,
    diff_revspec_context,
    repository_identity,
    working_tree_context,
)
from agents_shipgate.core.errors import ConfigError


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _git_output(root: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_text,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    return root


def test_exact_snapshot_does_not_honor_export_ignore(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text("secret.yaml export-ignore\n", encoding="utf-8")
    (root / "secret.yaml").write_text("policy: bound\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    out = tmp_path / "snapshot"
    archive_tree(root, "HEAD", out)
    assert (out / "secret.yaml").read_text(encoding="utf-8") == "policy: bound\n"


def test_exact_snapshot_rejects_git_symlink_bindings(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "target").write_text("value", encoding="utf-8")
    (root / "link").symlink_to("target")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    with pytest.raises(ConfigError, match="unsupported external binding"):
        archive_tree(root, "HEAD", tmp_path / "snapshot")


@pytest.mark.parametrize("policy", ["version: 1", "version: ["])
@pytest.mark.parametrize("registration_path", [".shipgate/openshell.json", ".shipgate/OpenShell.json", "nested/.SHIPGATE/openshell.json"])
def test_verification_snapshot_allows_only_selected_contained_policy_file_chains(tmp_path, policy, registration_path):
    import json

    root = _repo(tmp_path)
    registration = root / registration_path
    registration.parent.mkdir(parents=True)
    registration.write_text(json.dumps({"version": 1, "runtime_version": "0.1.2",
        "policies": [{"path": "link", "role": "authored"}]}))
    (root / "policy").write_text(policy)
    (root / "middle").symlink_to("policy")
    (root / "link").symlink_to("middle")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "selected file chain")
    out = tmp_path / "snapshot"
    archive_verification_tree(root, "HEAD", out)
    assert (out / "link").read_text() == policy
    assert (out / "link").readlink() == Path("middle")
    assert (out / "middle").readlink() == Path("policy")
    with pytest.raises(ConfigError, match="unsupported external binding"):
        archive_tree(root, "HEAD", tmp_path / "generic")


@pytest.mark.parametrize("target", ["../outside", "/etc/passwd", "missing", "link"])
def test_verification_snapshot_refuses_unresolved_selected_links(tmp_path, target):
    import json

    root = _repo(tmp_path)
    registration = root / ".shipgate/openshell.json"
    registration.parent.mkdir()
    registration.write_text(json.dumps({"version": 1, "runtime_version": "0.1.2",
        "policies": [{"path": "link", "role": "authored"}]}))
    (root / "directory").mkdir()
    (root / "directory/policy").write_text("version: 1")
    (root / "link").symlink_to(target)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "unresolved selected link")
    with pytest.raises(ConfigError, match="unsupported external binding"):
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")


def test_worktree_cannot_authorize_a_committed_link(tmp_path):
    # Admission reads the committed link, never the worktree's: a link the
    # worktree has since retargeted inside the tree, and selected, is still
    # the escaping link the commit holds.
    root = _repo(tmp_path)
    _select_policy(root, "policy")
    (root / "policy").write_text("version: 1")
    (root / "link").symlink_to("../outside")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "escaping link")
    (root / "link").unlink()
    (root / "link").symlink_to("policy")
    (root / ".shipgate/openshell.json").unlink()
    (root / ".shipgate").rmdir()
    _select_policy(root, "link")
    with pytest.raises(ConfigError, match="unsupported external binding at link "):
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")


@pytest.mark.parametrize("hops", [7, 8, 9])
def test_selected_policy_file_chain_uses_the_host_reader_hop_bound(tmp_path, hops):
    import json

    root = _repo(tmp_path)
    registration = root / ".shipgate/openshell.json"
    registration.parent.mkdir()
    registration.write_text(json.dumps({"version": 1, "runtime_version": "0.1.2",
        "policies": [{"path": "link-0", "role": "authored"}]}))
    (root / "policy").write_text("version: 1")
    for index in range(hops):
        (root / f"link-{index}").symlink_to(f"link-{index + 1}" if index + 1 < hops else "policy")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "bounded selected chain")
    if hops <= 8:
        out = tmp_path / "snapshot"
        archive_verification_tree(root, "HEAD", out)
        assert (out / "link-0").read_text() == "version: 1"
    else:
        with pytest.raises(ConfigError, match="unsupported external binding"):
            archive_verification_tree(root, "HEAD", tmp_path / "snapshot")


def test_selected_policy_links_do_not_admit_submodules(tmp_path):
    import json

    root = _repo(tmp_path)
    registration = root / ".shipgate/openshell.json"
    registration.parent.mkdir()
    registration.write_text(json.dumps({"version": 1, "runtime_version": "0.1.2",
        "policies": [{"path": "link", "role": "authored"}]}))
    (root / "policy").write_text("version: 1")
    (root / "link").symlink_to("policy")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "selected policy link")
    commit = _git_output(root, "rev-parse", "HEAD")
    _git(root, "update-index", "--add", "--cacheinfo", f"160000,{commit},vendor")
    _git(root, "commit", "-m", "add gitlink")
    with pytest.raises(ConfigError, match="unsupported external binding at vendor"):
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")


def _select_policy(root: Path, path: str) -> None:
    import json

    registration = root / ".shipgate/openshell.json"
    registration.parent.mkdir()
    registration.write_text(json.dumps({"version": 1, "runtime_version": "0.1.2",
        "policies": [{"path": path, "role": "authored"}]}))


@pytest.mark.parametrize("target", ["missing/../policy", "policy/", "afile/../policy"])
def test_verification_snapshot_refuses_selected_links_the_filesystem_cannot_open(tmp_path, target):
    # Each normalizes to `policy`, but opening the link fails at `missing`, or
    # where a regular file is used as a directory.
    root = _repo(tmp_path)
    _select_policy(root, "link")
    (root / "policy").write_text("version: 1")
    (root / "afile").write_text("not a directory")
    (root / "link").symlink_to(target)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "selected link that does not open")
    with pytest.raises(ConfigError, match="unsupported external binding at link "):
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")


@pytest.mark.parametrize("links", [
    {"link": "./policy"},
    {"nested/link": "../policies/x.yaml"},
    {"nested/link": "../middle", "middle": "./policies/../policies/x.yaml"},
])
def test_verification_snapshot_admits_selected_links_that_open_as_written(tmp_path, links):
    root = _repo(tmp_path)
    selected = next(iter(links))
    _select_policy(root, selected)
    (root / "policy").write_text("version: 1")
    (root / "policies").mkdir()
    (root / "policies/x.yaml").write_text("version: 1")
    for link, target in links.items():
        (root / link).parent.mkdir(parents=True, exist_ok=True)
        (root / link).symlink_to(target)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "selected links that open")
    out = tmp_path / "snapshot"
    archive_verification_tree(root, "HEAD", out)
    assert (out / selected).read_text() == "version: 1"
    assert {link: os.readlink(out / link) for link in links} == links


@pytest.mark.parametrize("target", [b"\xff\xfe-target", b"bad\x00target"])
def test_verification_snapshot_refuses_an_unrepresentable_unrelated_link(tmp_path, target):
    # Selection discovery recreates every link of a scoped tree. One it cannot
    # decode or create leaves the generic refusal; it is not an internal error.
    root = _repo(tmp_path)
    _select_policy(root, "link")
    (root / "policy").write_text("version: 1")
    (root / "link").symlink_to("policy")
    _git(root, "add", ".")
    blob = subprocess.run(["git", "-C", str(root), "hash-object", "-w", "--stdin"],
                          input=target, check=True, capture_output=True).stdout.decode().strip()
    _git(root, "update-index", "--add", "--cacheinfo", f"120000,{blob},unrelated")
    _git(root, "commit", "-m", "unrelated link no checkout can represent")
    # The refusal names the link it cannot represent; the selected link beside
    # it opens inside the tree and is not the reason.
    with pytest.raises(ConfigError, match="unsupported external binding at unrelated "):
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")


def test_verification_snapshot_refuses_selected_links_where_links_cannot_be_created(
    tmp_path, monkeypatch
):
    root = _repo(tmp_path)
    _select_policy(root, "link")
    (root / "policy").write_text("version: 1")
    (root / "link").symlink_to("policy")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "selected policy link")

    def unavailable(*_args, **_kwargs):
        # As on Windows without the symbolic-link privilege.
        raise OSError("A required privilege is not held by the client")

    monkeypatch.setattr(os, "symlink", unavailable)
    with pytest.raises(
        ConfigError, match="unsupported external binding at link .*could not create the link"
    ):
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")


_SKILL = "---\nname: demo\n---\n"


def _commit_links(root: Path, links: dict[str, str]) -> None:
    (root / "AGENTS.md").write_text("# Agents\n")
    (root / "afile").write_text("not a directory")
    (root / ".agents/skills/demo").mkdir(parents=True)
    (root / ".agents/skills/demo/SKILL.md").write_text(_SKILL)
    for link, target in links.items():
        (root / link).parent.mkdir(parents=True, exist_ok=True)
        (root / link).symlink_to(target)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "committed links")


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
@pytest.mark.parametrize(
    ("links", "reads"),
    [
        ({"CLAUDE.md": "AGENTS.md"}, {"CLAUDE.md": "# Agents\n"}),
        ({".claude/skills": "../.agents/skills"}, {".claude/skills/demo/SKILL.md": _SKILL}),
        ({".claude/skills": "../.agents/skills/"}, {".claude/skills/demo/SKILL.md": _SKILL}),
        (
            {"pkg/CLAUDE.md": "../CLAUDE.md", "CLAUDE.md": "./AGENTS.md"},
            {"pkg/CLAUDE.md": "# Agents\n"},
        ),
        (
            {
                "docs/demo.md": "../.claude/skills/demo/SKILL.md",
                ".claude/skills": "../.agents/skills",
            },
            {"docs/demo.md": _SKILL},
        ),
        ({"pkg/root": ".."}, {"pkg/root/AGENTS.md": "# Agents\n"}),
    ],
)
def test_verification_snapshot_recreates_links_that_open_inside_the_tree(tmp_path, links, reads):
    # A checkout of the commit holds these links, so the snapshot holds them
    # too, as links, with no policy selection: `CLAUDE.md -> AGENTS.md` is the
    # common case, then a linked skills directory, a chain, a path through a
    # linked directory and a link to the repository root.
    root = _repo(tmp_path)
    _commit_links(root, links)
    out = tmp_path / "snapshot"
    archive_verification_tree(root, "HEAD", out)
    assert {link: os.readlink(out / link) for link in links} == links
    assert {path: (out / path).read_text() for path in reads} == reads
    # A generic archive still refuses every link.
    with pytest.raises(ConfigError, match="unsupported external binding"):
        archive_tree(root, "HEAD", tmp_path / "generic")


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
@pytest.mark.parametrize(
    "target",
    [
        "../outside",
        "sub/../../outside",
        "/etc/hosts",
        "missing.md",
        ".git/config",
        "link",
        "AGENTS.md/",
        "missing/../AGENTS.md",
        "afile/../AGENTS.md",
    ],
)
def test_verification_snapshot_refuses_links_that_do_not_open_inside_the_tree(tmp_path, target):
    # Escaping, absolute, dangling and looping links, and text the filesystem
    # would not open although it normalizes to a file, keep the refusal. It
    # names the link and the rule, and says nothing about the manifest.
    root = _repo(tmp_path)
    (root / "sub").mkdir()
    (root / "sub/keep").write_text("")
    _commit_links(root, {"link": target})
    with pytest.raises(ConfigError) as refused:
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")
    assert str(refused.value) == (
        "Git tree contains unsupported external binding at link (mode 120000, type blob): "
        "only a link that opens a file or directory inside the repository is recreated."
    )


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
@pytest.mark.parametrize("hops", [8, 9])
def test_verification_snapshot_bounds_a_link_chain(tmp_path, hops):
    root = _repo(tmp_path)
    links = {f"link-{index}": f"link-{index + 1}" for index in range(hops - 1)}
    links[f"link-{hops - 1}"] = "AGENTS.md"
    _commit_links(root, links)
    if hops <= 8:
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")
        assert (tmp_path / "snapshot/link-0").read_text() == "# Agents\n"
    else:
        with pytest.raises(ConfigError, match="unsupported external binding at link-0 "):
            archive_verification_tree(root, "HEAD", tmp_path / "snapshot")


_NOT_CONTAINED = (
    "Git tree contains unsupported external binding at link (mode 120000, type blob): "
    "only a link that opens a file or directory inside the repository is recreated."
)


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
@pytest.mark.parametrize(
    "links",
    [
        {"link": "../AGENTS.md"},
        {"link": "/AGENTS.md"},
        {"pkg/root": "..", "link": "pkg/root/../AGENTS.md"},
    ],
    ids=["above-the-root", "absolute", "above-the-root-through-a-link"],
)
def test_verification_snapshot_refuses_a_link_that_leaves_the_tree_by_a_held_name(
    tmp_path, links
):
    # Each text names a file the tree holds, so only the rule against leaving
    # the tree, not the absence of the name, refuses it.
    root = _repo(tmp_path)
    _commit_links(root, links)
    with pytest.raises(ConfigError) as refused:
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")
    assert str(refused.value) == _NOT_CONTAINED


def _commit_tree(root: Path, entries: dict[str, tuple[str, bytes]]) -> None:
    """Commit exactly ``entries`` (path -> (mode, bytes)) as HEAD, bypassing the worktree.

    Built with plumbing, so a tree whose names one filesystem would merge can be
    committed on that filesystem too.
    """

    def blob(data: bytes) -> str:
        return subprocess.run(
            ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
            input=data, check=True, capture_output=True,
        ).stdout.decode().strip()

    nested: dict = {}
    for path, value in entries.items():
        *parents, name = path.split("/")
        node = nested
        for part in parents:
            node = node.setdefault(part, {})
        node[name] = value

    def write(node: dict) -> str:
        lines = []
        for name, value in node.items():
            if isinstance(value, dict):
                lines.append(f"040000 tree {write(value)}\t{name}")
            else:
                mode, data = value
                lines.append(f"{mode} blob {blob(data)}\t{name}")
        return subprocess.run(
            ["git", "-C", str(root), "mktree"],
            input="\n".join(lines).encode() + b"\n", check=True, capture_output=True,
        ).stdout.decode().strip()

    commit = _git_output(root, "commit-tree", write(nested), "-m", "plumbed tree")
    _git(root, "update-ref", "HEAD", commit)


def _merges_on_this_filesystem(tmp_path: Path, written: str, looked_up: str) -> bool:
    probe = tmp_path / "filesystem-probe"
    probe.mkdir()
    (probe / written).write_text("")
    return (probe / looked_up).exists() and len(os.listdir(probe)) == 1


_AGENTS = ("100644", b"# Agents\n")
_CASE = {"Alpha": ("120000", b"."), "alpha/inner": ("120000", b"../AGENTS.md"), "AGENTS.md": _AGENTS}
_NORMALIZATION = {
    "cafe\u0301": ("120000", b"."),
    "caf\u00e9/inner": ("120000", b"../AGENTS.md"),
    "AGENTS.md": _AGENTS,
}
_BLOB_BESIDE_DIRECTORY = {
    "A": _AGENTS,
    "B": _AGENTS,
    "a/x": ("120000", b"../B"),
}


@pytest.mark.parametrize(
    ("entries", "pair"),
    [
        (_CASE, ("alpha", "Alpha")),
        (_NORMALIZATION, ("caf\u00e9", "cafe\u0301")),
        (_BLOB_BESIDE_DIRECTORY, ("a", "A")),
    ],
    ids=["case", "normalization", "blob"],
)
def test_verification_snapshot_refuses_a_name_that_collides_with_a_directory(
    tmp_path, entries, pair
):
    # Refused on every filesystem, as whole paths that collide already are,
    # before anything is written.
    root = _repo(tmp_path)
    _commit_tree(root, entries)
    with pytest.raises(ConfigError) as refused:
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")
    assert str(refused.value) == (
        f"Git tree contains filesystem-colliding paths: {pair[0]!r} and {pair[1]!r}"
    )


@pytest.mark.parametrize(
    ("entries", "pair"),
    [
        ({"\u2460": ("120000", b"AGENTS.md"), "1/x": _AGENTS, "AGENTS.md": _AGENTS}, ("1", "\u2460")),
        ({"\u2460": _AGENTS, "1/x": ("120000", b"../AGENTS.md"), "AGENTS.md": _AGENTS}, ("1", "\u2460")),
    ],
    ids=["link-named-like-a-directory", "link-in-a-directory-named-like-a-file"],
)
def test_verification_snapshot_holds_a_link_apart_from_names_that_fold_together(
    tmp_path, entries, pair
):
    # Names whose portable spellings fold together are refused on every
    # filesystem once a link is one of them, or sits in the directory.
    root = _repo(tmp_path)
    _commit_tree(root, entries)
    with pytest.raises(ConfigError) as refused:
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")
    assert str(refused.value) == (
        f"Git tree contains filesystem-colliding paths: {pair[0]!r} and {pair[1]!r}"
    )


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
@pytest.mark.parametrize("unrelated_link", [False, True], ids=["no-link", "unrelated-link"])
def test_verification_snapshot_archives_folding_names_when_no_link_is_involved(
    tmp_path, unrelated_link
):
    # When neither name is a link nor holds one, a file and a directory whose
    # portable spellings fold together are written as they are, wherever the
    # filesystem keeps them apart (as it keeps these two on every filesystem
    # in use), even beside a link elsewhere in the tree.
    root = _repo(tmp_path)
    entries = {"\u2460": ("100644", b"one\n"), "1/x": ("100644", b"x\n")}
    if unrelated_link:
        entries.update({"AGENTS.md": _AGENTS, "CLAUDE.md": ("120000", b"AGENTS.md")})
    _commit_tree(root, entries)
    out = tmp_path / "snapshot"
    archive_verification_tree(root, "HEAD", out)
    assert (out / "\u2460").read_text() == "one\n"
    assert (out / "1/x").read_text() == "x\n"


@pytest.mark.parametrize(
    ("entries", "written", "looked_up", "failing"),
    [
        ({"BUILD": ("100644", b"b\n"), "build/x": ("100644", b"x\n")}, "BUILD", "build", "build/x"),
        (
            {"caf\u00e9": ("100644", b"b\n"), "cafe\u0301/x": ("100644", b"x\n")},
            "caf\u00e9",
            "cafe\u0301",
            "caf\u00e9",
        ),
    ],
    ids=["case", "normalization"],
)
def test_verification_snapshot_writes_a_file_beside_a_folding_directory_or_types_why_not(
    tmp_path, entries, written, looked_up, failing
):
    # A link-free tree keeps the archive's behaviour: where the filesystem
    # keeps the names apart it is written; where it merges them, writing one
    # fails, and that is a typed refusal naming the tree path, never an
    # internal error naming a temporary one.
    root = _repo(tmp_path)
    _commit_tree(root, entries)
    out = tmp_path / "snapshot"
    if _merges_on_this_filesystem(tmp_path, written, looked_up):
        with pytest.raises(ConfigError) as refused:
            archive_verification_tree(root, "HEAD", out)
        assert str(refused.value) == (
            f"Git tree path {failing} collides with another path on this filesystem."
        )
    else:
        archive_verification_tree(root, "HEAD", out)
        assert {path: (out / path).read_bytes() for path in entries} == {
            path: data for path, (_mode, data) in entries.items()
        }


@pytest.mark.parametrize(
    ("entries", "method", "name", "error", "failing"),
    [
        (
            {"BUILD": ("100644", b"b\n"), "build/x": ("100644", b"x\n")},
            "mkdir", "build", FileExistsError, "build/x",
        ),
        (
            {"BUILD": ("100644", b"b\n"), "build/x": ("100644", b"x\n")},
            "write_bytes", "x", NotADirectoryError, "build/x",
        ),
        (
            {"caf\u00e9": ("100644", b"b\n"), "cafe\u0301/x": ("100644", b"x\n")},
            "write_bytes", "caf\u00e9", IsADirectoryError, "caf\u00e9",
        ),
    ],
    ids=["directory-over-file", "file-as-directory", "file-over-directory"],
)
def test_verification_snapshot_types_a_write_a_merging_filesystem_refuses(
    tmp_path, monkeypatch, entries, method, name, error, failing
):
    # The refusal a filesystem that merges these names gives, raised here on
    # any filesystem, is a typed refusal naming the tree path, never an
    # internal error naming a temporary one.
    import errno

    root = _repo(tmp_path)
    _commit_tree(root, entries)
    original = getattr(Path, method)

    def refusing(self, *args, **kwargs):
        if self.name == name:
            code = {
                FileExistsError: errno.EEXIST,
                NotADirectoryError: errno.ENOTDIR,
                IsADirectoryError: errno.EISDIR,
            }[error]
            raise error(code, os.strerror(code), str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, refusing)
    with pytest.raises(ConfigError) as refused:
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")
    assert str(refused.value) == (
        f"Git tree path {failing} collides with another path on this filesystem."
    )


def _links_not_within(root: Path) -> list[str]:
    resolved = os.path.realpath(root)
    return [
        os.path.join(directory, name)
        for directory, names, files in os.walk(root)
        for name in [*names, *files]
        if os.path.islink(os.path.join(directory, name))
        and not os.path.realpath(os.path.join(directory, name)).startswith(resolved + os.sep)
        and os.path.realpath(os.path.join(directory, name)) != resolved
    ]


_MERGES = {
    "case": (_CASE, "probe", "PROBE"),
    "normalization": (_NORMALIZATION, "cafe\u0301", "caf\u00e9"),
}


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
@pytest.mark.parametrize("merge", sorted(_MERGES))
@pytest.mark.parametrize("layer", ["parent", "result"])
def test_verification_snapshot_holds_each_link_in_its_own_directory(
    tmp_path, monkeypatch, merge, layer
):
    # With the name-collision check disabled, the per-link checks still
    # refuse on a filesystem that merges these names, and every link the
    # snapshot holds stays within it.
    from agents_shipgate.cli.verify import git as git_module

    entries, written, looked_up = _MERGES[merge]
    if not _merges_on_this_filesystem(tmp_path, written, looked_up):
        pytest.skip(f"this filesystem keeps {merge} variants apart")
    root = _repo(tmp_path)
    _commit_tree(root, entries)
    monkeypatch.setattr(git_module, "_portable_tree_path_key", lambda path: path)
    if layer == "result":
        def unchecked(root_path, path_text, link_text, *, directory):
            (root_path / path_text).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(link_text, root_path / path_text, target_is_directory=directory)

        monkeypatch.setattr(git_module, "_create_admitted_link", unchecked)
    out = tmp_path / "snapshot"
    with pytest.raises(ConfigError, match="collides with another path on this filesystem"):
        archive_verification_tree(root, "HEAD", out)
    if layer == "parent":
        assert _links_not_within(out) == []


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
@pytest.mark.parametrize("target", [b"p", b"p/x"], ids=["directory", "file"])
def test_verification_snapshot_types_a_link_whose_directory_cannot_be_resolved(
    tmp_path, monkeypatch, target
):
    # With the name-collision check disabled, a link whose directory this
    # filesystem cannot resolve is a typed refusal of that link, never an
    # internal error naming a temporary path.
    from agents_shipgate.cli.verify import git as git_module

    if not _merges_on_this_filesystem(tmp_path, "probe", "PROBE"):
        pytest.skip("this filesystem keeps case variants apart")
    root = _repo(tmp_path)
    _commit_tree(root, {
        "AGENTS.md": _AGENTS,
        "P": ("120000", target),
        "p/x": ("120000", b"../AGENTS.md"),
    })
    monkeypatch.setattr(git_module, "_portable_tree_path_key", lambda path: path)
    with pytest.raises(ConfigError) as refused:
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")
    assert str(refused.value) == (
        "Git tree contains unsupported external binding at p/x (mode 120000, type blob): "
        "it collides with another path on this filesystem."
    )


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")
def test_verification_snapshot_types_a_link_its_directory_cannot_hold(tmp_path, monkeypatch):
    # A file where the link's directory should be is a typed refusal of the
    # link, never an internal error.
    from agents_shipgate.cli.verify import git as git_module

    if not _merges_on_this_filesystem(tmp_path, "probe", "PROBE"):
        pytest.skip("this filesystem keeps case variants apart")
    root = _repo(tmp_path)
    _commit_tree(root, _BLOB_BESIDE_DIRECTORY)
    monkeypatch.setattr(git_module, "_portable_tree_path_key", lambda path: path)
    with pytest.raises(ConfigError) as refused:
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")
    assert str(refused.value) == (
        "Git tree contains unsupported external binding at a/x (mode 120000, type blob): "
        "it collides with another path on this filesystem."
    )


def test_verification_snapshot_keeps_refusing_links_on_windows(tmp_path, monkeypatch):
    # Where a recreated link would not be the link a checkout holds, every
    # committed link keeps the refusal, with the reason.
    from agents_shipgate.cli.verify import git as git_module

    monkeypatch.setattr(git_module, "_recreates_committed_links", lambda: False)
    root = _repo(tmp_path)
    _commit_tree(root, {"AGENTS.md": _AGENTS, "link": ("120000", b"AGENTS.md")})
    with pytest.raises(ConfigError) as refused:
        archive_verification_tree(root, "HEAD", tmp_path / "snapshot")
    assert str(refused.value) == (
        "Git tree contains unsupported external binding at link (mode 120000, type blob): "
        "committed links are not recreated on Windows."
    )


def test_exact_snapshot_rejects_portable_filesystem_path_collisions(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    blob = _git_output(root, "hash-object", "-w", "--stdin", input_text="bound\n")
    tree = _git_output(
        root,
        "mktree",
        input_text=(
            f"100644 blob {blob}\tPolicy.yaml\n"
            f"100644 blob {blob}\tpolicy.yaml\n"
        ),
    )
    commit = _git_output(root, "commit-tree", tree, "-m", "colliding tree")

    with pytest.raises(ConfigError, match="filesystem-colliding paths"):
        archive_tree(root, commit, tmp_path / "snapshot")


def test_exact_snapshot_rejects_blob_bytes_that_do_not_match_their_oid(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "policy.yaml").write_text("safe\n", encoding="utf-8")
    _git(root, "add", "policy.yaml")
    _git(root, "commit", "-m", "fixture")
    oid = _git_output(root, "rev-parse", "HEAD:policy.yaml")
    loose_object = root / ".git" / "objects" / oid[:2] / oid[2:]
    if not loose_object.is_file():
        pytest.skip("fixture blob is not stored as a loose object")
    loose_object.chmod(0o644)
    loose_object.write_bytes(zlib.compress(b"blob 5\0evil\n"))

    with pytest.raises(ConfigError, match="Git object"):
        archive_tree(root, "HEAD", tmp_path / "snapshot")


def test_diff_rejects_binary_source_like_paths(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = root / "agent.py"
    source.write_text("print('base')\n", encoding="utf-8")
    _git(root, "add", "agent.py")
    _git(root, "commit", "-m", "base")
    source.write_bytes(b"\x00binary capability payload\n")
    _git(root, "add", "agent.py")
    _git(root, "commit", "-m", "binary head")

    with pytest.raises(ConfigError, match="source-like changed paths as binary"):
        diff_revspec_context(root, "HEAD~1...HEAD")


def test_repository_identity_normalizes_ssh_and_https_remotes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _git(root, "remote", "add", "origin", "git@GitHub.com:ThreeMoonsLab/shipgate.git")
    assert repository_identity(root) == "github.com/ThreeMoonsLab/shipgate"
    _git(
        root,
        "remote",
        "set-url",
        "origin",
        "https://token@example.test/org/repo.git?credential=secret",
    )
    assert repository_identity(root) == "example.test/org/repo"


def test_effective_worktree_diff_coalesces_committed_staged_and_unstaged_edits(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    target = root / "AGENTS.md"
    target.write_text("base\n", encoding="utf-8")
    _git(root, "add", "AGENTS.md")
    _git(root, "commit", "-m", "base")

    target.write_text("committed\n", encoding="utf-8")
    _git(root, "add", "AGENTS.md")
    _git(root, "commit", "-m", "issue fix")
    target.write_text("staged review\n", encoding="utf-8")
    _git(root, "add", "AGENTS.md")
    target.write_text("unstaged review\n", encoding="utf-8")

    changed, diff_text = working_tree_context(
        root,
        comparison_ref="HEAD~1",
        reject_index_hidden=True,
    )

    assert changed == ["AGENTS.md"]
    assert diff_text.count("diff --git a/AGENTS.md b/AGENTS.md") == 1
    assert "unstaged review" in diff_text
    assert "committed" not in diff_text
    assert "+staged review" not in diff_text


def test_effective_worktree_diff_preserves_rename_and_mode_semantics(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    source = root / "AGENTS.md"
    source.write_text(
        "shared rule\nshared scope\nbase instructions\n",
        encoding="utf-8",
    )
    _git(root, "add", "AGENTS.md")
    _git(root, "commit", "-m", "base")

    destination = root / "CLAUDE.md"
    source.rename(destination)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "rename instructions")
    destination.write_text(
        "shared rule\nshared scope\nreviewed instructions\n",
        encoding="utf-8",
    )
    destination.chmod(0o755)

    changed, diff_text = working_tree_context(
        root,
        comparison_ref="HEAD~1",
        reject_index_hidden=True,
    )

    assert changed == ["AGENTS.md", "CLAUDE.md"]
    assert diff_text.count("diff --git ") == 1
    assert "rename from AGENTS.md" in diff_text
    assert "rename to CLAUDE.md" in diff_text
    assert "old mode 100644" in diff_text
    assert "new mode 100755" in diff_text


def test_effective_worktree_diff_honors_repository_filemode_false(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    target = root / "lib" / "util.py"
    target.parent.mkdir()
    target.write_text("base\n", encoding="utf-8")
    manifest = root / "shipgate.yaml"
    manifest.write_text("version: '0.1'\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")

    _git(root, "config", "core.fileMode", "false")
    target.write_text("committed change\n", encoding="utf-8")
    _git(root, "add", "lib/util.py")
    _git(root, "commit", "-m", "change utility")
    target.chmod(0o755)
    manifest.chmod(0o755)

    changed, diff_text = working_tree_context(
        root,
        comparison_ref="HEAD~1",
        reject_index_hidden=True,
    )

    assert changed == ["lib/util.py"]
    assert "shipgate.yaml" not in diff_text
    assert "old mode 100644" not in diff_text
    assert "new mode 100755" not in diff_text


def test_effective_worktree_diff_retains_merge_base_relative_untracked_paths(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    tracked = root / "agent.py"
    tracked.write_text("base\n", encoding="utf-8")
    _git(root, "add", "agent.py")
    _git(root, "commit", "-m", "base")

    tracked.write_text("committed\n", encoding="utf-8")
    _git(root, "add", "agent.py")
    _git(root, "commit", "-m", "change agent")
    untracked = root / "new_agent.py"
    untracked.write_text("untracked capability\n", encoding="utf-8")

    changed, diff_text = working_tree_context(
        root,
        comparison_ref="HEAD~1",
        reject_index_hidden=True,
    )

    assert changed == ["agent.py", "new_agent.py"]
    assert "diff --git a/agent.py b/agent.py" in diff_text
    assert "new_agent.py" not in diff_text
