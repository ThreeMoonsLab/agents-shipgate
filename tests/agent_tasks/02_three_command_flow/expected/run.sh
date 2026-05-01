#!/usr/bin/env bash
# Deterministic baseline for task 02. Mirrors the canonical 4-call flow
# from the v0.6 agent-friendly adoption plan.
set -euo pipefail

# 1. detect (read-only; emits JSON for the next step to consume)
agents-shipgate detect --workspace . --json > /tmp/02_detect.json

# 2. init --write --ci (auto-generates a near-complete manifest + workflow)
agents-shipgate init --workspace . --write --ci

# 3. scan --suggest-patches (attaches patches to every active finding)
agents-shipgate scan \
    -c shipgate.yaml \
    --suggest-patches \
    --format json \
    --ci-mode advisory

# 4. apply-patches (mutates only high-confidence patches)
agents-shipgate apply-patches \
    --from agents-shipgate-reports/report.json \
    --confidence high \
    --apply

# 5. Replace the seeded CHANGE_ME purpose with a real description.
python - <<'PY'
from pathlib import Path
text = Path("shipgate.yaml").read_text()
text = text.replace("- CHANGE_ME", "- look up support cases for the agent")
Path("shipgate.yaml").write_text(text)
PY
