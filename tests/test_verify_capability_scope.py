"""SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED — toolkit least-privilege weakening.

Regression coverage for the Stripe stripe/ai PR #232 finding: an agent whose
Stripe tools load via a dynamic factory (``*stripe_agent_toolkit.get_tools()``)
the static extractor cannot enumerate, where the base declared an explicit
least-privilege configuration bound and the head silently removed it. Before
this check the opaque factory drove the verdict to ``insufficient_evidence``;
the statically-parseable bound removal must instead make it ``blocked``.

Three layers are exercised: the extractor (constructor + configuration parse),
the carriage codec (policy-fact round trip), the check (base-vs-head
classification), and one end-to-end ``run_scan`` base→head verdict assertion.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agents_shipgate.checks import verify_capability_scope
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent, ToolkitScopeBound
from agents_shipgate.core.lenses.tool_surface import ToolSurfaceDiffReference
from agents_shipgate.core.toolkit_scope import (
    TOOLKIT_BOUND_POLICY_KIND,
    bound_from_policy_fact,
    bound_to_policy_fact,
    toolkit_bound_facts,
)
from agents_shipgate.inputs.openai_sdk_static import (
    _detect_toolkit_bounds,
    load_openai_sdk_static_tools,
)
from agents_shipgate.schemas.manifest import ToolSourceConfig
from agents_shipgate.schemas.surfaces import ToolSurfaceFacts
from agents_shipgate.schemas.verification import VerificationContext

FIXTURES = Path(__file__).parent / "fixtures" / "stripe_pr232"
CHECK_ID = "SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED"


# --- extractor -------------------------------------------------------------


def _bounds_from_src(src: str) -> list[ToolkitScopeBound]:
    return _detect_toolkit_bounds(ast.parse(src), "support_agent.py")


def test_extractor_parses_bounded_configuration():
    src = (
        "from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit\n"
        "tk = StripeAgentToolkit(configuration={'actions': {"
        "'customers': {'read': True}, 'invoices': {'read': True}, "
        "'billing_portal_sessions': {'create': True}}})\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.provider == "stripe"
    assert bound.constructor == "StripeAgentToolkit"
    assert bound.bounded is True
    assert bound.scopes == [
        "billing_portal_sessions:create",
        "customers:read",
        "invoices:read",
    ]
    assert bound.binding == "tk"


def test_extractor_parses_unbounded_factory_call():
    src = (
        "from stripe_agent_toolkit.openai.toolkit import create_stripe_agent_toolkit\n"
        "async def init():\n"
        "    global tk\n"
        "    tk = await create_stripe_agent_toolkit(secret_key='x')\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.provider == "stripe"
    assert bound.bounded is False
    assert bound.scopes == []


def test_extractor_drops_only_false_actions():
    src = (
        "from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit\n"
        "tk = StripeAgentToolkit(configuration={'actions': {"
        "'customers': {'read': True, 'update': False}}})\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.scopes == ["customers:read"]


def test_extractor_marks_non_literal_configuration_unknown():
    # A configuration passed as a variable the tracer cannot resolve is
    # ambiguous; emit an ``unknown`` marker (never silence — the previous
    # skip was a fail-open) but never claim it is bounded or unbounded.
    # The removal check must never fire on an unknown binding.
    src = (
        "from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit\n"
        "cfg = load_cfg()\n"
        "tk = StripeAgentToolkit(configuration=cfg)\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.bounded is False
    assert bound.config_binding == "unknown"
    assert bound.config_path is None


def test_extractor_ignores_unknown_constructor():
    # A real Stripe import but an unknown symbol name must not match.
    src = (
        "from stripe_agent_toolkit.openai.toolkit import SomeOtherToolkit\n"
        "tk = SomeOtherToolkit(configuration={'a': 1})\n"
    )
    assert _bounds_from_src(src) == []


def test_extractor_missing_actions_is_unbounded():
    # Stripe mounts the FULL toolkit when `actions` is absent, so a config with
    # only `context` is unbounded -- NOT "bounded to no tools". Otherwise a base
    # `actions={...}` -> head `configuration={context: ...}` migration would
    # read as a narrowing and escape the gate.
    src = (
        "from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit\n"
        "tk = StripeAgentToolkit(configuration={'context': {'account': 'acct_1'}})\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.bounded is False
    assert bound.scopes == []


def test_extractor_explicit_empty_actions_is_bounded_to_nothing():
    src = (
        "from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit\n"
        "tk = StripeAgentToolkit(configuration={'actions': {}})\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.bounded is True
    assert bound.scopes == []


def test_extractor_tracks_from_import_alias():
    # `import StripeAgentToolkit as SAT; SAT(...)` must resolve.
    src = (
        "from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit as SAT\n"
        "tk = SAT(configuration={'actions': {'customers': {'read': True}}})\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.provider == "stripe"
    assert bound.constructor == "StripeAgentToolkit"
    assert bound.scopes == ["customers:read"]


def test_extractor_resolves_module_alias_attribute_call():
    src = (
        "import stripe_agent_toolkit.openai.toolkit as t\n"
        "tk = t.StripeAgentToolkit(configuration={'actions': {'customers': {'read': True}}})\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.provider == "stripe"
    assert bound.constructor == "StripeAgentToolkit"


def test_extractor_resolves_fully_qualified_call():
    src = (
        "import stripe_agent_toolkit.openai.toolkit\n"
        "tk = stripe_agent_toolkit.openai.toolkit.create_stripe_agent_toolkit(secret_key='x')\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.provider == "stripe"
    assert bound.bounded is False


def test_extractor_ignores_unimported_local_symbol():
    # A local symbol that merely shares the constructor name, never imported
    # from a known toolkit module, must NOT be matched (no false positive).
    src = (
        "def StripeAgentToolkit(**kwargs):\n"
        "    return None\n"
        "tk = StripeAgentToolkit(configuration={'actions': {'customers': {'read': True}}})\n"
    )
    assert _bounds_from_src(src) == []


def test_extractor_ignores_same_name_from_other_module():
    src = (
        "from totally_unrelated import StripeAgentToolkit\n"
        "tk = StripeAgentToolkit(configuration={'actions': {'customers': {'read': True}}})\n"
    )
    assert _bounds_from_src(src) == []


def test_extractor_public_loader_attaches_bounds():
    source = ToolSourceConfig(id="s", type="openai_agents_sdk", path="base/support_agent.py")
    loaded = load_openai_sdk_static_tools(source, load_manifest(_cfg("base")), FIXTURES)
    assert [b.provider for b in loaded.toolkit_bounds] == ["stripe"]
    assert loaded.toolkit_bounds[0].bounded is True
    # search_faq is still enumerated as an ordinary function tool.
    assert any(t.name == "search_faq" for t in loaded.tools)


# --- carriage codec --------------------------------------------------------


def test_policy_fact_round_trip_bounded():
    bound = ToolkitScopeBound(
        provider="stripe",
        constructor="StripeAgentToolkit",
        bounded=True,
        scopes=["customers:read", "invoices:read"],
        binding="customer_tk",
    )
    fact = bound_to_policy_fact(bound)
    assert fact.kind == TOOLKIT_BOUND_POLICY_KIND
    # Keyed per-instance as provider:binding.
    assert fact.key == "stripe:customer_tk"
    decoded = bound_from_policy_fact(fact)
    assert decoded.provider == "stripe"
    assert decoded.binding == "customer_tk"
    assert decoded.bounded is True
    assert decoded.scopes == ["customers:read", "invoices:read"]


def test_policy_fact_round_trip_unbounded():
    bound = ToolkitScopeBound(
        provider="stripe", constructor="create_stripe_agent_toolkit", bounded=False
    )
    decoded = bound_from_policy_fact(bound_to_policy_fact(bound))
    assert decoded.bounded is False
    assert decoded.scopes == []


def test_bound_from_unrelated_policy_fact_is_none():
    from agents_shipgate.schemas.surfaces import ToolSurfacePolicyFact

    other = ToolSurfacePolicyFact(kind="suppression", key="x", value_hash="abc")
    assert bound_from_policy_fact(other) is None


# --- check: base-vs-head classification ------------------------------------


def _cfg(side: str) -> Path:
    return FIXTURES / side / "shipgate.yaml"


def _bound(
    *,
    bounded=True,
    scopes=(),
    provider="stripe",
    constructor="StripeAgentToolkit",
    binding="stripe_agent_toolkit",
):
    return ToolkitScopeBound(
        provider=provider,
        constructor=constructor,
        bounded=bounded,
        scopes=sorted(scopes),
        binding=binding,
        source_ref="support_agent.py",
        source_line=27,
    )


def _ctx(*, head_bounds=(), base_bounds=None, verification=True) -> ScanContext:
    vc = VerificationContext(changed_files=["support_agent.py"]) if verification else None
    diff_reference = None
    if base_bounds is not None:
        facts = ToolSurfaceFacts(policies=toolkit_bound_facts(list(base_bounds)))
        diff_reference = ToolSurfaceDiffReference(kind="report", facts=facts)
    return ScanContext(
        manifest=load_manifest(_cfg("head")),
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=_cfg("head"),
        verification=vc,
        diff_reference=diff_reference,
        toolkit_bounds=list(head_bounds),
    )


def test_removed_bound_emits_critical_finding():
    ctx = _ctx(
        base_bounds=[_bound(scopes=["customers:read", "invoices:read"])],
        head_bounds=[_bound(bounded=False)],
    )
    [finding] = verify_capability_scope.run(ctx)
    assert finding.check_id == CHECK_ID
    assert finding.severity == "critical"
    assert finding.category == "verify"
    # Category "verify" gates via severity, never a second verdict.
    assert finding.blocks_release is False
    assert finding.evidence["kind"] == "scope_bound_removed"
    assert finding.evidence["base_scopes"] == ["customers:read", "invoices:read"]
    assert finding.evidence["head_scopes"] == []


def test_broadened_bound_reports_added_scopes():
    ctx = _ctx(
        base_bounds=[_bound(scopes=["customers:read"])],
        head_bounds=[_bound(scopes=["customers:read", "refunds:create"])],
    )
    [finding] = verify_capability_scope.run(ctx)
    assert finding.evidence["kind"] == "scope_bound_broadened"
    assert finding.evidence["added_scopes"] == ["refunds:create"]


def test_gaining_a_scope_while_dropping_one_still_broadens():
    ctx = _ctx(
        base_bounds=[_bound(scopes=["customers:read", "invoices:read"])],
        head_bounds=[_bound(scopes=["customers:read", "disputes:update"])],
    )
    [finding] = verify_capability_scope.run(ctx)
    assert finding.evidence["added_scopes"] == ["disputes:update"]


def test_narrowed_bound_emits_nothing():
    ctx = _ctx(
        base_bounds=[_bound(scopes=["customers:read", "invoices:read"])],
        head_bounds=[_bound(scopes=["customers:read"])],
    )
    assert verify_capability_scope.run(ctx) == []


def test_unchanged_bound_emits_nothing():
    ctx = _ctx(
        base_bounds=[_bound(scopes=["customers:read"])],
        head_bounds=[_bound(scopes=["customers:read"])],
    )
    assert verify_capability_scope.run(ctx) == []


def test_base_unbounded_head_bounded_is_narrowing():
    # Tightening an already-unbounded toolkit is the safe direction.
    ctx = _ctx(
        base_bounds=[_bound(bounded=False)],
        head_bounds=[_bound(scopes=["customers:read"])],
    )
    assert verify_capability_scope.run(ctx) == []


def test_new_toolkit_only_in_head_emits_nothing():
    ctx = _ctx(base_bounds=[], head_bounds=[_bound(bounded=False)])
    assert verify_capability_scope.run(ctx) == []


def test_multiple_instances_do_not_collapse():
    # Two Stripe toolkits: only customer_tk broadens; invoice_tk is unchanged.
    # Provider-only keying collapsed these (last wins) and missed the
    # broadening; per-binding keying keeps them distinct.
    ctx = _ctx(
        base_bounds=[
            _bound(binding="customer_tk", scopes=["customers:read"]),
            _bound(binding="invoice_tk", scopes=["invoices:read"]),
        ],
        head_bounds=[
            _bound(binding="customer_tk", bounded=False),
            _bound(binding="invoice_tk", scopes=["invoices:read"]),
        ],
    )
    [finding] = verify_capability_scope.run(ctx)
    assert finding.evidence["kind"] == "scope_bound_removed"
    assert finding.evidence["binding"] == "customer_tk"


def test_multiple_instances_unchanged_emit_nothing():
    bounds = [
        _bound(binding="customer_tk", scopes=["customers:read"]),
        _bound(binding="invoice_tk", scopes=["invoices:read"]),
    ]
    ctx = _ctx(base_bounds=list(bounds), head_bounds=list(bounds))
    assert verify_capability_scope.run(ctx) == []


def test_renamed_single_toolkit_still_compared():
    # A rename-plus-broaden: keys differ, but the leftover-singleton fallback
    # re-pairs the one unmatched bound per side so it cannot slip through.
    ctx = _ctx(
        base_bounds=[_bound(binding="customer_tk", scopes=["customers:read"])],
        head_bounds=[_bound(binding="billing_tk", bounded=False)],
    )
    [finding] = verify_capability_scope.run(ctx)
    assert finding.evidence["kind"] == "scope_bound_removed"
    assert finding.evidence["binding"] == "billing_tk"


def test_no_base_reference_emits_nothing():
    ctx = _ctx(base_bounds=None, head_bounds=[_bound(bounded=False)])
    assert verify_capability_scope.run(ctx) == []


def test_no_verification_context_emits_nothing():
    ctx = _ctx(
        base_bounds=[_bound(scopes=["customers:read"])],
        head_bounds=[_bound(bounded=False)],
        verification=False,
    )
    assert verify_capability_scope.run(ctx) == []


# --- end-to-end: PR #232 verdict -------------------------------------------


def test_pr232_bound_removal_blocks_release(tmp_path):
    base_report, _ = run_scan(
        config_path=_cfg("base"),
        output_dir=tmp_path / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    # Base in isolation cannot enumerate the toolkit tools -> insufficient.
    assert base_report.release_decision.decision == "insufficient_evidence"

    head_report, _ = run_scan(
        config_path=_cfg("head"),
        output_dir=tmp_path / "head",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=tmp_path / "base" / "report.json",
        verification_context=VerificationContext(changed_files=["support_agent.py"]),
        packet_enabled=False,
    )
    # The statically-parseable bound removal flips the verdict from
    # insufficient_evidence to blocked.
    assert head_report.release_decision.decision == "blocked"
    blockers = {b.check_id for b in head_report.release_decision.blockers}
    assert CHECK_ID in blockers
    [finding] = [f for f in head_report.findings if f.check_id == CHECK_ID]
    assert finding.severity == "critical"
    assert finding.evidence["kind"] == "scope_bound_removed"
    assert finding.evidence["provider"] == "stripe"


def test_pr232_scoped_verdict_is_precise_not_whole_surface(tmp_path):
    """Adopter-realistic scoping fixes BOTH W27 legs at once.

    The 2026-W27 re-eval showed stripe/ai#232 landing ``human_review_required``
    (not ``blocked``) and safe PRs escalating to review — both traced to the
    miner's whole-monorepo cold-start (``init`` emits 16+ sources: every example
    agent + the toolkit library itself), which dilutes the base→head bound
    removal below the ``blocked`` bar and floods the verdict with whole-surface
    review items. A manifest scoped to the ONE agent file — what a real Stripe
    adopter of this example would write, and what this fixture uses — makes the
    critical removal check the sole blocker and keeps the finding set tiny
    (agent-local), so the verdict is a precise ``blocked`` rather than a noisy
    ``review``. This pins the precision half; the block half is pinned above.
    """
    base_report, _ = run_scan(
        config_path=_cfg("base"),
        output_dir=tmp_path / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    head_report, _ = run_scan(
        config_path=_cfg("head"),
        output_dir=tmp_path / "head",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=tmp_path / "base" / "report.json",
        verification_context=VerificationContext(changed_files=["support_agent.py"]),
        packet_enabled=False,
    )
    decision = head_report.release_decision
    assert decision.decision == "blocked"
    # The critical scope-broadened check is the ONLY thing gating release —
    # no whole-surface policy/schema blockers piling on.
    assert {b.check_id for b in decision.blockers} == {CHECK_ID}
    # Precise, agent-local finding set — nothing like the 150+ review items the
    # whole-monorepo cold-start produced on this same PR (the benign-escalation
    # artifact). A generous ceiling keeps this robust to minor check additions.
    assert len(head_report.findings) <= 8, [f.check_id for f in head_report.findings]


def test_base_report_carries_toolkit_bound_policy_fact(tmp_path):
    base_report, _ = run_scan(
        config_path=_cfg("base"),
        output_dir=tmp_path / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    toolkit_facts = [
        p for p in base_report.tool_surface_facts.policies if p.kind == TOOLKIT_BOUND_POLICY_KIND
    ]
    assert [p.key for p in toolkit_facts] == ["stripe:stripe_agent_toolkit"]
    assert "customers:read" in toolkit_facts[0].summary
