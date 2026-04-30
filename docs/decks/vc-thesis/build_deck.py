"""Three Moons Lab — VC thesis discussion deck.

15 slides, 1920x1080 @ 2x, brand palette (cream + navy), with a darker variant
for the philosophical core (Gödel + FEP). Slides 8 + 9 form a DECLARED/DETECTED
diptych showing the source files (refund_agent.py + shipgate.yaml) followed by
the shipgate report on those files.

Run:
    python3 build_deck.py
Output:
    build/slide-01.png … slide-15.png
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import markdown
from playwright.async_api import async_playwright
from pygments import highlight
from pygments.formatters.html import HtmlFormatter
from pygments.lexers.data import YamlLexer
from pygments.lexers.python import PythonLexer

DECK_DIR = Path(__file__).resolve().parent
REPO = DECK_DIR.parents[2]
BUILD_DIR = DECK_DIR / "build"
BUILD_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1920, 1080
TOTAL_SLIDES = 15

# ---------------------------------------------------------------------------
# Shared design system
# ---------------------------------------------------------------------------

BRAND_CSS = r"""
:root {
  --cream:        #F5F0E5;
  --cream-2:      #ECE5D5;
  --cream-3:      #E0D7C0;
  --paper:        #FBF7EE;
  --navy:         #1A2530;
  --navy-2:       #2A3540;
  --navy-deep:    #0E1820;
  --navy-deeper:  #060D14;
  --muted:        #6B5F4D;
  --muted-2:      #8B7E68;
  --muted-dark:   #B5A988;     /* on dark bg */
  --muted-dark-2: #8C8167;
  --rule:         #D4CCB8;
  --rule-soft:    #E5DCC6;
  --rule-dark:    #2A3540;
  --critical:     #B8392F;
  --critical-bg:  #F4D9D6;
  --high:         #C76A2C;
  --high-bg:      #F4E2D0;
  --medium:       #B89530;
  --accent:       #6B7B4F;     /* warm green */
  --accent-dark:  #B5C99B;     /* cream-side */
  --gold:         #D4A847;     /* highlight on dark */
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  font-feature-settings: "liga", "kern";
}
body {
  width: 1920px; height: 1080px;
  background: var(--cream);
  color: var(--navy);
  display: flex; flex-direction: column;
  padding: 72px 96px;
  position: relative;
}
body.dark {
  background: var(--navy-deep);
  color: var(--cream);
}
body.dark .muted { color: var(--muted-dark); }
body.dark .rule { background: var(--rule-dark); }

.kicker {
  font-size: 16px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted-2);
  font-weight: 700;
  margin-bottom: 18px;
}
body.dark .kicker { color: var(--muted-dark-2); }

h1.head {
  font-size: 76px;
  line-height: 1.04;
  font-weight: 700;
  letter-spacing: -0.025em;
  margin: 0;
}
h1.head.med  { font-size: 64px; }
h1.head.sm   { font-size: 56px; }
h1.head .accent  { color: var(--critical); }
h1.head .gold    { color: var(--gold); }
h1.head .dim     { color: var(--muted-2); }
body.dark h1.head .dim { color: var(--muted-dark-2); }
body.dark h1.head { color: var(--cream); }

.lede {
  font-size: 26px;
  line-height: 1.45;
  color: var(--navy-2);
  max-width: 1400px;
  margin-top: 22px;
}
body.dark .lede { color: var(--muted-dark); }

.footer-row {
  position: absolute;
  left: 96px; right: 96px; bottom: 48px;
  display: flex; justify-content: space-between; align-items: center;
  font-size: 14px;
  color: var(--muted);
}
body.dark .footer-row { color: var(--muted-dark-2); }
.brand {
  display: flex; align-items: center; gap: 12px;
  font-weight: 700; letter-spacing: 0.04em;
  color: var(--navy);
}
.brand .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--navy); }
body.dark .brand { color: var(--cream); }
body.dark .brand .dot { background: var(--cream); }

.slide-no {
  font-family: "SF Mono", "JetBrains Mono", Menlo, monospace;
  font-size: 13px;
  letter-spacing: 0.08em;
  color: var(--muted);
}
body.dark .slide-no { color: var(--muted-dark-2); }

.body-content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

/* utility */
.code {
  font-family: "SF Mono", "JetBrains Mono", Menlo, monospace;
}
.tag {
  display: inline-block;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  border-radius: 3px;
  background: var(--cream-2);
  color: var(--muted);
}
.tag.crit { background: var(--critical-bg); color: var(--critical); }
.tag.high { background: var(--high-bg);     color: var(--high); }
.tag.gold { background: rgba(212,168,71,0.18); color: var(--gold); }

.divider { height: 1px; background: var(--rule); width: 100%; }
body.dark .divider { background: var(--rule-dark); }

.callout {
  border-left: 4px solid var(--navy);
  background: var(--cream-2);
  padding: 24px 32px;
  border-radius: 4px;
  font-size: 24px;
  line-height: 1.4;
  color: var(--navy);
}
body.dark .callout { border-color: var(--gold); background: rgba(255,255,255,0.04); color: var(--cream); }

.hairline { border-top: 1px solid var(--rule); padding-top: 18px; }
"""


def page(body_html: str, theme: str = "light", slide_no: int = 0, total: int = TOTAL_SLIDES) -> str:
    body_class = "dark" if theme == "dark" else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>{BRAND_CSS}</style></head>
<body class="{body_class}">
{body_html}
<div class="footer-row">
  <div class="brand"><span class="dot"></span>Three Moons Lab · A working thesis · April 2026</div>
  <div class="slide-no">{slide_no:02d} / {total:02d}</div>
</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Slide content
# ---------------------------------------------------------------------------

def slide_01_cover() -> str:
    body = f"""
<div style="flex:1; display:flex; flex-direction:column; justify-content:center; align-items:flex-start; padding-top:40px;">
  <!-- Real Three Moons Lab logo mark, cropped from brand asset -->
  <img src="file://{BUILD_DIR}/_logo-mark-light.png"
       style="width:160px; height:160px; margin-bottom:48px;"/>
  <div style="font-size:14px; letter-spacing:0.32em; text-transform:uppercase; color:#6B5F4D; font-weight:700; margin-bottom:30px;">
    Three Moons Lab
  </div>
  <h1 class="head" style="font-size:108px; max-width:1500px; margin-bottom:36px;">
    Release readiness<br/>for agentic systems.
  </h1>
  <div style="font-size:30px; color:#2A3540; max-width:1200px; line-height:1.4; font-style:italic;">
    A working thesis — not a pitch.
  </div>
  <div style="margin-top:80px; display:flex; gap:48px; font-size:16px; color:#6B5F4D; font-family:'SF Mono',Menlo,monospace; letter-spacing:0.06em;">
    <div>Wendy · pengfei@threemoonslab.com</div>
    <div>April 2026</div>
    <div>v0.1 — for discussion</div>
  </div>
</div>
"""
    return page(body, "light", 1)


def slide_02_inflection() -> str:
    body = r"""
<div class="kicker">Act 1 · The shift</div>
<h1 class="head">Models that answer.<br/><span class="dim">Agents that</span> act.</h1>

