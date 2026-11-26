"""Starts Guaardvark's code agent (backend/services/worktree_agent.py) for
the swarm's guaardvark backend.

The agent runs with a task's worktree as its working folder. Started as a
script, Python keeps that folder off the import path, so a worktree of
Guaardvark itself cannot put its own, half-edited ``backend`` package in
place of the installed one.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# plugins/swarm/service/agent_backends/ -> the checkout root
CHECKOUT = HERE.parents[3]


def _same(path: str, folder: Path) -> bool:
    try:
        return Path(path or ".").resolve() == folder
    except OSError:
        return False


if __name__ == "__main__":
    sys.path[:] = [p for p in sys.path if not _same(p, HERE)]
    sys.path.insert(0, str(CHECKOUT))
    from backend.services.worktree_agent import main

    sys.exit(main())
