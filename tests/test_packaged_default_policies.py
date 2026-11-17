"""A source checkout evaluates the default boundary policies the wheel ships.

The wheel force-includes ``policies/*.shipgate.yaml`` under
``agents_shipgate/_meta/policies/``. A source checkout, an editable install and
the ``./shipgate`` launcher have no ``_meta/``, and their loaders used to fall
back to the in-code rules there instead. On a workspace with no policy file,
``./shipgate`` evaluated ``codex-boundary-default`` and ``host-boundary-default``,
where the 1.1.0 through 1.3.0 wheels load ``codex-boundary`` and
``host-boundary``. One ``CODEX-POLICY-WEAKENED`` recommendation differed as
well, so the boundary result's ``policy_set_sha256`` was ``5ad04b64…`` from the
checkout and ``480e1cec…`` from those wheels. ``mcp audit`` reported policy
version ``builtin`` against the wheel's ``1``.

These tests run against the checkout. The built-wheel comparison is
``tests/test_packaging.py::test_installed_wheel_evaluates_the_checkouts_default_policies``.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from agents_shipgate.cli import mcp as mcp_cli
from agents_shipgate.core import agent_boundary, codex_boundary, host_boundary
from agents_shipgate.core.wheel_layout import WHEEL_LAYOUT, packaged_path

ROOT = Path(__file__).resolve().parent.parent


def _wheel_target() -> dict:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]


def _force_include() -> dict[str, str]:
    """``pyproject.toml``'s force-include table, as package-relative targets."""

    return {
        source: target.removeprefix("agents_shipgate/")
        for source, target in _wheel_target()["force-include"].items()
    }


def _shipped_source(packaged: str) -> Path:
    """The repository file the wheel copies to ``agents_shipgate/<packaged>``."""

    sources = [source for source, target in _force_include().items() if target == packaged]
    assert len(sources) == 1, f"{packaged} is not force-included exactly once"
    return ROOT / sources[0]


