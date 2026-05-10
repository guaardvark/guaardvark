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

## Desktop
This is a desktop environment, customized desktop view.

**Central Area:**
- The background is a dark, abstract pattern of blue and black lines, resembling topographical map contours.
- Dominating the center of the screen is a large, bright blue cartoon mouse graphic.

**Left Side (Icons/Applications):**
- Along the left side, there is a vertical column of icons representing applications and folders. These include:
  - Applications
  - File System
  - Home
  - Trash
  - Pictures
  - Firefox (with a flame/fire icon)
  - Downloads
  - Documents

**Bottom Bar (Taskbar/Dock):**
- At the bottom of the screen, there is a taskbar area.
- It contains several small icons (some of which are visible but not clearly labeled).
- On the far right, there is a search bar with a magnifying glass icon, suggesting search functionality.


## Critical rules


## xdotool key names (use these literal strings)
- Return (not "enter"), Escape (not "esc"), Tab, BackSpace, Delete
- Modifier keys: ctrl, alt, shift, super

## Recovering from a stuck loop
If a critical step (scrolling, finding, or clicking) fails three times, assume the required element is off-screen or the page structure has changed. Instead of repeating the last action, attempt a broader context change — scroll the viewport, or re-run the initial navigation recipe — before another click attempt.
