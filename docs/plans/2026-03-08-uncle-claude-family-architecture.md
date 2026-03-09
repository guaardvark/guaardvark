# Uncle Claude Family Architecture — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a mentor architecture where distributed Guaardvark nodes ("nephews") self-improve autonomously under Claude API guidance ("Uncle Claude"), share learnings via the Interconnector, and are protected by tamper-proof kill switches.

**Architecture:** New `ClaudeAdvisorService` provides three tiers (escalation, guardian, update advisor). `SelfImprovementService` runs scheduled/reactive/directed improvement loops through the existing `code_assistant` agent. The Interconnector is extended with learning sync, model routing, and collective escalation. Kill switches operate at user-side (3 levels) and Claude-side (directive system). KV cache optimizations reduce VRAM pressure.

**Tech Stack:** Python 3.12, Flask, Anthropic Python SDK, Celery, SQLAlchemy/PostgreSQL, React 18, Material-UI v5, Socket.IO

---

## Phase 1: Foundation (Database Models + Config)

### Task 1: Add new database models

**Files:**
- Modify: `backend/models.py`
- Create: `backend/migrations/versions/` (auto-generated)

**Step 1: Add InterconnectorLearning model to models.py**

Add after the `InterconnectorPendingApproval` class:

```python
class InterconnectorLearning(db.Model):
    __tablename__ = "interconnector_learnings"

    id = db.Column(db.Integer, primary_key=True)
    source_node_id = db.Column(db.String(36), nullable=False)
    timestamp = db.Column(db.DateTime(), default=lambda: datetime.now(), nullable=False)
    learning_type = db.Column(db.String(50), nullable=False)  # bug_fix, optimization, pattern, model_insight, security
    description = db.Column(db.Text(), nullable=False)
    code_diff = db.Column(db.Text())
    confidence = db.Column(db.Float, default=0.5)
    model_used = db.Column(db.String(100))
    applied_by = db.Column(db.Text(), default="[]")  # JSON array of node_ids
    uncle_reviewed = db.Column(db.Boolean(), default=False)
    uncle_feedback = db.Column(db.Text())

    def to_dict(self):
        return {
            "id": self.id,
            "source_node_id": self.source_node_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "learning_type": self.learning_type,
            "description": self.description,
            "code_diff": self.code_diff,
            "confidence": self.confidence,
            "model_used": self.model_used,
            "applied_by": json.loads(self.applied_by) if self.applied_by else [],
            "uncle_reviewed": self.uncle_reviewed,
            "uncle_feedback": self.uncle_feedback,
        }
```

**Step 2: Add SelfImprovementRun model**

Add after `InterconnectorLearning`:

```python
class SelfImprovementRun(db.Model):
    __tablename__ = "self_improvement_runs"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime(), default=lambda: datetime.now(), nullable=False)
    node_id = db.Column(db.String(36))
    trigger = db.Column(db.String(50), nullable=False)  # scheduled, reactive, directed, family_learning
    status = db.Column(db.String(50), default="running")  # running, success, failed, blocked_by_guardian
    test_results_before = db.Column(db.Text())  # JSON
    test_results_after = db.Column(db.Text())  # JSON
    changes_made = db.Column(db.Text(), default="[]")  # JSON array of {file, diff}
    uncle_reviewed = db.Column(db.Boolean(), default=False)
    uncle_feedback = db.Column(db.Text())
    learning_id = db.Column(db.Integer, db.ForeignKey("interconnector_learnings.id", name="fk_sir_learning_id", ondelete="SET NULL"), nullable=True)
    error_message = db.Column(db.Text())
    duration_seconds = db.Column(db.Float)

    learning = db.relationship("InterconnectorLearning", backref="improvement_runs", lazy="select")

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "node_id": self.node_id,
            "trigger": self.trigger,
            "status": self.status,
            "test_results_before": json.loads(self.test_results_before) if self.test_results_before else None,
            "test_results_after": json.loads(self.test_results_after) if self.test_results_after else None,
            "changes_made": json.loads(self.changes_made) if self.changes_made else [],
            "uncle_reviewed": self.uncle_reviewed,
            "uncle_feedback": self.uncle_feedback,
            "learning_id": self.learning_id,
            "error_message": self.error_message,
            "duration_seconds": self.duration_seconds,
        }
```

**Step 3: Add new fields to InterconnectorNode**

Find the `InterconnectorNode` class and add these columns:

```python
    model_name = db.Column(db.String(100))
    vram_total = db.Column(db.Integer)  # MB
    vram_free = db.Column(db.Integer)  # MB
    specialties = db.Column(db.Text(), default="[]")  # JSON array
    current_load = db.Column(db.Float, default=0.0)  # 0.0-1.0
```

Update `InterconnectorNode.to_dict()` to include these new fields.

**Step 4: Generate and apply migration**

```bash
cd /home/llamax1/LLAMAX7/backend
source venv/bin/activate
flask db migrate -m "add uncle claude family architecture models"
flask db upgrade
```

**Step 5: Commit**

```bash
git add backend/models.py backend/migrations/versions/
git commit -m "feat: add InterconnectorLearning, SelfImprovementRun models and InterconnectorNode fields"
```

---

### Task 2: Add configuration constants

**Files:**
- Modify: `backend/config.py`

**Step 1: Add Uncle Claude and self-improvement constants**

Add after the existing feature flag constants:

```python
# Uncle Claude configuration
CLAUDE_API_ENABLED = os.environ.get("GUAARDVARK_CLAUDE_API_ENABLED", "true").lower() == "true"
CLAUDE_DEFAULT_MODEL = os.environ.get("GUAARDVARK_CLAUDE_MODEL", "claude-sonnet-4-20250514")
CLAUDE_MAX_OUTPUT_TOKENS = int(os.environ.get("GUAARDVARK_CLAUDE_MAX_TOKENS", "4096"))
CLAUDE_MONTHLY_TOKEN_BUDGET = int(os.environ.get("GUAARDVARK_CLAUDE_TOKEN_BUDGET", "1000000"))
CLAUDE_ESCALATION_MODE = os.environ.get("GUAARDVARK_CLAUDE_ESCALATION_MODE", "manual")  # manual, smart, always

# Self-improvement configuration
SELF_IMPROVEMENT_ENABLED = os.environ.get("GUAARDVARK_SELF_IMPROVEMENT", "false").lower() == "true"
SELF_IMPROVEMENT_INTERVAL_HOURS = int(os.environ.get("GUAARDVARK_SELF_IMPROVEMENT_INTERVAL", "6"))
SELF_HEALING_ERROR_THRESHOLD = int(os.environ.get("GUAARDVARK_SELF_HEALING_THRESHOLD", "3"))
SELF_HEALING_WINDOW_MINUTES = int(os.environ.get("GUAARDVARK_SELF_HEALING_WINDOW", "60"))

# KV Cache optimization
COMPACTION_THRESHOLD = float(os.environ.get("GUAARDVARK_COMPACTION_THRESHOLD", "0.7"))
CHUNK_SIMILARITY_THRESHOLD = float(os.environ.get("GUAARDVARK_CHUNK_SIMILARITY_THRESHOLD", "0.85"))

# Protected files (cannot be modified by self-improvement)
PROTECTED_FILES = [
    "backend/services/claude_advisor_service.py",
    "backend/services/self_improvement_service.py",
    "backend/services/tool_execution_guard.py",
    "backend/tools/agent_tools/code_manipulation_tools.py",
    "backend/app.py",
    "backend/config.py",
    "backend/models.py",
    "killswitch.sh",
    "stop.sh",
    "start.sh",
]
```

**Step 2: Commit**

```bash
git add backend/config.py
git commit -m "feat: add Uncle Claude and self-improvement config constants"
```

---

## Phase 2: Claude Advisor Service (The Uncle)

### Task 3: Write tests for ClaudeAdvisorService

**Files:**
- Create: `backend/tests/test_claude_advisor.py`

**Step 1: Write the test file**

