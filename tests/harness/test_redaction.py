"""Redaction guarantee tests.

Pins the commit-#94 contract: no secret matching Shipgate's secret patterns
ever appears in a redacted artifact, a scorecard JSON, or a public CSV row.
"""
from __future__ import annotations

from pathlib import Path

from harness.adoption.observer.redact import RedactionConfig, redact_string, redact_tree


SK_TOKEN = "sk-proj-test1234567890abcdef00"
AWS_TOKEN = "AKIAIOSFODNN7EXAMPLE"


def test_sk_token_redacted_in_string() -> None:
    out = redact_string(f"My key is {SK_TOKEN} please be careful", config=RedactionConfig())
    assert SK_TOKEN not in out
    assert "[REDACTED:" in out


def test_aws_key_redacted_in_string() -> None:
    out = redact_string(f"AWS={AWS_TOKEN}", config=RedactionConfig())
    assert AWS_TOKEN not in out
    assert "[REDACTED:" in out


def test_env_harness_literal_secret_redacted() -> None:
    config = RedactionConfig(extra_literal_secrets=("super-secret-token-value",))
    out = redact_string("token is super-secret-token-value", config=config)
    assert "super-secret-token-value" not in out
    assert "[REDACTED:env_secret]" in out


def test_redact_tree_round_trip(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "transcript.jsonl").write_text(
        f'{{"text": "leaked {SK_TOKEN}"}}\n', encoding="utf-8"
    )
    (raw / "commands.jsonl").write_text(
        f'{{"command": "echo {AWS_TOKEN}"}}\n', encoding="utf-8"
    )
    redacted = tmp_path / "redacted"
    redact_tree(raw, redacted, config=RedactionConfig())
    for file in redacted.rglob("*"):
        if file.is_file():
            content = file.read_text(encoding="utf-8")
            assert SK_TOKEN not in content, f"SK token leaked in {file}"
            assert AWS_TOKEN not in content, f"AWS token leaked in {file}"
