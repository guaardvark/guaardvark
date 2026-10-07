"""Data shapes shared by every inbound-guard caller: a change, a finding, a verdict.

Severities run info < low < medium < high < critical, a common ladder, so an
extension can file a verdict beside findings of its own without translation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional, Tuple

SEVERITIES = ("info", "low", "medium", "high", "critical")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}

VERDICTS = ("allow", "hold", "block")
MODES = ("off", "observe", "enforce")


@dataclass
class Change:
    """One file as it would land.

    ``added`` holds (new line number, text) for every added line; rules read only
    these, so code that already exists trips nothing until someone touches it.
    ``removed`` holds the text of removed lines, which matters only where taking
    something away is the risk (a guard step, a rule).
    """

    path: str
    status: str = "M"  # git letters: A, M, D, R, T
    added: List[Tuple[int, str]] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    old_path: Optional[str] = None
    old_mode: Optional[str] = None
    new_mode: Optional[str] = None
    new_blob: Optional[str] = None
    old_blob: Optional[str] = None
    binary: bool = False
    truncated: bool = False
    # Full texts, when the caller has them in memory (self-code edits). Git
    # changes carry blob ids instead and are read on demand.
    old_text: Optional[str] = field(default=None, repr=False, compare=False)
    new_text: Optional[str] = field(default=None, repr=False, compare=False)

    @property
    def is_symlink(self) -> bool:
        return self.new_mode == "120000"

    @property
    def symlink_target(self) -> Optional[str]:
        if self.is_symlink and self.added:
            return self.added[0][1]
        return None


CORE_PROVIDER = "core"


@dataclass
class Finding:
    """One thing a rule noticed.

    ``provider`` names what produced it, set by ``scan``: CORE_PROVIDER for the
    rules in this package, or the registered name of an extension's scanner.
    It is left out of ``to_dict`` while unset (findings built outside a scan).
    """

    rule: str
    pack: str
    severity: str
    path: str
    line: Optional[int]
    excerpt: str
    why: str
    provider: Optional[str] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if data["provider"] is None:
            del data["provider"]
        return data


@dataclass
class Verdict:
    """What the engine concluded and what the caller should do with it.

    ``verdict`` is the judgement (allow, hold, block); ``mode`` says whether the
    caller acts on it. In observe mode a hold or block is recorded and the change
    still lands.
    """

    verdict: str
    mode: str
    source: str
    subject: str
    digest: str
    findings: List[Finding] = field(default_factory=list)
    providers: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    files: int = 0
    added_lines: int = 0
    engine_version: str = ""

    @property
    def enforced(self) -> bool:
        """True when the caller must refuse or hold the change."""
        return self.mode == "enforce" and self.verdict != "allow"

    @property
    def top_severity(self) -> Optional[str]:
        if not self.findings:
            return None
        return max(self.findings, key=lambda f: SEVERITY_RANK[f.severity]).severity

    def to_dict(self) -> dict:
        data = asdict(self)
        data["findings"] = [f.to_dict() for f in self.findings]
        data["enforced"] = self.enforced
        data["top_severity"] = self.top_severity
        return data