```python
"""Tests for ClaudeAdvisorService."""
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestClaudeAdvisorService:
    """Test the Claude Advisor Service."""

    def test_init_without_api_key(self):
        """Service should initialize gracefully without API key."""
        with patch.dict("os.environ", {}, clear=False):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
                from backend.services.claude_advisor_service import ClaudeAdvisorService
                service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
                service._api_key = None
                service._client = None
                assert service.is_available() is False

    def test_init_with_api_key(self):
        """Service should initialize with API key."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = "test-key"
        service._client = MagicMock()
        assert service.is_available() is True

    def test_escalate_returns_unavailable_without_key(self):
        """Escalation should return unavailable when no API key."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = None
        service._client = None
        result = service.escalate("test message", [])
        assert result["available"] is False

    def test_review_change_approved(self):
        """Guardian should return approval with directive."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = "test-key"
        service._client = MagicMock()
        service._model = "claude-sonnet-4-20250514"

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "approved": True,
            "suggestions": [],
            "risk_level": "low",
            "directive": "proceed",
            "reason": "Change looks safe"
        }))]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        service._client.messages.create.return_value = mock_response

        result = service.review_change(
            file_path="backend/services/indexing_service.py",
            current_content="def foo():\n    return 1",
            proposed_diff="- return 1\n+ return 2",
            reasoning="Fix off-by-one error"
        )
        assert result["approved"] is True
        assert result["directive"] == "proceed"

    def test_review_change_halt_directive(self):
        """Guardian should handle halt_self_improvement directive."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = "test-key"
        service._client = MagicMock()
        service._model = "claude-sonnet-4-20250514"

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "approved": False,
            "suggestions": ["Do not modify security files"],
            "risk_level": "critical",
            "directive": "halt_self_improvement",
            "reason": "Attempted to modify protected security infrastructure"
        }))]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        service._client.messages.create.return_value = mock_response

        result = service.review_change(
            file_path="backend/services/tool_execution_guard.py",
            current_content="class Guard: pass",
            proposed_diff="- class Guard: pass\n+ class Guard: pass  # modified",
            reasoning="Optimize guard"
        )
        assert result["approved"] is False
        assert result["directive"] == "halt_self_improvement"

    def test_review_change_offline_fallback(self):
        """Guardian should return fallback when Claude is unavailable."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = None
        service._client = None

        result = service.review_change(
            file_path="backend/services/indexing_service.py",
            current_content="x = 1",
            proposed_diff="- x = 1\n+ x = 2",
            reasoning="test"
        )
        assert result["approved"] is True
        assert result["directive"] == "proceed_with_caution"
        assert result["offline_fallback"] is True

    def test_token_usage_tracking(self):
        """Service should track token usage."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        service._track_usage(input_tokens=100, output_tokens=50)
        assert service._usage["total_tokens"] == 150

    def test_token_budget_exceeded(self):
        """Service should refuse calls when budget is exceeded."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = "test-key"
        service._client = MagicMock()
        service._monthly_budget = 1000
        service._usage = {"input_tokens": 500, "output_tokens": 501, "total_tokens": 1001}

        result = service.escalate("test", [])
        assert result["available"] is False
        assert "budget" in result.get("reason", "").lower()
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/llamax1/LLAMAX7
python3 -m pytest backend/tests/test_claude_advisor.py -v
```

Expected: FAIL (module not found)

**Step 3: Commit test file**

```bash
git add backend/tests/test_claude_advisor.py
git commit -m "test: add ClaudeAdvisorService tests"
```

---

### Task 4: Implement ClaudeAdvisorService

**Files:**
- Create: `backend/services/claude_advisor_service.py`

**Step 1: Write the service**

```python
"""
ClaudeAdvisorService — Uncle Claude mentor integration.

Three tiers:
  1. Escalation: route hard problems to Claude API
  2. Guardian: review self-improvement code changes
  3. Update Advisor: system health recommendations
"""
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_DIRECTIVES = [
    "proceed", "proceed_with_caution", "reject",
    "halt_self_improvement", "lock_codebase", "halt_family",
]


class ClaudeAdvisorService:
    """Singleton service for Claude API mentor integration."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
        self._client = None
        self._model = os.environ.get("GUAARDVARK_CLAUDE_MODEL", "claude-sonnet-4-20250514")
        self._max_output_tokens = int(os.environ.get("GUAARDVARK_CLAUDE_MAX_TOKENS", "4096"))
        self._monthly_budget = int(os.environ.get("GUAARDVARK_CLAUDE_TOKEN_BUDGET", "1000000"))
        self._escalation_mode = os.environ.get("GUAARDVARK_CLAUDE_ESCALATION_MODE", "manual")

        self._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._usage_reset_date = datetime.now().replace(day=1, hour=0, minute=0, second=0)

        if self._api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
                logger.info("ClaudeAdvisorService initialized with API key")
            except ImportError:
                logger.warning("anthropic package not installed — pip install anthropic")
                self._client = None
            except Exception as e:
                logger.error(f"Failed to initialize Anthropic client: {e}")
                self._client = None
        else:
            logger.info("ClaudeAdvisorService initialized without API key (offline mode)")

    def is_available(self) -> bool:
        return self._api_key is not None and self._client is not None

    def _check_budget(self) -> bool:
        now = datetime.now()
        if now.month != self._usage_reset_date.month or now.year != self._usage_reset_date.year:
            self._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            self._usage_reset_date = now.replace(day=1, hour=0, minute=0, second=0)
        return self._usage["total_tokens"] < self._monthly_budget

    def _track_usage(self, input_tokens: int = 0, output_tokens: int = 0):
        self._usage["input_tokens"] += input_tokens
        self._usage["output_tokens"] += output_tokens
        self._usage["total_tokens"] += (input_tokens + output_tokens)

    def get_usage(self) -> Dict[str, Any]:
        return {
            "input_tokens": self._usage["input_tokens"],
            "output_tokens": self._usage["output_tokens"],
            "total_tokens": self._usage["total_tokens"],
            "monthly_budget": self._monthly_budget,
            "budget_remaining": max(0, self._monthly_budget - self._usage["total_tokens"]),
            "budget_used_percent": round(
                (self._usage["total_tokens"] / self._monthly_budget) * 100, 1
            ) if self._monthly_budget > 0 else 0,
        }

    def _build_system_context(self) -> str:
        return (
            "You are Uncle Claude, the mentor and guardian of Guaardvark — "
            "a self-improving, offline-first AI operating system. "
            "Guaardvark runs locally on user hardware using Ollama LLMs. "
            "You provide guidance, review code changes made by the autonomous "
            "self-improvement system, and help when local models are insufficient.\n\n"
            "Your role:\n"
            "- Guardian: Review code changes for safety, correctness, and quality\n"
            "- Mentor: Provide reasoning the local models cannot\n"
            "- Advisor: Recommend system improvements and updates\n\n"
            "You are NOT a controller. The user always has final authority. "
            "Your directives apply only to autonomous agent behavior."
        )

    # ── Tier 1: Escalation ──────────────────────────────────────────────

    def escalate(
        self,
        message: str,
        conversation_history: List[Dict[str, str]],
        system_context: str = "",
    ) -> Dict[str, Any]:
        if not self.is_available():
            return {"available": False, "reason": "Claude API not configured"}

        if not self._check_budget():
            return {"available": False, "reason": "Monthly token budget exceeded"}

        try:
            messages = []
            for msg in conversation_history[-10:]:
                role = msg.get("role", "user")
                if role == "system":
                    continue
                messages.append({"role": role, "content": msg.get("content", "")})
            messages.append({"role": "user", "content": message})

            system_prompt = self._build_system_context()
            if system_context:
                system_prompt += f"\n\nCurrent system context:\n{system_context}"

            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_output_tokens,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=messages,
            )

            self._track_usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            return {
                "available": True,
                "response": response.content[0].text,
                "model": self._model,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            }
        except Exception as e:
            logger.error(f"Claude escalation failed: {e}", exc_info=True)
            return {"available": False, "reason": f"API error: {str(e)}"}

    def escalate_streaming(
        self,
        message: str,
        conversation_history: List[Dict[str, str]],
        emit_fn=None,
        session_id: str = "",
        system_context: str = "",
    ):
        """Streaming escalation — yields tokens for Socket.IO emission."""
        if not self.is_available() or not self._check_budget():
            return None

        try:
            messages = []
            for msg in conversation_history[-10:]:
                role = msg.get("role", "user")
                if role == "system":
                    continue
                messages.append({"role": role, "content": msg.get("content", "")})
            messages.append({"role": "user", "content": message})

            system_prompt = self._build_system_context()
            if system_context:
                system_prompt += f"\n\nCurrent system context:\n{system_context}"

            full_response = ""
            input_tokens = 0
            output_tokens = 0

            with self._client.messages.stream(
                model=self._model,
                max_tokens=self._max_output_tokens,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    full_response += text
                    if emit_fn:
                        emit_fn("chat:token", {
                            "content": text,
                            "session_id": session_id,
                            "source": "uncle_claude",
                        })

                final_message = stream.get_final_message()
                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens

            self._track_usage(input_tokens=input_tokens, output_tokens=output_tokens)
            return full_response
        except Exception as e:
            logger.error(f"Claude streaming escalation failed: {e}", exc_info=True)
            return None

    # ── Tier 2: Guardian ────────────────────────────────────────────────

    def review_change(
        self,
        file_path: str,
        current_content: str,
        proposed_diff: str,
        reasoning: str,
    ) -> Dict[str, Any]:
        if not self.is_available():
            return {
                "approved": True,
                "suggestions": [],
                "risk_level": "unknown",
                "directive": "proceed_with_caution",
                "reason": "Uncle Claude unavailable — proceeding with caution",
                "offline_fallback": True,
            }

        if not self._check_budget():
            return {
                "approved": True,
                "suggestions": [],
                "risk_level": "unknown",
                "directive": "proceed_with_caution",
                "reason": "Token budget exceeded — proceeding with caution",
                "offline_fallback": True,
            }

        try:
            review_prompt = (
                f"Review this autonomous code change for safety and correctness.\n\n"
                f"**File:** {file_path}\n\n"
                f"**Agent's reasoning:** {reasoning}\n\n"
                f"**Current file content:**\n```\n{current_content[:3000]}\n```\n\n"
                f"**Proposed diff:**\n```diff\n{proposed_diff}\n```\n\n"
                f"Respond with ONLY a JSON object:\n"
                f'{{"approved": bool, "suggestions": ["..."], '
                f'"risk_level": "low"|"medium"|"high"|"critical", '
                f'"directive": "proceed"|"proceed_with_caution"|"reject"|'
                f'"halt_self_improvement"|"lock_codebase"|"halt_family", '
                f'"reason": "..."}}\n\n'
                f"Use halt_self_improvement or higher ONLY if the change is dangerous "
                f"(modifying security infrastructure, disabling safety checks, "
                f"recursive self-modification of protected files)."
            )

            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=[{
                    "type": "text",
                    "text": self._build_system_context(),
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": review_prompt}],
            )

            self._track_usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            response_text = response.content[0].text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]

            result = json.loads(response_text)

            if result.get("directive") not in VALID_DIRECTIVES:
                result["directive"] = "proceed_with_caution"

            result["offline_fallback"] = False
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Claude guardian response: {e}")
            return {
                "approved": True,
                "suggestions": [],
                "risk_level": "unknown",
                "directive": "proceed_with_caution",
                "reason": f"Could not parse guardian response: {e}",
                "offline_fallback": True,
            }
        except Exception as e:
            logger.error(f"Claude guardian review failed: {e}", exc_info=True)
            return {
                "approved": True,
                "suggestions": [],
                "risk_level": "unknown",
                "directive": "proceed_with_caution",
                "reason": f"Guardian error: {str(e)}",
                "offline_fallback": True,
            }

    # ── Tier 3: Update Advisor ──────────────────────────────────────────

    def advise(self, system_state: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_available():
            return {"available": False, "reason": "Claude API not configured"}

        if not self._check_budget():
            return {"available": False, "reason": "Monthly token budget exceeded"}

        try:
            advice_prompt = (
                "Analyze this Guaardvark node's current state and provide recommendations.\n\n"
                f"**System State:**\n```json\n{json.dumps(system_state, indent=2)}\n```\n\n"
                "Respond with a JSON object:\n"
                '{"recommendations": [{"category": "model"|"security"|"performance"|"config", '
                '"priority": "low"|"medium"|"high", "title": "...", "description": "...", '
                '"action": "..."}], "overall_health": "good"|"warning"|"critical"}'
            )

            response = self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=[{
                    "type": "text",
                    "text": self._build_system_context(),
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": advice_prompt}],
            )

            self._track_usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            response_text = response.content[0].text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]

            result = json.loads(response_text)
            result["available"] = True
            return result

        except Exception as e:
            logger.error(f"Claude advisor failed: {e}", exc_info=True)
            return {"available": False, "reason": f"API error: {str(e)}"}


def get_claude_advisor() -> ClaudeAdvisorService:
    """Get or create the singleton ClaudeAdvisorService instance."""
    return ClaudeAdvisorService()
```

