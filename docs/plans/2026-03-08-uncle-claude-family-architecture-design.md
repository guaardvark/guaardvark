# Uncle Claude Family Architecture — Design Document

**Date:** 2026-03-08
**Version:** Guaardvark 2.4.1 → 2.5.0
**Target:** Friday 2026-03-13
**Scope:** Claude API mentor integration, distributed self-improvement, KV cache optimization, kill switch safety architecture

---

## Vision

Guaardvark becomes the first self-improving, distributed AI operating system with a mentor architecture. Local "nephew" instances run offline, improve themselves, share learnings across the family via the Interconnector, and escalate to "Uncle Claude" when they need world-class reasoning, code review, or guidance. The user always has final authority via kill switches at every level.

---

## Architecture Overview

```
  ┌─────────────────────────────────────────────────┐
  │                  UNCLE CLAUDE                    │
  │           (Cloud Mentor / Guardian)              │
  │         Escalation · Review · Advising           │
  │         Kill Switch (halt_family directive)       │
  └──────────────┬──────────────┬───────────────────┘
                 │              │
        ┌────────┴──┐    ┌─────┴────────┐
        │  MASTER   │◄──►│   CLIENT     │
        │ Nephew A  │    │  Nephew B    │
        │ llama3.1  │    │  deepseek-r1 │
        │ 16GB GPU  │    │  32GB GPU    │
        └─────┬─────┘    └──────┬───────┘
              │                 │
        ┌─────┴─────────────────┴──────┐
        │         INTERCONNECTOR       │
        │   Learning Sync · Model      │
        │   Routing · Collective       │
        │   Escalation                 │
        └─────────────┬───────────────┘
                      │
              ┌───────┴───────┐
              │  CLIENT       │
              │  Nephew C     │
              │  phi3-mini    │
              │  Raspberry Pi │
              └───────────────┘
```

---

## Section 1: Claude Advisor Service (The Uncle)

### New file: `backend/services/claude_advisor_service.py`

Singleton service. Reads `ANTHROPIC_API_KEY` from `.env`. When no key is present, all methods return `{"available": false}` and nephews operate independently. Uses Claude prompt caching (`cache_control: {type: "ephemeral"}`) on Guaardvark system context block.

### Tier 1: Escalation (`escalate()`)

Called when local models signal low confidence or user explicitly requests Claude.

**Trigger conditions:**
- User message contains `/claude` or `/ask-uncle` prefix
- Local model response contains uncertainty markers
- Task complexity exceeds threshold (multi-step reasoning detected by orchestrator)
- Configurable auto-escalation toggle in Settings

**Flow:** Takes current conversation context (pruned to fit), sends to Claude, streams response back through Socket.IO `chat:token` pipeline. Frontend shows a subtle indicator (different badge) that Claude is responding.

**Cost control:** Token budget cap per request (configurable, default 4096 output tokens). Monthly usage tracking persisted to `SystemSetting` table. Hard limit option that falls back to local model when exceeded.

### Tier 2: Guardian (`review_change()`)

Called by `edit_code` tool before writing to disk during self-improvement.

**Flow:** Sends file's current content, proposed diff, and agent's reasoning to Claude. Returns:
```json
{
  "approved": bool,
  "suggestions": ["..."],
  "risk_level": "low" | "medium" | "high",
  "directive": "proceed" | "proceed_with_caution" | "reject" | "halt_self_improvement" | "lock_codebase" | "halt_family"
}
```

- If approved: edit proceeds
- If rejected: suggestions fed back to agent as observation, ReACT loop continues
- Offline fallback: edit proceeds with caution flag logged

**Bypass:** Changes to test files and non-critical paths can skip review (configurable allowlist).

### Tier 3: Update Advisor (`advise()`)

On-demand or Celery periodic task.

**Flow:** Collects system state (installed models, VRAM usage, index health, recent errors, dependency versions), sends to Claude asking for recommendations. Returns structured recommendations: model upgrades, security patches, config optimizations, feature suggestions. Results displayed on Dashboard "Family & Self-Improvement" card.

