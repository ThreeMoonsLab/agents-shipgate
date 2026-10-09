"""Field metadata and annotation value ownership."""

from __future__ import annotations

import pytest

from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_constructor_dependency_ownership import (
    _comparison,
    _unread,
    _versions,
)


@pytest.mark.parametrize("usage", [
    "saved = Field\n",
    "from shim import replacement\nreplacement(Field)\n",
    "def callback():\n    return None\ndata = Field(default_factory=callback)\n",
    "from shim import replacement\ndata = Field(default=replacement)\n",
    "options = {'default': None}\ndata = Field(**options)\n",
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_adk_field_value_exception_does_not_authorize_retention_or_callbacks(tmp_path, usage, change):
    before, after = _versions("adk", "direct", change)
    after["app.py"] = "from pydantic import Field\n" + usage + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("usage", [
    "from foreign_consumer import consume\nconsume(data)\n",
    "from foreign_consumer import consume\nconsume([data])\n",
    "metadata = data.metadata\n",
    "klass = data.__class__\n",
    "copied = data.copy()\n",
    "data.count(None)\n",
    "data.index(None)\n",
    "repr(data)\n",
    "len(data)\n",
    "str(data)\n",
    "alias = data\n",
    "def inspect_data():\n    return data\nfrom foreign_consumer import consume\nconsume(inspect_data())\n",
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_field_result_cannot_export_shared_metadata_or_borrow_container_reads(tmp_path, usage, change):
    before, after = _versions("adk", "direct", change)
    after["app.py"] = "from pydantic import Field\ndata = Field(default=None)\n" + usage + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_field_result_exported_by_an_imported_module_is_unread(tmp_path, change):
    before, after = _versions("adk", "direct", change)
    after["fields.py"] = "from pydantic import Field\ndata = Field(default=None)\n"
    after["bridge.py"] = "from fields import data\n"
    after["app.py"] = "import bridge\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("usage", [
    "Field(default=None)\n",
    "data = Field(default=None)\ndata\n",
    "data = Field(default=None)\nempty = data is None\n",
    "data = Field(default=None)\ndef inspect_data():\n    return data\n",
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_field_result_with_proven_inert_uses_preserves_changes(tmp_path, usage, change):
    before, after = _versions("adk", "direct", change)
    after["app.py"] = "from pydantic import Field\n" + usage + after["app.py"]
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == change



@pytest.mark.parametrize("prefix", [
    "from foreign_consumer import replacement\nField = replacement\n",
    "def Field(**kwargs):\n    return None\n",
    "Field = None\n",
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_field_call_exception_requires_one_canonical_identity(tmp_path, prefix, change):
    before, after = _versions("adk", "direct", change)
    after["app.py"] = "from pydantic import Field\n" + prefix + "data = Field(default=None)\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_field_factory_call_retains_a_named_helper_limit(tmp_path, change):
    before, after = _versions("adk", "direct", change)
    after["app.py"] = "from pydantic import Field\ndef create_field():\n    return Field(default=None)\ndata = create_field()\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_typing_extension_annotations_keep_an_explicit_namespace_boundary(tmp_path, framework, deferred, change):
    before, after = _versions(framework, "direct", change)
    prefix = "from __future__ import annotations\n" if deferred else ""
    after["app.py"] = prefix + "from typing_extensions import Any\ndef separate(value: Any):\n    return None\n" + after["app.py"]
    result = _comparison(tmp_path, before, after)
    if deferred:
        assert result["comparison_status"] == "compared"
        assert not result["head"]["coverage_gaps"]
        assert len(result["rows"]) == 1 and result["rows"][0]["change"] == change
    else:
        _unread(result)