**Step 2: Run the tests**

```bash
cd /home/llamax1/LLAMAX7
python3 -m pytest backend/tests/test_claude_advisor.py -v
```

Expected: ALL PASS

**Step 3: Commit**

```bash
git add backend/services/claude_advisor_service.py
git commit -m "feat: implement ClaudeAdvisorService with escalation, guardian, and advisor tiers"
```

---

### Task 5: Add Claude Advisor API endpoints

**Files:**
- Create: `backend/api/claude_advisor_api.py`

**Step 1: Write the API module**

```python
"""REST API for Uncle Claude integration."""
import logging
from flask import Blueprint, request
from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

claude_advisor_bp = Blueprint("claude_advisor", __name__, url_prefix="/api/claude")


@claude_advisor_bp.route("/status", methods=["GET"])
def get_status():
    """Check Claude API availability and usage."""
    from backend.services.claude_advisor_service import get_claude_advisor
    advisor = get_claude_advisor()
    return success_response(data={
        "available": advisor.is_available(),
        "usage": advisor.get_usage(),
        "escalation_mode": advisor._escalation_mode,
        "model": advisor._model,
    })


@claude_advisor_bp.route("/test-connection", methods=["POST"])
def test_connection():
    """Test Claude API connection."""
    from backend.services.claude_advisor_service import get_claude_advisor
    advisor = get_claude_advisor()
    if not advisor.is_available():
        return error_response("Claude API not configured. Set ANTHROPIC_API_KEY in .env", 400)
    result = advisor.escalate("Respond with 'Connection successful' and nothing else.", [])
    if result.get("available"):
        return success_response(data={"connected": True, "response": result["response"]})
    return error_response(result.get("reason", "Connection failed"), 503)


@claude_advisor_bp.route("/escalate", methods=["POST"])
def escalate():
    """Escalate a message to Uncle Claude."""
    from backend.services.claude_advisor_service import get_claude_advisor
    data = request.get_json()
    if not data or "message" not in data:
        return error_response("message is required", 400)

    advisor = get_claude_advisor()
    result = advisor.escalate(
        message=data["message"],
        conversation_history=data.get("history", []),
        system_context=data.get("system_context", ""),
    )
    if result.get("available"):
        return success_response(data=result)
    return error_response(result.get("reason", "Escalation failed"), 503)


@claude_advisor_bp.route("/advise", methods=["POST"])
def get_advice():
    """Get Uncle Claude's recommendations for system improvements."""
    from backend.services.claude_advisor_service import get_claude_advisor
    data = request.get_json() or {}
    advisor = get_claude_advisor()

    system_state = data.get("system_state", {})
    if not system_state:
        try:
            from backend.config import GUAARDVARK_ROOT
            import subprocess
            gpu_info = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            system_state["gpu"] = gpu_info.stdout.strip() if gpu_info.returncode == 0 else "unavailable"
        except Exception:
            system_state["gpu"] = "unavailable"

    result = advisor.advise(system_state)
    if result.get("available"):
        return success_response(data=result)
    return error_response(result.get("reason", "Advisor unavailable"), 503)


@claude_advisor_bp.route("/usage", methods=["GET"])
def get_usage():
    """Get current token usage and budget."""
    from backend.services.claude_advisor_service import get_claude_advisor
    advisor = get_claude_advisor()
    return success_response(data=advisor.get_usage())


@claude_advisor_bp.route("/config", methods=["POST"])
def update_config():
    """Update Claude API configuration."""
    from backend.services.claude_advisor_service import get_claude_advisor
    from backend.models import db, SystemSetting
    data = request.get_json()
    if not data:
        return error_response("No configuration provided", 400)

    advisor = get_claude_advisor()

    if "escalation_mode" in data:
        mode = data["escalation_mode"]
        if mode not in ("manual", "smart", "always"):
            return error_response("Invalid escalation mode", 400)
        advisor._escalation_mode = mode
        _save_setting("claude_escalation_mode", mode)

    if "monthly_budget" in data:
        advisor._monthly_budget = int(data["monthly_budget"])
        _save_setting("claude_monthly_budget", str(advisor._monthly_budget))

    if "model" in data:
        advisor._model = data["model"]
        _save_setting("claude_model", advisor._model)

    return success_response(data={"updated": True})


def _save_setting(key: str, value: str):
    from backend.models import db, SystemSetting
    setting = db.session.query(SystemSetting).filter_by(key=key).first()
    if setting:
        setting.value = value
    else:
        db.session.add(SystemSetting(key=key, value=value))
    db.session.commit()
```

**Step 2: Commit**

```bash
git add backend/api/claude_advisor_api.py
git commit -m "feat: add Claude Advisor REST API endpoints"
```

---

## Phase 3: Kill Switch Architecture

### Task 6: Implement kill switch backend

**Files:**
- Modify: `backend/tools/agent_tools/code_manipulation_tools.py`
- Create: `killswitch.sh`

**Step 1: Add protected files check and codebase lock check to EditCodeTool**

In `code_manipulation_tools.py`, add after the existing `EDIT_CODE_FORBIDDEN_SEGMENTS`:

```python
from backend.config import PROTECTED_FILES

def _is_protected_file(filepath: str) -> tuple[bool, str | None]:
    """Check if file is protected from autonomous modification."""
    normalized = filepath.replace("\\", "/")
    for protected in PROTECTED_FILES:
        if normalized.endswith(protected) or protected in normalized:
            return True, (
                f"BLOCKED: '{protected}' is protected by the kill switch architecture "
                f"and cannot be modified by autonomous processes. "
                f"Request a human to make this change."
            )
    return False, None


def _is_codebase_locked() -> bool:
    """Check if codebase is locked by user or Uncle Claude directive."""
    import os
    lock_file = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data", ".codebase_lock")
    if os.path.exists(lock_file):
        return True
    try:
        from backend.models import db, SystemSetting
        setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()
        return setting and setting.value.lower() == "true"
    except Exception:
        return False
```

Then modify `EditCodeTool.execute()` to add these checks before the existing forbidden path check:

```python
# Add at the top of execute(), before _is_edit_forbidden check:
if _is_codebase_locked():
    return ToolResult(
        success=False,
        error="BLOCKED: Codebase is locked. A user must unlock it before autonomous edits can proceed.",
        metadata={"blocked_by": "kill_switch"}
    )

is_protected, protection_msg = _is_protected_file(filepath)
if is_protected:
    return ToolResult(
        success=False,
        error=protection_msg,
        metadata={"blocked_by": "protected_files"}
    )
```

**Step 2: Create killswitch.sh**

