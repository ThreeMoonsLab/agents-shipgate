# Exec-equivalent Bash permission ratings (#824)

A trailing wildcard after these exact launcher prefixes leaves code, a command,
or a package/image to execute selectable by the caller. Agents Shipgate rates
these allow declarations `access: admin`, `risk: critical`, the same review tier
as `Bash(*)`. This is a declaration rating, not a claim that it matches every
Bash command, overrides a deny/ask rule, bypasses a sandbox, or ran any code.

[Claude Code's wildcard rules](https://code.claude.com/docs/en/permissions#wildcard-patterns)
make `Bash(prefix *)` and `Bash(prefix:*)` equivalent spellings. These two
spellings after the exact prefixes below enter this tier, and so does any rule
the containment lattice decides is wider than one of them (see *Wider rules*).
The launcher semantics come from the following primary documentation (reviewed
2026-09-27):

| Prefix before the wildcard | Why arbitrary code remains selectable | Source |
| --- | --- | --- |
| `python -c`, `python3 -c` | Command string supplied by caller | [Python command line](https://docs.python.org/3/using/cmdline.html#cmdoption-c) |
| `node -e`, `node --eval` | Evaluate caller-supplied script | [Node CLI](https://nodejs.org/api/cli.html#-e---eval-script) |
| `node -p`, `node --print` | Evaluate caller-supplied script and print the result | [Node CLI](https://nodejs.org/api/cli.html#-p---print-script) |
| `ruby -e` | Evaluate caller-supplied program | [Ruby options](https://docs.ruby-lang.org/en/master/language/options_md.html) |
| `perl -e`, `perl -E` | Evaluate caller-supplied program | [perlrun](https://perldoc.perl.org/perlrun) |
| `php -r` | Run caller-supplied PHP code | [PHP command line options](https://www.php.net/manual/en/features.commandline.options.php) |
| `bash -c` | Execute caller-supplied command string | [Bash invocation](https://www.gnu.org/software/bash/manual/html_node/Invoking-Bash.html) |
| `sh -c` | Execute caller-supplied command string | [POSIX sh](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/sh.html) |
| `zsh -c` | Execute caller-supplied command string | [zsh invocation](https://zsh.sourceforge.io/Doc/Release/Invocation.html) |
| `pwsh -c` | Execute caller-supplied command string (`-Command`) | [about_Pwsh](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pwsh) |
| `eval` | Execute caller-supplied arguments as a command | [Bash builtins](https://www.gnu.org/software/bash/manual/html_node/Bourne-Shell-Builtins.html) |
| `npx` | Select package executable or command | [npm npx](https://docs.npmjs.com/cli/v11/commands/npx/) |
| `bunx` | Select package executable | [Bun bunx](https://bun.com/docs/pm/bunx) |
| `pnpm dlx` | Select package executable | [pnpm pnx/dlx](https://pnpm.io/cli/pnx) |
| `pnpm exec` | Execute caller-supplied shell command | [pnpm exec](https://pnpm.io/cli/exec) |
| `uvx`, `uv tool run` | Select tool executable (`uvx` is `uv tool run`) | [uv tools](https://docs.astral.sh/uv/concepts/tools/) |
| `uv run` | Select command, script or interpreter invocation | [uv run](https://docs.astral.sh/uv/concepts/projects/run/) |
| `pipx run` | Select package application | [pipx](https://pipx.pypa.io/stable/) |
| `docker exec` | Select container and command | [Docker exec](https://docs.docker.com/reference/cli/docker/container/exec/) |
| `docker run` | Select image, command and options | [Docker run](https://docs.docker.com/reference/cli/docker/container/run/) |
| `xargs` | Select command to execute | [GNU xargs](https://www.gnu.org/software/findutils/manual/html_node/find_html/Invoking-xargs.html) |
| `env` | Select command in modified environment | [GNU env](https://www.gnu.org/s/coreutils/manual/html_node/env-invocation.html) |
| `sudo` | Select command to run as another user | [sudo](https://www.sudo.ws/docs/man/sudo.man/) |

## Wider rules

A rating that let a wider rule sit below a narrower one would let a pull request
clear the block by widening: `Bash(python3 *)` allows every `python3 -c` program,
so it cannot be `medium` while `Bash(python3 -c *)` is `critical`. A Bash allow
rule is therefore also in the tier when `subsumes()` decides it is strictly wider
than a table prefix followed by ` *`: `Bash(python3 *)`, `Bash(python3:*)`,
`Bash(docker *)`, `Bash(npx*)`, `Bash(python3 -c*)` and `Bash(n*)`. That is a
single trailing `*` over text the command `<launcher> <anything>` starts with.
`Bash(n *)` (the command `n`), `Bash(npm *)` and `Bash(python3 -c foo *)` are not
wider than any entry and keep their prior rating, as does a pattern the lattice
cannot decide, such as `Bash(p* -c *)`. The whole tool (`Bash`, `Bash(*)`) keeps
its own critical rating.

Not in the table, though #824 lists launchers "at least": `npm exec`, `npm x`,
`yarn dlx`, `bun x`, `bun -e`, `deno eval`, combined flags such as `bash -lc`,
`podman`/`kubectl exec`, and forwarders such as `nohup`, `timeout`, `nice`,
`time`, `command` and `exec`. Adding `npm exec` or `yarn dlx` would, through the
wider-rule rating, make the common `Bash(npm *)` and `Bash(yarn *)` block, which
is a separate decision; the rest need their own sources.

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
because its new rule has this rating, and neither does respelling a rule
(`Bash(npx:*)` to `Bash(npx *)`), which grants nothing new. No check ID is
added or removed.

Check evidence and rows show a tier rule's canonical argument in table text:
`Bash(npx *)` for an entry, and for a wider rule its own trailing-`*` text,
which is always a prefix of a table entry (`Bash(python3 *)`, `Bash(n*)`) and is
sliced from that entry, never copied from the declaration. Other arguments
remain redacted. This distinguishes launcher grants without disclosing code,
paths, package names, credentials or other operands.
