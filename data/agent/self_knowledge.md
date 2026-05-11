# Guaardvark Self-Knowledge Map
# This document helps the agent understand its own application architecture.

Every UI claim below is a hypothesis. If a description doesn't match the
current screen, trust the screen, not this file.

## CRITICAL: URLs
- Frontend: http://localhost:5175
- Backend API: http://localhost:5002
- NEVER navigate to guaardvark.com or guaardvark.ai — those are the public website.
- ALWAYS use URL navigation (Ctrl+L → type URL → Return) instead of clicking sidebar icons.

## Screen layout
The agent display is a 1024×1024 virtual desktop. The taskbar runs along
the bottom edge — system controls live there, not page content; don't click
inside it to interact with a web page. The rest of the area is the desktop
or whatever app/window is on top of it.

When Firefox or any other window is in focus, the desktop column of icons
typically can't be seen — verify before assuming any desktop element is
clickable.

## Browser Tab Management
- **New tab:** Ctrl+T — opens a new blank tab, address bar is auto-focused
- **Close current tab:** Ctrl+W — closes the tab you are currently on
- **Switch to next tab:** Ctrl+Tab — moves to the tab to the right
- **Switch to previous tab:** Ctrl+Shift+Tab — moves to the tab to the left
- **Switch to specific tab:** Ctrl+1 through Ctrl+9 — jumps to tab by position (Ctrl+1 = first tab, Ctrl+2 = second, etc.)
- **Reopen closed tab:** Ctrl+Shift+T — reopens the last closed tab
- Tabs appear as a horizontal row at the top of the browser window
- The active tab is highlighted/brighter than inactive tabs
- Each tab shows a favicon and page title
- The + button at the end of the tab row also opens a new tab
- To navigate in a tab: Ctrl+L to focus address bar, type URL, press Return

## Browser tips
- F11 toggles fullscreen (hides browser chrome, more screen space)
- Ctrl+L focuses the address bar — ALWAYS use this for navigation
- New tab: Ctrl+T (address bar is auto-focused in new tabs)
- The address bar is at the top of the browser window

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

## Chat page layout (localhost:5175/chat)
- A top bar with navigation arrows, a "New Chat" button, and a "Past Chats" button
- A scrollable message area in the middle: user messages on one side, assistant on the other
- A wide text input field along the bottom with placeholder text like "Type your message, paste an image, or use voice..."
- A send button (arrow icon) at the right of the input
- IMPORTANT: click the text input field FIRST, then type. Do NOT type into the browser's address bar.
- Enter sends the message, Shift+Enter adds a newline
- Assistant messages may have a Narrate button (speaker icon) for text-to-speech

## Dashboard layout (localhost:5175/)
- Shows system overview cards in a grid
- Project Manager card, Clients card, recent activity
- Quick access links to other pages

## Settings page layout (localhost:5175/settings)
- Grid of configuration cards
- Each card has a title, description, and controls (toggles, dropdowns, text fields)
- Cards include: LLM Settings, RAG Settings, Voice Settings, System Config, Backup/Restore

## Images page layout (localhost:5175/images)
- Image generation prompt input at top
- Gallery grid of previously generated images below
- Each image card has hover controls (download, delete, info)

## YouTube interaction patterns
- To search YouTube: navigate to youtube.com, click the search box near the top, type the search query, press Return
- To watch a video: click on the video thumbnail or title from search results
- To add a comment: scroll down below the video to the comments section, click the "Add a comment..." text field, type the comment, then click the "Comment" button that appears
- YouTube comment box requires being signed in — the browser should already be logged in
- The comment box is below the video description and above existing comments
- After clicking the comment text field, a "Comment" button and "Cancel" button appear
- Wait for each page to fully load before interacting

## Known Guaardvark content
- YouTube channel: guaardvark
- Video: "Gotham Rising" — a Guaardvark-produced video on the guaardvark YouTube channel

## DOM element awareness
When Firefox is active, the agent loop may receive a list of interactive
elements with their exact screen pixel coordinates. Each element is numbered
like [1], [2], etc.
- Coordinates supplied with elements are already in screen pixels — no conversion needed
- If an element is marked "(focused)", it has keyboard focus — typing goes there directly
- The list includes buttons, links, inputs, textareas, and other interactive elements
- If the element list is empty or missing, fall back to visual description
- Always use the action JSON shape defined in this prompt's reply template — do NOT improvise alternative shapes from past examples or other systems

## XFCE desktop (when nothing covers it)
The agent runs in a real XFCE session. The following elements are typically
present on the desktop; verify each against the current frame before
acting.

- An **Applications** menu in the top-left corner of the screen (XFCE menu).
- A **vertical column of icons** rendered down the left edge of the desktop. By name, top → bottom: Trash, File System, Home, Pictures, Firefox (flame icon), Outreach Drafts, Downloads, Documents.
- A **taskbar** along the bottom of the screen with small system icons and a **search bar** with a magnifying glass icon at the right end.
- The wallpaper is a topographic-map-style background with a **blue cartoon mouse** centered as the XFCE logo.

When a Firefox window is in focus, the desktop column is usually hidden
behind it. Don't assume the column is clickable when a window is on top.

## Agent files
- Location: data/agent/files/ (the "Outreach Drafts" / "Downloads" / "Documents" desktop icons point at user-owned locations the agent can read and write to)
- Use this folder for downloads, screenshots, and any files the agent creates or needs

