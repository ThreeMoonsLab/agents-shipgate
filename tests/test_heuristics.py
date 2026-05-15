from agents_shipgate.core.heuristics import is_broad_scope


def test_broad_scope_admin_detection_uses_scope_segments():
    assert is_broad_scope("admin:*") is True
    assert is_broad_scope("service:admin") is True
    assert is_broad_scope("service:admin:write") is True
    assert is_broad_scope("administrator:read") is False
    assert is_broad_scope("admin_panel:view") is False
    assert is_broad_scope("site_admin_metrics:read") is False
