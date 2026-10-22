"""ACE-Step 1.5 subprocess daemon.

Runs INSIDE plugins/audio_foundry/venv-music15/, which scripts/setup_music15.sh builds
from the pinned ACE-Step 1.5 release (torch 2.10, transformers 4.57: neither the main
plugin venv nor venv-music can hold them). backends/music_gen_acestep15.py drives it
over the same JSON-line protocol as run_acestep.py.

Commands:
    {"op": "ping"}
        -> {"ok": true, "ready": <bool>}

    {"op": "load"}
        -> {"ok": true} on success
        -> {"ok": false, "error": "..."} on failure

    {"op": "plan",
     "params": {
        "query": "...",              # the person's description, not a tag list
        "lyrics": "",                # empty: the planner writes them, unless instrumental
        "instrumental_only": false,
        "duration_s": 60.0,          # locked into the plan
        "bpm": null,                 # null: the planner chooses; a number is kept
        "keyscale": "",
        "timesignature": "",         # "2", "3", "4" or "6"
        "vocal_language": ""
     }}
        -> {"ok": true, "arrangement": {caption, lyrics, bpm, keyscale,
            timesignature, vocal_language, duration_s, instrumental_only, genres, query}}

    {"op": "generate",
     "params": {
        "style_prompt": "...",       # the caption the DiT hears
        "lyrics": "...",
        "instrumental_only": false,
        "duration_s": 60.0,
        "bpm": null,
        "keyscale": "",
        "timesignature": "",
        "vocal_language": "",
        "use_cot_caption": true,     # false once a caption was accepted, so a second
                                     # planner pass does not replace it
        "steps": 8,
        "seed": null,                # null: a fresh one; the seed used is reported back
        "thinking": true,            # the 1.7B planner writes metadata and audio codes first
        "out_path": "/abs/path/to/output.wav"
     }}
        -> {"ok": true, "samples": <int>, "channels": <int>, "duration_s": <float>,
            "sample_rate": <int, as written: 48000>, "seed": <int, the seed used>}
        -> {"ok": false, "error": "..."}

    {"op": "unload"}
        -> {"ok": true}

    {"op": "shutdown"}
        -> {"ok": true}, then process exits

Weights are read from <repo>/<local_dir>/checkpoints (backends/acestep15_files.json),
where Install puts them. A missing file is refused here: ACE-Step's own loader answers
one by downloading, from Hugging Face or, after probing www.google.com, from ModelScope.
stdout carries only protocol lines; stderr is the human log the parent forwards.
"""
from __future__ import annotations

import gc
import json
import os
import random
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PLUGIN_ROOT.parents[1]
_CATALOG = _PLUGIN_ROOT / "backends" / "acestep15_files.json"

_INSTALL_HINT = (
    "Open Audio Studio → Manage models and Install ACE-Step 1.5. "
    "Generation never downloads on its own."
)

# The planner runs on plain PyTorch. ACE-Step's "vllm" backend reserves about 82% of
# VRAM up front, and a 240 s render then fails its own VRAM preflight (needs 1.7 GB,
# 1.5 GB free; measured 2026-10-03 on a 16 GB card). "pt" rendered 240 s in 24 s.
_PLANNER_BACKEND = "pt"

_dit: Any = None
_lm: Any = None
_protocol: Any = None


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _claim_stdout() -> Any:
    """A private copy of stdout for protocol lines, with fd 1 then pointed at stderr.

    ACE-Step prints progress to stdout ("Using precomputed LM hints"), which the
    parent would read as a broken response line.
    """
    sys.stdout.flush()
    fd = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(fd, "w", encoding="utf-8", buffering=1)


def _respond(payload: dict[str, Any]) -> None:
    out = _protocol or sys.stdout
    out.write(json.dumps(payload) + "\n")
    out.flush()


def catalog() -> dict[str, Any]:
    return json.loads(_CATALOG.read_text(encoding="utf-8"))


def model_root(cat: dict[str, Any]) -> Path:
    """ACE-Step's project root: it reads <root>/checkpoints and keeps a small cache in <root>/.cache."""
    return _REPO_ROOT / cat["local_dir"]


