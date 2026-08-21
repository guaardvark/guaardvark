"""GlobalLoadGate — RAM + VRAM + load admission control for the whole box.

The freeze that motivated this: a self-driven GPU/model job on the single shared
16GB GPU + 60GB RAM box drove the machine into swap and locked up the operator's
PC. JobOperationGate serializes *GPU exclusivity*, but it does NOT model total
RAM/VRAM/CPU pressure — two CPU-heavy jobs (or a job plus several `claude` CLI
subprocesses, each carrying "shadow RAM") can still exhaust memory and freeze the
box without ever touching the GPU-exclusive slot.

GlobalLoadGate is a process-local admission gate. Before a heavy job starts, it
calls ``admit(JobWeight(...))``; admit blocks (up to ``timeout``) until the box
has headroom for the declared weight, or raises ``LoadGateTimeout``. It is
defense-in-depth on top of JobOperationGate, not a replacement.

Thresholds for THIS box (60GB RAM / 16GB VRAM / 16 threads):
    RAM available  < 6 GB   -> HARD block      (< 12 GB -> warn)
    swap used      > 8 GB   -> HARD block
    VRAM free      < 1.5 GB -> HARD block      (< 3 GB  -> warn)
    loadavg(1m)    > 18     -> HARD block

Intent buffer (the "shadow RAM" cover): when a job is admitted we add its
``ram_gb`` to ``reserved_ram`` and hold it for ~60s. Spawned subprocesses (e.g.
``claude`` CLIs) don't show their RAM footprint immediately; reserving up front
keeps a second admit from over-committing before the first job's children have
materialized in psutil's numbers.

LOCK-ORDERING RULE (read before composing with JobOperationGate):
    acquire the GPU-exclusive slot FIRST (JobOperationGate.gpu_exclusive), THEN
    GlobalLoadGate.admit(). Releasing happens in reverse. Acquiring the load gate
    first while waiting on the GPU slot would let a load-gate holder sit on RAM
    headroom while blocked on the GPU — a self-inflicted deadlock under
    contention.

VRAM is read via ``nvidia-smi`` subprocess (no pynvml dependency added). If
``nvidia-smi`` is missing or fails, VRAM is treated as UNKNOWN and is NOT used to
block — degrade gracefully rather than refuse all work on a CPU-only host.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional

import psutil

logger = logging.getLogger(__name__)


# ---- thresholds (this box) -------------------------------------------------

RAM_HARD_MIN_GB = 6.0      # hard-floor CAP; the effective floor scales down on small boxes
RAM_WARN_MIN_GB = 12.0     # below this -> warn (still admit)
SWAP_HARD_MAX_GB = 8.0     # above this swap used -> hard block
VRAM_HARD_MIN_GB = 1.5     # below this free VRAM -> hard block
VRAM_WARN_MIN_GB = 3.0     # below this -> warn (still admit)
LOADAVG_HARD_MAX = 18.0    # 1-min loadavg above this -> hard block

# How long an admitted job's RAM reservation lingers to cover subprocess
# "shadow RAM" before psutil reflects it.
RESERVED_RAM_TTL_S = 60.0


def effective_ram_hard_min_gb() -> float:
    """Hard RAM floor for this machine: RAM_HARD_MIN_GB, capped at 10% of total RAM.

    A FIXED 6 GB floor is ~20% of a 32 GB machine but ~5% of a 128 GB one. On 32 GB
    boxes that made the default image model unadmittable at ANY moment: it declares
    ~21 GB, so the gate demanded 21 + 6 = 27 GB against the ~25 GB such a machine ever
    has available. Guaardvark refused to run its own recommended model, with no
    configuration that could help (this constant was not overridable).

    Scaling by total RAM keeps large machines exactly as protected as before -
    min() means the floor can only ever shrink, never grow.

    Override with GUAARDVARK_RAM_HARD_MIN_GB (float, GB) to pin an explicit value.
    """
    override = os.environ.get("GUAARDVARK_RAM_HARD_MIN_GB")
    if override:
        try:
            return float(override)
        except ValueError:
            logger.warning("Ignoring non-numeric GUAARDVARK_RAM_HARD_MIN_GB=%r", override)
    try:
        total_gb = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        return RAM_HARD_MIN_GB
    return min(RAM_HARD_MIN_GB, total_gb * 0.10)

GB = 1024 ** 3


class LoadGateTimeout(Exception):
    """Raised by admit() when headroom never appears within the timeout."""


@dataclass
class JobWeight:
    """Declared resource footprint of a job seeking admission."""
    ram_gb: float = 0.0
    vram_gb: float = 0.0
    cpu_cores: float = 0.0


@dataclass
class _Reservation:
    ram_gb: float
    expires_at: float


@dataclass
class LoadReading:
    ram_avail_gb: float
    swap_used_gb: float
    loadavg_1m: float
    vram_free_gb: Optional[float]  # None == unknown (degrade gracefully)
    reserved_ram_gb: float = 0.0

    @property
    def effective_ram_avail_gb(self) -> float:
        """Available RAM minus our outstanding intent reservations."""
        return self.ram_avail_gb - self.reserved_ram_gb


def _read_vram_free_gb() -> Optional[float]:
    """Free VRAM in GB via nvidia-smi. None when nvidia-smi is missing/fails.

    Returning None (UNKNOWN) is deliberate: a missing nvidia-smi means either a
    CPU-only host or a probe failure — neither should hard-block all work.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        logger.debug("nvidia-smi unavailable for VRAM probe (%s) — VRAM UNKNOWN", e)
        return None

    if result.returncode != 0:
        logger.debug("nvidia-smi returned %s — VRAM UNKNOWN", result.returncode)
        return None

    line = (result.stdout or "").strip().splitlines()
    if not line:
        return None
    try:
        # First GPU only (this box has one).
        free_mb = float(line[0].strip().split(",")[0])
    except (ValueError, IndexError):
        logger.debug("could not parse nvidia-smi output %r — VRAM UNKNOWN", result.stdout)
        return None
    return free_mb / 1024.0


