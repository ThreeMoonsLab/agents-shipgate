"""Crop image fragments from rendered slides for embedding in the native pptx.

We only crop the regions that can't be reconstructed in PowerPoint shapes
without losing visual intent:

- Slide 4 Gödel self-reference loop SVG (right column)
- Slide 5 FEP three-region diagram (right column)
- Slide 8 syntax-highlighted editor panel + report card (full slide content)
"""
from pathlib import Path

from PIL import Image

BUILD = Path(__file__).resolve().parent / "build"
FRAGS = BUILD / "fragments"
FRAGS.mkdir(exist_ok=True)

# Rendered slides are 1920x1080@2x = 3840x2160. Coordinates given in 1x.
def crop_2x(src: Path, x: int, y: int, w: int, h: int, out: Path):
    im = Image.open(src)
    sx = im.width / 1920
    sy = im.height / 1080
    box = (int(x * sx), int(y * sy), int((x + w) * sx), int((y + h) * sy))
    crop = im.crop(box)
    crop.save(out, optimize=True)
    print(f"  {out.name}  ({crop.size[0]}×{crop.size[1]})")


# Slide 4 — self-reference loop. SVG was at (~960, ~430) with size (540, 400).
crop_2x(BUILD / "slide-04.png", 920, 380, 700, 500, FRAGS / "godel-loop.png")

# Slide 5 — FEP three-region diagram. Was right column.
crop_2x(BUILD / "slide-05.png", 1000, 400, 800, 480, FRAGS / "fep-boundary.png")

# Slide 8 — keep the entire editor + report panel as a single image fragment.
# Use the v2 source which is already at exactly the right composition.
slide_08 = BUILD / "slide-08.png"
crop_2x(slide_08, 0, 0, 1920, 1080, FRAGS / "slide-08-full.png")
