"""Regression coverage for source encoding and static Python parsing."""

import ast
import warnings
from pathlib import Path

import pytest

from agents_shipgate.core.python_source import decode_python_source
from agents_shipgate.inputs.common import load_structured_file, load_structured_file_with_positions
from agents_shipgate.inputs.mcp_manifest import load_codex_config_mcp_sources
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.schemas.manifest import ToolSourceConfig


def test_structured_reader_accepts_bom_and_uses_declared_format(tmp_path):
    json_path = tmp_path / "input.json"
    json_path.write_bytes(b'\xef\xbb\xbf{"enabled":true}')
    yaml_path = tmp_path / "input.yaml"
    yaml_path.write_text("{enabled: true}", encoding="utf-8")
    assert load_structured_file(json_path) == load_structured_file(yaml_path) == {"enabled": True}
    odd_suffix = tmp_path / "input.txt"
    odd_suffix.write_text('{\n\t"maximum": 1e6\n}', encoding="utf-8")
    assert load_structured_file(odd_suffix) == {"maximum": 1e6}
    assert load_structured_file_with_positions(odd_suffix)[0] == {"maximum": 1e6}

    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_bytes(b'\xef\xbb\xbf[mcp_servers.lookup]\nurl = "https://example.test/mcp"\n')
    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    assert [tool.name for source in loaded for tool in source.tools] == ["lookup.*"]


@pytest.mark.parametrize("codec", ["rot13", "base64", "hex"])
def test_python_encoding_cookie_requires_a_text_codec(codec):
    with pytest.raises(UnicodeError, match="not a text encoding"):
        decode_python_source(f"# coding: {codec}\nvalue = 1\n".encode())


def test_static_python_scan_loads_a_tool_with_a_deep_call_chain(tmp_path):
    path = tmp_path / "agent.py"
    path.write_text(
        "from agents import Agent, function_tool\n"
        "@function_tool\n"
        "def normalize(value: str) -> str:\n"
        f"    return value{'.strip()' * 600}\n"
        "agent = Agent(name='normalize', tools=[normalize])\n",
        encoding="utf-8",
    )
    source = ToolSourceConfig(id="normalize", type="openai_agents_sdk", path=path.name)
    loaded = load_openai_sdk_static_tools(source, None, tmp_path)
    assert [tool.name for tool in loaded.tools] == ["normalize"]


def test_static_python_scan_does_not_inherit_interpreter_warning_filters(tmp_path, capsys):
    path = tmp_path / "agent.py"
    path.write_text(
        'from agents import function_tool\n@function_tool\n'
        'def lookup() -> str:\n    return "\\d"\n',
        encoding="utf-8",
    )
    source = ToolSourceConfig(id="lookup", type="openai_agents_sdk", path=path.name)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        loaded = load_openai_sdk_static_tools(source, None, tmp_path)
    assert [tool.name for tool in loaded.tools] == ["lookup"]
    assert not emitted
    assert not capsys.readouterr().err


def test_production_static_parsing_uses_the_shared_python_parser():
    root = Path(__file__).parents[1] / "src" / "agents_shipgate"
    direct_parsers = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "ast.parse" not in text and "from ast import" not in text:
            continue
        tree = ast.parse(text)
        if any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and (node.value.id, node.attr) == ("ast", "parse")
            or isinstance(node, ast.ImportFrom)
            and node.module == "ast"
            and any(alias.name == "parse" for alias in node.names)
            for node in ast.walk(tree)
        ):
            direct_parsers.append(path.relative_to(root).as_posix())
    assert direct_parsers == ["core/python_source.py"]
