#!/usr/bin/env python3
"""Pull the latest icons from upstream wwm-uicons and regenerate the outlined set.

Compares a content hash of every upstream PNG against `.outline-manifest`, so
only icons that actually changed upstream are re-rendered. Changing
`outline.config.json` (or the generator itself) changes the recorded config
hash, which forces a full rebuild automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import outline  # noqa: E402

UPSTREAM = "https://github.com/WatWowMap/wwm-uicons.git"
MANIFEST = ".outline-manifest"
STATE = ".outline-state.json"


def sh(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def read_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if "\t" in line:
            rel, sha = line.rsplit("\t", 1)
            out[rel] = sha
    return out


def write_manifest(path: Path, manifest: dict[str, str]) -> None:
    path.write_text("".join(f"{rel}\t{sha}\n" for rel, sha in sorted(manifest.items())))


SKIP_DIRS = {".git", ".github", "tools", "docs", "node_modules"}


def prune_empty_dirs(root: Path) -> list[str]:
    """Drop category directories that no longer hold any icon (index.json goes too)."""
    removed = []
    categories = [d for d in root.iterdir() if d.is_dir() and d.name not in SKIP_DIRS]
    for category in categories:
        for dirpath, dirnames, filenames in os.walk(category, topdown=False):
            d = Path(dirpath)
            if any(f.lower().endswith(outline.ICON_SUFFIXES) for f in filenames) or dirnames:
                continue
            shutil.rmtree(d)
            removed.append(str(d.relative_to(root)))
    return removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--src", type=Path, default=None, help="existing upstream checkout (skips the clone)")
    ap.add_argument("--upstream", default=None, help="overrides the config's `upstream`")
    ap.add_argument("--full", action="store_true", help="rebuild every icon")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = args.repo.resolve()
    cfg = outline.load_config(repo / "outline.config.json")
    upstream_url = args.upstream or cfg.get("upstream") or UPSTREAM
    cfg_hash = outline.config_hash(cfg)
    gen_hash = hashlib.sha256((repo / "tools" / "outline.py").read_bytes()).hexdigest()[:16]
    build_hash = hashlib.sha256(f"{cfg_hash}:{gen_hash}".encode()).hexdigest()[:16]

    previous_state = {}
    if (repo / STATE).exists():
        previous_state = json.loads((repo / STATE).read_text())

    tmp: tempfile.TemporaryDirectory | None = None
    if args.src:
        # Building from a local checkout tells us nothing new about which upstream
        # commit the icons came from, so keep whatever was recorded before.
        src = args.src.resolve()
        commit = previous_state.get("commit", "(local)")
    else:
        tmp = tempfile.TemporaryDirectory(prefix="wwm-upstream-")
        src = Path(tmp.name) / "upstream"
        print(f"cloning {upstream_url} ...", flush=True)
        sh("git", "clone", "--depth", "1", "--quiet", upstream_url, str(src))
        commit = sh("git", "rev-parse", "HEAD", cwd=src)

    try:
        print("hashing upstream tree ...", flush=True)
        current = {rel: digest(src / rel) for rel in outline.iter_icons(src)}

        state = previous_state
        previous = read_manifest(repo / MANIFEST)

        full = args.full or not previous or state.get("build_hash") != build_hash
        if full:
            reason = (
                "--full" if args.full
                else "no manifest" if not previous
                else f"generator or config changed ({state.get('build_hash')} -> {build_hash})"
            )
            print(f"full rebuild: {reason}")
            changed = sorted(current)
        else:
            changed = sorted(rel for rel, sha in current.items() if previous.get(rel) != sha)

        removed = sorted(set(previous) - set(current))

        dirty = bool(changed or removed)
        # Record the commit even when nothing rendered, otherwise a poll that compares
        # SHAs would re-run the whole sync forever on an upstream commit that changed
        # no icons (a README edit, say).
        commit_moved = commit not in ("(local)",) and commit != previous_state.get("commit")
        print(f"upstream {commit[:8]}: {len(current)} icons, {len(changed)} to render, {len(removed)} to delete")
        if args.dry_run:
            mark = "~" if full else "+"   # a full rebuild re-renders, it does not add
            for rel in changed[:20]:
                print(f"  {mark}", rel)
            if len(changed) > 20:
                print(f"  ... and {len(changed) - 20} more")
            for rel in removed[:20]:
                print("  -", rel)
            return 0

        for rel in removed:
            target = repo / rel
            if target.exists():
                target.unlink()

        counts, errors = outline.run(src, repo, cfg, changed, args.jobs)
        if errors:
            for err in errors[:20]:
                print("  !", err, file=sys.stderr)
            print(f"{len(errors)} icons failed; not updating the manifest", file=sys.stderr)
            return 1

        pruned = prune_empty_dirs(repo) if removed else []

        # Only touch the bookkeeping files when something actually moved, so an
        # unchanged upstream leaves a clean working tree and produces no commit.
        if dirty:
            write_manifest(repo / MANIFEST, current)
        if dirty or commit_moved or not (repo / STATE).exists():
            (repo / STATE).write_text(
                json.dumps(
                    {
                        "upstream": upstream_url,
                        "commit": commit,
                        "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "build_hash": build_hash,
                        "config_hash": cfg_hash,
                        "generator_hash": gen_hash,
                        "icons": len(current),
                    },
                    indent=2,
                )
                + "\n"
            )

        if dirty:
            print("rebuilding index.json ...", flush=True)
            subprocess.run(["node", "-e", 'require("./index").update()'], cwd=repo, check=True)

        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no changes"
        if removed:
            summary += f", deleted={len(removed)}"
        if pruned:
            summary += f", pruned_dirs={len(pruned)}"
        print(f"done: {summary}")

        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
                fh.write(f"commit={commit}\n")
                fh.write(f"short={commit[:8]}\n")
                fh.write(f"rendered={counts.get('outlined', 0) + counts.get('copied', 0)}\n")
                fh.write(f"deleted={len(removed)}\n")
                fh.write(f"full={'true' if full else 'false'}\n")
        return 0
    finally:
        if tmp:
            tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
