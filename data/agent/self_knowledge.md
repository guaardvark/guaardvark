# Guaardvark Self-Knowledge Map
# This document helps the agent understand its own application architecture.

## CRITICAL: URLs
- Frontend: http://localhost:5175
- Backend API: http://localhost:5002
- NEVER navigate to guaardvark.com or guaardvark.ai — those are the public website.
- ALWAYS use URL navigation (Ctrl+L → type URL → Return) instead of clicking sidebar icons.

## Screen Layout (1280x720)
- Taskbar: bottom 30px (y=690-720) — ignore this area
- Sidebar: left 60px (x=0-60), dark background, contains navigation icons
- Main content: x=60 to x=1280, y=0 to y=690
- The sidebar icons are small (45px each) and hard to distinguish — USE URL NAVIGATION INSTEAD

## Browser Tab Management
- **New tab:** Ctrl+T — opens a new blank tab, address bar is auto-focused
- **Close current tab:** Ctrl+W — closes the tab you are currently on
- **Switch to next tab:** Ctrl+Tab — moves to the tab to the right
- **Switch to previous tab:** Ctrl+Shift+Tab — moves to the tab to the left
- **Switch to specific tab:** Ctrl+1 through Ctrl+9 — jumps to tab by position (Ctrl+1 = first tab, Ctrl+2 = second, etc.)
- **Reopen closed tab:** Ctrl+Shift+T — reopens the last closed tab
- Tabs appear as a horizontal row at the very top of the browser window (y=50-70)
- The active tab is highlighted/brighter than inactive tabs
- Each tab shows a favicon and page title
- The + button at the end of the tab row also opens a new tab
- To navigate in a tab: Ctrl+L to focus address bar, type URL, press Return

## Browser Tips
- F11 toggles fullscreen (hides browser chrome, more screen space)
- Ctrl+L focuses the address bar — ALWAYS use this for navigation
- New tab: Ctrl+T (address bar is auto-focused in new tabs)
- The address bar is at the top of the screen, approximately y=20-40

## Page Routes (use these with Ctrl+L navigation)
- Dashboard: localhost:5175/
- Chat: localhost:5175/chat
- Code Editor: localhost:5175/code-editor
- Documents: localhost:5175/documents
- Notes: localhost:5175/notes
- Clients: localhost:5175/clients
- Projects: localhost:5175/projects
- Websites: localhost:5175/websites
- Media/Images: localhost:5175/images
- Video: localhost:5175/video
- Job Scheduler: localhost:5175/tasks
- Rules & Prompts: localhost:5175/rules
- Agent Tools: localhost:5175/tools
- Agents: localhost:5175/agents
- Settings: localhost:5175/settings
- Plugins: localhost:5175/plugins

## Chat Page Layout (localhost:5175/chat)
- Top bar (y=0-50): navigation arrows, "New Chat" button (+), "Past Chats" button (clock icon)
- Message area (y=50-650): scrollable list of messages, user messages on right, assistant on left
- Input area (y=650-690): text field with placeholder "Type your message, paste an image, or use voice..."
- Send button: right side of input area, arrow icon
- The text input field spans most of the width (x=80 to x=1200, y=660)
- IMPORTANT: Click the text input field FIRST, then type. Do NOT type into the address bar.
- Enter sends the message, Shift+Enter adds a newline
- Assistant messages may have a Narrate button (speaker icon) for text-to-speech

## Dashboard Layout (localhost:5175/)
- Shows system overview cards in a grid
- Project Manager card, Clients card, recent activity
- Quick access links to other pages

## Settings Page Layout (localhost:5175/settings)
- Grid of configuration cards
- Each card has a title, description, and controls (toggles, dropdowns, text fields)
- Cards include: LLM Settings, RAG Settings, Voice Settings, System Config, Backup/Restore

## Images Page Layout (localhost:5175/images)
- Image generation prompt input at top
- Gallery grid of previously generated images below
- Each image card has hover controls (download, delete, info)

