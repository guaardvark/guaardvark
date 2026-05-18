"""Test-wide environment setup.

Must run before `service.app` is imported anywhere, so we set the env var at
import time — pytest collects this file first by convention.
"""
from __future__ import annotations

import os

# Default: every pytest run skips real backend registration so import stays
# fast and we don't accidentally pull ~1.5 GB of weights in a hermetic test.
# Specific test files (test_fx_sao.py etc.) clear this env var themselves,
# using importlib.reload to re-trigger bootstrap.
os.environ.setdefault("AUDIO_FOUNDRY_DISABLE_BACKENDS", "all")
# Same idea for the GPU orchestrator HTTP client — never talk to a live backend
# during tests. The dispatcher's load lifecycle stays the same shape; the
# orchestrator calls just become no-ops.
os.environ.setdefault("AUDIO_FOUNDRY_DISABLE_ORCHESTRATOR", "1")
