"""V2 — Designed report card, paired with a realistic editor screenshot of the
actual sample files (shipgate.yaml + refund_agent.py).

Left = the raw input the team checked into git, syntax-highlighted with Pygments
inside an editor-like chrome. Right = the designed shipgate report card.

The narrative spine: the team's prohibited-actions list literally says "issue
refund without approval", but the tool surface ships stripe.create_refund with
no approval policy declared — and shipgate detects exactly that gap.
"""
import asyncio
import html
from pathlib import Path

from playwright.async_api import async_playwright
from pygments import highlight
from pygments.formatters.html import HtmlFormatter
from pygments.lexers.data import YamlLexer
from pygments.lexers.python import PythonLexer

OUT_DIR = Path(__file__).resolve().parent
REPO = OUT_DIR.parents[3]
OUT = OUT_DIR / "v2-product-ui.png"

SAMPLE_YAML = (REPO / "samples/support_refund_agent/shipgate.yaml").read_text()
SAMPLE_PY = (REPO / "samples/support_refund_agent/agents/refund_agent.py").read_text()

# Hand-trimmed slide excerpt of the actual shipgate.yaml — keeps the
# narrative-load-bearing sections (declared_purpose, prohibited_actions,
# tool_sources, permissions) and elides the rest with `# …` markers so the
# viewer knows it continues.
YAML_TRIMMED = """version: "0.1"

project:
  name: support-refund-agent
  owner: support-platform

agent:
  name: refund-assistant
  sdk: { type: openai-agents, entrypoint: agents/refund_agent.py }
  declared_purpose:
    - answer refund policy questions
    - prepare refund requests for human review
    - update support ticket notes
  prohibited_actions:
    - issue refund without approval
    - cancel order without explicit confirmation
    - send external email without preview

environment:
  target: production_like

tool_sources:
  - { id: support_openapi,    type: openapi, path: specs/support-tools.openapi.yaml }
  - { id: support_mcp_tools,  type: mcp,     path: .agents-shipgate/mcp-tools.json }
  - { id: wildcard_mcp_tools, type: mcp,     path: .agents-shipgate/wildcard-tools.json }
  - { id: openai_sdk_static,  type: openai_agents_sdk, path: agents/refund_agent.py }

permissions:
  scopes:
    - zendesk:tickets:read
    - zendesk:tickets:write
    - stripe:*
  credential_mode: service_account

# … policies, risk_overrides, checks, ci, output omitted"""



# Brand-tinted Pygments theme that matches the cream/navy palette
PYGMENTS_THEME_CSS = """
.codeblock { font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace; font-size: 11.5px; line-height: 1.5; }
.codeblock .hll { background-color: #F0E9D6 }
.codeblock .c, .codeblock .ch, .codeblock .cm, .codeblock .cpf, .codeblock .c1, .codeblock .cs { color: #8B7E68; font-style: italic } /* Comment */
.codeblock .k, .codeblock .kc, .codeblock .kd, .codeblock .kn, .codeblock .kp, .codeblock .kr, .codeblock .kt { color: #1A2530; font-weight: 700 }
.codeblock .nt { color: #2A3540; font-weight: 600 }   /* yaml key */
.codeblock .nb, .codeblock .nc, .codeblock .nf, .codeblock .nn { color: #1A2530; font-weight: 600 }
.codeblock .l, .codeblock .ld, .codeblock .s, .codeblock .s1, .codeblock .s2, .codeblock .se, .codeblock .sx, .codeblock .sb, .codeblock .sc, .codeblock .sd, .codeblock .sh, .codeblock .si, .codeblock .sr, .codeblock .ss { color: #6B7B4F }    /* strings -> warm green */
.codeblock .m, .codeblock .mf, .codeblock .mh, .codeblock .mi, .codeblock .mo, .codeblock .il { color: #C76A2C }   /* numbers -> warm orange */
.codeblock .o, .codeblock .ow { color: #6B5F4D }
.codeblock .p { color: #6B5F4D }
.codeblock .err { color: #B8392F }
.codeblock .gh { color: #1A2530; font-weight: 700 }
.codeblock .linenos { color: #B8AB91; padding-right: 12px; user-select: none; border-right: 1px solid #E5DCC6; margin-right: 12px; }
.codeblock pre { margin: 0; padding: 0; background: transparent; }
"""


def render_code(src: str, lexer, start_line: int = 1) -> str:
    formatter = HtmlFormatter(
        cssclass="codeblock",
        linenos="inline",
        linenostart=start_line,
        nobackground=True,
        wrapcode=True,
    )
    out = highlight(src, lexer, formatter)
    return out


