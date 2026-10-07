"""Dependabot version updates are grouped; security updates are not (#501).

Grouping turns a week's five pip pull requests into one lock recompilation.
The negative control is that an advisory never waits inside that group: every
group applies to version updates only, so a security update still arrives as
its own pull request.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / ".github" / "dependabot.yml"


def _updates() -> list[dict]:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["updates"]


def test_every_ecosystem_groups_its_version_updates() -> None:
    for update in _updates():
        groups = update.get("groups")
        assert groups, update["package-ecosystem"]
        assert any(group.get("patterns") == ["*"] for group in groups.values())


def test_no_group_absorbs_security_updates() -> None:
    for update in _updates():
        for name, group in update["groups"].items():
            assert group.get("applies-to") == "version-updates", (update["package-ecosystem"], name)


def test_the_pip_ignore_rules_survive_grouping() -> None:
    pip = next(update for update in _updates() if update["package-ecosystem"] == "pip")
    ignored = {rule["dependency-name"] for rule in pip["ignore"]}
    assert {"chardet", "pydantic-core"} <= ignored


def test_major_bumps_stay_individual() -> None:
    for update in _updates():
        for name, group in update["groups"].items():
            assert group.get("update-types") == ["minor", "patch"], (update["package-ecosystem"], name)


def test_the_lock_resolver_and_harness_sdk_are_not_grouped() -> None:
    pip = next(update for update in _updates() if update["package-ecosystem"] == "pip")
    for group in pip["groups"].values():
        assert {"uv", "claude-agent-sdk"} <= set(group.get("exclude-patterns", []))
