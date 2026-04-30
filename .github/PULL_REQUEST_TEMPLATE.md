## Summary

- 

## Type

- [ ] Check or risk-model change
- [ ] Input adapter change
- [ ] CLI or GitHub Action behavior
- [ ] Report, schema, or SARIF output
- [ ] Documentation only

## Verification

- [ ] `python -m ruff check .`
- [ ] `python -m compileall -q src tests`
- [ ] `python -m pytest`
- [ ] `agents-shipgate fixture run support_refund_agent`

## Release-readiness notes

- [ ] No user-code import added to default scan paths
- [ ] No network access added to default scan paths
- [ ] New or changed check IDs are documented in `docs/checks.md`
- [ ] Report/schema changes are additive or documented in `STABILITY.md`
