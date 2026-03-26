"""
guaardvark-guardian — Three-tier Claude supervision for autonomous AI systems.

Provides escalation, code review (guardian), and system advisory capabilities
using Anthropic's Claude API. Designed for platforms that run autonomous AI
agents locally and need cloud-based supervision.

Usage:
    from guaardvark_guardian import Guardian

    guardian = Guardian(api_key="sk-ant-...")

    # Tier 1: Escalate a hard problem to Claude
    result = guardian.escalate("Why is this query returning stale data?")

    # Tier 2: Review an autonomous code change
    review = guardian.review_change(
        file_path="app/models.py",
        current_content="...",
        proposed_diff="...",
        reasoning="Fix N+1 query in user listing"
    )
    if review.approved:
        apply_patch(review)
    elif review.directive in ("halt_self_improvement", "lock_codebase"):
        emergency_stop()

    # Tier 3: Get system health advice
    advice = guardian.advise({"gpu_usage": 0.85, "disk_free_gb": 12})
"""

__version__ = "0.1.0"

from guaardvark_guardian.guardian import Guardian
from guaardvark_guardian.types import (
    ReviewResult,
    EscalationResult,
    AdvisoryResult,
    Directive,
    RiskLevel,
)

__all__ = [
    "Guardian",
    "ReviewResult",
    "EscalationResult",
    "AdvisoryResult",
    "Directive",
    "RiskLevel",
]
