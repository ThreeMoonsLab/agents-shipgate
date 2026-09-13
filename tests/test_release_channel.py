"""Each release version's channel comes from reviewed code, and fails closed (#648)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import release_channel as rc

REPO_ROOT = Path(__file__).resolve().parents[1]


def _declaration(tmp_path: Path, channels: object, **extra: object) -> Path:
    path = tmp_path / "release-channels.json"
    path.write_text(json.dumps({"schema": rc.SCHEMA, "channels": channels, **extra}), encoding="utf-8")
    return path


def test_the_committed_declaration_records_the_advisory_1_0_decision() -> None:
    assert rc.load_declaration(REPO_ROOT / rc.DECLARATION_PATH) == {"1.0.0": "advisory"}


@pytest.mark.parametrize("channel", ["advisory", "qualified"])
def test_a_declared_version_resolves_to_its_channel(tmp_path: Path, channel: str) -> None:
    path = _declaration(tmp_path, {"1.0.0": channel})
    assert rc.resolve("1.0.0", path=path) == channel
    assert rc.resolve_tag("v1.0.0", path=path) == channel


def test_an_undeclared_version_stops_the_release(tmp_path: Path) -> None:
    """No declaration is no release — never a default channel."""

    path = _declaration(tmp_path, {"1.0.0": "advisory"})
    with pytest.raises(rc.ChannelError, match="no reviewed release channel is declared for 1.1.0"):
        rc.resolve_tag("v1.1.0", path=path)


def test_a_missing_declaration_file_stops_the_release(tmp_path: Path) -> None:
    with pytest.raises(rc.ChannelError, match="not found"):
        rc.resolve_tag("v1.0.0", path=tmp_path / "absent.json")


@pytest.mark.parametrize(
    ("channels", "extra", "message"),
    [
        ({"1.0.0": "preview"}, {}, "unknown channel"),
        ({"1.0.0": ""}, {}, "unknown channel"),
        ({"latest": "advisory"}, {}, "not a complete public release version"),
        ({"1.0.0+local": "advisory"}, {}, "not a complete public release version"),
        (["1.0.0"], {}, "must map release versions"),
        ({"1.0.0": "advisory"}, {"note": "x"}, "exactly 'schema' and 'channels'"),
    ],
)
def test_a_malformed_declaration_is_refused(tmp_path, channels, extra, message) -> None:
    path = _declaration(tmp_path, channels, **extra)
    with pytest.raises(rc.ChannelError, match=message):
        rc.load_declaration(path)


def test_a_wrong_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "release-channels.json"
    path.write_text(json.dumps({"schema": "shipgate.release_channels/v0", "channels": {}}), encoding="utf-8")
    with pytest.raises(rc.ChannelError, match="unsupported release channel schema"):
        rc.load_declaration(path)


def test_a_duplicated_version_is_refused_not_last_one_wins(tmp_path: Path) -> None:
    """``json`` keeps the last duplicate, so a later line could silently flip a channel."""

    path = tmp_path / "release-channels.json"
    path.write_text(
        '{"schema": "' + rc.SCHEMA + '", "channels": {"1.0.0": "qualified", "1.0.0": "advisory"}}',
        encoding="utf-8",
    )
    with pytest.raises(rc.ChannelError, match="duplicate key"):
        rc.load_declaration(path)


@pytest.mark.parametrize("tag", ["1.0.0", "preview-1.0.0", "v1.0", "vlatest", "v1.0.0+local", ""])
def test_a_tag_that_is_not_v_version_is_refused(tmp_path: Path, tag: str) -> None:
    path = _declaration(tmp_path, {"1.0.0": "advisory", "1.0": "advisory"})
    if tag == "v1.0":
        assert rc.resolve_tag(tag, path=path) == "advisory"
        return
    with pytest.raises(rc.ChannelError):
        rc.resolve_tag(tag, path=path)


@pytest.mark.parametrize(
    ("channel", "qualified", "advisory"),
    [("advisory", "skipped", "success"), ("qualified", "success", "skipped")],
)
def test_select_accepts_only_the_declared_verification_running_alone(channel, qualified, advisory) -> None:
    assert rc.select(channel, qualified_result=qualified, advisory_result=advisory) == channel


@pytest.mark.parametrize(
    ("channel", "qualified", "advisory"),
    [
        ("advisory", "success", "skipped"),    # the undeclared line's evidence
        ("advisory", "success", "success"),    # both ran
        ("advisory", "skipped", "skipped"),    # neither ran
        ("advisory", "skipped", "failure"),    # the declared one failed
        ("advisory", "skipped", "cancelled"),
        ("qualified", "skipped", "success"),
        ("qualified", "failure", "skipped"),
        ("qualified", "success", "failure"),   # the other did not stay skipped
        ("advisory", "skipped", "neutral"),    # a result GitHub does not emit
        ("preview", "skipped", "success"),     # an unknown channel
    ],
)
def test_select_refuses_every_other_outcome(channel, qualified, advisory) -> None:
    with pytest.raises(rc.ChannelError):
        rc.select(channel, qualified_result=qualified, advisory_result=advisory)


def test_cli_writes_the_channel_to_github_output(tmp_path, monkeypatch, capsys) -> None:
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    path = _declaration(tmp_path, {"1.0.0": "advisory"})
    assert rc.main(["resolve", "--tag", "v1.0.0", "--declaration", str(path)]) == 0
    assert output.read_text(encoding="utf-8") == "channel=advisory\n"


def test_cli_refusal_exits_nonzero_and_writes_no_output(tmp_path, monkeypatch, capsys) -> None:
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    path = _declaration(tmp_path, {"1.0.0": "advisory"})
    assert rc.main(["resolve", "--tag", "v2.0.0", "--declaration", str(path)]) == 1
    assert not output.exists()
    assert "::error::" in capsys.readouterr().err