def missing_files(checkpoints: Path, files: list[str]) -> list[str]:
    """Required checkpoint files that are not on disk."""
    return [f for f in files if not (checkpoints / f).is_file()]


def _offline_env(root: Path) -> None:
    """Point ACE-Step at the installed weights and keep every library off the network."""
    os.environ["ACESTEP_PROJECT_ROOT"] = str(root)
    os.environ["ACESTEP_CHECKPOINTS_DIR"] = str(root / "checkpoints")
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
        os.environ[name] = "1"
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"


def _block_downloads() -> None:
    """Make ACE-Step's download fallback fail instead of fetching."""
    from acestep import model_downloader

    def _refuse(repo_id, *args, **kwargs):
        return False, f"{repo_id} is not installed here. {_INSTALL_HINT}"

    model_downloader._smart_download = _refuse


def _do_load() -> dict[str, Any]:
    global _dit, _lm
    if _dit is not None:
        return {"ok": True}

    cat = catalog()
    root = model_root(cat)
    missing = missing_files(root / "checkpoints", cat["files"])
    if missing:
        return {
            "ok": False,
            "error": f"ACE-Step 1.5 weights are not on this machine (missing {missing[0]}). {_INSTALL_HINT}",
        }

    _offline_env(root)
    import torch
    if not torch.cuda.is_available():
        return {"ok": False, "error": "No CUDA GPU available; ACE-Step 1.5 here needs an NVIDIA GPU"}

    _block_downloads()
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler

    _eprint(f"[run_acestep15] loading {cat['dit']} + {cat['planner']} from {root / 'checkpoints'}")
    dit = AceStepHandler()
    msg, ok = dit.initialize_service(
        project_root=str(root), config_path=cat["dit"], device="cuda", offload_to_cpu=False,
    )
    if not ok:
        return {"ok": False, "error": f"ACE-Step 1.5 could not load {cat['dit']}: {msg}"}

    lm = LLMHandler()
    msg, ok = lm.initialize(
        checkpoint_dir=str(root / "checkpoints"), lm_model_path=cat["planner"],
        backend=_PLANNER_BACKEND, device="cuda", offload_to_cpu=False, dtype=None,
    )
    if not ok:
        del dit
        _free_cuda()
        return {"ok": False, "error": f"ACE-Step 1.5 could not load its planner {cat['planner']}: {msg}"}

    _dit, _lm = dit, lm
    _eprint(f"[run_acestep15] loaded (dtype={getattr(dit, 'dtype', '?')}, planner={_PLANNER_BACKEND})")
    return {"ok": True}


def _locked_metadata(params: dict[str, Any]) -> dict[str, str]:
    """Constraints the planner must write through, as the strings its decoder injects."""
    locked: dict[str, str] = {}
    if params.get("bpm"):
        locked["bpm"] = str(int(params["bpm"]))
    if params.get("duration_s"):
        locked["duration"] = str(int(float(params["duration_s"])))
    keyscale = (params.get("keyscale") or "").strip()
    if keyscale:
        locked["keyscale"] = keyscale
    meter = (params.get("timesignature") or "").strip()
    if meter:
        locked["timesignature"] = meter
    language = (params.get("vocal_language") or "").strip()
    if language and language.lower() != "unknown":
        locked["language"] = language
    return locked


def _whole_number(value: Any, low: int, high: int) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number < low or number > high:
        return None
    return number


def _clean_caption(text: str) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= 1500:
        return collapsed
    # The planner's caption field is a sentence. Cut at a period before the
    # decoder's token budget, not in the middle of a word.
    cut = collapsed.rfind(".", 0, 1500)
    if cut > 200:
        return collapsed[: cut + 1]
    return collapsed[:1500].rstrip()


