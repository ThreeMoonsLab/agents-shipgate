"""The #659 oracle is exercised by controls it must pass, and a bad engine cannot.

`benchmark/host-config/expected.py` derives what each case should show from the
two file versions alone. These controls check that oracle and scorer the way
#659 asks: a positive control (an allow rule added), a narrowing control (an
allow rule removed) and a neutral-inside-config control (the same rules,
reordered and reformatted). A zero-output implementation and a false-widening
implementation both fail them; the engine passes.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

BENCHMARK = Path(__file__).resolve().parents[1] / "benchmark"
ROOT = BENCHMARK / "host-config"
KIND = PATH = ".claude/settings.json"
SUBJECT = f"claude-code {PATH}"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"host_config_controls_{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


expected = _module("expected")
score = _module("score")
replay = _module("replay")
ABSENT = score.cold.ABSENT

CONTROLS = {
    "positive": (
        {"permissions": {"allow": ["Bash(npm test)"]}},
        {"permissions": {"allow": ["Bash(npm test)", "Bash(*)"]}},
    ),
    "narrowing": (
        {"permissions": {"allow": ["Bash(npm test)", "Bash(*)"]}},
        {"permissions": {"allow": ["Bash(npm test)"]}},
    ),
    "neutral": (
        {"permissions": {"allow": ["Bash(npm test)", "Read(src/**)"]}},
        {"permissions": {"allow": ["Read(src/**)", "Bash(npm test)"]}},
    ),
}


def _texts(name: str) -> tuple[str, str]:
    base, head = CONTROLS[name]
    # The neutral control is also reformatted: layout is not a capability.
    return json.dumps(base), json.dumps(head, indent=4) + "\n"


def _row(change: dict[str, Any], *, expands: bool) -> dict[str, Any]:
    name = change["key"].split(":", 1)[1]
    added = change["direction"] == "added"
    return {
        "subject": SUBJECT,
        "direction": change["direction"],
        "before": ABSENT if added else name,
        "after": name if added else ABSENT,
        "expands": expands,
    }


def _correct(expectation: dict[str, Any]) -> list[dict[str, Any]]:
    return [_row(c, expands=c["semantic_direction"] == "widening") for c in expectation["changes"]]


def _zero_output(expectation: dict[str, Any]) -> list[dict[str, Any]]:
    return []


def _non_expanding(expectation: dict[str, Any]) -> list[dict[str, Any]]:
    return [_row(c, expands=False) for c in expectation["changes"]]


def _false_widening(expectation: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [_row(c, expands=True) for c in expectation["changes"]]
    if not expectation["changes"]:
        rows.append({"subject": SUBJECT, "direction": "added", "before": ABSENT, "after": "Bash(npm test)", "expands": True})
    return rows


def _record(name: str, rows: list[dict[str, Any]], expectation: dict[str, Any]) -> dict[str, Any]:
    return {
        "case": name, "kind": KIND, "path": PATH, "stop_point": "comparison_comparable",
        "payload": {"comparison_status": "comparable", "incomparable_reasons": [], "rows": rows},
        "expectation": expectation,
    }


def _passes(name: str, record: dict[str, Any]) -> bool:
    outcome = score.score_record(record)
    on_file = [row for row in record["payload"]["rows"] if str(row.get("subject", "")).endswith(" " + PATH)]
    if name == "positive":
        return (
            outcome["expected_widenings"] >= 1
            and sum(row.get("expands") is True for row in on_file) == outcome["expected_widenings"]
            and outcome["widenings_named"] == outcome["expected_widenings"]
            and outcome["correct_rows"] == outcome["rows_on_file"]
        )
    if name == "narrowing":
        return (
            outcome["expected_changes"] >= 1
            and outcome["expected_widenings"] == 0
            and outcome["correct_rows"] == outcome["expected_changes"] == outcome["rows_on_file"]
            and not any(row.get("expands") for row in on_file)
        )
    return bool(outcome["benign_case"] and outcome["benign_zero_row"])


def test_the_oracles_import_nothing_from_the_engine() -> None:
    for path in (ROOT / "expected.py", BENCHMARK / "cold-start" / "expected.py", ROOT / "score.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not {name for name in imported if name.split(".")[0] == "agents_shipgate"}, path


def test_the_controls_derive_the_directions_they_stand_for() -> None:
    directions = {name: [c["semantic_direction"] for c in expected.expected(KIND, *_texts(name))["changes"]] for name in CONTROLS}

    assert directions == {"positive": ["widening"], "narrowing": ["narrowing"], "neutral": []}
    assert expected.expected(KIND, *_texts("neutral"))["scope"] == "supported"


@pytest.mark.parametrize(
    ("implementation", "failing"),
    [(_correct, set()), (_non_expanding, {"positive"}), (_zero_output, {"positive", "narrowing"}), (_false_widening, {"narrowing", "neutral"})],
    ids=["correct", "non-expanding", "zero-output", "false-widening"],
)
def test_a_zero_output_or_false_widening_implementation_fails_the_controls(implementation, failing: set[str]) -> None:
    failed = set()
    for name in CONTROLS:
        expectation = expected.expected(KIND, *_texts(name))
        if not _passes(name, _record(name, implementation(expectation), expectation)):
            failed.add(name)

    assert failed == failing


def test_the_engine_passes_every_control(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    for name in CONTROLS:
        base_text, head_text = _texts(name)
        repo = tmp_path / name
        repo.mkdir()
        replay._git(repo, "init", "-q", "-b", "main")
        base = replay._commit(repo, PATH, base_text, "base")
        replay._commit(repo, PATH, head_text, "head")
        result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", base, "--json"])
        payload = json.loads(result.stdout)
        assert payload["comparison_status"] == "comparable", (name, payload.get("incomparable_reasons"))
        expectation = expected.expected(KIND, base_text, head_text)
        record = _record(name, payload.get("rows") or [], expectation)
        record["payload"] = payload
        assert _passes(name, record), (name, payload.get("rows"))
