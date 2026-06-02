#!/usr/bin/env bash
#
# Run the agent-adoption harness in an isolated local container.
#
# No host checkout is mounted, so an agent that wanders out of its cell cannot
# reach a real working copy (the failure mode an on-host run hit). `smoke` is
# free (mock driver); `run` is paid and needs ANTHROPIC_API_KEY. Results land in
# ./.agents-private/harness-out/ . The GitHub Actions workflow
# (.github/workflows/adoption-harness.yml) is the primary CI-validated path;
# this is the local equivalent.
#
#   ./harness/adoption/run-isolated.sh smoke
#   ANTHROPIC_API_KEY=... ./harness/adoption/run-isolated.sh run \
#       --matrix .agents-private/_realrun.matrix.yaml --budget-usd 5 --agent claude-code
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found. Use the GitHub Actions workflow" \
       "(.github/workflows/adoption-harness.yml) instead." >&2
  exit 1
fi

IMAGE=agents-shipgate-harness
docker build -f Dockerfile.harness -t "$IMAGE" .

mode="${1:-smoke}"; shift || true
out="$(pwd)/.agents-private/harness-out"
mkdir -p "$out"

if [ "$mode" = "smoke" ]; then
  docker run --rm "$IMAGE" smoke
else
  : "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY for a paid run}"
  docker run --rm \
    -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e SHIPGATE_HARNESS_SCOPE_HOME=1 \
    -v "$out:/app/out" \
    "$IMAGE" "$mode" --out /app/out --results-csv /app/out/results.csv "$@"
fi
