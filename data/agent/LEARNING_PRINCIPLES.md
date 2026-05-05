# Agent Learning Principles

This document is the contract for any code that stores, retrieves, or
distills knowledge for the screen-control agent. It is short on purpose.
If you're tempted to add an exception, re-read it before writing the code.

## The principle

**The agent has eyes (vision model), hands (cursor), and a brain (LLM).
Stored knowledge describes WHAT to look for and WHAT it does. Vision
finds WHERE on the current frame. Knowledge never includes pixel
coordinates.**

Layouts shift. Buttons get redrawn. Websites redesign. Windows move.
Files rename. DPI changes. Themes swap. Anything cached as `(x, y)` is
a time bomb that fires the moment the environment moves under it.

## What this means in practice

**Stored knowledge** — `self_knowledge_compact.md`, `recipes.json`,
`example_traces.json`, `AgentMemory` rows of type `lesson_summary`,
any future lesson/skill files — must be expressible to a vision model
as **"find this and do that"**.

Examples that follow the principle:
- "the orange Firefox flame icon in the top-left Shortcuts panel" ✓
- "the chat input field with placeholder text 'Type your message...'" ✓
- "the Send button below the comment box" ✓
- "anywhere on the empty desktop background" ✓
- URL strings — they are stable semantic addresses, not pixel positions ✓

Examples that violate the principle:
- `"x": 92, "y": 103` ✗
- "the chat input is at y=660" ✗
- "click at (640, 360)" ✗
- "the address bar is at y=20-40" ✗
- "avoid the taskbar at y=690-720" → say "the bottom edge of the screen
  contains system controls, not page content" ✗ → ✓

## Where coordinates may legitimately appear

- **As output from the vision model**, fresh per frame. The model says
  "the icon is at (892, 145)"; the cursor goes there; nothing is stored.
- **In servo telemetry** — `servo_archive.jsonl` records what coordinates
  vision proposed and whether the click succeeded. That's a learning
  signal about vision-model accuracy, not a memory of where things sit.
- **In code that operates on the rendered screen** — code that parses a
  screenshot, draws annotations, interprets DOM bounding boxes. None of
  that is "knowledge" stored for cross-session reuse; it's per-call work.

## Where coordinates must NOT appear

- `data/agent/self_knowledge_compact.md`
- `data/agent/self_knowledge.md` (legacy doc — being weaned)
- `data/agent/recipes.json` `click` steps (use `target_description`)
- `data/agent/example_traces.json`
- Any new lesson, pearl, or memory the agent saves about itself
- Any file the End-Lesson distiller writes
- Any file that gets injected into the agent's prompt at decision time

## When you're tempted

- "But this coordinate works today." It rots tomorrow. The point of
  the vision model is not to confirm what we already cached.
- "But vision is slower." Yes. The vision model finding the Firefox icon
  takes ~5 seconds; a cached `(92, 103)` takes microseconds. The slowness
  is the price of resilience. Optimize separately, not by caching.
- "But the user said it's at (92, 103)." Then teach the agent to look for
  what's at that location ("the orange Firefox flame icon in the
  top-left Shortcuts panel"), not the location.
- "But this is the agent's own UI, it never moves." The Shortcuts panel
  is configured by tint2; tint2 config can change; it has changed before;
  it will change again.

## How to add new knowledge

1. Write the description as a vision query: "I see [X] — find it."
2. If you can't describe it that way, you don't have knowledge — you
   have a coordinate. Stop.
3. Test by deleting any cached coordinates and re-running the task. The
   vision model should succeed cold.

This is the contract. Phase 2 (lesson distillation, skill induction,
auto-recipe-generation) lands on top of it. Don't break it.
