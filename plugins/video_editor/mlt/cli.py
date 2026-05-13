"""Beat-sync CLI — the M1 end-to-end entry point.

Usage:
    python -m plugins.video_editor.mlt.cli \\
        --audio path/to/song.wav \\
        --videos a.mp4 b.mp4 c.mp4 \\
        --out project.mlt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .beat_detector import BeatFilterParams, detect_beats
from .frame_math import FrameRate
from .mlt_parser import MediaAsset, ProjectProfile
from .mlt_writer import plan_cuts_from_beats, write_project


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate a beat-synced Shotcut .mlt project.")
    p.add_argument("--audio", required=True, help="Master soundtrack (wav/mp3/flac/...)")
    p.add_argument("--videos", required=True, nargs="+", help="Source video files to draw cuts from.")
    p.add_argument("--out", required=True, help="Output .mlt path.")
    p.add_argument("--fps", default="30", help="Project frame rate (e.g. 30, 60, 30000/1001).")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--subdivision", type=int, default=2, help="Keep every Nth beat (2=half-time).")
    p.add_argument("--min-clip", type=float, default=1.2, help="Minimum clip duration in seconds.")
    p.add_argument("--tightness", type=int, default=100, help="librosa beat_track tightness.")
    p.add_argument("--use-onsets", action="store_true", help="Cut on raw onsets instead of beats.")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducible cuts.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    audio_path = Path(args.audio).expanduser().resolve()
    if not audio_path.exists():
        print(f"audio file not found: {audio_path}", file=sys.stderr)
        return 2

    video_paths: list[Path] = []
    for v in args.videos:
        vp = Path(v).expanduser().resolve()
        if not vp.exists():
            print(f"video file not found: {vp}", file=sys.stderr)
            return 2
        video_paths.append(vp)

    profile = ProjectProfile(
        frame_rate=FrameRate.from_string(args.fps),
        width=args.width,
        height=args.height,
    )

    print(f"[beat-sync] analyzing {audio_path.name} ...", file=sys.stderr)
    analysis = detect_beats(
        str(audio_path),
        BeatFilterParams(
            subdivision=args.subdivision,
            min_clip_seconds=args.min_clip,
            tightness=args.tightness,
            use_onset_envelope=args.use_onsets,
        ),
    )
    print(
        f"[beat-sync] tempo={analysis.tempo_bpm:.2f} bpm  "
        f"beats={len(analysis.beat_times)}  duration={analysis.duration_seconds:.2f}s",
        file=sys.stderr,
    )

    assets = [MediaAsset(producer_id=f"src{i}", resource_path=str(p)) for i, p in enumerate(video_paths)]

    cuts = plan_cuts_from_beats(
        analysis.beat_times,
        assets,
        profile,
        seed=args.seed,
    )
    print(f"[beat-sync] planned {len(cuts)} cuts", file=sys.stderr)

    out_path = write_project(
        args.out,
        cuts,
        str(audio_path),
        profile,
        audio_out_seconds=analysis.duration_seconds,
    )
    print(f"[beat-sync] wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
