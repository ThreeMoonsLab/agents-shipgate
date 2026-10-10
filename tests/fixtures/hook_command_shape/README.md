# Hook command shape fixtures (#934)

`jet_pr303_base.settings.json` and `jet_pr303_head.settings.json` are the
`.claude/settings.json` of CirrusRedOrg/EntityFrameworkCore.Jet (MIT) at the
base `416a2aa46e17c2ea9e9bf4302be5b130bd6c8f37` and head
`0d999b84e6dc62a68a8a553a10ab37b08c8bf226` of pull request #303, the pair
`agents-shipgate diff` 1.2.0 printed as `PreToolUse: command changed
(<not-shown> sha256:f23acba4b10f → <not-shown> sha256:3f1dc36980b7)`.

They are read as data and never run. They are kept outside a `.claude/`
directory so no tool treats them as this repository's own host
configuration.
