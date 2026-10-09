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
