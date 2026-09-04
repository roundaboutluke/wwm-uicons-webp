#!/usr/bin/env python3
"""Compare this set's geometry against a reference UICONS set.

The reference is a copy of the original outline set. Only *geometry* is compared —
canvas size and whether a stroke was drawn — because the artwork here comes from
wwm-uicons by design, and several categories of the original used different source
art. Content-size differences are reported for information, not as failures.

    python3 tools/verify_reference.py \
        --reference https://raw.githubusercontent.com/TiMXL73/PogoAssets/main/uicons-outline \
        --upstream /path/to/wwm-uicons --per-category 4
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outline import dilate  # noqa: E402


def erode(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return mask
    return ~(dilate((~mask).astype(np.float32) * 255.0, radius) > 127)


def measure(path_or_bytes) -> tuple[tuple[int, int], tuple[int, int], float]:
    """Canvas size, content bbox and how much of the outer 2px is dark."""
    im = Image.open(path_or_bytes).convert("RGBA")
    arr = np.asarray(im).astype(np.int16)
    alpha = arr[..., 3]
    box = im.getchannel("A").getbbox()
    if box is None:
        return im.size, (0, 0), 0.0
    mask = alpha >= 128
    ring = mask & ~erode(mask, 2) & (alpha >= 200)
    px = arr[..., :3][ring]
    dark = float((px.max(axis=1) <= 70).mean()) if len(px) else 0.0
    return im.size, (box[2] - box[0], box[3] - box[1]), dark


def fetch(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read()
    except Exception:
        return None


def flatten(node, prefix="") -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(flatten(v, f"{prefix}/{k}" if prefix else k))
    else:
        out[prefix] = list(node)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True, help="base URL of the reference set")
    ap.add_argument("--upstream", required=True, type=Path, help="a wwm-uicons checkout")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--per-category", type=int, default=4)
    args = ap.parse_args()

    base = args.reference.rstrip("/")
    index = fetch(f"{base}/index.json")
    if not index:
        print(f"could not read {base}/index.json", file=sys.stderr)
        return 2
    groups = flatten(json.loads(index))

    import io

    rows = defaultdict(list)
    for group, files in sorted(groups.items()):
        usable = [f for f in files if f.endswith(".png") and (args.upstream / group / f).exists()]
        step = max(1, len(usable) // args.per_category)
        for name in usable[::step][: args.per_category]:
            rel = f"{group}/{name}"
            raw = fetch(f"{base}/{rel}")
            if not raw or not (args.repo / rel).exists():
                continue
            t_size, t_box, t_dark = measure(io.BytesIO(raw))
            o_size, o_box, o_dark = measure(args.repo / rel)
            _, _, u_dark = measure(args.upstream / rel)
            rows[group].append((t_size, o_size, t_box, o_box, t_dark, o_dark, u_dark))

    canvas_ok = stroke_ok = total = 0
    print(f"{'category':<24}{'n':>4}{'canvas':>9}{'stroke':>9}  median content delta")
    for group in sorted(rows):
        v = rows[group]
        c = sum(1 for x in v if x[0] == x[1])
        s = sum(1 for x in v if ((x[4] - x[6]) >= 0.25) == ((x[5] - x[6]) >= 0.25))
        deltas = sorted(max(abs(x[2][0] - x[3][0]), abs(x[2][1] - x[3][1])) for x in v)
        canvas_ok += c
        stroke_ok += s
        total += len(v)
        print(f"  {group:<22}{len(v):>4}{f'{c}/{len(v)}':>9}{f'{s}/{len(v)}':>9}  {deltas[len(deltas)//2]}px")
    if total:
        print(f"\n  canvas match:          {canvas_ok}/{total} ({100*canvas_ok/total:.0f}%)")
        print(f"  stroke decision match: {stroke_ok}/{total} ({100*stroke_ok/total:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