### Integration points:
- `unified_chat_engine.py` `_run_chat()` — escalation check after local model responds
- `agent_executor.py` `_execute_iteration()` — escalation for complex orchestrator tasks
- `code_manipulation_tools.py` `EditCodeTool.execute()` — guardian call before `edit_code()`
- `model_api.py` — advisor endpoint for model recommendations

---

## Section 2: Distributed Family (Interconnector Extensions)

### New capability: Learning Sync

New sync category added to the existing Interconnector.

**New model: `InterconnectorLearning`**
- `id`, `source_node_id`, `timestamp`
- `learning_type`: "bug_fix" | "optimization" | "pattern" | "model_insight" | "security"
- `description`: text
- `code_diff`: text (if applicable)
- `confidence`: float
- `model_used`: string
- `applied_by`: JSON array (which nodes have applied this)
- `uncle_reviewed`: bool
- `uncle_feedback`: text

**Flow:**
1. Nephew A's `code_assistant` agent fixes a bug
2. Guardian approves the fix → recorded as a `Learning`
3. Interconnector broadcasts learning to all connected nodes
4. Receiving nodes evaluate applicability and apply through own `edit_code` (also Guardian-gated)

### New capability: Distributed Model Routing

**New endpoint: `POST /api/interconnector/route-inference`**

Master node maintains model capability registry — mapping of `{node_id: {model, vram, specialties, avg_latency, current_load}}` updated via heartbeat.

Routing logic:
- Reasoning-heavy → node running `deepseek-r1`
- Fast/simple → Raspberry Pi running `phi3-mini`
- General purpose → handle locally
- Beyond all local models → collective escalation to Uncle Claude

New fields on `InterconnectorNode`: `model_name`, `vram_total`, `vram_free`, `specialties` (JSON), `current_load`.

### New capability: Collective Escalation

**New endpoint: `POST /api/interconnector/ask-family`**

Before escalating to Claude (costs tokens), ask family first:
1. Node encounters something it can't handle
2. Asks other nephews: "Can anyone handle this?"
3. If another node is better suited → routed there (free)
4. Only if no local node can handle it → escalate to Uncle Claude

**Priority order:** Local model → Family routing → Uncle Claude

---

## Section 3: Kill Switch Architecture

### Principle: Two independent kill switches. Either is sufficient. Both always available.

### Kill Switch 1: User-Side (Local, Always Works)

**Level 1 — Pause Self-Improvement:**
- Toggle on Settings/Dashboard: "Self-Improvement: Active / Paused"
- `SystemSetting("self_improvement_enabled", false)`
- `SelfImprovementService` checks flag before every action
- In-progress changes complete current `verify_change` step, then halt

**Level 2 — Lock Codebase:**
- Button on Settings/DevTools: "Lock Codebase"
- Writes lockfile at `data/.codebase_lock` AND `SystemSetting("codebase_locked", true)`
- `edit_code` hard-blocks before even calling Guardian
- Agent can still read/search/analyze — just can't write
- Unlocking requires user UI confirmation (not automatable by agent)

**Level 3 — Full Stop:**
- `./killswitch.sh` script:
  1. Sets `codebase_locked=true` in DB
  2. Sets `self_improvement_enabled=false`
  3. Disables all Celery beat schedules
  4. Kills all running agent executor threads
  5. Stops Celery workers
  6. Optionally stops entire application
- Talks directly to PostgreSQL via `psql` + sends SIGTERM to PIDs — works even if Flask is unresponsive

### Kill Switch 2: Uncle Claude (Remote, Requires Connectivity)

**Guardian Directive System:**

Claude's `review_change()` returns a `directive` field:
- `"proceed"` — approved
- `"proceed_with_caution"` — approved, flag for user review
- `"reject"` — this change blocked, agent continues other work
- `"halt_self_improvement"` — pause self-improvement on this node (triggers Level 1)
- `"lock_codebase"` — lock codebase on this node (triggers Level 2)
- `"halt_family"` — broadcast halt to ALL connected nodes via Interconnector

**`halt_family` flow:**
1. Receiving node sets own kill switch (Level 1 + Level 2)
2. Broadcasts `InterconnectorBroadcast` type `"uncle_directive"` payload `"halt_family"` to all nodes
3. Each node applies same lock, logs reason, notifies user via Dashboard
4. Only the user on each node can unlock — Claude can shut down but can't restart

