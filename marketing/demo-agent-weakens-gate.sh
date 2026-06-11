#!/usr/bin/env bash
# Driver for the "agent deletes the gate" demo recording.
# Record with:  asciinema rec marketing/agent-weakens-gate.cast \
#                 --command "bash marketing/demo-agent-weakens-gate.sh" --overwrite
# Script + voiceover beats: marketing/demo-agent-weakens-gate.md
set -euo pipefail
export TERM="${TERM:-xterm-256color}"

say() { printf '\n\033[1;36m# %s\033[0m\n' "$1"; sleep "${2:-2}"; }
run() { printf '\033[1;33m$ %s\033[0m\n' "$1"; sleep 1; eval "$1"; }

clear 2>/dev/null || true
say "Your coding agent's PR fails the release gate." 2
say "The cheapest way for it to pass? Delete the gate." 2
say "Watch what happens when it tries:" 1

run "agents-shipgate --version"
sleep 1

OUT=$(mktemp -d)/reports
run "agents-shipgate fixture run agent_weakens_gate --out $OUT"
sleep 2

say "merge_verdict: blocked. can_merge_without_human: false." 2
say "Why? The PR comment a reviewer would see:" 1
run "head -40 $OUT/pr-comment.md"
sleep 3

say "Both gate-removal checks are suppression-immune:" 1
run "python3 -c \"import json; r=json.load(open('$OUT/report.json')); [print(' -', b['check_id']) for b in r['release_decision']['blockers']]\""
sleep 2

say "The manifest cannot silence them. Severity floors stop downgrades." 2
say "The agent cannot approve its own boundary change." 2
say "" 1
say "Agents Shipgate — the deterministic merge gate" 1
say "for AI-generated agent capability changes." 3
