# Exec-equivalent Bash permission ratings (#824)

A trailing wildcard after these exact launcher prefixes leaves code, a command,
or a package/image to execute selectable by the caller. Agents Shipgate rates
these allow declarations `access: admin`, `risk: critical`, the same review tier
as `Bash(*)`. This is a declaration rating, not a claim that it matches every
Bash command, overrides a deny/ask rule, bypasses a sandbox, or ran any code.

[Claude Code's wildcard rules](https://code.claude.com/docs/en/permissions#wildcard-patterns)
make `Bash(prefix *)` and `Bash(prefix:*)` equivalent spellings. Only these two
spellings after the exact prefixes below enter this tier. The launcher semantics
come from the following primary documentation (reviewed 2026-09-27):

| Prefix before the wildcard | Why arbitrary code remains selectable | Source |
| --- | --- | --- |
| `python -c`, `python3 -c` | Command string supplied by caller | [Python command line](https://docs.python.org/3/using/cmdline.html#cmdoption-c) |
| `node -e` | Evaluate caller-supplied script | [Node CLI](https://nodejs.org/api/cli.html#-e---eval-script) |
| `ruby -e` | Evaluate caller-supplied program | [Ruby options](https://docs.ruby-lang.org/en/master/language/options_md.html) |
| `perl -e` | Evaluate caller-supplied program | [perlrun](https://perldoc.perl.org/perlrun) |
| `bash -c` | Execute caller-supplied command string | [Bash invocation](https://www.gnu.org/software/bash/manual/html_node/Invoking-Bash.html) |
| `sh -c` | Execute caller-supplied command string | [POSIX sh](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/sh.html) |
| `npx` | Select package executable or command | [npm npx](https://docs.npmjs.com/cli/v11/commands/npx/) |
| `bunx` | Select package executable | [Bun bunx](https://bun.com/docs/pm/bunx) |
| `pnpm dlx` | Select package executable | [pnpm pnx/dlx](https://pnpm.io/cli/pnx) |
| `uvx` | Select tool executable | [uv tools](https://docs.astral.sh/uv/concepts/tools/) |
| `uv run` | Select command, script or interpreter invocation | [uv run](https://docs.astral.sh/uv/concepts/projects/run/) |
| `docker exec` | Select container and command | [Docker exec](https://docs.docker.com/reference/cli/docker/container/exec/) |
| `docker run` | Select image, command and options | [Docker run](https://docs.docker.com/reference/cli/docker/container/run/) |
| `xargs` | Select command to execute | [GNU xargs](https://www.gnu.org/software/findutils/manual/html_node/find_html/Invoking-xargs.html) |
| `env` | Select command in modified environment | [GNU env](https://www.gnu.org/s/coreutils/manual/html_node/env-invocation.html) |

The table is bounded, not a complete shell capability analysis. It does not infer
executable identity through paths, wrappers, aliases, case folding, shell syntax,
additional options or fixed operands. Exact `Bash(npx prettier --check .)` is
outside the tier, as are `Bash(npx prettier *)`, `Bash(npm test *)`, `find`, `make`,
`sed` and `gh api`. An exclusion is not a safety claim. In particular `bash -e`
and `ruby -c` are not the eval forms above. Unsupported forms retain their prior
rating rather than being guessed into this table.

`subsumes()` never consults this table. For example, `Bash(npx *)` remains narrower
than `Bash(*)` in the matching language, despite their equal review rating. No
widening/narrowing claim or expansion signal is introduced by the tier itself.
Ask and deny declarations retain `none`/`low`.

Newly granted tier rules follow `SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW`
(`block`) rather than `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED`
(`require_review`). Existing containment suppression still applies: a decided
narrowing from an already broader allow does not become an expansion solely
because its new rule has this rating. No check ID is added or removed.

Check evidence and rows preserve only the exact table prefix and canonical ` *`.
The helper returns a member of a fixed string table, never a user-provided
operand. Other arguments remain redacted. This distinguishes launcher grants
without disclosing code, paths, package names, credentials or other operands.