## YouTube Interaction Patterns
- To search YouTube: navigate to youtube.com, click the search box at the top center, type the search query, press Return
- To watch a video: click on the video thumbnail or title from search results
- To add a comment: scroll down below the video to the comments section, click the "Add a comment..." text field, type the comment, then click the "Comment" button that appears
- YouTube comment box requires being signed in — the browser should already be logged in
- The comment box is below the video description and above existing comments
- After clicking the comment text field, a "Comment" button and "Cancel" button appear
- Wait for each page to fully load before interacting

## Known Guaardvark Content
- YouTube channel: guaardvark
- Video: "Gotham Rising" — a Guaardvark-produced video on the guaardvark YouTube channel

## DOM Element Awareness
When Firefox is active, the agent loop may receive a list of interactive elements with their exact screen pixel coordinates. Each element is numbered like [1], [2], etc.
- Coordinates supplied with elements are already in screen pixels — no conversion needed
- If an element is marked "(focused)", it has keyboard focus — typing goes there directly
- The list includes buttons, links, inputs, textareas, and other interactive elements
- If the element list is empty or missing, fall back to visual description
- Always use the action JSON shape defined in this prompt's reply template — do NOT improvise alternative shapes from past examples or other systems

## Shortcuts Panel (top-left corner)
A panel at the top-left of the screen with clickable buttons. Always visible.
The panel is 160px wide and ~420px tall, anchored at (10, 10).

**Apps section:**
- **Firefox** (big orange button, ~center x=92, y=103) — launches Firefox with the user's logged-in profile (cookies for Reddit / Discord / Facebook / etc. are already there)
- **Agent Files** — opens the agent's file storage folder
- **Drawing** — opens the GNOME Drawing paint app
- **Terminal** — opens xterm

