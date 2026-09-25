#!/usr/bin/env python3
"""Run the post-render frame checker over existing clips and print what it sees.

The thresholds in backend/services/video_consistency_metrics.QUALITY_THRESHOLDS
were set from what each defect looks like, not from a corpus of renders. Point
this at a folder of real outputs (good ones and ones you know are bad) to see
the metrics each threshold is compared against, and adjust the table.

Usage:
    python scripts/video_quality_scan.py data/uploads/Videos
    python scripts/video_quality_scan.py some/clip.mp4 --json > scan.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    from backend.services.video_consistency_metrics import inspect_video_frames

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", type=Path, help="clips, or folders searched for *.mp4")
    parser.add_argument("--json", action="store_true", help="one JSON object per clip")
    args = parser.parse_args(argv)

    files = []
    for p in args.paths:
        files.extend(sorted(p.rglob("*.mp4")) if p.is_dir() else [p])
    for f in files:
        r = inspect_video_frames(f)
        if args.json:
            print(json.dumps({"path": str(f), **{k: r.get(k) for k in (
                "width", "height", "frames", "fps", "metrics", "flags")}}))
            continue
        m = r.get("metrics") or {}
        codes = ",".join(x["code"] for x in r.get("flags") or []) or "-"
        print(f"{f}  {r.get('width')}x{r.get('height')} {r.get('frames')}f  "
              f"luma {m.get('mean_luma', 0):.0f} spread {m.get('spread', 0):.0f} "
              f"sat {m.get('saturation', 0):.2f} clip {m.get('clipped', 0):.2f} "
              f"crush {m.get('crushed', 0):.2f} still {m.get('longest_still_run', 0)}  flags: {codes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