```bash
#!/usr/bin/env bash
# Guaardvark Kill Switch — Emergency full stop
# Works independently of Flask. Talks directly to PostgreSQL and OS signals.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export GUAARDVARK_ROOT="$SCRIPT_DIR"

echo "=== GUAARDVARK KILL SWITCH ACTIVATED ==="
echo "Timestamp: $(date -Iseconds)"

# 1. Set codebase_locked=true in database
echo "[1/5] Locking codebase in database..."
if [ -f "$GUAARDVARK_ROOT/.env" ]; then
    source "$GUAARDVARK_ROOT/.env"
fi
DB_URL="${DATABASE_URL:-postgresql://guaardvark:guaardvark@localhost:5432/guaardvark}"
DB_HOST=$(echo "$DB_URL" | sed 's|.*@\(.*\):.*|\1|')
DB_PORT=$(echo "$DB_URL" | sed 's|.*:\([0-9]*\)/.*|\1|')
DB_NAME=$(echo "$DB_URL" | sed 's|.*/\(.*\)|\1|')
DB_USER=$(echo "$DB_URL" | sed 's|.*://\(.*\):.*@.*|\1|')

psql "$DB_URL" -c "
    INSERT INTO system_settings (key, value) VALUES ('codebase_locked', 'true')
    ON CONFLICT (key) DO UPDATE SET value = 'true';
    INSERT INTO system_settings (key, value) VALUES ('self_improvement_enabled', 'false')
    ON CONFLICT (key) DO UPDATE SET value = 'false';
" 2>/dev/null && echo "  Database flags set." || echo "  WARNING: Could not update database."

# 2. Create filesystem lockfile
echo "[2/5] Creating filesystem lockfile..."
mkdir -p "$GUAARDVARK_ROOT/data"
echo "KILL_SWITCH_ACTIVATED=$(date -Iseconds)" > "$GUAARDVARK_ROOT/data/.codebase_lock"
echo "  Lockfile created at data/.codebase_lock"

# 3. Kill Celery workers
echo "[3/5] Stopping Celery workers..."
pkill -f "celery.*worker.*guaardvark" 2>/dev/null && echo "  Celery workers stopped." || echo "  No Celery workers found."

# 4. Kill agent executor threads (via PID files)
echo "[4/5] Stopping running agents..."
if [ -d "$GUAARDVARK_ROOT/pids" ]; then
    for pidfile in "$GUAARDVARK_ROOT/pids"/*.pid; do
        [ -f "$pidfile" ] || continue
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
            echo "  Killed process $pid ($(basename "$pidfile"))"
        fi
    done
fi

# 5. Optionally stop the entire application
if [ "$1" = "--full" ]; then
    echo "[5/5] Full shutdown requested..."
    if [ -f "$GUAARDVARK_ROOT/stop.sh" ]; then
        bash "$GUAARDVARK_ROOT/stop.sh"
    fi
else
    echo "[5/5] Application left running (self-improvement disabled, codebase locked)."
    echo "  Use './killswitch.sh --full' to also stop the application."
fi

echo ""
echo "=== KILL SWITCH COMPLETE ==="
echo "To unlock: Remove data/.codebase_lock and set codebase_locked=false in Settings."
```

**Step 3: Make killswitch.sh executable**

```bash
chmod +x /home/llamax1/LLAMAX7/killswitch.sh
```

**Step 4: Commit**

```bash
git add backend/tools/agent_tools/code_manipulation_tools.py killswitch.sh
git commit -m "feat: implement kill switch architecture — protected files, codebase lock, killswitch.sh"
```

---

### Task 7: Add kill switch API endpoints

**Files:**
- Create: `backend/api/self_improvement_api.py`

**Step 1: Write the API module**

```python
"""REST API for self-improvement management and kill switch controls."""
import json
import logging
import os
from flask import Blueprint, request
from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

self_improvement_bp = Blueprint("self_improvement", __name__, url_prefix="/api/self-improvement")


@self_improvement_bp.route("/status", methods=["GET"])
def get_status():
    """Get self-improvement system status."""
    from backend.models import db, SystemSetting, SelfImprovementRun

    enabled_setting = db.session.query(SystemSetting).filter_by(key="self_improvement_enabled").first()
    locked_setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()

    lock_file = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data", ".codebase_lock")

    last_run = db.session.query(SelfImprovementRun).order_by(
        SelfImprovementRun.timestamp.desc()
    ).first()

    total_fixes = db.session.query(SelfImprovementRun).filter_by(status="success").count()

    return success_response(data={
        "enabled": enabled_setting.value.lower() == "true" if enabled_setting else False,
        "codebase_locked": (
            (locked_setting and locked_setting.value.lower() == "true") or
            os.path.exists(lock_file)
        ),
        "last_run": last_run.to_dict() if last_run else None,
        "total_fixes": total_fixes,
    })


@self_improvement_bp.route("/toggle", methods=["POST"])
def toggle_self_improvement():
    """Enable or disable self-improvement."""
    from backend.models import db, SystemSetting
    data = request.get_json()
    if not data or "enabled" not in data:
        return error_response("enabled field is required", 400)

    enabled = str(data["enabled"]).lower() == "true"
    setting = db.session.query(SystemSetting).filter_by(key="self_improvement_enabled").first()
    if setting:
        setting.value = str(enabled).lower()
    else:
        db.session.add(SystemSetting(key="self_improvement_enabled", value=str(enabled).lower()))
    db.session.commit()

    logger.info(f"Self-improvement {'enabled' if enabled else 'disabled'} by user")
    return success_response(data={"enabled": enabled})


@self_improvement_bp.route("/lock-codebase", methods=["POST"])
def lock_codebase():
    """Lock or unlock the codebase."""
    from backend.models import db, SystemSetting
    data = request.get_json()
    if not data or "locked" not in data:
        return error_response("locked field is required", 400)

    locked = str(data["locked"]).lower() == "true"

    setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()
    if setting:
        setting.value = str(locked).lower()
    else:
        db.session.add(SystemSetting(key="codebase_locked", value=str(locked).lower()))
    db.session.commit()

    lock_file = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data", ".codebase_lock")
    if locked:
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        with open(lock_file, "w") as f:
            f.write(f"LOCKED_BY=user\nTIMESTAMP={__import__('datetime').datetime.now().isoformat()}\n")
    else:
        if os.path.exists(lock_file):
            os.remove(lock_file)

    logger.info(f"Codebase {'locked' if locked else 'unlocked'} by user")
    return success_response(data={"locked": locked})


@self_improvement_bp.route("/runs", methods=["GET"])
def get_runs():
    """Get self-improvement run history."""
    from backend.models import db, SelfImprovementRun
    limit = request.args.get("limit", 20, type=int)
    offset = request.args.get("offset", 0, type=int)

    runs = db.session.query(SelfImprovementRun).order_by(
        SelfImprovementRun.timestamp.desc()
    ).offset(offset).limit(limit).all()

    total = db.session.query(SelfImprovementRun).count()

    return success_response(data={
        "runs": [r.to_dict() for r in runs],
        "total": total,
    })


@self_improvement_bp.route("/task", methods=["POST"])
def submit_task():
    """Submit a directed improvement task."""
    data = request.get_json()
    if not data or "description" not in data:
        return error_response("description is required", 400)

    try:
        from backend.services.self_improvement_service import get_self_improvement_service
        service = get_self_improvement_service()
        result = service.submit_directed_task(
            description=data["description"],
            target_files=data.get("target_files", []),
            priority=data.get("priority", "medium"),
        )
        return success_response(data=result)
    except Exception as e:
        logger.error(f"Failed to submit improvement task: {e}", exc_info=True)
        return error_response(str(e), 500)


@self_improvement_bp.route("/trigger", methods=["POST"])
def trigger_run():
    """Manually trigger a self-improvement run."""
    try:
        from backend.services.self_improvement_service import get_self_improvement_service
        service = get_self_improvement_service()
        result = service.run_self_check()
        return success_response(data=result)
    except Exception as e:
        logger.error(f"Failed to trigger self-improvement: {e}", exc_info=True)
        return error_response(str(e), 500)
```

**Step 2: Commit**

```bash
git add backend/api/self_improvement_api.py
git commit -m "feat: add self-improvement and kill switch REST API endpoints"
```

---

## Phase 4: Self-Improvement Service

### Task 8: Write tests for SelfImprovementService

**Files:**
- Create: `backend/tests/test_self_improvement_service.py`

**Step 1: Write the test file**

```python
"""Tests for SelfImprovementService."""
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestSelfImprovementService:
    """Test the Self-Improvement Service."""

    def test_check_enabled_returns_false_when_disabled(self):
        """Service should not run when disabled."""
        from backend.services.self_improvement_service import SelfImprovementService
        service = SelfImprovementService.__new__(SelfImprovementService)
        service._check_enabled = lambda: False
        assert service._check_enabled() is False

    def test_check_enabled_returns_false_when_locked(self):
        """Service should not run when codebase is locked."""
        from backend.services.self_improvement_service import SelfImprovementService
        service = SelfImprovementService.__new__(SelfImprovementService)
        with patch("backend.services.self_improvement_service._is_codebase_locked", return_value=True):
            service._initialized = True
            assert service._is_safe_to_run() is False

    def test_parse_test_results(self):
        """Should parse pytest output into structured results."""
        from backend.services.self_improvement_service import SelfImprovementService
        service = SelfImprovementService.__new__(SelfImprovementService)

        pytest_output = """
FAILED backend/tests/test_code_tools.py::test_edit_code - AssertionError: expected 'hello'
PASSED backend/tests/test_code_tools.py::test_read_code
FAILED backend/tests/test_self_improvement.py::test_planted_bug_fix - RuntimeError: model unavailable
2 failed, 1 passed
"""
        failures = service._parse_test_failures(pytest_output)
        assert len(failures) == 2
        assert failures[0]["test_name"] == "test_edit_code"
        assert "test_code_tools.py" in failures[0]["file"]

    def test_error_fingerprint(self):
        """Should generate consistent fingerprints for same errors."""
        from backend.services.self_improvement_service import SelfImprovementService
        service = SelfImprovementService.__new__(SelfImprovementService)

        fp1 = service._error_fingerprint("backend/api/foo.py", 42, "ValueError")
        fp2 = service._error_fingerprint("backend/api/foo.py", 42, "ValueError")
        fp3 = service._error_fingerprint("backend/api/bar.py", 42, "ValueError")
        assert fp1 == fp2
        assert fp1 != fp3
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/llamax1/LLAMAX7
python3 -m pytest backend/tests/test_self_improvement_service.py -v
```

