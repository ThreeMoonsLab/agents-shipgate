"""Static injection claims stay within the pinned common framework profile.

SDK probes below call only the installed framework's signature utility on
functions authored in this test. No scanned server is imported or executed.
"""

import importlib
import importlib.metadata
import importlib.util
import typing

import pytest

from agents_shipgate.inputs.mcp_idioms import scan_source

#: Where the installed SDK keeps the signature utility these probes compare
#: against. FastMCP was renamed to MCPServer in mcp 2.x, and the `[mcp]` extra
#: requires `mcp>=2.1.1,<3`, so importing only the 1.x path skipped on every
#: supported install: the cross-checks against the real SDK never ran (#716).
_SDK_LOCATIONS = (
    ("mcp.server.mcpserver.utilities.context_injection", "mcp.server.mcpserver"),  # mcp 2.x
    ("mcp.server.fastmcp.utilities.context_injection", "mcp.server.fastmcp"),  # mcp 1.x
)


def _installed_sdk():
    """The installed SDK's injection utility and its ``Context`` class.

    Skips only when ``mcp`` itself is absent. An installed SDK exposing
    neither location is an API move this file has not followed, and skipping
    it would hide the gap exactly the way the 1.x-only import did, so that
    fails instead.
    """

    if importlib.util.find_spec("mcp") is None:
        pytest.skip(
            "the mcp SDK is not installed; install agents-shipgate[mcp] to run "
            "the SDK cross-checks"
        )
    for utility, package in _SDK_LOCATIONS:
        try:
            module = importlib.import_module(utility)
        except ModuleNotFoundError:
            continue
        return module, importlib.import_module(package).Context
    try:
        version = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    pytest.fail(
        f"mcp {version} is installed but exposes no context-injection utility at a "
        "known location; extend _SDK_LOCATIONS rather than letting these cross-checks skip"
    )


def test_an_absent_sdk_skips_and_names_the_extra(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None)
    with pytest.raises(pytest.skip.Exception, match=r"agents-shipgate\[mcp\]"):
        _installed_sdk()


def test_an_installed_sdk_at_an_unknown_location_fails_instead_of_skipping(monkeypatch):
    """The skip that hid #716 must not come back with the next rename."""

    def missing(name, *args, **kwargs):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
    monkeypatch.setattr(importlib, "import_module", missing)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9")
    with pytest.raises(pytest.fail.Exception, match="no context-injection utility"):
        _installed_sdk()


def _parameters(signature, *, imports="", definitions="", family="mcp.server.fastmcp"):
    source = (
        f"from {family} import FastMCP, Context\n"
        "from typing import Optional, Union, Annotated\n"
        f"{imports}\n{definitions}\n"
        "server = FastMCP('fixture')\n"
        "@server.tool()\n"
        f"def lookup({signature}:\n    return 'fixture'\n"
    )
    result = scan_source(source, "python")
    assert not result.anomalies
    assert len(result.sites) == 1 and result.sites[0].name == "lookup"
    return {parameter.name: parameter.injection for parameter in result.sites[0].parameters}


@pytest.mark.parametrize("family", ["mcp.server.fastmcp", "fastmcp"])
@pytest.mark.parametrize("annotation", ["Context", "Context | None", "Optional[Context]", '"Context"', "Annotated[Context, 'description']"])
def test_single_canonical_context_is_injected(family, annotation):
    assert _parameters(f"query: str, ctx: {annotation}) -> str", family=family) == {
        "query": "caller_supplied", "ctx": "framework_injected",
    }


@pytest.mark.parametrize("family", ["mcp.server.fastmcp", "fastmcp"])
def test_first_matching_context_is_the_only_excluded_parameter(family):
    assert _parameters("first: Context, second: Context) -> str", family=family) == {
        "first": "framework_injected", "second": "caller_supplied",
    }


@pytest.mark.parametrize("annotation", [
    "list[Context]", "Context[object, None]", "list[list[Context]]",
    "Optional[Context[object, None]]",
])
@pytest.mark.parametrize("family", ["mcp.server.fastmcp", "fastmcp"])
def test_generic_context_has_an_explicit_local_limit(annotation, family):
    assert _parameters(f"ctx: {annotation}) -> str", family=family)["ctx"] == "unresolved"


