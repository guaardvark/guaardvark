#!/usr/bin/env python3
"""Score today's rules on the decision labelled sets.

The rule is each decision's fallback: the code that decides when the model is
not asked. Its score on the same items is what a decision in
decision_data.QUALIFY_BY_RULE has to beat, by EVIDENCE["rule_margin"] at the
model's lower bound (decision_evidence). No model is called.

    backend/venv/bin/python scripts/eval_decision_rules.py
    backend/venv/bin/python scripts/eval_decision_rules.py --rows

--rows prints the RULE_ACCURACY entries to paste into
backend/services/decision_data.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def rules() -> dict:
    """{decision: function(state) -> today's answer}."""
    from backend.services import unified_chat_engine as ue
    from backend.services.agent_brain import AFFIRMATION_PATTERNS, NO_SCREEN_CONTEXT
    from backend.services.unified_chat_engine import UnifiedChatEngine as Engine

    def image(state):
        message, has_image = state["message"], bool(state["has_recent_image"])
        if ue.user_wants_image_edit(message, has_recent_image=has_image):
            return "edit_image"
        if ue.user_wants_image_generation(message) or ue.user_wants_video_generation(message):
            return "new_image"
        return "neither"

    return {
        "needs_live_data": lambda st: Engine._is_realtime_query(st["message"]),
        "accepted_offer": lambda st: bool(AFFIRMATION_PATTERNS.fullmatch(st["user_reply"].strip())),
        "image_intent": image,
        "screen_or_chat": lambda st: "chat" if NO_SCREEN_CONTEXT.match(st["message"].strip()) else "screen_task",
        "skip_rag": lambda st: not Engine._should_skip_rag(st["message"]) and not ue.is_conversational(st["message"]),
        "context_answers": lambda st: True,  # every passage found goes into the prompt
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--decision", action="append", default=[])
    parser.add_argument("--rows", action="store_true")
    args = parser.parse_args(argv)

    from backend.services.decision_data import DECISIONS
    from backend.tests.fixtures.decision_sets import load_set, scored_items

    rows = {}
    for decision, rule in rules().items():
        if args.decision and decision not in args.decision:
            continue
        data = load_set(DECISIONS[decision]["set"])
        items = scored_items(data)
        errors: dict = {}
        correct = 0
        for item in items:
            got = rule(item["state"])
            if got == item["want"]:
                correct += 1
            else:
                key = f"want {item['want']}, rule {got}"
                errors[key] = errors.get(key, 0) + 1
        print(f"{decision}: rule {correct}/{len(items)} ({correct / len(items):.1%}); misses {errors}")
        rows[decision] = {"set_version": data["set_version"], "correct": correct, "n": len(items),
                          "date": dt.date.today().isoformat()}
    if args.rows:
        print("\n# RULE_ACCURACY entries (backend/services/decision_data.py)")
        for decision, row in rows.items():
            print(f'    "{decision}": [{json.dumps(row)}],')
    return 0


if __name__ == "__main__":
    sys.exit(main())
