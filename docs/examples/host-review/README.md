# Three host-review cards — prepared assets for #798

- [Claude shell rule](shell/card.md)
- [Remote MCP declaration](mcp/card.md)
- [Workflow token permissions](workflow/card.md)
- [60-second HTML demonstration](demo.html), [transcript](transcript.md), [PNG still](still.png) and [SVG source](still.svg)
- [Captured provenance](provenance.json)

The examples are constructed. The released 1.1.0 wheel (contract 40) was freshly installed into a temporary target; existing environment dependencies were reused. No manifest, policy, baseline, legacy skill, CI installation or account was used for comparison. No hook, workflow or MCP endpoint was executed.

Readiness: assets prepared; visual review and product review still required. This documentation change prepares assets for review; it does not start distribution or a study. The capture scripts used during preparation are not a new product command or fixture framework.

Owner: awaiting assignment. Channels: none selected (at most two). Start date and day-30 checkpoint: unset until the owner selects an authorized experiment. No distribution, recruitment, qualified attempt or unaided first-value observation has occurred. Record any future actual attempts and all failures in the existing #653/#571 pilot records; fixture passes and demo views do not count as first value.

## Restore the exact example history

Each card includes `history.git-export`, a Git data stream (not a shell script). In an empty temporary directory, import the selected card's history, then check out its `change` branch:

```sh
git init
git fast-import < /absolute/path/to/the/card/history.git-export
git checkout change
agents-shipgate diff --base main
```

All three exports were imported into fresh local repositories and their base/head commit IDs matched provenance.json exactly. The demo JavaScript passed a syntax check. The SVG was rendered with a static image renderer and the resulting PNG was visually inspected for readability and clipping. HTML/browser playback remains unverified; review it before publication.

## Compare the malformed-source control

In the imported shell example, replace `.claude/settings.json` with the exact bytes in [malformed-input.txt](shell/malformed-input.txt), then run the same comparison against `main`. [Captured text](shell/malformed-output.txt) and [JSON](shell/malformed-output.json) show `incomparable`; advisory exit 0 is not a covered no-change answer. Restore the imported head afterward with `git restore -- .claude/settings.json`. This control is an uncommitted modification of the recorded shell head, not another committed revision.

The wheel digest was checked against [PyPI's version-specific release metadata](https://pypi.org/pypi/agents-shipgate/1.1.0/json). The exact download URL and upload timestamp are in provenance.json. Package version alone does not identify a source checkout that also reports 1.1.0; these assets record contract 40 and the installed wheel hash.
