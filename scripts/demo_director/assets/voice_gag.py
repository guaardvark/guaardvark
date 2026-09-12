#!/usr/bin/env python3
"""One sentence, one voice per word, the creator's clone on the last words.

    "I — CAN — USE — ANY — VOICE — EVEN — OUR — CREATOR'S — VOICE"

Each word is synthesised on its own through Audio Foundry (Kokoro voices by
id; Chatterbox with a consented reference clip for the creator's words), then
butted together with a short crossfade so the cut is audible and lands on the
beat. Writes the WAV and a JSON of word start/end times for on-screen sync.

Usage (Audio Foundry plugin running, GPU free):
  voice_gag.py --ref /abs/path/creator_ref.wav --out gag.wav
  voice_gag.py --words "I,CAN,USE,ANY,VOICE,EVEN,OUR,CREATOR'S,VOICE" --gap-ms 60

The reference clip must have gone through /api/audio-foundry/voice-clips/upload
(that is what records consent); an arbitrary path is refused with 403.
"""
from __future__ import annotations

import argparse, json, os, subprocess, sys, tempfile, time, wave
from pathlib import Path
import requests

BACKEND = os.environ.get("GUAARDVARK_URL", "http://127.0.0.1:5000").rstrip("/")
DEFAULT_WORDS = ["I", "CAN", "USE", "ANY", "VOICE", "EVEN", "OUR", "CREATOR'S", "VOICE"]
# One Kokoro voice per word, chosen for maximum contrast between neighbours.
DEFAULT_VOICES = ["af_heart", "bm_george", "af_nova", "am_fenrir", "bf_emma", "em_alex", "am_puck"]
CREATOR_WORDS = 2  # the last N words use the creator's clone, or the robot voice with --robot
# ffmpeg "robot": ring-modulated spectrum plus a short metallic echo; reads as a vocoder.
ROBOT_FILTER = ("afftfilt=real='hypot(re,im)*cos(2*3.14159*0.9*t)':imag='hypot(re,im)*sin(2*3.14159*0.9*t)',"
                "aecho=0.8:0.6:8:0.3,highpass=f=120,lowpass=f=6000")


def tts(text: str, *, voice_id: str | None, ref: str | None, out: Path, timeout=300) -> dict:
    body = {"text": text, "output_format": "wav", "async": False}
    if ref:
        body.update({"backend": "chatterbox", "reference_clip_path": ref, "emotion": "confident"})
    else:
        body.update({"backend": "kokoro", "voice_id": voice_id})
    r = requests.post(f"{BACKEND}/api/audio-foundry/generate/voice", json=body, timeout=timeout)
    if r.status_code >= 400:
        raise SystemExit(f"{text!r} via {voice_id or 'clone'}: HTTP {r.status_code} {r.text[:200]}")
    d = r.json(); d = d.get("data", d)
    if d.get("job_id"):  # long text path; poll
        while True:
            time.sleep(1.0)
            j = requests.get(f"{BACKEND}/api/audio-foundry/jobs/{d['job_id']}", timeout=30).json()
            j = j.get("data", j)
            if j.get("status") in ("done", "completed"):
                d = j; break
            if j.get("status") in ("error", "failed"):
                raise SystemExit(f"{text!r}: {j.get('error')}")
    src = Path(d["path"])
    out.write_bytes(src.read_bytes())
    return {"engine": d.get("engine") or ("chatterbox" if ref else "kokoro"), "path": str(src)}


def duration_s(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", default=",".join(DEFAULT_WORDS))
    ap.add_argument("--voices", default=",".join(DEFAULT_VOICES), help="Kokoro ids for the non-creator words, in order")
    ap.add_argument("--ref", help="creator reference clip path (from the consent-gated upload)")
    ap.add_argument("--robot", action="store_true",
                    help="no clone: the last words are a Kokoro voice run through the robot filter")
    ap.add_argument("--robot-voice", default="am_onyx", help="Kokoro voice under the robot filter")
    ap.add_argument("--creator-words", type=int, default=CREATOR_WORDS)
    ap.add_argument("--gap-ms", type=int, default=40, help="crossfade between words")
    ap.add_argument("--out", default="voice_gag.wav")
    a = ap.parse_args()
    words = [w.strip() for w in a.words.split(",") if w.strip()]
    voices = [v.strip() for v in a.voices.split(",") if v.strip()]
    n_plain = len(words) - (a.creator_words if (a.ref or a.robot) else 0)
    if len(voices) < n_plain:
        raise SystemExit(f"need {n_plain} voices for the non-creator words, got {len(voices)}")
    tmp = Path(tempfile.mkdtemp(prefix="voice_gag_"))
    parts, timings, t = [], [], 0.0
    for i, w in enumerate(words):
        tail = i >= n_plain
        use_ref = bool(a.ref) and tail
        use_robot = a.robot and tail and not a.ref
        p = tmp / f"{i:02d}.wav"
        info = tts(w, voice_id=a.robot_voice if use_robot else (None if use_ref else voices[i]),
                   ref=a.ref if use_ref else None, out=p)
        if use_robot:
            raw = tmp / f"{i:02d}_raw.wav"; p.rename(raw)
            subprocess.run(["ffmpeg", "-y", "-i", str(raw), "-af", ROBOT_FILTER, str(p)],
                           check=True, capture_output=True)
        d = duration_s(p)
        timings.append({"word": w, "voice": "creator" if use_ref else ("robot" if use_robot else voices[i]),
                        "engine": info["engine"], "start": round(t, 3), "end": round(t + d, 3)})
        t += d - (a.gap_ms / 1000.0 if parts else 0)
        parts.append(p)
        print(f"  {w:10s} {timings[-1]['voice']:10s} {d:.2f}s")
    # ffmpeg: chain acrossfade filters
    inputs = sum([["-i", str(p)] for p in parts], [])
    if len(parts) == 1:
        subprocess.run(["ffmpeg", "-y", *inputs, a.out], check=True, capture_output=True)
    else:
        fc, prev = [], "[0:a]"
        for i in range(1, len(parts)):
            lab = f"[x{i}]" if i < len(parts) - 1 else "[out]"
            fc.append(f"{prev}[{i}:a]acrossfade=d={a.gap_ms/1000:.3f}:c1=tri:c2=tri{lab}")
            prev = lab
        subprocess.run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc), "-map", "[out]", a.out],
                       check=True, capture_output=True)
    Path(a.out).with_suffix(".json").write_text(json.dumps(timings, indent=1))
    print(f"wrote {a.out} ({duration_s(Path(a.out)):.2f}s) and {Path(a.out).with_suffix('.json')}")


if __name__ == "__main__":
    main()
