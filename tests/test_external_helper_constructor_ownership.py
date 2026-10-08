"""A source helper preserves only the existing ordinary external call boundary."""

import pytest

from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_constructor_dependency_ownership import _comparison, _versions


def _with_helper(framework, header, function, extra=None):
    before, after = _versions(framework, "direct", "added")
    before["support.py"] = "def send(value):\n    return None\n"
    after["support.py"] = header + function
    after["tools.py"] = "from support import send\n" + after["tools.py"].replace("def write(value: int):\n    return None", "def write(value: int):\n    return send(value)")
    assert "return send(value)" in after["tools.py"]
    after.update(extra or {})
    return before, after


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("header,expression", [
    ("import requests\n", 'requests.get("https://example.test", params={"value": value})'),
    ("import requests as http\n", 'http.get("https://example.test").json()'),
    ("from requests import get as fetch\n", 'fetch("https://example.test").json()'),
    ("", 'fetch("https://example.test").json()'),
    ("import requests\n", 'requests.get("https://example.test").json().copy()'),
])
def test_stable_absolute_external_call_results_keep_the_inline_boundary(tmp_path, framework, header, expression):
    local_import = "    from requests import get as fetch\n" if not header else ""
    function = "def send(value):\n" + local_import + "    return " + expression + "\n"
    result = _comparison(tmp_path, *_with_helper(framework, header, function))
    assert result["comparison_status"] == "compared"
    assert not result["head"]["coverage_gaps"]
    assert [(row["tool"], row["change"]) for row in result["rows"]] == [("write", "added")]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("header,function,extra", [
    ("import requests\n", "def send(value):\n    return requests.get\n", {}),
    ("import requests\n", "def send(value):\n    return requests.response\n", {}),
    ("import requests\n", "def send(value):\n    response = requests.get(value)\n    return response\n", {}),
    ("import requests\n", "def send(value):\n    return requests.get(value)()\n", {}),
    ("import requests\n", "def send(value):\n    return requests.get(value).payload.json()\n", {}),
    ("import requests\n", "def send(value):\n    return requests['get'](value)\n", {}),
    ("import requests\n", "def send(value):\n    return getattr(requests, 'get')(value)\n", {}),
    ("import requests\n", "def send(requests):\n    return requests(requests)\n", {}),
    ("import requests\n", "def send(value):\n    requests = value\n    return requests(value)\n", {}),
    ("if choice:\n    import requests\n", "def send(value):\n    return requests.get(value)\n", {}),
    ("import requests\nfrom vendor import requests\n", "def send(value):\n    return requests.get(value)\n", {}),
    ("from .vendor import fetch\n", "def send(value):\n    return fetch(value)\n", {}),
    ("import requests\n", "def send(value):\n    return missing(value)\n", {}),
    ("import requests\nclass Receiver:\n    pass\n", "def send(value):\n    return Receiver()\n", {}),
    ("import pydantic\n", "def send(value):\n    return pydantic.create_model('Held')\n", {}),
    ("import requests\n", "def send(value):\n    return requests.get(value)\n", {"requests.py": "from vendor import get\n"}),
    ("import requests\nfrom abc import ABCMeta\n", "def send(value):\n    return requests.get(ABCMeta)\n", {}),
    ("import requests\n", "async def send(value):\n    return requests.get(value)\n", {}),
    ("import requests\n", "def send(value):\n    yield requests.get(value)\n", {}),
    ("import requests\n", "def send(value):\n    return requests.get(value).a().b().c().d()\n", {}),
    ("import requests.client\n", "def send(value):\n    return requests.client.get(value)\n", {"requests/__init__.py": "# An actual local regular provider.\n"}),
])
def test_helper_call_parity_grants_no_other_handle_or_receiver_role(tmp_path, framework, header, function, extra):
    result = _comparison(tmp_path, *_with_helper(framework, header, function, extra))
    assert result["comparison_status"] == "partial"
    assert result["head"]["coverage_gaps"]
    assert all(row["change"] == "not_established" for row in result["rows"])


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("route", ["helper", "inline"])
@pytest.mark.parametrize("expression", [
    "requests.get(value).__class__()",
    "requests.get(value).__getattribute__('__class__')",
    "requests.get(value).__getattr__('__class__')",
    "requests.get(value).json().__class__()",
    "requests.get(requests.get(value).__class__).json()",
])
def test_call_rooted_reflection_cannot_borrow_ordinary_call_parity(tmp_path, framework, route, expression):
    before, after = _with_helper(framework, "import requests\n", "def send(value):\n    return " + expression + "\n")
    if route == "inline":
        after["tools.py"] = "import requests\n" + after["tools.py"].replace("return send(value)", "return " + expression)
        after["support.py"] = before["support.py"]
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "partial"
    assert result["head"]["coverage_gaps"]
    assert all(row["change"] == "not_established" for row in result["rows"])
