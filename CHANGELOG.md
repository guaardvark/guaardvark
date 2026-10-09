# Changelog

## Unreleased

## 3.1.0 — References for MiniMax H3, Sana Sprint, and an interface you can find your way around

- **Video Gen takes References.** A third input next to Text and Image builds a board of
  pictures, clips and voice for MiniMax H3 Reference, and start/end frames get an end-frame tile
  with the Files picker. Boards are checked against the card's measured budget before they queue;
  one that does not fit is refused with the reason.
- **Sana Sprint** joins the offline image models: fast stills, priced by each model's measured
  peak.
- **Getting around.** First-run "Did you know" tips and a guide to the interface
  (`docs/interface.md`). Drag files into Files, chat or the Video Editor bin; right-click menus
  on Files and Notes; resize handles and one drop placeholder on every card page; Always approve
  on chat tool cards.
- **Keep ready** holds the chat model in memory and steps it aside for GPU work. Off by default.
- **Settings → Access** has a Network access switch (off by default) and the chat model list
  offers Start Ollama when Ollama is off.
- **Speech to text** uses the Whisper model chosen in Settings, works with PyAV 19, and runs on
  the CPU when CTranslate2 cannot use the card.
- **Image, upscaling, reranker and Studio models download only behind an Install button**, never
  on first use.
- **Smaller cards:** SD and SDXL render on 8 GB cards, and LoRA training is offered only for
  installed base models.
- **Outreach posts only what it can confirm on the page.** Connections answer this machine only
  without a key and hold publishes for approval; the proxy's own X-Forwarded-For entry is the one
  trusted.
- **Discord voice** hears end-to-end encrypted calls and replies in an installed voice.

## 3.0.1 — Video renders run to the end

- **Video renders are no longer marked lost mid-render.** The check for whether ComfyUI is
  running imported a name that does not exist, so it always failed: every video render in
  progress was recorded as "ComfyUI connection lost — job orphaned", and an active render did
  not stop ComfyUI from being shut down. A ComfyUI that is busy loading a model is also no
  longer taken for one that has stopped.
- **An update no longer leaves a white error page.** The web UI holds its live reloads while an
  Interconnector sync is writing files, reloads once when it finishes, and says when the backend
  needs a restart.
- **Image and video generation:** the gallery refreshes on its own, a renamed video keeps
  playing, LoRAs are offered only for the models they fit, and the progress bar shows image and
  video jobs side by side.
- **One microphone for the whole app** sits in the top bar; Voice becomes a status and settings
  page.
- **The CLI shows what it is doing** while a reply is prepared. `GUAARDVARK_NO_SPINNER=1` turns
  it off.
- **`scripts/video_box_fingerprint.py`** records a machine's video models and render graph so two
  installs can be compared when one renders differently.

## 3.0.0 — An inbound guard, agents that act on what they see, and Docker on a fresh Ubuntu

Guaardvark 3.0 reads code before it lands, keeps every outbound path behind your say-so, and
makes the agents act instead of narrate: they run tools from a request for them, claim done only
when the screen shows it, and ask before anything public. The Docker install was walked through
on a freshly installed Ubuntu and fixed where a new user would have stopped.

### Upgrading

- **Docker:** run `./start-docker.sh` (add `--gpu` for NVIDIA) again. It rebuilds the images, gives
  Redis a password, keeps an existing database's password, and the containers now restart after a
  reboot. `--gpu` needs NVIDIA Container Toolkit; INSTALL.md has the steps.
- **API key:** running tools, automation, backups and restarts from another device needs the
  install's API key (Settings → API key). The Guaardvark machine itself needs nothing.
- **Agent recipes** that leave their safety bounds are not loaded (see below). The stock library
  is inside them.
- **AutoResearch** nightly runs need an independent judge model and refuse without one.
- **Code Search (zvec-grep):** press its Install again to pick up the patched dependencies.
- **Contributors:** agent instructions, recipes, rule and lesson bundles are maintainer-written;
  a pull request from a fork that changes them fails the inbound check. Open an issue instead.

### New since the notes below

- **Docker installs on a fresh Ubuntu.** A clean clone builds again; `./start-docker.sh` says
  which prerequisite is missing (Docker, Compose, the `docker` group, NVIDIA Container Toolkit)
  before it builds anything; `--gpu` builds PyTorch for your card, GTX 10 series and newer; the
  backend reaches Ollama inside Docker; the health check reports the real version.
- **Agent recipes have safety bounds.** A recipe runs before any model reads the request, so one
  that claims everyday messages, types fixed text other than an address, presses terminal or
  developer-tools keys, or clicks something that spends, deletes or grants is refused at load.
