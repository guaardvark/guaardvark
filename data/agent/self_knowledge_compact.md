# Guaardvark Agent — Operational Facts

This document carries semantic, vision-actionable knowledge. **Never store
pixel coordinates here.** The agent has eyes (the vision model) and hands
(the cursor); memory describes what to look for and what it does, not where
it sits. Layouts move; descriptions survive.

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

## Shortcuts panel
A small panel anchored in the top-left corner of the desktop, always visible whether or not Firefox is running. It contains a column of large, colorful launch buttons.
- **Firefox launch button** — a large orange flame/circle icon at the top of the panel. Single click opens Firefox with the user's logged-in profile (cookies for Reddit / Discord / Facebook / etc. are already there).
- **Sites section** (lower in the same panel, under a "Sites" label) — colored buttons for YouTube (red), Reddit (orange-red), Guaardvark (teal), Outreach (green). Single click opens that site in Firefox; if Firefox is closed it opens; if open it adds a tab.

When you need to click any of these, describe what you see ("the orange Firefox flame in the top-left Shortcuts panel", "the teal Guaardvark button in the Sites section") — let vision find it on the current frame. Don't try to remember pixel positions; the panel can shift, the icon can be redrawn, your DPI can change.

## Critical rules
- Always use Ctrl+L to focus the address bar before typing a URL — never type into page content or search bars
- Never click sidebar icons in the Guaardvark app for navigation — use Ctrl+L + a URL from the list above
- The chat input is a wide text field at the bottom of the chat page, usually with placeholder text like "Type your message..."; click it once to focus before typing. Don't confuse it with the browser's URL/address bar (small input at the very top of the browser window).
- The desktop taskbar runs along the bottom edge of the screen and contains system controls (clock, app launchers, volume) — not page content. Don't click there to interact with a web page.
- If the screen looks transient (mid-render, blurry, partial), use the wait action — do NOT report done and do NOT guess a click. Patience first.

## xdotool key names (use these literal strings)
- Return (not "enter"), Escape (not "esc"), Tab, BackSpace, Delete
- Modifier keys: ctrl, alt, shift, super
