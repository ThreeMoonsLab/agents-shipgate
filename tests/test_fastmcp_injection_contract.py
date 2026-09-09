"""Static injection claims stay within the pinned common framework profile.

SDK probes below call only the installed framework's signature utility on
functions authored in this test. No scanned server is imported or executed.
"""

import pytest

from agents_shipgate.inputs.mcp_idioms import scan_source


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
    sdk = pytest.importorskip("mcp.server.fastmcp.utilities.context_injection")
    from mcp.server.fastmcp import Context

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
