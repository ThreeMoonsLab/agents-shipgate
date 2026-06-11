# Design note: config-bound capability detection

Status: **partially shipped** (v0.12: `insufficient_evidence` fix-task
remedies) + **design for the adapter-level detection** that the first
design-partner pilot showed is missing. Validate any implementation against
a real dynamic-toolkit repo before trusting it; this is exactly the failure
mode that cannot be unit-tested into existence.

## The pilot finding

The first real verifier pilot (an AI-generated PR against a production-like
agent repo, 2026-06) returned `insufficient_evidence` and stalled there.
Root cause: the agent's tool surface was built by a **dynamic toolkit
factory** — a constructor like `Toolkit(actions=config)` whose effective
tool list comes from configuration the AST adapters cannot resolve. Static
extraction saw the factory, assigned low confidence, and the evidence
threshold correctly refused to gate. Correct, but a dead end: nothing told
the agent or the human *which* source hid the authority or *what* the
mechanical remedy was.

Two consequences, two fixes:

1. **The dead-end (shipped, v0.12).** `fix_task` for `insufficient_evidence`
   now names each low-confidence source with the explicit-inventory remedy
   and quotes source warnings. Human-routed by design: declaring an
   inventory asserts what the agent can do, and the agent that wrote the PR
   must not author its own authority evidence.

2. **The blind spot (this design).** A diff that *changes the config* a
   factory consumes — adding `"refund"` to an actions list, or **removing
   the config binding entirely so the toolkit falls back to
   everything-enabled defaults** — produces no capability change in the
   diff, because neither side's tools were statically enumerable. The
   removal case is the dangerous one: deleting a restrictive config line
   *expands* authority while looking like cleanup.

## Design: config-bound removal detection

Premise: we cannot statically resolve dynamic tool lists (and must not start
executing code to try). But we CAN detect the *shape* of the risk:

- **Factory-site facts.** Adapters already emit low-confidence facts for
  dynamic constructs. Extend them with a `config_binding` field when the
  factory call's authority-bearing argument is statically traceable to a
  source: a literal (resolvable — not this design's concern), a name bound
  from a config read (`json.load`, `yaml.safe_load`, `os.environ`,
  `pydantic settings`), or absent (defaults).
- **The detection.** In the base/head diff, for each factory site present on
  both sides, compare `config_binding`:
  - `bound → absent` (config argument removed) ⇒ new check
    `SHIP-CAP-CONFIG-BINDING-REMOVED`, severity high, `blocks_release`
    candidate: "a restriction-bearing config binding was removed from a
    dynamic toolkit; the effective tool surface may have expanded to the
    toolkit's defaults."
  - `bound → bound` with the referenced config file in `changed_files` ⇒
    review item: "the config that binds this toolkit's authority changed;
    static analysis cannot diff the effective tool list — review the config
    delta or declare an explicit inventory."
  - factory **added** with no binding and no matching `tool_inventories`
    entry ⇒ the existing dynamic-toolset warning, upgraded to a finding
    with the inventory remedy (mirrors the ADK adapter's behavior).
- **Trust model unchanged.** Everything above is AST + manifest + diff; no
  import, no execution, no network. Confidence stays explicit: these checks
  assert "authority may have changed invisibly," never "authority is X."
- **One decision engine.** The new findings flow into the normal release
  decision. No special verdict; `insufficient_evidence` thresholds are
  untouched.

## Why not shipped in this pass

The binding-trace heuristic (which argument is "authority-bearing", which
assignments count as config reads) needs calibration against the actual
pilot repo and at least one more dynamic-toolkit codebase, or it will ship
as a false-positive generator that burns the trigger-trust the verifier
depends on. The fix-task remediation shipped first because it is
calibration-free.

## Acceptance for the implementation PR

- Reproduces the pilot scenario: a fixture repo with a config-bound factory
  where removing the config line yields `SHIP-CAP-CONFIG-BINDING-REMOVED`
  on the diff and a `blocked`/`review_required` decision instead of silent
  `insufficient_evidence` parity between base and head.
- Zero new findings on the existing golden samples (no false positives on
  static repos).
- `fix_task` for the new findings carries the inventory remedy with the
  factory's source path and line.
