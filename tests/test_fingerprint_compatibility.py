from __future__ import annotations

from pathlib import Path

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.findings.identity import finding_fingerprint
from agents_shipgate.schemas.report import Finding

V015_FINGERPRINTS = {
    ("SHIP-INVENTORY-WILDCARD-TOOLS", "wildcard_mcp_tools.*"): "fp_fc02d8ecd30f2578",
    ("SHIP-SCHEMA-MISSING-BOUNDS", "stripe.create_refund"): "fp_ab60b01cb53cfcbe",
    ("SHIP-SCHEMA-BROAD-FREE-TEXT", "zendesk.update_ticket"): "fp_ff2f028953d1c220",
    ("SHIP-SCHEMA-BROAD-FREE-TEXT", "gmail.send_customer_email"): "fp_acd63b899d49aa1c",
    ("SHIP-AUTH-MANIFEST-BROAD-SCOPE", None): "fp_d27325cbdbbf5483",
    ("SHIP-AUTH-SCOPE-COVERAGE-MISSING", "shopify.cancel_order"): "fp_83852fbd6b440524",
    ("SHIP-AUTH-SCOPE-COVERAGE-MISSING", "support.search_kb"): "fp_d8e6d1865dae97cc",
    ("SHIP-AUTH-SCOPE-COVERAGE-MISSING", "gmail.send_customer_email"): "fp_1f6cfd6b7daa9b7c",
    ("SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING", "shopify.cancel_order"): "fp_fd2577850cef1f87",
    ("SHIP-MANIFEST-UNUSED-SCOPE", None): "fp_39b9ae878f343d1b",
}


def test_v015_unchanged_finding_fingerprints_remain_stable(tmp_path: Path) -> None:
    report, _ = run_scan(
        config_path=Path("samples/support_refund_agent/shipgate.yaml"),
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    observed = {
        (finding.check_id, finding.tool_name): finding.fingerprint for finding in report.findings
    }
    assert {key: observed.get(key) for key in V015_FINGERPRINTS} == V015_FINGERPRINTS


def test_v015_freeform_output_fingerprint_remains_stable_after_sample_migration() -> None:
    """The reviewed inventory intentionally removed this sample finding.

    Pin its unchanged v0.15 identity directly so the migration does not erase
    the compatibility guarantee for old baselines that still contain it.
    """

    finding = Finding(
        check_id="SHIP-SCHEMA-FREEFORM-OUTPUT",
        title="send_email_preview returns free-form text output",
        severity="medium",
        category="schema",
        tool_name="send_email_preview",
        evidence={"output_schema": {"type": "string"}},
        confidence="medium",
        recommendation=(
            "Prefer a structured output schema for send_email_preview, especially "
            "when output is later passed back into model context."
        ),
    )

    assert finding_fingerprint(finding) == "fp_85f8513ad72cd9ea"