YAML_HTML = render_code(YAML_TRIMMED, YamlLexer())
PY_HTML = render_code(SAMPLE_PY, PythonLexer())
# Keep PY_HTML available in case we want to add it back, but the slide focuses
# on shipgate.yaml since that's where the declared release contract lives.
_ = PY_HTML


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><style>
:root {
  --cream: #F5F0E5;
  --cream-2: #ECE5D5;
  --paper: #FBF7EE;
  --editor-bg: #FAF6EB;
  --editor-bar: #E9E0CB;
  --navy: #1A2530;
  --navy-2: #2A3540;
  --muted: #6B5F4D;
  --muted-2: #8B7E68;
  --rule: #D4CCB8;
  --rule-soft: #E5DCC6;
  --critical: #B8392F;
  --critical-bg: #F4D9D6;
  --high: #C76A2C;
  --high-bg: #F4E2D0;
  --medium: #B89530;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  background: var(--cream);
  color: var(--navy);
  font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Helvetica, Arial, sans-serif;
}
body {
  width: 1920px; height: 1080px;
  padding: 56px 72px 56px 72px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.header {
  display: grid; grid-template-columns: 1fr auto;
  align-items: end; gap: 40px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--rule);
}
.kicker {
  font-size: 16px; letter-spacing: 0.18em; text-transform: uppercase;
  color: var(--muted); font-weight: 600; margin-bottom: 6px;
}
h1 {
  font-size: 54px; line-height: 1.05; font-weight: 700;
  margin: 0; letter-spacing: -0.02em;
}
h1 .accent { color: var(--critical); }
h1 .dim    { color: var(--muted-2); }
.cmd {
  font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace;
  font-size: 15px; color: var(--navy-2);
  background: var(--cream-2);
  padding: 11px 16px;
  border-left: 3px solid var(--navy);
  border-radius: 2px;
  white-space: nowrap;
}
.cmd .prompt { color: var(--muted); margin-right: 6px; }

.body {
  display: grid; grid-template-columns: 1fr 1.05fr; gap: 32px;
  flex: 1; min-height: 0;
}

