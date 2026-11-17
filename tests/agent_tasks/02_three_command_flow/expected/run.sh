#!/usr/bin/env bash
# Deterministic baseline for task 02. Mirrors the canonical 4-call flow
# from the v0.6 agent-friendly adoption plan.
set -euo pipefail
task_python="${AGENTS_SHIPGATE_TASK_PYTHON:-python}"

# 1. detect (read-only; emits JSON for the next step to consume)
"$task_python" -m agents_shipgate detect --workspace . --json

# 2. init --write --ci (auto-generates a near-complete manifest + workflow)
"$task_python" -m agents_shipgate init --workspace . --write --ci

# 3. scan --suggest-patches (attaches patches to every active finding)
"$task_python" -m agents_shipgate scan \
    -c shipgate.yaml \
    --suggest-patches \
    --format json \
    --ci-mode advisory

# 4. apply-patches (mutates only high-confidence patches)
"$task_python" -m agents_shipgate apply-patches \
    --from agents-shipgate-reports/report.json \
    --confidence high \
    --apply

# 5. Replace ALL CHANGE_ME placeholders.
#
# The LangChain starter has no `Agent(name="…")` literal, so auto-init
# also emits `agent.name: CHANGE_ME` in addition to the seeded
# `declared_purpose: [- CHANGE_ME]`. The post-flow assertion forbids any
# CHANGE_ME survivor in shipgate.yaml, so we replace both.
"$task_python" - <<'PY'
from pathlib import Path
text = Path("shipgate.yaml").read_text()
text = text.replace("name: CHANGE_ME", "name: support-case-reader")
text = text.replace("- CHANGE_ME", "- look up support cases for the agent")
Path("shipgate.yaml").write_text(text)
PY
