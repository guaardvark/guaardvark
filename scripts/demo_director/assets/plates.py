#!/usr/bin/env python3
"""Launch-video motion plates: a Z-Image keyframe per statement, then Wan 2.2
14B image-to-video on each. Text-free, centre kept clear; typography goes on
in the editor.

    plates.py run        # keyframes → wait → queue every I2V pass → wait → manifest
    plates.py status     # read the manifest and print where each plate stands
    plates.py upscale    # after run: 2x every finished clip through the upscaling plugin

Everything goes through the backend's own routes (the same queue the Studio
uses), so the GPU stays arbitrated and the Studio shows the jobs. Runs for
hours at HD; start it under nohup and read plates.log.
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path
import requests

B = os.environ.get("GUAARDVARK_URL", "http://127.0.0.1:5000").rstrip("/")
REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "data/demo_assets/launch/plates"
COVERS = REPO / "data/demo_assets/launch/covers"
MANIFEST = OUT / "manifest.json"
# 960x544: 16:9, 32-aligned, 522k px. The 1.0 MP ceiling (1312x736) made the 14B GGUF load
# only ~6 GB of its 10.5 GB on a 16 GB card ("loaded partially", ~4.5 GB offloaded to CPU) and one
# plate took 43 minutes. 2x upscale afterwards gives 1920x1088, a 4 px crop from 1080p.
W, H = int(os.environ.get("PLATES_W", 960)), int(os.environ.get("PLATES_H", 544))
PROFILE = os.environ.get("PLATES_PROFILE", "standard")   # "lightx2v-4" once the i2v Lightning LoRAs are installed
UPSCALE = dict(model="realesrgan-x2", scale=2, two_pass=False, suffix="_2x")   # 1920x1088 from 960x544; 4 px crop to 1080p
I2V = dict(model="wan22-14b-i2v", width=W, height=H, duration_frames=81, fps=16,
           num_inference_steps=int(os.environ.get("PLATES_STEPS", 25)), speed_profile=PROFILE,
           interpolation_multiplier=1, upscale=False,   # Draft tier: raw frames; upscaling is batched afterwards
           prompt_style="cinematic", enhance_prompt=False,
           negative_prompt="text, letters, watermark, logo, blurry, low quality, jitter, flicker, morphing, scanlines, VHS, tracking lines, static, noise, film grain, interlacing, chromatic aberration, glitch, people, person, figure, character, crowd")
STYLE = ("cyberpunk synthwave key art, magenta and electric-cyan palette on near-black, chrome highlights, "
         "grid horizon, volumetric fog, clean sharp render, high contrast, cinematic lighting, "
         "wide 16:9, the centre of the frame is empty negative space, no text, no letters, no typography, no signage")

PLATES = [
  # id, statement (for the editor), keyframe prompt or existing image, motion prompt
  ("00_intro", "—", COVERS / "01_aardvark_rooftop.png",
   "slow push-in toward the striped sun, rain falling, fog drifting across the rooftop, the sun stripes glow steadily, the aardvark breathes, neon reflections ripple on wet tiles"),
  ("01_one_machine", "ONE MACHINE. NO CLOUD.",
   f"a single dark computer tower standing alone at the bottom edge of an endless neon grid plain, storm clouds parting above, one thin beam of cyan light from the tower, {STYLE}",
   "storm clouds pull back and dissolve, the grid lights up in waves toward the horizon, the cyan beam pulses, slow drift forward"),
  ("02_fifteen_skills", "FIFTEEN SKILLS. ONE SENTENCE.",
   f"a long chrome corridor of glowing doorways receding to a vanishing point, fifteen doors, alternating magenta and cyan light spilling from each, wet floor reflections, {STYLE}",
   "steady dolly forward down the corridor, the doorways light up one after another as the camera passes, reflections slide on the wet floor"),
  ("03_your_gpu", "YOUR GPU. YOUR STUDIO.", COVERS / "03_reactor_gpu.png",
   "steam curls upward, the cyan veins pulse with light like a heartbeat, slow orbit around the card, rain droplets glint"),
  ("04_asks_first", "IT ASKS BEFORE IT SPENDS.",
   f"a neon barrier gate across a rain-soaked road at night, a tall beacon light on a pole glowing red, empty road ahead, chrome rails, {STYLE}",
   "the beacon pulses red then turns green, the barrier begins to lift, rain streaks, slow tilt up toward the beacon"),
  ("05_nothing_leaves", "NOTHING LEAVES THE BOX.",
   f"a black glass cube floating above a neon grid, streams of cyan and magenta light swirling inside the cube and never escaping, dark void around it, {STYLE}",
   "the light streams swirl and bounce inside the cube, the cube rotates slowly, the grid scrolls beneath it, particles drift"),
  ("06_eleven_models", "ELEVEN VIDEO MODELS. ONE CARD.",
   f"eleven small floating film-reel screens orbiting an empty centre above a glowing chip on a dark grid, each screen a different neon tint, {STYLE}",
   "the screens orbit slowly around the empty centre, each glows and shifts colour, the chip glows and pulses, camera drifts sideways"),
  ("07_film_crew", "A FILM CREW THAT NEVER SLEEPS.",
   f"an empty neon-lit film set at night, a clapperboard and two director chairs at the edges, tungsten and magenta lights on stands, haze in the air, {STYLE}",
   "haze drifts through the light beams, the set lights switch on one by one, a slow dolly along the set, the clapperboard glints"),
  ("08_any_voice", "ANY VOICE. WITH CONSENT.",
   f"a chrome studio microphone at the left edge of frame, concentric neon waveform rings radiating across a dark grid, empty centre, {STYLE}",
   "the waveform rings ripple outward in time, the microphone glints, light pulses along the rings, gentle camera push"),
  ("09_train_a_face", "TRAIN A FACE. KEEP IT.",
   f"a wireframe human head made of glowing cyan threads being woven by strands of magenta light, off-centre to the right, dark grid, {STYLE}",
   "threads of light weave across the head, the wireframe rotates slowly, loose strands drift and connect, sparks travel along the lines"),
  ("10_twenty_agents", "TWENTY AGENTS. ONE REPO.",
   f"twenty small glowing terminal windows arranged in a grid floating over a neon plain, thin lines of light converging from each toward a single point at the bottom, {STYLE}",
   "cursors blink in the terminals, the lines of light stream toward the convergence point, the grid scrolls, slow push-in"),
  ("11_two_lines", "TWO LINES. NO CLONE.",
   f"a lone blinking terminal cursor low in the frame on black, a thin neon horizon line with a striped chrome sun far away, vast empty space, {STYLE}",
   "the cursor blinks, the horizon grid scrolls toward the camera, the distant sun stripes glow, subtle rain"),
  ("12_loop_grid", "(generic loop A)",
   f"pure synthwave landscape, an endless neon grid to the horizon under a striped chrome sun, nothing else, {STYLE}",
   "endless smooth dolly forward over the grid, the sun stripes glow, stars drift, no cuts"),
  ("13_loop_rain", "(generic loop B)",
   f"abstract close-up of rain on wet black asphalt at night with magenta and cyan neon reflections, bokeh, no objects, {STYLE}",
   "rain falls and ripples the reflections, bokeh lights drift, slow slide sideways"),
  ("14_outro", "—", COVERS / "02_terminal_icons.png",
   "code scrolls on the terminal screen, the laser beam streams toward the icon panel with particles flowing along it, the icons pulse in sequence, city lights glow"),
]


def post(path, body, timeout=60):
    r = requests.post(B + path, json=body, timeout=timeout); r.raise_for_status()
    d = r.json(); return d.get("data", d)


def get(path, timeout=30):
    r = requests.get(B + path, timeout=timeout); r.raise_for_status()
    d = r.json(); return d.get("data", d)


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(OUT / "plates.log", "a") as fh: fh.write(line + "\n")


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"plates": {}, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    need_kf = [(pid, st, prompt, motion) for pid, st, prompt, motion in PLATES if isinstance(prompt, str)]
    kf_id = os.environ.get("PLATES_KEYFRAME_BATCH")   # reuse a finished keyframe batch instead of rendering again
    if kf_id:
        log(f"keyframes reused: {kf_id}")
    else:
        kf = post("/api/batch-image/generate/prompts",
                  {"prompts": [p for _, _, p, _ in need_kf], "model": "zimage-turbo", "width": W, "height": H,
                   "style": "artistic", "negative_prompt": "text, letters, words, typography, title, caption, signage, watermark, logo, blurry, cluttered centre, scanlines, VHS, tracking lines, static, noise, film grain, glitch"})
        kf_id = kf["batch_id"]; log(f"keyframes queued: {kf_id} ({len(need_kf)} prompts)")
    manifest["keyframe_batch"] = kf_id
    while True:
        s = get(f"/api/batch-image/status/{kf_id}?include_results=true")
        if s["status"] in ("completed", "error", "cancelled"): break
        time.sleep(15)
    log(f"keyframes {s['status']}: {s.get('completed_images')}/{s.get('total_images')}")
    by_prompt = {r["prompt_id"]: r for r in s.get("results", [])}
    for i, (pid, st, prompt, motion) in enumerate(need_kf, start=1):
        r = by_prompt.get(f"prompt_{i}")
        if not (r and r.get("success") and r.get("image_path")):
            log(f"  {pid}: keyframe FAILED: {(r or {}).get('error')}"); continue
        manifest["plates"][pid] = {"statement": st, "keyframe": r["image_path"], "motion": motion}
    for pid, st, img, motion in PLATES:
        if isinstance(img, Path):
            manifest["plates"][pid] = {"statement": st, "keyframe": str(img), "motion": motion}
    # Let the image pipeline leave the card before the first video render, or the
    # 14B loads with ~1 GB usable and runs from CPU (seen 2026-09-12: keyframes
    # finished 10:08:57, the render started the same second, 9.6 GB offloaded).
    for _ in range(12):
        try:
            g = get("/api/gpu/memory/status")
            busy = [m for m in g.get("models", []) if m.get("model_type") == "image_batch" and m.get("in_use")]
            if not busy:
                break
        except Exception:
            break
        time.sleep(10)
    time.sleep(45)
    # queue every image-to-video pass; the worker drains them one at a time.
    # PLATES_ONLY=a,b limits the run (a probe plate first); PLATES_SKIP=a,b leaves finished ones out.
    only = {x for x in os.environ.get("PLATES_ONLY", "").split(",") if x}
    skip = {x for x in os.environ.get("PLATES_SKIP", "").split(",") if x}
    for pid, rec in list(manifest["plates"].items()):
        if (only and pid not in only) or pid in skip:
            del manifest["plates"][pid]; continue
        try:
            v = post("/api/batch-video/generate/image",
                     {"image_paths": [rec["keyframe"]], "prompt": rec["motion"], "seed": 1984, **I2V}, timeout=120)
            rec["video_batch"] = v["batch_id"]; log(f"  {pid}: i2v queued {v['batch_id']}")
        except requests.HTTPError as e:
            rec["error"] = e.response.text[:300]; log(f"  {pid}: i2v REFUSED {rec['error']}")
        MANIFEST.write_text(json.dumps(manifest, indent=1))
        time.sleep(2)
    # wait for all
    pending = {pid for pid, r in manifest["plates"].items() if r.get("video_batch")}
    while pending:
        time.sleep(60)
        for pid in sorted(pending):
            rec = manifest["plates"][pid]
            s = get(f"/api/batch-video/status/{rec['video_batch']}")
            if s["status"] in ("completed", "error", "cancelled"):
                ok = [r for r in s.get("results", []) if r.get("success") and r.get("video_path")]
                rec["status"] = s["status"]
                rec["video"] = f"/api/batch-video/video/{rec['video_batch']}/{ok[0]['video_path']}" if ok else None
                rec["video_error"] = None if ok else (s.get("error") or (s.get("results") or [{}])[0].get("error"))
                log(f"  {pid}: {s['status']} {rec['video'] or rec['video_error']}")
                pending.discard(pid)
        MANIFEST.write_text(json.dumps(manifest, indent=1))
    manifest["finished"] = time.strftime("%Y-%m-%d %H:%M:%S"); MANIFEST.write_text(json.dumps(manifest, indent=1))
    log("ALL PLATES DONE")


def upscale():
    """After the plates finish: send every finished clip through the upscaling plugin (2x),
    one job each, and record the output paths. Needs the upscaling plugin running."""
    m = json.loads(MANIFEST.read_text())
    for pid, r in m["plates"].items():
        if not r.get("video") or r.get("upscale_job"):
            continue
        s = get(f"/api/batch-video/status/{r['video_batch']}")
        ok = [x for x in s.get("results", []) if x.get("success") and x.get("video_path")]
        if not ok:
            continue
        src = Path(s.get("output_dir") or "") / ok[0]["video_path"]
        try:
            j = post("/api/upscaling/upscale/video", {"input_path": str(src), **UPSCALE}, timeout=60)
            r["upscale_job"] = j.get("job_id") or j.get("id"); r["upscale_input"] = str(src)
            log(f"  {pid}: upscale queued {r['upscale_job']} ← {src.name}")
        except requests.HTTPError as e:
            r["upscale_error"] = e.response.text[:200]; log(f"  {pid}: upscale REFUSED {r['upscale_error']}")
        MANIFEST.write_text(json.dumps(m, indent=1))
    log("upscale jobs queued; poll GET /api/upscaling/jobs")


def status():
    m = json.loads(MANIFEST.read_text())
    for pid, r in m["plates"].items():
        print(f"{pid:18s} {r.get('status','queued'):10s} {r.get('video') or r.get('video_error') or r.get('video_batch','-')}")


if __name__ == "__main__":
    {"run": run, "status": status, "upscale": upscale}[sys.argv[1] if len(sys.argv) > 1 else "status"]()
