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
- "the Firefox icon on the desktop (orange flame graphic)" ✓
- "the chat input field with placeholder text 'Type your message...'" ✓
- "the Send button below the comment box" ✓
- "anywhere on the empty desktop background" ✓
- URL strings — they are stable semantic addresses, not pixel positions ✓

Examples that violate the principle:
- `"x": 92, "y": 103` ✗
- "the chat input is at y=660" ✗
- "click at (640, 360)" ✗
- "the address bar at the top of the screen, approximately y=20-40" ✗
  → "the address bar at the top of the browser window" ✓
- "avoid the taskbar at y=690-720" → "the bottom edge of the screen
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
- `data/agent/self_knowledge.md`
- `data/agent/recipes.json` `click` steps (use `target_description`)
- `data/agent/example_traces.json`
- Any new lesson, pearl, or memory the agent saves about itself
- Any file the End-Lesson distiller writes
- Any file that gets injected into the agent's prompt at decision time

## When you're tempted

- "But this coordinate works today." It rots tomorrow. The point of
  the vision model is not to confirm what we already cached.
- "But vision is slower." Yes. The vision model finding the Firefox
  icon takes a few seconds; a cached coordinate takes microseconds.
  The slowness is the price of resilience. Optimize separately, not
  by caching.
- "But the user said it's at this position." Then teach the agent to
  look for what's at that position ("the Firefox icon on the desktop,
  with a flame graphic"), not the position itself.
- "But this is the agent's own UI, it never moves." Desktop icons
  reflow on resolution change, theme swap, or icon-set update; XFCE
  config can be edited; what's on the desktop today may shift tomorrow.
  Describe what you see, not where it sat last time.

## How to add new knowledge

1. Write the description as a vision query: "I see [X] — find it."
2. If you can't describe it that way, you don't have knowledge — you
   have a coordinate. Stop.
3. Test by deleting any cached coordinates and re-running the task. The
   vision model should succeed cold.

## Short labels for the servo, rich context for the brain

Two roles, two budgets:

- **`target_description` in recipes/lessons** is the vision model's
  *detection query*. It must be short and conventional ("Firefox icon",
  "chat input field", "Send button"). Empirically (2026-05-05), small
  VLMs like qwen3-vl:2b emit clean detection JSON for short labels
  with one distinctive adjective and switch to prose (which the parser
  can't read) when given verbose multi-clause descriptions.
- **Knowledge files** like `self_knowledge_compact.md` carry the rich
  context the LLM needs to *reason* about the environment ("a column
  of icons on the left side of the desktop, visible whenever no app
  windows cover them, contains Firefox / Trash / Home / Pictures /
  Outreach Drafts / Downloads / Documents"). Long-form is fine here —
  this isn't fed to the detector, it's fed to the decider.

A target_description of "the Firefox icon with the flame graphic in
the column of desktop icons on the left side of the screen" is the
WRONG length for both: too long for vision (prose output, parse fails),
too redundant for knowledge (just say "Firefox icon" and let the
knowledge file describe context).

This is the contract. Phase 2 (lesson distillation, skill induction,
auto-recipe-generation) lands on top of it. Don't break it.
