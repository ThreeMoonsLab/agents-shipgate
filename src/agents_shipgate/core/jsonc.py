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
    if chars and chars[0] == "\ufeff":
        chars[0] = " "
    length = len(chars)
    index = 0
    in_string = False
    comma_at: int | None = None
    previous_token = ""
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
        if char == "/" and index + 1 < length and chars[index + 1] == "/":
            end = index + 2
            while end < length and chars[end] not in "\r\n":
                end += 1
            chars[index:end] = " " * (end - index)
            index = end
            continue
        if char == "/" and index + 1 < length and chars[index + 1] == "*":
            end = text.find("*/", index + 2)
            if end == -1:
                raise json.JSONDecodeError("Unterminated comment", text, index)
            for position in range(index, end + 2):
                if chars[position] not in "\r\n":
                    chars[position] = " "
            index = end + 2
            continue
        if char not in " \t\r\n":
            if comma_at is not None and char in "}]":
                chars[comma_at] = " "
            comma_at = index if char == "," and previous_token not in {"", "{", "["} else None
            previous_token = char
            in_string = char == '"'
        index += 1
    return json.loads("".join(chars))


def is_vscode_mcp_path(path: str) -> bool:
    """The one host file this repository reads as JSON with comments."""

    normalized = path.replace("\\", "/").removeprefix("./").casefold()
    return normalized == ".vscode/mcp.json" or normalized.endswith("/.vscode/mcp.json")
