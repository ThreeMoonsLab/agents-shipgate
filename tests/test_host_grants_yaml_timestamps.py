"""#1100: Deterministic serialization and hashing of YAML date and datetime objects.

When PyYAML parses unquoted timestamps or dates in host configuration files
(e.g., GitHub Actions workflows with `not-before: 2023-07-06T02:54:32Z`), they
are loaded as `datetime.datetime` or `datetime.date` objects.

These tests pin the contract:
1. YAML timestamp scalars and date-only scalars are deterministically normalized
   to their ISO-8601 string representations.
2. Canonical JSON serialization and redaction preserve timezone offsets.
3. Secret-redaction markers take precedence over date normalization.
4. Ordinary JSON-compatible inputs retain their existing hashes unchanged.
5. Unsupported arbitrary objects still raise a clear TypeError rather than
   being silently stringified by a broad fallback.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.host_grants import (
    _canonical,
    _redact_secret_values,
    redacted_config_sha256,
)


def _expected_sha256(data: Any) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_yaml_timestamp_parsed_as_datetime() -> None:
    raw_yaml = "not-before: 2023-07-06T02:54:32Z\n"
    parsed = yaml.safe_load(raw_yaml)
    assert isinstance(parsed["not-before"], datetime.datetime)

    digest = redacted_config_sha256(parsed)
    expected = _expected_sha256({"not-before": "2023-07-06T02:54:32+00:00"})
    assert digest == expected
    assert digest == "2e5bcf7346b697b42364fa1547a3dc4279074a2f5c551c955b6ad56d2732898f"


def test_yaml_date_only_scalar_parsed_as_date() -> None:
    raw_yaml = "effective-date: 2023-07-06\n"
    parsed = yaml.safe_load(raw_yaml)
    assert type(parsed["effective-date"]) is datetime.date

    digest = redacted_config_sha256(parsed)
    expected = _expected_sha256({"effective-date": "2023-07-06"})
    assert digest == expected
    assert digest == "1908e74b420e4b2ac4a2bc71a33294f8769a4b5b1f662969d62516b90ebabb5f"


def test_utc_and_non_utc_timezone_offsets() -> None:
    dt_utc = datetime.datetime(2023, 7, 6, 2, 54, 32, tzinfo=datetime.UTC)
    dt_p2 = datetime.datetime(
        2023, 7, 6, 2, 54, 32, tzinfo=datetime.timezone(datetime.timedelta(hours=2))
    )
    dt_m5 = datetime.datetime(
        2023, 7, 6, 2, 54, 32, tzinfo=datetime.timezone(datetime.timedelta(hours=-5))
    )
    dt_naive = datetime.datetime(2023, 7, 6, 2, 54, 32)

    assert _canonical({"ts": dt_utc}) == '{"ts":"2023-07-06T02:54:32+00:00"}'
    assert _canonical({"ts": dt_p2}) == '{"ts":"2023-07-06T02:54:32+02:00"}'
    assert _canonical({"ts": dt_m5}) == '{"ts":"2023-07-06T02:54:32-05:00"}'
    assert _canonical({"ts": dt_naive}) == '{"ts":"2023-07-06T02:54:32"}'

    # Different timezones must produce distinct hashes
    hashes = {
        redacted_config_sha256({"ts": dt_utc}),
        redacted_config_sha256({"ts": dt_p2}),
        redacted_config_sha256({"ts": dt_m5}),
        redacted_config_sha256({"ts": dt_naive}),
    }
    assert len(hashes) == 4


def test_deterministic_sha256_for_repeated_equivalent_inputs() -> None:
    data1 = yaml.safe_load("steps:\n  - with:\n      not-before: 2023-07-06T02:54:32Z\n")
    data2 = yaml.safe_load("steps:\n  - with:\n      not-before: 2023-07-06T02:54:32Z\n")

    h1 = redacted_config_sha256(data1)
    h2 = redacted_config_sha256(data2)
    assert h1 == h2


def test_unchanged_hashes_for_ordinary_json_compatible_inputs() -> None:
    ordinary_dict = {"key": "value"}
    expected_dict_sha = hashlib.sha256(b'{"key":"value"}').hexdigest()
    assert expected_dict_sha == "e43abcf3375244839c012f9633f95862d232a95b00d5bc7348b3098b9fed7f32"
    assert redacted_config_sha256(ordinary_dict) == expected_dict_sha

    complex_input = {
        "alpha": "string",
        "beta": 123,
        "gamma": [True, False, None],
        "delta": {"nested": "value"},
    }
    expected_complex = _expected_sha256(complex_input)
    assert redacted_config_sha256(complex_input) == expected_complex


def test_nested_mappings_and_lists_containing_date_and_datetime() -> None:
    nested = {
        "workflow": {
            "jobs": [
                {
                    "step": 1,
                    "not-before": datetime.datetime(2023, 7, 6, 2, 54, 32, tzinfo=datetime.UTC),
                },
                {
                    "step": 2,
                    "schedule": [
                        datetime.date(2023, 7, 6),
                        datetime.date(2023, 7, 7),
                    ],
                },
            ]
        }
    }
    redacted = _redact_secret_values(nested)
    assert redacted["workflow"]["jobs"][0]["not-before"] == "2023-07-06T02:54:32+00:00"
    assert redacted["workflow"]["jobs"][1]["schedule"] == ["2023-07-06", "2023-07-07"]

    digest = redacted_config_sha256(nested)
    expected = _expected_sha256(
        {
            "workflow": {
                "jobs": [
                    {
                        "not-before": "2023-07-06T02:54:32+00:00",
                        "step": 1,
                    },
                    {
                        "schedule": ["2023-07-06", "2023-07-07"],
                        "step": 2,
                    },
                ]
            }
        }
    )
    assert digest == expected


def test_secret_redaction_takes_precedence_over_date_serialization() -> None:
    sensitive_data = {
        "api_key": datetime.datetime(2023, 7, 6, 2, 54, 32, tzinfo=datetime.UTC),
        "secret_token": "deploy-token",
        "safe_date": datetime.date(2023, 7, 6),
        "nested": {
            "password_updated": datetime.datetime(2023, 7, 6, 2, 54, 32),
            "created_at": datetime.datetime(2023, 7, 6, 2, 54, 32, tzinfo=datetime.UTC),
        },
    }
    redacted = _redact_secret_values(sensitive_data)
    assert redacted["api_key"] == "<redacted>"
    assert redacted["secret_token"] == "<redacted>"
    assert redacted["safe_date"] == "2023-07-06"
    assert redacted["nested"]["password_updated"] == "<redacted>"
    assert redacted["nested"]["created_at"] == "2023-07-06T02:54:32+00:00"


def test_unsupported_objects_raise_clear_type_error() -> None:
    class CustomObject:
        pass

    with pytest.raises(TypeError, match="Object of type CustomObject is not JSON serializable"):
        _canonical({"unsupported": CustomObject()})

    with pytest.raises(TypeError, match="Object of type set is not JSON serializable"):
        _canonical({"unsupported_set": {1, 2, 3}})

    with pytest.raises(TypeError, match="Object of type object is not JSON serializable"):
        _canonical(object())


_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    ).stdout.strip()


def test_host_diff_cli_workflow_unquoted_timestamp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")

    workflow_dir = repo / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_file = workflow_dir / "demo.yml"
    workflow_file.write_text(
        "name: demo\n"
        "on: workflow_dispatch\n"
        "jobs:\n"
        "  triage:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/labeler@v6\n"
        "        with:\n"
        "          not-before: 2023-07-06T02:54:32Z\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "commit 1: add workflow with unquoted timestamp")

    readme = repo / "README.md"
    readme.write_text("trigger diff from base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "commit 2: trigger diff from base")

    runner = CliRunner()
    result = runner.invoke(app, ["diff", "--workspace", str(repo), "--base", "HEAD^", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["comparison_status"] == "comparable"
    assert payload["review"]["summary"]["changes"] == 0
    coverage_sources = [item["source"] for item in payload["coverage"]["items"]]
    assert ".github/workflows/demo.yml" in coverage_sources
