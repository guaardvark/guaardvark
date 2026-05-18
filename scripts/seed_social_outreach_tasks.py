#!/usr/bin/env python3
"""
Manual trigger for social outreach passes.

The beat scheduler runs the loops automatically every 45 min (reddit) /
4 h (self-share). This script lets you kick off a one-off pass right now —
useful for smoke-testing before walking away for the night.

Usage:
    python scripts/seed_social_outreach_tasks.py reddit r/LocalLLaMA
    python scripts/seed_social_outreach_tasks.py share r/SideProject https://guaardvark.com
    python scripts/seed_social_outreach_tasks.py status
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def cmd_status():
    from backend.services.social_outreach import kill_switch
    enabled = kill_switch.is_enabled()
    supervised = kill_switch.is_supervised()
    cadence = kill_switch.cadence_status()
    print(f"social_outreach_enabled = {enabled}")
    print(f"social_outreach_supervised = {supervised}")
    print()
    print("Cadence (per platform):")
    for platform, info in cadence.items():
        print(f"  {platform}: {info}")


def cmd_reddit(sub: str):
    sub = sub.strip().lstrip("/").removeprefix("r/")
    if not sub:
        print("usage: ... reddit <subreddit>", file=sys.stderr)
        sys.exit(2)
    from backend.services.social_outreach.reddit_outreach import RedditOutreachLoop
    print(f"running reddit outreach pass on r/{sub} ...")
    report = RedditOutreachLoop().run_one_pass(sub)
    print("report:", report)


def cmd_share(sub: str, link_url: str):
    sub = sub.strip().lstrip("/").removeprefix("r/")
    if not sub or not link_url:
        print("usage: ... share <subreddit> <link_url>", file=sys.stderr)
        sys.exit(2)
    from backend.services.social_outreach.self_share import SelfShareLoop
    print(f"running self-share pass on r/{sub} ({link_url}) ...")
    report = SelfShareLoop().run_one_pass(sub, link_url)
    print("report:", report)


def cmd_kill():
    from backend.services.social_outreach import kill_switch
    kill_switch.set_enabled(False)
    print("social_outreach_enabled = false (kill switch flipped)")


def cmd_enable():
    from backend.services.social_outreach import kill_switch
    kill_switch.set_enabled(True)
    print("social_outreach_enabled = true")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    sub = argv[1]
    if sub == "status":
        cmd_status()
    elif sub == "kill":
        cmd_kill()
    elif sub == "enable":
        cmd_enable()
    elif sub == "reddit":
        if len(argv) < 3:
            print("usage: ... reddit <subreddit>", file=sys.stderr)
            return 2
        cmd_reddit(argv[2])
    elif sub == "share":
        if len(argv) < 4:
            print("usage: ... share <subreddit> <link_url>", file=sys.stderr)
            return 2
        cmd_share(argv[2], argv[3])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    # Wrap in Flask app context for DB access
    try:
        from backend.app import app
        with app.app_context():
            sys.exit(main(sys.argv))
    except Exception:
        sys.exit(main(sys.argv))
