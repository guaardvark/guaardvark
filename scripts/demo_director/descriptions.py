#!/usr/bin/env python3
"""YouTube description generation for the 12-episode walkthrough series.

Content is drawn directly from scripts/demo_director/SERIES.md (hooks, beats,
on-screen text) — no invented facts. Chapter timestamps are placeholders
except 0:00: real cut points don't exist until each episode is edited, so
fabricating them would be dishonest metadata. Fill them in during the edit
pass, before upload.

Cross-episode links use {EP01}..{EP12} / {PLAYLIST} placeholders since video
IDs don't exist until upload. Run `resolve` after uploading to substitute
real watch URLs.

Usage:
  descriptions.py build             write draft .txt files (with placeholders)
  descriptions.py resolve MAP.json  substitute {EPxx}/{PLAYLIST} with real
                                     URLs from a {"1": "videoId", ...} map,
                                     writing *_final.txt
"""

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "data/demo_assets/descriptions"

GITHUB = "https://github.com/guaardvark/guaardvark"
MANTRA = "One machine. No cloud."

WHAT_IS = (
    "Guaardvark is a self-hosted AI system that writes, voices, shoots, and "
    "edits video — chat, image gen, video gen, voice cloning, an autonomous "
    "screen agent, and a full AI film crew — running on a single desktop GPU, "
    "no cloud API calls. This is one of 12 walkthrough episodes."
)

TAGS_COMMON = ["Guaardvark", "local AI", "self-hosted AI", "open source AI",
               "offline AI", "AI video generation", "one machine no cloud"]

# Real cut points measured (ffprobe) from the actual EP0N_FINAL.mp4 renders —
# not the finer-grained SERIES.md beat plan, which the production consolidated
# during editing. Overrides the placeholder chapters below for these episodes.
REAL_CHAPTERS = {
    5: [
        (0, "Hook — the Media Director"),
        (27, "The wall — browsing the pre-rendered batch"),
        (52, "Model registry & downloads"),
        (68, "Infographic & anatomy fixes"),
        (88, "Upscaling to 8K"),
        (99, "Closer — auto-filing into Files"),
    ],
    6: [
        (0, "Hook — eleven video models, one GPU, one of them talks"),
        (22, "Preset tour — Quality, Duration, Motion, Aspect"),
        (43, "Live render — Queued → Storyboard → Director → Generating"),
        (72, "Draft-tier render"),
        (93, "Cinema-tier render — 2× RIFE + 2× ESRGAN"),
        (111, "Advanced Editor — one click into ComfyUI"),
    ],
    7: [
        (0, "Hook — the 13-second reference clip"),
        (39, "Consent required — 403 on camera"),
        (56, "The clone — voices compared side by side"),
        (84, "Self-check — it listens to itself first"),
        (105, "Music generation — style chips to instrumental"),
        (155, "The finished track"),
        (176, "FX Lab — sound effects from text"),
        (193, "Closer — auto-filing into Files"),
    ],
    8: [
        (0, "Hook — drop in an MP3"),
        (24, "Energy arc — tempo & beats read automatically"),
        (44, "The plan — cost gate & per-cut shots"),
        (70, "The finished music video"),
        (97, "Closer — what's next"),
    ],
}

