"""Reconciler interface. Stdlib-only top-level imports."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass
class ReconcileResult:
    reconciler_id: str
    status: Literal["ok", "skipped", "failed"]
    message: str | None = None


class Reconciler(ABC):
    """One unit of dependency reconciliation.

    All non-stdlib imports MUST happen inside method bodies, not at module top.
    The `scripts.dep_reconciler` package may be imported before the very
    packages it's responsible for installing.
    """

    id: str = ""
    name: str = ""

    @abstractmethod
    def manifests(self) -> list[Path]:
        """Files this reconciler watches for drift."""

    @abstractmethod
    def is_active(self) -> bool:
        """Should this reconciler run on this machine right now?"""

    @abstractmethod
    def compute_hash(self) -> str:
        """SHA256 (or aggregated SHA256) over this reconciler's manifests."""

    @abstractmethod
    def install(self, log_path: Path) -> int:
        """Run the underlying installer. Return 0 on success, non-zero on failure."""

    def extra_state(self) -> dict[str, object]:
        """Optional secondary drift dimensions (numpy major, alembic head, etc.)."""
        return {}
