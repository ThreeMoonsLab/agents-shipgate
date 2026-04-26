from agents_shipgate.checks.manifest_scope import _purpose_is_read_only


def test_purpose_read_only_detection_uses_tokens_not_substrings():
    assert not _purpose_is_read_only("interview customer support agents")
    assert not _purpose_is_read_only("preview and review generated reports")
    assert _purpose_is_read_only("read-only ticket lookups")
