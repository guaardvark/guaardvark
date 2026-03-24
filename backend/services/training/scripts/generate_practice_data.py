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


def run_practice(rounds: int = 50):
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.services.servo_controller import ServoController
    from backend.services.training_data_collector import TrainingDataCollector
    from backend.utils.vision_analyzer import VisionAnalyzer

    screen = LocalScreenBackend()
    analyzer = VisionAnalyzer(default_model="qwen3-vl:2b-instruct")
    collector = TrainingDataCollector()
    servo = ServoController(screen, analyzer, collector=collector)

    completed = 0
    for i in range(rounds):
        page_url, targets = random.choice(PRACTICE_PAGES)
        target = random.choice(targets)

        logger.info(f"[{i+1}/{rounds}] Practice: click '{target}' on {page_url}")
        navigate_to(screen, page_url)

        result = servo.click_target(target)
        status = "HIT" if result.get("verified") else "MISS"
        corrections = result.get("corrections", 0)
        logger.info(f"  -> {status} ({corrections} corrections, {result.get('time_ms', 0)}ms)")
        completed += 1

        time.sleep(1)

    stats = collector.stats()
    logger.info(f"\nPractice complete: {completed}/{rounds} rounds")
    logger.info(f"Training data: {stats['total']} interactions recorded")
    logger.info(f"Successful: {stats['successful']}")


def main():
    parser = argparse.ArgumentParser(description="Generate training data via practice sessions")
    parser.add_argument("--rounds", type=int, default=50, help="Number of practice rounds")
    args = parser.parse_args()
    run_practice(args.rounds)


if __name__ == "__main__":
    main()
