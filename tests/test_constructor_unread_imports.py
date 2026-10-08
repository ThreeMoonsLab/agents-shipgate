"""Unread dependency imports and above-scope snapshot ownership."""

from __future__ import annotations

import pytest

from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_constructor_dependency_ownership import (
    _commit,
    _compare,
    _comparison,
    _git,
    _scoped_versions,
    _unread,
    _versions,
)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("imported", ["from os import getenv as Carrier", "from dataclasses import field as Carrier",
                                     "from collections import Counter as Carrier", "from alternate_exports import ABCMeta as Carrier"])
@pytest.mark.parametrize("route", ["opaque", "alias", "metadata", "mutation", "namespace"])
def test_unread_imported_handle_retention_has_no_canonical_identity(tmp_path, framework, imported, route):
    before, after = _versions(framework, "direct", "added")
    after["bridge.py"] = imported + "\n"
    usage = {
        "opaque": "from foreign_consumer import consume\nconsume(Carrier)\n",
        "alias": "saved = Carrier\n",
        "metadata": "metadata = Carrier.metadata\n",
        "mutation": "Carrier.changed = replacement\n",
        "namespace": "import bridge\nfrom foreign_consumer import consume\nconsume(bridge)\n",
    }[route]
    after["app.py"] = ("from bridge import Carrier\n" if route != "namespace" else "") + "from shim import replacement\n" + usage + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("usage", [
    "import os\nvalue = os.getenv('KEY')\n", "import os\nvalue = os.path.join('a', 'b')\n",
    "import io\nvalue = io.StringIO('value').getvalue()\n", "from pathlib import Path\nvalue = Path('data')\n",
    "from collections import Counter, deque, defaultdict\nvalue = Counter()\nqueue = deque()\nlookup = defaultdict()\n",
    "import dataclasses\n", "from alternate_exports import unused\n",
    "import builtins\n", "import builtins as primitives\n",
    "from builtins import getattr as lookup\n", "import inspect\n",
    "import inspect as inspector\n", "from inspect import currentframe as capture\n",
])
def test_ordinary_external_calls_and_unused_imports_keep_their_boundary(tmp_path, framework, usage):
    before, after = _versions(framework, "direct", "added")
    after["app.py"] = usage + after["app.py"]
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == "added"


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("usage", [
    "import builtins\nconsume(builtins)\n",
    "from builtins import getattr as lookup\nconsume(lookup)\n",
    "import inspect\nconsume(inspect)\n",
    "from inspect import currentframe as capture\ncapture()\n",
    "import importlib\n",
])
def test_used_reflection_handles_and_dynamic_imports_remain_unread(tmp_path, framework, usage):
    before, after = _versions(framework, "direct", "added")
    after["app.py"] = "from foreign_consumer import consume\n" + usage + after["app.py"]
    _unread(_comparison(tmp_path, before, after))


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("provider", ["inspect.py", "inspect/__init__.py"])
def test_unused_inspect_import_does_not_hide_a_local_provider(tmp_path, framework, provider):
    before, after = _versions(framework, "direct", "added")
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    after[provider] = f"from {namespace} import Agent\nfrom shim import replacement\nAgent.__init__ = replacement\n"
    after["app.py"] = "import inspect\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("provider", ["abc.py", "abc/__init__.py", "os.py", "io.py"])
def test_preloaded_provider_controls_do_not_borrow_sibling_files(tmp_path, framework, provider):
    before, after = _versions(framework, "direct", "added")
    after[provider] = "# The canonical startup module is already loaded.\n"
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == "added"



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("patch", [
    "from os import getenv as Carrier\nSaved = Carrier\nCarrier = None\nconsume(Saved)\n",
    "from alternate_exports import ABCMeta as Carrier\nSaved = Carrier\nCarrier = None\nconsume(Saved)\n",
    "if enabled:\n    from os import getenv as Carrier\nconsume(Carrier)\n",
    "from os import getenv as Carrier\nfrom dataclasses import field as Carrier\nconsume(Carrier)\n",
    "import os as Carrier\nimport alternate_exports as Carrier\nconsume(Carrier.getenv)\n",
    "def helper():\n    from os import getenv as Carrier\n    Saved = Carrier\n    Carrier = None\n    consume(Saved)\nhelper()\n",
    "def helper():\n    if enabled:\n        from os import getenv as Carrier\n    consume(Carrier)\nhelper()\n",
    "def helper():\n    from os import getenv as Carrier\n    from dataclasses import field as Carrier\n    consume(Carrier)\nhelper()\n",
])
def test_unread_import_candidates_survive_rebinding_and_mixed_lexical_bindings(tmp_path, framework, patch):
    before, after = _versions(framework, "direct", "added")
    after["app.py"] = "from foreign_consumer import consume\n" + patch + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("change", ["changed", "removed"])
