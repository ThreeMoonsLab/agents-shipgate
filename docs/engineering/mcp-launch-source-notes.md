# Mutable MCP launch source notes (#825)

A known package runner can select a source that changes between launches. An
added or changed MCP row now appends “launch source is mutable”, or, when both
sides establish it, “launch source moved from pinned … to mutable …”. This is
about the declaration, not whether a package is unsafe, an author should change
an intentional choice, or any code has run. Version pins do not pin transitive
dependencies or prove provenance.

The reader examines only a literal argument list for an exact command name.
It does not look up executable paths, packages, registries, Git refs or images.
The supported grammar is bounded to 64 arguments, 2,048 characters per argument
and 8,192 characters total; interpolation and newline-bearing lists abstain.

| Command and prefix | Source classification | Primary reference |
| --- | --- | --- |
| `npx [-y|--yes] SPEC` | npm package: full three-part version is pinned; missing version, tags and recognized ranges are mutable | [npm package specs](https://docs.npmjs.com/cli/v11/using-npm/package-spec/), [npx](https://docs.npmjs.com/cli/v11/commands/npx/) |
| `bunx [--bun] SPEC` | Same npm spec grammar | [bunx](https://bun.com/docs/pm/bunx) |
| `pnpm dlx SPEC` | Same npm spec grammar | [pnpm pnx/dlx](https://pnpm.io/cli/pnx) |
| `uvx SPEC`, `uvx --from SPEC TOOL` | PyPI exact `==` version is pinned; bare name or recognized range is mutable. Directly after `uvx` only, uv's `NAME@VERSION` is pinned and `NAME@latest` is mutable | [uv tools](https://docs.astral.sh/uv/concepts/tools/), [uv tool versions](https://docs.astral.sh/uv/guides/tools/#requesting-specific-versions) |
| `pipx run [--no-cache] --spec SPEC TOOL` | PyPI as above, or Git URL: full 40-hex commit is pinned; branch/tag/missing ref is mutable | [pipx CLI](https://pipx.pypa.io/stable/reference/cli.html), [pip VCS support](https://pip.pypa.io/en/latest/topics/vcs-support/) |
| `docker run [FLAGS] IMAGE` | `@sha256:` plus 64 lowercase hex digits is pinned; tag or missing digest is mutable, including version-looking tags | [Docker digest pulls](https://docs.docker.com/reference/cli/docker/image/pull/), [docker run](https://docs.docker.com/reference/cli/docker/container/run/) |

Docker FLAGS here are the switches `-i`, `--interactive`, `-t`, `--tty`, `--rm`
and `--init` (`-it` and `-ti` combine the short ones), and the options `-e`,
`--env`, `--env-file`, `-v`, `--volume`, `--mount`, `--network`, `--name`, `-w`,
`--workdir`, `-u`, `--user`, `-p`, `--publish`, `--platform`, `--entrypoint` and
`--pull`, written `FLAG VALUE` or `FLAG=VALUE`. An option's value is skipped: it
is never the selected source and never published. So GitHub's documented
`docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server`
is read as a mutable image. Any other flag before the selected source abstains,
rather than mistaking a flag's value for the package. Other combined flags,
launcher paths, wrapper scripts, shell commands, local paths, npm
aliases/tarballs/`github:` specs, `npx -p`/`--package`, `pipx run` without
`--spec`, flags before a `uvx` source (`--python`, `--with`), `uvx --from` with
`@`, and unsupported requirement syntax also abstain, as does any list holding
`$`, a backtick, `{` or `}`. Git parsing supports literal HTTP(S)/SSH URLs
without credentials, query or fragment. These are deliberately bounded
declaration rules, not full package-manager parsers. A missing note is not
evidence of a pinned source.

Arguments use the existing secret redaction before publication. Classification
rejects a rewritten source (for example an image after `--env API_KEY`, which
that redaction reads as the key's value), except a credential-free literal Git
URL: only its pin state is retained from before path redaction. A redacted path
is never interpreted as a real mutable reference.
Only a selected source that independently passes #819's package publication
rules is printed. Bare package names, Git URLs, unknown tag spellings and other
withheld arguments remain absent from note text. A package-looking argument
elsewhere in the list cannot substitute for the selected source. No script
contents are read. A remote MCP `url` entry is outside this launch question.

`launch_source` is optional on current MCP grants: `null` for an unestablished
source, otherwise `{pin: "pinned" | "mutable", package: string | null}`. It is
omitted from saved baselines and excluded from grant equality and inventory
digests, like the existing display-only package/argument facts. The underlying
configuration digest continues to establish actual changes. Its existing limit
on URL-path-only edits remains: a Git ref change hidden by path redaction alone
does not create a row. So `pipx run --spec git+https://…/repo.git@<sha>` moved to
`…@main`, or to another repository, produces no row and no note; this known
limit is tracked by [#772](https://github.com/ThreeMoonsLab/agents-shipgate/issues/772)
and pinned in `tests/test_mcp_launch_source.py`. A note cannot make a
previously unread delta a widening. Historical baselines lack pin evidence and
cannot support a “moved from pinned” claim.

The note lives in the existing row `why`, shared by diff text/JSON, check,
verifier host comparison and PR comments. It adds no row, severity, check ID,
expansion signal, verdict or merge authority. Removed or currently pinned
sources get no note. A mutable source already present in an otherwise changed
server can be noted as current context, but that does not mean the PR introduced
mutability or qualify it as a new actionable finding under #830.

The source/parser controls are engineering evidence only. The released-build
noise/actionability gate in #830 remains pending; no outreach yield, independent
adjudication or M5 pass is claimed by these notes.