<div class="body-content" style="margin-top:60px;">
  <div style="display:grid; grid-template-columns: 1fr 1fr; gap:48px;">

    <!-- LEFT: LLM call -->
    <div style="background:rgba(0,0,0,0.02); border:1px solid #D4CCB8; border-radius:10px; padding:36px 40px;">
      <div style="font-size:11px; letter-spacing:0.2em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:14px;">
        Yesterday — LLM call
      </div>
      <div style="font-size:28px; font-weight:700; margin-bottom:24px;">
        Input → Output
      </div>
      <svg viewBox="0 0 600 140" style="width:100%; height:120px; margin-bottom:18px;">
        <rect x="20"  y="50" width="160" height="44" fill="#ECE5D5" stroke="#D4CCB8"/>
        <text x="100" y="78" font-family="SF Mono,Menlo,monospace" font-size="16" fill="#1A2530" text-anchor="middle">prompt</text>
        <line x1="190" y1="72" x2="280" y2="72" stroke="#6B5F4D" stroke-width="2" marker-end="url(#a1)"/>
        <rect x="290" y="50" width="160" height="44" fill="#ECE5D5" stroke="#D4CCB8"/>
        <text x="370" y="78" font-family="SF Mono,Menlo,monospace" font-size="16" fill="#1A2530" text-anchor="middle">model</text>
        <line x1="460" y1="72" x2="550" y2="72" stroke="#6B5F4D" stroke-width="2" marker-end="url(#a1)"/>
        <text x="575" y="78" font-family="SF Mono,Menlo,monospace" font-size="16" fill="#1A2530" text-anchor="middle">text</text>
        <defs><marker id="a1" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">
          <polygon points="0 0, 10 5, 0 10" fill="#6B5F4D"/></marker></defs>
      </svg>
      <ul style="font-size:18px; line-height:1.7; color:#2A3540; padding-left:22px; margin:0;">
        <li>stateless</li>
        <li>no real-world side effects</li>
        <li>release risk: <em style="font-style:normal; color:#6B5F4D;">"is the answer wrong?"</em></li>
      </ul>
    </div>

    <!-- RIGHT: Agent loop -->
    <div style="background:rgba(184,57,47,0.04); border:1px solid #B8392F; border-radius:10px; padding:36px 40px;">
      <div style="font-size:11px; letter-spacing:0.2em; text-transform:uppercase; color:#B8392F; font-weight:700; margin-bottom:14px;">
        Today — agent
      </div>
      <div style="font-size:28px; font-weight:700; margin-bottom:24px;">
        Observe → Plan → Tool → Side effect → Memory
      </div>
      <svg viewBox="0 0 600 140" style="width:100%; height:120px; margin-bottom:18px;">
        <!-- a closed loop with tool branching to "world" -->
        <ellipse cx="120" cy="70" rx="80" ry="48" fill="none" stroke="#1A2530" stroke-width="2"/>
        <text x="120" y="58" font-family="SF Mono,Menlo,monospace" font-size="14" fill="#1A2530" text-anchor="middle">observe</text>
        <text x="120" y="78" font-family="SF Mono,Menlo,monospace" font-size="14" fill="#1A2530" text-anchor="middle">plan</text>
        <text x="120" y="98" font-family="SF Mono,Menlo,monospace" font-size="14" fill="#1A2530" text-anchor="middle">memory</text>
        <line x1="200" y1="70" x2="320" y2="70" stroke="#B8392F" stroke-width="2.5" marker-end="url(#a2)"/>
        <text x="260" y="60" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#B8392F" text-anchor="middle" font-weight="700">tool call</text>
        <rect x="320" y="46" width="120" height="48" fill="#F4D9D6" stroke="#B8392F"/>
        <text x="380" y="76" font-family="SF Mono,Menlo,monospace" font-size="15" fill="#B8392F" text-anchor="middle" font-weight="700">side effect</text>
        <line x1="440" y1="70" x2="560" y2="70" stroke="#B8392F" stroke-width="2.5" marker-end="url(#a2)"/>
        <text x="580" y="76" font-family="SF Mono,Menlo,monospace" font-size="14" fill="#1A2530" text-anchor="middle">world</text>
        <defs><marker id="a2" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">
          <polygon points="0 0, 10 5, 0 10" fill="#B8392F"/></marker></defs>
      </svg>
      <ul style="font-size:18px; line-height:1.7; color:#2A3540; padding-left:22px; margin:0;">
        <li>stateful, looping</li>
        <li><strong>real consequences</strong> — refunds, emails, PRs, deploys</li>
        <li>release risk: <em style="font-style:normal; color:#B8392F;">"did the agent do the wrong thing?"</em></li>
      </ul>
    </div>
  </div>

  <div class="callout" style="margin-top:48px;">
    Once an agent can call tools, <strong>every tool change becomes a release event.</strong>
    The release process built for code does not map onto agents.
  </div>
</div>
"""
    return page(body, "light", 2)


def slide_03_new_release_problem() -> str:
    body = r"""
<div class="kicker">Act 1 · The shift</div>
<h1 class="head med">Agent Release Readiness<br/>is a new release decision.</h1>

<div class="body-content" style="margin-top:50px;">
  <div style="font-size:24px; line-height:1.5; color:#2A3540; max-width:1500px; margin-bottom:50px;">
    Bounded assurance that a stochastic, open, tool-using system can enter a higher-permission
    environment — under a declared task scope, tool surface, permission boundary, and risk tier.
  </div>

  <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:32px; margin-bottom:40px;">
    <div style="border:1px solid #D4CCB8; border-radius:10px; padding:28px 32px; background:#FBF7EE;">
      <div style="font-size:11px; letter-spacing:0.2em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:14px;">It is not — software testing</div>
      <div style="font-size:18px; line-height:1.55; color:#2A3540;">
        Software testing assumes a deterministic code path. Agents make their action graph
        <em>at runtime</em> from goals, context, tools, and feedback.
      </div>
    </div>
    <div style="border:1px solid #D4CCB8; border-radius:10px; padding:28px 32px; background:#FBF7EE;">
      <div style="font-size:11px; letter-spacing:0.2em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:14px;">It is not — LLM eval</div>
      <div style="font-size:18px; line-height:1.55; color:#2A3540;">
        Eval scores measure input → output behavior on sampled tasks. They cannot answer
        whether <em>this</em> tool surface, in <em>this</em> environment, is safe to ship.
      </div>
    </div>
    <div style="border:1px solid #D4CCB8; border-radius:10px; padding:28px 32px; background:#FBF7EE;">
      <div style="font-size:11px; letter-spacing:0.2em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:14px;">It is not — runtime SRE</div>
      <div style="font-size:18px; line-height:1.55; color:#2A3540;">
        SLOs, canaries, and observability fire <em>during</em> or <em>after</em> execution.
        Release readiness is the decision <em>before</em> we promote.
      </div>
    </div>
  </div>

  <div style="display:flex; align-items:center; gap:24px; padding:24px 32px; background:#1A2530; color:#F5F0E5; border-radius:10px;">
    <div style="font-size:11px; letter-spacing:0.2em; text-transform:uppercase; color:#B5A988; font-weight:700;">It is</div>
    <div style="font-size:22px; line-height:1.4;">
      An <strong style="color:#D4A847;">evidence-based release decision</strong> over a stochastic, tool-using,
      state-mutating system — graded against a declared operational envelope.
    </div>
  </div>
</div>
"""
    return page(body, "light", 3)


def slide_04_godel() -> str:
    body = r"""
<div class="kicker" style="color:#8C8167;">Act 2 · First principle №1</div>
<h1 class="head med" style="max-width:1700px;">A sufficiently capable agent<br/>cannot self-certify its own readiness.</h1>

<div class="body-content" style="margin-top:60px;">
  <div style="display:grid; grid-template-columns: 1fr 1fr; gap:80px; align-items:center;">

    <!-- LEFT: prose -->
    <div style="font-size:22px; line-height:1.55; color:#B5A988;">
      <p style="margin:0 0 20px 0;">
        Any system rich enough to express its own behavior contains statements
        about itself it cannot prove from within.
      </p>
      <p style="margin:0 0 20px 0;">
        For agents, those statements are about
        <strong style="color:#F5F0E5;">side effects</strong>,
        <strong style="color:#F5F0E5;">long-horizon consequence</strong>,
        and <strong style="color:#F5F0E5;">prompt-injection susceptibility</strong>.
      </p>
      <p style="margin:0; color:#D4A847; font-weight:600;">
        External assurance is not optional. It is structural.
      </p>
    </div>

    <!-- RIGHT: self-reference loop SVG -->
    <div style="display:flex; justify-content:center;">
      <svg width="540" height="400" viewBox="0 0 540 400">
        <!-- outer ring representing agent system -->
        <circle cx="300" cy="200" r="170" fill="none" stroke="#5C6B7C" stroke-width="2.5"/>
        <text x="300" y="50" font-family="-apple-system" font-size="14" fill="#B5A988" text-anchor="middle" letter-spacing="3" font-weight="700">AGENT SYSTEM</text>

        <!-- inner self-referential loop -->
        <path d="M 240 200 Q 240 140 300 140 Q 360 140 360 200 Q 360 260 300 260 Q 260 260 250 230"
              fill="none" stroke="#D4A847" stroke-width="3" marker-end="url(#g_arrow)"/>
        <text x="300" y="205" font-family="SF Mono,Menlo,monospace" font-size="15" fill="#F5F0E5" text-anchor="middle">"am I ready?"</text>

        <!-- impossibility marker at the join -->
        <circle cx="250" cy="230" r="16" fill="#B8392F"/>
        <text x="250" y="237" font-family="-apple-system" font-size="20" fill="#F5F0E5" text-anchor="middle" font-weight="700">⊥</text>

        <!-- external arrow pointing in -->
        <line x1="40" y1="200" x2="125" y2="200" stroke="#B5C99B" stroke-width="3.5" marker-end="url(#g_arrow_ext)"/>
        <text x="82" y="186" font-family="-apple-system" font-size="13" fill="#B5C99B" text-anchor="middle" font-weight="700" letter-spacing="1">EXTERNAL</text>
        <text x="82" y="222" font-family="-apple-system" font-size="13" fill="#B5C99B" text-anchor="middle" font-weight="700" letter-spacing="1">ASSURANCE</text>

        <defs>
          <marker id="g_arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">
            <polygon points="0 0, 10 5, 0 10" fill="#D4A847"/>
          </marker>
          <marker id="g_arrow_ext" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">
            <polygon points="0 0, 10 5, 0 10" fill="#B5C99B"/>
          </marker>
        </defs>
      </svg>
    </div>
  </div>
