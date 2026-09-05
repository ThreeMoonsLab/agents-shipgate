#!/bin/sh
# Fails the build when the agent's tool surface exceeds the
# region's approved capability list.
exec python3 tools/policy_check.py "$@"
