"""Shot assembly for training videos.

Narration is synthesized first and every shot's picture is built to its measured
length, so audio and video are in sync by construction rather than by trimming.

Stills get a slow push so a held frame never reads as a freeze; overlays fade in
over the moving picture and the narration is muxed last.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cards
import context
import visuals
from config import FPS, HEIGHT, OUT_ROOT, WIDTH
from narration import (ensure_narrator_ready, ffprobe_duration,
                       generate_narration, run)

# Trailing picture after the last word, so a cut never lands on a hard stop.
TAIL_S = 0.9
LOWER_THIRD_IN = 0.5
LOWER_THIRD_HOLD = 5.5
CARD_IN = 1.2
REFERENCE_IN = 6.0


@dataclass
class Shot:
    """One section of a guide: what is said, what is shown, what is specified."""

    key: str
    section: str                       # lower-third text
    step_label: str                    # spec-card eyebrow
    title: str                         # spec-card headline
    narration: list[str]
    prompt: str                        # b-roll prompt (atmospheric only)
    specs: list[str] = field(default_factory=list)
    citation: str | None = None
    # (authority, code, official title) — shown as its own standards plate when
    # the section is governed by a named regulation.
    reference: tuple[str, str, str] | None = None
    motion: bool = False               # render image-to-video instead of a push
    variant: int = 0                   # which cached still to use
    # Figures are excluded from b-roll by default; set where the shot is
    # about a hand or a stance and the frame is meaningless without one.
    people: bool = False


@dataclass
class TrainingScript:
    slug: str
    title: str
    subtitle: str
    shots: list[Shot]


def _still_segment(still: Path, seconds: float, dest: Path) -> Path:
    """Hold `still` for `seconds` with a slow centred push."""
    frames = max(int(seconds * FPS), 1)
    zoom = f"min(1.0+0.00030*on,1.09)"
    vf = (
        f"scale={WIDTH * 2}:-2,"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"setsar=1,format=yuv420p"
    )
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(still),
         "-frames:v", str(frames), "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(dest)])
    return dest


def _pingpong(clip: Path, dest: Path) -> Path:
    """Follow `clip` with its own reverse, so repeated loops have no jump cut."""
    run(["ffmpeg", "-y", "-i", str(clip), "-filter_complex",
         "[0:v]split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1[v]",
         "-map", "[v]", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(dest)])
    return dest


def _clip_segment(clip: Path, seconds: float, dest: Path) -> Path:
    """Fit a rendered motion clip to `seconds`, looping if it runs short.

    I2V returns a few seconds against narration that often runs ten times
    longer. A camera drift does not end where it began, so the clip is mirrored
    before looping — otherwise the picture jumps at every seam.
    """
    if ffprobe_duration(clip) < seconds:
        clip = _pingpong(clip, clip.with_name(f"{clip.stem}_pp.mp4"))
    run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(clip),
         "-t", f"{seconds:.2f}",
         "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1,format=yuv420p",
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         str(dest)])
    return dest


def _apply_overlays(base: Path, card: Path | None, lower: Path | None,
                    seconds: float, dest: Path,
                    reference: Path | None = None) -> Path:
    if not card and not lower and not reference:
        return base
    cmd = ["ffmpeg", "-y", "-i", str(base)]
    filters, stream = [], "[0:v]"
    idx = 1
    if lower:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.2f}",
                "-i", str(lower)]
        out_at = min(LOWER_THIRD_HOLD, max(seconds - 0.6, LOWER_THIRD_IN + 0.6))
        filters.append(
            f"[{idx}:v]format=rgba,"
            f"fade=t=in:st={LOWER_THIRD_IN}:d=0.4:alpha=1,"
            f"fade=t=out:st={out_at:.2f}:d=0.5:alpha=1[lt]")
        filters.append(f"{stream}[lt]overlay=0:0[withlt]")
        stream = "[withlt]"
        idx += 1
    if card:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.2f}",
                "-i", str(card)]
        filters.append(
            f"[{idx}:v]format=rgba,fade=t=in:st={CARD_IN}:d=0.5:alpha=1[cd]")
        filters.append(f"{stream}[cd]overlay=0:0[withcd]")
        stream = "[withcd]"
        idx += 1
    if reference:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.2f}",
                "-i", str(reference)]
        st = min(REFERENCE_IN, max(seconds - 4.0, CARD_IN + 0.5))
        filters.append(
            f"[{idx}:v]format=rgba,fade=t=in:st={st:.2f}:d=0.5:alpha=1[rf]")
        filters.append(f"{stream}[rf]overlay=0:0[withrf]")
        stream = "[withrf]"
    filters.append(f"{stream}format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[v]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(dest)]
    run(cmd)
    return dest


def _mux(video: Path, audio: Path, dest: Path) -> Path:
    run(["ffmpeg", "-y", "-i", str(video), "-i", str(audio),
         "-filter_complex", "[1:a]apad[a]",
         "-map", "0:v:0", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", str(dest)])
    return dest


def _concat(parts: list[Path], dest: Path) -> Path:
    cmd, graph = ["ffmpeg", "-y"], ""
    for i, p in enumerate(parts):
        cmd += ["-i", str(p)]
        graph += f"[{i}:v][{i}:a]"
    graph += f"concat=n={len(parts)}:v=1:a=1[v][a]"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(dest)]
    run(cmd)
    return dest


def _title_segment(script: TrainingScript, workdir: Path) -> Path:
    """Silent opening plate, held long enough to read."""
    png = cards.title_card(context.current().series_label, script.title,
                           script.subtitle,
                           workdir / "title.png")
    seconds = 4.0
    video = _still_segment(png, seconds, workdir / "title_v.mp4")
    silent = workdir / "title_silence.wav"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{seconds:.2f}", "-sample_fmt", "s16", str(silent)])
    return _mux(video, silent, workdir / "title.mp4")


def produce(script: TrainingScript, only: list[str] | None = None,
            verify_voice: bool = True) -> Path:
    """Build `script` end to end; returns the finished mp4.

    `only` restricts production to the named shot keys, which is how a single
    section gets cut for review without paying for the whole guide.
    """
    shots = [s for s in script.shots if not only or s.key in only]
    if not shots:
        raise RuntimeError(f"no shots matched {only}")

    workdir = OUT_ROOT / script.slug
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"[{script.slug}] stills for {len(shots)} shot(s)…")
    library = visuals.stills_for(
        [s.prompt for s in shots],
        people={s.prompt for s in shots if s.people})

    # After the stills pass, which may have stopped the voice service to free
    # VRAM. Restarting here also means a missing clip fails before any picture
    # work rather than at the first line of narration.
    ensure_narrator_ready()

    parts: list[Path] = []
    if not only:
        parts.append(_title_segment(script, workdir))

    report = []
    for shot in shots:
        print(f"[{script.slug}] {shot.key}: {shot.title}")
        wav = workdir / f"{shot.key}.wav"
        seconds = generate_narration(shot.narration, wav,
                                     verify=verify_voice) + TAIL_S
        print(f"  narration {seconds - TAIL_S:.1f}s")

        options = library[shot.prompt]
        still = options[min(shot.variant, len(options) - 1)]
        if shot.motion:
            clip = visuals.animate(still, shot.prompt,
                                   workdir / f"{shot.key}_motion.mp4")
            base = (_clip_segment(clip, seconds, workdir / f"{shot.key}_base.mp4")
                    if clip.suffix == ".mp4"
                    else _still_segment(clip, seconds, workdir / f"{shot.key}_base.mp4"))
        else:
            base = _still_segment(still, seconds, workdir / f"{shot.key}_base.mp4")

        card = cards.spec_card(shot.step_label, shot.title, shot.specs,
                               shot.citation, workdir / f"{shot.key}_card.png") \
            if shot.specs else None
        lower = cards.lower_third(shot.section, workdir / f"{shot.key}_lt.png") \
            if shot.section else None
        reference = cards.reference_card(*shot.reference,
                                         workdir / f"{shot.key}_ref.png") \
            if shot.reference else None
        dressed = _apply_overlays(base, card, lower, seconds,
                                  workdir / f"{shot.key}_dressed.mp4",
                                  reference=reference)
        parts.append(_mux(dressed, wav, workdir / f"{shot.key}.mp4"))
        report.append({"key": shot.key, "title": shot.title,
                       "seconds": round(seconds, 2), "still": str(still)})

    suffix = "_" + "-".join(s.key for s in shots) if only else ""
    final = workdir / f"{script.slug}{suffix}.mp4"
    _concat(parts, final)
    duration = ffprobe_duration(final)
    (workdir / f"report{suffix}.json").write_text(json.dumps(
        {"final": str(final), "duration_s": round(duration, 2),
         "shots": report}, indent=2))
    print(f"[{script.slug}] DONE -> {final}  ({duration:.1f}s)")
    return final