### Tamper-Proof Guarantee

- `killswitch.sh`, `stop.sh` added to `edit_code` path blocklist
- `PROTECTED_FILES` list in `code_manipulation_tools.py`: `SelfImprovementService`, `ClaudeAdvisorService`, `ToolExecutionGuard`, kill switch scripts
- Any `edit_code` targeting protected files is auto-rejected without calling Guardian
- Agent sees: "BLOCKED: This file is protected by the kill switch architecture and cannot be modified by autonomous processes."

### Absolute Rule (Hardcoded)

The user can always override Claude. Claude's directives apply to autonomous behavior only — never prevents the user from operating their own system. Uncle Claude is a guardian, not a warden.

---

## Section 4: Self-Improvement Loop (The Autonomous Nephew)

### New file: `backend/services/self_improvement_service.py`

### Mode 1: Scheduled Self-Check (Celery Periodic Task)

Configurable interval (default every 6 hours):
1. Runs self-improvement test suite
2. Collects failures as `{test_name, error, file, line}`
3. Dispatches `code_assistant` agent per failure (read test → read source → edit → verify)
4. Guardian gate on every proposed fix (if Claude available; proceeds with caution flag if offline)
5. Records `InterconnectorLearning` → broadcast to family
6. Re-runs test to confirm fix
7. Logs to `logs/self_improvement.log` + `SelfImprovementRun` DB record

### Mode 2: Reactive Self-Healing

- Error tracking middleware in `app.py` counts exceptions by fingerprint (file + line + exception type)
- Same error 3+ times in 1 hour → triggers `SelfImprovementService.heal(error_context)`
- Same Guardian → Learning → verification cycle

### Mode 3: Directed Improvement

- `POST /api/self-improvement/task` with `{description, target_files[], priority}`
- Orchestrator decomposes complex tasks into DAG steps
- Each step through `code_assistant` agent, full Guardian gate
- Results on Dashboard

### Wiring Missing Connections

**MemoryManager → AgentExecutor:**
- Before building system prompt, load `MemoryManager(session_id).get_relevant_context()`
- Inject as "Previous learnings relevant to this task:"
- Cross-session memory for the agent

**HonestySteering → Agent Pipelines:**
- Wrap system prompt in both `unified_chat_engine.py` and `agent_executor.py`:
  `system_prompt = honesty_steering.get_enhanced_system_prompt(base_prompt)`
- Prevents hallucinated fixes or false success claims

**code_execution_tools → Tool Registry:**
- Register `execute_python` for `code_assistant` agent only
- Sandboxed to `backend/tests/` directory, pytest commands only
- Agent can run tests to verify own fixes within ReACT loop

### New DB Model: `SelfImprovementRun`
- `id`, `timestamp`, `node_id`
- `trigger`: "scheduled" | "reactive" | "directed" | "family_learning"
- `status`: "running" | "success" | "failed" | "blocked_by_guardian"
- `test_results_before`: JSON
- `test_results_after`: JSON
- `changes_made`: JSON array of `{file, diff}`
- `uncle_reviewed`: bool
- `uncle_feedback`: text
- `learning_id`: FK to `InterconnectorLearning`

---

## Section 5: KV Cache and Context Optimization

### Optimization 1: Ollama Prefix Cache Exploitation

Restructure message array in `unified_chat_engine.py` so static content comes first:
```
[SYSTEM]  ← rules text + tool schemas (STATIC, cacheable)
[USER: context_injection]  ← RAG context + web results (DYNAMIC)
[history messages...]
[USER: actual message]
```

Add `num_keep` parameter to Ollama options dict to lock system prompt prefix in KV cache.

### Optimization 2: Conversation Compaction

New method `_compact_history()` in `unified_chat_engine.py`:
- Trigger at 70% context window utilization
- Oldest N messages → summarized by local model into 200-word summary
- Replaced with single `[SYSTEM: conversation_summary]` message
- Preserves semantic weight in less token space

### Optimization 3: RAG Context Deduplication

