"""SHIP-CAP-CONFIG-BINDING-* — config-bound dynamic-toolkit authority.

Covers the detection half of
docs/engineering/config-bound-capability-detection.md, reproducing the
2026-06 design-partner pilot blind spot: a toolkit factory whose authority
allowlist is *bound from config* hides its effective tool surface from both
sides of a verify diff, so removing the binding (authority expansion) or
editing the bound config produced no signal at all.

Layers exercised: the conservative config-read tracer, the extractor
(config-bound / unknown markers), the carriage codec (policy-fact round
trip incl. legacy hash stability), the check (base-vs-head classification),
end-to-end base→head verdicts on the ``config_bound_factory`` fixture, and
the fix_task projection carrying the inventory remedy.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from agents_shipgate.checks import toolkit_bounds, verify_config_binding
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.verify.fix_task import build_fix_task
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
from agents_shipgate.inputs.config_trace import trace_config_binding
from agents_shipgate.inputs.openai_sdk_static import _detect_toolkit_bounds
from agents_shipgate.schemas.surfaces import ToolSurfaceFacts
from agents_shipgate.schemas.verification import VerificationContext
from agents_shipgate.schemas.verifier import VerifierCapabilityReview

FIXTURES = Path(__file__).parent / "fixtures" / "config_bound_factory"
CHECK_REMOVED = "SHIP-CAP-CONFIG-BINDING-REMOVED"
CHECK_CHANGED = "SHIP-CAP-CONFIG-BINDING-CHANGED"


# --- tracer ------------------------------------------------------------------


def _trace(src: str, expr_src: str, line: int | None = None):
    tree = ast.parse(src)
    expr = ast.parse(expr_src, mode="eval").body
    return trace_config_binding(
        expr, tree=tree, line=line if line is not None else len(src.splitlines())
    )


def test_trace_json_load_open_literal_path():
    trace = _trace('import json\ncfg = json.load(open("config/tools.json"))\n', "cfg")
    assert trace.binding == "config"
    assert trace.config_path == "config/tools.json"


def test_trace_yaml_module_alias():
    trace = _trace('import yaml as y\ncfg = y.safe_load(open("cfg.yaml"))\n', "cfg")
    assert trace.binding == "config"
    assert trace.config_path == "cfg.yaml"


def test_trace_from_import_alias():
    trace = _trace('from json import load as jload\ncfg = jload(open("a.json"))\n', "cfg")
    assert trace.binding == "config"
    assert trace.config_path == "a.json"


def test_trace_toml_literal_path_argument():
    trace = _trace('import toml\ncfg = toml.load("pyproject.toml")\n', "cfg")
    assert trace.binding == "config"
    assert trace.config_path == "pyproject.toml"


def test_trace_pathlib_read_text():
    src = (
        "import json\nfrom pathlib import Path\n"
        'cfg = json.loads(Path("config/actions.json").read_text())\n'
    )
    trace = _trace(src, "cfg")
    assert trace.binding == "config"
    assert trace.config_path == "config/actions.json"


def test_trace_os_environ_subscript_and_getenv():
    assert _trace("import os\n", 'os.environ["ACTIONS"]').binding == "config"
    getenv = _trace('import os\ncfg = os.getenv("ACTIONS")\n', "cfg")
    assert getenv.binding == "config"
    assert getenv.config_path is None


def test_trace_pydantic_settings_subclass():
    src = (
        "from pydantic_settings import BaseSettings\n"
        "class ToolSettings(BaseSettings):\n"
        "    actions: dict = {}\n"
        "settings = ToolSettings()\n"
    )
    assert _trace(src, "settings").binding == "config"
    # Attribute access on the settings object carries the same binding.
    assert _trace(src, "settings.actions").binding == "config"


def test_trace_subscript_and_get_on_traced_name():
    src = 'import json\ncfg = json.load(open("cfg.json"))\n'
    assert _trace(src, 'cfg["actions"]').binding == "config"
    assert _trace(src, 'cfg.get("actions")').binding == "config"


def test_trace_unresolvable_name_is_unknown():
    assert _trace("x = 1\n", "somewhere_else").binding == "unknown"


def test_trace_non_config_call_is_unknown():
    assert _trace("cfg = build_config()\n", "cfg").binding == "unknown"


def test_trace_reassigned_name_is_ambiguous_unknown():
    # Same-scope reassignment is ambiguous; the conservative tracer must not
    # pick a winner (the design doc's false-positive guard).
    src = (
        "import json\n"
        'cfg = json.load(open("a.json"))\n'
        "cfg = build_config()\n"
    )
    assert _trace(src, "cfg").binding == "unknown"


def test_trace_function_scope_falls_back_to_module_scope():
    src = (
        "import json\n"
        '_CONF = json.load(open("config/actions.json"))\n'
        "def init():\n"
        "    tk = make(configuration=_CONF)\n"
    )
    trace = _trace(src, "_CONF", line=4)
    assert trace.binding == "config"
    assert trace.config_path == "config/actions.json"


def test_trace_local_shadow_wins_over_module_scope():
    src = (
        "import json\n"
        '_CONF = json.load(open("config/actions.json"))\n'
        "def init():\n"
        "    _CONF = build_config()\n"
        "    tk = make(configuration=_CONF)\n"
    )
    assert _trace(src, "_CONF", line=5).binding == "unknown"


# --- extractor ---------------------------------------------------------------


def _bounds_from_src(src: str) -> list[ToolkitScopeBound]:
    return _detect_toolkit_bounds(ast.parse(src), "support_agent.py")


def test_extractor_marks_config_bound_factory():
    src = (
        "import json\n"
        "from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit\n"
        '_CONF = json.load(open("config/stripe_actions.json"))\n'
        "tk = StripeAgentToolkit(configuration=_CONF)\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.bounded is False
    assert bound.config_binding == "config"
    assert bound.config_path == "config/stripe_actions.json"
    assert bound.binding == "tk"


def test_extractor_traces_non_literal_actions_value():
    # configuration is a dict literal but the authority-bearing `actions`
    # value is the traced name.
    src = (
        "import yaml\n"
        "from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit\n"
        '_ACTIONS = yaml.safe_load(open("actions.yaml"))\n'
        "tk = StripeAgentToolkit(configuration={'actions': _ACTIONS})\n"
    )
    [bound] = _bounds_from_src(src)
    assert bound.config_binding == "config"
    assert bound.config_path == "actions.yaml"


def test_extractor_literal_and_absent_bindings_are_labeled():
    literal = _bounds_from_src(
        "from stripe_agent_toolkit.openai.toolkit import StripeAgentToolkit\n"
        "tk = StripeAgentToolkit(configuration={'actions': {'customers': {'read': True}}})\n"
    )
    assert literal[0].config_binding == "literal"
    absent = _bounds_from_src(
        "from stripe_agent_toolkit.openai.toolkit import create_stripe_agent_toolkit\n"
        "tk = create_stripe_agent_toolkit(secret_key='x')\n"
    )
    assert absent[0].config_binding == "absent"


# --- carriage codec ------------------------------------------------------------


def _bound(
    *,
    config_binding=None,
    config_path=None,
    bounded=False,
    scopes=(),
    binding="stripe_agent_toolkit",
):
    return ToolkitScopeBound(
        provider="stripe",
        constructor="StripeAgentToolkit",
        bounded=bounded,
        scopes=sorted(scopes),
        binding=binding,
        source_ref="support_agent.py",
        source_line=32,
        config_binding=config_binding,
        config_path=config_path,
    )


def test_policy_fact_round_trip_config_bound_with_path():
    fact = bound_to_policy_fact(
        _bound(config_binding="config", config_path="config/stripe_actions.json")
    )
    assert fact.kind == TOOLKIT_BOUND_POLICY_KIND
    assert "config-bound" in (fact.summary or "")
    decoded = bound_from_policy_fact(fact)
    assert decoded.config_binding == "config"
    assert decoded.config_path == "config/stripe_actions.json"
    assert decoded.bounded is False


def test_policy_fact_round_trip_config_bound_without_path():
    decoded = bound_from_policy_fact(
        bound_to_policy_fact(_bound(config_binding="config"))
    )
    assert decoded.config_binding == "config"
    assert decoded.config_path is None


def test_policy_fact_round_trip_unknown_binding():
    decoded = bound_from_policy_fact(
        bound_to_policy_fact(_bound(config_binding="unknown"))
    )
    assert decoded.config_binding == "unknown"
    assert decoded.bounded is False


def test_legacy_value_hash_unchanged_for_literal_and_absent():
    # The serialized value_hash of literal/absent bounds must stay
    # byte-identical to the pre-feature formula, or every existing base
    # report's toolkit facts would churn on upgrade.
    def legacy_hash(bounded: bool, scopes: list[str]) -> str:
        payload = json.dumps(
            {"bounded": bounded, "scopes": sorted(scopes)},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    literal = _bound(
        config_binding="literal", bounded=True, scopes=["customers:read"]
    )
    assert bound_to_policy_fact(literal).value_hash == legacy_hash(
        True, ["customers:read"]
    )
    absent = _bound(config_binding="absent")
    assert bound_to_policy_fact(absent).value_hash == legacy_hash(False, [])


# --- check: base-vs-head classification ---------------------------------------


def _cfg(side: str) -> Path:
    return FIXTURES / side / "shipgate.yaml"


def _ctx(
    *,
    head_bounds=(),
    base_bounds=None,
    verification=True,
    changed=("support_agent.py",),
) -> ScanContext:
    vc = VerificationContext(changed_files=list(changed)) if verification else None
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


_CONFIG_BASE = dict(config_binding="config", config_path="config/stripe_actions.json")


def test_binding_removed_emits_high_finding():
    ctx = _ctx(
        base_bounds=[_bound(**_CONFIG_BASE)],
        head_bounds=[_bound(config_binding="absent")],
    )
    [finding] = verify_config_binding.run(ctx)
    assert finding.check_id == CHECK_REMOVED
    assert finding.severity == "high"
    assert finding.category == "verify"
    assert finding.blocks_release is False
    assert finding.evidence["kind"] == "config_binding_removed"
    assert finding.evidence["base_config_path"] == "config/stripe_actions.json"
    # The remedy carries the inventory remedy with the factory's site.
    assert "support_agent.py:32" in finding.recommendation
    assert "tool inventory" in finding.recommendation


def test_unknown_head_binding_never_fires_removal():
    # The design doc's calibration guard: an unreadable binding must not be
    # read as a removal (it degrades to the extractor's source warning).
    ctx = _ctx(
        base_bounds=[_bound(**_CONFIG_BASE)],
        head_bounds=[_bound(config_binding="unknown")],
    )
    assert verify_config_binding.run(ctx) == []


def test_head_moving_to_literal_allowlist_emits_nothing():
    ctx = _ctx(
        base_bounds=[_bound(**_CONFIG_BASE)],
        head_bounds=[
            _bound(config_binding="literal", bounded=True, scopes=["customers:read"])
        ],
    )
    assert verify_config_binding.run(ctx) == []


def test_config_file_changed_emits_review_item():
    ctx = _ctx(
        base_bounds=[_bound(**_CONFIG_BASE)],
        head_bounds=[_bound(**_CONFIG_BASE)],
        changed=("config/stripe_actions.json",),
    )
    [finding] = verify_config_binding.run(ctx)
    assert finding.check_id == CHECK_CHANGED
    assert finding.severity == "medium"
    assert finding.evidence["kind"] == "config_binding_changed"
    assert finding.evidence["changed_file"] == "config/stripe_actions.json"


def test_config_file_untouched_emits_nothing():
    ctx = _ctx(
        base_bounds=[_bound(**_CONFIG_BASE)],
        head_bounds=[_bound(**_CONFIG_BASE)],
        changed=("README.md",),
    )
    assert verify_config_binding.run(ctx) == []


def test_config_binding_retarget_fires_without_config_in_changed_files():
    # Review-reproduced blind spot: the PR edits only the .py file, repointing
    # the binding from safe.json to a PRE-EXISTING broad.json. Neither config
    # file appears in changed_files, yet the effective tool surface may have
    # expanded — the retarget itself must be the review item.
    base = dict(_CONFIG_BASE)
    head = dict(_CONFIG_BASE)
    base["config_path"] = "config/safe.json"
    head["config_path"] = "config/broad.json"
    ctx = _ctx(
        base_bounds=[_bound(**base)],
        head_bounds=[_bound(**head)],
        changed=("support_agent.py",),
    )
    [finding] = verify_config_binding.run(ctx)
    assert finding.check_id == CHECK_CHANGED
    assert finding.severity == "medium"
    assert finding.evidence["kind"] == "config_binding_retargeted"
    assert finding.evidence["previous_config_path"] == "config/safe.json"
    assert finding.evidence["config_path"] == "config/broad.json"
    assert "config/safe.json → config/broad.json" in finding.recommendation


def test_pathless_config_binding_never_guesses_a_match():
    # Env/settings-bound factories carry no literal path; a changed file must
    # never be matched by guesswork.
    ctx = _ctx(
        base_bounds=[_bound(config_binding="config")],
        head_bounds=[_bound(config_binding="config")],
        changed=("config/stripe_actions.json",),
    )
    assert verify_config_binding.run(ctx) == []


def test_literal_base_binding_is_not_this_checks_concern():
    # literal → absent is SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED's territory.
    ctx = _ctx(
        base_bounds=[
            _bound(config_binding="literal", bounded=True, scopes=["customers:read"])
        ],
        head_bounds=[_bound(config_binding="absent")],
    )
    assert verify_config_binding.run(ctx) == []


def test_no_verification_context_emits_nothing():
    ctx = _ctx(
        base_bounds=[_bound(**_CONFIG_BASE)],
        head_bounds=[_bound(config_binding="absent")],
        verification=False,
    )
    assert verify_config_binding.run(ctx) == []


def test_no_base_reference_emits_nothing():
    ctx = _ctx(base_bounds=None, head_bounds=[_bound(config_binding="absent")])
    assert verify_config_binding.run(ctx) == []


def test_toolkit_unbounded_check_skips_config_bound_factories():
    # "Mounted without a scope bound" would be a false claim for a factory
    # whose configuration lives in config (or is unreadable).
    ctx = _ctx(
        head_bounds=[
            _bound(**_CONFIG_BASE),
            _bound(config_binding="unknown", binding="other_tk"),
        ]
    )
    assert toolkit_bounds.run(ctx) == []
    ctx_absent = _ctx(head_bounds=[_bound(config_binding="absent")])
    [finding] = toolkit_bounds.run(ctx_absent)
    assert finding.check_id == "SHIP-SCOPE-TOOLKIT-UNBOUNDED"


# --- end-to-end: the pilot scenario -------------------------------------------


def test_base_report_carries_config_bound_policy_fact(tmp_path):
    base_report, _ = run_scan(
        config_path=_cfg("base"),
        output_dir=tmp_path / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    toolkit_facts = [
        p
        for p in base_report.tool_surface_facts.policies
        if p.kind == TOOLKIT_BOUND_POLICY_KIND
    ]
    assert [p.key for p in toolkit_facts] == ["stripe:stripe_agent_toolkit"]
    assert toolkit_facts[0].summary == "(config-bound: config/stripe_actions.json)"


def test_config_binding_removal_routes_to_review(tmp_path):
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
    # The statically-visible binding removal lifts the verdict out of silent
    # insufficient_evidence parity.
    assert head_report.release_decision.decision in {"blocked", "review_required"}
    gating = {
        item.check_id
        for item in [
            *head_report.release_decision.blockers,
            *head_report.release_decision.review_items,
        ]
    }
    assert CHECK_REMOVED in gating
    [finding] = [f for f in head_report.findings if f.check_id == CHECK_REMOVED]
    assert finding.severity == "high"
    assert finding.evidence["provider"] == "stripe"
    assert finding.evidence["base_config_path"] == "config/stripe_actions.json"

    # Acceptance: fix_task for the new finding carries the inventory remedy
    # with the factory's source path and line.
    fix_task = build_fix_task(
        head_report,
        merge_verdict="human_review_required",
        capability_review=VerifierCapabilityReview(),
        base_ref="origin/main",
        head_ref="HEAD",
    )
    assert fix_task is not None
    assert fix_task.actor == "human"
    combined = "\n".join(
        [*fix_task.instructions, *[r.reason or "" for r in fix_task.allowed_repairs]]
    )
    assert "tool inventory" in combined
    assert "support_agent.py:" in combined


def test_config_delta_routes_to_review_item(tmp_path):
    # Same tree on both sides; the PR touches only the bound config file.
    base_report, _ = run_scan(
        config_path=_cfg("base"),
        output_dir=tmp_path / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    head_report, _ = run_scan(
        config_path=_cfg("base"),
        output_dir=tmp_path / "head",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=tmp_path / "base" / "report.json",
        verification_context=VerificationContext(
            changed_files=["config/stripe_actions.json"]
        ),
        packet_enabled=False,
    )
    [finding] = [f for f in head_report.findings if f.check_id == CHECK_CHANGED]
    assert finding.evidence["changed_file"] == "config/stripe_actions.json"
    review_ids = {
        item.check_id for item in head_report.release_decision.review_items
    }
    assert CHECK_CHANGED in review_ids
