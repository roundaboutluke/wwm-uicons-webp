#!/usr/bin/env python3
"""Generate outlined variants of a UICONS icon tree.

Reads a source tree of PNGs (an unmodified checkout of WatWowMap/wwm-uicons)
and writes an identically-shaped tree where every enabled category has a
contrasting stroke drawn around the sprite silhouette.

Only Pillow + numpy are required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ICON_SUFFIXES = (".png", ".webp")

DEFAULT_CONFIG = {
    "reference_size": 128,
    "defaults": {
        "enabled": True,
        "width": 3.0,
        "color": "#FFFFFF",
        "opacity": 1.0,
        "solid_at": 128,
        "mode": "fit",
        "square": False,
        "outline": True,
        "canvas": None,
        "content": None,
        "resize": None,
        "shadow": None,
    },
    "categories": {},
}


@dataclass(frozen=True)
class Style:
    enabled: bool = True
    width: float = 3.0
    color: str = "#FFFFFF"
    opacity: float = 1.0
    solid_at: int = 128
    mode: str = "fit"  # fit | expand | clip | inset
    square: bool = False
    outline: bool = True
    canvas: int | None = None
    content: int | None = None
    resize: int | None = None
    shadow: dict | None = None
    reference_size: int = 128


def parse_color(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        raise ValueError(f"bad colour {value!r}")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def disc_offsets(radius: int) -> list[tuple[int, int]]:
    limit = radius * radius + 0.25
    return [
        (dy, dx)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
        if dy * dy + dx * dx <= limit and (dy or dx)
    ]


def dilate(a: np.ndarray, radius: int) -> np.ndarray:
    """Greyscale dilation of `a` by a disc of `radius` (max over the disc)."""
    h, w = a.shape
    out = a.copy()
    for dy, dx in disc_offsets(radius):
        ys0, ys1 = max(0, dy), h + min(0, dy)
        xs0, xs1 = max(0, dx), w + min(0, dx)
        np.maximum(
            out[ys0:ys1, xs0:xs1],
            a[ys0 - dy : ys1 - dy, xs0 - dx : xs1 - dx],
            out=out[ys0:ys1, xs0:xs1],
        )
    return out


def resize_longest(im: Image.Image, longest: int) -> Image.Image:
    """Scale the whole image so its longer side is `longest`, aspect preserved."""
    w, h = im.size
    scale = longest / max(w, h)
    return im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)


def to_canvas(im: Image.Image, canvas: int, content: int | None) -> Image.Image:
    """Scale the artwork to `content` on its longer side, centred on a square canvas.

    Normalises icons of any aspect ratio onto one square with a small margin.
    """
    box = im.getchannel("A").getbbox()
    if box:
        im = im.crop(box)
    target = content or canvas
    w, h = im.size
    scale = target / max(w, h)
    im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    out = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    out.paste(im, ((canvas - im.width) // 2, (canvas - im.height) // 2))
    return out


def to_square(im: Image.Image) -> Image.Image:
    """Centre the image on a transparent square canvas of its longer side."""
    w, h = im.size
    if w == h:
        return im
    side = max(w, h)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(im, ((side - w) // 2, (side - h) // 2))
    return out


def shape(im: Image.Image, style: Style) -> Image.Image:
    """Apply the canvas convention: square-and-centre, plain resize, or neither."""
    if style.canvas:
        return to_canvas(im, style.canvas, style.content)
    if style.resize:
        return resize_longest(im, style.resize)
    return to_square(im) if style.square else im


def stroke_at(im: Image.Image, radius: int, style: Style) -> Image.Image:
    """Pad by `radius` and draw the stroke around the silhouette at this resolution."""
    w, h = im.size
    padded = Image.new("RGBA", (w + 2 * radius, h + 2 * radius), (0, 0, 0, 0))
    padded.paste(im, (radius, radius))
    alpha = np.asarray(padded.getchannel("A"), dtype=np.float32)
    if not alpha.any():
        return padded
    gain = 255.0 / max(1, min(255, style.solid_at))
    grown = np.clip(dilate(alpha, radius) * gain, 0, 255) * style.opacity
    layer = Image.new("RGBA", padded.size, parse_color(style.color) + (0,))
    layer.putalpha(Image.fromarray(np.clip(grown, 0, 255).astype(np.uint8), "L"))
    return Image.alpha_composite(layer, padded)


def outline_image(src: Image.Image, style: Style) -> Image.Image | None:
    """Render one icon: scale the artwork to its target, stroke it, place it on the canvas.

    The stroke is drawn at the *final* resolution, so its thickness is `width`
    pixels whatever the category's canvas size. Drawing it before scaling would
    make the stroke thinner on categories that scale down further.
    """
    src = src.convert("RGBA")
    if np.asarray(src.getchannel("A")).min() == 255:
        return None  # fully opaque, no silhouette to trace

    target = style.content or style.canvas or style.resize
    if target:
        # `content` means "make the artwork this big", so crop away its margin first.
        # `canvas`/`resize` alone means "scale the whole frame", keeping the margin.
        if style.content:
            box = src.getchannel("A").getbbox()
            if box:
                src = src.crop(box)
        radius = max(1, int(round(style.width))) if style.outline else 0
        art = max(1, target - 2 * radius)
        w, h = src.size
        scale = art / max(w, h)
        src = src.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        out = stroke_at(src, radius, style) if radius else src
        if style.canvas:
            canvas = Image.new("RGBA", (style.canvas, style.canvas), (0, 0, 0, 0))
            canvas.paste(out, ((style.canvas - out.width) // 2, (style.canvas - out.height) // 2))
            return canvas
        return out

    if not style.outline:
        return None  # nothing to do without canvas rules: copied through as upstream
    w, h = src.size
    radius = max(1, int(round(style.width * max(w, h) / float(style.reference_size or 128))))
    out = stroke_at(src, radius, style)
    if style.mode == "fit":
        out = out.resize((w, h), Image.LANCZOS)
    return out


# --------------------------------------------------------------------------- config


def load_config(path: Path | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and path.exists():
        user = json.loads(path.read_text())
        cfg["reference_size"] = user.get("reference_size", cfg["reference_size"])
        if "upstream" in user:
            cfg["upstream"] = user["upstream"]
        cfg["defaults"].update(user.get("defaults", {}))
        cfg["categories"] = user.get("categories", {})
    return cfg


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


def style_for(cfg: dict, relpath: str) -> Style:
    base = dict(cfg["defaults"])
    base["reference_size"] = cfg.get("reference_size", 128)
    parts = Path(relpath).parts
    # Longest matching prefix wins: "reward", then "reward/item", then the exact file.
    for depth in range(1, len(parts) + 1):
        key = "/".join(parts[:depth])
        override = cfg["categories"].get(key)
        if override:
            base.update(override)
    # Glob keys apply last so a pattern can refine a whole category, e.g. "*_h.png".
    for key, override in cfg["categories"].items():
        if "*" in key and fnmatch(relpath, key):
            base.update(override)
    known = {f.name for f in Style.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return Style(**{k: v for k, v in base.items() if k in known})


# --------------------------------------------------------------------------- worker

_CTX: dict = {}


def _init(src_root: str, dst_root: str, cfg: dict) -> None:
    _CTX["src"] = Path(src_root)
    _CTX["dst"] = Path(dst_root)
    _CTX["cfg"] = cfg


def _process(relpath: str) -> tuple[str, str]:
    src = _CTX["src"] / relpath
    dst = _CTX["dst"] / relpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    style = style_for(_CTX["cfg"], relpath)
    if not style.enabled:
        shutil.copyfile(src, dst)
        return relpath, "copied"
    try:
        with Image.open(src) as im:
            out = outline_image(im, style)
        if out is None:
            shutil.copyfile(src, dst)
            return relpath, "copied"
        save_icon(out, dst)
    except Exception as exc:  # noqa: BLE001 - report and keep going
        return relpath, f"error: {exc}"
    return relpath, "outlined"


# --------------------------------------------------------------------------- main


def save_icon(im: Image.Image, path: Path) -> None:
    """Write in the format the path asks for. WebP is written losslessly: upstream's
    WebP is already lossy, and re-encoding lossily would stack a second generation of
    artefacts on top of it for a file that is barely smaller."""
    if path.suffix.lower() == ".webp":
        im.save(path, "WEBP", lossless=True, method=6)
    else:
        im.save(path, "PNG", optimize=True)


def iter_icons(root: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", ".github", "tools", "docs", "node_modules"}]
        for name in filenames:
            if name.lower().endswith(ICON_SUFFIXES):
                out.append(str(Path(dirpath, name).relative_to(root)))
    return sorted(out)


iter_pngs = iter_icons  # kept for callers using the old name


def run(
    src: Path,
    dst: Path,
    cfg: dict,
    targets: list[str],
    jobs: int = 0,
    quiet: bool = False,
) -> tuple[dict[str, int], list[str]]:
    """Generate `targets` (paths relative to `src`) into `dst`. Returns (counts, errors)."""
    targets = [t for t in targets if (src / t).is_file()]
    counts: dict[str, int] = {}
    errors: list[str] = []
    if not targets:
        return counts, errors

    dst.mkdir(parents=True, exist_ok=True)
    jobs = max(1, jobs or os.cpu_count() or 4)

    with ProcessPoolExecutor(
        max_workers=jobs, initializer=_init, initargs=(str(src), str(dst), cfg)
    ) as pool:
        for i, (rel, status) in enumerate(pool.map(_process, targets, chunksize=32), 1):
            key = "error" if status.startswith("error") else status
            counts[key] = counts.get(key, 0) + 1
            if key == "error":
                errors.append(f"{rel}: {status}")
            if not quiet and (i % 500 == 0 or i == len(targets)):
                print(f"  {i}/{len(targets)}", flush=True)
    return counts, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, type=Path, help="source icon tree")
    ap.add_argument("--dst", required=True, type=Path, help="destination icon tree")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument(
        "--only",
        action="append",
        default=None,
        help="limit to these relative paths (repeatable); '-' reads them from stdin",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.only == ["-"]:
        targets = [line.strip() for line in sys.stdin if line.strip()]
    elif args.only:
        targets = args.only
    else:
        targets = iter_pngs(args.src)

    counts, errors = run(args.src, args.dst, cfg, targets, args.jobs, args.quiet)
    if not counts:
        print("nothing to do")
        return 0
    print(f"config {config_hash(cfg)}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for err in errors[:20]:
        print("  !", err, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
