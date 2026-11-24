"""Draw the SheetFlat program icon (the logo mark: a flat sheet with two bend lines).

    python tools/make_icon.py   ->  sheetflat/web/icon.png (page icon) + build/sheetflat.ico (installer)
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
INK, PAPER, BEND = (21, 32, 42), (251, 252, 252), (207, 65, 41)


def draw(n: int) -> Image.Image:
    S = 1024                                   # draw big, then shrink: smooth edges at every size
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 0.2, fill=INK)
    x0, x1, y0, y1 = S * 0.14, S * 0.86, S * 0.30, S * 0.70
    d.rectangle([x0, y0, x1, y1], fill=PAPER)
    w = S * 0.05
    for fx in (0.39, 0.61):                    # the two bend lines, dashed
        x = S * fx
        y, dash, gap = y0, S * 0.075, S * 0.045
        while y < y1:
            d.rectangle([x - w / 2, y, x + w / 2, min(y + dash, y1)], fill=BEND)
            y += dash + gap
    return im.resize((n, n), Image.LANCZOS)


if __name__ == "__main__":
    big = draw(256)
    (ROOT / "build").mkdir(exist_ok=True)
    big.save(ROOT / "build" / "sheetflat.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    draw(192).save(ROOT / "sheetflat" / "web" / "icon.png")
    print("icon written")