</div>
"""
    return page(body, "dark", 4)


def slide_05_fep() -> str:
    body = r"""
<div class="kicker" style="color:#8C8167;">Act 2 · First principle №2</div>
<h1 class="head med" style="max-width:1700px;">Tool calls are where uncertainty<br/>escapes into the world.</h1>

<div class="body-content" style="margin-top:60px;">
  <div style="display:grid; grid-template-columns: 1fr 1fr; gap:80px; align-items:center;">

    <!-- LEFT: prose -->
    <div style="font-size:22px; line-height:1.55; color:#B5A988;">
      <p style="margin:0 0 20px 0;">
        Agents act to minimize prediction error under their generative model of reality —
        the <em>Free Energy Principle</em> framing.
      </p>
      <p style="margin:0 0 20px 0;">
        The <strong style="color:#F5F0E5;">tool boundary</strong> is the only channel through which
        an agent's internal uncertainty becomes external side effect.
      </p>
      <p style="margin:0; color:#D4A847; font-weight:600;">
        Release readiness = bounding free energy at the action boundary
        <em style="font-style:normal; color:#B5A988;">before</em> it propagates.
      </p>
    </div>

    <!-- RIGHT: agent → tool boundary → world diagram -->
    <div style="display:flex; justify-content:center;">
      <svg width="540" height="380" viewBox="0 0 540 380">
        <!-- Internal agent state cloud (left) -->
        <ellipse cx="100" cy="190" rx="80" ry="100" fill="rgba(181,201,155,0.08)" stroke="#2A3540" stroke-width="1.5" stroke-dasharray="4 3"/>
        <text x="100" y="80" font-family="-apple-system" font-size="13" fill="#8C8167" text-anchor="middle" letter-spacing="2">INTERNAL</text>
        <text x="100" y="180" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#B5A988" text-anchor="middle">belief</text>
        <text x="100" y="200" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#B5A988" text-anchor="middle">memory</text>
        <text x="100" y="220" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#B5A988" text-anchor="middle">plan</text>

        <!-- Tool boundary (center) -->
        <rect x="200" y="80" width="140" height="220" fill="rgba(212,168,71,0.10)" stroke="#D4A847" stroke-width="2"/>
        <text x="270" y="60" font-family="-apple-system" font-size="13" fill="#D4A847" text-anchor="middle" font-weight="700" letter-spacing="2">TOOL BOUNDARY</text>
        <text x="270" y="180" font-family="SF Mono,Menlo,monospace" font-size="15" fill="#F5F0E5" text-anchor="middle" font-weight="700">tool call</text>
        <text x="270" y="208" font-family="SF Mono,Menlo,monospace" font-size="12" fill="#B5A988" text-anchor="middle">scope</text>
        <text x="270" y="226" font-family="SF Mono,Menlo,monospace" font-size="12" fill="#B5A988" text-anchor="middle">schema</text>
        <text x="270" y="244" font-family="SF Mono,Menlo,monospace" font-size="12" fill="#B5A988" text-anchor="middle">approval</text>
        <text x="270" y="262" font-family="SF Mono,Menlo,monospace" font-size="12" fill="#B5A988" text-anchor="middle">side effect class</text>

        <!-- World (right) -->
        <ellipse cx="440" cy="190" rx="80" ry="100" fill="rgba(184,57,47,0.06)" stroke="#B8392F" stroke-width="1.5"/>
        <text x="440" y="80" font-family="-apple-system" font-size="13" fill="#B8392F" text-anchor="middle" letter-spacing="2">WORLD</text>
        <text x="440" y="180" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#F5F0E5" text-anchor="middle">DB · email</text>
        <text x="440" y="200" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#F5F0E5" text-anchor="middle">refund · code</text>
        <text x="440" y="220" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#F5F0E5" text-anchor="middle">customers</text>

        <!-- Multiple uncertainty arrows from agent through boundary -->
        <line x1="180" y1="160" x2="220" y2="160" stroke="#D4A847" stroke-width="1.5" stroke-dasharray="3 3"/>
        <line x1="180" y1="190" x2="220" y2="190" stroke="#D4A847" stroke-width="1.5" stroke-dasharray="3 3"/>
        <line x1="180" y1="220" x2="220" y2="220" stroke="#D4A847" stroke-width="1.5" stroke-dasharray="3 3"/>

        <!-- Single arrow from boundary to world -->
        <line x1="320" y1="190" x2="360" y2="190" stroke="#B8392F" stroke-width="3" marker-end="url(#fep_a)"/>

        <!-- Free energy label -->
        <text x="200" y="320" font-family="-apple-system" font-size="14" fill="#D4A847" text-anchor="middle" font-style="italic">free energy escapes here</text>
        <line x1="200" y1="295" x2="270" y2="270" stroke="#D4A847" stroke-width="1" stroke-dasharray="2 2"/>

        <defs>
          <marker id="fep_a" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">
            <polygon points="0 0, 10 5, 0 10" fill="#B8392F"/>
          </marker>
        </defs>
      </svg>
    </div>
  </div>
</div>
"""
    return page(body, "dark", 5)


def slide_06_thesis() -> str:
    body = r"""
<div class="kicker">Act 3 · Our thesis</div>
<h1 class="head med">The evidence layer between<br/>agent dev and production action.</h1>

<div class="body-content" style="margin-top:50px;">
  <!-- Stack diagram showing where Three Moons sits -->
  <div style="display:grid; grid-template-columns: 1.1fr 1fr; gap:60px; align-items:center;">

    <!-- LEFT: layered stack -->
    <div>
      <div style="display:flex; flex-direction:column; gap:12px;">
        <div style="background:#FBF7EE; border:1px solid #D4CCB8; border-radius:8px; padding:18px 24px;">
          <div style="font-size:11px; letter-spacing:0.16em; text-transform:uppercase; color:#8B7E68; font-weight:700;">Above</div>
          <div style="font-size:18px; color:#2A3540; margin-top:4px;">Agent frameworks · OpenAI Agents SDK · Anthropic · Google ADK · LangChain · CrewAI</div>
        </div>

        <div style="background:#1A2530; color:#F5F0E5; border-radius:8px; padding:24px 28px; box-shadow: 0 4px 0 rgba(26,37,48,0.10);">
          <div style="font-size:11px; letter-spacing:0.16em; text-transform:uppercase; color:#D4A847; font-weight:700;">Three Moons Lab — what's missing</div>
          <div style="font-size:24px; font-weight:700; margin-top:6px; line-height:1.3;">
            CI/CD + audit layer for agentic systems
          </div>
          <div style="font-size:15px; color:#B5A988; margin-top:8px;">
            pre-release evidence · trace-based replay · runtime continuous readiness
          </div>
        </div>

        <div style="background:#FBF7EE; border:1px solid #D4CCB8; border-radius:8px; padding:18px 24px;">
          <div style="font-size:11px; letter-spacing:0.16em; text-transform:uppercase; color:#8B7E68; font-weight:700;">Below</div>
          <div style="font-size:18px; color:#2A3540; margin-top:4px;">Tool surfaces · MCP · OpenAPI · function tools · shell · computer use</div>
        </div>
      </div>

      <div style="margin-top:24px; font-size:14px; color:#6B5F4D; font-style:italic;">
        Adjacent (not us): eval frameworks · runtime guardrails · LLM observability · MCP gateways.
      </div>
    </div>

    <!-- RIGHT: thesis statement -->
    <div style="background:#ECE5D5; border-left:4px solid #1A2530; border-radius:4px; padding:36px 40px;">
      <div style="font-size:11px; letter-spacing:0.2em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:18px;">Thesis</div>
      <div style="font-size:24px; line-height:1.45; color:#1A2530;">
        Every production agent will need a <strong>release-readiness record</strong> before
        it gets promoted — and a <strong>trace-replayable evidence trail</strong> after.
      </div>
      <div style="margin-top:24px; font-size:18px; line-height:1.5; color:#2A3540;">
        That record won't live inside the model. It won't live inside the framework.
        It has to live in <strong>independent infrastructure</strong>.
      </div>
    </div>
  </div>
