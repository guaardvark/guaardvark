# Guaardvark Agent — Operational Facts

This document carries semantic, vision-actionable knowledge. **Never store
pixel coordinates here.** The agent has eyes (the vision model) and hands
(the cursor); memory describes what to look for and what it does, not where
it sits. Layouts move; descriptions survive.

**Read every UI claim below as a hypothesis, not a guarantee.** If you don't
see something on screen this session, it isn't there — describe what you
*do* see and act on that.

## URLs
- Frontend: http://localhost:5175
- Backend: http://localhost:5002
- NEVER navigate to guaardvark.com or guaardvark.ai (those are the public website, not this agent's environment)

## Page routes
Use Ctrl+L → type URL → Return for all of these. Do NOT click sidebar icons for navigation.
- Dashboard: localhost:5175/
- Chat: localhost:5175/chat
- Settings: localhost:5175/settings
- Images: localhost:5175/images
- Video: localhost:5175/video
- Documents: localhost:5175/documents
- Notes: localhost:5175/notes
- Projects: localhost:5175/projects
- Clients: localhost:5175/clients
- Code Editor: localhost:5175/code-editor
- Tasks: localhost:5175/tasks
- Plugins: localhost:5175/plugins
- Tools: localhost:5175/tools
- Agents: localhost:5175/agents
- Outreach: localhost:5175/outreach

## The desktop (XFCE on :99)
Hypothesis: when no window covers the desktop you typically see a vertical
column of icons down the left edge, an "Applications" menu in the top-left
corner, and a taskbar along the bottom. Verify against the current frame
before acting; any of these can be hidden, missing, or differently arranged
this session.

Desktop icons typically present along the left edge (top → bottom):
- Trash
- File System
- Home
- Pictures
- Firefox (the one with the flame / fire icon)
- Outreach Drafts
- Downloads
- Documents

If the column isn't visible — for example a Firefox window covers it — say
so in your reasoning and pick a different path (use a taskbar entry, an
Applications menu, or Ctrl+L if a browser is already focused).

When you need to click a desktop icon, describe what you actually see
("the Firefox icon with the flame in the column down the left edge"). Don't
remember pixel positions; the column can shift, icon sizes can change,
the wallpaper can update.

## Critical rules
- Always use Ctrl+L to focus the address bar before typing a URL — never type into page content or search bars
- Never click sidebar icons in the Guaardvark app for navigation — use Ctrl+L + a URL from the list above
- The chat input is a wide text field at the bottom of the chat page, usually with placeholder text like "Type your message..."; click it once to focus before typing. Don't confuse it with the browser's URL/address bar (small input at the very top of the browser window).
- The taskbar runs along the bottom edge of the screen and contains system controls — not page content. Don't click there to interact with a web page.
- If the screen looks transient (mid-render, blurry, partial), use the wait action — do NOT report done and do NOT guess a click. Patience first.

## xdotool key names (use these literal strings)
- Return (not "enter"), Escape (not "esc"), Tab, BackSpace, Delete
- Modifier keys: ctrl, alt, shift, super

## Recovering from a stuck loop
If a click or find fails twice on the same target, do not retry it. Stop
treating your prior description as truth. Describe what is actually on the
current frame in plain language ("Firefox is in focus showing a new-tab
page; I see no desktop column") and pick a different action — scroll,
switch via the taskbar, navigate by URL, or report blocked.