def _arrangement_from_metadata(metadata: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    instrumental = bool(params.get("instrumental_only"))
    lyrics = "[Instrumental]" if instrumental else (metadata.get("lyrics") or params.get("lyrics") or "")
    if not instrumental:
        lyrics = str(lyrics).strip()[:4000]
    meter = str(metadata.get("timesignature") or "").strip()
    if meter.endswith("/4") or meter.endswith("/8"):
        meter = meter.split("/")[0]
    if meter not in {"2", "3", "4", "6"}:
        meter = ""
    return {
        "caption": _clean_caption(str(metadata.get("caption") or "")),
        "lyrics": lyrics,
        "bpm": _whole_number(metadata.get("bpm"), 30, 300),
        "keyscale": str(metadata.get("keyscale") or "").strip(),
        "timesignature": meter,
        "vocal_language": str(metadata.get("language") or metadata.get("vocal_language") or "").strip(),
        "duration_s": float(params.get("duration_s") or metadata.get("duration") or 60),
        "instrumental_only": instrumental,
        "genres": str(metadata.get("genres") or "").strip(),
        "query": (params.get("query") or params.get("style_prompt") or "").strip(),
    }


def _expand_query(query: str, instrumental: bool, locked: dict[str, str]) -> tuple[dict[str, Any], str]:
    """Simple mode: expand a description. Same call as create_sample_from_query,
    plus locked bpm, key, meter, language and duration, which that method does
    not accept.
    """
    formatted = _lm.build_formatted_prompt_for_inspiration(query, instrumental=instrumental)
    output_text, status = _lm.generate_from_formatted_prompt(
        formatted_prompt=formatted,
        cfg={
            "temperature": 0.85,
            "repetition_penalty": 1.0,
            "target_duration": None,
            "user_metadata": locked or None,
            "skip_caption": False,
            "skip_language": "language" in locked,
            "skip_genres": False,
            "generation_phase": "understand",
            "caption": "",
            "lyrics": "",
        },
        use_constrained_decoding=True,
        stop_at_reasoning=False,
    )
    if not output_text:
        return {}, status or "planner returned nothing"
    metadata, _parsed_codes = _lm.parse_lm_output(output_text)
    lyrics = _lm._extract_lyrics_from_output(output_text)
    if lyrics:
        metadata["lyrics"] = lyrics
    elif instrumental:
        metadata["lyrics"] = "[Instrumental]"
    return metadata, status


def _do_plan(params: dict[str, Any]) -> dict[str, Any]:
    """Ask the loaded 1.7B planner for a caption, lyrics and metadata. No audio."""
    if _lm is None:
        return {"ok": False, "error": "model not loaded — send 'load' first"}

    query = (params.get("query") or params.get("style_prompt") or "").strip()
    if not query:
        return {"ok": False, "error": "a description is required"}
    lyrics = (params.get("lyrics") or "").strip()
    instrumental = bool(params.get("instrumental_only"))
    locked = _locked_metadata(params)
    _eprint(
        f"[run_acestep15] plan query={query[:80]!r} lyrics={len(lyrics)}-chars "
        f"instrumental={instrumental} locked={locked or '-'}"
    )
    if lyrics and not instrumental:
        metadata, status = _lm.format_sample_from_input(
            caption=query, lyrics=lyrics, user_metadata=locked or None,
        )
    else:
        metadata, status = _expand_query(query, instrumental, locked)
    arrangement = _arrangement_from_metadata(metadata or {}, params)
    if not arrangement["caption"]:
        return {"ok": False, "error": status or "planner returned no caption"}
    _eprint(
        f"[run_acestep15] plan caption={arrangement['caption'][:100]!r} "
        f"bpm={arrangement['bpm']} key={arrangement['keyscale'] or '-'} "
        f"meter={arrangement['timesignature'] or '-'}"
    )
    return {"ok": True, "arrangement": arrangement}


def _do_generate(params: dict[str, Any]) -> dict[str, Any]:
    if _dit is None:
        return {"ok": False, "error": "model not loaded — send 'load' first"}

    from acestep.inference import GenerationConfig, GenerationParams, generate_music
    import soundfile as sf

    style_prompt = params["style_prompt"]
    instrumental_only = bool(params.get("instrumental_only", False))
    # "[Instrumental]" plus the instrumental flag is how ACE-Step 1.5 documents a track
    # without vocals.
    lyrics = "[Instrumental]" if instrumental_only else (params.get("lyrics") or "")
    duration_s = float(params["duration_s"])
    seed = params.get("seed")
    seed = int(seed) if seed is not None else random.randint(0, 2**32 - 1)
    out_path = Path(params["out_path"])
    bpm = _whole_number(params.get("bpm"), 30, 300)
    keyscale = (params.get("keyscale") or "").strip()
    meter = (params.get("timesignature") or "").strip()
    language = (params.get("vocal_language") or "").strip() or "unknown"
    # Once a caption was planned or edited, a second CoT caption would replace
    # it. Audio codes still need thinking; only the caption rewrite is off.
    use_cot_caption = bool(params.get("use_cot_caption", True))

    gen_params = GenerationParams(
        task_type="text2music",
        caption=style_prompt,
        lyrics=lyrics,
        instrumental=instrumental_only,
        duration=duration_s,
        bpm=bpm,
        keyscale=keyscale,
        timesignature=meter,
        vocal_language=language,
        inference_steps=int(params.get("steps", 8)),
        thinking=bool(params.get("thinking", True)),
        use_cot_caption=use_cot_caption,
    )
    # generate_music takes the DiT's seed from the config, never from
    # GenerationParams.seed. The planner seeds itself only for batches, so with one
    # item it samples from torch's global generator: seeded here, or the same seed
    # plans a different bpm and key and the take cannot be repeated.
    gen_config = GenerationConfig(batch_size=1, audio_format="wav", use_random_seed=False, seeds=[seed])
    import torch
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    _eprint(
        f"[run_acestep15] generate style={style_prompt[:60]!r} duration={duration_s:.1f}s "
        f"lyrics={len(lyrics)}-chars instrumental={instrumental_only} bpm={bpm} "
        f"cot_caption={use_cot_caption} seed={seed}"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Same filesystem as out_path, so the move is a rename; anything else ACE-Step
    # writes next to the audio goes with the folder.
    with tempfile.TemporaryDirectory(dir=out_path.parent, prefix=".acestep15-") as tmp:
        result = generate_music(_dit, _lm, params=gen_params, config=gen_config, save_dir=tmp)
        audio = result.audios[0] if result.success and result.audios else {}
        if not audio.get("path"):
            reason = result.error or result.status_message or "no audio came back"
            return {"ok": False, "error": f"ACE-Step 1.5 generation failed: {reason}"}
        shutil.move(audio["path"], out_path)

    info = sf.info(str(out_path))
    rate = int(info.samplerate)
    return {
        "ok": True,
        "samples": int(info.frames),
        "channels": int(info.channels),
        "duration_s": info.frames / rate if rate else 0.0,
        "sample_rate": rate,
        "seed": seed,
    }


def _free_cuda() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _do_unload() -> dict[str, Any]:
    global _dit, _lm
    if _dit is None and _lm is None:
        return {"ok": True}
    _dit = None
    _lm = None
    _free_cuda()
    _eprint("[run_acestep15] unloaded")
    return {"ok": True}


def _handle(cmd: dict[str, Any]) -> dict[str, Any]:
    op = cmd.get("op")
    if op == "ping":
        return {"ok": True, "ready": _dit is not None}
    if op == "load":
        return _do_load()
    if op == "plan":
        return _do_plan(cmd.get("params") or {})
    if op == "generate":
        return _do_generate(cmd["params"])
    if op == "unload":
        return _do_unload()
    if op == "shutdown":
        return {"ok": True}
    return {"ok": False, "error": f"unknown op: {op!r}"}


def main() -> int:
    global _protocol
    _protocol = _claim_stdout()
    _eprint("[run_acestep15] daemon ready, waiting on stdin...")
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            _respond({"ok": False, "error": f"bad json: {e}"})
            continue

        try:
            response = _handle(cmd)
        except Exception as e:
            tb = traceback.format_exc()
            _eprint(f"[run_acestep15] handler exception:\n{tb}")
            response = {"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": tb}

        _respond(response)

        if cmd.get("op") == "shutdown":
            _eprint("[run_acestep15] shutdown requested, exiting")
            return 0

    _eprint("[run_acestep15] stdin closed, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