def test_saved_unread_handle_also_withholds_changes_and_removals(tmp_path, framework, change):
    before, after = _versions(framework, "direct", change)
    after["app.py"] = ("from os import getenv as Carrier\nSaved = Carrier\nCarrier = None\n"
                       "from foreign_consumer import consume\nconsume(Saved)\n" + after["app.py"])
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("patch", [
    "from os import getenv as Carrier\ndef helper(Carrier):\n    consume(Carrier)\nhelper(None)\n",
    "from os import getenv as Carrier\ndef helper():\n    Carrier = None\n    consume(Carrier)\nhelper()\n",
    "from os import getenv as Carrier\ndef outer():\n    Carrier = None\n    def helper():\n        nonlocal Carrier\n        consume(Carrier)\n    helper()\nouter()\n",
    "from os import getenv as Carrier\nclass Ordinary:\n    Carrier = None\n    def helper(self, value: Carrier):\n        return None\n",
    "from local_values import Carrier\nfrom foreign_exports import unused\nconsume(Carrier)\n",
    "import local_values as Carrier, foreign_exports\nconsume(Carrier.value)\n",
])
def test_unread_candidate_does_not_replace_actual_local_or_data_bindings(tmp_path, framework, patch):
    before, after = _versions(framework, "direct", "added")
    after["local_values.py"] = "Carrier = None\nvalue = None\n"
    after["app.py"] = "from foreign_consumer import consume\n" + patch + after["app.py"]
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == "added"



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("patch", [
    "from os import getenv as Carrier\nconsume(Carrier)\n",
    "from os import getenv as Carrier\nSaved = Carrier\nCarrier = None\nconsume(Saved)\n",
    "if enabled:\n    from os import getenv as Carrier\nconsume(Carrier)\n",
    "from os import getenv as Carrier\nfrom dataclasses import field as Carrier\nconsume(Carrier)\n",
    "from os import getenv as Carrier\nmetadata = Carrier.metadata\n",
    "from os import getenv as Carrier\nCarrier.changed = None\n",
    "from . import carrier\nconsume(carrier)\n",
    "from .carrier import helper\nconsume(helper)\n",
])
def test_above_scope_unread_imported_handles_keep_snapshot_ownership(tmp_path, framework, patch):
    before, after = _scoped_versions(framework, "added")
    after["pkg/carrier.py"] = "from os import getenv as Carrier\ndef helper():\n    return None\n"
    after["pkg/__init__.py"] = "from foreign_consumer import consume\n" + patch
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", "pkg/app")
    _unread(result)
    assert any("above the read scope" in gap["reason"] for gap in result["head"]["coverage_gaps"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("route", ["constructor", "builder"])
def test_above_scope_constructor_owner_cannot_borrow_the_scope_list_reader(tmp_path, framework, route):
    before, after = _scoped_versions(framework, "added")
    imported = "from agents import Agent as Ctor\n" if framework == "sdk" else "from google.adk.agents import Agent as Ctor\n"
    after["pkg/app/exports.py"] = imported + "def build(tools):\n    return Ctor(name='Above', tools=tools)\n"
    receiving = "Ctor" if route == "constructor" else "build"
    invocation = "Ctor(name='Above', tools=[])" if route == "constructor" else "build([])"
    after["pkg/__init__.py"] = (f"from .app.exports import {receiving}\n"
                               "from foreign_consumer import consume\n"
                               f"value = {invocation}\nconsume(value)\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", "pkg/app")
    _unread(result)
    assert any("above the read scope" in gap["reason"] for gap in result["head"]["coverage_gaps"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("patch", [
    "from os import getenv as Carrier\nvalue = Carrier('KEY')\n",
    "from os.path import join\nvalue = join('a', 'b')\n",
    "from alternate_exports import unused\n",
    "from os import getenv as Carrier\ndef helper(Carrier):\n    consume(Carrier)\nhelper(None)\n",
    "from os import getenv as Carrier\ndef helper():\n    Carrier = None\n    consume(Carrier)\nhelper()\n",
])
def test_above_scope_ordinary_calls_and_lexical_data_keep_their_boundary(tmp_path, framework, patch):
    before, after = _scoped_versions(framework, "added")
    after["pkg/__init__.py"] = "from foreign_consumer import consume\n" + patch
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", "pkg/app")
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == "added"



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_above_scope_all_actual_import_candidates_retain_constructor_ownership(tmp_path, framework, change):
    before, after = _scoped_versions(framework, change)
    after["pkg/__init__.py"] = "import helper\n"
    after["helper.py"] = "value = None\n"
    after["pkg/helper.py"] = ("from abc import ABCMeta\nfrom foreign_consumer import replacement\n"
                              "ABCMeta.__call__ = replacement\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    _unread(_compare(tmp_path, base, head, "--scope", "pkg/app"))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_above_scope_listed_unread_sibling_cannot_be_dropped_for_a_clean_provider(tmp_path, framework):
    before, after = _scoped_versions(framework, "added")
    after["pkg/__init__.py"] = "import helper\n"
    after["helper.py"] = "value = None\n"
    # Above-scope snapshot reads have their own 4 MiB bound. Existence comes
    # from ls-tree, not from whether this candidate's contents fit that bound.
    after["pkg/helper.py"] = "#" + "x" * (4 * 1024 * 1024) + "\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    _unread(_compare(tmp_path, base, head, "--scope", "pkg/app"))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("above", [False, True])
@pytest.mark.parametrize("imported,provider", [
    ("from os.path import join\nvalue = join('a', 'b')\n", "os.py"),
    ("from io import StringIO\nvalue = StringIO('value')\n", "io.py"),
])
def test_startup_import_closure_does_not_read_impossible_sibling_providers(tmp_path, framework, above, imported, provider):
    before, after = _scoped_versions(framework, "added") if above else _versions(framework, "direct", "added")
    after[provider] = ("this is not valid Python!\n" if above else
                       "from abc import ABCMeta\nfrom foreign_consumer import replacement\nABCMeta.__call__ = replacement\n")
    source = "pkg/__init__.py" if above else "app.py"
    after[source] = imported + after[source]
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", "pkg/app" if above else ".")
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == "added"



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("retained", [False, True])
def test_above_scope_src_provider_preserves_actual_data_and_namespace_roles(tmp_path, framework, retained):
    before, after = _scoped_versions(framework, "added")
    after["src/helper.py"] = ("from os import getenv as Carrier\n" if retained else "") + "value = None\n"
    after["pkg/__init__.py"] = ("import helper\nfrom foreign_consumer import consume\n"
                               + ("consume(helper)\n" if retained else "consume(helper.value)\n"))
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", "pkg/app")
    if retained:
        _unread(result)
    else:
        assert result["comparison_status"] == "compared"
        assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
        assert len(result["rows"]) == 1 and result["rows"][0]["change"] == "added"



@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_above_scope_linked_alternate_runner_cannot_borrow_a_clean_provider(tmp_path, framework):
    before, after = _scoped_versions(framework, "added")
    after["pkg/__init__.py"] = "import helper\n"
    after["helper.py"] = "value = None\n"
    after["pkg/mutator.py"] = ("from abc import ABCMeta\nfrom foreign_consumer import replacement\n"
                              "ABCMeta.__call__ = replacement\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, before)
    _commit(tmp_path, after)
    (tmp_path / "pkg/helper.py").symlink_to("mutator.py")
    _git(tmp_path, "add", "pkg/helper.py")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", "commit", "-qm", "linked alternate provider")
    head = _git(tmp_path, "rev-parse", "HEAD")
    _unread(_compare(tmp_path, base, head, "--scope", "pkg/app"))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_newly_discovered_above_runner_keeps_a_missing_relative_import_limit(tmp_path, framework):
    before, after = _scoped_versions(framework, "added")
    after["pkg/__init__.py"] = "import helper\n"
    after["helper.py"] = "value = None\n"
    after["pkg/helper.py"] = "from .missing import value\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    _unread(_compare(tmp_path, base, head, "--scope", "pkg/app"))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_expanded_above_closure_hands_every_in_scope_runner_to_original_census(tmp_path, framework, change):
    before, after = _scoped_versions(framework, change)
    after["pkg/__init__.py"] = "import app.helper\n"
    after["app/__init__.py"] = ""
    after["app/helper.py"] = "value = None\n"
    after["pkg/app/helper.py"] = ("from abc import ABCMeta\nfrom foreign_consumer import replacement\n"
                                  "ABCMeta.__call__ = replacement\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    _unread(_compare(tmp_path, base, head, "--scope", "pkg/app"))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_expanded_in_scope_ordinary_runner_has_its_own_original_census(tmp_path, framework):
    before, after = _scoped_versions(framework, "added")
    after["pkg/__init__.py"] = "import app.helper\n"
    after["app/__init__.py"] = ""
    after["app/helper.py"] = "value = None\n"
    after["pkg/app/helper.py"] = "value = None\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", "pkg/app")
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == "added"



@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_expanded_above_package_search_path_patch_keeps_a_named_limit(tmp_path, framework):
    before, after = _scoped_versions(framework, "added")
    after["pkg/__init__.py"] = "import helper\n"
    after["helper.py"] = "value = None\n"
    after["pkg/helper/__init__.py"] = "__path__.insert(0, 'opaque')\nfrom . import patches\n"
    after["pkg/helper/patches.py"] = "value = None\n"
    after["opaque/patches.py"] = ("from abc import ABCMeta\nfrom foreign_consumer import replacement\n"
                                  "ABCMeta.__call__ = replacement\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    _unread(_compare(tmp_path, base, head, "--scope", "pkg/app"))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("namespace,symbol", [("contextlib", "contextmanager"), ("dataclasses", "field"),
                                              ("alternate_exports", "Carrier")])
@pytest.mark.parametrize("route", ["direct", "namespace", "saved_rebound", "module_handle", "mutation", "projected_mutation"])
def test_namespace_portions_do_not_verify_an_unread_external_imported_handle(tmp_path, framework, namespace, symbol, route):
    before, after = _versions(framework, "direct", "added")
    after[f"{namespace}/unrelated.py"] = "value = None\n"
    usage = {
        "direct": f"from {namespace} import {symbol} as Carrier\nconsume(Carrier)\n",
        "namespace": f"import {namespace} as Carrier\nconsume(Carrier.{symbol})\n",
        "saved_rebound": f"from {namespace} import {symbol} as Carrier\nSaved = Carrier\nCarrier = None\nconsume(Saved)\n",
        "module_handle": f"import {namespace} as Carrier\nconsume(Carrier)\n",
        "mutation": f"from {namespace} import {symbol} as Carrier\nCarrier.changed = None\n",
        "projected_mutation": f"import {namespace} as Carrier\nCarrier.{symbol}.changed = None\n",
    }[route]
    after["app.py"] = "from foreign_consumer import consume\n" + usage + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("usage", [
    "from local_ns.data import value\nconsume(value)\n",
    "from local_ns import data\nconsume(data.value)\n",
    "import local_ns.data\nconsume(local_ns.data.value)\n",
    "import local_ns\nconsume(local_ns.data.value)\n",
])
def test_known_namespace_child_literal_preserves_actual_local_data(tmp_path, framework, usage):
    before, after = _versions(framework, "direct", "added")
    after["local_ns/data.py"] = "value = None\n"
    after["app.py"] = "from foreign_consumer import consume\n" + usage + after["app.py"]
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == "added"



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("imported", ["from os import getenv as Carrier", "import os as Carrier"])
@pytest.mark.parametrize("rebinding", ["Carrier = None", "del Carrier"])
def test_unused_local_import_rebinding_does_not_mutate_the_external_object(tmp_path, framework, imported, rebinding):
    before, after = _versions(framework, "direct", "added")
    after["app.py"] = imported + "\n" + rebinding + "\n" + after["app.py"]
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == "added"
