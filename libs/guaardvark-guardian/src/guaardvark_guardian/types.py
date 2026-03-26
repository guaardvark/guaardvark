"""Type definitions for guaardvark-guardian."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Directive(str, Enum):
    """Guardian directives — actions the supervisor can instruct.

    proceed: Change is safe, apply it.
    proceed_with_caution: Change is likely safe but warrants monitoring.
    reject: Change should not be applied.
    halt_self_improvement: Stop all autonomous code modification immediately.
    lock_codebase: Prevent any file writes until manually unlocked.
    halt_family: Broadcast halt to all connected nodes (fleet-wide emergency stop).
    """
    PROCEED = "proceed"
    PROCEED_WITH_CAUTION = "proceed_with_caution"
    REJECT = "reject"
    HALT_SELF_IMPROVEMENT = "halt_self_improvement"
    LOCK_CODEBASE = "lock_codebase"
    HALT_FAMILY = "halt_family"

    @property
    def is_emergency(self) -> bool:
        return self in (
            Directive.HALT_SELF_IMPROVEMENT,
            Directive.LOCK_CODEBASE,
            Directive.HALT_FAMILY,
        )


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class ReviewResult:
    """Result of a guardian code review."""
    approved: bool
    directive: Directive
    risk_level: RiskLevel
    reason: str
    suggestions: List[str] = field(default_factory=list)
    offline_fallback: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewResult":
        directive_str = data.get("directive", "proceed_with_caution")
        try:
            directive = Directive(directive_str)
        except ValueError:
            directive = Directive.PROCEED_WITH_CAUTION

        risk_str = data.get("risk_level", "unknown")
        try:
            risk_level = RiskLevel(risk_str)
        except ValueError:
            risk_level = RiskLevel.UNKNOWN

        return cls(
            approved=data.get("approved", True),
            directive=directive,
            risk_level=risk_level,
            reason=data.get("reason", ""),
            suggestions=data.get("suggestions", []),
            offline_fallback=data.get("offline_fallback", False),
        )

    @staticmethod
    def offline_default(reason: str = "Guardian unavailable") -> "ReviewResult":
        """Safe default when Claude API is not reachable."""
        return ReviewResult(
            approved=True,
            directive=Directive.PROCEED_WITH_CAUTION,
            risk_level=RiskLevel.UNKNOWN,
            reason=reason,
            offline_fallback=True,
        )


@dataclass
class EscalationResult:
    """Result of escalating a question to Claude."""
    available: bool
    response: Optional[str] = None
    reason: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[Dict[str, int]] = None


@dataclass
class AdvisoryResult:
    """Result of requesting system health advice."""
    available: bool
    recommendations: List[Dict[str, str]] = field(default_factory=list)
    overall_health: Optional[str] = None
    reason: Optional[str] = None