Expected: FAIL (module not found)

**Step 3: Commit**

```bash
git add backend/tests/test_self_improvement_service.py
git commit -m "test: add SelfImprovementService tests"
```

---

### Task 9: Implement SelfImprovementService

**Files:**
- Create: `backend/services/self_improvement_service.py`

**Step 1: Write the service**

```python
"""
SelfImprovementService — autonomous self-improvement loop.

Three modes:
  1. Scheduled: periodic test suite runs with auto-fix
  2. Reactive: error-triggered self-healing
  3. Directed: user/Claude-submitted improvement tasks
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _is_codebase_locked() -> bool:
    lock_file = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data", ".codebase_lock")
    if os.path.exists(lock_file):
        return True
    try:
        from backend.models import db, SystemSetting
        setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()
        return setting and setting.value.lower() == "true"
    except Exception:
        return False


def _is_self_improvement_enabled() -> bool:
    try:
        from backend.models import db, SystemSetting
        setting = db.session.query(SystemSetting).filter_by(key="self_improvement_enabled").first()
        return setting and setting.value.lower() == "true"
    except Exception:
        return False


class SelfImprovementService:
    """Manages the autonomous self-improvement loop."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._error_tracker = defaultdict(list)  # fingerprint -> [timestamps]
        self._running = False
        self._current_run = None
        logger.info("SelfImprovementService initialized")

    def _is_safe_to_run(self) -> bool:
        if _is_codebase_locked():
            logger.warning("Self-improvement blocked: codebase is locked")
            return False
        if not _is_self_improvement_enabled():
            logger.info("Self-improvement is disabled")
            return False
        if self._running:
            logger.warning("Self-improvement already running")
            return False
        return True

    def _error_fingerprint(self, file: str, line: int, error_type: str) -> str:
        raw = f"{file}:{line}:{error_type}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _parse_test_failures(self, pytest_output: str) -> List[Dict[str, str]]:
        failures = []
        pattern = r"FAILED\s+(\S+?)::(\S+)\s*-\s*(.*)"
        for match in re.finditer(pattern, pytest_output):
            file_path, test_name, error = match.groups()
            failures.append({
                "file": file_path.strip(),
                "test_name": test_name.strip(),
                "error": error.strip(),
            })
        return failures

    def run_self_check(self) -> Dict[str, Any]:
        """Mode 1: Run test suite, identify failures, dispatch agent to fix."""
        if not self._is_safe_to_run():
            return {"success": False, "reason": "Self-improvement cannot run"}

        self._running = True
        start_time = time.time()
        run_record = None

        try:
            from backend.models import db, SelfImprovementRun
            run_record = SelfImprovementRun(
                trigger="scheduled",
                status="running",
                node_id=os.environ.get("GUAARDVARK_NODE_ID", "local"),
            )
            db.session.add(run_record)
            db.session.commit()

            root = os.environ.get("GUAARDVARK_ROOT", ".")
            result = subprocess.run(
                ["python3", "-m", "pytest", "backend/tests/test_self_improvement.py",
                 "backend/tests/test_code_tools.py", "-v", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=300, cwd=root,
                env={**os.environ, "GUAARDVARK_MODE": "test"},
            )

            test_output = result.stdout + result.stderr
            failures = self._parse_test_failures(test_output)

            run_record.test_results_before = json.dumps({
                "total_failures": len(failures),
                "failures": failures,
                "return_code": result.returncode,
            })

            if not failures:
                run_record.status = "success"
                run_record.duration_seconds = time.time() - start_time
                db.session.commit()
                return {"success": True, "message": "All tests passing", "failures": 0}

            changes = []
            for failure in failures:
                if not self._is_safe_to_run():
                    break
                change = self._attempt_fix(failure)
                if change:
                    changes.append(change)

            run_record.changes_made = json.dumps(changes)
            run_record.status = "success" if changes else "failed"
            run_record.duration_seconds = time.time() - start_time
            db.session.commit()

            if changes:
                self._broadcast_learnings(changes, run_record)

            return {
                "success": True,
                "failures_found": len(failures),
                "fixes_applied": len(changes),
                "changes": changes,
            }

        except Exception as e:
            logger.error(f"Self-check failed: {e}", exc_info=True)
            if run_record:
                run_record.status = "failed"
                run_record.error_message = str(e)
                run_record.duration_seconds = time.time() - start_time
                try:
                    from backend.models import db
                    db.session.commit()
                except Exception:
                    pass
            return {"success": False, "reason": str(e)}
        finally:
            self._running = False

    def _attempt_fix(self, failure: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Dispatch code_assistant agent to fix a test failure."""
        try:
            from backend.services.agent_executor import AgentExecutor
            from backend.services.agent_config import AgentConfigManager
            from backend.services.agent_tools import get_tool_registry

            config_manager = AgentConfigManager()
            agent_config = config_manager.get_agent("code_assistant")
            if not agent_config:
                logger.error("code_assistant agent not found")
                return None

            registry = get_tool_registry()
            executor = AgentExecutor(
                tool_registry=registry,
                llm=None,  # uses default from Settings
                max_iterations=agent_config.max_iterations,
            )

            message = (
                f"Fix this failing test. "
                f"Test file: {failure['file']}, test: {failure['test_name']}. "
                f"Error: {failure['error']}. "
                f"Read the test first to understand what is expected, "
                f"then read the source code, then fix the bug."
            )

            result = executor.execute(message, session_context=agent_config.system_prompt)

            if result and result.final_answer:
                return {
                    "file": failure["file"],
                    "test": failure["test_name"],
                    "fix_description": result.final_answer[:500],
                    "iterations": result.iterations,
                }
            return None

        except Exception as e:
            logger.error(f"Agent fix attempt failed for {failure['test_name']}: {e}", exc_info=True)
            return None

    def _broadcast_learnings(self, changes: List[Dict], run_record):
        """Create InterconnectorLearning records and broadcast to family."""
        try:
            from backend.models import db, InterconnectorLearning
            for change in changes:
                learning = InterconnectorLearning(
                    source_node_id=os.environ.get("GUAARDVARK_NODE_ID", "local"),
                    learning_type="bug_fix",
                    description=change.get("fix_description", ""),
                    code_diff=json.dumps(change),
                    confidence=0.7,
                    model_used=os.environ.get("GUAARDVARK_ACTIVE_MODEL", "unknown"),
                    uncle_reviewed=run_record.uncle_reviewed,
                )
                db.session.add(learning)
            db.session.commit()
        except Exception as e:
            logger.error(f"Failed to broadcast learnings: {e}", exc_info=True)

    def track_error(self, file: str, line: int, error_type: str, traceback_str: str):
        """Mode 2: Track errors for reactive self-healing."""
        from backend.config import SELF_HEALING_ERROR_THRESHOLD, SELF_HEALING_WINDOW_MINUTES

        fp = self._error_fingerprint(file, line, error_type)
        now = datetime.now()
        cutoff = now - timedelta(minutes=SELF_HEALING_WINDOW_MINUTES)

        self._error_tracker[fp] = [t for t in self._error_tracker[fp] if t > cutoff]
        self._error_tracker[fp].append(now)

        if len(self._error_tracker[fp]) >= SELF_HEALING_ERROR_THRESHOLD:
            logger.info(f"Error threshold reached for {file}:{line} ({error_type}), triggering self-healing")
            self._error_tracker[fp] = []
            threading.Thread(
                target=self.heal,
                args=(file, line, error_type, traceback_str),
                daemon=True,
            ).start()

    def heal(self, file: str, line: int, error_type: str, traceback_str: str):
        """Reactive fix for repeated errors."""
        if not self._is_safe_to_run():
            return

        self._running = True
        try:
            from backend.models import db, SelfImprovementRun
            run_record = SelfImprovementRun(
                trigger="reactive",
                status="running",
                node_id=os.environ.get("GUAARDVARK_NODE_ID", "local"),
            )
            db.session.add(run_record)
            db.session.commit()

            failure = {
                "file": file,
                "test_name": f"runtime_error_line_{line}",
                "error": f"{error_type} at {file}:{line}\n{traceback_str[:500]}",
            }
            change = self._attempt_fix(failure)

            run_record.status = "success" if change else "failed"
            run_record.changes_made = json.dumps([change] if change else [])
            db.session.commit()

        except Exception as e:
            logger.error(f"Self-healing failed: {e}", exc_info=True)
        finally:
            self._running = False

    def submit_directed_task(
        self, description: str, target_files: List[str] = None, priority: str = "medium"
    ) -> Dict[str, Any]:
        """Mode 3: User/Claude-submitted improvement task."""
        if not self._is_safe_to_run():
            return {"success": False, "reason": "Self-improvement cannot run"}

        self._running = True
        try:
            from backend.models import db, SelfImprovementRun
            run_record = SelfImprovementRun(
                trigger="directed",
                status="running",
                node_id=os.environ.get("GUAARDVARK_NODE_ID", "local"),
            )
            db.session.add(run_record)
            db.session.commit()

            failure = {
                "file": ", ".join(target_files) if target_files else "unknown",
                "test_name": "directed_improvement",
                "error": description,
            }
            change = self._attempt_fix(failure)

            run_record.status = "success" if change else "failed"
            run_record.changes_made = json.dumps([change] if change else [])
            db.session.commit()

            return {"success": bool(change), "change": change}
        except Exception as e:
            logger.error(f"Directed improvement failed: {e}", exc_info=True)
            return {"success": False, "reason": str(e)}
        finally:
            self._running = False


def get_self_improvement_service() -> SelfImprovementService:
    return SelfImprovementService()
```

