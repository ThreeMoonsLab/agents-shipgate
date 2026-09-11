# Workflow diff regression inputs (#685)

Reduced configuration oracles from the three pinned `DanielLavrushin/b4` commits in `provenance.json`. Source: https://github.com/DanielLavrushin/b4. These are not full upstream files: keep trigger names, job identities, permissions, reusable targets and secret forwarding; replace names and executable payloads with synthetic `echo` steps. The `script` case changes only that synthetic payload. No upstream script is executed. Original full-file SHA-256 values make the reduction auditable; the full public inputs are also replayed separately before delivery.

`base → delegation`: the existing release workflow gains a secret-inheriting reusable call, and a separate workflow file is added. Expect one widened **release** row naming the delegation, plus the independently added workflow row. `delegation → script`: no selected authority changes, hence zero capability rows even though artifact bytes differ.
