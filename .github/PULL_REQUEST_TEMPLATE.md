## Summary

- 

## Type

- [ ] Check or risk-model change
- [ ] Input adapter change
- [ ] CLI or GitHub Action behavior
- [ ] Report, schema, or SARIF output
- [ ] Documentation only

## Verification

CI is authoritative for `python -m ruff check .`, `python -m compileall -q src tests`, and `python -m pytest`.

Additional local checks run:

-

## Release-readiness notes

- [ ] No user-code import added to default scan paths
- [ ] No network access added to default scan paths
- [ ] New or changed check IDs are documented in `docs/checks.md`
- [ ] Report/schema changes are additive or documented in `STABILITY.md`
