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

## Common Interaction Patterns
- To navigate: Ctrl+L → type URL → Return (ALWAYS use this, never click sidebar)
- To send chat: click input field (y=660) → type message → Return
- To scroll: use scroll action with negative amount (scroll up) or positive (scroll down)
- Popups/modals: click X button or press Escape to close
- If you see "Page Not Found" (404): you navigated to a wrong URL, use Ctrl+L to correct

## Known Gotchas
- DO NOT type URLs into the chat input — use Ctrl+L first to focus the address bar
- The chat input and address bar can be confused — the address bar is at y=20-40, chat input is at y=660
- After clicking a sidebar icon, wait for the page to load before interacting
- Some pages have loading spinners — wait for content to appear before clicking