- **Browser recipes:** next and previous tab, zoom in and out, and bookmark this page (thanks
  @bibhacodex, #252). Recipe clicks point with the task's own vision model, "go to the … page"
  opens this install's web UI port, and the agent's browser starts on a blank page.
- **Agents act.** An agent run's first message asks for action, not a plan; agents get their
  instructions once and the right tools, ask before gated tools, and the screen agent claims done
  only when the screen shows it.
- **Chat goes where you meant.** Ordinary questions stay in chat; images, video, Film Crew and photo
  tools start only from a request for them; a reply that merely names a tool no longer runs it.
  Current-information questions get a **Search the web for this** offer instead of a silent search.
- **Remembering carries across chats.** "remember …", "from now on …" and "for future reference …"
  apply to every chat, not only the one they were said in.
- **Training runs end to end.** Add Dataset works, a training job runs from the page, the result is
  measured against its base and held if it is worse (you can still export it), and the training
  libraries install from Settings on a click.
- **AutoResearch reports what it measured** and refuses runs it cannot measure.
- **The self-check** every six hours tries, verifies and stages fixes for your review; it never
  applies one on its own.
- **Outreach has one stop** for all public posting, needs a second check that actually ran, and
  counts a post only when the page shows it.
- **Right-click menus** across the app, Settings explanations in hover help, and **Esc** stops a
  running reply in the web chat and the CLI.
- **Cast:** import LoRAs trained elsewhere (thanks @Carol-zolet, #253, #303) and keep one per
  base model (#300); the identity score is earned from the images. Z-Image through ComfyUI is an
  opt-in route (thanks @kwiksher, #197).
- **Code Search (zvec-grep):** patched simple-git, MCP SDK and sharp under zvec-grep 0.2.2.
- **Image model stays loaded between batches** if you switch it on.

### Earlier in this cycle

- **Docker installs get their own database and queue passwords.** The first `./start-docker.sh`
  writes random `GUAARDVARK_POSTGRES_PASSWORD` and `GUAARDVARK_REDIS_PASSWORD` into `.env`, and
  Redis now requires its password. A database created before this keeps its password; INSTALL.md
  shows how to change it.
- **ARM machines with an NVIDIA GPU are sized as GPU machines.** A GB10 machine (DGX Spark and
  similar) reports no VRAM figure because its GPU shares the system memory; Guaardvark now finds
  that GPU, uses the shared memory for Ollama's settings, and starts it on the standard chat
  model instead of the 1B model meant for boards like the Raspberry Pi.
- **ACE-Step 1.5 is an optional music model.** Audio Studio → Manage models lists it with an
  Install button that downloads its weights (9.4 GB, MIT license, pinned revision) into
  `data/models/ace-step-1.5/` and builds its own Python environment from the pinned 1.5 release;
  nothing is fetched until then. Once installed, the Music model picker, the REST route and the
  `generate_music` tool (`model: "ace-step-1.5"`) can use it. ACE-Step v1 stays the default.
- **Nothing leaves the machine just because a page opened.** The web UI's Lato and Raleway fonts
  ship with the app instead of loading from Google Fonts, and the image pages no longer ask
  Hugging Face about every model that is not installed: Manage models has **Check access**, which
  asks on your click and never sends your HF_TOKEN (Install still uses it). No backend request
  sends a login saved in `~/.netrc`.
- **Uncle Claude runs on a schedule only if you say so.** With an Anthropic key set, the
  twice-daily advice and the servo-change reviews now also need Settings → Uncle Claude →
  **Scheduled sends** (off). Uncle Claude's routes, like the staged-fix routes, refuse other
  devices without the API key.
- **Outreach posts wait for you.** Supervised mode is on unless you switch it off, and waiting
  drafts, held code changes and publishes all sit on **Approvals**, each in its own tab; approving
  a held code change applies it.
- **Every outbound path is listed** in `scripts/inbound_guard/egress.json`. The inbound guard
  holds new code that reaches a host no path declares and reports any outbound switch that turns
  on; the security check lists the paths that are on.
- **An inbound guard reads code before it lands.** `scripts/check_inbound.py` is the
  counterpart of the portability guard: it reads the lines a change adds and says whether
  they may land, should be held for a person to read, or must be refused. It looks at
  edits to the guards, CI and security policy; agent instructions; hosted AI and telemetry
  clients, new outside hosts and TLS checks turned off; code that runs text or unpickles;
  hidden characters and encoded strings; new or unpinned dependencies; and symlinks,
  pickles and binaries. Pull requests get a report-only check that uses the base branch's
  copy. In a clone, `scripts/install_hooks.sh` installs both guards' hooks; the inbound one
  is off until `git config inboundguard.mode observe` (record what fetches, merges and
  cherry-picks bring to `main`) or `enforce` (also refuse a held merge until that exact
  change is approved).
- **Settings → Agents → Inbound guard.** One switch (Off / Observe / Enforce) for the guard inside
  the product too: edits Guaardvark makes to its own code, the code editor's file actions, swarm
  merges, generated-code tasks and backup restores are read before they land, and in Enforce a
  risky edit waits as a pending fix with the findings beside its diff. A source watch sweeps the
  checkout every ten minutes for changes that came in any other way, and an Interconnector master
  holds back files that are waiting for review. Extensions can add checks through
  `extensions/<id>/inbound_guard.py`.
- **Starting a background task no longer hangs when Redis is down.** A request that hands work to
  the Celery worker (indexing, a Film Crew or music video step, a training job, Cast samples, a
  timeline render, a bulk import) waited 19 s and then failed with Celery's "The Celery application
  must be restarted" when Redis was stopped, and for minutes when Redis's address did not answer.
  Sending now gives up within about half a second (Redis stopped) or 7 s (no answer), and says which
  task was not started and that Redis is not reachable. Routes answer 503 `task_queue_unreachable`.
  Film Crew and music video steps that move a project forward answer as before, with
  `dispatched: false` and a `warning`, and resume when Guaardvark restarts. A training export,
  import or resume puts the job back as it was instead of marking a finished job failed; parse and
  filter jobs are marked failed with the reason instead of sitting at pending; an indexed document
  is marked ERROR (Resume pending indexing re-queues it) instead of staying INDEXING; Cast sample
  runs, renders and bulk imports close their progress entry with the reason. Workers still wait for
  Redis as long as it takes and still retry storing a result for about 20 s.
- **The web UI says when background work did not start.** A Film Crew or music video step that
  was saved but not queued shows its warning on that production or music video (creating it,
  re-dispatching, confirming casting, approving, re-analyzing, re-planning, regenerating a shot);
  casting stops at a subject whose LoRA training was not queued. Any request Redis did not take
  shows "Not started: Guaardvark's background queue (Redis) is not reachable", with the advice to
  run `./start.sh` on the Guaardvark machine, instead of an internal task name.
- **`flask celery-health` answers in one line.** It prints `up: <answer>`, or `down: <reason>` and
  exits 1: Redis not reachable, or no worker answered the ping within 5 s. With Redis stopped it
  printed a traceback.
- **`GET /api/settings/security/check` works.** It imported a module that does not exist and
  answered 500 every time. It now reports, without returning any key, whether an API key is set,
  tool-endpoint protection, the Host and origin checks, debug mode, web access, tool file access,
  and the addresses the backend, web UI, Redis, PostgreSQL and each plugin listen on, with a
  warning for any of the others that other machines can reach.
- **`GET /api/generate/status?job_id=…` works.** It called a progress method that did not exist and
  answered 500 every time. It now answers the job's status, progress and message (live while the
  backend tracks the job, from its progress record otherwise) and 404 for an id nothing knows.
  `GET /api/jobs/unified:<id>`, which `llx job status` uses, also never found a live progress job;
  it does now.
- **A failed background task now shows its reason instead of sitting at 0 %.** An earlier release
  said so, but the worker's handler for it was connected in a way Python discarded at once, so it
  never ran. It is kept now, as is the worker's runtime-audit flush on shutdown.
- **The backend answers only to this install's names.** A site can point its DNS name at the
  Guaardvark machine's address after its page has loaded (DNS rebinding). The browser then treats
  the backend as that site's own, so the page could read every reply and, from the Guaardvark
  machine, use the routes that trust it. The frontend port already refused unknown names; the
  backend port now does too. A request addressed to anything but an IP address, `localhost`, this
  machine's hostname (its first part, `<first part>.local`), a name in `VITE_ALLOWED_HOSTS`, or the
  host of `VITE_FRONTEND_URL` or of an origin in `GUAARDVARK_CORS_ORIGINS` is refused with HTTP 421
  `host_not_allowed` before any route or Socket.IO sees it. The web UI, the CLI, the MCP server,
  plugins, and Interconnector and cluster calls by IP address are unaffected. Reaching the backend
  by another DNS name (`gpubox.lan`, a Tailscale name, an Interconnector master URL written with
  such a name, Docker opened at a name) needs that address in `GUAARDVARK_CORS_ORIGINS`, and the
  refusal says which. Under Docker the backend also answers to `backend`, its name on the compose
  network.
- **Every other server Guaardvark starts answers only to this machine's names too.** A page
  re-pointed at 127.0.0.1 could drive the plugin ports directly: queue ComfyUI workflows and read
  its outputs, turn on the Vision Pipeline's camera, run Audio Foundry, Video Editor or upscaling
  jobs. Audio Foundry, upscaling, swarm, Video Editor, Vision Pipeline, GPU Embedding, the Discord
  bot's health port, the MCP server's HTTP transport, the reboot log and `llx`'s lite server now
  apply the backend's Host rule (421 `host_not_allowed`), with the same settings. ComfyUI gets it
  from a Guaardvark extension in `plugins/comfyui/guaardvark_nodes/`, loaded through
  `guaardvark_model_paths.yaml`; ComfyUI itself is unchanged and the ComfyUI link on the video
  page still opens. Restart each plugin to pick this up.
- **No plugin reply carries a token any more.** Upscaling's and the Vision Pipeline's `/health`
  replies held the bearer token their protected routes check, and the backend passed the
  upscaling one on to any browser at `/api/upscaling/health` and `/api/plugins/<id>/health`. The
  token now lives in `data/.upscaling_internal_secret` and `data/.vision_pipeline_internal_secret`
  (readable by your user only), which the plugin and the backend both read; health replies report
  status only. The backend also masks any credential-named field in a plugin health reply it
  relays. Restart the upscaling and Vision Pipeline plugins after updating, or their protected
  routes refuse the backend's calls until you do.
- **Upscaling and the Vision Pipeline answer only the backend.** Every route but `/health` now
  needs the plugin's token, as the swarm's routes already did: before, only their write routes
  did, and anything on the machine could list upscale jobs (with their file paths), read the
  camera's latest frame and scene, or start and stop the camera. The backend sends the token on
  every call (the Upscaling page, the Plugins page's camera buttons, chat's vision context, the
  GPU notices), so nothing changes in the UI; no page loads these plugins directly.
- **Docker publishes PostgreSQL, Redis and Ollama on 127.0.0.1 only.** `docker-compose.yml`
  published all three on every interface of the Docker host, so anyone on the network could log
  in to the database with the stock password, queue Celery tasks through Redis, or use Ollama.
  The backend reaches them inside Docker's network and is unaffected; tools on the host (`psql`,
  `redis-cli`, `ollama`) still connect at `127.0.0.1`. The Web UI and API ports are unchanged.
  `GUAARDVARK_POSTGRES_PUBLISH_HOST`, `GUAARDVARK_REDIS_PUBLISH_HOST` and
  `GUAARDVARK_OLLAMA_PUBLISH_HOST` in `.env` publish one more widely on purpose (INSTALL.md,
  Docker, "Ports").
- **The web terminal listens on 127.0.0.1.** `scripts/terminal_server.sh` started ttyd, a writable
  shell, on every interface. It now listens on `127.0.0.1` (`GUAARDVARK_TERMINAL_INTERFACE` opens
  it), refuses a websocket opened by a page from another origin (`--check-origin`), and writes its
  per-install password file unreadable to others from the moment it is created.
- **The Vision Pipeline, Video Editor and the Discord bot's health port listen on 127.0.0.1.** They
  listened on every interface with no login, so anyone on the network could start the camera and
  read its frames, or run editor jobs. Every caller is the backend on the same machine.
  `GUAARDVARK_VISION_PIPELINE_HOST`, `GUAARDVARK_VIDEO_EDITOR_HOST` and `DISCORD_HEALTH_HOST` open
  them deliberately (INSTALL.md, "Plugin servers and the network"). The Video Editor also stopped
  letting any web page read its replies (it answered every origin with CORS).
- **A GET or HEAD request no longer changes anything.** Any web page can make a browser send
  either without asking, and both are let through by design. `HEAD /api/enhanced-chat/history/all`
  deleted all chat history; only `DELETE` does now. Settings → Test LLM
  (`/api/meta/test-llm`), the diagnostics export, the quality scorecard (`llx quality scorecard`,
  `scripts/quality_gate.py --mode full`) and `/api/simple-chat/health` ran the model on a GET and now
  take POST. The three under `/api/meta` then need the Guaardvark machine or the API key, like other
  `/api/meta` actions; the script sends `GUAARDVARK_API_KEY` from its environment. The
  Interconnector heartbeat takes POST
  only, as its callers already sent. Opening a chat no longer creates an empty session (its first
  message does), the memory recall debug view no longer counts as a recall, video batch and merged
  CSV downloads no longer leave a file in the temp directory each time, and the System Map reads an
  uploaded code repository without running its code.
- **Cast: a character's voice is picked from a list.** The Overview's free-text "Voice ID" let a
  typo become an id that renders drop. It is now a list of Audio Foundry's voices, grouped as in
  the Audio Studio, with "Default voice" first; voices that are not installed say so and link to
  Audio Studio → Manage models. A saved id that is not a voice is shown as invalid until another
  is picked, and is never changed on its own. Cloned voices are not offered: a Cast member has no
  reference clip to clone from. `GET /api/audio-foundry/voices` now answers while Audio Foundry is
  stopped, from the catalog in the checkout, with `plugin_running: false`.
- **Cast: unsaved edits are no longer wiped by the page's refresh.** The Cast member page reloaded
  the member every 30 seconds, and every 5 seconds while samples generated or a LoRA trained, and
  reset the Overview and training-settings forms each time, so a name, description, voice, bible
  or hyperparameter left unsaved was lost. A refresh now updates only the fields the person has
  not touched. When a field being edited was saved with another value elsewhere, the page says so
  and offers *Reload* or *Keep mine* instead of choosing. The Overview's Save sends only the changed
  fields, and leaving the page with unsaved edits asks first (links, the page's back arrow, closing
  or reloading the tab; the browser's own Back button is not covered). The page now polls only while training or
  sample generation is under way, and refreshes when its tab is shown again.
- **Film Crew: the "Regenerate shot" dialog survives the storyboard refresh.** The refresh that
  runs for a minute after a shot regen replaced the storyboard with a spinner every 5 seconds,
  closing a regen dialog opened for the next shot and losing its prompt. It now refreshes in place.
- **Cast: saving training settings or training keeps the identity sync.** Both replaced the cast
  member's stored settings with the six hyperparameters, dropping the "grounded from photos" flag,
  the vision tags and marks, the class token, the manual-edit flag and the post-train smoke score.
  The Overview then warned that the bible might not match the photos, and every Train re-ran the
  vision sync from the photos and rewrote the bible. The hyperparameters are now merged into the
  stored settings. Train also stores the settings it was started with when no identity sync runs.
- **Music Video: unsaved plan edits survive a change saved elsewhere.** When any cut's prompt or
  the treatment changed on the server (another tab, an agent), the next 5-second refresh threw
  away every unsaved prompt and treatment edit. Now only untouched fields update, and an edited
  field changed elsewhere shows *Reload* / *Keep mine*. Save sends only the changed fields and
  keeps the edits if it fails. Regenerate asks before discarding edits. Approving, opening
  another video, or leaving the page with unsaved edits asks first. *Regen this storyboard* uses
  the cut's edited prompt, as its caption said.
- **Interconnector: typing in the client settings no longer contacts the master.** On an enabled
  client node, every keystroke in Node Name, Master Server Address or Master API Key re-registered
  with the master using the half-typed value, sending the API key to partial addresses such as
  `ht` or `http://10.0.0`. Registration and the heartbeat now follow the saved configuration and
  re-register when it is saved.
- **Interconnector: auto-sync settings take effect on Save, and each registration is sent once.**
  Turning on Enable Auto-Sync, or changing its interval or entities, started syncing from the
  form, before Save or Cancel. Auto-sync now follows the saved configuration. Opening the
  settings registered a client node with the master twice (three times when the master handed
  back a new node id) and saving registered it twice; each now registers once.
- **Training → Demonstrations: unsaved steps edits are kept.** Collapsing a row or pressing the
  list's refresh button discarded the steps being edited, and after *Save Steps* re-opening the
  row showed the steps from before the save. Edits now stay until saved, a refresh updates only
  rows without edits, steps changed elsewhere under an edit are reported and the edit is kept,
  and a save updates the list.
- **Training → Demonstrations: Save Steps keeps click positions.** The steps editor shows a
  click's position as `"coordinates": [x, y]`, but saving read only `coordinates_x` /
  `coordinates_y`, so every save erased the recorded positions (replay finds its targets by
  vision and was not affected; the stored record of where each click landed was). `PUT
  /api/agent-control/learn/demonstrations/<id>/steps` now takes either shape, and refuses
  coordinates that are not `[x, y]` or null without changing anything.
- **Audio Studio: withdraw consent for a voice clip, or delete it.** "Manage imported clips" under
  the reference clip lists each clip and whether consent is recorded. *Withdraw consent* removes
  the record and keeps the clip, which is not cloned again until consent is confirmed; *Delete
  clip* removes the clip and its record. A clone already running finishes; one still waiting to
  start is refused. Deleting needs the Guaardvark machine or the API key, like the Cast Library's
  deletes; withdrawing is as open as giving consent. Deleting `me` no longer also deletes
  `me.v2.wav`, clips renamed on import (`me (2).wav`) can be played and confirmed, and a new import
  never inherits the consent of a clip removed under the same name.
- **Chatterbox's own voice stays its own after a clone.** Chatterbox kept the last cloned voice as
  its default, so a later voiceover without a reference clip (the Audio Studio's default voice, a
  Film Crew character without a voice) spoke in that clone's voice, even after its consent was
  withdrawn, until the model unloaded. The stock voice now comes back after every generation, and
  a clone reads its clip once rather than once per chunk.
- **Only Guaardvark's own pages can read its replies.** A web page served from any device on the
  local network (any 192.168.x, 10.x or 172.16–31.x address, on any port) could call the backend
  and read what it answered. Browsers are now answered only for this install's own pages: its
  frontend and backend ports on this machine's names and addresses (`localhost`, its IP addresses,
  its hostname and `<hostname>.local`), `VITE_FRONTEND_URL`, and origins listed in the new
  `GUAARDVARK_CORS_ORIGINS` for a reverse proxy or another name. Other local ports (3000, 5175)
  count only when one is this install's `VITE_PORT`. Socket.IO uses the same list, so the UI
  opened at the machine's LAN address from a phone or another computer now gets live chat,
  progress and voice; its connection was refused before. The Interconnector's status, register
  and heartbeat routes still accept any private-network page, which is how a client node's
  Settings page reaches its master.
- **Pages on other sites cannot change anything.** A page on any website open in a browser on
  the Guaardvark machine (or on any device the backend answers) could make that browser send a
  form-style POST to the backend, and routes that trust the Guaardvark machine would act on it.
  Every request other than GET, HEAD and OPTIONS is now refused with `cross_site_request` when the
  browser says it came from a page that is not this install's (its `Origin`, an `Origin: null`, or
  `Sec-Fetch-Site: cross-site` with no `Origin`). The web UI under any name it is reached by, the
  CLI, the MCP server, scripts and calls between machines are unaffected, and a client node's
  Settings page still registers with its master.
- **Restarting Guaardvark needs this machine or the API key**, like the other protected actions.
  From another device the restart dialog says to enter the key in Settings → API key instead of
  restarting.
- **A browser preflight no longer needs the API key.** Once a key existed, a UI built with an
  absolute `VITE_API_BASE_URL` could not call protected routes: the browser's CORS preflight
  (an OPTIONS request, which never carries a key or cookie) was refused, so the real request was
  never sent. OPTIONS requests that Flask answers itself now pass; the request that follows still
  needs the key or a signed-in browser.
- **The restart log server answers only this install's pages.** During a restart from Settings
  the log shown on the page came from a small server that listened on every network address,
  let any web page read the restart log, and had a `/shutdown` link that did not stop it but kept
  the process from ever exiting. It now listens on this machine only, only Guaardvark's own
  pages can read the log, and `POST /shutdown` stops it.
- **`start.sh` stops when run as root.** With `sudo`, the install landed under `/root` and left
  files the normal user could not write. It now says to run it as your normal user; it asks
  for your password itself when it installs system packages. Machines where root is the only
  account set `GUAARDVARK_ALLOW_ROOT=1`.
- **More credential files are off limits to the agent's file and code tools.** Added to the names
  they refuse to read, list or grep: `*.env`, `.npmrc`, `.pypirc`, `*.ppk`, `*.jks`, `*.keystore`,
  `*.secret`, `client_secret*.json` and dot-files with "secret" in the name.
- **The System Mapper maps what git lists.** In a git checkout `map_codebase` and the System Map
  page survey tracked files plus untracked files git does not ignore, and no longer count ignored
  local folders such as scratch copies and worktrees. On a workstation holding about 49,000 such
  `.py` copies the static analysis of the whole checkout went from 270 s to 9 s; a fresh clone
  maps the same files as before. Outside a git checkout the folder is walked as before.
- **Running tools and automation needs the Guaardvark machine or the API key.**
  `/api/tools/execute`, `/api/tools/jobs/` and `/api/automation/*` answered every device on the
  network. They now answer the Guaardvark machine itself, or a client that sends
  `GUAARDVARK_API_KEY`. `GUAARDVARK_PROTECT_TOOL_ENDPOINTS=false` brings back the old behaviour.
- **API key in Settings.** Pasting this install's key into Settings → API key signs the browser in:
  the backend answers with an HttpOnly, SameSite=Strict cookie holding a token derived from the key,
  so the page never keeps the key and a script in it cannot read the sign-in. On the Guaardvark
  machine the panel creates the key (shown once, saved in `.env`, working at once without a
  restart); a signed-in browser replaces or removes it, which signs every other browser out. Once a
  key exists every device needs it, the Guaardvark machine included. Pages that are refused (Tools,
  MCP Servers, and the rest through one notice) say what to do and link there.
- **Agent screenshots are served by signed links.** `/api/tools/screenshots/` answered any device
  that guessed a capture's name. Chat now shows each capture through a link signed for that one
  file, which works on every device; anything else needs the Guaardvark machine or the key.
  Deleting `data/.screenshot_url_secret` revokes every link; saved chats keep their pictures.
- **The CLI and MCP server on the Guaardvark machine find the key in `.env`.** A key created or
  replaced in Settings works for them at once, without copying it into their environment.
- **Docker: the first `./start-docker.sh` creates the API key** in `.env` next to
  `docker-compose.yml` and prints it for Settings → API key, since under Docker no browser counts
  as the Guaardvark machine. API URLs ending in `.png`, `.svg` and the like now reach the backend
  instead of nginx's static files.

## 2.9.3 — The command line does what it says, agents make music and voice, outpaint fills the frame

- **Command line.** `jobs watch` and `jobs status`, `tasks info`, `rag status|query|entities`,
  `/ingest`, `audio music`, `swarm run` (and a new `swarm templates`), `lessons`, the `outreach`
  subcommands, `/tool` and `/edit` now do what their help says. `search` shows the reranker's
  scores and says when it did not run; `/imagine` and chat draw the pictures they make right in
  terminals that show images (kitty); `images generate --from-file` and `videos generate --save`
  are new; `status`, `models list|active` and `setup` read the reply's data instead of its message.
  `search --json` now returns `results` (each passage with its source, page and score) and
  `retrieval`, where it returned an answer before; `ask` is the command that answers.
- **MCP for coding agents.** `guaardvark mcp serve` works from any folder, so a client can launch
  it anywhere. `guaardvark mcp install` adds Codex, Antigravity and opencode (and `--skills`).
  Two new tools, `generate_music` and `generate_speech`, run on Audio Foundry while it is running,
  with no network. `get_generation_status` can wait up to 50 s for a job and knows audio jobs; the
  photo-edit tools take the image links other tools return; every list parameter declares what it
  holds, which Gemini-based clients require.
- **Tool hardening.** fetch_url and analyze_website fetch only public addresses, checked when they
  connect; the code tools, codegen, analyze_code and process_file stay inside the install and
  refuse key and ignored files. Failed MCP results lead with the error, a null argument takes the
  published default, and a retry with the same idempotency key does not run twice. The WordPress
  and bulk CSV tools produce real output (bulk CSVs no longer double their quotes); memory,
  document and repository tools say what they return; media tools leave your own players alone.
- **Install.** `start.sh` requires Node 20.19+ or 22.12+ (Vite 8). On Linux it installs Node 22 to
  `~/.local/node` when the system Node is older; the previous fallback installed 20.18.0, which
  broke the frontend build (#255). Frontend packages are reinstalled when Node changes, a failed
  frontend build is reported, and a fresh clone no longer runs an early build that can only fail.
  `stop.sh` and the ComfyUI plugin stop only a ComfyUI started from this install, leaving one you
  run separately alone.
- **Fixes.** Outpaint fills the new border instead of returning the picture between grey bars.
  ACE-Step music loads in half precision and fits a 16 GB card. Reading free VRAM no longer opens a
  CUDA context in every process. An uploaded file's indexing job finishes instead of staying
  active. Chat's direct-tool reply carries the files it made. The CLA check accepts a sign-off
  with a trailing line break.

## 2.9.2 — MCP tools in chat, video that starts its own engine, and answers instead of refusals

- **MCP client, rebuilt on the official SDK.** One session per server with health pings, crash
  detection and clean teardown; paginated tool catalogs that refresh on `list_changed`;
  schema-validated arguments, `isError` handling, resources, prompts, an audit log, and each
  server's stderr in `logs/mcp/<server>.log`. `data/config/mcp_servers.json` accepts the Claude
  Desktop `mcpServers` shape. Only local stdio servers are accepted, and a server's process gets a
  minimal environment (no database URL or API keys unless mapped).
- **MCP tools are chat tools.** Each connected server's tools register as
  `mcp__<server>__<tool>` with their real schemas. A tool the server policy gates (a destructive
  hint, a mutating name, `confirmTools`) asks through the chat's approval card and refuses on any
  path that did not ask; `denyTools` are never offered. Resources and prompts get their own tools.
  A server entry can set `fixedArgs` (with `${VAR}` expansion) for a parameter such as a workspace
  root, which the model is then never asked for. Proxy calls no longer pass the chat message and
  project path to the server.
- **Managing MCP servers.** A new MCP Servers page, linked from Settings → Agents, shows each
  server's status, its tools and their policy, its stderr and recent activity, and adds, edits or
  removes local servers. REST routes under `/api/automation/mcp/` and a `llx mcp client` command
  group do the same; writes to the server config answer localhost or the API key only.
- **Tool fixes and containment.** generate_bulk_csv uses the topic, row count and client it is
  given and reports a failure instead of "queued"; media volume 0 sets the volume; gui_hotkey
  takes "ctrl+c"; string parameters stay strings. system_command refuses `find -exec`/`-delete`
  and credential files, generated file names stay inside the output folder, and tool output
  reaching the model is capped. Settings → Agents → File access adds an opt-in "Project folder
  only" switch for system_command and codegen. `GUAARDVARK_PROTECT_TOOL_ENDPOINTS=true` closes
  `/api/automation/*` and `/api/tools/execute` to other hosts without the API key.
- **Chat answers general questions.** With documents indexed, chat attached the top passages to
  every question and refused whatever they did not answer. Passages the reranker rates unrelated
  are now dropped (a floor of 0.30 for bge-reranker-v2-m3, measured; `GUAARDVARK_RAG_MIN_RERANK_SCORE`
  overrides it), so "what is the capital of Australia?" gets "Canberra". Questions your files
  answer still cite them.
- **Reasoning goes to the Thinking card.** Models that write their reasoning into the answer
  (lfm2.5, granite4.2) have it moved to the Thinking card, a lone `</think>` included. The tag
  pairs (`<think>`, `<thinking>`, `<reason>`, `<reasoning>`, `<thought>`, `<|begin_of_thought|>`)
  are data with per-model overrides, and a quoted tag stays in the text.
- **Model capabilities come from Ollama.** One capability record per model, read from Ollama's
  `/api/show`: an embedding model (bge-m3) is never picked as the chat model, qwen3-embedding is
  not sent `think:false`, and an embedding model's width is read from the model.
- **Web pages are read where the answer is.** fetch_url and analyze_website with a question take
  the passage its rarer words point to. "What does an aardvark eat" on the Wikipedia page opens at
  Feeding instead of the reference list; on 14 questions over five pages the answering sentence
  is in the excerpt for 10, against 5.
- **Photo edits in chat.** An edit, outpaint or likeness request made while the GPU is busy waits
  up to 600 s with a status line (Stop cancels) instead of refusing. The message after a photo is
  sent instead of opening the upload dialog. "Put this person in <place>" keeps a person in the
  scene. The likeness consent card renders on the direct path, tool cards show paths relative to
  the checkout (live and after a reload), and approval cards and in-progress replies use the
  theme's primary colour instead of error red.
- **Video renders start ComfyUI themselves.** Queuing a video from Video Gen, chat or MCP starts
  ComfyUI when it is all the render lacks (`GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR=0` keeps the old
  error); music videos and the Film Crew still ask for it to be started, and with
  `GUAARDVARK_JOB_SERVICE_START=1` the Film Crew editor starts it too. A finished render's job
  ends complete instead of sitting at 99% and being reported later as stalled.
- **Video limits in data, failures by name, frames checked.** Per-model render limits (canvas,
  frames, steps, guidance, fps, VRAM floor, attention pin) are registry data enforced in one
  place; `GUAARDVARK_VIDEO_STRICT_LIMITS=1` enforces the rest. A request that names no guidance
  renders at the model's own template value instead of 7.5 (LTX 1, Wan 14B 3.5, Wan 5B 5,
  Hunyuan 6, CogVideoX 6); at 7.5 LTX rendered posterised noise. A failed render carries a kind,
  a label and a next step in the status JSON, MCP, the Studio card and the Jobs page, and a
  ComfyUI error names the failing node. Finished clips are checked frame by frame for black
  frames, blown highlights, washed-out colour, wrong size and wrong length, flagged on the card.
- **Video fixes.** CogVideoX T2V and I2V are admitted on a 16 GB card, render after other jobs,
  take a typed negative prompt, and skip the live preview their wrapper crashed. LTX 2.5 snaps to
  the 64 px grid its output lands on. A LoRA at strength 0 loads at 0. A MiniMax H3 render with
  12 or more guides and a user LoRA builds a valid graph. Workflow contract tests check every
  family's graph against a ComfyUI node snapshot in CI, and `scripts/video_smoke.py` renders one
  short clip per installed model.
- **Add new model.** The Hugging Face lookup files each model under its own family (Wan 5B,
  Hunyuan, CogVideoX and repo roots were read as Wan 14B), suggests the 5B for a Wan 2.2 5B LoRA,
  and refuses Mochi and LTX-Video 0.9 by name.
- **Cast Library.** Cast LoRAs are on ComfyUI's LoRA search path, so FLUX and SDXL characters
  render. FLUX characters sample at FLUX's 28 steps and guidance 3.5, and Image Gen locks steps,
  guidance and quality while a character is selected. Image model limits are registry data
  (`GUAARDVARK_IMAGE_STRICT_LIMITS=1` applies the rest).
- **Indexing uses the model chosen in Settings.** Celery workers and scripts embed with the
  Settings embedding model instead of the `.env` one, so switching models and re-indexing takes
  effect.
- **Screen agent.** Every screen task is recorded as an episode (`agent_task_runs`,
  `agent_task_steps`, created at boot; read-only routes under `/api/agent-control/runs`). The model
  sees every step of the task, not the last three. The loop stops when progress stops (four steps
  with no new target and no visible change), with 40 steps and 480 s as the floor instead of a
  fixed 15 and 120 s. "Done" needs a click that changed the screen, a click with no visible change
  is shown as such, and a click limit stated in the task holds. A model that sees but points
  badly borrows a measured eye, and the correction loop clicks its answer for eyes measured to
  judge well.
- **GPU and plugins.** A cancelled batch leaves the GPU wait at once, a crashed plugin reads as
  stopped, and a lease left from before a reboot is released. Orphan cleanup only kills processes
  started from this install, so a second checkout's services are left alone.
  `GUAARDVARK_JOB_SERVICE_START=1` (off by default) lets image, audio and upscale jobs start their
  service too.
- **Dependencies.** jsonschema >= 4.20.

## 2.9.1 — Local-only chat, feedback that teaches, and a start that works offline

- **Chat is local-only again.** The dormant Mistral cloud provider, the `cloud_models_enabled`
  switch behind it and the `/api/llm/*` endpoints are removed. The switch was off by default and
  no page rendered it, but the routes were live and unauthenticated, so an install with
  `MISTRAL_API_KEY` in its environment could be pointed at Mistral by any client that reached the
  API. Local Mistral-family models served by Ollama are unaffected. Hosted models reach Guaardvark
  through the MCP server or the opt-in Uncle Claude escalation, neither of which changes chat routing.
- **`./start.sh` starts offline.** A requirements or lockfile change since the last install no
  longer makes a working environment count as broken: with no route to the package index the
  backend starts on the installed packages, and the frontend keeps its `node_modules` instead of
  letting `npm ci` delete them. The next start with a connection applies the update.
  `GUAARDVARK_OFFLINE=1` forces the offline path.
- **Faster launches when nothing changed.** Python bytecode is cleared and the frontend rebuilt
  only when a fingerprint of the checkout (commit, uncommitted edits, untracked sources, lockfile,
  build-time `VITE_*` env) changed since the last launch; `./start.sh --clean` forces both. The
  clear no longer reaches environments named `venv-*` or `.venv`, so Audio Foundry's music
  environment keeps its library bytecode between launches.
- **Thumbs teach, and can be taken back.** Every assistant reply records its provenance (request
  id, tier, model, persona rule, the memories and retrieval sources its prompt used, the recipe a
  screen task ran, tools). A thumb names its reply by message id; it adjusts the confidence of
  those memories, counts against the recipe, and records corrections and lessons; a second click
  withdraws the verdict and reverses what it taught. The caption under the reply says what it
  taught. Tool cards carry their own thumbs.
- **Any model can drive the screen.** One resolver answers what a model can do by asking Ollama,
  not by matching names (10 of the 23 models on the reference box had at least one detector wrong).
  The user's active model is the brain; when it cannot see, the resolver lends the most accurate
  measured eye. Each eye's axis order is measured on a known board instead of assumed, calibration
  and accuracy live in one store, and eyes are ranked by measured accuracy. A correction loop,
  armed only when the eye's measured accuracy is coarser than the target, re-checks the estimate
  with a marker before clicking. Offline benchmarking, truth-labelled frame capture and a
  calibration page back the measurements.
- **Agent desktop.** Firefox launches on the virtual display with the snap build and its private
  bus; the floating card is square and chat bubbles have a half-opaque background.
- **Dependencies.** beautifulsoup4 4.15.0, Flask-Migrate 4.1.0, mss >= 10.2.0, anthropic >= 1.7.0,
  lucide-react 1.47.0.

- **ComfyUI and the GPU, seven truths.** The plugin's health probe proves the process on :8188 is
  ours (a stranger's ComfyUI on the port used to read as "running" while every Wan batch failed).
  Image-batch bookings release through their generator and a video render books the model's
  VRAM estimate and waits for it before queuing, instead of loading with 1 GB usable and 9.6 GB
  offloaded. The ComfyUI VRAM reserve is declared per model in the registry (MiniMax H3 5.0 GB,
  Wan 2.2 14B 1.0 GB, both measured 2026-09-12); the first render of the other family after a
  launch restarts ComfyUI once with the right reserve, and an explicit
  `GUAARDVARK_COMFYUI_RESERVE_VRAM` still wins. The plugin start re-reads the
  `GUAARDVARK_COMFYUI_*` keys from `.env` every time, so a reserve or attention change needs a
  plugin restart, not a backend restart. A chosen canvas under 1024 on a text-intent prompt is
  kept and batch metadata records the file's real size. A stop/start cycle brings the ComfyUI
  plugin back the way it was. The dead-probe limit is a named constant with its reason.
- **RAG: the index tells the truth.** The unified index manager loads onto the configured pgvector
  store or refuses, never a fresh empty index over the real one. Repository "architectural
  summaries" come only from the model: the old import target was an empty stub, so every summary
  indexed to date was a template; a repository whose model call fails is marked pending, not
  summarised. Purges report a count and a reason; `list_documents` reports an unavailable count
  instead of the page length. The context expander is project-scoped. `read_logs` over MCP scrubs
  machine paths from log lines. (Two audit items were already fixed and are now pinned by tests.)
- **Attachments are answered, not dropped.** A chat attachment over the declared
  `CHAT_ATTACHMENT_MAX_BYTES` (16 MB) gets HTTP 413 or a `chat:error` instead of vanishing at the
  socket buffer; `GET /api/chat/config` reports the limit; the chat page downscales photos before
  sending (longest edge 2048, JPEG 0.9) and shows the size.
- **A self-improvement scan can be cancelled.** `POST /api/self-improvement/scans/<id>/cancel`,
  a `cancelled` status the worker honours at its checkpoints, and a Cancel button that waits for
  it.
- **Building a plugin manager no longer builds a second one.** The status snapshot emitter used
  the process singleton, so any privately constructed manager (a test, a tool) spawned a real-
  registry manager that probed every install port and ran orphan cleanup; one such run under
  pytest took down this checkout's ComfyUI. The emitter now uses the manager that broadcasts, and
  the port-kill path refuses to run under a test.
- **Docs.** HARDWARE.md and INSTALL.md say the same thing about Apple Silicon (stills and LoRA
  training verified; ComfyUI video on Metal has no evidence on file); `--fast` is documented as
  what it gates.
- **Chat asks before it uses a face.** Putting an attached photo's person into a new scene
  (`generate_identity`) now pauses on a consent card: approving records consent next to the photo
  (and under `outputs/consent/` by content hash, so the same image does not ask again), declining
  ends the turn with a refusal and no render. The chat intercepts no longer grant consent on the
  user's behalf, and MCP or CLI callers need a recorded consent for the reference image.
- **Code questions search the code before they read the docs.** When a question mentions the
  source and `search_codebase` is available, the first prompt carries the code tools and holds the
  knowledge-base passages back; they are added, labelled secondary, once a code search has run or
  returned nothing. Other questions are unchanged.
- **The answer says when it was assembled from tool results.** A reply produced after the tool
  budget ran out carries an "Assembled from tool results" chip and its own step row.
- **`guaardvark mcp serve`.** The pip-installed CLI can start the MCP server (stdio, or `--http`)
  from the checkout it finds; with no checkout it says what it looked for. The README's PyPI
  paragraph now says what the package does, the package carries keywords, and the README ends with
  the MCP Registry ownership marker.
- **The identity tool is on by default.** `generate_identity`, its `/identity` command and the PuLID
  pack row no longer wait behind `GUAARDVARK_IDENTITY_TOOL=1`; the consent card and the recorded
  consent are the gate. (Operator-approved 2026-09-19.)
- **Identity renders keep the face again.** PuLID on FLUX had been contributing nothing: the
  pinned node stored its face embedding on the model and deleted it in the node's `__del__`,
  which current ComfyUI fires before the sampler runs, so every render silently ignored the
  reference at any weight. The shipped patch (`plugins/comfyui/custom_nodes.patches/`) moves the
  data onto the cloned patcher's `transformer_options`, where the forward reads it on every step.
  Measured on a synthetic reference (late sixties, white hair, full grey beard): before the fix,
  weights 1.0, 1.5 and 5.0 gave the same clean-shaven man in his thirties; after it, the default
  (fp8, weight 1.0, start 0) renders the reference's hair, beard, age and eyes.
  `generate_with_identity` also takes `weight`, `start_at`, `end_at` and the UNET dtype, and
  `scripts/experiments/pulid_matrix.py` renders a likeness matrix through the product path.
- **Small truths.** The maintenance handler's progress-job cleanup runs the real script instead of
  importing a function that never existed; folder create, upload and document copy answer HTTP 201
  with a message instead of 200 with `"message": 201`; a Setting's repr redacts secret values; a
  chat attachment is served at `/api/outputs/edit_inputs/<name>` so the consent card can show it.
- **The server tells the truth about what it did.** Deleting one image or video batch now removes
  its document, folder and job-history rows and vectors, not just its folder. Starting a service
  plugin whose process died no longer answers "already running": the manager probes the health
  endpoint first and starts it. The Plugins page shows the code-search plugin as running when it
  runs (it answers `/healthz`, not `/health`). `list_documents` over MCP no longer counts RAPTOR
  summary nodes as documents. The progress-job cleanup script that three callers invoked, and the
  health check reported missing, now exists (dry run by default, `--execute` to clean).
- **A chat turn that runs out of tool iterations answers from what it found.** The engine makes one
  more call with tools off and marks the reply `synthesized` instead of emitting an empty response.
- **Credentials stay out of the log.** A failed read or write of a `*_key`, `*_token`, `*_secret` or
  password setting logs the exception type only; SQLAlchemy would otherwise print the bound value.
- **File Manager copies files and folders.** New `POST /api/files/folder/<id>/copy` deep-copies a
  folder into a target (or the root) with `(Copy)` naming; the context-menu actions use it.
- **Settings and Images page say what they do.** Slash commands defined as rules load again (the
  rules endpoint returns a bare array). On Z-Image and Krea 2 Turbo the negative prompt and
  "enhance anatomy" controls are disabled with a note, since those models ignore them, and the
  auto preset's caption says it stuffs quality tags rather than detecting settings. RAG debug shows
  the performance metrics it fetched; LoRA strength saves on blur or Enter; the scan dialog can be
  closed with the scan continuing; the memory merge target is picked from a list, not a prompt.
- **Tests.** `run_tests.py` runs the whole suite by default (4,077 tests) with `--quick` for the old
  filter, and its migration check points at the script that exists. `backend/mcp/tests` collects
  from both the repo root and `backend/`. Twenty-one September test files that had never been run
  now pass; the image generator exposes `get_status()` with the GPU fault the API reports.
- **Web pages are read at the passage the question is about.** `fetch_url` and `analyze_website`
  take an optional `query`; with it, the 2,000-character excerpt is the stretch of the page that
  holds the most of the question's terms (`backend/utils/text_focus.py`) instead of the top of the
  page, which on most sites is the navigation. On the Wikipedia page for the aardvark, "what does
  an aardvark eat" used to return the language menu; it now returns the feeding passage. Without a
  query nothing changes.
- **Chat edits photos: Qwen-Image-Edit, inpaint, outpaint, background removal, and a face
  carried into a new scene.** With a picture attached, chat has `edit_image` (prefers
  Qwen-Image-Edit 2509 FP8 when installed, then FLUX.1 Kontext, then img2img), `inpaint_image`,
  `outpaint_image` (grows the canvas and fills it) and `remove_background` (an alpha cut-out),
  plus `/inpaint`, `/outpaint` and `/removebg`. Plain phrasings route on their own ("remove the
  background", "extend the picture to the left"); "put a hat on this person" stays an edit.
  `generate_identity` (PuLID on FLUX.1-dev: one face photo, a new scene) is built but off by
  default: on the reference 16 GB card it returned a younger, dark-haired man for a grey-bearded
  reference at identity weights 1.0 and 1.5, so it waits behind `GUAARDVARK_IDENTITY_TOOL=1`
  until the likeness is right. Everything they need installs from **Manage Image Models →
  Image editing**: one table declares which pack each tool needs, the modal lists the packs with
  per-row Install and Install all, and a tool without its pack answers one sentence naming that
  modal. Nothing downloads on its own: the PuLID pack now carries the EVA02-CLIP and facexlib
  files its ComfyUI node used to fetch at first use (the plugin patches the pinned node so it
  runs on ComfyUI 0.33; its pack is listed only with the flag), and background removal runs the rembg project's u2net / BiRefNet ONNX
  files through the onnxruntime already shipped, without the rembg package. Measured on a 16 GB
  card: a 20-step Qwen edit in 108 s (ComfyUI keeps 11.2 GB of the FP8 weights resident and
  offloads 8.2 GB), a PuLID render in 28 s, a u2net cut-out in 1 s on CPU; the registry's VRAM
  estimates are those measurements, since the earlier 14 GB guess plus admission headroom
  refused every edit on that card. Model entries: `qwen-image-edit`, `qwen-image-clip`,
  `qwen-image-vae`, `pulid-flux`, `pulid-antelopev2`, `eva02-clip`, `facexlib-face`,
  `bgremove-birefnet`, `bgremove-u2net`. A finished image download is no longer re-announced
  each time the Image Models modal opens after a restart.
- **Add new model is a declared family table, and the server checks the paste.** Which
  architectures a Hugging Face paste can join is now one table (`user_model_families.py`)
  shared by the Image and Video Add dialogs: Z-Image, Krea 2, SDXL, SD 1.5 and FLUX stills for
  images (FLUX UNETs and SDXL/FLUX LoRAs install into ComfyUI), Wan, MiniMax, LTX, Hunyuan and
  CogVideoX for video. A repo the product cannot load (Qwen-Image, SD3, HiDream) is refused by
  name instead of being filed under SDXL. Look-up reads the licence, gated state, pipeline tag
  and `model_index.json` class; `hf.co` and `refs/pr/N` revisions parse; dataset and Space
  URLs are refused. Registering re-inspects the repo on the server (the client's
  `has_model_index` and file list are no longer trusted, and a file path cannot leave the
  repo), the same repo and files twice answers 409 with the existing id, and a catalog add
  succeeds even when the Install could not start. Image installs gained the video downloader's
  behaviour: persisted status, stall detection that keeps its lock, progress from the real
  destination folder, already-installed short-circuit. The dialogs share one look-up hook,
  clear a stale preview when the paste changes, use a radio for a single file, offer a filter
  for long file lists, show name, family, size and licence before Install, and can add without
  installing. Remove asks whether to delete the files on both modals; Batch Image has Manage
  models.
- **Audio Studio has a Manage models modal, and Generate never downloads weights.** Audio
  Studio's first Generate used to fetch weights from Hugging Face on its own (ACE-Step 8 GB,
  the gated Stable Audio Open). Weights now install only from Audio Studio → Manage models
  (voice, music, FX rows with sizes, per-row Install and Install all missing; the plugin can be
  started from there), listed and downloaded by the backend so the modal works with the sidecar
  stopped. The loaders refuse a cache miss and name the modal instead of downloading.
  Installs fetch only the files the loaders read: Chatterbox is five files (3.3 GB) out of a
  13.9 GB repo, Stable Audio Open skips its duplicate root checkpoints (5.3 GB of 15.7). MiniMax
  Music 3 keeps installing into ComfyUI through the same button. Routes:
  `GET /api/audio-foundry/models`, `POST .../models/download`, `GET .../models/download-status`.

## 2.9.0 — Agent skills, a Claude Code plugin, and MiniMax H3 at its ceiling

229 commits since 2.8.1. A coding agent can drive Guaardvark through fifteen Agent Skills, a
Claude Code plugin installable from this repository, and an MCP server that behaves the way its
tools are described. MiniMax H3 is wired through the product, users can add their own Hugging
Face models, and the Settings page, chat defaults and retrieval were rebuilt or fixed.
**No database migration.** Python and frontend dependencies changed; the next `./start.sh` sees
the edited requirement files and reinstalls, and the frontend installs from its lockfile.
Interconnector clients: Update Now, restart, and rebuild the frontend bundle.

- **Wan 2.2 14B no longer renders black rectangles under ComfyUI's ck attention.** ComfyUI's ck
  (Comfy Kitchen INT8) attention left NaN patch tokens in Wan 2.2 14B image-to-video latents,
  which decode as black rectangles: 6 of 9 Lightning renders at 960x544 with the same seed. The
  same graphs with PyTorch attention had no NaN. The Wan 14B registry entries now declare
  `attention: "pytorch"`, and the graph adds a `ModelAttentionBackend` after the LoRAs when
  `GUAARDVARK_COMFYUI_ATTENTION` asks for ck, sage or auto and ComfyUI offers the node; a default
  launch builds the same graph as before. MiniMax H3, where ck was measured faster with identical
  frames, keeps it. A failed `/object_info` fetch is no longer cached, so a ComfyUI stopped for
  another GPU job no longer reads as every node missing until the backend restarts.
- **Adjust & Retry keeps the speed profile; a profile's LoRAs are not free adapters.** A batch
  saved its step count but not its speed profile, so reloading a 4-step Lightning batch came back
  as Standard at 4 steps, and the reload counted those steps as typed, which bypassed Wan's
  20-step floor. Batches now save `speed_profile` and `style_embedding`, and the reload keeps the
  stored steps only when `steps_explicit` says a person typed them. The Video Gen page no longer
  lists a speed profile's LoRAs as adapters, and the backend refuses them with a message naming
  the profile (`speed_profile_loras` in the registry). Tests: `backend/tests/services/test_wan_speed_profiles.py`.
- **MCP tools that need the backend reach it over HTTP.** The MCP server has no Flask app, so any
  tool that touched `db.session`, `current_app` or `flask.request` failed with "Working outside of
  application context". Those tools now hand their work to the running backend
  (`backend/utils/backend_http.py`, `POST /api/tools/execute`, two new GET routes for the
  repository map and dependency graph); Film Crew, music video, bulk CSV, video and animation run
  in the backend process, and `backend.app` refuses to import inside the MCP server.
- **The MCP adapter behaves the way its tools are described.** Guard state is per client session;
  an identical call is refused only while the first is still running, and a tool that keeps
  failing pauses for 60 s instead of staying blocked. Every tool declares read-only (and
  destructive where it can discard something), published schemas carry defaults, enums and
  bounds and arguments are validated before a tool runs, a state-changing call accepts an
  `idempotency_key`, `resources/list` pages with a cursor, the `/outputs/<path>` download route
  registers again, and `doctor --call` makes one real read-only call per tool family.
- **Verbatim prompts shows what the server saved.** The Settings toggle flipped before the save
  and swallowed a failure; it now disables itself while saving, takes `enabled` and
  `forced_by_env` from the response, and reports a failed save.
- **Bring your own Hugging Face models.** Manage Video Models adds a model, LoRA or text encoder
  from a pasted Hugging Face URL, a text encoder can be picked per generation, and user LoRAs and
  encoders work on LTX and Hunyuan as well as Wan and MiniMax. The Images page adds Hugging Face
  image models and LoRAs the same way.
- **Settings.** The page was rebuilt around what each control does, five settings that reported
  the wrong state were fixed, the Rules page filters learned rules, each retrieval profile has an
  editor, and the Workspaces navigation choice writes the value the layout checks for.
- **Ollama.** Only what `start.sh` started is stopped (`--keep-ollama`, `--all`,
  `--external-ollama`, with matching Settings switches), and every request carries a context size
  instead of inheriting the chat model's Modelfile window.
- **Chat.** A host can hand the engine its conversation and hooks, so an embedded assistant runs
  the engine's tool loop; Floating Chat messages have a copy-text icon.
- **Video fixes.** The H3 Turbo 4-step profile no longer refuses the default canvas, the
  effective-settings chip names the model that will run, the end-frame control says what it does,
  and a failed task shows its reason instead of sitting at 0 %.
- **macOS.** AppleDouble sidecar files are stripped, the LoRA venv is created, the backend
  defaults to port 5055 away from AirPlay Receiver, pgvector builds from source when Homebrew's
  formula skips the running Postgres major, and Stable Audio Open may try Apple Silicon when
  opted in.
- **Interconnector.** `frontend/public`, `VERSION` and `celery_beat_gates.py` now reach clients,
  with the allowlist guard running in CI.
- **Dependencies.** OpenCV is locked to 4.11.0.86 across its three distributions (the last line
  that accepts numpy 1.x; numpy stays on 1.x) and the unused CV stack is gone; Vite 8, Vitest 5,
  react-grid-layout 2 and zustand 5 on the frontend; `mcp>=2.1.1`, `peft>=0.20.0`,
  `llama-index-vector-stores-postgres>=0.9.0` and `numba` in the backend.

- **MCP calls no longer hang on a busy GPU, and the MCP server no longer renders.** The MCP server is its own process, and `generate_image` used to load a diffusion pipeline inside it, next to the backend's; a failed result then reached the client as `(no output)` because the adapter dropped `ToolResult.error`. Now the adapter tags calls with `transport=mcp`, and `generate_image` in that context hands the prompt to the backend over its HTTP API (queued by default, returning the batch id in ~10 ms; `wait_for_result=true` polls the backend and returns the file), `get_generation_status` reads any image or video batch back with file URLs (idempotent, so polling is not blocked by the duplicate-call guard), and failed results carry their error text. Inside the backend (chat) the inline render is unchanged. The MCP adapter now runs every tool on a worker thread under the configured timeout (default raised from a never-enforced 30 s to an enforced 120 s; 30 min for a call that asked to wait), answers a timeout with a message that the work is still running, and per-tool argument defaults live in `data/config/mcp.json` `server.tools.argument_defaults`. Tests: `backend/mcp/tests/test_smoke.py`, `backend/tests/unit/test_image_tool_queue.py`.
- **AGENT_GUIDE.md.** The operating contract for a coding agent that uses Guaardvark: first-interaction rules, Rule Zero (every job goes through a skill), a mandatory preflight that turns the hardware tier into what this box can do, the announce-before-spend / ask-before-switch / no-silent-downgrade contract, the human checkpoints with the route that releases each, how the tools behave over MCP, prompting rules per model family, a quick lookup, what not to do, and a contributor section. `AGENTS.md` becomes the router that points at it.
- **Claude Code plugin + marketplace.** `.claude-plugin/plugin.json` and `marketplace.json` make the repository installable with `/plugin marketplace add guaardvark/guaardvark` and `/plugin install guaardvark@guaardvark`: the skills load as `/guaardvark:<skill>` and the MCP server starts from the checkout path the install asks for.
- **Agent skills pack.** `.agents/skills/` carries fifteen Agent Skills (`setup`, `-image`, `-video`, `-music-video`, `-film-crew`, `-voice`, `-music`, `-upscale`, `-cast`, `-models`, `-swarm`, `-knowledge`, `-code`, `-outreach`, `-ops`), each naming the exact MCP tool or REST route for its flow, so a coding agent (Claude Code, Cursor, Codex, OpenClaw) can drive a running Guaardvark without guessing endpoints. `python -m backend.mcp install --skills` links them into `~/.claude/skills`; the pack README is `.agents/skills/README.md`.
- **Z-Image gets prompts as prose, never as SD-era tags.** A plain sentence such as
  "a man and woman watching a movie on a couch, her head on his shoulder" was leaving the
  Images page with 22 phrases appended ("full body shot, realistic stance, correct anatomy,
  anatomically correct, ..."), boilerplate written for CLIP-captioned SD 1.5. Z-Image's
  encoder is an LLM and reads those as scene content: on this box the stuffed prompt gave
  posed, camera-facing figures with tangled legs on four of four seeds, while the bare
  sentence or a prose rewrite on the same seeds was clean. The 1,400-character anatomy
  negative was never reaching the model at all (CFG-distilled, guidance 0). Each stills
  family now declares a `prompt_style` in `backend/services/stills_defaults.py`; for
  "natural" families the offline enhancer sends the sentence as written (plus one prose
  clause for a non-photo style), and the default enhance rung becomes the media director's
  new prose contract, a port of the prompt-enhancer template the model's authors ship with
  their demo, which falls back to the exact sentence when no chat model answers. Chat and
  batch share the change; the Images page's prompt preview now resolves "auto" to the
  default model so it shows the policy that actually runs. Krea 2 keeps tags until it is
  measured the same way.
- **The Director asks the active chat model first.** Every Director call (stills rewrite,
  storyboards, edit refinement, video and music video planning) was hard-wired to
  `gemma4:e4b`, then any gemma, then whatever was installed, ignoring the model made active
  on the Settings page. The ladder is now: an explicit per-job model, then the Settings-page
  model, then any installed gemma, then any installed qwen, then the rest, matched anywhere
  in the tag so a custom build such as `someone/Gemma-4-custom` counts. Embedding models
  never qualify. The stills rewrite tries up to three rungs, so a model that errors or hands
  back the wrong number of prompts is skipped rather than silently dropping to the raw
  sentence.
- **The chat can search the code.** `search_codebase` is one tool name for "search this
  project's source": by meaning or by symbol, returning files, line numbers and the code. With
  the new zvec-grep plugin (`plugins/zvec_grep`, off by default, Node 22, CPU, everything on
  the machine) it runs a local vector-plus-keyword index of the checkout; without it, the
  repository's regex search. Questions about the source keep the tool in the prompt and get
  one system line saying the code is already indexed. Measured 2026-09-08 on eight questions
  about this repository with document retrieval off: baseline made no tool calls and
  answered three with hedges; with the tool every question called it and seven came back
  naming the right file and function. Four engine fixes came out of the trial and apply to
  every tool: the result a tool hands back to the model is capped by a budget the tool
  declares (`BaseTool.observation_chars`) instead of a flat 500 characters that left a search
  with a header and no code; a tool call written in signature form
  (`search_codebase(query:string='x')`) is normalised instead of failing as an unknown tool;
  an MCP server's error result is a failure, not output; and the request's `project_root`
  reaches tools that need it. Known: with document retrieval on, the model still prefers the
  documents and rarely reaches for the tool.
- **Thinking is off unless someone asks for it, everywhere the product talks to Ollama.**
  The Chat page's "Chat thinking" setting was documented as off by default while the stored
  value said on, so every reply on this box and on a client's box paid for gemma4's hidden reasoning:
  the same question measured at 1,163 generated tokens and about 40 s for a 554-character
  answer with thinking on, 183 tokens and about 10 s for an 858-character answer with it off.
  Outside the Chat page nothing set the flag at all, and a thinking model given a token cap
  can spend the whole cap reasoning and hand back an empty answer (the agent's narration
  fallback, an 800-token call, did exactly that). One predicate now decides which models
  reason, `model_supports_thinking` in `backend/utils/ollama_resource_manager.py`, by name
  pattern and then by Ollama's own capabilities list, so qwen3 is covered and a family the
  list has not met is still caught. `build_ollama` turns thinking off for those models unless
  the caller passes `thinking` itself; `get_llm_instance(model=...)` accepts `thinking`,
  `request_timeout`, `json_mode`, `num_ctx` and `num_predict` like a white-label build already
  did; the model-switch and startup instances, the brain's capability probe, the diagnostics
  ping and the agent's narration fallback all go through the same helper. The Chat page's
  per-chat `/thinking on` still wins, and the retry after an Ollama serializer crash now keeps
  that choice instead of silently reasoning again. Retrieved context handed to the chat model
  is cut on whitespace (`backend/utils/text_cut.py`): a 500-character slice through "4:12"
  left "4:1" in a prompt and the model repeated it as fact.
- **The same answer-only default for every direct Ollama call.** The film crew's
  screenwriting and consensus calls, the character generator, the video, media and music video
  directors, the animation steering prompt, the video quality review, the H3 prompt polish, the
  outreach persona and grader, the natural-language control plane, the lesson distiller, image
  OCR and the music prompt rewriter each built their own request without a `think` field, so a
  thinking model could spend a 150- or 400-token cap on reasoning and return nothing to parse.
  Every one of them now spreads `think_payload(model)`, which is `{"think": false}` for a model
  that reasons and nothing for any other.
- **Three chat defects seen on camera 2026-09-05.** A thinking model is prompted with
  `[tool_call]` markup, but the stream only held back the angle-bracket form, so the raw
  markup typed into the bubble for a second before the parser consumed it; both forms are
  held back now, and neither reaches saved history. A reply that echoed the tool list
  (`search_knowledge_base(query:string, top_k:int?)...`) was non-empty, so the empty-answer
  retry never fired and the echo became the answer; the turn is now repeated once with
  thinking off and says plainly if the model echoes again. On the legacy agent-loop and
  file-generation paths the page appended a second user bubble after the optimistic one and
  read a `final_answer` that `tool_result` and `file_generation` replies never carry, so a
  finished CSV was reported as "Agent execution completed with no response"; the bubble is
  reused and the server now returns the `display_content` it already persisted.
- **Chat retrieval had been failing on every turn.** The hybrid retriever ran its vector and
  keyword legs through a nested event loop; inside a request thread the first call died with
  "Detected nested async" and every later one with asyncpg's "another operation is in
  progress", so the model answered from memory and told people nothing was indexed while 18
  documents were. The two legs now run in sequence on the store's synchronous engine
  (`use_async=False` in `backend/services/indexing_service.py`); a question about the indexed
  README comes back citing it.
- **One active video model for every pipeline.** Chat `/video`, `videos generate` in the CLI,
  batch requests that omit a model, the music video and Film Crew all pick their model through
  one resolver: an explicit id, else a per-pipeline override, else the global setting at
  `/api/settings/active_video_model`, else the largest installed model the card can hold. An id
  that cannot run is refused in one sentence; families are never swapped silently. Omitted fps,
  frames, steps and canvas fill from the model's declared native values, so the CogVideoX
  low-VRAM path no longer cuts steps below the model's floor. Note: where the music video and
  Film Crew editor used to hard-code Wan 2.2 14B I2V, the default now follows the registry,
  which has been Wan 2.2 5B TI2V since July; pick 14B in the picker or the setting to keep it.
- **Music video and Film Crew start from chat, the CLI and MCP.** "Make a music video from
  song.mp3, neon noir" and "film this script …" create the project and start analysis or the
  screenwriter, then stop at the Studio gate: nothing is approved and no GPU render starts
  outside Studio. A song path or script path given to those tools must sit under the uploads or
  outputs directory or the install root. Frame counts snap to each model's declared grid,
  MiniMax's 17k+5 included.
- **Paths from a request stay inside the directory they belong to.** One helper,
  `backend/utils/path_guard.py`, joins caller-supplied names under a server-chosen root and
  refuses anything that lands outside it; forty call sites (batch video, files, backups,
  outputs, jobs, uploads, the interconnector, the swarm and video-editor sidecars) now go
  through it instead of their own `resolve()`/`startswith` checks. Vector-store table names
  are quoted through psycopg2's `Identifier`, the self-test category is allow-listed before
  it reaches a subprocess, Audio Foundry proxy replies are always JSON, and the system map
  and `/build` accept roots inside the running codebase or the uploads directory only.
  Closes the 280 open code-scanning alerts except the 21 that describe operator-directed
  browsing of the server's own filesystem, which are dismissed with reasons.
- **GPU faults are reported as GPU faults.** A CUDA error that kills the context (launch
  timeout, illegal memory access, device-side assert, uncorrectable ECC and kin) is now
  recognised in one place. The backend records it, refuses further GPU work immediately
  instead of retrying it, fails the rest of a running batch without trying each prompt, and
  tells the user the backend needs a restart. Before this, one driver watchdog reset left
  every later image request failing for hours with "pipeline failed to load — usually VRAM
  pressure or an incomplete download". Status reports the fault under `gpu_fault`.

- **Capability contract.** Every video model entry can declare modes (text, first frame,
  last frame, first+last, reference), audio in and out, whether it samples with CFG, its
  frame rule and rate, clip bounds, a step floor and default, speed profiles, style
  embeddings, reference limits, per-VRAM-class starting settings and its license.
  `model_capabilities()` fills family defaults for older entries; `/api/batch-video/models`
  exposes the record; the Video Generator, `generate_video`, Film Crew and the music video
  pipeline read it instead of testing a family name. The step floor now lives in the
  registry, so API and MCP callers get it too; a value a person typed still wins.
- **MiniMax H3.** Reference build, unpruned Int8 (24GB) and BF16 (48GB) rungs, the three
  turbo LoRAs as optional companions behind speed profiles, ten style embeddings, all six
  aspect ratios, last-frame and first+last-frame modes, image and audio anchors at any
  frame, and the reference graph (9 images, 3 clips, 3 audio files) with references named
  in the prompt in wiring order. A prompt compiler renders Guaardvark's structured intent
  into the model's format (numbered shots with cut times that add up to the clip, speaker
  ids, tagged dialogue in the model card's eleven languages), with an optional polish pass
  by the local director model that is discarded if it touches the dialogue. Eight authored
  prompt presets ship in `plugins/comfyui/scripts/prompt_bundles/minimax_h3`.
- **Film Crew on H3.** A production can name its video model; on a native-audio model each
  scene renders as windows of at most fifteen seconds with the cast's lines spoken by the
  model, joined on the storyboard stills, no voiceover laid over them, the score mixed
  under the window's own soundtrack. Cast reference images go in as references when the
  reference build is installed.
- **Music video on H3.** The clip profile (rate, frame bounds, native audio) comes from the
  registry; on a native-audio model each cut renders in one pass with the song slice
  anchored at frame 0 so the motion follows its beats, the song staying the master track.
- **Chat and MCP.** `generate_video` takes model, aspect ratio, seconds, first and last
  image, references, audio and a speed profile, each checked against the model's record;
  the assistant is told the H3 prompt format on video pages. Publishing adds a
  "Generated with MiniMax H3 on Guaardvark" line to posts that carry H3 clips (opt-out per
  connection) and enforces a per-platform clip-length cap declared in data.
- **ComfyUI launch.** `GUAARDVARK_COMFYUI_ATTENTION=auto|ck|sage|pytorch` selects an
  attention backend (Comfy Kitchen int8 ships in the venv; SageAttention is never
  installed for you); `GUAARDVARK_COMFYUI_RESERVE_VRAM` raises the reserve a partially
  loaded model needs. The plugin restart route no longer fails on a missing attribute.
- **Measured** on a 16 GB RTX 40-series card with 64 GB-class RAM, pruned Int8, 864x480, 124
  frames (5 s), 20 steps, PyTorch attention: 6.5 minutes wall, about 17 s per step, VRAM
  peak 14.5 GB with most of the transformer offloaded, ComfyUI resident memory peak 27 GB;
  the clip was clean. The first attempt ran out of memory at 1 GB of reserve; 3 GB
  finished. Same seed and canvas: the 8-step turbo profile 186 s with the subject,
  motion and background intact and slightly softer fur (now the 16 GB starting
  profile); Comfy Kitchen int8 attention 339 s at 15 s per step with frames
  indistinguishable from PyTorch (opt-in until the other families are compared). On the
  turbo profile the 10 s clip took 237 s and the 15 s clip 372 s, both coherent to the
  end at 480p; those tiers now appear as duration presets. The 1344x768 canvas ran out
  of memory at a 3 GB reserve and rendered at 5 GB (`GUAARDVARK_COMFYUI_RESERVE_VRAM=5.0`):
  171 s on the 4-step 768p profile with the transformer fully offloaded. An audio anchor works as a
  performance track: a 4 s narration anchored at frame 0 came back in the clip's
  soundtrack with a 0.91 waveform correlation (0.99 on the envelope), rendered in 138 s
  on the turbo profile.

### CLI

The `guaardvark` command is now a peer of the web UI, not a subset.

- **One command catalog.** Slash router, tab completion, `/help`, and the contract tests
  share `COMMAND_TREE`. `/imagine`, `/video`, `/voice`, `/ingest`, `/agent`, `/web`,
  `/load`, `/skills`, and `recipes` complete. Unknown commands get “Did you mean…?”.
  Completion works without a leading `/`.
- **Theme-true prompt.** REPL colors follow `/theme` (including `day` and `auto`). Compact
  banner on short terminals so the 30-row aardvark art does not overflow. Chat prefix is
  the brand mark, not a llama. `/clear` uses Rich. Config lives in `~/.guaardvark/cli.json`
  (legacy `~/.llx` still read). `/web` uses the real frontend port from runtime.json.
- **Studio commands.** `plugins`, `gpu`, `mcp`, `audio`, `swarm`, `lessons` wrap the
  existing APIs. `guaardvark completion bash|zsh|fish` prints a shell script.
  `guaardvark doctor --cli` reports terminal graphics / tmux passthrough.
- **Show the artifact.** `/imagine` previews inline (Kitty / iTerm / chafa). `/voice`
  plays locally. `/agent shot` dumps the agent desktop. Jobs notify on complete.

## 2.8.1 — Profiles, extensions, and a bootstrap that converges offline

16 commits since 2.8.0. Two product-shaping features — a profile switch and a client
extension seam — and a set of installer fixes from watching a client box with a flapping
resolver fail to finish bootstrap for an evening. Nothing in this release changes the
database or the knowledge index; upgrading is a pull and a restart.

- `start_postgres.sh` takes the role, database, host and port from `DATABASE_URL` and never
  re-provisions a role it did not create; before this a fork with its own role on the same
  machine had its password reset and its URL rewritten to the stock database. `start.sh` and
  the agent display kill only a port's listener, not a process holding a client socket to it.
  The DSN is logged with its password masked. ComfyUI's liveness probe tolerates ~2 minutes of
  silence while a 20 GB+ model loads on a 16 GB card (measured downstream), instead of 20 s.
- **Profiles.** One switch sets the product shape: `GUAARDVARK_PROFILE=<name>` in `.env` or
  `./start.sh --profile <name>`. `workstation` is today's product and sets nothing;
  `creator` is the media workflow (image, video, audio, Film Crew, LoRA, upscaling) with the
  agent, knowledge-index, outreach and automation subsystems left installed but unlisted and
  off by default; an extension can ship its own. An explicit `.env` value, flag, plugin toggle
  or DB setting always wins over a profile, and hidden means unlisted, never removed. See
  `backend/profiles/README.md`. The sidebar lists what the profile lists, `/` lands where it
  says, Settings → Product Profile switches profiles (applies on restart), and a fresh
  install asks once — Creator or Workstation — before anything else.
- **Extensions.** A client vertical lives in `extensions/<id>/` — blueprints, models, Celery
  tasks, column migrations, seeds, a profile, an optional sidecar plugin — and core loads it
  through fixed hook points without any core file naming it. A broken extension is reported
  by id while the others still load, and a declared URL prefix with no mounted route is an
  error rather than a silent 404. Extensions register handlers for their own task types
  instead of editing the unified task executor. `extensions/_template/` is the starting point;
  see `extensions/README.md`. On the frontend, `extensions/<id>/frontend/index.jsx` contributes
  routes, nav groups, themes, page context, chat surfaces, store state, a header bar and a
  logo; core merges them at build time and imports for the extension resolve through core's
  dependencies and the `@` alias.
- Settings → Maintenance gains **Delete History**, next to Clear Cache: removes every
  batch-image, batch-video and audio generation — the media directories and files, their
  `documents`/`folders` rows and `job_history` entries — and logs each purge to
  `retention_audit`. Batches still generating are skipped. Film Crew productions, video
  editor projects, the cast library and LoRAs, and chat history are not touched. The audio
  sidecar gains `DELETE /jobs` so its in-memory job list and its `.jobs` files stay in step.
- **Bootstrap converges offline.** Every step that contacted a package index even when
  nothing needed to change is gone or gated: `install_pytorch.sh` probes the venv first
  and skips the 3 GB torch re-stage when the exact build is already installed
  (`GUAARDVARK_TORCH_FORCE=1` restores always-reinstall); the torch channel comes from the
  hardware policy instead of a second table that disagreed with it (cu121 vs cu124 on
  Ampere/Ada made the reconciler and `start.sh` swap the CUDA stack back and forth); the
  numpy/setuptools re-pin probes offline (`scripts/lib/venv_pins.sh`) and only reinstalls
  a violated spec; the cli reconciler skips when the editable install already points at
  `cli/`; and `system-manager` never creates a venv from a non-3.12 interpreter (Ubuntu
  26.04's `python3` is 3.14, whose venv compiled numpy from source and failed).
- Every bootstrap pip pass runs under `backend/constraints.txt` (`PIP_CONSTRAINT`, operator
  value wins), which now caps `opencv-contrib-python`, `tifffile` and `ml-dtypes` at their
  last numpy<2 lines. Before this the unconstrained CV pass upgraded numpy to 2.x on every
  boot and the torch pass dragged it back, looping through a full torch re-stage each time.
- The CV / face-restoration stack (gfpgan, realesrgan, basicsr, facexlib, controlnet-aux,
  mediapipe — hundreds of MB) is opt-in with `GUAARDVARK_INSTALL_CV=1` instead of
  installing on every GPU box. Both consumers import lazily and degrade when it is absent.
- Two installs on one machine no longer see each other's Celery workers: `start_celery.sh`
  and `start.sh` count a worker only when its working directory is under this checkout,
  the same confinement `stop.sh` already applies. The pgvector step distinguishes a
  missing package from a missing superuser, names the package for the major actually
  serving, and reads the same configured URL as the role and database do.
- **Interconnector sync ships every `backend/` package.** The sync allowlist named
  `backend/` packages one by one, so `backend/profiles` and `backend/extensions` never
  reached a client while the synced `config.py` / `app.py` already imported them — every
  client boot after Update Now died with `cannot import name 'extensions' from 'backend'`.
  The nine missing entries are listed and a test walks the real `backend/` directory so
  the next new package fails in CI, not on a client.
- The PyPI project page shows the README again. `setup.py` read `long_description` from the
  repo root, which the wheel build cannot see; the 2.8.0 wheel published with an empty
  page. The release build now copies `README.md` into `cli/` the way it already does
  `VERSION`.

## 2.8.0 — MiniMax H3, a rebuilt knowledge index, and a cleaner clean install

367 commits since 2.7.0. The largest single change is the knowledge index, which was
rebuilt from the storage layout up and needs one re-index (see the note below). Around
it: three new video model families, an overnight self-improvement director, Discord
through the same chat engine as the UI, a privacy audit of every path that could reach
the network, a platform layer with macOS in CI, and the clean-install bugs a tester
found on a fresh Windows 11 / WSL2 box.

**This release requires a full re-index of your knowledge base.** Existing vectors were
built with different chunking and are not migrated. Nothing is lost — your documents are
the source of truth and are re-read from disk — but plan for the corpus to be
unavailable while it rebuilds. See *Upgrading the knowledge index* below.

### Clean-install fixes

All three were reported against a fresh install on 2026-08-29 and all three were real:

- **Film Crew failed with `model 'gemma4:e4b' not found`.** The installer's hardware
  policy pulls `gemma4:e2b` on most machines; the swarm agents hard-coded `e4b`. A chat
  model name is now a preference resolved against what Ollama actually has — same
  family first, then the saved active model, then the policy's tier model
  (`backend/services/ollama_chat_model.py`).
- **pgvector was never installed.** `start_postgres.sh` provisioned PostgreSQL but not
  the `vector` extension the index stores into, and enabling it needs a superuser the
  app role is not. Provisioning now installs `postgresql-<major>-pgvector` (Homebrew
  `pgvector` on macOS) and runs `CREATE EXTENSION`; existing installs get it on the next
  start, with one sudo prompt.
- **LoRA training stopped at `No module named 'peft'`.** Z-Image training runs in the
  backend venv, which never listed it. It does now.

### Video generation

- **MiniMax H3** — download plan in the video model registry and generation through
  ComfyUI. It fits a 16 GB card at the template's settings; see *Known limitations*
  for the speed caveat.
- **LTX-2.5 distilled** as a local ComfyUI family, with I2V/T2V aligned to the official
  pipeline for identity preservation, a preflight file check, and the audio VAE
  registered where the loader actually looks.
- **HunyuanVideo 13B** T2V/I2V (GGUF Q5_K_M) in the downloader and generator.
- **Wan 2.2** — quality presets can no longer hand Wan a step count it cannot render
  (`minSteps` on the model entry, measured against the smearing that 10 steps produced);
  the 14B's trained sampler shift is fixed at 8.0 by default instead of scaled by pixel
  area; 1:1 is back; a sampler profile toggle for the 5B (adaptive euler or official
  uni_pc); 24 fps presets and a Motion preset that reaches the model; guidance comes
  from the model's own workflow.
- **Live latent preview** while ComfyUI renders.
- Long renders survive: VRAM-wait admission, staged progress, a real start budget for
  the ComfyUI launcher with a loud fallback, and a ComfyUI interrupt scoped to the
  prompts we queued rather than everything in its queue.
- A per-family pixel-area clamp prevents hangs; an unsupported aspect ratio is clamped
  server-side as well as in the UI.
- The chosen model animates its own keyframe; a failed director pass no longer switches
  the prompt enhancer off.
- Video from inline chat (`generate_video` tool), a fullscreen player with prev/next,
  and a VLM temporal-quality reviewer (MiniCPM-V 4.5).

### Images and the media workspace

- One tabbed media workspace, a route per tab.
- **Image upscaling**, single and batch.
- Batch images: clear completed batches from the queue, Adjust & Retry no longer
  multiplies the prompt by the quantity, prompt auto-detect no longer silently
  overrides steps (and has a toggle), Z-Image keeps its CFG-free defaults.
- The hidden SD-1.5 fallback is gone; img2img goes through the same guards as txt2img;
  large canvases no longer take the desktop down with them; the seed generator builds
  on CPU when CUDA cannot initialise.
- Documents: opt-in media gallery, fast streaming PDF viewing and an in-app DOCX viewer.

### Knowledge index

Vectors moved to pgvector, hybrid search is back, and ingest is roughly an order of
magnitude faster. Measured on the same machine and the same model, ingesting the same
corpus:

Measured on the same machine and the same model, ingesting the same corpus:

| | before | after |
|---|---|---|
| Fixed cost per document | ~3.5 s | **0.02–0.07 s** |
| Characters embedded per chunk | ~2,230 | **~800** |
| A 151 KB document | 19.3 s | **6.1 s** |
| A 2 KB note | 9.3 s | **0.1 s** |

Four things account for most of it:

- **Every chunk was being embedded twice.** The text kept for citations was stored in
  metadata, and metadata is concatenated ahead of the chunk before embedding — so each
  chunk was sent to the model as both its text and its own metadata. Roughly half of all
  embedding work was duplication.
- **Chunks are sized by what is embedded**, not by what they carry. Chunk size is
  computed as `size - len(metadata)`, and that metadata is now excluded before splitting
  rather than after, so a document with a long path and tags no longer loses most of its
  chunk to text it was never going to embed.
- **The index no longer rewrites itself on every document.** It kept a JSON copy of every
  node and rewrote the whole file each time a document was added, so adding one document
  got slower as the corpus grew. That file is gone.
- **Garbage collection is amortised** rather than run twice per document. In a process
  holding the ML stack a full collection costs ~250 ms, which on a small file exceeded
  parsing, chunking and embedding combined.

Ingest cost is now flat in corpus size: one constant pair of coefficients predicts it
across a corpus growing from 0 to 703 documents and 36,000 chunks.

#### Keyword search moved into PostgreSQL

The keyword half of hybrid search now queries the full-text index PostgreSQL was already
maintaining, instead of an in-memory index rebuilt from a JSON file. Retrieval behaviour
is unchanged in shape — same fusion, same adaptive weighting, same reranking — but it no
longer depends on a file that had to be rewritten constantly, and it can filter by project
in SQL rather than after the fact.

Ranking is tuned for how people actually search. Rare words now decide a query: asking for
a specific name, identifier or error code puts the passage containing it first, instead of
letting common words in the rest of the question outvote it.

#### Also fixed in the index

- **Client, project and job metadata was never indexed.** Both metadata indexers passed
  their metadata in a form the indexing layer rejects, so every attempt failed and logged a
  message that read like a transient problem. They now work.
- **Uploads went through a lesser pipeline than everything else.** Files uploaded through
  the UI were read as plain text — a PDF or DOCX arrived as mojibake, markdown was never
  sectioned, and re-indexing appended a second copy instead of replacing the first.
- **Re-indexing generated text left the old copy behind.** A repository summary, client
  profile or extracted relationship stayed in the index after being regenerated, competing
  with the current version at query time.
- Re-indexing a document is no longer slower on a large corpus than a small one.
- Documents with no headings no longer explode into tens of thousands of fragments.
- Audio and video files are no longer fed to a text reader. They are not yet indexed;
  they are simply left alone until transcription lands.

- Deleting a document now actually removes it from the knowledge base; documents left
  PENDING by an interrupted run are requeued; auto-resume defers to an Ollama outage
  instead of condemning documents to it.
- Corpus sensemaking and a document navigation surface; index profiles (one registry,
  several derived projections); a staged document archive with filter, dedup and
  chronology; a `docling` dependency declared so PDF and Word files can be indexed at all.
- The knowledge tools work inside the MCP subprocess.

#### Upgrading the knowledge index

1. Back up if you want a fallback: `pg_dump` your database, and keep `data/docstore.json`
   until you are satisfied.
2. Upgrade and restart.
3. Re-index. The knowledge base rebuilds from your documents; the background catch-up job
   will work through them on its own, or drive it directly for a bulk rebuild.
4. `data/docstore.json` and `data/index_store.json` are no longer used and can be deleted
   once the rebuild finishes.

If you run PostgreSQL with default memory settings, `scripts/tune_postgres_for_rag.sh`
raises the two that matter for a vector index of any size. It prints what it would change
with `--dry-run` and needs root only to apply.

### Chat

- The floating chat keeps its thread across a refresh and names the page it is
  looking at; a distribution can declare which pages are chat surfaces.
- Pluggable per-turn context providers, and a knowledge-source registry on the RAG
  retrieval step; facts supplied by a provider are not treated as a web-search question.
- Markdown tables render as tables; a pasted description no longer triggers image
  generation; explicit "remember this" intents are captured.
- Rule bundles apply by name (`flask load-rules`), and Guaardvark ships its own voice as
  one; lesson bundles load into agent memories.
- The Ollama context window is bounded on every call that was leaving it unset.
- `list_documents` joins the core tools.

### Self-improvement and autoresearch

- Autoresearch rebuilt as a research system: live parameters, honest evals, bounded
  overnight runs with two-fidelity evaluation, a real kill switch.
- One overnight director schedules retrieval tuning, code tuning and Auto Improve.
- Proposals and research runs bind to the saved active model in the worker; a directed
  run only counts as a success when it staged a fix; bulk approve/reject/apply in the
  fixes dialog with progress and a result banner.

### Film Crew and training

- A `training_director` engine for procedure-guide videos: Kokoro or a cloned narrator,
  per-shot control over whether people appear, image-to-video that actually renders.
- Before Create, the LoRA trainer says whether the base model will be downloaded.

### Discord

- `/ask`, channel chat and voice all go through the unified chat engine, so the bot
  answers the way the UI does; the bot listens and talks in voice channels; `/video`
  delivers the finished clip to the channel.

### Privacy and security

- **Generation never leaves the machine**: backend model loads read local registry
  files only and never reach Hugging Face on their own; downloads happen only behind
  an explicit Install.
- ComfyUI binds to loopback and stops pinning host memory.
- Exact host matching and an explicit URL regex in the scout; an arithmetic-only
  calculator tool; batch-image names resolved through `safe_join`; cast-library deletes
  and other destructive media and GPU routes require localhost or the API key.
- Pillow floor raised to 12.3.0 and torch stopped silently undoing it;
  `socket.io-parser` overridden to 4.2.7; the Dependabot queue cleared again (av 18,
  safetensors 0.8, pydantic 2.13, psutil 7, typer 0.27, and the rest).
- CodeQL runs on every push; pull requests need a signed CLA; a pre-commit, commit-msg
  and pre-push guard keeps machine-specific content out of the repository.

### GPU and resources

- Resident models are reclaimable, so admission can free VRAM instead of refusing.
- Cross-process training leases; heartbeat leases under `flock`; eviction only when it
  helps; the GPU power limit is left alone by default.
- The job reaper tolerates ComfyUI probe blips and never steals a live worker's gate.
- The default image model is no longer refused on 32 GB machines, and a RAM-gate
  refusal now says "system RAM (not VRAM)".

### Install, platform and operations

- A curl-able bootstrap installer: `curl -fsSL https://guaardvark.com/install.sh | bash`.
- A platform layer (`scripts/platform/`) that says in one place what this machine can
  do, with macOS in CI; macOS honours the ComfyUI port override, finds a font, and names
  the reconciler that failed.
- Fresh Ubuntu 24.04 installs: Python dev headers before pip, the setuptools pin that
  made PyTorch uninstallable is gone, PyTorch wheels are staged before the working ones
  are removed, the Node major version is verified, the Postgres sudo gate no longer
  needs a tty, and bootstrap survives tmpfs `ENOSPC`, half-installed venvs and
  proxy-only boxes.
- `scripts/heal_backend_venv.sh` re-runs every reconciler the failure sentinel names,
  not only the venv ones (#41).
- Plugins ship disabled by default, with Ollama the exception; `plugin.local.json`
  per-install overrides survive updates; plugin config actually saves; restart waits out
  the cooldown instead of failing half-way.
- Backups dump only the database and install the app is using, and stop following
  plugin symlinks out of the project.
- Redis broker URL and Celery beat heal on stop/start; an abandoned `start.sh` is reaped
  without killing a new boot; the Intel e1000e NIC hang is detected and mitigated before
  heavy downloads.

### Interconnector and connections

- Stable, IP-independent node identity and a server-side client heartbeat daemon.
- Outbound connections with a credential store, publishing, and a publish approval queue.

### MCP and CLI

- `python -m backend.mcp install` writes the server entry into the configs of the agent
  clients it detects; `python -m backend.mcp doctor` self-tests the server and flags stale
  client configs. The server is on the 2.x MCP SDK.
- `/abort` for a wedged chat and stream timeouts that end one; offline recipe
  inspection commands (`recipes list / show / validate`).
- The CLI test suite runs in CI; the `release` workflow publishes to PyPI on tag and
  refuses a tag that disagrees with `VERSION` or a version PyPI already has.

### Desktop agent

- DOM-based verification fast path (fixes 60 s task-timeout blowouts); a window
  fast path for launch gates; the DOM element inventory feeds the decision prompt.
- Servo calibration restored with a live Gemma4 fit; truth injection and
  validation-gated calibration in the learning stack.
- Basic browser-navigation recipes and a YouTube-comment recipe.

### Documentation

- `docs/HARDWARE.md` — what runs CPU-only, on 8–12 GB, on the 16 GB target, and beyond.
- `AGENT_MENTAL_MODEL.md` — chat versus agent versus Swarm.
- The README repositioned around the whole platform, with the walkthrough series;
  `CONTRIBUTING.md` carries the comment and portability standards.
- The knowledge index documented, with the claims that were aspirational corrected.

### Known limitations

- Python 3.12 only; the ML wheels for 3.13/3.14 are still missing upstream.
- MiniMax H3 on a 16 GB card is correct but slow: it runs at the template's 20 steps
  with no distilled speed LoRA and no SageAttention path, and a tester measured about
  12 minutes for a 3 s clip on an RTX 5060 Ti. Both accelerations are tracked for the
  next release.
- The Creator profile (#114) — the media-creation workflow with the agent, index,
  outreach and automation subsystems one setting away — is designed and not yet built.