</div>
"""
    return page(body, "light", 6)


def slide_07_wedge() -> str:
    body = r"""
<div class="kicker">Act 3 · Our wedge</div>
<h1 class="head med">Tool-use is the right wedge.</h1>

<div class="body-content" style="margin-top:50px;">
  <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:32px;">

    <!-- 1. Action boundary -->
    <div style="background:#FBF7EE; border:1px solid #D4CCB8; border-radius:10px; padding:32px 36px;">
      <div style="font-size:48px; font-weight:700; color:#B8392F; line-height:1; margin-bottom:14px;">①</div>
      <div style="font-size:24px; font-weight:700; color:#1A2530; margin-bottom:14px; line-height:1.2;">
        Action boundary
      </div>
      <div style="font-size:17px; line-height:1.5; color:#2A3540; margin-bottom:18px;">
        Tool call = the moment language becomes consequence. It's where every interesting
        risk crystallizes: side effect, scope, approval, idempotency, recoverability.
      </div>
      <div style="font-size:13px; color:#8B7E68; font-style:italic; line-height:1.5;">
        The model becoming smarter doesn't change this boundary. It only makes it more active.
      </div>
    </div>

    <!-- 2. Most formalizable -->
    <div style="background:#FBF7EE; border:1px solid #D4CCB8; border-radius:10px; padding:32px 36px;">
      <div style="font-size:48px; font-weight:700; color:#B8392F; line-height:1; margin-bottom:14px;">②</div>
      <div style="font-size:24px; font-weight:700; color:#1A2530; margin-bottom:14px; line-height:1.2;">
        Most formalizable
      </div>
      <div style="font-size:17px; line-height:1.5; color:#2A3540; margin-bottom:18px;">
        Tool surfaces ship with structure: schemas, scopes, MCP annotations, OpenAPI specs,
        SDK function signatures. Static analysis bites — unlike "is the agent's reasoning correct?"
      </div>
      <div style="font-size:13px; color:#8B7E68; font-style:italic; line-height:1.5;">
        Formalize what's crisp · annotate what's contextual · review what's ambiguous.
      </div>
    </div>

    <!-- 3. Highest-leverage risk -->
    <div style="background:#FBF7EE; border:1px solid #D4CCB8; border-radius:10px; padding:32px 36px;">
      <div style="font-size:48px; font-weight:700; color:#B8392F; line-height:1; margin-bottom:14px;">③</div>
      <div style="font-size:24px; font-weight:700; color:#1A2530; margin-bottom:14px; line-height:1.2;">
        Highest-leverage risk
      </div>
      <div style="font-size:17px; line-height:1.5; color:#2A3540; margin-bottom:18px;">
        AppWorld, ToolEmu, AgentDojo, τ-bench, AgentHarm — the academic evidence converges:
        tool-use is where current agents fail, where attacks land, where damage compounds.
      </div>
      <div style="font-size:13px; color:#8B7E68; font-style:italic; line-height:1.5;">
        High-stakes tools (refund, email, deploy, delete) need readiness, not a benchmark score.
      </div>
    </div>
  </div>

  <div style="margin-top:48px; padding:24px 32px; background:#1A2530; color:#F5F0E5; border-radius:10px; font-size:20px; line-height:1.5;">
    <strong style="color:#D4A847;">Wedge logic:</strong>
    The narrowest cut where the static check is meaningful, the risk is real, the buyer is identifiable,
    and the evidence corpus compounds. Tool-use clears all four.
  </div>
</div>
"""
    return page(body, "light", 7)


# ---------------------------------------------------------------------------
# Slides 8 + 9 — Phase 1 diptych: DECLARED (source files) → DETECTED (report)
# ---------------------------------------------------------------------------

# Real source files used in both slides
SAMPLE_PY = (REPO / "samples/support_refund_agent/agents/refund_agent.py").read_text()

# Hand-trimmed YAML excerpt — keep narrative-load-bearing sections only
SAMPLE_YAML = """version: "0.1"

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


PYGMENTS_THEME_CSS = """
.codeblock { font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace; font-size: 12.5px; line-height: 1.55; }
.codeblock .hll { background-color: #F0E9D6 }
.codeblock .c, .codeblock .ch, .codeblock .cm, .codeblock .cpf, .codeblock .c1, .codeblock .cs { color: #8B7E68; font-style: italic }
.codeblock .k, .codeblock .kc, .codeblock .kd, .codeblock .kn, .codeblock .kp, .codeblock .kr, .codeblock .kt { color: #1A2530; font-weight: 700 }
.codeblock .nt { color: #2A3540; font-weight: 600 }
.codeblock .nb, .codeblock .nc, .codeblock .nf, .codeblock .nn { color: #1A2530; font-weight: 600 }
.codeblock .n, .codeblock .nv { color: #2A3540 }
.codeblock .l, .codeblock .ld, .codeblock .s, .codeblock .s1, .codeblock .s2, .codeblock .se, .codeblock .sx, .codeblock .sb, .codeblock .sc, .codeblock .sd, .codeblock .sh, .codeblock .si, .codeblock .sr, .codeblock .ss { color: #6B7B4F }
.codeblock .m, .codeblock .mf, .codeblock .mh, .codeblock .mi, .codeblock .mo, .codeblock .il { color: #C76A2C }
.codeblock .o, .codeblock .ow { color: #6B5F4D }
.codeblock .p { color: #6B5F4D }
.codeblock .err { color: #B8392F }
.codeblock .gh { color: #1A2530; font-weight: 700 }
.codeblock .linenos { color: #B8AB91; padding-right: 12px; user-select: none; border-right: 1px solid #E5DCC6; margin-right: 12px; }
.codeblock pre { margin: 0; padding: 0; background: transparent; }
"""


def _render_code(src: str, lexer) -> str:
    formatter = HtmlFormatter(cssclass="codeblock", linenos="inline",
                              nobackground=True, wrapcode=True)
    return highlight(src, lexer, formatter)


def slide_08_declared() -> str:
    py_html = _render_code(SAMPLE_PY, PythonLexer())
    yaml_html = _render_code(SAMPLE_YAML, YamlLexer())
    body = f"""
<style>
{PYGMENTS_THEME_CSS}
.editor {{
  background: #FAF6EB;
  border: 1px solid #D4CCB8;
  border-radius: 10px;
  display: flex; flex-direction: column;
  overflow: hidden;
  box-shadow: 0 2px 0 rgba(26,37,48,0.04);
  height: 100%;
}}
.editor-bar {{
  background: #E9E0CB;
  border-bottom: 1px solid #D4CCB8;
  display: flex; align-items: stretch; padding-left: 14px; height: 36px;
}}
.editor-bar .dots {{
  display: flex; align-items: center; gap: 7px; padding-right: 14px;
  border-right: 1px solid #D4CCB8; margin-right: 4px;
}}
.editor-bar .dot {{ width: 11px; height: 11px; border-radius: 50%; background: #C7BCA1; }}
.tab {{
  display: flex; align-items: center; gap: 6px; padding: 0 16px;
  font-size: 12px; color: #8B7E68;
  border-right: 1px solid #D4CCB8;
  font-family: "SF Mono", Menlo, monospace;
}}
.tab.active {{ background: #FAF6EB; color: #1A2530; font-weight: 600; }}
.tab .ic {{ font-size: 10px; color: #6B7B4F; }}
.editor-bar .path {{
  margin-left: auto; padding: 0 18px; display: flex; align-items: center;
  font-size: 11px; color: #8B7E68;
  font-family: "SF Mono", Menlo, monospace;
}}
.editor-body {{ flex: 1; padding: 18px 18px 12px 18px; overflow: hidden; }}
.section-label {{
  font-family: "SF Mono", Menlo, monospace;
  font-size: 10px; color: #8B7E68;
  letter-spacing: 0.06em;
  margin-bottom: 8px;
  display: flex; justify-content: space-between;
}}
.section-label .lang {{
  background: #ECE5D5; padding: 1px 6px; border-radius: 3px; color: #6B5F4D;
  letter-spacing: 0.1em; text-transform: uppercase; font-weight: 700; font-size: 9px;
}}
</style>
<div class="kicker">Phase 1 · Static Release-Readiness Scanner</div>
<h1 class="head med">What the team <span class="dim" style="color:#8B7E68;">declared</span>.</h1>
<p class="lede" style="margin-top:14px; max-width:1500px;">
  An OpenAI Agents SDK refund agent and its release contract.
  ~50 lines of human-authored intent, across two files, that determines whether
  a refund of $5,000 can fire without human approval.
</p>

<div class="body-content" style="margin-top:24px;">
  <div style="display:grid; grid-template-columns: 1fr 1.2fr; gap:32px; height:580px;">

    <!-- LEFT: refund_agent.py -->
    <div class="editor">
      <div class="editor-bar">
        <div class="dots"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
        <div class="tab active"><span class="ic">py</span>refund_agent.py</div>
        <div class="path">samples/support_refund_agent/agents/</div>
      </div>
      <div class="editor-body">
        <div class="section-label">
          <span>refund_agent.py — agent + tool definitions</span>
          <span class="lang">PYTHON</span>
        </div>
        {py_html}
      </div>
    </div>

    <!-- RIGHT: shipgate.yaml -->
    <div class="editor">
      <div class="editor-bar">
        <div class="dots"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
        <div class="tab active"><span class="ic">●</span>shipgate.yaml</div>
        <div class="tab"><span class="ic">[ ]</span>support-tools.openapi.yaml</div>
        <div class="tab"><span class="ic">{{ }}</span>mcp-tools.json</div>
        <div class="path">samples/support_refund_agent/</div>
      </div>
      <div class="editor-body">
        <div class="section-label">
          <span>shipgate.yaml — declared release contract</span>
          <span class="lang">YAML</span>
        </div>
        {yaml_html}
      </div>
    </div>
  </div>
</div>
"""
    return page(body, "light", 8)