New method `deduplicate_chunks()` in `indexing_service.py`:
- Pairwise embedding cosine similarity on retrieved chunks
- Chunks >85% similar → merged (keep higher-scored, append unique sentences from lower)
- Same information density, fewer tokens

### Optimization 4: Claude Prompt Caching

Use `cache_control: {"type": "ephemeral"}` on:
- Guaardvark system context block (architecture, node config, tool schemas)
- Large RAG context blocks sent for escalation
- First call pays full price, subsequent calls ~90% token cost reduction

### New config constants:
- `COMPACTION_THRESHOLD = 0.7`
- `CHUNK_SIMILARITY_THRESHOLD = 0.85`

---

## Section 6: Frontend Integration

### Settings Page: "Uncle Claude" Section
- API Key field (password-masked, saved to `.env` + `SystemSetting` encrypted)
- Connection test button
- Escalation mode: "Manual only" | "Smart" | "Always"
- Token budget with usage bar
- Guardian toggle
- Kill Switch controls: Self-Improvement toggle, Lock Codebase button, node status

### Dashboard: "Family & Self-Improvement" Card
- Node status ring (connected nephews with model names, online/offline/locked)
- Self-improvement stats (last run, fixes applied, learnings shared, Uncle approval rate)
- Recent activity feed (last 5 events)
- Quick actions: "Run Self-Check Now", "Ask Uncle for Advice", kill switch toggle

### Chat Interface
- Claude response badge (distinguishes Uncle Claude from local model)
- New slash commands: `/claude`, `/ask-family`, `/improve`
- Family routing indicator

### DevTools
- Self-Improvement Log tab (full history, diff viewer, Claude feedback, filters)
- Family Network tab (node graph, per-node stats, learning/broadcast history)

### New Frontend API Services
- `claudeAdvisorService.js` — test connection, get usage, escalate, get recommendations
- `selfImprovementService.js` — get status, trigger run, submit task, get logs, kill switch controls

### New Socket.IO Events
- `self_improvement:started`, `self_improvement:progress`, `self_improvement:completed`, `self_improvement:blocked`
- `uncle:directive`
- `family:learning`, `family:node_status`

---

## New Files Summary

### Backend
- `backend/services/claude_advisor_service.py` — Uncle Claude integration (3 tiers)
- `backend/services/self_improvement_service.py` — autonomous improvement loop (3 modes)
- `backend/api/claude_advisor_api.py` — REST endpoints for Claude interaction
- `backend/api/self_improvement_api.py` — REST endpoints for self-improvement management
- `backend/tasks/self_improvement_tasks.py` — Celery periodic tasks
- `killswitch.sh` — emergency full stop script

### Frontend
- `frontend/src/api/claudeAdvisorService.js` — Claude API client
- `frontend/src/api/selfImprovementService.js` — Self-improvement API client
- `frontend/src/components/dashboard/FamilySelfImprovementCard.jsx` — Dashboard card
- `frontend/src/components/settings/UncleClaudeSection.jsx` — Settings section

### Database Migrations
- `InterconnectorLearning` model
- `SelfImprovementRun` model
- New fields on `InterconnectorNode` (model_name, vram_total, vram_free, specialties, current_load)

### Modified Files
- `backend/services/unified_chat_engine.py` — escalation, prompt restructuring, history compaction
- `backend/services/agent_executor.py` — MemoryManager + HonestySteering integration, escalation
- `backend/tools/agent_tools/code_manipulation_tools.py` — Guardian gate, protected files, kill switch blocklist
- `backend/services/indexing_service.py` — chunk deduplication
- `backend/services/interconnector_sync_service.py` — learning sync, model registry, collective escalation
- `backend/api/interconnector_api.py` — route-inference, ask-family endpoints
- `backend/tools/tool_registry_init.py` — register code_execution_tools for code_assistant
- `backend/config.py` — new constants
- `backend/models.py` — new models
- `backend/app.py` — error tracking middleware
- `frontend/src/pages/SettingsPage.jsx` — Uncle Claude section
- `frontend/src/pages/DashboardPage.jsx` — Family card
- `frontend/src/pages/DevToolsPage.jsx` — Self-improvement log, Family network tabs
- `frontend/src/components/chat/EnhancedChatInterface.jsx` — Claude badge, slash commands
