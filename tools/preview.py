#!/usr/bin/env python3
"""Render a contact sheet of the outlined icons for the README.

With --src pointing at an upstream checkout you get a before/after comparison;
without it, the outlined icons are shown on a light and a dark map background.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SAMPLES = [
    "pokemon/25.png",
    "pokemon/94_s.png",
    "pokemon/149.png",
    "pokemon/151.png",
    "reward/item/1.png",
    "reward/candy/25.png",
    "invasion/48.png",
    "pokestop/505_ar.png",
    "raid/egg/6_h.png",
    "gym/1_t3_ex_ar.png",
    "team/2.png",
    "weather/3_n.png",
]
LIGHT = (232, 230, 223, 255)
DARK = (31, 36, 48, 255)
CELL = 96
PAD = 10
GUTTER = 96


def font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def paste(sheet: Image.Image, path: Path, x: int, y: int) -> None:
    with Image.open(path) as im:
        im = im.convert("RGBA")
    w, h = im.size
    s = min(CELL / w, CELL / h)
    im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    sheet.alpha_composite(im, (x + (CELL - im.width) // 2, y + (CELL - im.height) // 2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--src", type=Path, default=None, help="upstream checkout, for a before/after sheet")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    repo = args.repo.resolve()
    out = args.out or repo / "docs" / "preview.png"
    def resolve(rel: str) -> str | None:
        for suffix in (".png", ".webp"):
            candidate = rel.rsplit(".", 1)[0] + suffix
            if (repo / candidate).exists():
                return candidate
        return None

    samples = [r for r in (resolve(s) for s in SAMPLES) if r]

    rows = (
        [("upstream", args.src, LIGHT), ("outlined", repo, LIGHT)]
        if args.src
        else [("outlined", repo, LIGHT), ("outlined", repo, DARK)]
    )

    width = GUTTER + PAD + len(samples) * (CELL + PAD)
    height = PAD + len(rows) * (CELL + PAD)
    sheet = Image.new("RGBA", (width, height), LIGHT)
    draw = ImageDraw.Draw(sheet)
    label = font(13)

    for r, (name, root, bg) in enumerate(rows):
        y = PAD + r * (CELL + PAD)
        draw.rectangle([0, y - PAD // 2, width, y + CELL + PAD // 2], fill=bg)
        draw.text(
            (12, y + CELL // 2 - 7),
            name,
            font=label,
            fill=(40, 40, 40) if bg == LIGHT else (225, 225, 225),
        )
        for c, rel in enumerate(samples):
            src = Path(root) / rel
            if not src.exists():
                src = Path(root) / (rel.rsplit(".", 1)[0] + ".png")
            if src.exists():
                paste(sheet, src, GUTTER + PAD + c * (CELL + PAD), y)

    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(out, optimize=True)
    print(f"{out} ({sheet.width}x{sheet.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