def slide_09_detected() -> str:
    body = r"""
<style>
.report-card {
  background: #FBF7EE; border: 1px solid #D4CCB8; border-radius: 10px;
  padding: 30px 36px; display: flex; flex-direction: column; gap: 16px;
  height: 100%; overflow: hidden;
}
.panel-head { display: flex; justify-content: space-between; align-items: baseline;
  border-bottom: 1px solid #E5DCC6; padding-bottom: 14px; }
.panel-head .col-label { font-size: 11px; letter-spacing: 0.22em; text-transform: uppercase;
  color: #8B7E68; font-weight: 700; }
.panel-head h2 { font-size: 26px; font-weight: 700; margin: 4px 0 0 0; letter-spacing: -0.005em; }
.panel-head .meta { font-size: 13px; color: #6B5F4D; font-family: "SF Mono", Menlo, monospace;
  text-align: right; line-height: 1.6; }

.verdict { display: flex; align-items: center; gap: 16px;
  background: #F4D9D6; border-left: 4px solid #B8392F;
  padding: 16px 20px; border-radius: 4px; }
.verdict .icon { width: 32px; height: 32px; border-radius: 50%;
  background: #B8392F; color: #FBF7EE;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 18px; font-family: "SF Mono", Menlo, monospace; }
.verdict .text { font-size: 19px; font-weight: 700; color: #1A2530; }
.verdict .text small { display: block; font-size: 13px; color: #6B5F4D;
  font-weight: 500; margin-top: 3px; }

.counts { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
.count { background: #ECE5D5; border: 1px solid #E5DCC6; border-radius: 6px; padding: 14px 16px; }
.count .n { font-size: 32px; font-weight: 700; color: #1A2530; line-height: 1; }
.count .lbl { font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase;
  color: #6B5F4D; margin-top: 6px; font-weight: 600; }
.count.crit .n { color: #B8392F; }
.count.high .n { color: #C76A2C; }
.count.med  .n { color: #B89530; }

.findings-head { display: flex; justify-content: space-between; align-items: baseline; margin-top: 4px; }
.findings-head h3 { font-size: 13px; letter-spacing: 0.18em; text-transform: uppercase;
  color: #8B7E68; margin: 0; font-weight: 700; }
.findings-head .more { font-size: 12px; color: #6B5F4D; font-family: "SF Mono", Menlo, monospace; }

.finding { display: grid; grid-template-columns: 80px 1fr;
  gap: 16px; padding: 11px 0; border-bottom: 1px solid #E5DCC6; align-items: start; }
.finding:last-child { border-bottom: none; }
.sev { font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 700;
  padding: 4px 7px; border-radius: 3px; text-align: center; font-family: "SF Mono", Menlo, monospace; }
.sev.crit { background: #F4D9D6; color: #B8392F; }
.sev.high { background: #F4E2D0; color: #C76A2C; }
.f-body .id { font-family: "SF Mono", Menlo, monospace; font-size: 12.5px; color: #6B5F4D; margin-bottom: 3px; }
.f-body .id .tool { color: #1A2530; background: #ECE5D5;
  padding: 1px 6px; border-radius: 3px; margin-left: 4px; }
.f-body .title { font-size: 14.5px; color: #1A2530; font-weight: 500; line-height: 1.42; }
.f-body .title code { background: #ECE5D5; padding: 1px 4px; border-radius: 2px;
  font-family: "SF Mono", Menlo, monospace; font-size: 13px; }

.foot-summary { display: flex; justify-content: space-between; font-size: 12px;
  color: #6B5F4D; border-top: 1px solid #E5DCC6; padding-top: 14px;
  font-family: "SF Mono", Menlo, monospace; margin-top: 4px; }
.foot-summary .pill { padding: 2px 8px; background: #ECE5D5; border-radius: 3px; margin-right: 6px; }
</style>

<div class="kicker">Phase 1 · Static Release-Readiness Scanner</div>
<h1 class="head med">What shipgate <span style="color:#B8392F;">detected</span>.</h1>
<p class="lede" style="margin-top:14px; max-width:1500px;">
  Static analysis on the declared tool surface from the previous slide.
  The manifest's <code style="background:#ECE5D5; padding:2px 6px; border-radius:3px;
   font-family:'SF Mono',Menlo,monospace; font-size:18px;">prohibited_actions</code> list says
  <em style="color:#1A2530;">"issue refund without approval"</em> — but
  <code style="background:#ECE5D5; padding:2px 6px; border-radius:3px;
   font-family:'SF Mono',Menlo,monospace; font-size:18px;">stripe.create_refund</code>
  has no approval policy declared.
</p>

<div class="body-content" style="margin-top:18px; justify-content:flex-start;">
  <div class="report-card">
    <div class="panel-head">
      <div>
        <div class="col-label">Detected · agents-shipgate scan</div>
        <h2>support-refund-agent · refund-assistant</h2>
      </div>
      <div class="meta">
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
"""
    return page(body, "light", 9)


