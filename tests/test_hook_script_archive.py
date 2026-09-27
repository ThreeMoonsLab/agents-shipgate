"""Historical dependency selection remains exact, bounded and non-executing."""
import pytest
from test_partial_host_comparison import _git, _repository

from agents_shipgate.cli.verify import git as git_module
from agents_shipgate.cli.verify.git import archive_tree
from agents_shipgate.cli.verify.host_tree import materialize_host_tree
from agents_shipgate.core.hook_script_capture import MAX_HOOK_SCRIPT_BYTES
from agents_shipgate.core.host_grants import HostStaticParseCache, build_host_boundary_snapshot


@pytest.mark.parametrize("shape,limit", [
    ("file", None), ("missing", "missing_input"),
    ("symlink", "symlink_input"), ("oversized", "oversized_input"),
])
def test_committed_script_capture_keeps_unsafe_limits(tmp_path, shape, limit, monkeypatch):
    files = {".claude/settings.json": {"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": '"${CLAUDE_PROJECT_DIR}/guard.sh"'},
    ]}]}}, "unrelated.bin": "not selected", "target": "not followed"}
    if shape in {"file", "oversized"}:
        files["guard.sh"] = "a" * (MAX_HOOK_SCRIPT_BYTES + 1) if shape == "oversized" else "script bytes"
    root = _repository(
        tmp_path, files, {"README.md": "changed"},
        links={"guard.sh": "target"} if shape == "symlink" else None,
    )
    calls = []
    copies = []
    copy = git_module._copy_verified_commit_graph

    def counted(*args, **kwargs):
        copies.append(1)
        return copy(*args, **kwargs)

    monkeypatch.setattr(git_module, "_copy_verified_commit_graph", counted)

    def scoped(scope):
        calls.append({path: scope(path) for path in ("guard.sh", "unrelated.bin", "target")})

    def archive(workspace, commit, destination, *, scope, rescope):
        scoped(scope)

        def wider(tree):
            found = rescope(tree)
            if found is not None:
                scoped(found[1])
            return found

        return archive_tree(workspace, commit, destination, scope=scope, rescope=wider)

    tree, read = materialize_host_tree(
        root, _git(root, "rev-parse", "HEAD"), tmp_path / "tree", archive=archive
    )
    snapshot = read or build_host_boundary_snapshot(
        tree, cache=HostStaticParseCache(reference_workspace=root)
    )
    (hook,) = [grant for grant in snapshot.inventory["grants"] if grant["kind"] == "hook"]
    assert hook["script_inputs"][0]["limit"] == limit
    assert len(calls) == (1 if shape == "symlink" else 2)
    # One copy of the verified object graph serves both trees (#702 review),
    # and a tree that selects no further file is read once.
    assert copies == [1]
    assert (read is None) == (shape != "symlink")
    assert calls[0]["guard.sh"] is False
    if shape != "symlink":
        assert calls[1]["guard.sh"] is True
    assert all(not call["unrelated.bin"] and not call["target"] for call in calls)
    assert not (tree / "unrelated.bin").exists()
    if shape == "symlink":
        # The generic archive preserves out-of-scope target *types* using
        # empty placeholders. The real target bytes must stay unread.
        assert (tree / "target").read_bytes() == b""
    else:
        assert not (tree / "target").exists()
