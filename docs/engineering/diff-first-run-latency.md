# Where `agents-shipgate diff` spends its first run

Status: measurement (phase 1 of #698). Nothing here changes what the command
prints or decides. Folds in the inventory row of #620.

Tracking issues: #698 (first-run latency), #620 (duplicate host/framework
inventory walk), closed #686 and #661 (the earlier tree-reader and Stop-hook
latency fixes this builds on).

## Finding

On the two large repositories from the issue, **74 to 86 percent of a host-mode
`diff` is one Python loop**: the per-entry scope test in
`_materialize_isolated_tree` (`cli/verify/git.py`), which calls
`is_boundary_surface_path` once for every entry of the base tree, and again for
every entry of the wider tree when dependency discovery selects a script. Each
call re-translates 25 glob patterns into regular expressions (`glob_match`,
`core/globbing.py`), about 0.7 ms per path. At 157,000 entries (kibana) and
381,000 entries (azure-rest-api-specs) that is minutes.

Reading and inventorying the repository, which is what both #698 and #620 point
at, is 5 to 10 percent. It is not the dominant cost, so no early-refusal path
was added (see [Early refusal](#early-refusal-was-not-added)).

## Method

`scripts/measure_diff_phases.py` runs the same `run_capability_diff` the CLI
runs, one fresh interpreter per sample, with the opt-in `_perf` instrumentation
(`AGENTS_SHIPGATE_PERF=1`, or the harness's `_perf.enable()`) turned on and the
command's own output discarded. Timing is never printed by the command;
`tests/test_diff_phase_timing.py` pins that the output and exit code are
byte-identical with timing on.

```
python scripts/measure_diff_phases.py --workspace <checkout> --base <merge-base> --repeats 3
python scripts/measure_diff_phases.py --workspace <checkout> --detect --repeats 3   # the #620 walk pair
```

Phases are inclusive and nested (`diff.*` is the command, `archive.*` is the
base tree's Git copy and materialization, `host.*` is the inventory reader).
The harness derives exclusive times and, because `host.*` phases run once per
snapshot read, reports each read separately and by side (head working tree,
base dependency-discovery read, base wider-tree read). Tables below are the
median of three samples.

**Cases.** The head is the working tree at the PR head, the base is the merge
base, both as the issue records them. Real repositories were cloned with
`--filter=blob:none`, the PR head fetched from `refs/pull/N/head` and checked
out; the few base-tree blobs the head checkout does not hold (6 for kibana, 4
for azure-rest-api-specs) were fetched with `git cat-file --batch`, because
`diff` correctly refuses a partial clone that lacks them (`objects_missing`).
Only `agents-shipgate diff` ran against them; none of their code was executed.

| case | base (merge base) | head | tree entries |
|---|---|---|---|
| small (synthetic: `.claude/settings.json`, `.mcp.json`, one workflow, 200 `.py`) | `8b3624b` | next commit | 207 |
| link (synthetic: small plus two directory symlinks `.claude/skills`, `.codex/skills`) | `4071614` | next commit | 63 |
| azure (`Azure/azure-rest-api-specs#46915`) | `9b650bfbcdf1229baa259f9076a2b99f4123937f` | `bd9e505707c4bbe718544529c4268e12d394af9f` | 381,442 |
| kibana (`elastic/kibana#295503`) | `8523ebc7afe93f85d9113d1a75b07be9c0b02b41` | `4f86d34b1c77cfab35c0c4708644c0bf7dddf766` | 157,017 |

The issue names `Azure/azure-rest-api-specs`, not `Azure/azure-dev`; the pins
above are the issue's. The `aburan28/crypto-autoresearcher#1994` case added
later (122,044 tracked entries, an 88 MB blob) was not re-run here.

**Machine.** macOS 26 arm64 (Darwin 25.6.0), Python 3.12.13, source at `origin/main`
`9a9a1d84a0` plus the instrumentation. The machine was shared with other
sessions the whole time: load average (1 minute) was 10 to 18 for the large
cases and 11 to 12 for the small ones, caches were not flushed, and absolute
seconds move with load (the same kibana command measured 205 s at load 7 in a
first run and 275 s at load 16). Read the shares, not the seconds.

## Per-phase medians (seconds, exclusive)

| phase | small | link | azure | kibana |
|---|---:|---:|---:|---:|
| interpreter start + teardown | 1.14 | 0.83 | 1.08 | 1.26 |
| import CLI | 0.06 | 0.05 | 0.10 | 0.13 |
| resolve base (merge-base, shallow check) | 0.23 | 0.20 | 0.16 | 0.28 |
| head: inventory walk | 0.14 | 0.04 | 11.94 | 21.17 |
| head: read + validate (rest of snapshot) | 0.01 | 0.40 | 0.75 | 1.46 |
| base: copy objects (`pack-objects`) | 0.07 | 0.03 | 3.48 | 7.50 |
| base: copy objects (`index-pack`) | 0.03 | 0.03 | 7.34 | 11.72 |
| base: copy objects (`fsck --strict`) | 0.04 | 0.05 | 9.40 | 10.07 |
| base: rev-parse + store init | 0.09 | 0.09 | 0.08 | 0.11 |
| base: `ls-tree` listing + parse | 0.02 | 0.02 | 0.27 | 1.41 |
| base: link scoping | 0.02 | 0.08 | 0.12 | 0.29 |
| **base: classify every entry (`in_scope`)** | 0.12 | 0.03 | **238.94** | **202.94** |
| base: read + write scoped blobs | 0.07 | 0.06 | 0.17 | 0.51 |
| base: links, link targets, verify walk | 0.00 | 0.06 | 0.02 | 0.23 |
| base: materialize, other | 0.10 | 0.06 | 1.75 | 1.85 |
| base: dependency-discovery read | 0.01 | 0.01 | 0.88 | 2.67 |
| base: second read of the wider tree | 0.00 | 0.00 | 0.00 | 3.23 |
| changed-input listing (`git diff`) | 0.29 | 0.20 | 9.73 | 6.25 |
| compare | 0.10 | 0.07 | 0.04 | 0.07 |
| render JSON | 0.00 | 0.00 | 0.01 | 0.00 |
| other (workspace checks, wiring) | 0.12 | 0.07 | 0.42 | 0.47 |
| **wall, median of 3** | **2.8** | **2.5** | **279.3** | **275.0** |
| wall samples | 3 / 3 / 3 | 3 / 3 / 2 | 239 / 279 / 294 | 291 / 275 / 257 |
| load average, per sample | 12 / 12 / 12 | 12 / 12 / 12 | 12 / 10 / 14 | 16 / 13 / 13 |

An unmeasured-overhead check: with instrumentation off, the real CLI measured
311.7 s on kibana at load 18 (`user` 199 s) and 4.4 s on `small` at load 18,
inside the instrumented runs' range once load is accounted for. The probes are
a boolean test at about two dozen call sites per run and none sits inside a
per-entry loop, so there is nothing to subtract.

## What dominates

- **classify** is 85.9 percent of the azure run and 74.2 percent of kibana's
  (kibana runs it twice, once per tree, because dependency discovery selected
  scripts and `archive.materialize_wider` repeated the pass; each pass is
  about 100 s).
- **Inventory** (head walk, base dependency read, base second read) is 4.9
  percent of azure and 10.4 percent of kibana.
- **Copying the base objects** (`pack-objects`, `index-pack`, `fsck --strict`
  of the whole tree, because a scoped archive still packs the entire tree) is
  20 s on azure and 29 s on kibana: 7 and 11 percent.
- Everything else, including the comparison itself, is under 1 percent.

The classify loop calls `in_scope(path)` for every `ls-tree -r -t` entry. The
cost is per call, not per match: with 25 glob patterns,
`glob_match` rebuilds a regex character by character on every call (twice, for
the case-folded retry), and `cProfile` of 5,000 paths puts 95 percent of the
time in that translation and 1 percent in `re.fullmatch`. Measured on the real
listings: 708 µs per call on kibana (157,017 entries, projected 111 s a pass)
and 739 µs on azure (381,442 entries, projected 282 s), against 203 s and 239 s
observed.

## The #620 duplicate walk, as its own phase

`detect_workspace` (`cli/discovery/signals.py`) makes two walks:
`discover_host_boundary` (a filesystem walk, the host reader's
`_repository_paths`) and `_candidate_files` (`git ls-files`). They are different
mechanisms, so "sharing the walk" is not removing a copy of the same work.
**Host-mode `diff` makes neither call**; `detect`, `init`, `first_look` and
`diff --application` do. Median of 3, `--detect`:

| case | host-boundary walk | framework inventory (`git ls-files`) |
|---|---:|---:|
| small | 0.17 | 0.15 |
| azure | 11.01 | 5.09, then `DiscoveryError` (output bound exceeded) |
| kibana | 13.72 | 49.25 |

The duplicate-walk cost of #620 is therefore the smaller walk at best (the
host walk, 11 to 14 s on these two trees) out of 17 to 63 s, and on azure the
second walk ends in a refusal. The `git ls-files` walk, not the duplicated one,
is the larger bill on kibana. This is a different command family from the
`diff` latency above; the two should not be fixed as one change.

## Early refusal was not added

The idea in #698 is that kibana's `incomparable` answer is preceded by 178 s of
inventory work it throws away, so refuse sooner. The measurements do not
support it:

1. The work before the refusal is not inventory. Inventories are 10.4 percent
   of kibana's run; 74 percent is the classify loop and 11 percent is copying
   the base objects, both of which run before any base inventory exists.
2. The refusal cannot be reproduced from one side early. The kibana answer
   names five unreadable link directories, four of them with `side: base` and
   one `side: head`, and its reasons are `base_inventory_incomplete` and
   `head_inventory_incomplete`. Those come from reading both inventories
   (`compare_host_inventories`, `_blocking_coverage`); refusing "the same
   result" before the base is materialized would require producing the base
   side, which is the expensive part. A refusal that skipped it would differ in
   `coverage.items[].side`, which the review text and `verifier.json` print.

So the bounded early refusal the issue suggests would save at most the roughly
10 percent that is inventory, at the price of a second implementation of the
refusal. Not worth its risk (the "second implementation" class of #322).

## Recommended next optimization

Compile each boundary glob once: memoize the pattern-to-regex step in
`core/globbing.glob_match` (the function is pure; the cache key is the
pattern). That removes roughly the 95 percent of `is_boundary_surface_path`
that is translation, and with it most of 74 to 86 percent of both large runs,
without touching which paths are in scope. Do it as its own change with the
existing `glob_match` tests, plus a before/after run of this harness on the same
pins. What is left afterwards, from the table, is about 40 s on azure and 72 s
on kibana: the `index-pack` plus `fsck --strict` of the whole tree for a scoped
archive (41 and 30 percent of that remainder), the head walk (30 and 29
percent), and the `git diff` changed-input listing (24 and 9 percent). Those are
the next candidates, in that order of size, and each needs its own measurement
before a change.

## Reproducing

```
git clone --filter=blob:none --no-checkout https://github.com/elastic/kibana.git kibana
git -C kibana fetch origin refs/pull/295503/head:refs/remotes/origin/pr295503
git -C kibana checkout -q 4f86d34b1c77cfab35c0c4708644c0bf7dddf766
# hydrate the base tree's missing blobs; diff never fetches
git -C kibana rev-list --objects --missing=print --no-walk 8523ebc7afe93f85d9113d1a75b07be9c0b02b41 \
  | grep '^?' | tr -d '?' | git -C kibana cat-file --batch > /dev/null
python scripts/measure_diff_phases.py --workspace kibana \
  --base 8523ebc7afe93f85d9113d1a75b07be9c0b02b41 --repeats 3
```
