#!/usr/bin/env python3
"""Every installed Ollama model, as the capability record sees it, next to each
per-model check the code still makes on its own. A row marked "!" is a model
two checks answer differently.

    python scripts/model_capability_report.py            # table
    python scripts/model_capability_report.py --json     # one object per model
    python scripts/model_capability_report.py --models gemma4:e4b,qwen3.5:9b

Reads Ollama (/api/tags, /api/show) at OLLAMA_BASE_URL; changes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _call(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - one broken check must not hide the rest
        return f"error: {e}"


def installed_models() -> list:
    from backend.utils import ollama_resource_manager as orm
    resp = orm.requests.get(f"{orm.get_ollama_base_url()}/api/tags", timeout=10)
    resp.raise_for_status()
    return sorted(m.get("name", "") for m in resp.json().get("models", []) if m.get("name"))


def checks_for(tag: str) -> dict:
    """The record and the separate checks, keyed by what each one decides."""
    from backend.services.model_capabilities import capabilities_for
    from backend.utils import ollama_resource_manager as orm

    rec = capabilities_for(tag)
    row = {
        "tag": tag,
        "record": {
            "exists": rec.exists, "capabilities": list(rec.capabilities),
            "tools": rec.tools, "thinking": rec.thinking, "thinks_by_name": rec.thinks_by_name,
            "vision": rec.vision, "embedding": rec.embedding, "completion": rec.completion,
            "native_context": rec.native_context, "embedding_dim": rec.embedding_dim,
            "evidence": rec.evidence,
        },
        "vision": {"record": rec.vision},
        "thinking": {"api_show": rec.thinking, "name_pattern (think:false sent)": rec.thinks_by_name},
        "embedding": {"api_show": rec.embedding},
        "text_chat": {"api_show (completion, not embedding)": rec.completion and not rec.embedding},
        "num_ctx": _call(lambda: orm.decide_num_ctx(tag).num_ctx),
    }
    info = _call(orm.get_model_info, tag)
    if isinstance(info, dict):
        row["vision"]["get_model_info.is_vision"] = bool(info.get("is_vision"))
    try:
        from backend.utils.chat_utils import is_vision_model as chat_utils_vision
        row["vision"]["chat_utils.is_vision_model"] = _call(chat_utils_vision, tag)
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.services.servo_knowledge_store import model_name_looks_vision
        row["vision"]["servo name markers"] = _call(model_name_looks_vision, tag)
    except Exception:  # noqa: BLE001
        pass
    from backend.services.model_capability_data import name_looks_vision
    row["vision"]["resolver name fallback"] = name_looks_vision(tag)
    try:
        from backend.services.ollama_chat_model import is_embedding_model
        row["embedding"]["ollama_chat_model markers"] = _call(is_embedding_model, tag)
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.services.music_video_director import _is_embedding_model
        row["embedding"]["music_video_director markers"] = _call(_is_embedding_model, tag)
    except Exception:  # noqa: BLE001
        pass
    row["text_chat"]["is_text_chat_model (name)"] = _call(orm.is_text_chat_model, tag)
    row["disagree"] = sorted(
        topic for topic in ("vision", "thinking", "embedding", "text_chat")
        if len({json.dumps(v) for v in row[topic].values()}) > 1
    )
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models", help="comma-separated tags (default: every installed model)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = ap.parse_args(argv)

    tags = [t.strip() for t in args.models.split(",")] if args.models else installed_models()
    rows = [checks_for(t) for t in tags if t]
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0

    print(f"{'':1} {'model':40} {'tools':5} {'think':5} {'byname':6} {'vision':6} {'embed':5} "
          f"{'native_ctx':>10} {'num_ctx':>7}  disagree")
    for r in rows:
        rec = r["record"]
        flag = "!" if r["disagree"] else " "
        yn = lambda b: "yes" if b else "-"  # noqa: E731
        print(f"{flag} {r['tag'][:40]:40} {yn(rec['tools']):5} {yn(rec['thinking']):5} "
              f"{yn(rec['thinks_by_name']):6} {yn(rec['vision']):6} {yn(rec['embedding']):5} "
              f"{rec['native_context']:>10} {str(r['num_ctx']):>7}  {', '.join(r['disagree'])}")
    for r in rows:
        for topic in r["disagree"]:
            answers = ", ".join(f"{k}={v}" for k, v in r[topic].items())
            print(f"  {r['tag']}: {topic}: {answers}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