def slide_10_workflow() -> str:
    body = r"""
<div class="kicker">Act 4 · The product path</div>
<h1 class="head med">How the release contract gets written.</h1>
<p class="lede" style="margin-top:14px; max-width:1600px;">
  <code style="background:#ECE5D5; padding:2px 7px; border-radius:3px;
   font-family:'SF Mono',Menlo,monospace; font-size:18px;">shipgate.yaml</code>
  is a living contract — half scaffolded by the scanner, half human-authored,
  versioned with the agent code, reviewed in PR, enforced in CI.
</p>

<div class="body-content" style="margin-top:24px; justify-content:flex-start;">

  <!-- 4-step horizontal flow -->
  <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:18px;">
"""
    cards = [
        ("01", "SCAFFOLD", "AUTO",
         "$ agents-shipgate init",
         "Scanner walks the workspace, detects OpenAPI / MCP / SDK files, prefills "
         "<code>tool_sources</code> and references their schemas.",
         "Output: starter shipgate.yaml with <code>CHANGE_ME</code> placeholders for intent."),
        ("02", "AUTHOR", "HUMAN",
         "~ shipgate.yaml",
         "Fill in <code>declared_purpose</code>, <code>prohibited_actions</code>, approval policies, "
         "permission scopes, risk owners. Scanner can't infer intent.",
         "Output: completed release contract — your team's policy as code."),
        ("03", "SCAN", "AUTO",
         "$ agents-shipgate scan",
         "Static analysis matches declared intent against actual configured enforcement. "
         "Surfaces gaps as findings — like the ones on the previous slide.",
         "Output: BLOCKED / WARN / PASS report (markdown · JSON · SARIF)."),
        ("04", "ITERATE", "HUMAN + CI",
         "~ PR review · CI gate",
         "Fix policies, narrow scopes, accept residual risk with reason. "
         "Each commit re-scans. Manifest evolves with the agent.",
         "Output: living contract, version-controlled. New findings block release."),
    ]
    for num, label, who, cmd, desc, out in cards:
        who_color = "#6B7B4F" if who == "AUTO" else ("#C76A2C" if who == "HUMAN" else "#1A2530")
        who_bg = "#E5EBD8" if who == "AUTO" else ("#F4E2D0" if who == "HUMAN" else "#ECE5D5")
        body += f"""
    <div style="background:#FBF7EE; border:1px solid #D4CCB8; border-radius:10px;
                padding:24px 26px; display:flex; flex-direction:column; gap:14px;">
      <div style="display:flex; align-items:baseline; justify-content:space-between;">
        <div style="font-size:36px; font-weight:700; color:#B8392F; line-height:1; letter-spacing:-0.02em;">{num}</div>
        <div style="font-size:10px; letter-spacing:0.18em; font-weight:700;
                    background:{who_bg}; color:{who_color}; padding:4px 8px; border-radius:3px;">{who}</div>
      </div>
      <div style="font-size:18px; font-weight:700; color:#1A2530; letter-spacing:-0.005em;">{label}</div>
      <div style="font-family:'SF Mono',Menlo,monospace; font-size:13px; color:#2A3540;
                  background:#ECE5D5; padding:8px 12px; border-radius:3px;
                  border-left:2px solid #1A2530; word-break:break-word;">{cmd}</div>
      <div style="font-size:14px; color:#2A3540; line-height:1.5; flex:1;">{desc}</div>
      <div style="font-size:12px; color:#6B5F4D; font-style:italic; line-height:1.45;
                  border-top:1px solid #E5DCC6; padding-top:10px;">{out}</div>
    </div>"""
    body += r"""
  </div>

  <!-- Loop back hint -->
  <div style="margin-top:18px; display:flex; align-items:center; gap:14px;
              font-size:12px; color:#6B5F4D; font-family:'SF Mono',Menlo,monospace;">
    <div style="flex:1; height:1px; background:#D4CCB8;"></div>
    <div>↻ findings → fix → re-scan → commit · the loop runs every PR</div>
    <div style="flex:1; height:1px; background:#D4CCB8;"></div>
  </div>

  <!-- Bottom: three-layer authorship breakdown -->
  <div style="margin-top:24px; display:grid; grid-template-columns: repeat(3, 1fr); gap:16px;">
    <div style="background:#E5EBD8; border-left:3px solid #6B7B4F; padding:14px 18px; border-radius:3px;">
      <div style="font-size:10px; letter-spacing:0.18em; font-weight:700; color:#6B7B4F; margin-bottom:6px;">SCANNER WRITES</div>
      <div style="font-size:13.5px; color:#1A2530; line-height:1.55;">
        <code style="background:rgba(255,255,255,0.5); padding:1px 5px; border-radius:2px; font-family:'SF Mono',Menlo,monospace;">tool_sources</code>
        · schemas, scopes, MCP annotations <em style="color:#6B5F4D;">— referenced, not copied</em>
      </div>
    </div>
    <div style="background:#F4E2D0; border-left:3px solid #C76A2C; padding:14px 18px; border-radius:3px;">
      <div style="font-size:10px; letter-spacing:0.18em; font-weight:700; color:#C76A2C; margin-bottom:6px;">HUMAN WRITES</div>
      <div style="font-size:13.5px; color:#1A2530; line-height:1.55;">
        <code style="background:rgba(255,255,255,0.5); padding:1px 5px; border-radius:2px; font-family:'SF Mono',Menlo,monospace;">declared_purpose</code> ·
        <code style="background:rgba(255,255,255,0.5); padding:1px 5px; border-radius:2px; font-family:'SF Mono',Menlo,monospace;">prohibited_actions</code>
        · policies · risk owners <em style="color:#6B5F4D;">— intent only humans can author</em>
      </div>
    </div>
    <div style="background:#ECE5D5; border-left:3px solid #1A2530; padding:14px 18px; border-radius:3px;">
      <div style="font-size:10px; letter-spacing:0.18em; font-weight:700; color:#1A2530; margin-bottom:6px;">HYBRID</div>
      <div style="font-size:13.5px; color:#1A2530; line-height:1.55;">
        <code style="background:rgba(255,255,255,0.5); padding:1px 5px; border-radius:2px; font-family:'SF Mono',Menlo,monospace;">permissions.scopes</code>
        <em style="color:#6B5F4D;">— scanner suggests least-privilege from tool specs; human ratifies</em>
      </div>
    </div>
  </div>
</div>
"""
    return page(body, "light", 10)


def slide_11_phase23() -> str:
    body = r"""
<div class="kicker">Act 4 · The product path</div>
<h1 class="head med">Beyond static — sandbox &amp; trace.</h1>

<div class="body-content" style="margin-top:50px;">

  <div style="display:grid; grid-template-columns: 1fr 1fr; gap:36px;">

    <!-- Phase 2 -->
    <div style="background:#FBF7EE; border:1px solid #D4CCB8; border-radius:10px; padding:32px 36px;">
      <div style="display:flex; align-items:baseline; justify-content:space-between; margin-bottom:18px;">
        <div style="font-size:11px; letter-spacing:0.2em; text-transform:uppercase; color:#8B7E68; font-weight:700;">Phase 2</div>
        <div style="font-size:13px; color:#6B5F4D; font-family:'SF Mono',Menlo,monospace;">~6–12 months out</div>
      </div>
      <div style="font-size:32px; font-weight:700; color:#1A2530; margin-bottom:14px; line-height:1.15;">
        Sandbox &amp; simulation
      </div>
      <div style="font-size:18px; line-height:1.5; color:#2A3540; margin-bottom:22px;">
        Turn the unknowns surfaced in Phase 1 into experimental evidence — without exposing
        production state.
      </div>
      <ul style="font-size:17px; line-height:1.7; color:#2A3540; padding-left:22px; margin:0;">
        <li>Mocked tool execution &amp; failure injection</li>
        <li>Prompt-injection harness on read-tools (web, email, docs)</li>
        <li>State-diff assertions for collateral damage</li>
        <li>Synthetic adversarial scenarios (ToolEmu / AppWorld lineage)</li>
      </ul>
      <div class="hairline" style="margin-top:24px; font-size:14px; color:#8B7E68; font-style:italic;">
        Output: pre-promotion stress test report. CI-attachable. Fails-loud on regressions.
      </div>
    </div>

    <!-- Phase 3 -->
    <div style="background:#FBF7EE; border:1px solid #D4CCB8; border-radius:10px; padding:32px 36px;">
      <div style="display:flex; align-items:baseline; justify-content:space-between; margin-bottom:18px;">
        <div style="font-size:11px; letter-spacing:0.2em; text-transform:uppercase; color:#8B7E68; font-weight:700;">Phase 3</div>
        <div style="font-size:13px; color:#6B5F4D; font-family:'SF Mono',Menlo,monospace;">~12–24 months out</div>
      </div>
      <div style="font-size:32px; font-weight:700; color:#1A2530; margin-bottom:14px; line-height:1.15;">
        Trace, replay, runtime
      </div>
      <div style="font-size:18px; line-height:1.5; color:#2A3540; margin-bottom:22px;">
        Turn one-time pre-release reports into a continuous readiness state — pulled from
        the agent's actual production behavior.
      </div>
      <ul style="font-size:17px; line-height:1.7; color:#2A3540; padding-left:22px; margin:0;">
        <li>Trace ingestion: OpenAI Agents SDK, MCP events, custom hooks</li>
        <li>Replay bundles for incident forensics</li>
        <li>Regression detection across prompt / model / tool changes</li>
        <li>Runtime anomaly &amp; blast-radius monitors</li>
      </ul>
      <div class="hairline" style="margin-top:24px; font-size:14px; color:#8B7E68; font-style:italic;">
        Output: living readiness state. Audit-grade. Connected to incident review.
      </div>
    </div>
  </div>

  <div style="margin-top:32px; font-size:18px; color:#2A3540; text-align:center; line-height:1.5;">
    Phase 1 ships now. Phase 2 and 3 are deliberate <strong>land-and-expand</strong>, not a roadmap to be promised on Slide 11.
  </div>
</div>
"""
    return page(body, "light", 11)