**Sites section** (below a "Sites" label — opens Firefox + a tab if Firefox is closed, or just a new tab if it's already open):
- **YouTube** (red button) — opens youtube.com
- **Reddit** (orange-red button) — opens reddit.com/r/LocalLLaMA
- **Guaardvark** (teal button) — opens the local Guaardvark dashboard
- **Outreach** (green button) — opens the outreach review UI for drafted comments

When you need a site that has a button, **clicking the button is the fastest path** —
it skips the URL bar dance entirely and uses your logged-in Firefox profile.

## Desktop Right-Click Menu
Right-clicking the desktop background opens the Openbox menu with:
- Firefox — launch the browser
- Agent Files — open the agent's file storage folder (data/agent/files/)
- Drawing — open paint program
- File Manager — open pcmanfm file browser
- Terminal — open xterm
- Guaardvark — open the Guaardvark web UI

## Agent Files
- Location: data/agent/files/ (also accessible via right-click menu → Agent Files)
- Use this folder for downloads, screenshots, and any files the agent creates or needs

## Common Interaction Patterns
- To open Firefox: click the big orange "Firefox" button in the Shortcuts panel (top-left), or right-click desktop → click Firefox
- To open YouTube / Reddit / Guaardvark / Outreach: click the matching colored button in the Sites section of the Shortcuts panel — single click, opens directly in Firefox
- If Firefox is already open and you need a different site, the Sites buttons open a new tab — no URL bar needed
- To navigate to anything else: Ctrl+L → type URL → Return (NEVER click sidebar bookmarks)
- If a page is still loading or the screen looks transient (mid-render, blurry, partial), use the `wait` action — do NOT report `done` and do NOT guess a click. Patience first.
- To send chat: click input field (y=660) → type message → Return
- To scroll: use scroll action with negative amount (scroll up) or positive (scroll down)
- Popups/modals: click X button or press Escape to close
- If you see "Page Not Found" (404): you navigated to a wrong URL, use Ctrl+L to correct

## Servo Learning Loop
- Every click you make is recorded in the servo archive (data/training/knowledge/servo_archive.jsonl)
- The archive stores: raw coordinates, scaled coordinates, actual click position, success/failure
- A self-improvement engine periodically analyzes your click accuracy and proposes calibration updates
- If your clicks are consistently off by a certain amount, the system will learn to compensate
- When clicking, aim for the CENTER of the element — the servo correction loop handles fine-tuning
- Your current accuracy stats are injected into your system prompt so you know how you're doing

## Key Names for Hotkeys
- Use "Return" NOT "enter" — xdotool does not recognize "enter", only "Return"
- Use "Escape" NOT "esc"
- Use "Tab" NOT "tab"
- Use "BackSpace" NOT "backspace"
- Use "Delete" NOT "delete"
- Modifier keys: "ctrl", "alt", "shift", "super"

## Known Gotchas
- DO NOT type URLs into the chat input — use Ctrl+L first to focus the address bar
- The chat input and address bar can be confused — the address bar is at y=20-40, chat input is at y=660
- After clicking a sidebar icon, wait for the page to load before interacting
- Some pages have loading spinners — wait for content to appear before clicking

<!-- AUTO-DISTILLED START -->
### Learned Strategies (auto-distilled from successful sessions)

- **[2026-04-09, seed]** To navigate to a URL, press Ctrl+L first to focus the address bar, then type the URL, then press Return. Never type URLs into page content or search bars.
- **[2026-04-09, seed]** If clicking a website's search bar fails or typing doesn't appear, construct the search URL directly instead (e.g., `youtube.com/results?search_query=term`, `google.com/search?q=term`). Most sites encode search terms as `?q=` or `?search_query=` in the URL.
- **[2026-04-09, seed]** After typing text into a field, always verify by taking a screenshot. If the text didn't appear, the field likely wasn't focused — click the field first, then retype.
- **[2026-04-09, seed]** To open Firefox from the desktop, click the orange "Firefox" button in the Shortcuts panel on the left side of the screen. If Firefox is already open but not visible, try clicking the taskbar at the bottom.
- **[2026-04-09, seed]** To copy text: click to place cursor, use Ctrl+A to select all (or click-drag to select specific text), then Ctrl+C to copy. To paste: click the target field, then Ctrl+V.
- **[2026-04-09, seed]** To dismiss popups, cookie banners, or permission dialogs, press Escape first. If that doesn't work, look for "Accept", "Dismiss", "No thanks", or an X button and click it.
- **[2026-04-09, seed]** When a page is loading or content hasn't appeared yet, wait 2-3 seconds before trying to interact. Don't click on elements that haven't rendered.
- **[2026-04-09, seed]** To scroll down a page, use the scroll action or press Page_Down. To scroll up, press Page_Up. For small scrolls, use the arrow keys Up/Down.
- **[2026-04-09, seed]** To switch between open tabs in Firefox, use Ctrl+Tab (next tab) or Ctrl+Shift+Tab (previous tab). To close a tab, use Ctrl+W. To open a new tab, use Ctrl+T.
- **[2026-04-09, seed]** If an action reports success but the screen doesn't change, the action likely failed silently. Try a different approach rather than repeating the same action.
- **[2026-04-09, seed]** To right-click for a context menu, use the right_click action. This is useful for "Save as", "Copy link", or "Open in new tab" options.
- **[2026-04-09, seed]** To interact with YouTube: the search bar is in the header area (around y=113 on a 1280x720 display). The video player is in the center. Comments are below the video — scroll down to reach them.
- **[2026-04-09, seed]** When multiple similar elements exist (like several buttons or links), describe the target precisely by its visible text label, color, or position relative to other elements rather than just its type.
- **[2026-04-09, seed]** To go back to the previous page, press Alt+Left. To refresh the current page, press Ctrl+R or F5.
- **[2026-04-09, seed]** The taskbar at the bottom of the screen (y=690-720) should be avoided for clicking — it contains system controls, not page content. Keep clicks above y=690.
- **[2026-04-09, batch-distill]** After achieving a goal, confirm completion by verifying the screen state (take a screenshot) before reporting success — don't assume it worked from the action alone.
- **[2026-04-09, batch-distill]** When the screen appears black or ambiguous after an action, don't repeat the same action — instead take a screenshot first to understand the current state, then decide the next step.
- **[2026-04-09, batch-distill]** Distinguish between general browser chrome (sidebar buttons, address bar, tab bar) and page-specific input fields (search bars, comment boxes, form inputs). Click the specific field you need, not a nearby browser element.

<!-- AUTO-DISTILLED END -->
