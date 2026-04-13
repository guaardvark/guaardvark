#!/usr/bin/env python3
"""
Generate training data by running automated practice sessions.

Opens known web pages on the virtual display and directs the servo controller
to click specific targets. Every interaction is automatically recorded.

Usage:
    python3 generate_practice_data.py --rounds 50
"""

import argparse
import logging
import os
import random
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.environ.setdefault("GUAARDVARK_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
os.environ.setdefault("GUAARDVARK_AGENT_DISPLAY", ":99")

PRACTICE_PAGES = [
    ("https://www.google.com", [
        "Google Search button",
        "I'm Feeling Lucky button",
        "Gmail link in top right",
        "Images link in top right",
        "search input box",
    ]),
    ("https://www.youtube.com", [
        "search box at the top",
        "Home button in left sidebar",
        "Shorts button in left sidebar",
        "Subscriptions in left sidebar",
        "Sign in button",
    ]),
    ("https://en.wikipedia.org", [
        "search box",
        "Main page link",
        "Contents link in sidebar",
        "Random article link in sidebar",
    ]),
    ("https://github.com", [
        "Sign in button",
        "search box at the top",
    ]),
]


def navigate_to(screen, url: str):
    screen.hotkey("ctrl", "l")
    time.sleep(0.3)
    screen.type_text(url)
    time.sleep(0.2)
    screen.hotkey("Return")
    time.sleep(4)


def run_practice(rounds: int = 50, bootstrap: bool = False):
    """Run practice sessions. In bootstrap mode, skips the full servo loop
    and just does ballistic estimation + record (faster, all marked successful
    for initial training data generation)."""
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.services.training_data_collector import TrainingDataCollector
    from backend.utils.vision_analyzer import VisionAnalyzer
    from backend.utils.cursor_overlay import composite_bullseye

    screen = LocalScreenBackend()
    analyzer = VisionAnalyzer(default_model="qwen3-vl:2b-instruct")
    collector = TrainingDataCollector()

    if not bootstrap:
        from backend.services.servo_controller import ServoController
        servo = ServoController(screen, analyzer, collector=collector)

    completed = 0
    for i in range(rounds):
        page_url, targets = random.choice(PRACTICE_PAGES)
        target = random.choice(targets)

        logger.info(f"[{i+1}/{rounds}] {'Bootstrap' if bootstrap else 'Practice'}: '{target}' on {page_url}")
        navigate_to(screen, page_url)

        if bootstrap:
            # Fast mode: just estimate coordinates and record
            import json as _json
            screenshot, cursor_pos = screen.capture()
            annotated = composite_bullseye(screenshot, cursor_pos)
            prompt = (
                f"Image size: 1024x1024. "
                f"Find the {target}. "
                f"Output only: {{\"x\": CENTER_X, \"y\": CENTER_Y}}"
            )
            result = analyzer.analyze(annotated, prompt=prompt, num_predict=128, temperature=0.3)
            try:
                text = result.description.strip()
                start_idx = text.find("{")
                end_idx = text.rfind("}") + 1
                if start_idx >= 0 and end_idx > start_idx:
                    data = _json.loads(text[start_idx:end_idx])
                    raw_x = data.get("x", 640)
                    raw_y = data.get("y", 360)
                    if isinstance(raw_x, list):
                        raw_x = raw_x[0]
                    if isinstance(raw_y, list):
                        raw_y = raw_y[0]
                    x, y = int(raw_x), int(raw_y)
                else:
                    x, y = 640, 360
            except Exception:
                x, y = 640, 360

            collector.record(
                screenshot_before=screenshot,
                crosshair_pos=cursor_pos,
                target_description=target,
                target_actual=(x, y),
                corrections=[],
                success=True,  # bootstrap: assume model's estimate is training target
                app_context=page_url,
            )
            logger.info(f"  -> Recorded estimate ({x}, {y}) in {result.inference_ms}ms")
        else:
            result = servo.click_target(target)
            status = "HIT" if result.get("verified") else "MISS"
            corrections = result.get("corrections", 0)
            logger.info(f"  -> {status} ({corrections} corrections, {result.get('time_ms', 0)}ms)")

        completed += 1
        time.sleep(0.5)

    stats = collector.stats()
    logger.info(f"\nComplete: {completed}/{rounds} rounds")
    logger.info(f"Training data: {stats['total']} interactions recorded")
    logger.info(f"Successful: {stats['successful']}")


def main():
    parser = argparse.ArgumentParser(description="Generate training data via practice sessions")
    parser.add_argument("--rounds", type=int, default=50, help="Number of practice rounds")
    parser.add_argument("--bootstrap", action="store_true",
                        help="Bootstrap mode: fast coordinate estimation only, no servo loop")
    args = parser.parse_args()
    run_practice(args.rounds, bootstrap=args.bootstrap)


if __name__ == "__main__":
    main()