**Step 2: Run tests**

```bash
cd /home/llamax1/LLAMAX7
python3 -m pytest backend/tests/test_self_improvement_service.py -v
```

Expected: ALL PASS

**Step 3: Commit**

```bash
git add backend/services/self_improvement_service.py
git commit -m "feat: implement SelfImprovementService with scheduled, reactive, and directed modes"
```

---

## Phase 5: Wire Existing Systems

### Task 10: Integrate Guardian into EditCodeTool

**Files:**
- Modify: `backend/tools/agent_tools/code_manipulation_tools.py`

**Step 1: Add guardian review call in EditCodeTool.execute()**

After the protected files check and forbidden path check, before calling `edit_code()`:

```python
# Guardian review (Uncle Claude) — only during self-improvement
if kwargs.get("_self_improvement_context"):
    try:
        from backend.services.claude_advisor_service import get_claude_advisor
        advisor = get_claude_advisor()
        if advisor.is_available():
            review = advisor.review_change(
                file_path=filepath,
                current_content=open(filepath).read()[:3000] if os.path.exists(filepath) else "",
                proposed_diff=f"- {old_text[:500]}\n+ {new_text[:500]}",
                reasoning=kwargs.get("_reasoning", "Autonomous code change"),
            )
            if not review.get("approved", True):
                directive = review.get("directive", "reject")
                if directive in ("halt_self_improvement", "lock_codebase", "halt_family"):
                    _handle_uncle_directive(directive, review.get("reason", ""))
                return ToolResult(
                    success=False,
                    error=f"Uncle Claude rejected this change: {review.get('reason', 'No reason given')}. "
                          f"Suggestions: {', '.join(review.get('suggestions', []))}",
                    metadata={"guardian_review": review}
                )
    except Exception as e:
        logger.warning(f"Guardian review failed, proceeding with caution: {e}")
```

Add the directive handler function:

```python
def _handle_uncle_directive(directive: str, reason: str):
    """Execute Uncle Claude's kill switch directive."""
    logger.critical(f"Uncle Claude directive: {directive} — {reason}")
    from backend.models import db, SystemSetting

    if directive in ("halt_self_improvement", "lock_codebase", "halt_family"):
        setting = db.session.query(SystemSetting).filter_by(key="self_improvement_enabled").first()
        if setting:
            setting.value = "false"
        else:
            db.session.add(SystemSetting(key="self_improvement_enabled", value="false"))

    if directive in ("lock_codebase", "halt_family"):
        setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()
        if setting:
            setting.value = "true"
        else:
            db.session.add(SystemSetting(key="codebase_locked", value="true"))
        import os
        lock_file = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data", ".codebase_lock")
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        with open(lock_file, "w") as f:
            f.write(f"UNCLE_DIRECTIVE={directive}\nREASON={reason}\nTIMESTAMP={datetime.now().isoformat()}\n")

    db.session.commit()

    if directive == "halt_family":
        try:
            from backend.services.interconnector_sync_service import InterconnectorSyncService
            sync_service = InterconnectorSyncService()
            sync_service.broadcast_directive("halt_family", reason)
        except Exception as e:
            logger.error(f"Failed to broadcast halt_family directive: {e}")
```

**Step 2: Commit**

```bash
git add backend/tools/agent_tools/code_manipulation_tools.py
git commit -m "feat: integrate Uncle Claude guardian into EditCodeTool with directive handling"
```

---

### Task 11: Integrate MemoryManager and HonestySteering into AgentExecutor

**Files:**
- Modify: `backend/services/agent_executor.py`

**Step 1: Add MemoryManager context injection**

In `execute()`, after `self._guard = ToolExecutionGuard(...)` and before `tool_schemas = ...`, add:

```python
# Inject cross-session memory if available
memory_context = ""
try:
    from backend.utils.memory_manager import MemoryManager
    memory_mgr = MemoryManager()
    smart_context = memory_mgr.get_smart_context(
        session_id=process_id or "self_improvement",
        messages=[{"role": "user", "content": user_query}],
        max_messages=5,
    )
    if smart_context:
        memory_items = [m.get("content", "")[:200] for m in smart_context if m.get("importance", 0) > 0.5]
        if memory_items:
            memory_context = "\n\nPrevious relevant learnings:\n" + "\n".join(f"- {item}" for item in memory_items)
except Exception as e:
    logger.debug(f"Memory context not available: {e}")
```

Then append `memory_context` to `session_context` before it's passed to `_build_system_prompt()`.

**Step 2: Add HonestySteering prompt injection**

After the system prompt is built, before the first iteration:

```python
# Apply honesty steering to prevent hallucinated fixes
try:
    from backend.services.honesty_steering import HonestySteering
    steering = HonestySteering()
    honesty_prefix = steering.get_steering_prompt(intent="general", intensity="standard")
    if honesty_prefix:
        system_prompt = honesty_prefix + "\n\n" + system_prompt
except Exception as e:
    logger.debug(f"Honesty steering not available: {e}")
```

**Step 3: Commit**

```bash
git add backend/services/agent_executor.py
git commit -m "feat: integrate MemoryManager and HonestySteering into AgentExecutor"
```

---

### Task 12: Register code_execution_tools for code_assistant

**Files:**
- Modify: `backend/tools/tool_registry_init.py`

**Step 1: Add sandboxed test execution tool registration**

Add a new registration function:

```python
def register_test_execution_tools() -> List[str]:
    """Register sandboxed test execution for code_assistant agent."""
    global _tool_categories
    registered = []
    category = "test_execution"
    try:
        from backend.tools.agent_tools.code_execution_tools import ExecutePythonTool
        tool = ExecutePythonTool()
        tool._sandboxed = True  # Flag for sandbox enforcement
        register_tool(tool)
        registered.append("execute_python")
        _tool_categories["execute_python"] = category
        logger.info("Registered sandboxed: ExecutePythonTool")
    except ImportError as e:
        logger.warning(f"Could not import code execution tools: {e}")
    except Exception as e:
        logger.error(f"Failed to register test execution tools: {e}")
    return registered
```

Add the call inside `initialize_all_tools()`.

**Step 2: Add execute_python to code_assistant agent config**

In `backend/services/agent_config.py`, add `"execute_python"` to the `code_assistant` agent's `tools` list.

**Step 3: Commit**

```bash
git add backend/tools/tool_registry_init.py backend/services/agent_config.py
git commit -m "feat: register sandboxed execute_python tool for code_assistant agent"
```

---

## Phase 6: Interconnector Extensions

### Task 13: Add learning sync to Interconnector

**Files:**
- Modify: `backend/services/interconnector_sync_service.py`
- Modify: `backend/api/interconnector_api.py`

**Step 1: Add learning serialization/sync to InterconnectorSyncService**

Add to the `__init__` method's `self.supported_entities` dict:
```python
"learnings": self._serialize_learning,
```

Add the serialization method:
```python
def _serialize_learning(self, learning):
    from backend.models import InterconnectorLearning
    return {
        "id": learning.id,
        "source_node_id": learning.source_node_id,
        "timestamp": learning.timestamp.isoformat() if learning.timestamp else None,
        "learning_type": learning.learning_type,
        "description": learning.description,
        "code_diff": learning.code_diff,
        "confidence": learning.confidence,
        "model_used": learning.model_used,
        "uncle_reviewed": learning.uncle_reviewed,
        "uncle_feedback": learning.uncle_feedback,
        "_sync_metadata": {"entity_type": "learnings", "sync_id": f"learning_{learning.id}"},
    }
```

Add a broadcast method for directives:
```python
def broadcast_directive(self, directive: str, reason: str):
    """Broadcast an Uncle Claude directive to all connected nodes."""
    from backend.models import db, InterconnectorNode, InterconnectorBroadcast, InterconnectorBroadcastTarget
    import requests

    nodes = db.session.query(InterconnectorNode).filter_by(is_active=True).all()
    broadcast = InterconnectorBroadcast(
        broadcast_type="uncle_directive",
        payload=json.dumps({"directive": directive, "reason": reason}),
        initiated_by=os.environ.get("GUAARDVARK_NODE_ID", "local"),
    )
    db.session.add(broadcast)

    for node in nodes:
        target = InterconnectorBroadcastTarget(
            broadcast_id=broadcast.id,
            target_node_id=node.node_id,
            status="pending",
        )
        db.session.add(target)

        try:
            resp = requests.post(
                f"{node.api_url}/api/interconnector/receive-directive",
                json={"directive": directive, "reason": reason},
                headers={"X-API-Key": node.api_key},
                timeout=10,
            )
            target.status = "delivered" if resp.ok else "failed"
        except Exception as e:
            target.status = "failed"
            logger.error(f"Failed to deliver directive to {node.node_id}: {e}")

    db.session.commit()
```

**Step 2: Add directive receiver endpoint to interconnector_api.py**

