"""Environment for a child process that must not reach the live database, the
task queue or Redis: the Diagnostics test run and self-edit checks."""

import os
from typing import Dict

UNREACHABLE = {
    "GUAARDVARK_MODE": "test",
    "DATABASE_URL": "postgresql://isolated-check@127.0.0.1:1/unreachable",
    "CELERY_BROKER_URL": "redis://127.0.0.1:1/0",
    "CELERY_RESULT_BACKEND": "redis://127.0.0.1:1/0",
    "REDIS_URL": "redis://127.0.0.1:1/0",
    "DISABLE_CELERY": "1",
    # Read by run_tests.py, which then skips its live schema check.
    "GUAARDVARK_NO_LIVE_STATE": "1",
}


def isolated_env(**extra: str) -> Dict[str, str]:
    """This process's environment with every live-state address replaced
    (nothing listens on port 1), plus ``extra``."""
    env = dict(os.environ)
    env.update(UNREACHABLE)
    env.update(extra)
    return env