EPISODES = {
    1: dict(
        title="Meet Guaardvark — Full Tour",
        keyword="full tour",
        hook=(
            "This is Guaardvark. It writes films, clones voices, edits video, "
            "trains characters, and fixes its own code — every bit of it on "
            "one desktop GPU, in one room, with the network cable out."
        ),
        body=(
            "Episode 1 is the whole system in four minutes: the eleven-step "
            "install, the dashboard, the sidebar tour across all four feature "
            "groups, theme switching, the plugin/GPU budget view, and a "
            "closing shot of nvidia-smi running with the cable unplugged. "
            "Everything you see here gets its own dedicated episode next."
        ),
        chapters=[
            "Cold open — one machine, no cloud",
            "Install — eleven steps, one command",
            "Dashboard",
            "Sidebar tour — Main / Studio / Management / Configuration",
            "Theme flip — Settings ▸ Appearance",
            "Plugins & GPU budget",
            "Closer — unplug the cable",
        ],
        links=[2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        tags=["AI system tour", "self-hosted AI platform"],
    ),
    2: dict(
        title="A Brain With Three Speeds — Chat Brain",
        keyword="chat brain",
        hook=(
            "Ask it to play a song — it answers in under a hundred "
            "milliseconds, zero AI calls. Ask it to research something and "
            "click through — it thinks in steps you can watch. Same chat box. "
            "Three different brains."
        ),
        body=(
            "Episode 2 walks the three-tier chat architecture: instant reflex "
            "commands, single-call instinct answers, and multi-step "
            "deliberation with a live thinking trail — including an honesty "
            "beat where the tool selector correctly declines to fire image "
            "generation on an ambiguous request. Also covers slash commands, "
            "inline /imagine, the floating chat window, the narrate button, "
            "and Lesson Pearls."
        ),
        chapters=[
            "One chat box, three brains",
            "Tier 1 — Reflex (<100ms, zero LLM calls)",
            "Tier 2 — Instinct",
            "Tier 3 — Deliberation (honesty beat)",
            "Slash commands",
            "/imagine — inline image generation",
            "Floating chat follows you everywhere",
            "Narrate button",
            "Lesson Pearls",
            "Closer — where do files live?",
        ],
        links=[5, 7, 3],
        tags=["local LLM chat", "AI agent tiers", "tool calling"],
    ),
    3: dict(
        title="Your Files Have a Desktop — File Desktop",
        keyword="file desktop",
        hook=(
            "This is a browser tab. Those are folder windows — draggable, "
            "resizable, snap to grid. Your files didn't move to the cloud. "
            "The desktop moved into Guaardvark."
        ),
        body=(
            "Episode 3 covers the in-browser file desktop: drag/resize/fold "
            "folder windows, bulk folder-tree import, in-app viewers for PDF/"
            "DOCX/CSV/audio, the opt-in media gallery, folder-to-entity "
            "linking, and RAG indexing with a live retrieval test — an "
            "honesty beat that shows the actual retrieved chunks and scores "
            "instead of just asserting an answer."
        ),
        chapters=[
            "Your files get a desktop",
            "Drag, resize, snap — folder windows",
            "Bulk import — whole folder trees",
            "In-app viewers — PDF, DOCX, CSV, audio",
            "Media gallery (opt-in)",
            "Folder Properties & entity links",
            "RAG — show your work",
            "Repo intelligence — dependency graph",
            "Closer — who tunes retrieval? Episode 11",
        ],
        links=[11],
        tags=["RAG", "local file management", "retrieval augmented generation"],
    ),
    4: dict(
        title="The Agent Behind the Glass — Screen Agent",
        keyword="screen agent",
        hook=(
            "The hardest thing we ever built is a mouse. This agent has its "
            "own desktop, its own eyes, its own hands. Watch it work, miss, "
            "and recover — because that's what real autonomy looks like."
        ),
        body=(
            "Episode 4 is the only vision-driven episode in the series: a "
            "screen agent with its own virtual desktop, SEE-THINK-ACT-VERIFY "
            "loop, click-correction via Servo, deterministic recipes, "
            "learn-by-demonstration, and a three-stage autonomy ladder "
            "(Guided → Supervised → Autonomous). The series' central "
            "honesty beat: a missed click, caught and retried, on camera — "
            "plus the system's own published mean miss distance."
        ),
        chapters=[
            "The hardest thing we built is a mouse",
            "The glass — its own desktop, eyes, hands",
            "/agent mode",
            "SEE → THINK → ACT → VERIFY",
            "Servo — approach, observe, correct",
            "A miss, on camera (honesty beat)",
            "Recipes — deterministic shortcuts",
            "Learn by demonstration",
            "Apprentice — Guided → Supervised → Autonomous",
            "Eye bake-off — we publish our own miss distance",
            "Closer — next, it makes movies",
        ],
        links=[6],
        tags=["computer use agent", "AI screen automation", "vision agent"],
    ),
    5: dict(
        title="A Million Pictures, One Prompt — Image Gen",
        keyword="image gen",
        hook=(
            "One concept in. The Media Director — an LLM art director — "
            "writes a dozen distinct, connected prompts. Not the same image "
            "with different seeds. Different images that belong together."
        ),
        body=(
            "Episode 5 covers offline image generation end to end: the Media "
            "Director expanding one concept into a connected prompt set, a "
            "live turbo-model batch, browsing a large pre-rendered batch, the "
            "model registry with live download speed, one-click infographics, "
            "anatomy/face-restore fixes with a VRAM calibration honesty beat, "
            "natural-language Kontext edits, and upscaling to 8K."
        ),
        chapters=[
            "One concept in, a dozen connected prompts out",
            "Media Director — an LLM art director",
            "Live batch — four images, turbo model",
            "The wall — the pre-rendered batch",
            "Model registry & downloads",
            "Fix Anatomy / face restore",
            "Kontext — natural-language image edit",
            "Upscaling to 8K",
            "Auto-filing into Files",
            "Closer — make them move (Ep 6), give them a face (Ep 9)",
        ],
        links=[6, 9, 3],
        tags=["AI image generation", "text to image", "Z-Image", "FLUX"],
    ),
    6: dict(
        title="Hollywood on One GPU — Video Gen",
        keyword="video gen",
        hook=(
            "Eleven video models across five families. Wan. CogVideoX. LTX. "
            "HunyuanVideo. MiniMax H3, which renders picture and its own "
            "soundtrack in one pass — on the same card that ran your chat in "
            "episode two."
        ),
        body=(
            "Episode 6 tours video generation: eleven backend models behind "
            "one interface, the quality/duration/motion/aspect preset system, "
            "instant prompt styles, a live Fast-tier render with staged "
            "progress, the gpu_wait queue (renders wait for the card instead "
            "of failing), a Draft-vs-Cinema comparison, and one-click access "
            "to the underlying ComfyUI graph."
        ),
        chapters=[
            "Eleven video models, one GPU",
            "Model menu — Wan, CogVideoX, LTX, Hunyuan, MiniMax H3",
            "Preset tour — Quality, Duration, Motion, Aspect",
            "Prompt styles — Cinematic, Anime, Claymation, Ghibli",
            "Live render — Queued → Storyboard → Director → Keyframe → Generating → Post",
            "Renders wait for the card, not fail",
            "Draft vs Cinema (2× RIFE + 2× ESRGAN)",
            "Advanced Editor — one click into ComfyUI",
            "Closer — next, a beat-synced music video",
        ],
        links=[8, 2],
        tags=["AI video generation", "text to video", "image to video", "ComfyUI"],
    ),
    7: dict(
        title="The Voice Foundry — Voice Clone",
        keyword="voice clone",
        hook=(
            "The narrator of this series isn't a person. She started as a "
            "tiny local text-to-speech model, then this feature cloned her "
            "into the voice you're hearing right now — including the part "
            "where the system demanded consent before it would speak a word."
        ),
        body=(
            "Episode 7 is the true story of how this series got its "
            "narrator: a 13-second phonetic reference clip, a consent flow "
            "that returns a hard 403 on an unconsented voice path, a "
            "three-way clone comparison, whisper-based self-checking that "
            "rejects a babbled take before it ever reaches you, and the "
            "session that generated the series' own music bed and sound "
            "effects."
        ),
        chapters=[
            "The narrator isn't a person",
            "The 13-second reference clip",
            "Consent required — 403 on camera",
            "The clone — three voices compared",
            "Self-check — it listens to itself first",
            "Music — instrumental generation",
            "FX Lab — sound effects from text",
            "Auto-filing into Files",
            "Closer — one song, one video, made from each other",
        ],
        links=[3, 8],
        tags=["voice cloning", "text to speech", "AI music generation"],
    ),
    8: dict(
        title="Drop a Song, Get a Music Video — Music Video",
        keyword="music video",
        hook=(
            "Drop in an MP3. The system reads its tempo, its beats, its "
            "energy — then an AI director writes a different shot for every "
            "cut. Watch the arc."
        ),
        body=(
            "Episode 8 turns one audio track into a full music video: tempo/"
            "beat/energy analysis, a per-cut shot plan where every prompt is "
            "genuinely different, a cost-approval gate before any GPU spend, "
            "a live generation launch, and the finished cuts landing on the "
            "beat with the energy arc overlaid."
        ),
        chapters=[
            "Drop in an MP3",
            "Three inputs — song, style, narrative",
            "Energy arc — tempo & beats read automatically",
            "The plan — a different shot per cut",
            "Cost gate — approve before any GPU spend",
            "Generation — live launch & stage progress",
            "The video — cuts landing on beats",
            "Closer — now imagine five crew members and a script",
        ],
        links=[9],
        tags=["AI music video", "beat sync video", "audio reactive video"],
    ),
    9: dict(
        title="A Film Crew That Never Sleeps — Film Crew",
        keyword="film crew",
        hook=(
            "Screenwriter. Casting director. Cinematographer. Storyboard "
            "artist. Editor. Five AI crew members, one three-line logline — "
            "and the only person on set is you, exactly twice."
        ),
        body=(
            "Episode 9 covers the full AI production pipeline: a "
            "screenwriter breaking a logline into scenes and shots, a "
            "human casting gate, character LoRA training from reference "
            "photos, an AI cinematographer, storyboard generation with a "
            "vision model auto-approving on-model shots and escalating the "
            "doubtful ones, single-shot regeneration, and a second human "
            "approval gate before rendering."
        ),
        chapters=[
            "Five AI crew members, one logline",
            "Screenwriter — scene/shot breakdown",
            "Casting gate (human #1)",
            "Cast & LoRA — reference photos to trained identity",
            "Cinematographer — camera, framing, lens",
            "Storyboard + Curator — the AI reviews its own work",
            "Regenerate one shot",
            "Approval gate (human #2) → rendering",
            "Closer — that file is a real editing project",
        ],
        links=[10],
        tags=["AI filmmaking", "LoRA training", "AI storyboard"],
    ),
    10: dict(
        title="The Editor That Shows Its Work — Video Editor",
        keyword="video editor",
        hook=(
            "Most AI editors are a black box. This one lets you open the "
            "exact frames its art director looked at — and overrule it, per "
            "clip."
        ),
        body=(
            "Episode 10 covers the AI-assisted video editor: dropping clips "
            "and a song onto a three-lane timeline, an auto-editor style "
            "recipe combining trims with beat/energy sync and per-clip "
            "vision look-picks, a Director's Notes panel showing the actual "
            "frames the model evaluated, real ffmpeg-backed rendering, and a "
            "one-click handoff to Shotcut where every filter is a native, "
            "editable object — nothing baked in."
        ),
        chapters=[
            "Most AI editors are a black box",
            "Drop in — clips + song, three-lane timeline",
            "Plan — Cinematic style recipe",
            "Director's Notes — the exact frames it looked at",
            "Render — .mlt + .mp4",
            "Open in Shotcut — native, editable filters",
            "Keyboard shortcuts",
            "Closer — can it improve its own code?",
        ],
        links=[11],
        tags=["AI video editing", "Shotcut", "automated video editing"],
    ),
    11: dict(
        title="The System That Fixes Itself — Self-Repair",
        keyword="self-repair",
        hook=(
            "Every night, this system runs its own test suite. When "
            "something fails, it dispatches an agent to fix it, then "
            "re-runs the tests — and it asks an outside guardian for "
            "permission before touching a single file."
        ),
        body=(
            "Episode 11 covers self-improvement and multi-agent swarms: a "
            "712-node dependency map, a real staged fix dispatched from a "
            "stale-node click, a pytest-driven fix-and-verify loop, an "
            "independent guardian model with veto power over risky changes, "
            "a human-reviewed pending-fixes queue, five agents coding in "
            "isolated git worktrees, offline operation in Flight Mode, and "
            "an overnight autoresearch run with morning promotion/revert. "
            "Closes with the honesty beat behind why five separate kill "
            "switches exist."
        ),
        chapters=[
            "Every night, it runs its own test suite",
            "System Map — 712-node constellation",
            "Finding → Fix — a real fix, staged",
            "Self-improvement run — test, fix, verify, green",
            "The guardian — an independent model with veto power",
            "Pending Fixes — you are the last gate",
            "Swarm — five agents, isolated worktrees",
            "Flight Mode — network down, agents keep coding",
            "Autoresearch — overnight run, morning report",
            "The retro — the runaway story, straight",
            "Closer — autonomy needs a leash",
        ],
        links=[12],
        tags=["self-improving AI", "multi-agent systems", "AI code review"],
    ),
    12: dict(
        title="Command Center — Every Kill Switch, Explained",
        keyword="command center",
        hook=(
            "Eleven episodes of AI doing whatever it wants would be "
            "terrifying — if you couldn't see everything, gate everything, "
            "and kill everything. Welcome to the command center."
        ),
        body=(
            "The series finale: live per-plugin VRAM budgeting, GPU conflict "
            "detection between services sharing one card, the difference "
            "between what you queued and what the system decided to do on "
            "its own, five kill switches (ending with a database-level "
            "killswitch script that works even when the app doesn't), "
            "codebase lock, the full `llx` CLI, MCP tool access with "
            "default-deny categories, Discord integration, second-node "
            "pairing, and schema-aware backup/restore — closing on a "
            "reprise of all 12 episodes."
        ),
        chapters=[
            "If you couldn't see everything, gate everything, kill everything",
            "VRAM budget — live, per-plugin",
            "Conflict detection — the system referees",
            "Jobs vs Activity",
            "Five kill switches",
            "Codebase Lock",
            "CLI — the whole platform from a shell",
            "MCP — tool access, default deny",
            "Discord integration",
            "Interconnector — fixes propagate to the family",
            "Backup — schema-aware restore",
            "Series closer — everything linked below",
        ],
        links=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
        tags=["AI safety", "kill switch", "GPU resource management"],
    ),
}


def render(num):
    ep = EPISODES[num]
    lines = []
    lines.append(ep["hook"])
    lines.append("")
    lines.append(ep["body"])
    lines.append("")
    lines.append(f"{MANTRA} This is Episode {num} of 12.")
    lines.append("")
    if num in REAL_CHAPTERS:
        lines.append("CHAPTERS")
        for seconds, label in REAL_CHAPTERS[num]:
            lines.append(f"{seconds // 60}:{seconds % 60:02d} {label}")
    else:
        lines.append("CHAPTERS (add exact timestamps after the final edit)")
        lines.append(f"0:00 {ep['chapters'][0]}")
        for label in ep["chapters"][1:]:
            lines.append(f"[__:__] {label}")
    lines.append("")
    lines.append("FEATURED IN THIS EPISODE")
    for n in ep["links"]:
        other = EPISODES[n]
        lines.append(f"▸ Ep {n}: {other['title']} — {{EP{n:02d}}}")
    lines.append("")
    lines.append(f"Full playlist: {{PLAYLIST}}")
    lines.append("")
    lines.append(WHAT_IS)
    lines.append("")
    lines.append(f"Source & docs: {GITHUB}")
    lines.append("")
    tags = TAGS_COMMON + [ep["keyword"]] + ep.get("tags", [])
    lines.append(" ".join(f"#{re.sub(r'[^A-Za-z0-9]', '', t)}" for t in tags))
    return "\n".join(lines) + "\n"


def build(_args):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for n in EPISODES:
        out = OUT_DIR / f"ep{n:02d}_description.txt"
        out.write_text(render(n))
        print(out)


TOKEN_RE = re.compile(r"\{(EP\d\d|PLAYLIST)\}")


def resolve(args):
    """Substitute {EPxx}/{PLAYLIST} tokens with real URLs. A line whose token
    has no entry in the map is dropped outright — a raw "{EP03}" placeholder
    shipped into a live description reads as broken, not as a TODO."""
    mapping = json.loads(Path(args.map).read_text())

    def resolved(key):
        if key == "PLAYLIST":
            return mapping.get("PLAYLIST")
        vid = mapping.get(str(int(key[2:])))
        return f"https://youtu.be/{vid}" if vid else None

    for n in EPISODES:
        src = OUT_DIR / f"ep{n:02d}_description.txt"
        if not src.exists():
            continue
        out_lines = []
        for line in src.read_text().splitlines():
            tokens = TOKEN_RE.findall(line)
            if not tokens:
                out_lines.append(line)
                continue
            values = {t: resolved(t) for t in tokens}
            if any(v is None for v in values.values()):
                continue
            out_lines.append(TOKEN_RE.sub(lambda m: values[m.group(1)], line))
        text = "\n".join(out_lines)
        # A section whose every bullet got dropped leaves a bare heading.
        text = re.sub(r"\nFEATURED IN THIS EPISODE\n(?=\n)", "\n", text)
        # Collapse a run of blank lines left by a dropped line.
        text = re.sub(r"\n{3,}", "\n\n", text)
        out = OUT_DIR / f"ep{n:02d}_description_final.txt"
        out.write_text(text.strip() + "\n")
        print(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    rs = sub.add_parser("resolve")
    rs.add_argument("map", help='JSON file: {"1": "videoId", ..., "PLAYLIST": "url"}')
    args = ap.parse_args()
    if args.cmd == "build":
        build(args)
    elif args.cmd == "resolve":
        resolve(args)


if __name__ == "__main__":
    main()
