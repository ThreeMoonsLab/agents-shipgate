"""V1 — Render the actual report.md as a clean PNG sized for slide use.

Style: brand-cream + navy, terminal-monospace for the report body, signaling
"this is the real artifact, not a mockup."
"""
import asyncio
from pathlib import Path

import markdown
from playwright.async_api import async_playwright

OUT_DIR = Path(__file__).resolve().parent
REPO = OUT_DIR.parents[3]
SRC = REPO / "samples/support_refund_agent/expected/report.md"
OUT = OUT_DIR / "v1-raw-report.png"

CSS = """
:root {
  --cream: #F5F0E5;
  --cream-2: #ECE5D5;
  --navy: #1A2530;
  --navy-2: #2A3540;
  --muted: #6B5F4D;
  --critical: #B8392F;
  --high: #C76A2C;
  --medium: #B89530;
  --rule: #D4CCB8;
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
  padding: 64px 96px;
  display: grid;
  grid-template-columns: 1fr 1.4fr;
  gap: 64px;
}
.left {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.kicker {
  font-size: 18px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 600;
}
h1.headline {
  font-size: 64px;
  line-height: 1.05;
  font-weight: 700;
  margin: 18px 0 28px 0;
  letter-spacing: -0.02em;
}
.subhead {
  font-size: 22px;
  line-height: 1.5;
  color: var(--navy-2);
  max-width: 540px;
}
.subhead em { font-style: italic; color: var(--muted); }
.cmd {
  margin-top: 36px;
  font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace;
  font-size: 18px;
  color: var(--navy-2);
  background: var(--cream-2);
  padding: 14px 20px;
  border-left: 3px solid var(--navy);
  border-radius: 2px;
}
.cmd .prompt { color: var(--muted); margin-right: 10px; }
.footer-row {
  display: flex; justify-content: space-between; align-items: flex-end;
  font-size: 16px; color: var(--muted);
}
.brand {
  display: flex; align-items: center; gap: 12px;
  font-weight: 600; letter-spacing: 0.04em;
  color: var(--navy);
}
.brand .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--navy); }
.report-card {
  background: var(--cream-2);
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 56px 44px 44px 44px;
  font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace;
  font-size: 14.5px;
  line-height: 1.55;
  color: var(--navy);
  overflow: hidden;
  position: relative;
}
.report-card::after {
  content: "";
  position: absolute;
  left: 0; right: 0; bottom: 0;
  height: 120px;
  background: linear-gradient(to bottom, rgba(236,229,213,0) 0%, var(--cream-2) 80%);
  pointer-events: none;
}
.report-card::before {
  content: "agents-shipgate-reports/report.md";
  position: absolute;
  top: -11px; left: 32px;
  background: var(--cream);
  padding: 0 12px;
  font-family: -apple-system, sans-serif;
  font-size: 13px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}
.report-card h1 { font-size: 22px; margin: 0 0 14px 0; font-weight: 700; }
.report-card h2 { font-size: 17px; margin: 22px 0 10px 0; color: var(--navy-2); border-bottom: 1px solid var(--rule); padding-bottom: 6px; }
.report-card h3 { font-size: 15px; margin: 16px 0 6px 0; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.report-card p { margin: 6px 0; }
.report-card ul, .report-card ol { margin: 6px 0 6px 22px; padding: 0; }
.report-card li { margin: 4px 0; }
.report-card code { background: rgba(26,37,48,0.08); padding: 1px 5px; border-radius: 3px; font-size: 13.5px; }
.report-card strong { color: var(--navy); }
.report-card table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 13px; }
.report-card th, .report-card td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--rule); }
.report-card th { color: var(--muted); font-weight: 600; }
/* highlight critical lines */
.report-card .blocked { color: var(--critical); font-weight: 700; }
"""


def truncate_report_for_slide(md: str) -> str:
    # Keep header + Top Findings + Findings By Category headers — drop the long appendix
    # so the page reads at slide distance.
    lines = md.splitlines()
    cut = []
    for line in lines:
        if line.startswith("## Recommended Next Actions"):
            break
        cut.append(line)
    return "\n".join(cut)


def post_process_html(html: str) -> str:
    # Mark BLOCKED line in red
    html = html.replace(
        "Result: BLOCKED - release blockers detected.",
        '<span class="blocked">Result: BLOCKED — release blockers detected.</span>',
    )
    return html


HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
  <div class="left">
    <div>
      <div class="kicker">Phase 1 · Static Release-Readiness Scanner</div>
      <h1 class="headline">When the agent can refund $5,000,<br/>release readiness becomes a CI question.</h1>
      <p class="subhead">
        Scanned a real OpenAI Agents SDK + MCP + OpenAPI tool surface for a customer-support refund agent.
        Static checks alone surfaced <strong>2 critical, 14 high</strong>, blocking the release.
      </p>
      <div class="cmd"><span class="prompt">$</span>agents-shipgate scan --config support-refund-agent/shipgate.yaml</div>
    </div>
    <div class="footer-row">
      <div class="brand"><span class="dot"></span>Three Moons Lab · agents-shipgate v0.5.1</div>
      <div>Slide&nbsp;8 / V1</div>
    </div>
  </div>
  <div class="report-card">
    {report}
  </div>
</body></html>
"""


async def main():
    raw = SRC.read_text()
    raw = truncate_report_for_slide(raw)
    md_html = markdown.markdown(raw, extensions=["tables", "fenced_code"])
    md_html = post_process_html(md_html)
    page_html = HTML_TEMPLATE.format(css=CSS, report=md_html)

    html_path = OUT_DIR / "v1-raw-report.html"
    html_path.write_text(page_html)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=2)
        page = await context.new_page()
        await page.goto(f"file://{html_path}")
        await page.screenshot(path=str(OUT), full_page=False, clip={"x": 0, "y": 0, "width": 1920, "height": 1080})
        await browser.close()
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
