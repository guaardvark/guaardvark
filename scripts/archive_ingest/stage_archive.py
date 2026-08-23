#!/usr/bin/env python3
"""Stage a document archive for indexing: filter, dedup, and record the chronology.

Measured on the source archive this was written for: 292,421 files, of which
only 864 were documents. The rest was node_modules, build output and scraped web
assets. Filtering is therefore the whole game and content-hash dedup is a
rounding error (864 -> 615), which is the opposite of what you would guess.

Because these are backup snapshots of one evolving project, the same document
appears at several paths with different mtimes. That is not noise to discard:
sorting the duplicate set by mtime reconstructs the project's chronology for
free, so the manifest keeps every path rather than only the survivor.

Nothing is copied into the repository. The staging directory must live under an
ignored path -- these files carry machine paths and identity, and a tracked
landing zone would publish them permanently.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

EXCLUDED_DIRS = {
    "node_modules", ".git", "dist", "build", "venv", ".venv", "site-packages",
    "__pycache__", ".next", ".cache", "vendor", "target", ".pytest_cache",
    "coverage", "htmlcov", ".mypy_cache", "bower_components",
}
DOC_EXTENSIONS = {".md", ".markdown", ".mdx", ".txt", ".pdf", ".docx", ".rst"}

# Vendored boilerplate that survives dedup because each copy differs slightly,
# yet carries nothing about the project being reconstructed.
NOISE_NAMES = {
    "readme.md", "license.md", "license.txt", "changelog.md", "code_of_conduct.md",
    "robots.txt", "llms.txt", "llm.txt", "llms-full.txt", "requirements.txt",
    "contributing.md", "security.md",
}


def iter_documents(root: Path, keep_noise: bool):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix.lower() not in DOC_EXTENSIONS:
                continue
            if not keep_noise and name.lower() in NOISE_NAMES:
                continue
            path = Path(dirpath) / name
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                    continue
            except OSError:
                continue
            yield path


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return ""
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="Archive root to scan")
    ap.add_argument("--dest", required=True, help="Staging directory (must be a gitignored path)")
    ap.add_argument("--keep-noise", action="store_true",
                    help="Keep vendored boilerplate (README/LICENSE/robots.txt ...)")
    ap.add_argument("--link", action="store_true",
                    help="Hardlink instead of copying (same filesystem only)")
    ap.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    args = ap.parse_args()

    source = Path(args.source).expanduser().resolve()
    dest = Path(args.dest).expanduser().resolve()
    if not source.is_dir():
        print(f"error: source is not a directory: {source}", file=sys.stderr)
        return 2

    print(f"scanning {source} ...", flush=True)
    by_hash: dict = {}
    scanned = skipped = 0
    for path in iter_documents(source, args.keep_noise):
        scanned += 1
        digest = file_hash(path)
        if not digest:
            skipped += 1
            continue
        try:
            stat = path.stat()
        except OSError:
            skipped += 1
            continue
        entry = by_hash.setdefault(digest, {"paths": [], "size": stat.st_size})
        entry["paths"].append({
            "path": str(path),
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "mtime_epoch": stat.st_mtime,
        })
        if scanned % 500 == 0:
            print(f"  {scanned} documents, {len(by_hash)} unique so far", flush=True)

    unique_bytes = sum(e["size"] for e in by_hash.values())
    print(f"\nscanned:        {scanned} documents ({skipped} unreadable)")
    print(f"unique content: {len(by_hash)}")
    print(f"unique bytes:   {unique_bytes / 1048576:.1f} MB  (~{unique_bytes / 4 / 1e6:.2f}M tokens)")
    dup = scanned - len(by_hash)
    print(f"duplicates:     {dup} ({(dup / scanned * 100) if scanned else 0:.1f}%)")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    docs_dir = dest / "documents"
    docs_dir.mkdir(exist_ok=True)

    manifest = []
    staged = 0
    for digest, entry in by_hash.items():
        # Oldest occurrence names the document: it is the first time this exact
        # content appeared, which is the point on the timeline that matters.
        occurrences = sorted(entry["paths"], key=lambda p: p["mtime_epoch"])
        first = occurrences[0]
        src = Path(first["path"])
        out_name = f"{digest[:12]}__{src.name}"
        out_path = docs_dir / out_name
        if not out_path.exists():
            try:
                if args.link:
                    os.link(src, out_path)
                else:
                    shutil.copy2(src, out_path)
                staged += 1
            except OSError as exc:
                print(f"  warn: could not stage {src}: {exc}", file=sys.stderr)
                continue
        manifest.append({
            "hash": digest,
            "staged_as": out_name,
            "size": entry["size"],
            "first_seen": first["mtime"],
            "last_seen": occurrences[-1]["mtime"],
            "occurrences": len(occurrences),
            "paths": [p["path"] for p in occurrences],
        })

    manifest.sort(key=lambda m: m["first_seen"])
    manifest_path = dest / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({
            "source": str(source),
            "generated": datetime.now(timezone.utc).isoformat(),
            "scanned": scanned,
            "unique": len(by_hash),
            "documents": manifest,
        }, fh, indent=2)

    print(f"\nstaged {staged} new document(s) into {docs_dir}")
    print(f"manifest: {manifest_path}")
    if manifest:
        print(f"chronology: {manifest[0]['first_seen'][:10]} → {manifest[-1]['first_seen'][:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
