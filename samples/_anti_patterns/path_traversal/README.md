# Anti-pattern · path traversal

This manifest declares a `tool_sources[].path` that escapes the manifest directory. As of v0.2, Agents Shipgate rejects out-of-tree paths to prevent a malicious manifest from reading arbitrary files.

## Expected behavior

```bash
$ agents-shipgate scan -c shipgate.yaml
Input parsing error: Input path '../../../../etc/passwd' resolves outside manifest directory: /etc/passwd
```

Exit code: `3` (input parse error).

## Why this is an anti-pattern

Tool sources should live alongside their manifest. If you legitimately want to share a spec across multiple manifests in a monorepo, use one of:

- **Symlink** the spec into the manifest directory: `ln -s ../shared/openapi.yaml openapi.yaml`
- **Copy** during CI prep: `cp ../shared/openapi.yaml .` before the scan step
- **Move** the spec inside the manifest tree

The trust posture is documented in `docs/trust-model.md` and tested in `tests/test_inputs.py::test_mcp_loader_rejects_path_traversal`.
