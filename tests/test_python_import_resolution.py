"""Repository-local import resolution (#864): what it follows, and where it stops.

Every negative case has to end in a named reason — never a guess, and never a
silent empty answer a caller could read as "no such tool".
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from agents_shipgate.inputs import python_imports as imports
from agents_shipgate.inputs.python_imports import ImportResolver


def _tree(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _resolve(root: Path, entry: str, reference: str) -> imports.Resolution:
    resolver = ImportResolver(root)
    path = root / entry
    text = path.read_text()
    module = resolver.entry(path, ast.parse(text), text)
    assert module is not None
    return resolver.resolve(module, reference)


def _definition(resolution: imports.Resolution) -> str:
    assert resolution.resolved, (resolution.reason, resolution.detail)
    assert resolution.module is not None and resolution.definition is not None
    return f"{resolution.module.ref}:{resolution.definition.lineno}"


def test_relative_module_import_and_qualified_function(tmp_path):
    _tree(
        tmp_path,
        {
            "agent/__init__.py": "",
            "agent/agent.py": "from . import memory_bank\n",
            "agent/memory_bank.py": "import os\n\ndef remember(fact: str) -> str:\n    return fact\n",
        },
    )
    resolution = _resolve(tmp_path, "agent/agent.py", "memory_bank.remember")
    assert _definition(resolution) == "agent/memory_bank.py:3"
    evidence = resolution.evidence()
    assert [step["path"] for step in evidence["steps"]] == [
        "agent/agent.py",
        "agent/__init__.py",
        "agent/memory_bank.py",
    ]
    # Every module the chain read is named with its digest.
    assert {item["path"] for item in evidence["inputs"]} == {
        "agent/agent.py",
        "agent/__init__.py",
        "agent/memory_bank.py",
    }
    assert all(len(item["sha256"]) == 64 for item in evidence["inputs"])


def test_package_reexport_chain_reaches_the_definition(tmp_path):
    _tree(
        tmp_path,
        {
            "agents/checkout.py": "from ..tools.shop import add_to_cart\n",
            "tools/shop/__init__.py": "from .cart import add_to_cart, view_cart\n",
            "tools/shop/cart.py": "def view_cart():\n    pass\n\ndef add_to_cart(item: str):\n    pass\n",
        },
    )
    resolution = _resolve(tmp_path, "agents/checkout.py", "add_to_cart")
    assert _definition(resolution) == "tools/shop/cart.py:4"


def test_absolute_import_from_the_scope_root(tmp_path):
    _tree(
        tmp_path,
        {
            "adk/endor_oss/agent.py": "from service.oss.adk_tools import package_risk\n",
            "service/__init__.py": "",
            "service/oss/__init__.py": "",
            "service/oss/adk_tools.py": "def package_risk(purl: str) -> dict:\n    return {}\n",
        },
    )
    resolution = _resolve(tmp_path, "adk/endor_oss/agent.py", "package_risk")
    assert _definition(resolution) == "service/oss/adk_tools.py:1"


def test_scope_root_package_name_resolves_inside_the_scope(tmp_path):
    root = tmp_path / "app"
    _tree(
        root,
        {
            "__init__.py": "",
            "agents/sub.py": "from app.agents.tools import search\n",
            "agents/tools.py": "def search(q: str):\n    pass\n",
        },
    )
    assert _definition(_resolve(root, "agents/sub.py", "search")) == "agents/tools.py:1"


def test_alias_import_and_module_level_alias_reach_one_definition(tmp_path):
    _tree(
        tmp_path,
        {
            "agent.py": (
                "from tools import read_patient_document as read_doc\n"
                "from tools import read_document\n"
                "import tools as t\n"
            ),
            "tools.py": "def read_patient_document(doc_id: str):\n    pass\n\nread_document = read_patient_document\n",
        },
    )
    targets = {
        _definition(_resolve(tmp_path, "agent.py", reference))
        for reference in ("read_doc", "read_document", "t.read_patient_document")
    }
    assert targets == {"tools.py:1"}


def test_type_checking_self_import_is_the_submodule(tmp_path):
    _tree(
        tmp_path,
        {
            "pkg/__init__.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n"
                "    from . import memory  # noqa: F401\n"
            ),
            "pkg/agent.py": "from . import memory\n",
            "pkg/memory.py": "def recall():\n    pass\n",
        },
    )
    assert _definition(_resolve(tmp_path, "pkg/agent.py", "memory.recall")) == "pkg/memory.py:1"


@pytest.mark.parametrize(
    "files, entry, reference, reason",
    [
        pytest.param(
            {"agent.py": "from missing_module import tool\n"},
            "agent.py",
            "tool",
            imports.MODULE_NOT_FOUND,
            id="missing_module",
        ),
        pytest.param(
            {"agent.py": "from ..outside import tool\n"},
            "agent.py",
            "tool",
            imports.OUTSIDE_SCOPE,
            id="relative_import_above_scope",
        ),
        pytest.param(
            {
                "app/agent.py": "from tools import tool\n",
                "app/tools.py": "def tool():\n    pass\n",
                "tools.py": "def tool():\n    pass\n",
            },
            "app/agent.py",
            "tool",
            imports.AMBIGUOUS_MODULE,
            id="ambiguous_module_root",
        ),
        pytest.param(
            {
                "agent.py": "from tools import tool\n",
                "tools.py": "def tool():\n    pass\n\ntool = wrap(tool)\n",
            },
            "agent.py",
            "tool",
            imports.REBOUND_NAME,
            id="reassigned_in_defining_module",
        ),
        pytest.param(
            {
                "agent.py": "from a import tool\nfrom b import tool\n",
                "a.py": "def tool():\n    pass\n",
                "b.py": "def tool():\n    pass\n",
            },
            "agent.py",
            "tool",
            imports.REBOUND_NAME,
            id="shadowed_in_importing_module",
        ),
        pytest.param(
            {
                "agent.py": "try:\n    from tools import tool\nexcept ImportError:\n    pass\n",
                "tools.py": "def tool():\n    pass\n",
            },
            "agent.py",
            "tool",
            imports.CONDITIONAL_BINDING,
            id="conditional_import",
        ),
        pytest.param(
            {
                "pkg/__init__.py": "",
                "pkg/agent.py": "from .a import tool\n",
                "pkg/a.py": "from .b import tool\n",
                "pkg/b.py": "from .a import tool\n",
            },
            "pkg/agent.py",
            "tool",
            imports.IMPORT_CYCLE,
            id="import_cycle",
        ),
        pytest.param(
            {
                "agent.py": "from tools import tool\n",
                "tools.py": "from helpers import *\n\ndef tool():\n    pass\n",
            },
            "agent.py",
            "tool",
            imports.STAR_IMPORT,
            id="wildcard_in_defining_module",
        ),
        pytest.param(
            {
                "agent.py": "from tools import Tool\n",
                "tools.py": "class Tool:\n    pass\n",
            },
            "agent.py",
            "Tool",
            imports.NOT_A_FUNCTION,
            id="class_not_function",
        ),
        pytest.param(
            {"agent.py": "from tools import tool\n", "tools.py": "x = 1\n"},
            "agent.py",
            "tool",
            imports.NAME_NOT_DEFINED,
            id="name_not_defined",
        ),
        pytest.param(
            {"agent.py": "from tools import tool\n", "tools.py": "def broken(:\n"},
            "agent.py",
            "tool",
            imports.UNREADABLE_MODULE,
            id="unparseable_module",
        ),
        pytest.param(
            {"agent.py": "def tool():\n    pass\n"},
            "agent.py",
            "undefined_name",
            imports.NOT_BOUND,
            id="not_bound_here",
        ),
    ],
)
def test_unresolved_reference_names_its_reason(tmp_path, files, entry, reference, reason):
    _tree(tmp_path, files)
    resolution = _resolve(tmp_path, entry, reference)
    assert not resolution.resolved
    assert resolution.reason == reason
    assert resolution.detail
    assert resolution.evidence()["reason"] == reason


def test_exact_spelling_is_required_even_on_a_case_folding_filesystem(tmp_path):
    _tree(tmp_path, {"agent.py": "from Tools import tool\n", "tools.py": "def tool():\n    pass\n"})
    resolution = _resolve(tmp_path, "agent.py", "tool")
    assert resolution.reason == imports.MODULE_NOT_FOUND


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_linked_module_is_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tools.py").write_text("def tool():\n    pass\n")
    scope = tmp_path / "scope"
    _tree(scope, {"agent.py": "from tools import tool\n"})
    os.symlink(outside / "tools.py", scope / "tools.py")
    resolution = _resolve(scope, "agent.py", "tool")
    assert resolution.reason == imports.LINKED_MODULE


def test_module_bound_is_a_named_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(imports, "MAX_MODULES", 2)
    _tree(
        tmp_path,
        {
            "agent.py": "from a import tool\n",
            "a.py": "from b import tool\n",
            "b.py": "from c import tool\n",
            "c.py": "def tool():\n    pass\n",
        },
    )
    assert _resolve(tmp_path, "agent.py", "tool").reason == imports.RESOLUTION_LIMIT


def test_resolution_never_executes_the_inspected_code(tmp_path):
    marker = tmp_path / "executed"
    _tree(
        tmp_path,
        {
            "agent.py": "from tools import tool\n",
            "tools.py": (
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('ran')\n"
                "raise SystemExit(3)\n"
                "def tool():\n    pass\n"
            ),
        },
    )
    before = set(sys.modules)
    assert _definition(_resolve(tmp_path, "agent.py", "tool")) == "tools.py:4"
    assert not marker.exists()
    assert "tools" not in set(sys.modules) - before
