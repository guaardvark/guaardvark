---
name: guaardvark-outreach
description: >-
  Draft and review social outreach with Guaardvark's supervised outreach system (Reddit,
  Discord, Twitter/X, Facebook): grounded, graded drafts that never post without the
  user's approval in the Studio. Use when the user wants a reply drafted for a thread,
  wants to see the pending outreach queue, or asks about outreach status or cadence.
---

# Outreach with Guaardvark (MCP tools, supervised)

Nothing here posts. Approval happens in the Studio's Outreach page, by design.

| tool | use |
|---|---|
| `outreach_status` | enabled / supervised / cadence / last run |
| `outreach_list_queue` | drafts, default `status='drafted'` (pending review) |
| `outreach_draft_post` | draft a comment or share post for a platform and thread URL; grounded in the user's indexed knowledge, graded, then queued |
| `outreach_reject_draft` | mark a draft rejected so it will not post |

## Pattern

1. `outreach_status` to confirm the loop is on and supervised.
2. `outreach_draft_post` with the platform and the thread. Show the draft and its grade.
3. The user edits or approves in the Studio. You can reject on their word; you cannot approve.

## Rules

- Do not bulk-draft across many threads. The system is for keeping up with engagement on the
  user's own products and topics, not for volume.
- Say clearly that a draft is queued, not posted.
