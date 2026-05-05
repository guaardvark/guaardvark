# Guaardvark Agent — Operational Facts

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

## Shortcuts panel (top-left, always visible)
- Firefox launch button — orange, located at x=92, y=103 — single click opens Firefox with the user's logged-in profile
- YouTube, Reddit, Guaardvark, Outreach buttons below — single click opens that site in Firefox

## Critical rules
- Always use Ctrl+L to focus the address bar before typing a URL — never type into page content or search bars
- Never click sidebar icons for navigation — use Ctrl+L + the URL above
- The chat input field is at y=660 — click it before typing, do not confuse with the address bar at y=20-40
- Wait for pages to load before interacting; if the screen looks transient (mid-render, blurry), use the wait action
- Avoid clicking the taskbar at y=690-720; it contains system controls, not page content

## xdotool key names (use these literal strings)
- Return (not "enter"), Escape (not "esc"), Tab, BackSpace, Delete
- Modifier keys: ctrl, alt, shift, super