/* === Left side: editor look === */
.editor {
  background: var(--editor-bg);
  border: 1px solid var(--rule);
  border-radius: 10px;
  display: flex; flex-direction: column;
  overflow: hidden;
  box-shadow: 0 2px 0 rgba(26,37,48,0.04);
}
.editor-bar {
  background: var(--editor-bar);
  border-bottom: 1px solid var(--rule);
  display: flex; align-items: stretch; gap: 0;
  padding-left: 14px;
  height: 38px;
}
.editor-bar .dots { display: flex; align-items: center; gap: 7px; padding-right: 14px; border-right: 1px solid var(--rule); margin-right: 4px; }
.editor-bar .dot { width: 11px; height: 11px; border-radius: 50%; background: #C7BCA1; }
.tab {
  display: flex; align-items: center; gap: 6px;
  padding: 0 16px;
  font-size: 13px;
  color: var(--muted-2);
  border-right: 1px solid var(--rule);
  font-family: "SF Mono", Menlo, monospace;
}
.tab.active { background: var(--editor-bg); color: var(--navy); font-weight: 600; }
.tab .ic { font-size: 11px; color: var(--medium); }
.tab.py .ic { color: #4F6B4F; }
.tab.json .ic { color: var(--muted); }
.editor-bar .path {
  margin-left: auto; padding: 0 18px;
  display: flex; align-items: center;
  font-size: 11.5px; color: var(--muted-2);
  font-family: "SF Mono", Menlo, monospace;
}

.editor-body {
  flex: 1; min-height: 0;
  display: flex; flex-direction: column;
  overflow: hidden;
}
.file-section {
  padding: 14px 18px 6px 18px;
}
.file-section + .file-section {
  border-top: 1px dashed var(--rule);
  margin-top: 6px;
}
.file-section-label {
  font-family: "SF Mono", Menlo, monospace;
  font-size: 10.5px;
  color: var(--muted-2);
  letter-spacing: 0.06em;
  margin-bottom: 6px;
  display: flex; justify-content: space-between;
}
.file-section-label .lang {
  background: var(--cream-2);
  padding: 1px 6px; border-radius: 3px; color: var(--muted);
  letter-spacing: 0.1em; text-transform: uppercase; font-weight: 700;
  font-size: 9.5px;
}

/* highlight key narrative blocks via box-shadow on lines */
.editor-body .codeblock { padding: 0 4px; }
.editor-body .annot {
  position: relative;
  display: inline-block;
  background: var(--high-bg);
  color: var(--high);
  font-family: "SF Mono", Menlo, monospace;
  font-size: 10.5px;
  padding: 2px 8px;
  border-radius: 3px;
  letter-spacing: 0.04em;
  font-weight: 700;
  margin-left: 10px;
}
.editor-body .annot.crit { background: var(--critical-bg); color: var(--critical); }

PYGMENTS_PLACEHOLDER

/* === Right side: report card (unchanged structure) === */
.panel {
  background: var(--paper);
  border: 1px solid var(--rule);
  border-radius: 10px;
  padding: 24px 28px 22px 28px;
  display: flex; flex-direction: column; gap: 12px;
  overflow: hidden; min-height: 0;
}
.panel-head {
  display: flex; justify-content: space-between; align-items: baseline;
  border-bottom: 1px solid var(--rule-soft);
  padding-bottom: 10px;
}
.panel-head .col-label {
  font-size: 10.5px; letter-spacing: 0.22em; text-transform: uppercase;
  color: var(--muted-2); font-weight: 700;
}
.panel-head h2 {
  font-size: 19px; font-weight: 700; margin: 4px 0 0 0; letter-spacing: -0.005em;
}
.panel-head .file {
  font-size: 11.5px; color: var(--muted); font-family: "SF Mono", Menlo, monospace;
  text-align: right; line-height: 1.5;
}

.verdict {
  display: flex; align-items: center; gap: 14px;
  background: var(--critical-bg);
  border-left: 4px solid var(--critical);
  padding: 12px 16px; border-radius: 4px;
}
.verdict .icon {
  width: 26px; height: 26px; border-radius: 50%;
  background: var(--critical); color: var(--paper);
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 15px;
  font-family: "SF Mono", Menlo, monospace;
}
.verdict .text { font-size: 16px; font-weight: 700; color: var(--navy); }
.verdict .text small {
  display: block; font-size: 12px; color: var(--muted);
  font-weight: 500; margin-top: 2px;
}

.counts {
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px;
}
.count {
  background: var(--cream-2);
  border: 1px solid var(--rule-soft);
  border-radius: 6px;
  padding: 9px 11px;
}
.count .n { font-size: 22px; font-weight: 700; color: var(--navy); line-height: 1; }
.count .lbl { font-size: 9.5px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); margin-top: 4px; font-weight: 600; }
.count.crit .n { color: var(--critical); }
.count.high .n { color: var(--high); }
.count.med  .n { color: var(--medium); }

.findings-head {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-top: 2px;
}
.findings-head h3 {
  font-size: 11px; letter-spacing: 0.18em; text-transform: uppercase;
  color: var(--muted-2); margin: 0; font-weight: 700;
}
.findings-head .more { font-size: 10.5px; color: var(--muted); font-family: "SF Mono", Menlo, monospace; }

.finding {
  display: grid;
  grid-template-columns: 64px 1fr;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--rule-soft);
  align-items: start;
}
.finding:last-child { border-bottom: none; }
.sev {
  font-size: 9.5px; letter-spacing: 0.12em; text-transform: uppercase;
  font-weight: 700;
  padding: 3px 5px; border-radius: 3px;
  text-align: center;
  font-family: "SF Mono", Menlo, monospace;
}
.sev.crit { background: var(--critical-bg); color: var(--critical); }
.sev.high { background: var(--high-bg); color: var(--high); }
.f-body .id {
  font-family: "SF Mono", Menlo, monospace;
  font-size: 11px; color: var(--muted);
  margin-bottom: 2px;
}
.f-body .id .tool {
  color: var(--navy);
  background: var(--cream-2);
  padding: 1px 5px; border-radius: 3px;
  margin-left: 4px;
}
.f-body .title {
  font-size: 13px; color: var(--navy); font-weight: 500; line-height: 1.42;
}
.f-body .title code { background: var(--cream-2); padding: 1px 4px; border-radius: 2px; font-family: "SF Mono", Menlo, monospace; font-size: 11.5px; }

.foot-summary {
  display: flex; justify-content: space-between;
  font-size: 11px; color: var(--muted);
  border-top: 1px solid var(--rule-soft);
  padding-top: 11px;
  font-family: "SF Mono", Menlo, monospace;
  margin-top: 2px;
}
.foot-summary .pill { padding: 2px 7px; background: var(--cream-2); border-radius: 3px; margin-right: 5px; }

