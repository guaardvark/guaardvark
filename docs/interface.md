# Getting around the interface

Guaardvark runs in your browser, at http://localhost:5173 on a default install. This
page covers the parts of the interface that are easy to miss: menus behind a
right-click, cards you can move and resize, drag and drop, voice, and keyboard
shortcuts. For what each feature does, see [CAPABILITIES.md](../CAPABILITIES.md).

- [Navigation: sidebar or workspaces](#navigation-sidebar-or-workspaces)
- [Right-click menus](#right-click-menus)
- [Cards you can move and resize](#cards-you-can-move-and-resize)
- [Drag and drop](#drag-and-drop)
- [The floating chat](#the-floating-chat)
- [The microphone](#the-microphone)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [The progress bar at the bottom](#the-progress-bar-at-the-bottom)
- [The update notice](#the-update-notice)
- [Help on hover in Settings](#help-on-hover-in-settings)
- [Themes](#themes)
- [Did you know tips](#did-you-know-tips)

## Navigation: sidebar or workspaces

There are two ways to list the pages. Switch between them in **Settings → General →
Navigation**; the choice is saved in this browser.

- **Sidebar** (the default) lists every page down the left, grouped under Main, Code,
  Studio, Management and Configuration. The button at the bottom expands it to show names or
  collapses it to icons; hover an icon to see its name. On a narrow window it collapses
  by itself. The bottom of the sidebar also holds System Metrics, Agent Screen and the
  microphone.
- **Workspaces** puts a bar across the top with eight workspaces: Home, Chat, Studio,
  Library, Code, Work, Agents and System. Click one to open its first page; when it has
  more than one page, a second row lists them. A few pages are listed only here: Voice,
  Training, Upload, Bulk Import, WordPress Sites, WordPress Pages and System Dashboard.
  The right end of the bar holds System Metrics, Agent Screen, Settings, the microphone
  and a button for the floating chat.

The **Code** group, and the Code workspace's second row, hold the pages that work on code:
Code Editor, Swarm, System Map and Self-improvement. **Self-improvement** is where
Guaardvark checks its own code: its switches (Self-improvement, Codebase locked), Run
self-check, the pending fixes waiting for your review, the log of past runs, and the code
keeps Autoresearch stages. Its icon shows how many fixes are waiting. Settings → Agents
keeps a link to it, and the Uncle Claude settings stay in Settings.

The product profile you picked on first start can leave some pages out of either list
(Creator does). They are still installed and open from their address; see
[EXTENSIONS.md](EXTENSIONS.md#profiles).

## Right-click menus

Cards, files, notes, messages and the entries in most lists have their own right-click
menu.

### Dashboard

Right-click a **card** for:

- **Open page**: the full page the card summarises.
- **Minimize** or **Expand**: fold the card down to its title bar. Double-clicking the
  title bar does the same.
- **Change colour…** and **Reset colour**.
- **Hide card**: take it off the dashboard. It comes back from the background menu.
- Then the card's own actions, such as **Refresh**, **Add Task…**, **New Images** or
  **Start run**. The GPU Memory card switches between the Speed, Balanced and Quality
  tiers and can evict a loaded model.

Right-click an **empty part of the dashboard** for:

- **Cycle layout (next: …)**: see [layouts](#dashboard-layouts) below.
- **Reset layout…**: after you confirm, every card goes back to its default place and
  size, colours are cleared, minimized cards open and hidden cards come back.
- **Show** followed by the card's name for each hidden card, and **Show all hidden
  cards** when there are several.
- **Sticky Notes**: opens the Notes page.

### Everywhere else

| Page | Right-click | What you get |
|---|---|---|
| Files | the desktop | New Folder, Import Files, Paste |
| Files | a folder | Open in Window, Cut, Copy, Paste, a colour, Rename, Properties, Index Contents, Delete |
| Files | a file | Cut, Copy, Paste, Download, Rename, Properties, Index, Delete, plus Edit, View or Open in Code Editor for the file types that have them |
| Media | the background | New Folder, Select All, Sort by Name, Date or Size, Arrange Icons, Arrange Windows |
| Media | empty space in a folder window | New Folder, Paste into this folder, Select All, Sort by Name, Date or Size, all for the folder the window shows |
| Media | an image | View Full Size, Edit, Share / Publish…, Cut, Copy, Download, Rename, Delete |
| Notes | the background | New Note, Cycle Layout, Go to Dashboard |
| Notes | a note | a colour, Duplicate, Rename, Pin to Top, Delete (and Copy, Select all, Paste inside the text) |
| Chat | a message | Copy; on the assistant's replies also Good response and Bad response |
| Chat | a past chat in the Chats list | Open, Delete (asks first) |
| Code Editor | a file in the Files card | New File, New Folder, Rename, Delete |
| Code Editor | selected code in the editor | Ask Chat, Fix This, Explain, Add to Chat, which send the code to the editor's chat card |
| Image Gen | a batch in the history | Browse, Download, Load batch, Adjust & Retry, Clear from list (as fits the batch) |
| Video Gen | a clip | Play, Open in new tab, Approve clip, Re-render clip, Rename…, Delete video… (as fits the clip) |
| Video Gen | a batch in Video Library | Open, Download All Videos, Adjust & Retry, Retry, Cancel batch…, Delete batch… (as fits the batch) |
| Jobs | a job | Start, Cancel, Edit, Duplicate, Delete |
| Jobs | the background | New Code Generation, New CSV / Bulk Content, New Content Generation, New Data Analysis, New Custom Job |
| Activity | a job | Details, Copy ID, Resume / Re-index, Cancel job |
| Clients, Projects, Websites | an entry | Edit, Delete, Files, Schedule Task (Websites adds Crawl). Files opens a project's Documents tab, or the Files page with a list of the files linked to that client or website (link them in File or Folder Properties) |
| Clients, Projects, Websites | the background | New Client, New Project or New Website |
| Rules & Prompts | a rule | Edit…, Activate or Deactivate, Duplicate, Link to projects…, Copy ID, Delete |
| Agents | an agent | Edit, Test, Enable or Disable, Copy id, Reset to default |
| Agent Memory | a memory | Edit…, Copy content, Restore, Archive, Mark wrong, Delete |
| Agent Tools | a tool | Test…, Copy tool name |
| Swarm | a swarm | Expand, Cancel, Merge All, Clean Up, Copy swarm ID |
| Swarm | a task | View logs, View diff, Copy branch name, Copy task ID |
| Plugins | a plugin | Turn on or Turn off, Show logs, Settings…, More details |
| Cast & LoRA | a subject | Open studio, Remove from cast library |
| Cast & LoRA | a sample on a subject's page | View larger, Approve, Regenerate…, Remove this generation |
| Autoresearch | a run | View report, Download ledger (TSV) |
| WordPress Sites | a site | Edit…, Test Connection, Copy URL, Delete |
| WordPress Pages | a page | View Details, Process Now, Refresh, Pull Pages… |
| any page | the microphone | the voice panel (see [The microphone](#the-microphone)) |

Items that only make sense in some states (Cancel on a running job, Approve on a clip
held for review) appear only then.

## Cards you can move and resize

The Dashboard, the Code Editor, the Video Editor and Notes are made of cards, and the
Files and Media pages open folders as windows that behave the same way.

- **Move** a card by dragging its title bar.
- **Resize** it from any edge or corner.
- **Double-click the title bar** to fold the card down to its title bar, and again to
  open it. (On Notes this works in the Normal layout.)
- **Click** a card to bring it to the front. Cards may overlap; nothing snaps into
  place.

Layouts are saved by Guaardvark itself, not by the browser, so the same arrangement
comes back after a reload or from another browser.

### Dashboard layouts

The layout button at the top of the Dashboard (its tooltip reads "Layout: … (click to
cycle)") and the background right-click menu step through four layouts:

| Layout | What it does |
|---|---|
| Normal | Your own arrangement. |
| Compact | Smaller cards in rows across the width. |
| Compact Layered | Every card folded to a bar, stacked down the right edge. Double-click a bar to open it. |
| Mode-X | Taller bars down the right edge that still show each card's content. |

Each layout places the cards once; after that you can move them freely. Moves you make
in the other layouts do not change your Normal arrangement. In a narrow window, Normal
and Compact re-pack the cards into rows and switch moving off until the window is
wider.

### Other card pages

- **Code Editor**: the buttons at the top apply the balanced layout (20% / 60% / 20%)
  or reset the layout.
- **Notes**: the layout button cycles Normal, Compact and Collapsed (bars down the
  right; click one to go back to Normal). Double-click a note's title bar to minimize or
  expand it; double-click the name of an open note to rename it.
  Closing a note moves it to the Closed notes list.
- **Files**: folders and files sit on a desktop; drag the icons around and they settle
  on a grid. Double-click a folder to open it as a window. Each window switches between
  List View and Grid View, plus Media View when the folder holds images or video.
  **Arrange Icons** and **Arrange Windows** at the top tidy the desktop.
- **Media** works the same way for your generated images, with Arrange Icons, Arrange
  Windows and sorting in the background menu.

## Drag and drop

| Where | Drop | What happens |
|---|---|---|
| Files | files or whole folders from your computer, onto the desktop, a folder icon or an open folder window | They are uploaded there (folders keep their structure) and queued for indexing, so chat and search can use them. |
| Files | icons inside the page | Moves them between folders. |
| Dashboard, File Manager card | files from your computer | Uploaded into the folder the card shows. |
| Chat and the floating chat | a document or an image anywhere in the chat | A document is uploaded and indexed, the same as the paperclip, and the chat notes it. An image is attached to your next message so you can ask about it; pasting an image does the same. Only the first image of a message is sent to the model. |
| Media | images inside the page | Moves them between folders. Files from your computer are not taken here; put them in Files. |
| Video Gen | images, with **Input** set to **Image** | Added as the starting images for image-to-video. |
| Upscaling | images or videos | Added to the upscaling queue. |
| Video Editor | clips from the Media Library card onto the Bin, or files from your computer | Added to the project's Bin (files from your computer are uploaded to Files first). |
| Cast & LoRA | reference images | Added to the subject's reference photos. |

Dropping several files at once uploads all of them. A file dropped anywhere else is
ignored, so a stray drop never replaces the app in your browser tab.

## The floating chat

A chat window that follows you from page to page.

- **Open it** with the round button at the bottom right, with **Ctrl+Shift+C**, or, in
  the Workspaces bar, with the chat button at the right end.
- **It knows where you are.** A chip in its header names the page you are on, and that
  context goes with your question, so "what is wrong with this job?" means the page in
  front of you.
- **Move** it by its header and **resize** it from the bottom-right corner. **+** starts
  a new conversation, **–** folds it to its header, **×** closes it. Its place, size and
  recent conversation are kept in this browser across reloads.
- **Images**: paste one into the box or use **Attach an image**.
- It stays off the Chat page, which is a chat of its own.
- To hide the round button, hover it and click its small **×**. Ctrl+Shift+C still
  opens the chat.

## The microphone

The microphone sits at the bottom of the sidebar, or near the right end of the
Workspaces bar. Every mic button in the app (chat boxes and the Voice page included)
works the same way and shares one microphone.

- **Click** it to talk hands-free: each pause sends what you said as a message, until
  you click again.
- **Press and hold** it, or **hold Ctrl+Shift+Space**, to talk only while you hold;
  release to send. A short tap of Ctrl+Shift+Space does what a click does.
- **Right-click** it, or click the small arrow beside it, for the voice panel: the mic
  level, the last thing it heard, what a click does (**Push to talk**, **Tap to talk**
  or **Hands-free**), **Speak replies**, the reply voice, and **Wait for "Hey …"**,
  which makes hands-free mode wait for a wake phrase.

What you say is transcribed on your own machine and sent to the floating chat, or to
the Chat page when you are on it. With **Speak replies** on (the default) the answer is
read aloud, and the mic pauses while it speaks.

The microphone only opens when you click it or press the shortcut; the first time, the
browser asks for permission. Browsers allow the microphone only on `localhost` or over
HTTPS, so opening Guaardvark from another computer by a plain `http://` address shows
"Mic needs HTTPS or localhost". The **Voice chat** switch in **Settings → Chat** turns
voice off and hides the mic.

## Keyboard shortcuts

Press **?** anywhere outside a text box to see the main shortcuts in the app.

| Where | Keys | What it does |
|---|---|---|
| Anywhere | ? | Show the keyboard shortcuts |
| Anywhere | Ctrl+Shift+C | Open or close the floating chat |
| Anywhere | Ctrl+Shift+Space | Tap: start or stop the mic. Hold: talk while held |
| Chat | Enter / Shift+Enter | Send / new line |
| Chat | ↑ / ↓ | Bring back earlier messages (with the cursor at the start or end of the box) |
| Chat | / | List the chat commands (/imagine, /video, /help and more); arrow keys choose, Tab completes |
| Chat page | Esc | Stop the reply that is being written |
| Files, Media | Ctrl+A | Select all the icons on the desktop |
| Files, Media | Ctrl+C / Ctrl+X / Ctrl+V | Copy, cut, paste |
| Files, Media | Delete | Delete the selection (asks first) |
| Files, Media | Esc | Clear the selection |
| Image viewer | ← / → | Previous / next image |
| Image viewer | E / Esc | Edit the image / close |
| Notes | Ctrl+Z | Undo the last change to the notes: colour, text, title, delete, close (up to 30 steps) |
| Code Editor | Ctrl+S | Save the file |
| Code Editor | Ctrl+Shift+S | Save the session now: open tabs and the editor chat |
| Code Editor | Ctrl+Shift+O | Find a function or class by name; pick one to open its file at that line |
| Code Editor | Ctrl+1 … Ctrl+5 | Jump to the Files, Editor, Chat, Search or Output card |
| Code Editor | Ctrl+Shift+E | Explain the selected code in the editor's chat |
| System Map | / or Ctrl+K | Search |
| System Map | R / Esc | Reset the view / clear the search and selection |
| Video Editor | Space | Play or pause the preview |
| Video Editor | Delete | Remove the selected text overlay |
| Video Editor | Ctrl+Z | Undo the last timeline change |

The Files and Media keys use Ctrl on every system, a Mac included.

## The progress bar at the bottom

While something runs (an image or video render, indexing, an upload, training), a thin
bar appears along the bottom of the window. It shows the leading job's progress, and
other jobs appear beside it as small chips, such as "Video: waiting for GPU". **Hover**
the job text to list every job in flight with its status; **click** it to keep that
list open. When the last job ends, the result stays for a few seconds; a failure stays
longer, with its reason. The queue button at the right end shows how many scheduled
tasks are running and waiting.

## The update notice

When Guaardvark's code changes while it is running, for example when an update arrives
from another of your machines through the Interconnector, a notice floats above the
bottom of the window:

- **Reload** when only the page's own files changed, or when Guaardvark has restarted
  since you opened the page.
- **Restart** when the running server is older than the code on disk (backend code,
  dependencies or the version changed). It asks before restarting, because work in
  progress (renders, indexing, chat replies) stops.

**×** hides that notice; the next update shows a new one.

## Help on hover in Settings

In Settings, a title with a dotted underline explains itself: rest the pointer on it,
or move to it with Tab, to read what it controls. Panel titles, the small labels above
each group of controls and the tiles at the top all work this way. Many of the on and
off switches show a short description on hover too.

## Themes

**Settings → General → Theme** opens the theme picker: six built-in themes, five dark
and one light. Pick one and press **Apply Theme**. The theme is saved in this browser.

## Did you know tips

After first-run setup, a small "Did you know?" card appears at the bottom left, one per
visit, with a tip about the interface. **Show me** opens the page the tip is about,
**Next tip** shows another, and **Don't show tips** turns them off. Tips you have seen
are remembered in this browser, so new ones come first. Turn tips back on, or off, with
**Settings → General → Tips**.
