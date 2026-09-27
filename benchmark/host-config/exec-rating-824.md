# Exec-equivalent rating replay (#824)

The same independent rating oracle runs against main
`1b1f6463e5ae48f447f9427959f797d5cc48f754` and the #824 candidate. The
[before/after records](results/2026-09-27-exec-rating-824/) bind engine distribution
digests. No engine imports occur in `expected.py`; labels derive from its explicit
launcher table and the [documented scope](../../docs/engineering/exec-equivalent-permissions.md).
The oracle was authored after observing engine output by the same coding agent,
not by an independent human rater.

This is a static audit of scoped Bash allow declarations at the **heads** of the
10 Claude settings cases in the frozen 50-case host-config population. The other
40 cases are outside this rating question, not credited as negatives. It does
not measure new grants per PR, live reach, runtime effects or deny precedence.
Whole-tool and non-Bash rules are outside this scoped rating denominator.

| Measure | Main | Candidate |
| --- | ---: | ---: |
| Scored population rules | 97 | 97 |
| Correct population ratings | 96/97 | 97/97 |
| Critical launcher rules found | 0/1 | 1/1 |
| False critical population ratings | 0 | 0 |
| Critical-label precision | undefined (0 labels) | 1/1 |
| Noise: critical launcher rated medium | 1 | 0 |
| Missing population rules | 0 | 0 |
| Scored control rules | 47 | 47 |
| Correct control ratings | 15/47 | 47/47 |
| Critical positive controls found | 0/32 | 32/32 |
| False critical controls | 0 | 0 |
| Critical control precision | undefined (0 labels) | 32/32 |

The 32 positives cover both trailing wildcard spellings for all 16 prefixes.
The 15 negatives include exact npx, scoped test/script commands, excluded utility
families, wrong eval flags and fixed operands. Positive-control recall is kept
beside precision so labelling nothing critical cannot appear to pass. Unit tests
also cover malformed patterns, wrappers, shell syntax and preservation of the
containment lattice. These small, selected denominators are not a population-wide
claim of zero noise or complete arbitrary-code detection.

```sh
PYTHONPATH=/path/to/base/src python benchmark/host-config/exec_rating_replay.py /tmp/before.json
PYTHONPATH=/path/to/candidate/src python benchmark/host-config/exec_rating_replay.py /tmp/after.json
```

Use the candidate checkout's harness/oracle for both runs. Source trees need
bundled package data for the distribution digest. The runner refuses if that
digest changes during measurement. It never executes a declared launcher.