class GlobalLoadGate:
    """Process-local RAM/VRAM/CPU admission gate. Singleton via get_load_gate()."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reservations: list[_Reservation] = []
        # Notified whenever a reservation is released / expires so blocked
        # admit() callers re-check promptly instead of only on poll.
        self._cond = threading.Condition(self._lock)

    # ---- reservation bookkeeping ------------------------------------------

    def _prune_expired(self) -> None:
        now = time.monotonic()
        self._reservations = [r for r in self._reservations if r.expires_at > now]

    def _reserved_ram_gb(self) -> float:
        self._prune_expired()
        return sum(r.ram_gb for r in self._reservations)

    # ---- reading the box --------------------------------------------------

    def read(self) -> LoadReading:
        """Snapshot current system load (plus our reserved RAM)."""
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        try:
            load1 = os.getloadavg()[0]
        except (OSError, AttributeError):
            load1 = 0.0
        with self._lock:
            reserved = self._reserved_ram_gb()
        return LoadReading(
            ram_avail_gb=vm.available / GB,
            swap_used_gb=sw.used / GB,
            loadavg_1m=load1,
            vram_free_gb=_read_vram_free_gb(),
            reserved_ram_gb=reserved,
        )

    def _blocking_reason(self, weight: JobWeight, reading: LoadReading) -> Optional[str]:
        """Return a human-readable reason to block, or None if admittable."""
        eff_ram = reading.effective_ram_avail_gb
        # The job needs its declared ram on top of the hard floor.
        ram_floor = effective_ram_hard_min_gb()
        if eff_ram - weight.ram_gb < ram_floor:
            return (
                f"system RAM too low (not VRAM): {eff_ram:.1f} GB available "
                f"(reserved {reading.reserved_ram_gb:.1f} GB), "
                f"need {weight.ram_gb:.1f} GB + {ram_floor:.1f} GB floor. "
                "Swap does not count; pick a lighter model or add system RAM"
            )
        if reading.swap_used_gb > SWAP_HARD_MAX_GB:
            return f"swap in use: {reading.swap_used_gb:.1f} GB > {SWAP_HARD_MAX_GB:.0f} GB"
        if reading.loadavg_1m > LOADAVG_HARD_MAX:
            return f"loadavg high: {reading.loadavg_1m:.1f} > {LOADAVG_HARD_MAX:.0f}"
        # VRAM only blocks when we actually know it (None == unknown == OK).
        if reading.vram_free_gb is not None:
            if reading.vram_free_gb - weight.vram_gb < VRAM_HARD_MIN_GB:
                return (
                    f"VRAM too low: {reading.vram_free_gb:.1f} GB free, "
                    f"need {weight.vram_gb:.1f} GB + {VRAM_HARD_MIN_GB:.1f} GB floor"
                )
        return None

    def _warn_if_marginal(self, reading: LoadReading) -> None:
        if reading.effective_ram_avail_gb < RAM_WARN_MIN_GB:
            logger.warning(
                "GlobalLoadGate: RAM marginal — %.1f GB available (reserved %.1f GB)",
                reading.effective_ram_avail_gb, reading.reserved_ram_gb,
            )
        if reading.vram_free_gb is not None and reading.vram_free_gb < VRAM_WARN_MIN_GB:
            logger.warning(
                "GlobalLoadGate: VRAM marginal — %.1f GB free", reading.vram_free_gb,
            )

    # ---- admit / release --------------------------------------------------

    def admit(self, weight: JobWeight, timeout: float = 0.0) -> None:
        """Block until the box has headroom for ``weight``, then reserve it.

        Adds ``weight.ram_gb`` to the intent buffer (held ~RESERVED_RAM_TTL_S)
        so concurrent admits don't over-commit before subprocess RAM lands in
        psutil. Raises LoadGateTimeout if headroom never appears in ``timeout``
        seconds (timeout=0 means a single check — fail fast, do not block).
        """
        deadline = time.monotonic() + timeout
        poll = 0.5
        with self._cond:
            while True:
                reading = self.read()
                reason = self._blocking_reason(weight, reading)
                if reason is None:
                    self._warn_if_marginal(reading)
                    self._reservations.append(
                        _Reservation(
                            ram_gb=weight.ram_gb,
                            expires_at=time.monotonic() + RESERVED_RAM_TTL_S,
                        )
                    )
                    logger.debug(
                        "GlobalLoadGate admitted weight(ram=%.1f vram=%.1f cpu=%.1f)",
                        weight.ram_gb, weight.vram_gb, weight.cpu_cores,
                    )
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LoadGateTimeout(f"GlobalLoadGate blocked: {reason}")
                # Wait on the condition (woken by release) or poll, whichever first.
                self._cond.wait(timeout=min(poll, remaining))

    def release(self, weight: JobWeight) -> None:
        """Release the intent reservation for ``weight`` (idempotent-ish).

        Drops the oldest reservation matching this weight's ram_gb. Expired
        reservations are pruned regardless, so a missed release self-heals after
        RESERVED_RAM_TTL_S.
        """
        with self._cond:
            self._prune_expired()
            for i, r in enumerate(self._reservations):
                if abs(r.ram_gb - weight.ram_gb) < 1e-9:
                    del self._reservations[i]
                    break
            self._cond.notify_all()


# ---- singleton -------------------------------------------------------------

_LOAD_GATE_SINGLETON: Optional[GlobalLoadGate] = None
_LOAD_GATE_LOCK = threading.Lock()


def get_load_gate() -> GlobalLoadGate:
    global _LOAD_GATE_SINGLETON
    if _LOAD_GATE_SINGLETON is None:
        with _LOAD_GATE_LOCK:
            if _LOAD_GATE_SINGLETON is None:
                _LOAD_GATE_SINGLETON = GlobalLoadGate()
    return _LOAD_GATE_SINGLETON


@contextmanager
def system_load_admit(weight: JobWeight, timeout: float = 0.0) -> Iterator[None]:
    """Admit ``weight`` for the duration of the block; release on exit.

    Remember the lock-ordering rule: enter the GPU-exclusive slot FIRST, then
    this. Example::

        with gate.gpu_exclusive(JobKind.VIDEO_RENDER, rid):
            with system_load_admit(JobWeight(ram_gb=8, vram_gb=10), timeout=120):
                ...render...
    """
    g = get_load_gate()
    g.admit(weight, timeout=timeout)
    try:
        yield
    finally:
        g.release(weight)
