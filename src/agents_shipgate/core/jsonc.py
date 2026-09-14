"""JSON with comments, the dialect VS Code reads its `mcp.json` in (#659)."""

from __future__ import annotations

import json
from typing import Any


def loads_jsonc(text: str) -> Any:
    """Parse JSON that may carry `//` and `/* */` comments and trailing commas.

    Comments and trailing commas are blanked outside string literals only, so a
    `//` inside a URL is kept, and offsets are preserved so a parse error still
    points at the original text. Anything else JSON rejects is still rejected.
    """

    chars = list(text)
    length = len(chars)
    index = 0
    in_string = False
    while index < length:
        char = chars[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
        elif char == "/" and index + 1 < length and chars[index + 1] == "/":
            end = text.find("\n", index)
            end = length if end == -1 else end
            chars[index:end] = " " * (end - index)
            index = end
            continue
        elif char == "/" and index + 1 < length and chars[index + 1] == "*":
            end = text.find("*/", index + 2)
            if end == -1:
                raise json.JSONDecodeError("Unterminated comment", text, index)
            for position in range(index, end + 2):
                if chars[position] != "\n":
                    chars[position] = " "
            index = end + 2
            continue
        elif char == ",":
            following = index + 1
            while following < length and chars[following] in " \t\r\n":
                following += 1
            if following < length and chars[following] in "}]":
                chars[index] = " "
        index += 1
    return json.loads("".join(chars))


def is_vscode_mcp_path(path: str) -> bool:
    """The one host file this repository reads as JSON with comments."""

    normalized = path.replace("\\", "/").removeprefix("./").casefold()
    return normalized == ".vscode/mcp.json" or normalized.endswith("/.vscode/mcp.json")
