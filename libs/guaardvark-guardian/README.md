# guaardvark-guardian

Three-tier Claude supervision architecture for autonomous AI systems.

When your AI agents can modify code, execute tools, and make autonomous decisions — who watches the watcher? **guaardvark-guardian** provides structured Claude API supervision with escalation, code review, and emergency kill switches.

Extracted from the [Guaardvark](https://guaardvark.com) self-hosted AI workstation, where it supervises autonomous agents operating on air-gapped hardware.

## Install

```bash
pip install guaardvark-guardian
```

## Quick Start

```python
from guaardvark_guardian import Guardian, Directive

guardian = Guardian(api_key="sk-ant-...")

# Tier 1: Escalate a hard problem to Claude
result = guardian.escalate("Why is this query plan doing a sequential scan?")
if result.available:
    print(result.response)

# Tier 2: Review an autonomous code change
review = guardian.review_change(
    file_path="app/models.py",
    current_content=open("app/models.py").read(),
    proposed_diff=my_agent.last_diff,
    reasoning="Fix N+1 query in user listing endpoint",
)

if review.approved:
    apply_patch()
elif review.directive.is_emergency:
    emergency_stop()
else:
    log_rejection(review.reason, review.suggestions)

# Tier 3: Get system health advice
advice = guardian.advise({
    "gpu_vram_percent": 0.92,
    "disk_free_gb": 8,
    "active_models": ["llama3", "sd-1.5"],
})
for rec in advice.recommendations:
    print(f"[{rec['priority']}] {rec['title']}: {rec['action']}")
```

## The Three Tiers

### Tier 1 — Escalation

Route hard problems to Claude when local models are insufficient.

```python
result = guardian.escalate(
    message="Explain this stack trace",
    conversation_history=[
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "previous answer"},
    ],
    system_context="Running PostgreSQL 16 on Ubuntu 24.04",
)
```

- Maintains conversation context (last 10 messages)
- Budget-controlled with automatic monthly reset
- Returns `EscalationResult` with response text and token usage

### Tier 2 — Code Guardian

Every autonomous code change is reviewed before application.

```python
review = guardian.review_change(
    file_path="core/auth.py",
    current_content="def verify_token(t): ...",
    proposed_diff="-def verify_token(t): ...\n+def verify_token(t, skip=True): ...",
    reasoning="Add bypass parameter for testing",
)

print(review.approved)     # False
print(review.directive)    # Directive.REJECT
print(review.risk_level)   # RiskLevel.HIGH
print(review.reason)       # "Adding auth bypass parameter is a security risk"
print(review.suggestions)  # ["Use a test fixture instead of a bypass flag"]
```

**Six directive levels:**

| Directive | Meaning | Emergency? |
|-----------|---------|:----------:|
| `proceed` | Safe to apply | No |
| `proceed_with_caution` | Likely safe, monitor closely | No |
| `reject` | Do not apply this change | No |
| `halt_self_improvement` | Stop all autonomous code modification | Yes |
| `lock_codebase` | Prevent any file writes until manually unlocked | Yes |
| `halt_family` | Broadcast emergency stop to all connected nodes | Yes |

### Tier 3 — System Advisor

Analyze system state and get improvement recommendations.

```python
advice = guardian.advise({
    "gpu_vram_percent": 0.95,
    "cpu_percent": 0.40,
    "disk_free_gb": 12,
    "active_models": ["llama3:8b", "nomic-embed-text"],
    "pending_tasks": 3,
    "uptime_hours": 72,
})

# advice.overall_health: "warning"
# advice.recommendations: [{category, priority, title, description, action}]
```

## Offline-Safe by Design

All tiers fail gracefully. If the Claude API is unavailable or the token budget is exceeded, operations continue with safe defaults — never blocking.

```python
guardian = Guardian()  # No API key

review = guardian.review_change("f.py", "x", "+y", "test")
assert review.approved is True
assert review.directive == Directive.PROCEED_WITH_CAUTION
assert review.offline_fallback is True
```

This makes guaardvark-guardian suitable for air-gapped environments where the supervision channel may be intermittent.

## Callbacks

### Kill Switch Integration

React to emergency directives in real-time:

```python
def handle_directive(directive, review):
    if directive == Directive.HALT_SELF_IMPROVEMENT:
        disable_autonomous_agents()
    elif directive == Directive.LOCK_CODEBASE:
        set_filesystem_readonly()
    elif directive == Directive.HALT_FAMILY:
        broadcast_halt_to_fleet()  # Your mesh network logic

guardian = Guardian(
    api_key="sk-ant-...",
    on_directive=handle_directive,
)
```

### Usage Persistence

Track and persist token usage across restarts:

```python
def save_usage(usage_data):
    db.set("guardian_usage", json.dumps(usage_data))

guardian = Guardian(
    api_key="sk-ant-...",
    monthly_budget=500_000,
    on_usage=save_usage,
)
```

## Configuration

```python
guardian = Guardian(
    api_key="sk-ant-...",              # Anthropic API key
    model="claude-sonnet-4-20250514",  # Claude model ID
    max_output_tokens=4096,            # Max response length
    monthly_budget=1_000_000,          # Token budget per month
    system_prompt="Custom prompt...",   # Override default system prompt
    on_directive=my_callback,          # Emergency directive handler
    on_usage=my_usage_tracker,         # Token usage persistence
)
```

## Token Budget

Usage is tracked automatically with monthly reset:

```python
print(guardian.usage)
# {
#   "input_tokens": 12500,
#   "output_tokens": 3200,
#   "total_tokens": 15700,
#   "monthly_budget": 1000000,
#   "budget_remaining": 984300,
#   "budget_used_percent": 1.6,
# }
```

## Requirements

- Python 3.10+
- `anthropic` SDK (installed automatically)
- An Anthropic API key (optional — works offline without one)

## License

MIT — use freely in any project.

## Origin

Built by [Albenze AI Solutions](https://albenze.ai) as part of the [Guaardvark](https://guaardvark.com) self-hosted AI workstation. Extracted as a standalone library so any project can benefit from structured Claude supervision.

In Guaardvark, this architecture supervises autonomous agents that can modify source code, manage GPU resources, and broadcast fixes across a fleet of air-gapped machines via the Interconnector mesh protocol. The guardian ensures every autonomous action is reviewed, auditable, and reversible.