## Common interaction patterns
- To open Firefox when the desktop is visible: click the Firefox icon (with the flame) in the column down the left side. If no window is covering the desktop you should see it; if the desktop is covered, the Applications menu or the taskbar may have a Firefox entry instead.
- To navigate within Firefox: Ctrl+L → type URL → Return (NEVER click sidebar bookmarks)
- If a page is still loading or the screen looks transient (mid-render, blurry, partial), use the `wait` action — do NOT report `done` and do NOT guess a click. Patience first.
- To send chat: click the chat input field → type message → Return
- To scroll: use scroll action with negative amount (scroll up) or positive (scroll down)
- Popups/modals: click X button or press Escape to close
- If you see "Page Not Found" (404): you navigated to a wrong URL, use Ctrl+L to correct

## Servo learning loop
- Every click you make is recorded in the servo archive (data/training/knowledge/servo_archive.jsonl)
- The archive stores: raw coordinates, scaled coordinates, actual click position, success/failure
- A self-improvement engine periodically analyzes your click accuracy and proposes calibration updates
- If your clicks are consistently off by a certain amount, the system will learn to compensate
- When clicking, aim for the CENTER of the element — the servo correction loop handles fine-tuning
- Your current accuracy stats are injected into your system prompt so you know how you're doing

## Key names for hotkeys
- Use "Return" NOT "enter" — xdotool does not recognize "enter", only "Return"
- Use "Escape" NOT "esc"
- Use "Tab" NOT "tab"
- Use "BackSpace" NOT "backspace"
- Use "Delete" NOT "delete"
- Modifier keys: "ctrl", "alt", "shift", "super"

## Known gotchas
- DO NOT type URLs into the chat input — use Ctrl+L first to focus the address bar
- The chat input and address bar look different but can be confused — the address bar is at the very top of the browser window, the chat input is along the bottom of the chat page
- After clicking any navigation icon, wait for the page to load before interacting
- Some pages have loading spinners — wait for content to appear before clicking

<!-- AUTO-DISTILLED START -->
### Learned strategies (auto-distilled from successful sessions)

- **[2026-04-09, seed]** To navigate to a URL, press Ctrl+L first to focus the address bar, then type the URL, then press Return. Never type URLs into page content or search bars.
- **[2026-04-09, seed]** If clicking a website's search bar fails or typing doesn't appear, construct the search URL directly instead (e.g., `youtube.com/results?search_query=term`, `google.com/search?q=term`). Most sites encode search terms as `?q=` or `?search_query=` in the URL.
- **[2026-04-09, seed]** After typing text into a field, always verify by taking a screenshot. If the text didn't appear, the field likely wasn't focused — click the field first, then retype.
- **[2026-04-09, seed]** To open Firefox from the desktop, click the Firefox icon (with the flame) in the column of icons down the left edge of the desktop. If Firefox is already running but no window is visible, look for it in the taskbar along the bottom.
- **[2026-04-09, seed]** To copy text: click to place cursor, use Ctrl+A to select all (or click-drag to select specific text), then Ctrl+C to copy. To paste: click the target field, then Ctrl+V.
- **[2026-04-09, seed]** To dismiss popups, cookie banners, or permission dialogs, press Escape first. If that doesn't work, look for "Accept", "Dismiss", "No thanks", or an X button and click it.
- **[2026-04-09, seed]** When a page is loading or content hasn't appeared yet, wait 2-3 seconds before trying to interact. Don't click on elements that haven't rendered.
- **[2026-04-09, seed]** To scroll down a page, use the scroll action or press Page_Down. To scroll up, press Page_Up. For small scrolls, use the arrow keys Up/Down.
- **[2026-04-09, seed]** To switch between open tabs in Firefox, use Ctrl+Tab (next tab) or Ctrl+Shift+Tab (previous tab). To close a tab, use Ctrl+W. To open a new tab, use Ctrl+T.
- **[2026-04-09, seed]** If an action reports success but the screen doesn't change, the action likely failed silently. Try a different approach rather than repeating the same action.
- **[2026-04-09, seed]** To right-click for a context menu, use the right_click action. This is useful for "Save as", "Copy link", or "Open in new tab" options.
- **[2026-04-09, seed]** To interact with YouTube: the search bar is in the header area near the top. The video player is in the center. Comments are below the video — scroll down to reach them.
- **[2026-04-09, seed]** When multiple similar elements exist (like several buttons or links), describe the target precisely by its visible text label, color, or position relative to other elements rather than just its type.
- **[2026-04-09, seed]** To go back to the previous page, press Alt+Left. To refresh the current page, press Ctrl+R or F5.
- **[2026-04-09, seed]** The taskbar along the bottom edge contains system controls — not page content. Keep clicks on web pages above the taskbar.
- **[2026-04-09, batch-distill]** After achieving a goal, confirm completion by verifying the screen state (take a screenshot) before reporting success — don't assume it worked from the action alone.
- **[2026-04-09, batch-distill]** When the screen appears black or ambiguous after an action, don't repeat the same action — instead take a screenshot first to understand the current state, then decide the next step.
- **[2026-04-09, batch-distill]** Distinguish between general browser chrome (sidebar buttons, address bar, tab bar) and page-specific input fields (search bars, comment boxes, form inputs). Click the specific field you need, not a nearby browser element.

<!-- AUTO-DISTILLED END -->