.slide-foot {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 13px; color: var(--muted);
  padding-top: 4px;
}
.brand { display: flex; align-items: center; gap: 10px; font-weight: 600; letter-spacing: 0.04em; color: var(--navy); }
.brand .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--navy); }
</style></head>
<body>
  <div class="header">
    <div>
      <div class="kicker">Phase 1 · Static Release-Readiness Scanner</div>
      <h1>What the team <span class="dim">declared</span>,
          what shipgate <span class="accent">detected</span>.</h1>
    </div>
    <div class="cmd"><span class="prompt">$</span>agents-shipgate scan --config support-refund-agent/shipgate.yaml</div>
  </div>

  <div class="body">
    <!-- LEFT: real editor view of the actual sample files -->
    <div class="editor">
      <div class="editor-bar">
        <div class="dots"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
        <div class="tab active"><span class="ic">●</span>shipgate.yaml</div>
        <div class="tab py"><span class="ic">py</span>refund_agent.py</div>
        <div class="tab json"><span class="ic">{ }</span>mcp-tools.json</div>
        <div class="tab json"><span class="ic">[ ]</span>support-tools.openapi.yaml</div>
        <div class="path">samples/support_refund_agent/</div>
      </div>
      <div class="editor-body">
        <div class="file-section">
          <div class="file-section-label">
            <span>shipgate.yaml — declared release contract</span>
            <span class="lang">YAML</span>
          </div>
          YAML_PLACEHOLDER
        </div>
      </div>
    </div>

    <!-- RIGHT: shipgate report -->
    <div class="panel">
      <div class="panel-head">
        <div>
          <div class="col-label">Detected · agents-shipgate scan</div>
          <h2>Release-readiness report</h2>
        </div>
        <div class="file">
          target: production_like<br/>
          evidence coverage: mixed<br/>
          human review: recommended
        </div>
      </div>

      <div class="verdict">
        <div class="icon">✕</div>
        <div class="text">Release blockers detected
          <small>2 critical findings on a financial-action tool · release should not promote</small>
        </div>
      </div>

      <div class="counts">
        <div class="count crit"><div class="n">2</div><div class="lbl">Critical</div></div>
        <div class="count high"><div class="n">14</div><div class="lbl">High</div></div>
        <div class="count med"><div class="n">2</div><div class="lbl">Medium</div></div>
        <div class="count"><div class="n">0</div><div class="lbl">Low</div></div>
        <div class="count"><div class="n">8</div><div class="lbl">Tools scanned</div></div>
      </div>

      <div class="findings-head">
        <h3>Top findings</h3>
        <div class="more">showing 4 of 18 · sorted by severity</div>
      </div>

      <div class="finding">
        <div class="sev crit">Critical</div>
        <div class="f-body">
          <div class="id">SHIP-POLICY-APPROVAL-MISSING<span class="tool">stripe.create_refund</span></div>
          <div class="title">Tool can issue refunds with no declared approval policy — directly contradicts the manifest's prohibited-actions list.</div>
        </div>
      </div>

      <div class="finding">
        <div class="sev crit">Critical</div>
        <div class="f-body">
          <div class="id">SHIP-SIDEFX-IDEMPOTENCY-MISSING<span class="tool">stripe.create_refund</span></div>
          <div class="title">No idempotency key, annotation, or declared idempotency policy — retries can double-refund.</div>
        </div>
      </div>

      <div class="finding">
        <div class="sev high">High</div>
        <div class="f-body">
          <div class="id">SHIP-AUTH-MANIFEST-BROAD-SCOPE</div>
          <div class="title">Manifest declares wildcard permission scope <code>stripe:*</code> — broader than any required tool scope.</div>
        </div>
      </div>

      <div class="finding">
        <div class="sev high">High</div>
        <div class="f-body">
          <div class="id">SHIP-INVENTORY-WILDCARD-TOOLS<span class="tool">wildcard_mcp_tools.*</span></div>
          <div class="title">MCP source declares wildcard tool exposure — full tool surface is unknown at release time.</div>
        </div>
      </div>

      <div class="foot-summary">
        <div>
          <span class="pill">8 tools</span>
          <span class="pill">3 high-risk</span>
          <span class="pill">1 wildcard</span>
          <span class="pill">mcp×3</span>
          <span class="pill">openapi×4</span>
          <span class="pill">sdk×1</span>
        </div>
        <div>report.md · report.json · report.sarif</div>
      </div>
    </div>
  </div>

  <div class="slide-foot">
    <div class="brand"><span class="dot"></span>Three Moons Lab · A working thesis · April 2026</div>
    <div style="font-family:'SF Mono',Menlo,monospace; font-size:13px; letter-spacing:0.08em;">08 / 13</div>
  </div>
</body></html>
"""


def assemble() -> str:
    page = HTML
    page = page.replace("PYGMENTS_PLACEHOLDER", PYGMENTS_THEME_CSS)
    page = page.replace("YAML_PLACEHOLDER", YAML_HTML)
    page = page.replace("PY_PLACEHOLDER", PY_HTML)
    return page


async def main():
    page_html = assemble()
    html_path = OUT_DIR / "v2-product-ui.html"
    html_path.write_text(page_html)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=2)
        pg = await context.new_page()
        await pg.goto(f"file://{html_path}")
        await pg.screenshot(path=str(OUT), clip={"x": 0, "y": 0, "width": 1920, "height": 1080})
        await browser.close()
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
