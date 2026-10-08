"""Credentials inside published host values stay withheld.

A permission rule, a hook command or an MCP argument is published in `diff`
rows, PR comments and saved baselines. Two credential shapes were published in
the clear: the token after an `Authorization: Bearer` header, because the
header pattern took the scheme word as the value, and the password in
`curl -u user:password`, which no pattern read.
"""

from __future__ import annotations

import pytest

from agents_shipgate.core.host_grants import _sanitize_sensitive_string

SECRET = "s3cr3tT0ken9876"


@pytest.mark.parametrize(
    "raw",
    [
        f"Authorization: Bearer {SECRET}",
        f"Bash(curl -H 'Authorization: Bearer {SECRET}' https://api.example.com)",
        f'Bash(curl -H "authorization: basic {SECRET}" https://api.example.com)',
        f"Bash(curl -H 'Proxy-Authorization: Token {SECRET}' https://api.example.com)",
        f"Bash(curl -u deploy:{SECRET} https://api.example.com)",
        f"Bash(curl --user deploy:{SECRET} https://api.example.com)",
        f"Bash(curl --user=deploy:{SECRET} https://api.example.com)",
        f"Bash(curl --proxy-user deploy:{SECRET} https://api.example.com)",
        f"Bash(wget --password={SECRET} https://api.example.com)",
        f"Bash(tool --token={SECRET})",
    ],
)
def test_the_credential_is_never_published(raw: str) -> None:
    published = _sanitize_sensitive_string(raw)
    assert SECRET not in published
    assert "<redacted>" in published


def test_the_user_name_and_rule_structure_survive() -> None:
    published = _sanitize_sensitive_string(f"Bash(curl -u deploy:{SECRET} https://api.example.com)")
    assert published.startswith("Bash(curl -u deploy:<redacted> ")
    assert published.endswith(")")


def test_a_header_keeps_its_name() -> None:
    assert _sanitize_sensitive_string(f"Authorization: Bearer {SECRET}") == "Authorization: <redacted>"


@pytest.mark.parametrize(
    "raw",
    ["Bash(sort -u data.txt)", "Bash(git push -u origin main)", "Bash(npm run build)"],
)
def test_an_unrelated_u_flag_keeps_its_argument(raw: str) -> None:
    assert _sanitize_sensitive_string(raw) == raw


def test_a_bare_bearer_mention_is_unchanged_apart_from_its_token() -> None:
    # The standalone pattern still redacts a token after a bare `Bearer`.
    assert _sanitize_sensitive_string(f"Bearer {SECRET}") == "Bearer <redacted>"
