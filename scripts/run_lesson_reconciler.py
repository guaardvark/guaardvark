#!/usr/bin/env python3
"""Run the Phase-5 lesson reconciler from the command line.

Scans every ``belief_update`` AgentMemory, groups them by source-file/line/element,
and stages a ``PendingFix`` for each cluster that has crossed the agreement
threshold (default 3 sessions).

Usage::

    python scripts/run_lesson_reconciler.py
    python scripts/run_lesson_reconciler.py --threshold 5
    python scripts/run_lesson_reconciler.py --dry-run

Intentionally not on the Celery beat schedule — the user runs this when they
want to review the staged proposals, not on a background timer that surprises
them with edits.
"""

import argparse
import logging
import os
import sys

# Make backend.* importable when invoked from scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--threshold", type=int, default=3,
        help="Minimum number of sessions agreeing before a fix is staged",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be staged, but don't write to the database",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable info-level logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from backend.app import create_app
    app = create_app()

    with app.app_context():
        if args.dry_run:
            # We rerun the bucket logic inline so we can print the plan
            # without committing anything.
            from backend.models import AgentMemory, db
            from backend.services.lesson_reconciler import _extract_group_key, _parse_tags
            memories = (
                db.session.query(AgentMemory)
                .filter(AgentMemory.type == "belief_update")
                .all()
            )
            from collections import Counter
            buckets: Counter = Counter()
            for m in memories:
                key = _extract_group_key(_parse_tags(m.tags))
                if key:
                    buckets[key] += 1
            ready = [(k, v) for k, v in buckets.items() if v >= args.threshold]
            print(f"Found {len(memories)} belief_update memories across {len(buckets)} buckets.")
            print(f"{len(ready)} bucket(s) at or above threshold {args.threshold}:")
            for (source_file, source_line, element), n in sorted(ready, key=lambda x: -x[1]):
                print(f"  - {n}x: {element!r} @ {source_file}:{source_line}")
            return 0

        from backend.services.lesson_reconciler import scan_belief_updates
        created = scan_belief_updates(threshold=args.threshold)
        print(f"Lesson reconciler staged {created} pending fix(es).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
