"""Search mutation is named uncertainty, never a complete tool surface."""

from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType

import pytest

from agents_shipgate.inputs import python_imports as imports
from agents_shipgate.inputs.python_imports import OUTSIDE_SCOPE, ImportResolver, _Stop
from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_imported_tool_bindings import _adk, _sdk


def _write(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _observations(root, framework, prelude, extras=None):
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    decorator = "from agents import function_tool\n@function_tool\n" if framework == "sdk" else ""
    _write(root, {
        "app.py": prelude + f"from {namespace} import Agent\nfrom tools import read\n"
        "root = Agent(name='Root', tools=[read])\n",
        "tools.py": decorator + "def read():\n    return 1\n",
        **(extras or {}),
    })
    if framework == "sdk":
        loaded = _sdk(root, "app.py")
        return loaded.binding_observations, loaded.warnings
    loaded, artifacts = _adk(root, "app.py")
    return [item for source in loaded for item in source.binding_observations], artifacts.warnings


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("prelude", [
    "import sys\nsys.path.insert(0, 'vendor')\n",
    "import sys as runtime\nruntime.meta_path = []\n",
    "import sys\nsys.path_hooks.append(hook)\n",
    "import sys\nsys.path_importer_cache.clear()\n",
    "import sys\npaths = sys.path\npaths[:] = ['vendor']\n",
    "import site\nsite.addsitedir('vendor')\n",
    "from site import addsitedir as add\nadd('vendor')\n",
    "import site\nadd = site.addsitedir\nadd('vendor')\n",
])
def test_search_changes_withhold_constructor_binding(tmp_path, framework, prelude):
    observations, warnings = _observations(tmp_path, framework, prelude, {
        "vendor/agents/__init__.py": "raise AssertionError('application source must never execute')\n",
    })
    assert observations and warnings
    assert all(not item.tools_complete and not item.handoffs_complete for item in observations)
    assert any("import search" in warning or "import-search" in warning for warning in warnings)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("prelude,extras", [
    ("import bridge\n", {"bridge.py": "import patcher\n", "patcher.py": "import sys\nsys.path.insert(0, 'vendor')\n"}),
    ("from bridge import search\nsearch.insert(0, 'vendor')\n", {"bridge.py": "import sys\nsearch = sys.path\n"}),
    ("import bridge\nbridge.runtime.path.insert(0, 'vendor')\n", {"bridge.py": "import sys as runtime\n"}),
    ("import bridge\nsaved = bridge\nopaque(saved)\n", {"bridge.py": "import holder\n", "holder.py": "import sys\n"}),
])
def test_imported_search_carriers_are_unread(tmp_path, framework, prelude, extras):
    observations, warnings = _observations(tmp_path, framework, prelude, extras)
    assert observations and warnings
    assert all(not item.tools_complete and not item.handoffs_complete for item in observations)
    assert any("import search" in warning or "import-search" in warning for warning in warnings)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("prelude", [
    "import sys\nimport site\n",
    "def local(sys):\n    sys.path.insert(0, 'vendor')\n",
])
def test_non_search_reads_and_local_shadow_keep_bindings(tmp_path, framework, prelude):
    observations, warnings = _observations(tmp_path, framework, prelude)
    assert observations and not warnings
    assert all(item.tools_complete and item.handoffs_complete for item in observations)


@pytest.mark.parametrize("prelude,extras", [
    ("import sys\nsys.path.insert(0, 'vendor')\n", {}),
    ("import site\nsite.addsitedir('vendor')\n", {}),
    ("import a\n", {"a.py": "import b\n", "b.py": "import sys\nsys.path.insert(0, 'vendor')\n"}),
    ("import carrier\ncarrier.runtime.path.clear()\n", {"carrier.py": "import sys as runtime\n"}),
])
def test_search_changes_do_not_establish_ordinary_named_tools(tmp_path, prelude, extras):
    _write(tmp_path, {"app.py": prelude + "from tools import read\n", "tools.py": "def read():\n    return 1\n", **extras})
    resolver = ImportResolver(tmp_path)
    text = (tmp_path / "app.py").read_text()
    module = resolver.entry(tmp_path / "app.py", ast.parse(text), text)
    resolution = resolver.resolve(module, "read")
    assert resolution.caveats or not resolution.resolved
    assert "import search" in str(resolution.caveats) + str(resolution.detail)


@pytest.mark.parametrize("method", ["module", "_patch_scan"])
@pytest.mark.parametrize("changed_text", [False, True])
def test_captured_search_context_rejects_unaccounted_cached_source(tmp_path, method, changed_text):
    _write(tmp_path, {"app.py": "import sys\n"})
    reader = ImportResolver(tmp_path)
    path = tmp_path / "app.py"
    module = reader.module(path)
    reader._import_search_captured = MappingProxyType({path: module.text + "# moved\n"} if changed_text else {})
    with pytest.raises(_Stop) as caught:
        getattr(reader, method)(path)
    assert caught.value.reason == OUTSIDE_SCOPE


@pytest.mark.parametrize("text", [
    "import sys\nversion = sys.version\n",
    "import sys\nsys.path\nsys.path[0]\nflag = 'vendor' in sys.path\n",
])
def test_small_non_escaping_reads_are_search_classification_only(text):
    assert imports._import_search_effects(ast.parse(text)) == {}
    # This supplies no receiving/native role to the separate constructor proof.


@pytest.mark.parametrize("count", [80, 160])
def test_repeated_alias_projection_has_bounded_structural_work(monkeypatch, count):
    text = "import sys as runtime\n" * count + "runtime.version\n" * count
    calls = 0
    original = imports._absolute_import_reference

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(imports, "_absolute_import_reference", counted)
    assert imports._import_search_effects(ast.parse(text)) == {}
    assert calls <= 2 * count


@pytest.mark.parametrize("lookup", ["default", "source_extra_root"])
def test_captured_search_uses_snapshot_namespace_without_disk_normalization(monkeypatch, lookup):
    root = Path("/__shipgate_captured_search_snapshot__")
    carrier = "helpers/carrier.py" if lookup == "source_extra_root" else "carrier.py"
    texts = {
        "app.py": "import carrier\ncarrier.runtime.path.insert(0, 'vendor')\n",
        carrier: "import sys as runtime\n",
    }

    class SnapshotResolver(ImportResolver):
        def __post_init__(self):
            pass

        def _listing(self, directory):
            if not directory.is_relative_to(root):
                return None
            prefix = "" if directory == root else directory.relative_to(root).as_posix() + "/"
            names = {name.removeprefix(prefix).split("/", 1)[0] for name in texts if name.startswith(prefix)}
            return frozenset(names) if names else None

        def _kind(self, directory, name):
            if name not in (self._listing(directory) or ()):
                return None
            return "file" if (directory / name).relative_to(root).as_posix() in texts else "dir"

        def _absolute_candidates(self, module, dotted):
            if lookup == "source_extra_root" and dotted == "carrier":
                return [imports._Container(directory=root / "helpers", module_path=root / carrier)]
            return super()._absolute_candidates(module, dotted)

    source = SnapshotResolver(root)
    source._layout = imports.RepositoryLayout("", lambda directory: source._listing(root / directory))
    modules = [imports._module(root / name, name, ast.parse(text), text) for name, text in texts.items()]

    def forbid_normalization(*args, **kwargs):
        raise AssertionError("captured source namespace must not consult disk normalization")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", forbid_normalization)
        captured = imports._CapturedImportSearchResolver(source, modules)
        limits = captured._import_search_limit([root / "app.py"])
    assert limits and any("import search" in issue for issue in limits)
    assert all(captured._modules[module.path] is module for module in modules)


def _resolver_over(root, files):
    _write(root, files)
    resolver = ImportResolver(root)
    return resolver, resolver.entry(root / "app.py", ast.parse(files["app.py"]), files["app.py"])


def _counting_context(resolver, monkeypatch):
    calls = []
    original = ImportResolver._import_search_context

    def counted(self, paths):
        calls.append(tuple(paths))
        return original(self, paths)

    monkeypatch.setattr(ImportResolver, "_import_search_context", counted)
    return calls


def test_an_import_search_answer_is_computed_once_per_runner_list(tmp_path, monkeypatch):
    resolver, module = _resolver_over(tmp_path, {
        "app.py": "import sys\nsys.path.insert(0, 'vendor')\n", "tools.py": "def read():\n    return 1\n",
    })
    calls = _counting_context(resolver, monkeypatch)
    first = resolver._import_search_limit([module.path])
    assert first and "import search" in first[0]
    assert resolver._import_search_limit([module.path]) == first
    assert len(calls) == 1
    # Another runner list is another question; the module's own refusal is reused.
    other = resolver._import_search_limit([module.path, tmp_path / "tools.py"])
    assert len(calls) == 2 and other[-1] == first[-1]


def test_an_import_search_answer_is_not_remembered_once_a_read_bound_is_reached(tmp_path, monkeypatch):
    resolver, module = _resolver_over(tmp_path, {"app.py": "import sys\nsys.path.insert(0, 'vendor')\n"})
    calls = _counting_context(resolver, monkeypatch)
    resolver._parsed = imports.MAX_MODULES  # The resolution budget is spent.
    first = resolver._import_search_limit([module.path])
    assert resolver._import_search_limit([module.path]) == first
    assert len(calls) == 2
    assert not resolver._import_search_limits and not resolver._import_search_issues


def test_a_stopped_import_search_is_raised_again_not_remembered(tmp_path, monkeypatch):
    resolver, module = _resolver_over(tmp_path, {"app.py": "import sys\n"})
    broken = tmp_path / "broken.py"
    broken.write_text("def (\n")
    for _ in range(2):
        with pytest.raises(_Stop):
            resolver._import_search_limit([module.path, broken])
    assert not resolver._import_search_limits


#: One read whose runner lists share modules: a package two entry points
#: import, an optional import, a typing-only import, an import of code above
#: the scope, and a module that changes the import search.
_SHARED_CLOSURE = {
    "app.py": "import pkg.a\nfrom pkg import b\nfrom tools import read\n",
    "tools.py": "import pkg.c\n\ndef read():\n    return 1\n",
    "pkg/__init__.py": "",
    "pkg/a.py": "from . import c\ntry:\n    import optional_dependency\nexcept ImportError:\n    pass\n",
    "pkg/b.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg import hidden\nimport pkg.c\n",
    "pkg/c.py": "import json\nfrom ...above import helper\n",
    "pkg/hidden.py": "import sys\nsys.path.insert(0, 'vendor')\n",
    "pkg/search.py": "import sys\nsys.path.insert(0, 'vendor')\nimport pkg.c\n",
}
_RUNNER_LISTS = (
    ("app.py",),
    ("app.py", "tools.py"),
    ("tools.py",),
    ("pkg/b.py",),
    ("pkg/a.py", "tools.py"),
    ("pkg/search.py", "app.py"),
    ("app.py",),
)


def _counting_module_reads(monkeypatch):
    """Each walk of a module's tree for its ``except ImportError`` guards.

    Within an import search only reading a module's edges walks them; the
    ``TYPE_CHECKING`` walk is also made by each per-carrier-set refusal check.
    """
    reads: list[ast.Module] = []
    guarded = imports._import_guarded

    def counted(tree):
        reads.append(tree)
        return guarded(tree)

    monkeypatch.setattr(imports, "_import_guarded", counted)
    return reads


def test_each_module_is_read_once_for_every_closure_that_reaches_it(tmp_path, monkeypatch):
    """A closure per runner list, but each module's guards and imports read once (#1046).

    Every list's answer is still the one a reader that never saw another
    list gives, so sharing the module reads changes no answer.
    """
    _write(tmp_path, _SHARED_CLOSURE)
    lists = [[tmp_path / name for name in names] for names in _RUNNER_LISTS]
    expected = [ImportResolver(tmp_path)._import_search_limit(paths) for paths in lists]
    assert any(answer and "above the read scope" in answer[0] for answer in expected)
    assert any(any("import search" in item for item in answer) for answer in expected)

    resolver = ImportResolver(tmp_path)
    contexts = _counting_context(resolver, monkeypatch)
    reads = _counting_module_reads(monkeypatch)
    answers = [resolver._import_search_limit(paths) for paths in lists]
    assert answers == expected
    # The repeated list is answered from the runner-list cache; every other
    # list is its own closure.
    assert len(contexts) == len(lists) - 1
    walked = {id(tree) for tree in reads}
    # One walk per module, however many closures reached it; the
    # typing-only import of pkg/hidden.py is never followed.
    assert len(reads) == len(walked)
    assert len(walked) == len(resolver._import_search_edge_reads)
    assert {path.relative_to(tmp_path).as_posix() for path, _ in resolver._import_search_edge_reads} == {
        "app.py", "tools.py", "pkg/__init__.py", "pkg/a.py", "pkg/b.py", "pkg/c.py", "pkg/search.py",
    }


def test_a_stop_reading_a_module_is_raised_by_every_closure_that_reaches_it(tmp_path, monkeypatch):
    """A module whose import cannot be followed refuses each closure, as on first read."""
    _write(tmp_path, {**_SHARED_CLOSURE, "pkg/c.py": "import json\nimport linked\n", "real.py": "x = 1\n"})
    (tmp_path / "linked.py").symlink_to(tmp_path / "real.py")
    lists = [[tmp_path / name for name in names] for names in _RUNNER_LISTS]
    expected = []
    for paths in lists:
        with pytest.raises(_Stop) as caught:
            ImportResolver(tmp_path)._import_search_limit(paths)
        expected.append((caught.value.reason, caught.value.detail))

    resolver = ImportResolver(tmp_path)
    reads = _counting_module_reads(monkeypatch)
    for paths, (reason, detail) in zip(lists, expected, strict=True):
        with pytest.raises(_Stop) as caught:
            resolver._import_search_limit(paths)
        assert (caught.value.reason, caught.value.detail) == (reason, detail)
        assert "symbolic link" in detail
    assert not resolver._import_search_limits
    assert len(reads) == len({id(tree) for tree in reads})


def test_a_closure_cut_short_reads_no_import_past_its_bound(tmp_path, monkeypatch):
    """The module bound stops reading where it fires; the next closure reads on (#1046).

    No lookup is made for a statement the bounded closure never reached, and
    none is repeated for the statements it did.
    """
    resolver, module = _resolver_over(tmp_path, {
        "app.py": "import a\nimport b\nimport c\nimport d\n", **{f"{name}.py": "" for name in "abcd"},
    })
    asked: list[str] = []
    original = ImportResolver._imported_paths

    def counted(self, runner, statement):
        asked.append(f"{runner.ref}:{statement.names[0].name}")
        return original(self, runner, statement)

    monkeypatch.setattr(ImportResolver, "_imported_paths", counted)
    monkeypatch.setattr(imports, "MAX_PATCH_SCAN_MODULES", 3)
    with pytest.raises(_Stop) as caught:
        resolver._import_search_context([module.path])
    assert "module bound" in caught.value.detail
    assert asked == ["app.py:a", "app.py:b", "app.py:c"]

    monkeypatch.setattr(imports, "MAX_PATCH_SCAN_MODULES", 1024)
    modules, carriers, unread = resolver._import_search_context([module.path])
    assert asked == ["app.py:a", "app.py:b", "app.py:c", "app.py:d"]
    assert [item.ref for item in modules] == ["app.py", "a.py", "b.py", "c.py", "d.py"]
    assert not carriers and not unread


#: One module that meets both carrier checks: a retained namespace (line 3)
#: and an unread import (line 4).
_CARRIER_CHECKS = {
    "app.py": "import bridge\nimport other\nsaved = bridge\nvalue = other.missing\nopaque(saved, value)\n",
    "bridge.py": "import holder\n",
    "other.py": "x = 1\n",
    "holder.py": "import sys\n",
}


def test_a_module_is_read_once_for_every_carrier_set(tmp_path, monkeypatch):
    """Each runner list's carriers ask again; the module is read once and checked per set (#1046).

    The reading is resumed only as far as each carrier set needs, and every
    answer is the one a reader that never saw another carrier set gives.
    """
    _write(tmp_path, _CARRIER_CHECKS)
    bridge, other, holder = (tmp_path / name for name in ("bridge.py", "other.py", "holder.py"))
    carrier_sets = [{bridge}, {other}, set(), {bridge, other}, {holder}]

    def answer(resolver, carriers):
        return resolver._import_search_issue(resolver.module(tmp_path / "app.py"), carriers)

    expected = [answer(ImportResolver(tmp_path), carriers) for carriers in carrier_sets]
    assert expected == [
        "app.py:3 retains an imported sys/site namespace; import search identity is unread",
        "app.py:4 has unread import-search carrier identity",
        None,
        "app.py:3 retains an imported sys/site namespace; import search identity is unread",
        None,
    ]
    followed: list[ast.AST] = []
    original = ImportResolver._constructor_reference

    def counted(self, module, node, scopes):
        if module.ref == "app.py":
            followed.append(node)
        return original(self, module, node, scopes)

    monkeypatch.setattr(ImportResolver, "_constructor_reference", counted)
    assert answer(ImportResolver(tmp_path), set()) is None
    once = len(followed)
    followed.clear()

    resolver = ImportResolver(tmp_path)
    assert [answer(resolver, carriers) for carriers in carrier_sets] == expected
    # Every reference of app.py is followed once, as reading it for the empty
    # carrier set does, not once per carrier set.
    assert len(followed) == once
    assert len({id(node) for node in followed}) == once


def test_a_file_version_patch_map_is_computed_once_for_every_reader(tmp_path, monkeypatch):
    """Each reader parses the modules it reads; their syntax-only patch map is shared (#1046).

    Every module still gets the map ``_attribute_patches`` gives its own tree,
    in its order, as its own copy, and another version of the file is its own
    computation.
    """
    text = "import sys\nimport pkg\nsys.modules['alias'] = pkg\npkg.lookup = None\n"
    _write(tmp_path, {"app.py": text, "pkg/__init__.py": ""})
    monkeypatch.setattr(imports, "_SOURCE_PATCHES", {})
    computed: list[ast.Module] = []
    original = imports._attribute_patches

    def counted(tree, **kwargs):
        if not kwargs:
            computed.append(tree)
        return original(tree, **kwargs)

    monkeypatch.setattr(imports, "_attribute_patches", counted)
    modules = [ImportResolver(tmp_path)._patch_scan(tmp_path / "app.py") for _ in range(3)]
    modules.append(ImportResolver(tmp_path).module(tmp_path / "app.py"))
    assert len(computed) == 1
    expected = original(ast.parse(text))
    assert "pkg.lookup" in expected and len(expected) > 1
    for module in modules:
        assert module.attribute_patches == expected
        assert list(module.attribute_patches) == list(expected)
    assert len({id(module.attribute_patches) for module in modules}) == len(modules)

    (tmp_path / "app.py").write_text(text + "pkg.other = None\n")
    changed = ImportResolver(tmp_path)._patch_scan(tmp_path / "app.py")
    assert len(computed) == 2
    assert changed.attribute_patches == original(ast.parse(text + "pkg.other = None\n"))
    assert "pkg.other" in changed.attribute_patches