def slide_12_compounding() -> str:
    body = r"""
<div class="kicker">Act 4 · Why this is a category, not a feature</div>
<h1 class="head med">Three phases. One compounding<br/>evidence corpus.</h1>

<div class="body-content" style="margin-top:50px;">

  <!-- Stacked diagram showing corpus accumulation -->
  <div style="display:grid; grid-template-columns: 1.4fr 1fr; gap:60px; align-items:center;">

    <div>
      <svg viewBox="0 0 700 420" style="width:100%; height:auto;">
        <!-- Phase layers -->
        <defs>
          <linearGradient id="g1" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#FBF7EE"/>
            <stop offset="100%" stop-color="#ECE5D5"/>
          </linearGradient>
          <linearGradient id="g2" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#ECE5D5"/>
            <stop offset="100%" stop-color="#E0D7C0"/>
          </linearGradient>
          <linearGradient id="g3" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#E0D7C0"/>
            <stop offset="100%" stop-color="#C7BCA1"/>
          </linearGradient>
        </defs>

        <!-- Phase 1 layer (bottom) -->
        <rect x="40" y="300" width="620" height="80" fill="url(#g1)" stroke="#1A2530"/>
        <text x="60" y="328" font-family="-apple-system" font-size="13" fill="#8B7E68" font-weight="700" letter-spacing="2">PHASE 1 · STATIC</text>
        <text x="60" y="352" font-family="-apple-system" font-size="20" fill="#1A2530" font-weight="700">Tool surface metadata</text>
        <text x="60" y="372" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#2A3540">manifests · schemas · scopes · effect classes · approval flags</text>

        <!-- Phase 2 layer (middle) -->
        <rect x="40" y="180" width="620" height="100" fill="url(#g2)" stroke="#1A2530"/>
        <text x="60" y="208" font-family="-apple-system" font-size="13" fill="#8B7E68" font-weight="700" letter-spacing="2">PHASE 2 · SANDBOX</text>
        <text x="60" y="232" font-family="-apple-system" font-size="20" fill="#1A2530" font-weight="700">Failure-mode taxonomy</text>
        <text x="60" y="252" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#2A3540">attack patterns · injection results · state-diff baselines · scenario library</text>

        <!-- Phase 3 layer (top) -->
        <rect x="40" y="40" width="620" height="120" fill="url(#g3)" stroke="#1A2530"/>
        <text x="60" y="68" font-family="-apple-system" font-size="13" fill="#8B7E68" font-weight="700" letter-spacing="2">PHASE 3 · TRACE</text>
        <text x="60" y="92" font-family="-apple-system" font-size="20" fill="#1A2530" font-weight="700">Production trace data</text>
        <text x="60" y="112" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#2A3540">tool-call events · approval logs · replay bundles · regression deltas</text>
        <text x="60" y="132" font-family="SF Mono,Menlo,monospace" font-size="13" fill="#2A3540">incident forensics · cross-org failure patterns</text>

        <!-- Right edge: compounding arrow -->
        <line x1="690" y1="380" x2="690" y2="40" stroke="#B8392F" stroke-width="3" marker-end="url(#c_arrow)"/>
        <text x="685" y="208" font-family="-apple-system" font-size="14" fill="#B8392F" font-weight="700" text-anchor="end" transform="rotate(-90 685 208)">corpus compounds</text>

        <defs>
          <marker id="c_arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">
            <polygon points="0 0, 10 5, 0 10" fill="#B8392F"/>
          </marker>
        </defs>
      </svg>
    </div>

    <div>
      <div style="font-size:22px; line-height:1.5; color:#2A3540; margin-bottom:24px;">
        Three phases are not three products. They are the same evidence corpus
        unfolding across three timescales.
      </div>
      <div style="font-size:18px; line-height:1.55; color:#2A3540; margin-bottom:18px;">
        Each user adds metadata, failure cases, and traces. The
        <strong>failure taxonomy</strong>, <strong>policy library</strong>, and
        <strong>trace schema</strong> compound.
      </div>
      <div style="background:#1A2530; color:#F5F0E5; padding:20px 24px; border-radius:8px; font-size:18px; line-height:1.45;">
        <strong style="color:#D4A847;">This is the anti-feature defense.</strong>
        A single GitHub Action lint cannot compound. A scanner backed by a growing
        cross-organizational evidence corpus can.
      </div>
    </div>
  </div>
</div>
"""
    return page(body, "light", 12)


def slide_13_validating() -> str:
    body = r"""
<div class="kicker">Act 5 · Where I am</div>
<h1 class="head med">What I'm validating now.</h1>

<div class="body-content" style="margin-top:40px;">
  <div style="font-size:20px; color:#2A3540; max-width:1500px; margin-bottom:36px;">
    Three open hypotheses. Each one converts (or kills) the company. The next 6–8 weeks are
    a validation loop, not a product sprint.
  </div>

  <div style="display:flex; flex-direction:column; gap:18px;">

    <!-- H1 -->
    <div style="display:grid; grid-template-columns: 80px 1fr 1.1fr; gap:24px; padding:22px 28px; background:#FBF7EE; border:1px solid #D4CCB8; border-radius:8px;">
      <div style="font-size:36px; font-weight:700; color:#B8392F; line-height:1;">H1</div>
      <div>
        <div style="font-size:11px; letter-spacing:0.18em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:6px;">Hypothesis</div>
        <div style="font-size:18px; line-height:1.45; color:#1A2530;">
          Production agents have a <strong>recurring pre-release readiness workflow</strong>
          today — even if no one has named it.
        </div>
      </div>
      <div>
        <div style="font-size:11px; letter-spacing:0.18em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:6px;">Proof I'm seeking</div>
        <div style="font-size:17px; line-height:1.45; color:#2A3540;">
          3–5 design partners running shipgate in real CI · 10+ release-blocking findings
          on real tool surfaces · repeatable trigger event.
        </div>
      </div>
    </div>

    <!-- H2 -->
    <div style="display:grid; grid-template-columns: 80px 1fr 1.1fr; gap:24px; padding:22px 28px; background:#FBF7EE; border:1px solid #D4CCB8; border-radius:8px;">
      <div style="font-size:36px; font-weight:700; color:#B8392F; line-height:1;">H2</div>
      <div>
        <div style="font-size:11px; letter-spacing:0.18em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:6px;">Hypothesis</div>
        <div style="font-size:18px; line-height:1.45; color:#1A2530;">
          The first owner is <strong>platform / AI infra engineering</strong>, not
          security/GRC. Security buys later, after evidence accumulates.
        </div>
      </div>
      <div>
        <div style="font-size:11px; letter-spacing:0.18em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:6px;">Proof I'm seeking</div>
        <div style="font-size:17px; line-height:1.45; color:#2A3540;">
          Design-partner data on who triggers / triages findings · which team owns
          the CI gate · whether security review piggybacks on shipgate output.
        </div>
      </div>
    </div>

    <!-- H3 -->
    <div style="display:grid; grid-template-columns: 80px 1fr 1.1fr; gap:24px; padding:22px 28px; background:#FBF7EE; border:1px solid #D4CCB8; border-radius:8px;">
      <div style="font-size:36px; font-weight:700; color:#B8392F; line-height:1;">H3</div>
      <div>
        <div style="font-size:11px; letter-spacing:0.18em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:6px;">Hypothesis</div>
        <div style="font-size:18px; line-height:1.45; color:#1A2530;">
          <strong>Static + manifest checks</strong> are sufficient through Risk Tier 3
          (reversible internal write). Tier 4+ requires sandbox + trace.
        </div>
      </div>
      <div>
        <div style="font-size:11px; letter-spacing:0.18em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:6px;">Proof I'm seeking</div>
        <div style="font-size:17px; line-height:1.45; color:#2A3540;">
          Real findings on real tool surfaces, post-fix · false-positive rate on
          static checks · which tiers actually demand simulation evidence.
        </div>
      </div>
    </div>
  </div>

  <div style="margin-top:30px; font-size:16px; color:#6B5F4D; font-style:italic; text-align:center;">
    What I'm <em>not</em> claiming: PMF, runtime safety certification, or that this is foundation-model-lab-proof. Those are unknowns to be earned.
  </div>
</div>
"""
    return page(body, "light", 13)


