"""Post-build: contact sheet (3x5 grid PNG) + PDF deck."""
from pathlib import Path

from PIL import Image

BUILD_DIR = Path(__file__).resolve().parent / "build"

SLIDES = sorted(BUILD_DIR.glob("slide-*.png"))
assert len(SLIDES) == 15, f"Expected 15 slides, got {len(SLIDES)}"

# Contact sheet — 5 columns × 3 rows, exactly fills 15 slides
COLS, ROWS = 5, 3
THUMB_W = 720
GAP = 20
MARGIN = 40

# Compute thumb height from one slide
sample = Image.open(SLIDES[0])
ratio = sample.height / sample.width
THUMB_H = int(THUMB_W * ratio)

sheet_w = COLS * THUMB_W + (COLS - 1) * GAP + 2 * MARGIN
sheet_h = ROWS * THUMB_H + (ROWS - 1) * GAP + 2 * MARGIN + 100  # extra for header

sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 240, 229))

# Header band
from PIL import ImageDraw, ImageFont

draw = ImageDraw.Draw(sheet)
try:
    font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
    font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
except Exception:
    font_title = ImageFont.load_default()
    font_sub = ImageFont.load_default()

draw.text((MARGIN, 30), "Three Moons Lab — Release Readiness for Agentic Systems", fill=(26, 37, 48), font=font_title)
draw.text((MARGIN, 78), f"Working thesis · {len(SLIDES)} slides · contact sheet", fill=(107, 95, 77), font=font_sub)

for i, slide in enumerate(SLIDES):
    row, col = divmod(i, COLS)
    x = MARGIN + col * (THUMB_W + GAP)
    y = MARGIN + 100 + row * (THUMB_H + GAP)
    img = Image.open(slide).convert("RGB").resize((THUMB_W, THUMB_H), Image.LANCZOS)
    sheet.paste(img, (x, y))
    # Slide number badge
    draw.rectangle((x + 12, y + 12, x + 56, y + 36), fill=(26, 37, 48))
    draw.text((x + 22, y + 14), f"{i+1:02d}", fill=(245, 240, 229), font=font_sub)

contact_path = BUILD_DIR / "contact-sheet.png"
sheet.save(contact_path, optimize=True)
print(f"Contact sheet: {contact_path}  ({contact_path.stat().st_size // 1024} KB)")

# PDF deck — one page per slide at native 1920x1080
slide_imgs = [Image.open(s).convert("RGB") for s in SLIDES]
pdf_path = BUILD_DIR / "deck.pdf"
slide_imgs[0].save(
    pdf_path,
    "PDF",
    resolution=144.0,
    save_all=True,
    append_images=slide_imgs[1:],
)
print(f"PDF deck:      {pdf_path}  ({pdf_path.stat().st_size // 1024} KB)")