def _snapshot(text: str) -> str:
    # The digest `load_codex_boundary_policy` records for a policy file.
    return hashlib.sha256(
        json.dumps(
            yaml.safe_load(text), sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


CODEX = "_meta/policies/codex-boundary.shipgate.yaml"
HOST = "_meta/policies/host-boundary.shipgate.yaml"
MCP = "_meta/policies/mcp-permissions.shipgate.yaml"
UNIFIED = "_meta/policies/agent-boundary.shipgate.yaml"


def test_the_wheel_layout_is_the_pyproject_force_include() -> None:
    """``verification_identity`` and the policy loaders read this one table.

    A force-include added to ``pyproject.toml`` and not here would give a
    checkout another engine identity than its wheel and leave the file unread
    in a checkout. ``"src" = ""`` is the package itself.
    """

    sources = {
        source: target.removeprefix("agents_shipgate/")
        for source, target in _wheel_target()["sources"].items()
        if target
    }

    assert len(WHEEL_LAYOUT) == len(set(WHEEL_LAYOUT))
    assert set(WHEEL_LAYOUT) == {*sources.items(), *_force_include().items()}


def test_a_wheel_reads_its_packaged_copy_and_a_checkout_the_repository_file(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "site-packages" / "agents_shipgate"
    (wheel / "_meta").mkdir(parents=True)
    # Even where its packaged copy is missing, a wheel never reads a checkout:
    # the loader then falls back to the in-code rules.
    assert packaged_path(CODEX, package_root=wheel) == wheel / CODEX

    repository = tmp_path / "repository"
    checkout = repository / "src" / "agents_shipgate"
    checkout.mkdir(parents=True)
    assert packaged_path(CODEX, package_root=checkout) == (
        repository / "policies" / "codex-boundary.shipgate.yaml"
    )
    assert packaged_path(UNIFIED, package_root=checkout) == (
        repository / "policies" / "templates" / "agent-boundary-default.shipgate.yaml"
    )
    assert packaged_path(
        "_meta/policies/templates/payments.shipgate.yaml", package_root=checkout
    ) == (repository / "policies" / "templates" / "payments.shipgate.yaml")
    assert packaged_path("_meta/unmapped.json", package_root=checkout) == (
        checkout / "_meta" / "unmapped.json"
    )


def test_each_family_loads_the_file_the_wheel_ships() -> None:
    codex_text = _shipped_source(CODEX).read_text(encoding="utf-8")

    assert codex_boundary._packaged_policy_text() == codex_text
    assert host_boundary._load_packaged_default_policy() == yaml.safe_load(
        _shipped_source(HOST).read_text(encoding="utf-8")
    )
    assert mcp_cli._load_packaged_default_mcp_policy() == yaml.safe_load(
        _shipped_source(MCP).read_text(encoding="utf-8")
    )


def test_an_empty_workspace_evaluates_the_shipped_boundary_policies(tmp_path: Path) -> None:
    """The records ``check`` reports when no policy file is in the workspace."""

    policies = agent_boundary._load_policy_set(workspace=tmp_path, explicit=None)

    codex_text = _shipped_source(CODEX).read_text(encoding="utf-8")
    host_policy = yaml.safe_load(_shipped_source(HOST).read_text(encoding="utf-8"))
    assert (policies.codex.id, policies.host.id) == (
        yaml.safe_load(codex_text)["id"],
        host_policy["id"],
    )
    assert policies.codex.snapshot_sha256 == _snapshot(codex_text)
    assert [item.source for item in policies.records] == ["packaged_default"] * 2
    assert policies.issues == ()
    assert list(policies.diagnostics) == []


def test_mcp_audit_reports_the_shipped_policy_version(tmp_path: Path) -> None:
    diagnostics: list = []

    _rules, version = mcp_cli._load_mcp_policy(
        mcp_cli.DEFAULT_MCP_POLICY_PATH, tmp_path, diagnostics
    )

    shipped = yaml.safe_load(_shipped_source(MCP).read_text(encoding="utf-8"))
    assert version == shipped["version"]
    assert diagnostics == []


@pytest.mark.parametrize(
    ("packaged", "defaults", "fallback_id"),
    [
        (
            CODEX,
            {key: asdict(rule) for key, rule in codex_boundary.DEFAULT_RULES.items()},
            "codex-boundary-default",
        ),
        (
            HOST,
            {key: asdict(rule) for key, rule in host_boundary.DEFAULT_RULES.items()},
            "host-boundary-default",
        ),
        (
            MCP,
            {key: {"id": key, **rule} for key, rule in mcp_cli._RULES.items()},
            None,
        ),
    ],
    ids=["codex-boundary", "host-boundary", "mcp-permissions"],
)
def test_the_code_fallback_states_the_shipped_rules(
    packaged: str, defaults: dict[str, dict[str, str]], fallback_id: str | None
) -> None:
    """The in-code rules answer only when no policy file can be read.

    That is an explicit ``--policy`` that is missing or invalid, a workspace
    policy that leaves a rule out, or an install without the packaged file.
    Each field a shipped rule states must match the code, so those runs report
    the same rule a default run reports. The one documented difference is the
    policy's own ``id`` (``version`` for ``mcp audit``, which reports ``builtin``):
    it says whether the rules came from a file. The comment in
    ``agent_boundary._load_policy_set`` relies on this test.
    """

    shipped = yaml.safe_load(_shipped_source(packaged).read_text(encoding="utf-8"))
    stated = {rule["id"]: rule for rule in shipped["rules"]}

    assert set(stated) == set(defaults)
    for rule_id, rule in stated.items():
        assert rule == {field: defaults[rule_id][field] for field in rule}, rule_id
    assert shipped["version"] == codex_boundary.DEFAULT_POLICY_VERSION
    if fallback_id is not None:
        assert fallback_id == f"{shipped['id']}-default"


def test_the_unified_template_lists_the_rule_union() -> None:
    """``policies/agent-boundary.shipgate.yaml`` names every rule the families own."""

    template = yaml.safe_load(_shipped_source(UNIFIED).read_text(encoding="utf-8"))
    listed = [rule["id"] for rule in template["rules"]]

    union = {
        *codex_boundary.DEFAULT_RULES,
        *host_boundary.DEFAULT_RULES,
        *(rule.id for rule in agent_boundary._GENERIC_RULES.values()),
    }
    assert len(listed) == len(set(listed))
    assert set(listed) == union