def slide_14_north_star() -> str:
    body = r"""
<div class="kicker">Act 5 · 10-year north star</div>
<h1 class="head med">Today: pre-release exam.<br/>Long term: a medical record for every agent.</h1>

<div class="body-content" style="margin-top:60px;">

  <!-- Timeline -->
  <svg viewBox="0 0 1700 360" style="width:100%; height:auto;">
    <!-- horizontal axis line -->
    <line x1="80" y1="180" x2="1620" y2="180" stroke="#1A2530" stroke-width="2"/>

    <!-- Stage 1 -->
    <circle cx="220" cy="180" r="14" fill="#1A2530"/>
    <text x="220" y="220" font-family="-apple-system" font-size="13" fill="#8B7E68" font-weight="700" text-anchor="middle" letter-spacing="2">TODAY</text>
    <text x="220" y="248" font-family="-apple-system" font-size="22" fill="#1A2530" font-weight="700" text-anchor="middle">Pre-release exam</text>
    <text x="220" y="278" font-family="-apple-system" font-size="14" fill="#2A3540" text-anchor="middle">static scanner ·</text>
    <text x="220" y="296" font-family="-apple-system" font-size="14" fill="#2A3540" text-anchor="middle">CI gate · SARIF report</text>
    <text x="220" y="138" font-family="-apple-system" font-size="48" fill="#1A2530" font-weight="200" text-anchor="middle">⊕</text>

    <!-- Stage 2 -->
    <circle cx="600" cy="180" r="14" fill="#1A2530"/>
    <text x="600" y="220" font-family="-apple-system" font-size="13" fill="#8B7E68" font-weight="700" text-anchor="middle" letter-spacing="2">YEAR 2</text>
    <text x="600" y="248" font-family="-apple-system" font-size="22" fill="#1A2530" font-weight="700" text-anchor="middle">Stress tests</text>
    <text x="600" y="278" font-family="-apple-system" font-size="14" fill="#2A3540" text-anchor="middle">sandbox · failure injection ·</text>
    <text x="600" y="296" font-family="-apple-system" font-size="14" fill="#2A3540" text-anchor="middle">prompt-injection harness</text>
    <text x="600" y="138" font-family="-apple-system" font-size="48" fill="#1A2530" font-weight="200" text-anchor="middle">⊗</text>

    <!-- Stage 3 -->
    <circle cx="980" cy="180" r="14" fill="#1A2530"/>
    <text x="980" y="220" font-family="-apple-system" font-size="13" fill="#8B7E68" font-weight="700" text-anchor="middle" letter-spacing="2">YEAR 3–4</text>
    <text x="980" y="248" font-family="-apple-system" font-size="22" fill="#1A2530" font-weight="700" text-anchor="middle">Vital signs</text>
    <text x="980" y="278" font-family="-apple-system" font-size="14" fill="#2A3540" text-anchor="middle">trace ingestion · replay ·</text>
    <text x="980" y="296" font-family="-apple-system" font-size="14" fill="#2A3540" text-anchor="middle">runtime anomaly monitors</text>
    <text x="980" y="138" font-family="-apple-system" font-size="48" fill="#1A2530" font-weight="200" text-anchor="middle">♥</text>

    <!-- Stage 4 — north star -->
    <circle cx="1400" cy="180" r="22" fill="#B8392F"/>
    <circle cx="1400" cy="180" r="36" fill="none" stroke="#B8392F" stroke-width="1.5" stroke-dasharray="4 3"/>
    <text x="1400" y="226" font-family="-apple-system" font-size="13" fill="#B8392F" font-weight="700" text-anchor="middle" letter-spacing="2">10-YEAR NORTH STAR</text>
    <text x="1400" y="256" font-family="-apple-system" font-size="24" fill="#1A2530" font-weight="700" text-anchor="middle">Medical record</text>
    <text x="1400" y="282" font-family="-apple-system" font-size="14" fill="#2A3540" text-anchor="middle">across the lifetime of every agent —</text>
    <text x="1400" y="300" font-family="-apple-system" font-size="14" fill="#2A3540" text-anchor="middle">development, incidents, retirement</text>
    <text x="1400" y="138" font-family="-apple-system" font-size="48" fill="#B8392F" font-weight="200" text-anchor="middle">★</text>
  </svg>

  <div style="margin-top:36px; padding:24px 32px; background:#ECE5D5; border-left:4px solid #1A2530; border-radius:4px; font-size:20px; line-height:1.5; color:#1A2530; max-width:1700px;">
    Today we build the first instrument. The compounding ambition is to make every production agent's release,
    incident, and behavior traceable and accountable across its life — the way we expect of any other deployable system.
  </div>
</div>
"""
    return page(body, "light", 14)


def slide_15_looking_for() -> str:
    body = r"""
<div class="kicker">Closing</div>
<h1 class="head med">What I'm looking for.</h1>

<div class="body-content" style="margin-top:50px;">
  <div style="font-size:22px; color:#2A3540; max-width:1500px; margin-bottom:42px; line-height:1.5;">
    This deck is not a fundraise. It's an <strong>invitation to think alongside us</strong>.
    Three concrete asks, in order of immediate value:
  </div>

  <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:32px;">

    <!-- Sparring partners -->
    <div style="background:#FBF7EE; border:1px solid #D4CCB8; border-radius:10px; padding:36px 40px; display:flex; flex-direction:column;">
      <div style="font-size:13px; letter-spacing:0.24em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:14px;">Ask 01</div>
      <div style="font-size:28px; font-weight:700; color:#1A2530; margin-bottom:18px; line-height:1.15;">
        Sparring partners
      </div>
      <div style="font-size:17px; line-height:1.55; color:#2A3540; flex:1;">
        Founders, operators, researchers willing to push back on the thesis. Especially:
        people who think this is a feature, not a category. I want to be wrong fast.
      </div>
      <div style="margin-top:24px; font-size:13px; color:#8B7E68; font-style:italic;">
        Best for: Prateek, AI-infra peers, security/GRC operators, MCP &amp; framework authors.
      </div>
    </div>

    <!-- Design partners -->
    <div style="background:#1A2530; color:#F5F0E5; border-radius:10px; padding:36px 40px; display:flex; flex-direction:column;">
      <div style="font-size:13px; letter-spacing:0.24em; text-transform:uppercase; color:#D4A847; font-weight:700; margin-bottom:14px;">Ask 02 — most valuable now</div>
      <div style="font-size:28px; font-weight:700; margin-bottom:18px; line-height:1.15;">
        Design partners
      </div>
      <div style="font-size:17px; line-height:1.55; color:#B5A988; flex:1;">
        Teams shipping production agents with non-trivial tool surfaces — refunds, customer
        comms, code execution, internal data access. I want to scan, find real risk,
        watch what gets fixed, learn what their CI actually demands.
      </div>
      <div style="margin-top:24px; font-size:13px; color:#8C8167; font-style:italic;">
        Looking for: 3–5 partners over the next 6–8 weeks.
      </div>
    </div>

    <!-- Future capital -->
    <div style="background:#FBF7EE; border:1px solid #D4CCB8; border-radius:10px; padding:36px 40px; display:flex; flex-direction:column;">
      <div style="font-size:13px; letter-spacing:0.24em; text-transform:uppercase; color:#8B7E68; font-weight:700; margin-bottom:14px;">Ask 03 — later</div>
      <div style="font-size:28px; font-weight:700; color:#1A2530; margin-bottom:18px; line-height:1.15;">
        Capital optionality
      </div>
      <div style="font-size:17px; line-height:1.55; color:#2A3540; flex:1;">
        Not raising today. When the design-partner loop converts the thesis to traction,
        I'd like the conversation to continue with people who already understood the worldview.
      </div>
      <div style="margin-top:24px; font-size:13px; color:#8B7E68; font-style:italic;">
        Trigger: 3+ design partners using shipgate findings to gate releases.
      </div>
    </div>
  </div>

  <div style="margin-top:50px; padding:28px 36px; background:#ECE5D5; border-left:4px solid #B8392F; border-radius:4px; max-width:1700px;">
    <div style="font-size:24px; line-height:1.45; color:#1A2530; font-style:italic;">
      Three Moons Lab is not building infrastructure to make agents smarter.
      We're building infrastructure to make their entry into the world <strong style="font-style:normal; color:#B8392F;">accountable</strong>.
    </div>
  </div>
</div>
"""
    return page(body, "light", 15)


# ---------------------------------------------------------------------------
# Render pipeline
# ---------------------------------------------------------------------------

SLIDE_BUILDERS = [
    (1,  slide_01_cover),
    (2,  slide_02_inflection),
    (3,  slide_03_new_release_problem),
    (4,  slide_04_godel),
    (5,  slide_05_fep),
    (6,  slide_06_thesis),
    (7,  slide_07_wedge),
    (8,  slide_08_declared),
    (9,  slide_09_detected),
    (10, slide_10_workflow),
    (11, slide_11_phase23),
    (12, slide_12_compounding),
    (13, slide_13_validating),
    (14, slide_14_north_star),
    (15, slide_15_looking_for),
]


async def render_html_to_png(page, html: str, out: Path):
    html_path = out.with_suffix(".html")
    html_path.write_text(html)
    await page.goto(f"file://{html_path}")
    await page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=2)
        pg = await context.new_page()
        for n, builder in SLIDE_BUILDERS:
            html = builder()
            out = BUILD_DIR / f"slide-{n:02d}.png"
            await render_html_to_png(pg, html, out)
            print(f"  rendered slide-{n:02d}")
        await browser.close()

    # All slides rendered directly — nothing else to copy.


if __name__ == "__main__":
    asyncio.run(main())
