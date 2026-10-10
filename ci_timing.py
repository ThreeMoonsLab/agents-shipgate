"""Best-effort hosted timings for whole files, including canceled CI runs.

This observer never selects tests or changes their result. A completed-file
record requires a real teardown for every selected case. Missing records are
unmeasured; an invalidation discards any earlier record for that file/session.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import pytest

PREFIX = "SHIPGATE_TIMING "
SCHEMA = "shipgate.ci_file_timing/v1"
_PHASES = {"setup": 1, "call": 2, "teardown": 4}


@dataclass
class _File:
    selected: int
    completed: int = 0
    phases: dict[str, float] = field(default_factory=lambda: dict.fromkeys(_PHASES, 0.0))
    outcomes: Counter = field(default_factory=Counter)
    invalid: bool = False


class FileTimings:
    """One immutable selected census and distinct worker phase reports."""

    def __init__(self) -> None:
        self.ids: tuple[str, ...] | None = None
        self.cases: dict[str, tuple[str, int, str | None]] = {}
        self.files: dict[str, _File] = {}
        self.invalid = False

    def collection(self, ids: list[str]) -> list[dict]:
        if self.invalid:
            return []
        current = tuple(ids)
        if self.ids is not None:
            if current != self.ids:
                self.invalid = True
                return [{"kind": "invalidate_session", "reason": "worker_collection_mismatch"}]
            return []
        if not current or len(current) > 50000 or len(set(current)) != len(current):
            self.invalid = True
            return [{"kind": "invalidate_session", "reason": "unsupported_collection"}]
        paths = [nodeid.split("::", 1)[0] for nodeid in current]
        if any(not path.startswith("tests/") or not path.endswith(".py")
               or len(path) > 1024 or ".." in PurePosixPath(path).parts
               or "\\" in path for path in paths):
            self.invalid = True
            return [{"kind": "invalidate_session", "reason": "unsupported_collector"}]
        counts = Counter(paths)
        if len(counts) > 1000:
            self.invalid = True
            return [{"kind": "invalidate_session", "reason": "collection_limit"}]
        census = {"kind": "census", "selected": len(current),
                  "files": dict(sorted(counts.items()))}
        if len(json.dumps(census, ensure_ascii=True)) > 65536:
            self.invalid = True
            return [{"kind": "invalidate_session", "reason": "census_output_limit"}]
        self.ids = current
        self.cases = {nodeid: (path, 0, None) for nodeid, path in zip(current, paths, strict=True)}
        self.files = {path: _File(count) for path, count in counts.items()}
        digest = hashlib.sha256()
        for nodeid in current:
            value = nodeid.encode("utf-8", errors="surrogatepass")
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        return [{**census, "node_ids_sha256": digest.hexdigest(),
                 "node_digest_encoding": "length-prefixed-utf8-surrogatepass"}]

    def report(self, nodeid: str, phase: str, duration: float, outcome: str) -> list[dict]:
        if self.invalid or self.ids is None:
            return []
        case = self.cases.get(nodeid)
        if case is None:
            self.invalid = True
            return [{"kind": "invalidate_session", "reason": "unknown_case"}]
        path, seen, setup_outcome = case
        timing = self.files[path]
        if timing.invalid:
            return []
        bit = _PHASES.get(phase)
        if (bit is None or seen & bit or seen & _PHASES["teardown"]
                or not isinstance(duration, int | float) or isinstance(duration, bool)
                or not math.isfinite(duration) or duration < 0
                or outcome not in {"passed", "failed", "skipped"}
                or (phase != "setup" and not seen & _PHASES["setup"])
                or (phase == "call" and setup_outcome != "passed")
                or (phase == "teardown" and (setup_outcome == "passed") != bool(seen & _PHASES["call"]))):
            timing.invalid = True
            return [{"kind": "invalidate_file", "file": path,
                     "reason": "unsupported_or_duplicate_report"}]
        self.cases[nodeid] = (path, seen | bit, outcome if phase == "setup" else setup_outcome)
        timing.phases[phase] += duration
        if not math.isfinite(timing.phases[phase]):
            timing.invalid = True
            return [{"kind": "invalidate_file", "file": path, "reason": "nonfinite_total"}]
        timing.outcomes[outcome] += 1
        if phase != "teardown":
            return []
        timing.completed += 1
        if timing.completed != timing.selected:
            return []
        return [{"kind": "complete_file", "file": path, "selected": timing.selected,
                 "completed": timing.completed, "phase_seconds": timing.phases.copy(),
                 "seconds": math.fsum(timing.phases.values()),
                 "phase_outcomes": dict(sorted(timing.outcomes.items()))}]


class HostedTimings:
    """Controller-only observer registered for the existing load-once CI."""

    def __init__(self, config) -> None:  # noqa: ANN001
        self.config = config
        self.timings = FileTimings()
        self.workers: set[str] = set()
        self.disabled = False

    def _emit(self, events: list[dict]) -> None:
        if self.disabled:
            return
        terminal = self.config.pluginmanager.getplugin("terminalreporter")
        if terminal is None:
            self.disabled = True
            return
        try:
            if events:
                terminal.ensure_newline()
                if terminal._tw.width_of_current_line != 0:
                    terminal.write_line("")
            for event in events:
                payload = {"schema": SCHEMA, "workers_observed": len(self.workers), **event}
                terminal.write_line(PREFIX + json.dumps(payload, ensure_ascii=True, allow_nan=False))
                terminal._tw.flush()
        except Exception:
            # Diagnostic output must not turn passing tests into a failing job.
            self.disabled = True

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids) -> None:  # noqa: ANN001
        if self.disabled:
            return
        try:
            self.workers.add(node.gateway.id)
            self._emit(self.timings.collection(ids))
        except Exception:
            self.disabled = True

    def pytest_runtest_logreport(self, report) -> None:  # noqa: ANN001
        if self.disabled:
            return
        try:
            self._emit(self.timings.report(report.nodeid, report.when, report.duration, report.outcome))
        except Exception:
            self.disabled = True