@pytest.mark.parametrize("signature", [
    "ctx: Context, payload: Missing) -> str",
    "ctx: Context) -> Missing",
    "ctx: Context, *values: Missing) -> str",
    "ctx: Context, **values: Missing) -> str",
    "ctx: Context, payload: list[Missing]) -> str",
    "ctx: Context, payload: Annotated[str, missing_metadata]) -> str",
    "ctx: Context, payload: Annotated[str, Missing()]) -> str",
    "*values: Context, ctx: Context) -> str",
])
def test_unresolved_whole_signature_does_not_hide_context(signature):
    assert _parameters(signature)["ctx"] == "unresolved"


@pytest.mark.parametrize("annotation", [
    "List[str, int]", "List[()]", "Set[str, int]", "FrozenSet[str, int]",
    "Dict[str]", "Dict[str, str, int]", "Union[()]",
])
def test_invalid_typing_arity_cannot_hide_context(annotation):
    sdk, Context = _installed_sdk()

    # Authored test annotations only: get_type_hints must reach the same
    # semantic refusal that stops the real SDK's whole-signature resolver.
    def specimen(ctx, payload):
        return ""

    specimen.__annotations__ = {"ctx": Context, "payload": f"typing.{annotation}", "return": str}
    with pytest.raises(TypeError):
        typing.get_type_hints(specimen)
    assert sdk.find_context_parameter(specimen) is None
    assert _parameters(
        f"ctx: Context, payload: {annotation}) -> str",
        imports="from typing import List, Set, FrozenSet, Dict",
    ) == {"ctx": "unresolved", "payload": "unresolved"}


@pytest.mark.parametrize("annotation", [
    "List[str]", "Set[str]", "FrozenSet[str]", "Dict[str, int]",
    "Union[str, int]", "list[str, int]",
])
def test_supported_container_arity_keeps_context_injection(annotation):
    assert _parameters(
        f"ctx: Context, payload: {annotation}) -> str",
        imports="from typing import List, Set, FrozenSet, Dict",
    ) == {"ctx": "framework_injected", "payload": "caller_supplied"}


@pytest.mark.parametrize("imports, annotation", [
    ("from acme.models import Context as ExternalContext", "ExternalContext"),
    ("import acme.models", "acme.models.Context"),
    ("from .models import Context as ExternalContext", "ExternalContext"),
])
def test_external_class_path_does_not_establish_caller_ownership(imports, annotation):
    assert _parameters(f"value: {annotation}) -> str", imports=imports)["value"] == "unresolved"


def test_application_model_and_ordinary_context_name_remain_caller_inputs():
    assert _parameters(
        "context: str, payload: ApplicationContext) -> str",
        imports="from pydantic import BaseModel",
        definitions="class ApplicationContext(BaseModel):\n    account_id: str",
    ) == {"context": "caller_supplied", "payload": "caller_supplied"}


def test_proven_local_context_subclass_preserves_injection():
    assert _parameters(
        "ctx: Reporting) -> str", definitions="class Reporting(Context):\n    pass",
    ) == {"ctx": "framework_injected"}


@pytest.mark.parametrize("definition", [
    "@decorate\nclass Reporting(Context):\n    pass",
    "class Reporting(Context, metaclass=Custom):\n    pass",
    "class Reporting(Context, Missing):\n    pass",
    "class Reporting(Missing, Context):\n    pass",
])
def test_unresolved_local_class_construction_cannot_hide_a_parameter(definition):
    assert _parameters("ctx: Reporting) -> str", definitions=definition) == {"ctx": "unresolved"}


def test_installed_sdk_whole_signature_and_generic_semantics():
    sdk, Context = _installed_sdk()

    def direct(ctx: Context) -> str:
        return ""

    def two(first: Context, second: Context) -> str:
        return ""

    def generic(holder: list[Context]) -> str:
        return ""

    def parameterized(ctx: Context[object, None]) -> str:
        return ""

    def unresolved(ctx: Context, payload: "MissingFixtureType") -> str:  # noqa: F821
        return ""

    assert sdk.find_context_parameter(direct) == "ctx"
    assert sdk.find_context_parameter(two) == "first"
    assert sdk.find_context_parameter(generic) == "holder"
    # The SDK Context is a Pydantic generic: parameterization materializes a class.
    assert sdk.find_context_parameter(parameterized) == "ctx"
    assert sdk.find_context_parameter(unresolved) is None