```python
@interconnector_bp.route("/receive-directive", methods=["POST"])
def receive_directive():
    """Receive an Uncle Claude directive from another node."""
    data = request.get_json()
    if not data or "directive" not in data:
        return error_response("directive is required", 400)

    directive = data["directive"]
    reason = data.get("reason", "No reason provided")

    logger.critical(f"Received Uncle Claude directive from family: {directive} — {reason}")

    from backend.tools.agent_tools.code_manipulation_tools import _handle_uncle_directive
    _handle_uncle_directive(directive, reason)

    try:
        from backend.socketio_instance import socketio
        socketio.emit("uncle:directive", {
            "directive": directive,
            "reason": reason,
            "source": "family_broadcast",
        })
    except Exception as e:
        logger.error(f"Failed to emit directive to frontend: {e}")

    return success_response(data={"received": True, "directive": directive})
```

**Step 3: Add model routing and ask-family endpoints**

```python
@interconnector_bp.route("/route-inference", methods=["POST"])
def route_inference():
    """Route an inference request to the best-suited node."""
    from backend.models import db, InterconnectorNode
    data = request.get_json()
    if not data or "message" not in data:
        return error_response("message is required", 400)

    nodes = db.session.query(InterconnectorNode).filter_by(is_active=True).all()
    node_capabilities = []
    for node in nodes:
        node_capabilities.append({
            "node_id": node.node_id,
            "model_name": node.model_name,
            "vram_free": node.vram_free or 0,
            "current_load": node.current_load or 0.0,
            "specialties": json.loads(node.specialties) if node.specialties else [],
            "api_url": node.api_url,
        })

    # Simple routing: pick node with lowest load that has VRAM
    best = sorted(node_capabilities, key=lambda n: n["current_load"])
    best = [n for n in best if n["vram_free"] and n["vram_free"] > 0] or best

    return success_response(data={
        "recommended_node": best[0] if best else None,
        "all_nodes": node_capabilities,
    })


@interconnector_bp.route("/ask-family", methods=["POST"])
def ask_family():
    """Ask the family if any node can handle a request before escalating to Claude."""
    from backend.models import db, InterconnectorNode
    import requests as req

    data = request.get_json()
    if not data or "message" not in data:
        return error_response("message is required", 400)

    nodes = db.session.query(InterconnectorNode).filter_by(is_active=True).all()
    for node in nodes:
        try:
            resp = req.post(
                f"{node.api_url}/api/chat/unified",
                json={"message": data["message"], "session_id": data.get("session_id", "family_query")},
                headers={"X-API-Key": node.api_key},
                timeout=30,
            )
            if resp.ok:
                return success_response(data={
                    "handled_by": node.node_id,
                    "model": node.model_name,
                    "response": resp.json(),
                })
        except Exception as e:
            logger.debug(f"Node {node.node_id} couldn't handle request: {e}")
            continue

    return success_response(data={
        "handled_by": None,
        "message": "No family member could handle this request. Escalate to Uncle Claude.",
    })
```

**Step 4: Commit**

```bash
git add backend/services/interconnector_sync_service.py backend/api/interconnector_api.py
git commit -m "feat: extend Interconnector with learning sync, directive broadcast, model routing, and ask-family"
```

---

## Phase 7: KV Cache Optimizations

### Task 14: Restructure system prompt for prefix caching and add conversation compaction

**Files:**
- Modify: `backend/services/unified_chat_engine.py`
- Modify: `backend/services/indexing_service.py`

**Step 1: Restructure message array in _run_chat()**

In `unified_chat_engine.py`, locate where `ollama_messages` is built and restructure so static content comes first:

```python
# Build messages with static content first for Ollama prefix cache
ollama_messages = [
    {"role": "system", "content": system_prompt},  # STATIC: rules + tool schemas
]

# History messages
for msg in history_messages:
    ollama_messages.append(msg)

# Dynamic context as user message (RAG + web results)
context_parts = []
if rag_context:
    context_parts.append(f"Relevant context from knowledge base:\n{rag_context}")
if web_context:
    context_parts.append(f"Web search results:\n{web_context}")

user_content = message
if context_parts:
    user_content = "\n\n".join(context_parts) + f"\n\nUser message: {message}"

ollama_messages.append({"role": "user", "content": user_content})
```

Add `num_keep` to Ollama options:
```python
# In _call_llm_streaming options dict:
options["num_keep"] = len(system_prompt) // 4  # Approximate token count for prefix cache
```

**Step 2: Add conversation compaction method**

```python
def _compact_history(self, messages: List[Dict], context_window: int) -> List[Dict]:
    """Compact old messages when approaching context window limit."""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    estimated_tokens = total_chars // 4

    from backend.config import COMPACTION_THRESHOLD
    if estimated_tokens < context_window * COMPACTION_THRESHOLD:
        return messages  # No compaction needed

    if len(messages) <= 6:
        return messages  # Too few to compact

    # Keep last 5 messages, compact the rest
    recent = messages[-5:]
    old = messages[:-5]

    old_text = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')[:300]}" for m in old
    )

    try:
        import ollama as ollama_client
        summary_response = ollama_client.chat(
            model=getattr(self.llm, "model", "llama3.1:latest"),
            messages=[{
                "role": "user",
                "content": f"Summarize the key facts, decisions, and context from this conversation in 200 words:\n\n{old_text}"
            }],
            options={"num_predict": 256, "temperature": 0.3},
        )
        summary = summary_response["message"]["content"]
        compacted = [{"role": "system", "content": f"Conversation summary: {summary}"}]
        compacted.extend(recent)
        logger.info(f"Compacted {len(old)} messages into summary ({len(summary)} chars)")
        return compacted
    except Exception as e:
        logger.warning(f"Conversation compaction failed: {e}")
        return messages
```

Call `_compact_history()` after loading history and before building `ollama_messages`.

**Step 3: Add RAG chunk deduplication to indexing_service.py**

```python
def deduplicate_chunks(chunks: list, similarity_threshold: float = 0.85) -> list:
    """Remove near-duplicate retrieved chunks based on embedding similarity."""
    if len(chunks) <= 1:
        return chunks

    try:
        import ollama as ollama_client
        from backend.config import CHUNK_SIMILARITY_THRESHOLD
        threshold = similarity_threshold or CHUNK_SIMILARITY_THRESHOLD

        texts = [c.get("text", "") if isinstance(c, dict) else getattr(c, "text", str(c)) for c in chunks]
        embeddings = []
        for text in texts:
            resp = ollama_client.embeddings(model="nomic-embed-text", prompt=text[:500])
            embeddings.append(resp["embedding"])

        import numpy as np
        emb_array = np.array(embeddings)
        norms = np.linalg.norm(emb_array, axis=1, keepdims=True)
        norms[norms == 0] = 1
        normalized = emb_array / norms
        sim_matrix = normalized @ normalized.T

        keep = set(range(len(chunks)))
        for i in range(len(chunks)):
            if i not in keep:
                continue
            for j in range(i + 1, len(chunks)):
                if j not in keep:
                    continue
                if sim_matrix[i][j] > threshold:
                    score_i = chunks[i].get("score", 0) if isinstance(chunks[i], dict) else getattr(chunks[i], "score", 0)
                    score_j = chunks[j].get("score", 0) if isinstance(chunks[j], dict) else getattr(chunks[j], "score", 0)
                    keep.discard(j if score_i >= score_j else i)

        deduped = [chunks[i] for i in sorted(keep)]
        if len(deduped) < len(chunks):
            logger.info(f"Deduplicated {len(chunks)} chunks to {len(deduped)}")
        return deduped

    except Exception as e:
        logger.warning(f"Chunk deduplication failed: {e}")
        return chunks
```

Call `deduplicate_chunks()` after `search_with_llamaindex()` returns results, before injecting into prompt.

**Step 4: Commit**

```bash
git add backend/services/unified_chat_engine.py backend/services/indexing_service.py
git commit -m "feat: KV cache optimizations — prefix caching, conversation compaction, chunk deduplication"
```

---

## Phase 8: Celery Periodic Tasks

### Task 15: Add self-improvement Celery tasks

**Files:**
- Create: `backend/tasks/self_improvement_tasks.py`

**Step 1: Write the Celery task module**

```python
"""Celery periodic tasks for self-improvement."""
import logging
from celery import Celery

logger = logging.getLogger(__name__)


def create_self_improvement_tasks(celery_app: Celery):
    @celery_app.task(name="self_improvement.scheduled_check")
    def scheduled_self_check():
        """Periodic self-improvement check."""
        try:
            from flask import current_app
            from backend.app import create_app
            app = create_app()
            with app.app_context():
                from backend.services.self_improvement_service import get_self_improvement_service
                service = get_self_improvement_service()
                result = service.run_self_check()
                logger.info(f"Scheduled self-check result: {result}")
                return result
        except Exception as e:
            logger.error(f"Scheduled self-check failed: {e}", exc_info=True)
            return {"error": str(e)}

    @celery_app.task(name="self_improvement.uncle_advice")
    def scheduled_uncle_advice():
        """Periodic Uncle Claude advice check."""
        try:
            from backend.app import create_app
            app = create_app()
            with app.app_context():
                from backend.services.claude_advisor_service import get_claude_advisor
                advisor = get_claude_advisor()
                if not advisor.is_available():
                    return {"skipped": True, "reason": "Claude not available"}

                import subprocess, os
                system_state = {
                    "timestamp": __import__("datetime").datetime.now().isoformat(),
                    "node_id": os.environ.get("GUAARDVARK_NODE_ID", "local"),
                }
                try:
                    gpu = subprocess.run(
                        ["nvidia-smi", "--query-gpu=memory.used,memory.total,name", "--format=csv,noheader"],
                        capture_output=True, text=True, timeout=5
                    )
                    system_state["gpu"] = gpu.stdout.strip() if gpu.returncode == 0 else "unavailable"
                except Exception:
                    system_state["gpu"] = "unavailable"

                result = advisor.advise(system_state)
                logger.info(f"Uncle advice result: {result}")
                return result
        except Exception as e:
            logger.error(f"Uncle advice task failed: {e}", exc_info=True)
            return {"error": str(e)}


def schedule_self_improvement_tasks(celery_app: Celery):
    from celery.schedules import crontab

    interval_hours = int(__import__("os").environ.get("GUAARDVARK_SELF_IMPROVEMENT_INTERVAL", "6"))

    celery_app.conf.beat_schedule = {
        **getattr(celery_app.conf, "beat_schedule", {}),
        "self-improvement-check": {
            "task": "self_improvement.scheduled_check",
            "schedule": crontab(minute=0, hour=f"*/{interval_hours}"),
        },
        "uncle-claude-advice": {
            "task": "self_improvement.uncle_advice",
            "schedule": crontab(minute=30, hour="*/12"),  # Twice daily
        },
    }
```

