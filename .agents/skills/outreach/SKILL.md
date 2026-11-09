---
name: outreach
description: >-
  Draft and review social outreach with Guaardvark's supervised outreach system (Reddit,
  YouTube, Discord, Facebook): grounded, graded drafts that never post without the
  user's approval in the Studio. Use when the user wants a reply drafted for a thread,
  wants to see the pending outreach queue, or asks about outreach status or cadence.
---

# Outreach with Guaardvark (MCP tools, supervised)

Nothing here posts. Approval happens in the Studio's Approvals page, by design, and a
Reddit, YouTube or Facebook draft posts only when the user presses Post now there with the
Agent Screen open. X and LinkedIn are not offered for outreach; the user's own LinkedIn
account takes posts through `request_publish` instead.

| tool | use |
|---|---|
| `outreach_status` | enabled / cadence / per-platform posting modes |
| `outreach_list_queue` | drafts, default `status='drafted'` (pending review) |
| `outreach_draft_post` | draft a comment or share post for a platform and thread URL; grounded in the thread and the user's pitch sheet, graded, then queued |
| `outreach_reject_draft` | reject a draft so it will not post; fails and says why once the post is being submitted or is already out |
| `request_publish` | ask to post the user's own announcement to one of their social connections; it waits on the Approvals page |

## Pattern

1. `outreach_status` to confirm the loop is on.
2. `outreach_draft_post` with the platform and the thread. Show the draft and its grade.
3. The user edits, approves and starts the post in the Studio. You can reject on their word;
   you cannot approve or post.

## Rules

- Do not bulk-draft across many threads. The system is for keeping up with engagement on the
  user's own products and topics, not for volume.
- Say clearly that a draft is queued, not posted.
