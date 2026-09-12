#!/usr/bin/env python3
"""Launch-video motion plates: a Z-Image keyframe per statement, then Wan 2.2
14B image-to-video on each. Text-free, centre kept clear; typography goes on
in the editor.

    plates.py run        # keyframes → wait → queue every I2V pass → wait → manifest
    plates.py status     # read the manifest and print where each plate stands

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
W, H = 1280, 704                      # 16:9-ish, 32-aligned, under Wan's 1.0 MP area
I2V = dict(model="wan22-14b-i2v", width=W, height=H, duration_frames=81, fps=16,
           num_inference_steps=25, prompt_style="cinematic", enhance_prompt=False,
           negative_prompt="text, letters, watermark, logo, blurry, low quality, jitter, flicker, morphing")
STYLE = ("cyberpunk synthwave key art, magenta and electric-cyan palette on near-black, chrome highlights, "
         "grid horizon, scanlines, volumetric fog, 1984 retro-future, high contrast, cinematic lighting, "
         "wide 16:9, the centre of the frame left empty for a title, no text")

PLATES = [
  # id, statement (for the editor), keyframe prompt or existing image, motion prompt
  ("00_intro", "—", COVERS / "01_aardvark_rooftop.png",
   "slow push-in toward the striped sun, rain falling, fog drifting across the rooftop, the sun stripes shimmer, the aardvark breathes, neon reflections ripple on wet tiles"),
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
   "the screens orbit slowly around the empty centre, each flickers with light, the chip glows and pulses, camera drifts sideways"),
  ("07_film_crew", "A FILM CREW THAT NEVER SLEEPS.",
   f"an empty neon-lit film set at night, a clapperboard and two director chairs at the edges, tungsten and magenta lights on stands, haze in the air, {STYLE}",
   "haze drifts through the light beams, the set lights flicker on one by one, a slow dolly along the set, the clapperboard glints"),
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
   "the cursor blinks, the horizon grid scrolls toward the camera, the distant sun stripes shimmer, subtle rain"),
  ("12_loop_grid", "(generic loop A)",
   f"pure synthwave landscape, an endless neon grid to the horizon under a striped chrome sun, nothing else, {STYLE}",
   "endless smooth dolly forward over the grid, the sun stripes shimmer, stars drift, no cuts"),
  ("13_loop_rain", "(generic loop B)",
   f"abstract close-up of rain on wet black asphalt at night with magenta and cyan neon reflections, bokeh, no objects, {STYLE}",
   "rain falls and ripples the reflections, bokeh lights drift, slow slide sideways"),
  ("14_outro", "—", COVERS / "02_terminal_icons.png",
   "code scrolls on the terminal screen, the laser beam streams toward the icon panel with particles flowing along it, the icons pulse in sequence, city lights flicker"),
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
    kf = post("/api/batch-image/generate/prompts",
              {"prompts": [p for _, _, p, _ in need_kf], "model": "zimage-turbo", "width": W, "height": H,
               "style": "artistic", "negative_prompt": "text, letters, watermark, logo, blurry, cluttered centre"})
    kf_id = kf["batch_id"]; manifest["keyframe_batch"] = kf_id; log(f"keyframes queued: {kf_id} ({len(need_kf)} prompts)")
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
    # queue every image-to-video pass; the worker drains them one at a time
    for pid, rec in manifest["plates"].items():
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


def status():
    m = json.loads(MANIFEST.read_text())
    for pid, r in m["plates"].items():
        print(f"{pid:18s} {r.get('status','queued'):10s} {r.get('video') or r.get('video_error') or r.get('video_batch','-')}")


if __name__ == "__main__":
    {"run": run, "status": status}[sys.argv[1] if len(sys.argv) > 1 else "status"]()