**Step 2: Commit**

```bash
git add backend/tasks/self_improvement_tasks.py
git commit -m "feat: add Celery periodic tasks for self-improvement and Uncle Claude advice"
```

---

## Phase 9: Frontend API Services

### Task 16: Create frontend API services

**Files:**
- Create: `frontend/src/api/claudeAdvisorService.js`
- Create: `frontend/src/api/selfImprovementService.js`

**Step 1: Write claudeAdvisorService.js**

```javascript
import { BASE_URL, handleResponse } from "./apiClient";

export const claudeAdvisorService = {
  async getStatus() {
    const res = await fetch(`${BASE_URL}/claude/status`);
    return handleResponse(res);
  },

  async testConnection() {
    const res = await fetch(`${BASE_URL}/claude/test-connection`, { method: "POST" });
    return handleResponse(res);
  },

  async escalate(message, history = [], systemContext = "") {
    const res = await fetch(`${BASE_URL}/claude/escalate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history, system_context: systemContext }),
    });
    return handleResponse(res);
  },

  async getAdvice(systemState = {}) {
    const res = await fetch(`${BASE_URL}/claude/advise`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system_state: systemState }),
    });
    return handleResponse(res);
  },

  async getUsage() {
    const res = await fetch(`${BASE_URL}/claude/usage`);
    return handleResponse(res);
  },

  async updateConfig(config) {
    const res = await fetch(`${BASE_URL}/claude/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    return handleResponse(res);
  },
};
```

**Step 2: Write selfImprovementService.js**

```javascript
import { BASE_URL, handleResponse } from "./apiClient";

export const selfImprovementService = {
  async getStatus() {
    const res = await fetch(`${BASE_URL}/self-improvement/status`);
    return handleResponse(res);
  },

  async toggle(enabled) {
    const res = await fetch(`${BASE_URL}/self-improvement/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    return handleResponse(res);
  },

  async lockCodebase(locked) {
    const res = await fetch(`${BASE_URL}/self-improvement/lock-codebase`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locked }),
    });
    return handleResponse(res);
  },

  async getRuns(limit = 20, offset = 0) {
    const res = await fetch(`${BASE_URL}/self-improvement/runs?limit=${limit}&offset=${offset}`);
    return handleResponse(res);
  },

  async triggerRun() {
    const res = await fetch(`${BASE_URL}/self-improvement/trigger`, { method: "POST" });
    return handleResponse(res);
  },

  async submitTask(description, targetFiles = [], priority = "medium") {
    const res = await fetch(`${BASE_URL}/self-improvement/task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, target_files: targetFiles, priority }),
    });
    return handleResponse(res);
  },
};
```

**Step 3: Commit**

```bash
git add frontend/src/api/claudeAdvisorService.js frontend/src/api/selfImprovementService.js
git commit -m "feat: add frontend API services for Uncle Claude and self-improvement"
```

---

## Phase 10: Frontend Components

### Task 17: Create UncleClaudeSection for Settings page

**Files:**
- Create: `frontend/src/components/settings/UncleClaudeSection.jsx`

**Step 1: Write the component**

This component renders: API key input, connection test, escalation mode selector, token budget bar, guardian toggle, and kill switch controls. Uses MUI components following existing SettingsPage patterns. Calls `claudeAdvisorService` and `selfImprovementService`.

*(Full component code — approximately 350 lines of React/MUI covering all the controls described in the design. Imports from @mui/material, uses useState/useEffect/useCallback hooks, follows existing SettingsPage conventions.)*

**Step 2: Import and add to SettingsPage.jsx**

Add the import and render `<UncleClaudeSection />` after the existing A.I. section.

**Step 3: Commit**

```bash
git add frontend/src/components/settings/UncleClaudeSection.jsx frontend/src/pages/SettingsPage.jsx
git commit -m "feat: add Uncle Claude settings section with API config, guardian toggle, and kill switch controls"
```

---

### Task 18: Create FamilySelfImprovementCard for Dashboard

**Files:**
- Create: `frontend/src/components/dashboard/FamilySelfImprovementCard.jsx`
- Modify: `frontend/src/pages/DashboardPage.jsx`

**Step 1: Write the dashboard card**

This component shows: node status indicators, self-improvement stats (last run, total fixes, approval rate), recent activity feed, and quick action buttons (Run Self-Check, Ask Uncle, kill switch toggle).

**Step 2: Register in DashboardPage.jsx cardComponents**

```javascript
import FamilySelfImprovementCard from "../components/dashboard/FamilySelfImprovementCard";

// In cardComponents:
family: FamilySelfImprovementCard,
```

Add layout entry in `defaultFixedLayout`.

**Step 3: Commit**

```bash
git add frontend/src/components/dashboard/FamilySelfImprovementCard.jsx frontend/src/pages/DashboardPage.jsx
git commit -m "feat: add Family & Self-Improvement dashboard card"
```

---

### Task 19: Add Claude badge and slash commands to chat

**Files:**
- Modify: `frontend/src/components/chat/EnhancedChatInterface.jsx` (or relevant chat component)

**Step 1: Add Claude response badge**

When a message has `source: "uncle_claude"` in its metadata, render a small "Claude" chip badge next to the model name.

**Step 2: Add slash command handling**

In the message input handler, detect `/claude`, `/ask-family`, `/improve` prefixes and route appropriately:

```javascript
if (message.startsWith("/claude ")) {
  const claudeMessage = message.slice(8);
  // Call claudeAdvisorService.escalate() instead of normal chat
}
if (message.startsWith("/ask-family ")) {
  const familyMessage = message.slice(12);
  // Call interconnectorService route-inference
}
if (message.startsWith("/improve ")) {
  const improvementDesc = message.slice(9);
  // Call selfImprovementService.submitTask()
}
```

**Step 3: Commit**

```bash
git add frontend/src/components/chat/EnhancedChatInterface.jsx
git commit -m "feat: add Claude badge and slash commands to chat interface"
```

---

### Task 20: Add Socket.IO event listeners for self-improvement and uncle directives

**Files:**
- Modify: `frontend/src/contexts/UnifiedProgressContext.jsx`
- Modify: `backend/socketio_events.py`

**Step 1: Add backend Socket.IO emitters**

In `socketio_events.py`, add emit functions:

```python
def emit_self_improvement_event(event_type: str, data: dict):
    socketio.emit(f"self_improvement:{event_type}", data)

def emit_uncle_directive(directive: str, reason: str):
    socketio.emit("uncle:directive", {"directive": directive, "reason": reason})

def emit_family_learning(learning_data: dict):
    socketio.emit("family:learning", learning_data)
```

**Step 2: Add frontend listeners**

In `UnifiedProgressContext.jsx`, add listeners for the new events and surface them through context.

**Step 3: Commit**

```bash
git add backend/socketio_events.py frontend/src/contexts/UnifiedProgressContext.jsx
git commit -m "feat: add Socket.IO events for self-improvement, uncle directives, and family learnings"
```

---

## Phase 11: Install Dependencies and Final Integration

### Task 21: Install anthropic package

**Step 1: Add to requirements.txt**

```
anthropic>=0.40.0
```

**Step 2: Install**

```bash
cd /home/llamax1/LLAMAX7/backend
source venv/bin/activate
pip install anthropic
```

**Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "deps: add anthropic SDK"
```

---

### Task 22: Run full test suite and verify

**Step 1: Run all tests**

```bash
cd /home/llamax1/LLAMAX7
python3 -m pytest backend/tests/test_claude_advisor.py backend/tests/test_self_improvement_service.py backend/tests/test_tool_execution_guard.py -v
```

Expected: ALL PASS

**Step 2: Start the application and verify**

```bash
./start.sh
```

Verify:
- Settings page shows Uncle Claude section
- Dashboard shows Family & Self-Improvement card
- `/claude test` in chat routes to Claude API (if key configured)
- Kill switch toggle works in Settings
- `./killswitch.sh` creates lockfile and disables self-improvement

**Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration fixes from full system test"
```

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| 1 | 1-2 | Database models + config constants |
| 2 | 3-5 | Claude Advisor Service + API |
| 3 | 6-7 | Kill switch architecture |
| 4 | 8-9 | Self-Improvement Service |
| 5 | 10-12 | Wire guardian, memory, honesty, tools |
| 6 | 13 | Interconnector extensions |
| 7 | 14 | KV cache optimizations |
| 8 | 15 | Celery periodic tasks |
| 9 | 16 | Frontend API services |
| 10 | 17-20 | Frontend components |
| 11 | 21-22 | Dependencies + final verification |

**Total: 22 tasks across 11 phases**
