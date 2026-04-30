"""V3 — Animated terminal GIF showing `agents-shipgate scan` running and arriving
at BLOCKED. Frame-by-frame in PIL.

Output is sized for slide use (1920x1080) with the terminal embedded so it
matches V1/V2 framing.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent
OUT = OUT_DIR / "v3-terminal-scan.gif"

# Brand
CREAM = (245, 240, 229)
CREAM_2 = (236, 229, 213)
NAVY = (26, 37, 48)
NAVY_2 = (42, 53, 64)
MUTED = (107, 95, 77)
RULE = (212, 204, 184)
TERMINAL_BG = (15, 22, 30)        # nearly black, navy-tinted
TERMINAL_FG = (235, 224, 196)     # warm cream-ish
TERMINAL_DIM = (140, 130, 110)
TERMINAL_PROMPT = (180, 200, 175)
RED = (220, 95, 85)
ORANGE = (220, 140, 70)
YELLOW = (220, 185, 90)
GREEN = (140, 180, 130)

W, H = 1920, 1080

# Fonts
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/Library/Fonts/Menlo.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()

def sans(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


# The full output we want to "type out"
# (text, color) pairs. None color = TERMINAL_FG default.
# A None entry separates lines.
SCRIPT: list[tuple[str, tuple[int, int, int] | None]] = [
    ("Agents Shipgate 0.5.1", None),
    ("", None),
    ("Project: support-refund-agent   Agent: refund-assistant   Target: production_like", None),
    ("", None),
    ("Status:   Release blockers detected", RED),
    ("Critical: 2     High: 14     Medium: 2     Low: 0", None),
    ("Evidence coverage: mixed   |   Human review: recommended", TERMINAL_DIM),
    ("", None),
    ("Top findings:", None),
    ("  CRITICAL  SHIP-POLICY-APPROVAL-MISSING     stripe.create_refund", RED),
    ("  CRITICAL  SHIP-SIDEFX-IDEMPOTENCY-MISSING  stripe.create_refund", RED),
    ("  HIGH      SHIP-AUTH-MANIFEST-BROAD-SCOPE   manifest scopes=['stripe:*']", ORANGE),
    ("  HIGH      SHIP-INVENTORY-WILDCARD-TOOLS    wildcard_mcp_tools.*", ORANGE),
    ("  HIGH      SHIP-SCHEMA-MISSING-BOUNDS       stripe.create_refund.amount", ORANGE),
    ("  HIGH      SHIP-SCHEMA-BROAD-FREE-TEXT      gmail.send_customer_email", ORANGE),
    ("", None),
    ("Reports written:  report.md   report.json   report.sarif", TERMINAL_DIM),
    ("", None),
    ("CI mode: strict           Exit code: 20", RED),
]

COMMAND_TEXT = "$ agents-shipgate scan --config support-refund-agent/shipgate.yaml"


@dataclass
class TerminalState:
    """Snapshot of what the terminal currently shows, for one frame."""
    typed_chars: int = 0          # how many chars of the command have been typed
    revealed_lines: int = 0       # how many output lines have been revealed
    cursor_visible: bool = True
    show_done: bool = False       # show the trailing "✕ release blocked" callout


# Layout inside the slide
TERM_X, TERM_Y = 96, 250
TERM_W, TERM_H = 1728, 720
PAD = 36
LINE_H = 30
HEAD_LINE_H = 38
FONT_MONO = font(21)
FONT_MONO_BOLD = font(21, bold=True)
FONT_HEAD_KICKER = sans(20, bold=True)
FONT_HEAD = sans(56, bold=True)
FONT_SUB = sans(22)
FONT_FOOT = sans(18)
FONT_BRAND = sans(18, bold=True)
FONT_TERM_TITLE = sans(15, bold=True)


def draw_slide_chrome(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    # Kicker
    d.text((96, 88), "PHASE 1 · STATIC RELEASE-READINESS SCANNER", fill=MUTED, font=FONT_HEAD_KICKER)
    # Headline
    d.text((96, 124), "What `agents-shipgate scan` actually says", fill=NAVY, font=FONT_HEAD)
    # Subhead
    d.text((96, 196), "Run on a real OpenAI Agents SDK + MCP + OpenAPI surface for a refund agent.",
           fill=NAVY_2, font=FONT_SUB)
    # Footer
    d.ellipse((96, 1024, 110, 1038), fill=NAVY)
    d.text((122, 1020), "Three Moons Lab · agents-shipgate v0.5.1", fill=NAVY, font=FONT_BRAND)
    d.text((1820, 1020), "Slide 8 / V3", fill=MUTED, font=FONT_BRAND, anchor="ra")


def draw_terminal_frame(img: Image.Image, state: TerminalState) -> None:
    d = ImageDraw.Draw(img)
    # Terminal panel
    d.rounded_rectangle((TERM_X, TERM_Y, TERM_X + TERM_W, TERM_Y + TERM_H), radius=10, fill=TERMINAL_BG)
    # macOS-ish window dots
    d.ellipse((TERM_X + 22, TERM_Y + 18, TERM_X + 38, TERM_Y + 34), fill=(190, 90, 80))
    d.ellipse((TERM_X + 46, TERM_Y + 18, TERM_X + 62, TERM_Y + 34), fill=(200, 170, 90))
    d.ellipse((TERM_X + 70, TERM_Y + 18, TERM_X + 86, TERM_Y + 34), fill=(140, 180, 130))
    # Title bar text
    d.text((TERM_X + TERM_W // 2, TERM_Y + 21), "support-refund-agent — agents-shipgate",
           fill=TERMINAL_DIM, font=FONT_TERM_TITLE, anchor="ma")
    # divider
    d.line((TERM_X, TERM_Y + 52, TERM_X + TERM_W, TERM_Y + 52), fill=(35, 45, 55), width=1)

    # Inside content
    cx = TERM_X + PAD
    cy = TERM_Y + 70

    # Line 1: typed command, character by character
    typed = COMMAND_TEXT[: state.typed_chars]
    # split into prompt + rest for color
    if typed.startswith("$"):
        d.text((cx, cy), "$", fill=TERMINAL_PROMPT, font=FONT_MONO_BOLD)
        rest = typed[1:]
        # measure "$ " width
        dx, _ = FONT_MONO_BOLD.getbbox("$")[2:]
        d.text((cx + dx + 6, cy), rest, fill=TERMINAL_FG, font=FONT_MONO)
    else:
        d.text((cx, cy), typed, fill=TERMINAL_FG, font=FONT_MONO)
    # blinking cursor at end of typed text (only while typing or after if no output yet)
    if state.cursor_visible and state.revealed_lines == 0:
        cmd_w, _ = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), typed, font=FONT_MONO)[2:]
        # use textlength which returns just the width
        cmd_w = FONT_MONO.getlength(typed)
        d.rectangle((cx + cmd_w + 4, cy + 2, cx + cmd_w + 16, cy + LINE_H - 4), fill=TERMINAL_FG)

    # Output lines (after one blank line of separation)
    if state.revealed_lines > 0:
        oy = cy + LINE_H + 12
        for i, (text, color) in enumerate(SCRIPT[: state.revealed_lines]):
            ink = color if color is not None else TERMINAL_FG
            if text == "":
                pass  # blank line — just skip the height
            else:
                d.text((cx, oy), text, fill=ink, font=FONT_MONO)
            oy += LINE_H

        # Trailing prompt + cursor only if we've revealed everything
        if state.revealed_lines >= len(SCRIPT):
            oy += 4
            d.text((cx, oy), "$", fill=TERMINAL_PROMPT, font=FONT_MONO_BOLD)
            if state.cursor_visible:
                d.rectangle((cx + 22, oy + 2, cx + 34, oy + LINE_H - 4), fill=TERMINAL_FG)


def render_frame(state: TerminalState) -> Image.Image:
    img = Image.new("RGB", (W, H), CREAM)
    draw_slide_chrome(img)
    draw_terminal_frame(img, state)
    return img


def build_frames() -> list[tuple[Image.Image, int]]:
    """Return list of (frame, duration_ms)."""
    frames: list[tuple[Image.Image, int]] = []

    # 1. Empty terminal, blinking cursor on empty prompt — 0.6s with 2 blink cycles
    for _ in range(2):
        frames.append((render_frame(TerminalState(typed_chars=0, revealed_lines=0, cursor_visible=True)), 200))
        frames.append((render_frame(TerminalState(typed_chars=0, revealed_lines=0, cursor_visible=False)), 200))

    # 2. Type the command character by character
    cmd_len = len(COMMAND_TEXT)
    # Type in chunks of 3 chars per frame for snappiness
    chunk = 3
    pos = 1  # already showed $
    while pos <= cmd_len:
        frames.append((render_frame(TerminalState(typed_chars=pos, revealed_lines=0, cursor_visible=True)), 30))
        pos += chunk
    # Final state with full command
    frames.append((render_frame(TerminalState(typed_chars=cmd_len, revealed_lines=0, cursor_visible=True)), 250))
    frames.append((render_frame(TerminalState(typed_chars=cmd_len, revealed_lines=0, cursor_visible=False)), 250))

    # 3. Reveal output lines progressively, faster at start, slower around verdict
    pacing = {
        0: 80,    # banner
        4: 400,   # Status line — pause for emphasis
        5: 250,   # severity counts
        6: 200,
        8: 250,   # Top findings:
        9: 280,   # first critical
        10: 280,  # second critical
        11: 180,
        12: 180,
        13: 180,
        14: 180,
        16: 200,
        18: 800,  # final exit code, hold
    }
    for i in range(1, len(SCRIPT) + 1):
        dur = pacing.get(i - 1, 90)
        frames.append((
            render_frame(TerminalState(typed_chars=cmd_len, revealed_lines=i, cursor_visible=False)),
            dur,
        ))

    # 4. Hold + blink final cursor 3 times (~2.4s)
    for _ in range(3):
        frames.append((render_frame(TerminalState(typed_chars=cmd_len, revealed_lines=len(SCRIPT), cursor_visible=True)), 400))
        frames.append((render_frame(TerminalState(typed_chars=cmd_len, revealed_lines=len(SCRIPT), cursor_visible=False)), 400))

    return frames


def main():
    frames = build_frames()
    print(f"Built {len(frames)} frames")
    images = [f for f, _ in frames]
    durations = [d for _, d in frames]
    # Reduce filesize: convert to P mode with adaptive palette
    # First downsize to keep GIF reasonable for slide use
    target_w = 1280
    target_h = int(H * target_w / W)
    images = [im.resize((target_w, target_h), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=128) for im in images]
    images[0].save(
        OUT,
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"Wrote {OUT}  ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
