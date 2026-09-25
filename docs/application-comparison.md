# Application comparison without prior setup

**Unreleased source feature.** The published 1.1.0 package does not yet have
`diff --application`. Run the source checkout's `./shipgate`, or a build
containing this feature.

For an OpenAI Agents SDK or Google ADK application, compare committed PR refs:

```bash
agents-shipgate diff --application --workspace /path/to/repository \
  --base BASE_SHA --head HEAD_SHA --scope backend
```

No `init`, manifest, saved baseline or authored declaration is required. The
command writes no files in the subject repository and runs no application code.
Fetch the refs first; the command never fetches. `--scope` defaults to the
repository root; `--head` defaults to committed `HEAD`, excluding dirty edits.
The base is the requested base ref's merge base with the selected head.

The result identifies each observed agent object and its added, removed or
changed tool bindings. It shows before/after signatures, binding and function
locations, and implementation digests. Existing reader observations supply the
binding edges; unrelated tool definitions are not agent capabilities. A missing
deployment-root selection does not hide known per-agent edges.

```text
ADDED  synthesizer_agent → python_exec
  before: no observed binding
  after: python_exec(code, dataset_json) -> str at backend/app/agents/synthesizer.py:38
```

A reviewer can inspect the new callable and decide whether that agent should
receive it. A body change is a request to review the implementation; it does not
establish widening, narrowing, business impact or runtime behavior.

## Scopes, moves and incomplete inputs

For an application directory moved by the PR, select its old location separately:

```bash
agents-shipgate diff --application --base BASE_SHA --head HEAD_SHA \
  --base-scope old/application --scope new/application --json
```

The two scopes are discovered independently. Exact Git blob renames supply file
correspondence, preserving original evidence locations; file identity is not a
claim about deployed agent identity. An absent directory is absence at that
Git path, not proof that no agent exists anywhere else. Missing manifests do not
supply an empty base.

`--json` emits `application_comparison_schema_version: "0.1"`, engine identity,
requested and compared refs/tree IDs, per-side scope/coverage, rows, source
correspondence, and a deterministic `comparison_id`. This is a separate advisory
artifact from the existing host diff JSON and verifier receipt.

- `compared`: the selected supported source observations were compared.
- `partial`: a parse/discovery/binding gap remains. Proven positive observations
  may be shown; unread input cannot establish absence or removal.
- `not_established`: neither side established a supported application agent.
- Exit 2: refs/materialization/input could not be read. This is not no change.

Discovery is bounded by `--max-python-files` (default 1000) and a 2 MB per-Python
file limit. Partial discovery remains visible. The first increment uses existing
SDK/ADK readers; unresolved imports, dynamic factories and built-ins remain
explicit reader limitations. It does not support other application frameworks
yet. Indirect helper effects, runtime loading, deployed reachability and business
authority are outside this comparison. It grants no release or merge permission
and cannot stand in for a reviewed verifier base or qualification evidence.
