"""Dry-run beats against the live UI without narration or recording.

    venv/bin/python dryrun.py episodes/ep13_whatsnew.py [beat ...]

Runs reset → action → verify for the named beats (all beats when none are
named), screenshots the stage after each action, and prints a pass/fail line
per beat. Nothing is narrated and nothing is recorded, so audio_foundry is
not needed; the GPU is touched only by beats that call the chat model.
"""

from __future__ import annotations

import importlib.util
import sys
import time
import traceback
from pathlib import Path

from director import FRONTEND, Stage
from helpers import REPO, kill_stage_terminal, snapshot


def load(path: str):
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    mod = load(sys.argv[1])
    wanted = set(sys.argv[2:])
    beats = [b for b in mod.BEATS if not wanted or b.name in wanted]
    out = REPO / "docs" / "local-workspace-only" / "demo_dryrun" / Path(sys.argv[1]).stem
    out.mkdir(parents=True, exist_ok=True)
    stage = Stage()
    results = []
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=60_000)
        stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        for b in beats:
            t0 = time.monotonic()
            try:
                if b.reset:
                    b.reset(stage)
                b.action(stage)
                snapshot(stage, out / f"{b.name}.png")
                if b.verify:
                    b.verify(stage)
                results.append((b.name, "ok", f"{time.monotonic() - t0:.1f}s"))
            except Exception as e:
                snapshot(stage, out / f"{b.name}_FAIL.png")
                results.append((b.name, "FAIL", f"{type(e).__name__}: {e}"))
                traceback.print_exc()
    finally:
        kill_stage_terminal()
        stage.close()
    print("\n== dry run ==")
    for name, status, info in results:
        print(f"  {status:4} {name:16} {info}")
    print(f"screens: {out}")
    if any(s == "FAIL" for _, s, _ in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
